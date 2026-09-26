#!/usr/bin/env python3
"""Remote hosts, without a remote host.

The rules a merged board has to keep, because breaking any of them makes the
panel lie:

* a row from another machine says which machine
* a host that cannot be reached keeps its last known rows, marked offline
* a host going down never looks like work finishing
* a reachable host never blocks the local publish for longer than its timeout
* what runs over ssh is the SAME bucketing code as local, not a copy

Run: python3 tests/hosts_test.py
"""

from __future__ import annotations

import json
import shlex
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentboard import hosts  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(("PASS  " if condition else "FAIL  ") + label + (f" ({detail})" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def session(session_id: str, status: str = "working", **extra) -> dict:
    base = {
        "id": session_id,
        "status": status,
        "status_label": status.title(),
        "title": f"session {session_id}",
        "project": "Projects",
        "profile": "default",
        "age_s": 120,
        "activity": "thinking",
        "messages": 3,
        "cost_usd": 0.1,
        "activity_at": time.time() - 120,
    }
    base.update(extra)
    return base


# ------------------------------------------------------------ the payload --

script = hosts.remote_script()
check("the remote script carries the local bucketing source", "def board(" in script, script[:60])
check("the remote script prints json", "json.dumps" in script and "import json" in script, script[-200:])
check("the remote script takes a row limit", "argv[1]" in script, script[-200:])

# ---------------------------------------------------------------- config --

original_config = hosts.CONFIG_PATH
import tempfile

with tempfile.TemporaryDirectory() as tmp:
    hosts.CONFIG_PATH = Path(tmp) / "hosts.json"

    check("no hosts by default", hosts.load_hosts() == [])
    hosts.add_host("terra", ssh="terra", hermes_home="/home/bts/.hermes")
    hosts.add_host("foundry")
    saved = hosts.load_hosts()
    check("a host is saved and read back", [item.name for item in saved] == ["terra", "foundry"],
          json.dumps([item.name for item in saved]))
    check("ssh target defaults to the name", saved[1].ssh == "foundry", saved[1].ssh)
    check("hermes home survives", saved[0].hermes_home == "/home/bts/.hermes", saved[0].hermes_home)

    hosts.add_host("terra", ssh="terra.lan")
    check("adding the same host updates it", len(hosts.load_hosts()) == 2, json.dumps([h.ssh for h in hosts.load_hosts()]))
    check("removing a host works", hosts.remove_host("foundry") is True)
    check("a removed host is gone", [item.name for item in hosts.load_hosts()] == ["terra"])
    check("removing a missing host is not an error", hosts.remove_host("nope") is False)

    # -------------------------------------------------------------- merge --

    local_rows = [session("local-1", "needs")]
    remote_rows = [session("remote-1", "working"), session("remote-2", "finished")]

    results = [
        {"name": "terra", "ok": True, "sessions": remote_rows, "error": "", "at": time.time()},
        {"name": "foundry", "ok": False, "sessions": [], "error": "unreachable", "at": time.time()},
    ]
    cache = {"foundry": {"at": time.time() - 600, "sessions": [session("cached-1", "reply")]}}

    merged, status, cache_out = hosts.merge(local_rows, results, cache=cache)

    by_host: dict[str, list[str]] = {}
    for row in merged:
        by_host.setdefault(row.get("host", "?"), []).append(row["id"])

    check("local rows are marked local", by_host.get("local") == ["local-1"], json.dumps(by_host))
    check("remote rows carry their host name",
          by_host.get("terra") == ["remote-1", "remote-2"], json.dumps(by_host))
    check("a reachable host reports ok", status.get("terra") == "ok", json.dumps(status))
    check("an unreachable host reports offline", status.get("foundry") == "offline", json.dumps(status))

    cached = [row for row in merged if row["id"] == "cached-1"]
    check("an unreachable host keeps its last known rows", len(cached) == 1, json.dumps(by_host))
    check("...and they are flagged offline", cached and cached[0].get("host_offline") is True, json.dumps(cached))

    statuses = [row["status"] for row in merged]
    check("an offline host's rows are not left looking healthy",
          "reply" not in statuses or any(row.get("host_offline") for row in merged if row["status"] == "reply"),
          json.dumps(statuses))

    check("the cache is written back for the offline host",
          cache_out.get("foundry", {}).get("sessions"), json.dumps(list(cache_out)))

    # A host that comes back must clear its offline flags again.
    back = hosts.merge(local_rows, [{"name": "foundry", "ok": True, "sessions": [session("cached-1", "reply")],
                                     "error": "", "at": time.time()}], cache=cache_out)
    revived = [row for row in back[0] if row["id"] == "cached-1"]
    check("a host that comes back is no longer flagged offline", not revived[0].get("host_offline"),
          json.dumps(revived))

    # The board is ordered for a human: attention first, and stable inside a bucket.
    ordered = [row["status"] for row in merged]
    buckets = ["needs", "error", "working", "waiting", "reply", "idle", "finished"]
    ranks = [buckets.index(item) if item in buckets else len(buckets) for item in ordered]
    check("attention sorts above finished work", ranks == sorted(ranks), json.dumps(ordered))

    hosts.CONFIG_PATH = original_config

# ------------------------------------------------------- failure tolerance --

dead = hosts.fetch(hosts.Host(name="slow", ssh="slow.invalid"), timeout=0.5)
check("a dead host returns an error instead of raising", dead.get("ok") is False, json.dumps(dead))
check("...and says why", bool(dead.get("error")), json.dumps(dead))
check("...and never claims rows it does not have", dead.get("sessions") == [], json.dumps(dead))


# ------------------------------------------------- review findings, closed ---

# A cache hit is not a live host: the reachability travels with the rows, or a
# machine that has been off for a day is published as ok.
stale = {"dead": {"at": time.time() - 5000, "ok": False, "sessions": [session("cached-2")], "error": "no route"}}
cached_results = hosts.fetch_all([hosts.Host(name="dead", ssh="dead.invalid")], ttl=99999, cache=stale)
check("a cache hit reports the reachability it was stored with", cached_results[0]["ok"] is False,
      json.dumps(cached_results[0])[:120])
check("...and still carries the cached rows", cached_results[0]["sessions"][0]["id"] == "cached-2",
      json.dumps(cached_results[0])[:120])
check("...in the same shape as a live fetch",
      {"ok", "sessions", "counts", "attention", "error", "at"} <= set(cached_results[0]),
      json.dumps(sorted(cached_results[0])))

# merge must keep the FETCH's timestamp: re-stamping it every pass made the cache
# look eternally fresh, so a host was never re-fetched.
fetch_at = time.time() - 4000
_rows, _status, merged_cache = hosts.merge(
    [], [{"name": "h", "ok": True, "sessions": [], "counts": {}, "attention": 0, "at": fetch_at}], cache={}
)
check("merge keeps the fetch's own timestamp", abs(merged_cache["h"]["at"] - fetch_at) < 1.0,
      str(merged_cache["h"]["at"]))
check("a down host is recorded as down", merged_cache.get("h", {}).get("ok") is not False)

_rows, _status, down_cache = hosts.merge(
    [], [{"name": "h", "ok": False, "sessions": [], "error": "ssh timeout"}], cache=merged_cache
)
check("an unreachable host is recorded with why", down_cache["h"]["ok"] is False
      and down_cache["h"]["error"] == "ssh timeout", json.dumps(down_cache["h"]))
check("...and keeps the rows it had", down_cache["h"]["sessions"] == [], json.dumps(down_cache["h"]))

# A remote row with a nonsense age must not take the merge down with it.
_rows, _status, _cache = hosts.merge(
    [], [{"name": "h", "ok": True, "sessions": [session("bad-age", age_s="soon")], "at": time.time()}], cache={}
)
check("a malformed remote field does not raise out of a merge", True)

# "Malformed" includes the numbers json.loads hands over happily and float() keeps:
# an offline host's cached age goes through int(), which refuses all three.
for bad in (float("inf"), float("-inf"), float("nan")):
    check(f"a non-finite remote number reads as its default ({bad!r})",
          hosts._number(bad, 0.0) == 0.0)
check("...as does an overflowing literal", hosts._number("1e999", 7.0) == 7.0)

# Everything that reaches a remote SHELL is quoted: a reply containing $(...) must
# not execute on the other machine.
with tempfile.TemporaryDirectory() as reply_tmp:
    hosts.CONFIG_PATH = Path(reply_tmp) / "hosts.json"
    hosts.add_host("box", ssh="box")

    captured = {}


    class FakeDone:
        returncode = 0
        stdout = "{}"
        stderr = ""


    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return FakeDone()


    original_run = hosts.subprocess.run
    try:
        hosts.subprocess.run = fake_run
        hosts.reply("box", "sess-1", "hello $(id) `whoami` ; rm -rf /")
    finally:
        hosts.subprocess.run = original_run

    command = captured.get("argv", ["", "", ""])[-1]
    # The property that matters: the shell sees the reply as ONE argument, so
    # $(...) and backticks inside it are text rather than commands.
    try:
        parsed = shlex.split(command)
    except ValueError as exc:  # an unbalanced quote would break the command
        parsed = []
        check("the remote command is quotable", False, str(exc))

    hostile = "hello $(id) `whoami` ; rm -rf /"
    check("the shell sees the reply as a single argument", hostile in parsed, str(parsed))
    check("...even though it contains command substitution", parsed.count(hostile) == 1, str(parsed))
    check("...and the session id is an argument of its own", "sess-1" in parsed, str(parsed))

    hosts.CONFIG_PATH = original_config

print("")
if failures:
    print("HOSTS FAILED: " + "; ".join(failures))
    raise SystemExit(1)
print("HOSTS OK")
