"""The WoW side of agent-board: publish the roster into the client, take replies out.

Why it works this way (the sandbox decides it, not taste): a WoW addon has no
filesystem and no network. Its only persistence is SavedVariables, which the
*client* reads at UI load and writes at logout or /reload. So the two directions
are asymmetric and both go through the client's own file moments:

* in  -> Agent writes ``Interface/AddOns/AgentBoard/Data.lua`` (a plain Lua
  table). The client reads it when the UI loads, so fresh data arrives on login,
  on ``/reload``, and on the addon's sync key.
* out -> the addon queues replies into SavedVariables; the client writes them to
  ``WTF/Account/<acct>/SavedVariables/AgentBoard.lua`` at the same reload. Agent
  watches that file and dispatches what it has not seen yet.

The outbox is a single delimited string on purpose: parsing Lua from Python
would mean shipping a Lua parser for one table, and the addon can sanitise the
separators out of user text trivially.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from . import backend, control, hermes_store, live as live_module, state as state_module, t3
from .roster import board

ADDON_NAME = "AgentBoard"
ENTRY_SEP = ";;"
FIELD_SEP = "|"

# What a queued entry can be. A closed set on purpose: the alternative was
# recognising a hand-off by its text being exactly "!focus", which meant a player
# who typed that sentence got a clipboard instead of a reply.
OUTBOX_KINDS = {"reply", "new", "approve", "decline", "answer", "focus", "mark_read", "stop"}
# The overlay can also settle (T3) and archive (Hermes, T3); the addon cannot.
LIVE_KINDS = OUTBOX_KINDS | {"new_project", "settle", "unsettle", "archive", "mark_unread"}

# What a session id looks like in the store: date_time_hex, or any uuid-shaped
# string. Used to tell a real entry from the debris of a torn one.
SESSION_ID_RE = re.compile(r"[A-Za-z0-9_.-]{8,}")

# Payload wire format. The addon refuses anything whose tag or schema it does not
# know, so both sides can be updated independently without silent nonsense.
PAYLOAD_TAG = "HE1"
PAYLOAD_SCHEMA = 5
BRIDGE_VERSION = "0.2.1"

_INSTALL_HINTS = (
    "/mnt/data/Games/World of Warcraft",
    "/data/Games/World of Warcraft",
    "~/Games/World of Warcraft",
    "~/Games",
    "/mnt/games/World of Warcraft",
)

_STATE_PATH = state_module.STATE_DIR / "wow-inbox.json"
ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------- discovery --


def _expand(path: str | Path) -> Path:
    return Path(os.path.expanduser(str(path)))


def addon_source() -> Path:
    """The addon as it lives in this project."""
    return Path(__file__).resolve().parent.parent / "addon" / ADDON_NAME


def find_addon_dirs() -> list[Path]:
    """Every ``Interface/AddOns`` belonging to a WoW client on this machine."""
    explicit = os.environ.get("AGENT_BOARD_ADDON_DIR")
    if explicit:
        return [_expand(explicit)]

    found: list[Path] = []
    seen: set[tuple[int, int]] = set()
    roots: list[Path] = []

    for hint in _INSTALL_HINTS:
        roots.append(_expand(hint))

    for root in roots:
        if not root.is_dir():
            continue
        # <root>/<version dir>/Interface/AddOns, plus the case where the hint
        # already points at the version directory.
        for candidate in sorted(root.glob("*/Interface/AddOns")) + sorted(root.glob("Interface/AddOns")):
            if not candidate.is_dir():
                continue
            # The same install is often reachable through more than one mount
            # path; same inode means one client, not two.
            try:
                stat = candidate.stat()
            except OSError:
                continue
            key = (stat.st_dev, stat.st_ino)
            if key in seen:
                continue
            seen.add(key)
            found.append(candidate)
    return found


def pick_addon_dir(explicit: str | Path | None = None) -> Path | None:
    if explicit:
        path = _expand(explicit)
        return path

    candidates = find_addon_dirs()
    if not candidates:
        return None

    # Prefer the directory the client has touched most recently: that is the
    # install the user is actually playing.
    return max(candidates, key=lambda path: path.stat().st_mtime if path.exists() else 0)


def version_dir(addon_dir: Path) -> Path:
    """The client's version directory: the one that holds ``Interface`` and ``WTF``.

    A client keeps `WTF` BESIDE `Interface`, not inside it, so walking up from the
    addon folder has to reach the version directory itself. This was wrong once in
    a way no gate noticed: every in-game reply silently went nowhere because the
    SavedVariables lookup returned None.

    Found by looking for the directory that actually has a `WTF` in it, rather
    than by counting parents: callers pass either the AddOns folder or the addon
    folder, and a count that is right for one is wrong for the other.
    """
    leaf = addon_path(addon_dir)
    candidates = [leaf, leaf.parent, leaf.parent.parent, leaf.parent.parent.parent]
    for candidate in candidates:
        if (candidate / "WTF").is_dir():
            return candidate
    # No WTF anywhere: a client that has never been launched. Assume the standard
    # layout so the caller gets a missing-file answer rather than a wrong one.
    return leaf.parent.parent.parent


def savedvars_path(addon_dir: Path) -> Path | None:
    """``WTF/Account/<account>/SavedVariables/AgentBoard.lua`` for this client."""
    account_root = version_dir(addon_dir) / "WTF" / "Account"
    if not account_root.is_dir():
        return None

    candidates = [path for path in account_root.glob(f"*/SavedVariables/{ADDON_NAME}.lua") if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


# ----------------------------------------------------------------- publish --

# Fields are delimited, so the separators are stripped out of user text rather
# than escaped: a title containing "|" must not be able to forge a field.
_SEPARATORS = str.maketrans({"|": "/", ";": "/", '"': "/", "\\": "/", "\r": " ", "\n": " ", "\t": " "})


def _field(value: Any, limit: int = 0) -> str:
    text = str(value if value is not None else "").translate(_SEPARATORS)
    if limit and len(text) > limit:
        return text[: limit - 1] + "\u2026"
    return text


def _lua_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _number(value: Any, default: float) -> float:
    """A numeric field from a row, or a default: a remote roster is not ours to trust.

    Non-finite counts as nonsense here too. `json.loads` hands over `Infinity`,
    `NaN` and an overflowing `1e999` without complaint, `float()` keeps them, and
    the `int()` on the other side of this refuses all three - which raised out of
    `render_payload`, so one bad field on one host cost the whole publish.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def render_payload(
    payload: dict,
    new_ids: Iterable[str] = (),
    *,
    host_status: dict[str, str] | None = None,
    acked: int = 0,
    error: str = "",
    notice: str = "",
) -> str:
    """The published snapshot as one delimited string.

    Header, then one record per session:

    ``id|status|age_seconds|host|profile|project|title|activity|messages|cost|offline|preview|activity_at``
    """
    generated = int(payload.get("generated_at", time.time()))
    sessions = list(payload.get("sessions", []) or [])
    projects = list(payload.get("projects", []) or [])
    host_status = host_status or {"local": "ok"}
    provider_status = payload.get("providers") or {"hermes": "ok"}

    header = "|".join(
        (
            PAYLOAD_TAG,
            f"bridge={BRIDGE_VERSION}",
            f"schema={PAYLOAD_SCHEMA}",
            f"generated={generated}",
            f"rows={len(sessions)}",
            "hosts=" + ";".join(f"{_field(name, 32)}:{state}" for name, state in sorted(host_status.items())),
            f"acked={int(acked or 0)}",
            "providers=" + ";".join(
                f"{_field(name, 32)}:{state}" for name, state in sorted(provider_status.items())
            ),
            "projects=" + ";".join(
                f"{_field(project.get('id'), 64)}~{_field(project.get('title'), 80)}"
                for project in projects[:40]
            ),
            # A bridge that cannot read the session store must say so in the
            # payload: an empty board with no explanation reads as "all clear".
            "error=" + _field(error, 160) if error else "error=",
            "notice=" + _field(notice, 240),
            "new=" + ";".join(_field(item, 64) for item in new_ids),
        )
    )

    records = [header]
    for session in sessions:
        records.append(
            "|".join(
                (
                    _field(session.get("id"), 64),
                    _field(session.get("status"), 16),
                    str(int(_number(session.get("age_s"), 0))),
                    _field(session.get("host") or "local", 32),
                    _field(session.get("profile"), 32),
                    _field(session.get("project"), 48),
                    _field(session.get("title"), 120),
                    _field(session.get("activity"), 80),
                    str(int(_number(session.get("messages"), 0))),
                    f"{_number(session.get('cost_usd'), 0.0):.4f}",
                    "1" if session.get("host_offline") else "0",
                    _field(session.get("preview"), 160),
                    str(_number(session.get("activity_at"), 0)),
                    _field(session.get("approval_request_id"), 128),
                    _field(session.get("approval_summary"), 160),
                    _field(session.get("user_input_request_id"), 128),
                    _field(session.get("user_input_question_id"), 64),
                    _field(session.get("user_input_summary"), 240),
                    _field(session.get("user_input_options"), 240),
                    _field(session.get("provider") or "hermes", 16),
                    _field(session.get("provider_label") or "Hermes", 32),
                    ",".join(_field(item, 24) for item in session.get("capabilities") or []),
                )
            )
        )

    return ENTRY_SEP.join(records)


def render_data(
    payload: dict,
    new_ids: Iterable[str] = (),
    *,
    host_status: dict[str, str] | None = None,
    acked: int = 0,
    error: str = "",
    notice: str = "",
) -> str:
    """The Data.lua the client reads at UI load: data, never code."""
    body = _lua_string(render_payload(payload, new_ids, host_status=host_status, acked=acked, error=error, notice=notice))
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"-- generated by agent-board at {stamp}; data only, do not edit by hand\n"
        f"AgentBoardData = {body}\n"
    )


def read_sync_stamp(path: Path | None) -> float | None:
    """When the player last synced, from the addon's own SavedVariables.

    The addon sets this immediately before it reloads the UI, so the value the
    client flushes is the moment of that sync.
    """
    if path is None:
        return None

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    match = re.search(r"^\s*AgentBoardSync\s*=\s*(\d+(?:\.\d+)?)", text, re.MULTILINE)
    if not match:
        return None

    try:
        return float(match.group(1))
    except ValueError:
        return None


def new_since(payload: dict, sync_stamp: float | None) -> list[str]:
    """Attention items that appeared since the player's last in-game sync.

    A needs-you transition is caused by the agent's message landing, so the
    session's own last-activity timestamp dates the transition closely enough to
    say "2 new since you last looked".
    """
    if not sync_stamp:
        return []

    fresh: list[str] = []
    for session in payload.get("sessions", []) or []:
        if session.get("status") not in ("needs", "error"):
            continue
        appeared_at = float(session.get("activity_at") or 0)
        if appeared_at > sync_stamp:
            fresh.append(str(session.get("id")))
    return fresh


def addon_path(base: Path, name: str = ADDON_NAME) -> Path:
    """Accept either the AddOns directory or the addon directory itself."""
    return base if base.name == name else base / name


def aggregate_board(*, limit: int = 15, days: float = 3.0) -> dict[str, Any]:
    """Hermes + T3 rows in the normalized provider-neutral shape."""
    data = board(limit=limit, days=days)
    provider_status = {"hermes": "error" if data.get("error") else "ok"}
    t3_snapshot = t3.snapshot()
    provider_status["t3"] = "ok" if t3_snapshot.get("connected") else "offline"
    rows = [*(data.get("sessions") or []), *(t3_snapshot.get("rows") or [])]
    return dict(
        data,
        sessions=rows,
        providers=provider_status,
        projects=list(t3_snapshot.get("projects") or []),
        t3_notice=str(t3_snapshot.get("notice") or ""),
    )


def recent_board(
    data: dict[str, Any],
    *,
    attention_window: float = 24 * 3600,
    reply_window: float = 12 * 3600,
    finished_window: float = 6 * 3600,
    limit: int = 32,
) -> dict[str, Any]:
    """Trim the live overlay to work that is active or genuinely recent."""
    rows: list[dict[str, Any]] = []
    for row in data.get("sessions", []) or []:
        status = str(row.get("status") or "")
        age = max(0.0, _number(row.get("age_s"), 0))
        if status in {"working", "waiting"}:
            keep = True
        elif status in {"needs", "error"}:
            keep = age <= attention_window
        elif status == "reply":
            keep = age <= reply_window
        elif status == "finished":
            keep = age <= finished_window
        else:
            keep = False
        if keep:
            rows.append(dict(row))

    rows.sort(key=lambda row: _number(row.get("activity_at"), 0), reverse=True)
    rows = rows[: max(1, limit)]
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "idle")
        counts[status] = counts.get(status, 0) + 1
    return dict(
        data,
        sessions=rows,
        counts=counts,
        attention=counts.get("needs", 0) + counts.get("error", 0),
        total_sessions=len(data.get("sessions", []) or []),
        visible_sessions=len(rows),
    )


def publish(
    addon_dir: Path,
    payload: dict | None = None,
    *,
    limit: int = 15,
    days: float = 3.0,
    hosts_enabled: bool = True,
    host_ttl: float = 120.0,
) -> dict:
    """Write the roster the addon will read, atomically.

    `host_ttl` is how stale a cached remote roster may be before a publish pays
    for a fresh fetch. The watcher passes a huge value and refreshes hosts on its
    own thread, so a slow or dead host can never hold up the board.
    """
    from . import hosts as host_module

    data = payload if payload is not None else aggregate_board(limit=limit, days=days)
    provider_status: dict[str, str] = dict(data.get("providers") or {"hermes": "ok"})
    t3_notice = str(data.get("t3_notice") or "")
    projects: list[dict[str, Any]] = list(data.get("projects") or [])
    # A caller-supplied host map wins: the fixture and any future caller that
    # merges its own hosts should not be overwritten by our default.
    host_status: dict[str, str] = dict(data.get("hosts") or {"local": "ok"})
    remote_error = ""

    if hosts_enabled:
        configured = host_module.load_hosts()
        if configured:
            try:
                # The ssh calls happen OUTSIDE the cache lock: holding it across a
                # dead host's timeout would stall the refresher thread, and the
                # whole point of the cache is that neither waits for the other.
                snapshot = host_module.load_cache()
                results = host_module.fetch_all(
                    configured, limit=limit, days=days, cache=snapshot, ttl=host_ttl
                )

                def apply_hosts(cache: dict) -> None:
                    nonlocal host_status, data
                    merged, host_status, cache_out = host_module.merge(
                        data.get("sessions", []) or [], results, cache=cache
                    )
                    cache.clear()
                    cache.update(cache_out)
                    # The addon recounts attention from the rows it receives, so
                    # there is nothing to fix up here beyond the merged rows.
                    data = dict(data, sessions=merged)

                host_module.mutate_cache(apply_hosts)
            except Exception as exc:  # noqa: BLE001 - a host problem must not stop the local publish
                remote_error = str(exc)

    directory = addon_path(addon_dir)
    target = directory / "Data.lua"
    target.parent.mkdir(parents=True, exist_ok=True)

    # A roster that could not be read is published AS an error, never as an empty
    # board: the panel has to say "Agent is not answering" instead of rendering
    # "all clear" over a bridge that is broken.
    dispatch_state = _load_state()
    roster_error = str(data.get("error") or "") if not any(
        row.get("provider") != "hermes" for row in data.get("sessions", []) or []
    ) else ""
    notice = str(dispatch_state.get("control_error") or "") or t3_notice

    data = dict(data, sessions=apply_read_suppressions(data.get("sessions", []) or [], dispatch_state))
    sync_stamp = read_sync_stamp(savedvars_path(directory))
    new_ids = new_since(data, sync_stamp)

    # 0644: the game reads this file, and it is not a secret.
    state_module.atomic_write(
        target,
        render_data(data, new_ids, host_status=host_status, acked=dispatch_state["acked_seq"], error=roster_error, notice=notice),
        mode=0o644,
    )

    return {
        "path": str(target),
        "sessions": len(data.get("sessions", []) or []),
        "attention": sum(row.get("status") in ("needs", "error") for row in data.get("sessions", []) or []),
        "new": new_ids,
        "sync_stamp": sync_stamp,
        "rows": list(data.get("sessions", []) or []),
        "host_status": host_status,
        "acked": dispatch_state["acked_seq"],
        "error": roster_error,
        "notice": notice,
        "remote_error": remote_error,
        "provider_status": provider_status,
        "projects": projects,
    }


def install(addon_dir: Path, *, force: bool = False) -> dict:
    """Copy the addon into the client's AddOns folder."""
    source = addon_source()
    if not source.is_dir():
        raise RuntimeError(f"addon sources missing at {source}")

    destination = addon_path(addon_dir)
    if destination.exists() and not force:
        # Two things can own this folder. If the copy came from a release zip or an
        # addon manager, overwriting it takes it out of that manager's hands and
        # silently pins the user to whatever version this checkout happens to be;
        # the only thing the bridge actually needs to write is the snapshot.
        raise RuntimeError(
            f"{destination} already exists.\n"
            "  If an addon manager or a release zip installed it, it is not ours to replace:\n"
            "  `agent-board wow publish` refreshes the snapshot without touching the code.\n"
            "  Pass --force only to replace a copy that this tool installed."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for item in sorted(source.iterdir()):
        if item.is_file():
            (destination / item.name).write_bytes(item.read_bytes())
            copied.append(item.name)

    return {"installed": str(destination), "files": copied}


# ------------------------------------------------------------------- inbox --


def _unescape_lua_literal(raw: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "\\" and index + 1 < len(raw):
            nxt = raw[index + 1]
            mapping = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "'": "'"}
            if nxt in mapping:
                out.append(mapping[nxt])
                index += 2
                continue
            if nxt.isdigit():
                # Bounded on purpose: `\2a` or `\12z` used to raise out of here,
                # and this parser's contract is that a torn or hand-edited file
                # reads as "nothing new", not as an exception in the watch loop.
                #
                # `str.isdigit()` is true for more than `\d` matches: superscripts
                # and fractions (`\²`, `\½`) pass the test above and fail the regex,
                # and taking `nxt` as the digits then reached int() and raised -
                # taking the watch loop down from a file that the client writes.
                # No match means the backslash was not a decimal escape, so it is
                # left alone like any other unknown one.
                match = re.match(r"\d{1,3}", raw[index + 1 :])
                if match:
                    code = int(match.group(0))
                    out.append(chr(code) if 0 < code < 0x110000 else nxt)
                    index += 1 + len(match.group(0))
                    continue
        out.append(char)
        index += 1
    return "".join(out)


def read_outbox(path: Path) -> list[dict[str, str]]:
    """Entries the addon queued, newest last.

    Wire format is ``seq|host|session|text``; a three-field entry (an addon older
    than the host routing) still reads, as a local reply.

    Tolerant by design: a half-written file (the client was killed mid-flush)
    must read as "nothing new", never as an exception in the watcher loop.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    match = re.search(r"^\s*AgentBoardOutbox\s*=\s*\"((?:[^\"\\]|\\.)*)\"", text, re.MULTILINE)
    if not match:
        return []

    payload = _unescape_lua_literal(match.group(1))
    entries: list[dict[str, str]] = []

    for chunk in payload.split(ENTRY_SEP):
        chunk = chunk.strip()
        if not chunk:
            continue

        parts = [part.strip() for part in chunk.split(FIELD_SEP)]

        # Current wire format: seq|kind|provider|host|session|text. Older shapes
        # remain readable so a bridge update cannot strand queued work.
        if len(parts) >= 6:
            seq, kind, provider, host, session_id = parts[0], parts[1], parts[2], parts[3], parts[4]
            message = FIELD_SEP.join(parts[5:])
        elif len(parts) >= 5:
            seq, kind, host, session_id = parts[0], parts[1], parts[2], parts[3]
            provider = "hermes"
            message = FIELD_SEP.join(parts[4:])
        elif len(parts) == 4:
            seq, kind, host, session_id, message = parts[0], "reply", parts[1], parts[2], parts[3]
            provider = "hermes"
        elif len(parts) == 3:
            seq, kind, host, session_id, message = parts[0], "reply", "local", parts[1], parts[2]
            provider = "hermes"
        else:
            continue

        legacy = len(parts) < 6
        if kind not in OUTBOX_KINDS:
            kind = "reply"
        # A hand-off carries no words, so only a reply needs text.
        if not session_id or (kind == "reply" and not message):
            continue
        # A three-field line from a truncated file reads as seq|session|text and
        # turns the first field into a "session id" like "reply". Only an id that
        # looks like one is worth sending anything to.
        if not SESSION_ID_RE.fullmatch(session_id):
            continue
        # A seq that is not a number cannot be acked or deduped, and int() on it
        # used to raise out of the watcher loop.
        # isdigit accepts superscripts which int rejects; enormous decimal
        # strings also exceed Python's conversion limit before ack calculation.
        if not re.fullmatch(r"[0-9]{1,20}", seq):
            continue

        entries.append(
            {
                "seq": seq,
                "kind": kind,
                "provider": provider or "hermes",
                "host": host or "local",
                "session_id": session_id,
                "text": message,
                "legacy": legacy,
            }
        )

    return entries


def acked_seq() -> int:
    """The highest entry seq the bridge has settled, for the next payload."""
    return int(_load_state().get("acked_seq") or 0)


def _load_state() -> dict[str, Any]:
    """Dispatch bookkeeping, and what to do when it cannot be trusted.

    The addon only ever appends to its outbox (it has no idea what the bridge did
    with an entry), so this file is the ONLY thing standing between a lost state
    and every queued reply being sent again. A state file that exists but does not
    parse is therefore treated as "assume everything was already sent": a missing
    reply is visible and fixable, a duplicate reply into a live session is not.
    """
    try:
        raw = _STATE_PATH.read_bytes()
    except OSError:
        return {"dispatched": [], "acked_seq": 0}

    try:
        state = json.loads(raw)
        if not isinstance(state, dict):
            raise ValueError("state must be an object")
        dispatched = state.get("dispatched", [])
        ack = state.get("acked_seq", 0)
        pending = state.get("pending", {})
        marks = state.get("read_marks", {})
        if (not isinstance(dispatched, list) or any(not isinstance(key, str) for key in dispatched)
                or not isinstance(ack, int) or isinstance(ack, bool) or ack < 0
                or not isinstance(pending, dict) or not isinstance(marks, dict)):
            raise ValueError("invalid dispatch ledger fields")
        for item in pending.values():
            if not isinstance(item, dict):
                raise ValueError("invalid pending entry")
            if not isinstance(item.get("pid", 0), int) or not isinstance(item.get("exit", 0), (int, type(None))):
                raise ValueError("invalid pending process")
            if any(not isinstance(item.get(field, ""), str) for field in
                   ("seq", "kind", "provider", "host", "session_id", "text", "log", "completion")):
                raise ValueError("invalid pending fields")
        if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in marks.values()):
            raise ValueError("invalid read timestamp")
    except (ValueError, TypeError):
        backup = _STATE_PATH.with_suffix(".corrupt.json")
        try:
            backup.write_bytes(raw)
        except OSError:
            pass

        # Persist the quarantine until dispatch has seen the outbox. A publish
        # reads state too, and must not consume a one-read recovery flag.
        fresh = {"dispatched": [], "acked_seq": 0, "recovered_from": str(backup), "unreadable": True}
        try:
            _save_state(fresh)
        except OSError:
            pass

        return {**fresh, "unreadable": True}

    state.setdefault("dispatched", [])
    state.setdefault("acked_seq", 0)
    return state


def _save_state(state: dict[str, Any]) -> None:
    """Write the state atomically: a crash mid-write must not lose the dedupe."""
    state_module.atomic_write(_STATE_PATH, json.dumps(state, indent=2))


def _entry_key(entry: dict[str, str]) -> str:
    # The seq counter lives in the addon's per-character SavedVariables, so two
    # characters can both start at 1. Content plus seq is the honest identity.
    return "|".join(
        (
            entry["seq"],
            entry.get("kind", "reply"),
            entry.get("provider", "hermes"),
            entry.get("host", ""),
            entry["session_id"],
            entry["text"],
        )
    )


# Ways to put text where the player can paste it. First one installed wins.
CLIPBOARD_COMMANDS = (
    ["wl-copy"],
    ["xclip", "-selection", "clipboard"],
    ["xsel", "--clipboard", "--input"],
    ["pbcopy"],
)


def session_env() -> dict[str, str]:
    """The display env a clipboard or notifier needs.

    A bridge started by a service, a cron job, or an ssh command has no
    WAYLAND_DISPLAY, and wl-copy exits 1 without one. The socket is right there
    on disk, so find it instead of refusing to copy.
    """
    env = dict(os.environ)
    if not env.get("XDG_RUNTIME_DIR"):
        runtime = Path(f"/run/user/{os.getuid()}")
        if runtime.exists():
            env["XDG_RUNTIME_DIR"] = str(runtime)
    if not env.get("WAYLAND_DISPLAY") and not env.get("DISPLAY"):
        candidates = sorted(Path("/run/user") .glob(f"{os.getuid()}/wayland-*"))
        if candidates:
            env["WAYLAND_DISPLAY"] = candidates[0].name
    if not env.get("DBUS_SESSION_BUS_ADDRESS"):
        bus = Path(f"/run/user/{os.getuid()}/bus")
        if bus.exists():
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"
    return env


def game_running() -> bool:
    """Whether a WoW client process is visible in this user session."""
    wanted = {
        "wow.exe",
        "wowclassic.exe",
        "wowb.exe",
        "wowt.exe",
        "wowclassic",
        "wowb",
        "wowt",
    }
    try:
        processes = list(Path("/proc").iterdir())
    except OSError:
        return False
    for process in processes:
        if not process.name.isdigit():
            continue
        try:
            raw = (process / "cmdline").read_bytes()
        except OSError:
            continue
        arguments = [item for item in raw.decode("utf-8", "replace").split("\0") if item]
        if not arguments:
            continue
        executable = arguments[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
        if executable in wanted:
            return True
        # Wine keeps its own executable in argv[0] and names the Windows game in
        # a later argument. Only search those arguments for an actual Wine host;
        # scanning every process argument made shell scripts that merely mention
        # WowClassic.exe look like a running game.
        if "wine" in executable:
            for argument in arguments[1:]:
                name = argument.replace("\\", "/").rsplit("/", 1)[-1].lower()
                if name in wanted:
                    return True
    return False


def _clipboard(text: str) -> str:
    """Put text on the clipboard, if this machine has a clipboard tool.

    The text goes through a temp FILE rather than a pipe: wl-copy forks a child
    that keeps the selection alive, and that child would hold a pipe open until
    the timeout, which reads as a failure even though the copy worked.
    """
    env = session_env()
    for command in CLIPBOARD_COMMANDS:
        if not shutil.which(command[0]):
            continue

        handle, path = tempfile.mkstemp(prefix="hermes-handoff-", suffix=".txt")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as source:
                source.write(text)
            with open(path, "r", encoding="utf-8") as source:
                done = subprocess.run(
                    command,
                    stdin=source,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=5,
                    env=env,
                )
        except (OSError, subprocess.SubprocessError):
            continue
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

        if done.returncode == 0:
            return command[0]
    return ""


def hand_off(entry: dict[str, str], *, keybind: str = "SUPER+SHIFT+H") -> dict[str, Any]:
    """Answer a hand-off request from the board.

    Agent has no outside API to focus a session in the desktop app, and faking
    one by pretending to read the player's screen would be worse than useless.
    So the honest version: the session id lands on the clipboard and a desktop
    notification says which one, and the player pastes it into the app they
    already have open.
    """
    from . import notify as notify_module

    session_id = entry.get("session_id", "")
    tool = _clipboard(session_id)

    try:
        notify_module.notify_desktop(
            {
                "kind": "handoff",
                "id": session_id,
                "title": f"hand-off: {session_id}",
                "project": entry.get("project", ""),
                "detail": "session id is on your clipboard" if tool else "copy the id from the board",
            }
        )
        notified = True
    except Exception:  # noqa: BLE001 - a notification is a courtesy, never a failure
        notified = False

    return {"ok": True, "session_id": session_id, "clipboard": tool, "notified": notified, "keybind": keybind}


def _read_key(host: str, session_id: str) -> str:
    return json.dumps([host or "local", session_id], separators=(",", ":"))


def apply_read_suppressions(rows: Iterable[dict], state: dict) -> list[dict]:
    marks = state.get("read_marks") or {}
    result = []
    for original in rows:
        row = dict(original)
        key = _read_key(str(row.get("host") or "local"), str(row.get("id") or ""))
        activity = _number(row.get("activity_at"), 0)
        if activity > 0 and marks.get(key) == activity and row.get("status") in ("needs", "reply", "error", "finished"):
            row.update(status="idle", status_label="Idle", dismissed=True, unread=False)
        result.append(row)
    return result


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _reconcile_pending(state: dict[str, Any]) -> tuple[list[str], list[dict[str, str]]]:
    """Settle the CLI fallbacks that were still running when we last looked.

    A detached `hermes chat --resume` takes minutes and can refuse the reply in
    its first second. Until it is seen to finish, its entry is neither delivered
    nor failed: it stays journaled and unacknowledged without launching again.
    """
    from . import backend as backend_module

    pending = state.get("pending") or {}
    delivered: list[str] = []
    refused: list[dict[str, str]] = []

    for key, item in list(pending.items()):
        completion = str(item.get("completion") or "")
        exit_code = backend_module.cli_exit(completion) if completion else item.get("exit")
        if exit_code is None and _pid_running(int(item.get("pid") or 0)):
            continue

        log = str(item.get("log") or "")
        reason = backend_module.refusal_reason(Path(log)) if log else ""
        entry = {
            field: item.get(field, "")
            for field in ("seq", "kind", "provider", "host", "session_id", "text")
        }
        entry.setdefault("kind", "reply")

        if exit_code is None and not reason:
            # No proof of success and no proof of failure: preserve the journal
            # and prevent a duplicate launch, even after a watcher restart.
            refused.append({**entry, "outcome": "uncertain: CLI exited without a completion record; inspect the log"})
            continue
        if reason or (exit_code is not None and int(exit_code or 0) != 0):
            refused.append({**entry, "outcome": f"failed: the CLI turn was refused ({reason or 'see the log'})"})
        else:
            delivered.append(key)

        pending.pop(key, None)

    state["pending"] = pending
    return delivered, refused


def dispatch(
    entries: Iterable[dict[str, str]],
    *,
    channel: str = "auto",
    state: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Send queued replies through the channels that match where the session lives."""
    from . import hosts as host_module

    entries = list(entries)
    state = state if state is not None else _load_state()
    seen = set(state.get("dispatched", []))
    results: list[dict[str, str]] = []

    if state.get("unreadable"):
        # Consume this uncertain baseline once, before permitting new entries.
        # Keeping the skipped keys makes even a stale SavedVariables file safe.
        state["dispatched"] = sorted({_entry_key(entry) for entry in entries})
        state["acked_seq"] = max((int(entry["seq"]) for entry in entries), default=0)
        state["control_error"] = "Dispatch ledger was unreadable; queued actions skipped. Inspect Agent before resending."
        state.pop("unreadable", None)
        _save_state(state)
        return [
            {**entry, "outcome": f"skipped: dispatch state was unreadable ({state.get('recovered_from', 'no copy')})"}
            for entry in entries
        ]

    # First settle whatever the previous round left running.
    delivered, refused_earlier = _reconcile_pending(state)
    seen.update(delivered)
    results.extend(refused_earlier)

    # ...and treat what is still running as spoken for. A pending entry is not in
    # `dispatched` by design - it has not been delivered - so without this the
    # watcher launches another CLI turn for the same reply on every round and
    # overwrites the pid of the turn already running. The reply then lands in the
    # same live session once per round, which is the one thing this module's own
    # notes call unfixable, and no round can ever settle the first child.
    inflight = set(state.get("pending") or {})

    for entry in entries:
        key = _entry_key(entry)
        if key in seen or key in inflight:
            continue

        host = (entry.get("host") or "").strip()
        provider = (entry.get("provider") or "hermes").strip() or "hermes"
        outcome = "sent"
        provider_result: dict[str, Any] | None = None

        if provider == "t3":
            result = t3.dispatch(
                {
                    "kind": entry.get("kind", "reply"),
                    "host": host,
                    "session_id": entry.get("session_id", ""),
                    "text": entry.get("text", ""),
                    "workspace_root": entry.get("workspace_root", ""),
                    "title": entry.get("title", ""),
                    "answers": entry.get("answers"),
                    "request_id": entry.get("request_id"),
                }
            )
            provider_result = result
            outcome = "sent" if result.get("ok") else f"failed: {result.get('message') or 'T3 rejected the action'}"
        # Control messages are their own kind, never inferred from the text. The
        # text form is honoured only for an entry from an addon that predates the
        # kind field: otherwise a player who replies "!focus" gets a clipboard
        # instead of an answer, which a gate caught only on the addon side.
        elif entry.get("kind") == "focus" or (entry.get("legacy") and entry.get("text", "").strip() == "!focus"):
            handed = hand_off(entry)
            outcome = "handed_off" if handed.get("clipboard") else "handed_off_no_clipboard"
        elif entry.get("kind") == "mark_read":
            activity = _number(entry.get("text"), 0)
            if activity <= 0:
                outcome = "failed: mark read requires the displayed activity timestamp"
            else:
                state.setdefault("read_marks", {})[_read_key(host, entry["session_id"])] = activity
                outcome = "marked_read"
                state.pop("control_error", None)
        elif entry.get("kind") == "stop":
            # Persist before calling: a lost RPC response or bridge crash must
            # never cause a later turn to be interrupted by an automatic retry.
            seen.add(key)
            state["dispatched"] = sorted(seen)
            state["control_error"] = "Stop delivery uncertain; inspect Agent before trying again"
            _save_state(state)
            try:
                if host and host != "local":
                    raise RuntimeError("stop is available only for a local desktop backend")
                backend.stop_turn(entry["session_id"])
                outcome = "stopped"
                state.pop("control_error", None)
            except Exception as exc:
                outcome = f"stop_failed_or_uncertain: {exc}; inspect Agent before trying again"
                state["control_error"] = outcome
        elif host and host != "local":
            sent = host_module.reply(host, entry["session_id"], entry["text"])
            outcome = "sent_remote" if sent.get("ok") else f"failed: {sent.get('error')}"
        else:
            try:
                if channel == "cli":
                    raise RuntimeError("cli channel requested")
                backend.submit_reply(entry["session_id"], entry["text"])
            except Exception:  # noqa: BLE001 - any RPC failure means the CLI path
                try:
                    verdict = backend.submit_reply_cli(entry["session_id"], entry["text"])
                except Exception as exc:  # noqa: BLE001
                    outcome = f"failed: {exc}"
                else:
                    if verdict.get("ok") is False:
                        outcome = f"failed: the CLI turn was refused ({verdict.get('reason')})"
                    elif verdict.get("ok") is None:
                        # Still running: journal it and do NOT ack it. A reply is not
                        # delivered until the child has been seen to finish.
                        state.setdefault("pending", {})[key] = {
                            "pid": verdict.get("pid"),
                            "log": str(verdict.get("log_path") or ""),
                            "exit": verdict.get("exit"),
                            "completion": str(verdict.get("completion_path") or ""),
                            "seq": entry["seq"],
                            "kind": entry["kind"],
                            "provider": provider,
                            "host": entry["host"],
                            "session_id": entry["session_id"],
                            "text": entry["text"],
                            "at": time.time(),
                        }
                        outcome = "sent_via_cli_pending"
                    else:
                        outcome = "sent_via_cli"

        # Failed and pending entries both stay unsettled: a failure deserves a
        # retry, and a pending one has not finished being a question.
        if outcome.startswith("failed") or outcome == "sent_via_cli_pending":
            record = {**entry, "outcome": outcome}
            if provider_result is not None:
                record["provider_result"] = provider_result
            results.append(record)
            continue

        seen.add(key)
        record = {**entry, "outcome": outcome}
        if provider_result is not None:
            record["provider_result"] = provider_result
        results.append(record)

    state["dispatched"] = sorted(seen)[-500:]

    # Only the contiguous settled prefix may be acknowledged. A failed entry
    # below a later success would otherwise be acked and dropped by the addon
    # with nothing left to retry it: the reply is simply lost.
    # Live-overlay actions have their own unique sequence ids and must never move
    # the addon's contiguous acknowledgement watermark.
    addon_entries = [entry for entry in entries if not entry.get("live")]
    settled = [int(e["seq"] or 0) for e in addon_entries if _entry_key(e) in seen]
    pending = [int(e["seq"] or 0) for e in addon_entries if _entry_key(e) not in seen]
    ceiling = (min(pending) - 1) if pending else None
    if settled:
        mark = max(settled)
        if ceiling is not None:
            mark = min(mark, ceiling)
        state["acked_seq"] = max(int(state.get("acked_seq") or 0), mark)

    _save_state(state)
    return results


def dispatch_live(action: dict[str, Any], *, state: dict[str, Any], channel: str = "auto") -> dict[str, Any]:
    """Dispatch one action sent directly by the desktop overlay."""
    kind = str(action.get("kind") or "").strip()
    provider = str(action.get("provider") or "hermes").strip() or "hermes"
    host = str(action.get("host") or "local").strip() or "local"
    session_id = str(action.get("session_id") or "").strip()
    text = str(action.get("text") or "")
    workspace_root = str(action.get("workspace_root") or "").strip()
    title = str(action.get("title") or "").strip()

    if kind not in LIVE_KINDS:
        return {"ok": False, "message": f"unsupported action: {kind or 'missing'}"}
    if provider not in {"hermes", "t3"}:
        return {"ok": False, "message": f"unsupported provider: {provider}"}
    if kind == "new_project":
        if provider != "t3":
            return {"ok": False, "message": "projects can only be added to T3 Code"}
        if not workspace_root or not Path(workspace_root).is_absolute():
            return {"ok": False, "message": "choose an absolute project folder"}
    elif not session_id or not SESSION_ID_RE.fullmatch(session_id):
        return {"ok": False, "message": "a valid session or project id is required"}
    if (kind in {"reply", "new"}) and not text.strip():
        return {"ok": False, "message": "write a message before sending"}
    if len(text) > 100_000:
        return {"ok": False, "message": "message is too large"}

    if provider == "hermes" and host == "local" and kind in {"approve", "decline", "answer"}:
        from .hermes_live import respond
        try:
            return respond(action)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    if provider == "hermes" and kind in {"settle", "unsettle"}:
        return {"ok": False, "message": "Hermes sessions are archived, not settled"}
    if provider == "hermes" and kind == "archive":
        if host != "local":
            return {"ok": False, "message": "archive is available only for local Hermes sessions"}
        result = hermes_store.set_flags(session_id, archived=True)
        return {"ok": bool(result.get("ok")), "message": "Archived" if result.get("ok") else result.get("error", "archive failed"),
                "error": "" if result.get("ok") else result.get("error", "archive failed")}
    if provider == "hermes" and kind == "mark_unread":
        if host != "local":
            return {"ok": False, "message": "mark unread is available only for local Hermes sessions"}
        result = hermes_store.set_flags(session_id, read=False)
        if not result.get("ok"):
            return {"ok": False, "message": result.get("error", "mark unread failed"), "error": result.get("error", "mark unread failed")}
        # Drop the bridge's own read marker too, or it would keep the row read.
        if (state.get("read_marks") or {}).pop(_read_key(host, session_id), None) is not None:
            _save_state(state)
        return {"ok": True, "message": "Marked unread"}
    if provider == "hermes" and host == "local" and kind == "mark_read":
        # Hermes's own read marker, so the desktop app agrees; the bridge's
        # marker below still applies if Hermes cannot be reached.
        hermes_store.set_flags(session_id, read=True)

    if kind == "reply" and provider == "hermes" and host == "local" and action.get("delivery"):
        try:
            result = backend.submit_reply(session_id, text, delivery=str(action["delivery"]))
            status = result.get("status") if isinstance(result, dict) else None
            if status not in {"streaming", "queued", "steered", "redirected"}:
                return {"ok": False, "error": "Backend did not confirm delivery. Check the conversation before retrying."}
            return {"ok": True, "message": status, "pending": status == "queued"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    entry = {
        "seq": str(time.time_ns()),
        "kind": kind,
        "provider": provider,
        "host": host,
        "session_id": session_id,
        "text": text,
        "workspace_root": workspace_root,
        "title": title,
        "live": True,
        "answers": action.get("answers"),
        "request_id": action.get("request_id"),
    }
    results = dispatch([entry], channel=channel, state=state)
    result = results[0] if results else {"outcome": "failed: no dispatch result"}
    provider_result = result.get("provider_result") if isinstance(result.get("provider_result"), dict) else {}
    outcome = str(result.get("outcome") or "")
    ok = not outcome.startswith("failed") and outcome not in {"uncertain", "skipped"} and not outcome.startswith("stop_failed_or_uncertain")
    return {
        "ok": ok,
        "message": outcome,
        "outcome": outcome,
        "session_id": session_id,
        "pending": outcome == "sent_via_cli_pending",
        "thread_id": provider_result.get("thread_id") or "",
        "project_id": provider_result.get("project_id") or "",
        "message_id": provider_result.get("message_id") or "",
    }


def inbox(*, addon_dir: Path | None = None, dispatch_replies: bool = False, channel: str = "auto") -> dict:
    directory = addon_dir or pick_addon_dir()
    if directory is None:
        return {"error": "no WoW client found (set AGENT_BOARD_ADDON_DIR)", "entries": []}

    path = savedvars_path(directory)
    if path is None:
        return {"error": f"no SavedVariables for {ADDON_NAME} yet under {directory}", "entries": []}

    entries = read_outbox(path)
    result: dict[str, Any] = {"path": str(path), "entries": entries}
    if dispatch_replies:
        result["results"] = dispatch(entries, channel=channel)
    return result


def _watch(
    *,
    addon_dir: Path | None = None,
    interval: float = 10.0,
    channel: str = "auto",
    once: bool = False,
    iterations: int | None = None,
    notify_desktop: bool = True,
    notify_platforms: Iterable[str] = (),
    notify_enabled: bool = True,
    limit: int = 15,
    days: float = 3.0,
    hosts_enabled: bool = True,
    host_refresh_interval: float = 60.0,
    health_file: Path | None = None,
) -> list[dict]:
    """Publish status out, dispatch replies in, ring the bell, forever (or N rounds)."""
    directory = addon_dir or pick_addon_dir()
    if directory is None:
        return [{"error": "no WoW client found (set AGENT_BOARD_ADDON_DIR)"}]

    from . import notify as notifier
    from . import hosts as host_module

    state = _load_state()
    state_lock = threading.RLock()
    reports: list[dict] = []
    count = 0
    stop_refresher = threading.Event()
    stop_gamewatch = threading.Event()

    def _refresh_hosts() -> None:
        """Keep the host cache warm off the publish path.

        A dead host can take its full timeout, so this never runs inline: the
        board is published from cache and the remote rows catch up when the
        fetch returns.
        """
        while not stop_refresher.is_set():
            configured = [host for host in host_module.load_hosts() if host.enabled]
            if configured:
                try:
                    # Fetch first, lock only for the write: the lock exists to
                    # serialise the read-modify-write, not to serialise ssh.
                    results = host_module.fetch_all(
                        configured, limit=limit, days=days, ttl=0, cache=host_module.load_cache()
                    )

                    def refresh(cache: dict) -> None:
                        _rows, _status, cache_out = host_module.merge([], results, cache=cache)
                        cache.clear()
                        cache.update(cache_out)

                    host_module.mutate_cache(refresh)
                except Exception:  # noqa: BLE001 - never let a host kill the watcher
                    pass
            stop_refresher.wait(max(15.0, host_refresh_interval))

    refresher = None
    if hosts_enabled and host_module.load_hosts():
        refresher = threading.Thread(target=_refresh_hosts, daemon=True, name="agent-board-hosts")
        refresher.start()

    def _watch_game() -> None:
        if os.environ.get("AGENT_BOARD_AUTO_OVERLAY", "1") == "0":
            return
        log_path = state_module.STATE_DIR / "gamewatch.log"
        while not stop_gamewatch.is_set():
            try:
                running = game_running()
                overlay_running = control.is_running()
                overlay_status = control.send("ping") if overlay_running else {}
                if running and overlay_running and not overlay_status.get("game_aware"):
                    control.send("quit")
                    time.sleep(1.0)
                    overlay_running = False
                if running and not overlay_running:
                    environment = session_env()
                    environment["AGENT_BOARD_GAME_AWARE"] = "1"
                    child = subprocess.Popen(
                        [str(ROOT / "bin" / "agent-board"), "overlay", "--mode", "badge"],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                        env=environment,
                    )
                    state_module.atomic_write(
                        log_path,
                        json.dumps({"at": time.time(), "event": "launched", "pid": child.pid}) + "\n",
                    )
            except Exception as exc:  # noqa: BLE001 - a game detector must never kill the bridge
                state_module.atomic_write(
                    log_path,
                    json.dumps({"at": time.time(), "event": "error", "error": str(exc)}) + "\n",
                )
            stop_gamewatch.wait(5.0)

    gamewatch = None
    if not once and iterations is None:
        gamewatch = threading.Thread(target=_watch_game, daemon=True, name="agent-board-gamewatch")
        gamewatch.start()

    live_hub = None
    hermes_monitor = None
    if not once and iterations is None:
        from .hermes_live import Monitor
        hermes_monitor = Monitor()
        def live_snapshot() -> dict[str, Any]:
            return recent_board(hermes_monitor.enrich(aggregate_board(limit=limit, days=days)))

        def live_action(action: dict[str, Any]) -> dict[str, Any]:
            with state_lock:
                return dispatch_live(action, state=state, channel=channel)

        # A snapshot costs a few milliseconds and only a changed board wakes the
        # overlay, so a short interval is what makes streamed replies feel live.
        live_hub = live_module.LiveBridge(snapshot=live_snapshot, action=live_action, interval=0.25)
        live_hub.start()

    while True:
        report: dict[str, Any] = {"at": time.time()}
        if live_hub is not None:
            data = live_hub.wait_ready(timeout=5.0).get("board") or {}
        else:
            data = aggregate_board(limit=limit, days=days)

        try:
            report["published"] = publish(
                directory, data, hosts_enabled=hosts_enabled, host_ttl=1e9 if refresher else 120.0
            )
        except Exception as exc:  # noqa: BLE001
            report["publish_error"] = str(exc)

        if health_file is not None:
            problems = []
            if report.get('publish_error'):
                problems.append('publish_error')
            if data.get('error') and not any(
                row.get('provider') != 'hermes' for row in data.get('sessions', []) or []
            ):
                problems.append('session_store_error')
            state_module.atomic_write(Path(health_file), json.dumps({
                'at': time.time(), 'ok': not problems, 'problems': problems,
            }) + '\n')

        if notify_enabled:
            # Notify from what was PUBLISHED, which includes the merged remote
            # rows: a needs-you on another machine used to be silent.
            published_rows = (report.get("published") or {}).get("rows")
            try:
                report["notify"] = notifier.transitions(
                    published_rows if published_rows is not None else (data.get("sessions", []) or []),
                    desktop=notify_desktop,
                    platforms=notify_platforms,
                )
            except Exception as exc:  # a notification ledger failure must not stop inbox delivery
                report["notify_error"] = str(exc)

        path = savedvars_path(directory)
        if path is not None:
            entries = read_outbox(path)
            if entries:
                # Guarded like the publish above: a state that cannot be written
                # (full disk, read-only home) must not stop the watcher.
                try:
                    with state_lock:
                        report["dispatched"] = dispatch(entries, channel=channel, state=state)
                except Exception as exc:  # noqa: BLE001
                    report["dispatch_error"] = str(exc)
            report["outbox"] = len(entries)

        if health_file is not None:
            problems = [name for name in ('publish_error', 'dispatch_error') if report.get(name)]
            if data.get('error'):
                problems.append('session_store_error')
            state_module.atomic_write(Path(health_file), json.dumps({
                'at': time.time(), 'ok': not problems, 'problems': problems,
            }) + '\n')

        if once or iterations is not None:
            reports.append(report)
        else:
            # A watcher that runs for days would otherwise hold every round it
            # has ever done, sessions and previews included.
            reports.append(report)
            del reports[:-10]
        count += 1

        if once or (iterations is not None and count >= iterations):
            break
        time.sleep(interval)

    stop_refresher.set()
    stop_gamewatch.set()
    if live_hub is not None:
        live_hub.stop()
    if hermes_monitor is not None:
        hermes_monitor.stop()
    return reports


def watch(**kwargs) -> list[dict]:
    """One dispatcher owns the ledger while its watcher is alive.

    Automatic login startup must not race a manually launched watcher and send
    the same queued action twice. The kernel releases this lock after a crash.
    """
    import fcntl
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _STATE_PATH.with_name("watch.lock").open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return [{"error": "The Agent WoW bridge is already running. Use agent-board status to check it."}]
        return _watch(**kwargs)
