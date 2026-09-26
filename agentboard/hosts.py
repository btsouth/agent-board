"""Remote hosts: other machines running Agent, merged into one board.

The board is not very useful if the agents you care about live on three boxes.
Two rules make that a feature instead of a liability:

* **No remote install.** The roster module is self-contained stdlib Python, so the
  bridge ships it over SSH on every poll (`ssh host python3 - < roster.py`). The
  remote side needs Agent and python3, nothing else, and there is no second copy
  of the bucketing logic to drift from the local one.
* **A dead host must not look like finished work.** Each host has a timeout and a
  cache; when a fetch fails its last known rows are kept and marked offline with
  their age, and the local publish never waits on it.
"""

from __future__ import annotations

import json
import math
import os
import shlex
import threading
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import state as state_module

CONFIG_PATH = state_module.CONFIG_DIR / "hosts.json"
CACHE_PATH = state_module.STATE_DIR / "hosts-cache.json"

DEFAULT_TIMEOUT = 10.0


@dataclass
class Host:
    name: str
    ssh: str
    hermes_home: str = ""
    enabled: bool = True

    def target(self) -> str:
        return self.ssh or self.name


def load_hosts() -> list[Host]:
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    hosts: list[Host] = []
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, str):
            hosts.append(Host(name=entry, ssh=entry))
        elif isinstance(entry, dict) and entry.get("name"):
            hosts.append(
                Host(
                    name=str(entry["name"]),
                    ssh=str(entry.get("ssh") or entry["name"]),
                    hermes_home=str(entry.get("hermes_home") or ""),
                    enabled=entry.get("enabled", True) is not False,
                )
            )
    return hosts


def save_hosts(hosts: list[Host]) -> None:
    _atomic_write(CONFIG_PATH, json.dumps([asdict(host) for host in hosts], indent=2))


def add_host(name: str, ssh: str = "", hermes_home: str = "") -> Host:
    hosts = [host for host in load_hosts() if host.name != name]
    host = Host(name=name, ssh=ssh or name, hermes_home=hermes_home)
    hosts.append(host)
    save_hosts(hosts)
    return host


def remove_host(name: str) -> bool:
    hosts = load_hosts()
    kept = [host for host in hosts if host.name != name]
    save_hosts(kept)
    return len(kept) != len(hosts)


# One writer at a time: the watcher's refresher thread and the publish path both
# do load -> mutate -> save, and interleaving them loses a host's rows.
_CACHE_LOCK = threading.Lock()


def _atomic_write(path: Path, text: str) -> None:
    """Write a small state file so a crash cannot leave half of it behind."""
    state_module.atomic_write(path, text)


def mutate_cache(callback):
    """Read-modify-write the host cache under the lock."""
    with _CACHE_LOCK:
        cache = load_cache()
        result = callback(cache)
        save_cache(cache)
        return result


def load_cache() -> dict[str, Any]:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(cache: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(CACHE_PATH, json.dumps(cache, indent=2))


def cached_entry(cache: dict[str, Any], name: str) -> dict[str, Any]:
    """One host's cache record, in the shape a fetch returns.

    The reachability travels WITH the rows: rows cached from a host that was down
    are not a host that is up, and a caller that has to guess is a caller that
    publishes `terra: ok` for a machine that has been off for a day.
    """
    entry = cache.get(name) or {}
    return {
        "name": name,
        "ok": bool(entry.get("ok", False)),
        "sessions": entry.get("sessions") or [],
        "counts": entry.get("counts") or {},
        "attention": int(entry.get("attention") or 0),
        "error": entry.get("error") or "",
        "at": float(entry.get("at") or 0.0),
        "cached": True,
    }


def remote_script() -> str:
    """The roster module plus a driver, to be piped into a remote python3.

    Deliberately the *same* module the local board uses: identical bucketing,
    identical statuses, one place to fix a bug.
    """
    source = (Path(__file__).resolve().parent / "roster.py").read_text(encoding="utf-8")
    driver = '''

def _main(argv: list[str]) -> int:
    import json as _json
    limit = int(argv[0]) if argv else 15
    days = float(argv[1]) if len(argv) > 1 else 3.0
    print(_json.dumps(board(limit=limit, days=days)))
    return 0


if __name__ == "__main__":
    import sys as _sys

    raise SystemExit(_main(_sys.argv[1:]))
'''
    return source + driver


def _failed(host: "Host", error: str, started: float) -> dict:
    """A failure result, in the same shape as a success.

    `sessions` is always present and always empty: a caller that iterates rows
    must never have to guard for a missing key and must never be handed rows we
    did not actually get.
    """
    return {
        "name": host.name,
        "ok": False,
        "sessions": [],
        "counts": {},
        "attention": 0,
        "error": error[:200],
        "at": started,
    }


def fetch(host: Host, *, limit: int = 15, days: float = 3.0, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """One host's roster, or a reason it did not arrive."""
    command = f"python3 - {int(limit)} {float(days)}"
    if host.hermes_home:
        command = f"HERMES_HOME={shlex.quote(host.hermes_home)} {command}"

    started = time.time()
    try:
        proc = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                f"ConnectTimeout={int(max(2, timeout - 4))}",
                host.target(),
                command,
            ],
            input=remote_script(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _failed(host, str(exc), started)

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return _failed(host, (detail[-1] if detail else f"ssh exited {proc.returncode}"), started)

    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return _failed(host, "remote roster was not JSON", started)

    return {
        "name": host.name,
        "ok": True,
        "sessions": payload.get("sessions", []),
        "counts": payload.get("counts", {}),
        "attention": payload.get("attention", 0),
        "at": started,
        "took": round(time.time() - started, 2),
    }


def fetch_all(
    hosts: list[Host] | None = None,
    *,
    limit: int = 15,
    days: float = 3.0,
    timeout: float = DEFAULT_TIMEOUT,
    ttl: float = 30.0,
    cache: dict[str, Any] | None = None,
) -> list[dict]:
    """Fetch every enabled host, reusing a result younger than `ttl`.

    The TTL is what keeps a publish from turning into a pile of SSH round trips:
    the local board is published on a short cadence and remote data moves slower.
    """
    hosts = hosts if hosts is not None else load_hosts()
    cache = cache if cache is not None else load_cache()
    now = time.time()
    results: list[dict] = []

    for host in hosts:
        if not host.enabled:
            continue
        entry = cache.get(host.name) or {}
        if entry.get("at") and now - float(entry["at"]) < ttl:
            cached = cached_entry(cache, host.name)
            # A cached host keeps the status it had when it was last heard from:
            # reporting ok here is what let a dead machine look healthy.
            results.append(cached)
            continue
        results.append(fetch(host, limit=limit, days=days, timeout=timeout))

    return results


def _number(value: Any, default: float) -> float:
    """A numeric field from a remote roster, or a default.

    A malformed or older remote payload must not raise out of a merge: one bad
    field on one host would take the whole publish with it. Non-finite is part of
    that: `json.loads` accepts `Infinity`, `NaN` and an overflowing literal, and a
    row carrying one reaches an `int()` that refuses it.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def merge(
    local_sessions: list[dict],
    results: list[dict],
    *,
    cache: dict[str, Any] | None = None,
    now: float | None = None,
) -> tuple[list[dict], dict[str, str], dict[str, Any]]:
    """Tag and combine rows, keeping a dead host's last known rows visible.

    Returns (all sessions, host status map, updated cache).
    """
    stamp = now if now is not None else time.time()
    cache = dict(cache if cache is not None else load_cache())
    merged = [dict(session, host="local", host_offline=False) for session in local_sessions]
    status: dict[str, str] = {"local": "ok"}

    for result in results:
        name = str(result.get("name", "?"))
        if result.get("ok"):
            rows = result.get("sessions", []) or []
            # The fetch's own stamp, not the merge's: re-stamping on every pass
            # made the cache look eternally fresh, so it was never re-fetched.
            cache[name] = {
                "at": float(result.get("at") or stamp),
                "sessions": rows,
                "counts": result.get("counts") or {},
                "attention": int(result.get("attention") or 0),
                "ok": True,
                "error": "",
            }
            status[name] = "ok"
            merged.extend(dict(session, host=name, host_offline=False) for session in rows)
            continue

        status[name] = "offline"
        previous = cache.get(name) or {}
        rows = previous.get("sessions") or []
        # Record that it is down, so the next fetch does not need a live attempt
        # to know it, and so `status` can report it from the cache alone.
        cache[name] = {
            **previous,
            "ok": False,
            "error": str(result.get("error") or "unreachable")[:200],
            # Without a timestamp a host that has only ever failed is re-fetched
            # (live ssh, full timeout) on every single publish.
            "at": float(result.get("at") or previous.get("at") or stamp),
        }
        for session in rows:
            merged.append(
                dict(
                    session,
                    host=name,
                    host_offline=True,
                    last_seen=previous.get("at", 0),
                    age_s=int(_number(session.get("age_s"), 0) + max(0.0, stamp - _number(previous.get("at"), stamp))),
                )
            )

    # Same urgency order the local board uses, so a foreign row cannot float.
    from .roster import STATUS_ORDER

    merged.sort(key=lambda session: (
        STATUS_ORDER.get(str(session.get("status") or "idle"), 9),
        _number(session.get("age_s"), 0.0),
    ))
    return merged, status, cache


def reply(host_name: str, session_id: str, text: str, *, timeout: float = 180.0) -> dict:
    """Send a reply to a session that lives on another host.

    Uses the remote Agent CLI rather than the RPC path: the remote desktop
    backend's transport belongs to whoever is sitting at that machine, and a
    one-shot resumed turn cannot take it away from them.
    """
    host = next((item for item in load_hosts() if item.name == host_name), None)
    if host is None:
        return {"ok": False, "error": f"unknown host {host_name}"}

    prefix = f"HERMES_HOME={shlex.quote(host.hermes_home)} " if host.hermes_home else ""
    # This string is run by the remote LOGIN SHELL. json.dumps escapes quotes for
    # JSON, not for bash: a reply containing `id` or $(...) would execute on the
    # other machine. shlex.quote is the only correct tool here.
    command = " ".join(
        (
            f"{prefix}hermes chat -Q --resume {shlex.quote(session_id)}",
            f"--oneshot -q {shlex.quote(text)}",
        )
    )

    try:
        proc = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                # A black-holed host must not hold the watcher's round for the
                # whole reply timeout.
                "-o",
                "ConnectTimeout=6",
                "-o",
                "ServerAliveInterval=5",
                "-o",
                "ServerAliveCountMax=2",
                host.target(),
                command,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": str(exc)}

    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout or "").strip()[-200:]}

    return {"ok": True, "host": host_name, "session": session_id, "tail": (proc.stdout or "").strip()[-120:]}


def status_report() -> list[dict]:
    """Configured hosts with their cached freshness, for the CLI."""
    cache = load_cache()
    now = time.time()
    report = []
    for host in load_hosts():
        entry = cache.get(host.name) or {}
        report.append(
            {
                "name": host.name,
                "ssh": host.target(),
                "hermes_home": host.hermes_home,
                "enabled": host.enabled,
                "cached_sessions": len(entry.get("sessions") or []),
                "cache_age_s": int(now - entry["at"]) if entry.get("at") else None,
            }
        )
    return report
