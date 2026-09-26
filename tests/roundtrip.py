#!/usr/bin/env python3
"""Round-trip the two halves against each other, without the game.

Python publishes a payload; the real addon Payload.lua (under a stub API) parses
it; the real addon Core.lua queues a reply; Python parses that out of a
SavedVariables file the way the client would have written it. Both directions are
asserted, so a format drift on either side fails here instead of in Azeroth.

Run: python3 tests/roundtrip.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentboard import wowclient  # noqa: E402

ADDON = ROOT / "addon" / "AgentBoard"

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(("PASS  " if condition else "FAIL  ") + label + (f" ({detail})" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def parse_payload_with_addon(path: Path) -> subprocess.CompletedProcess:
    """Load the published file and the addon's real parser, print what it saw."""
    script = f'''
      local ns = {{ STATUS_LABELS = {{ needs = "Needs you", working = "Working" }} }}
      local chunk = assert(loadfile("{ADDON}/Payload.lua"))
      chunk("AgentBoard", ns)
      assert(loadfile("{path}"))()
      local parsed = ns:ParsePayload(AgentBoardData)
      if not parsed then print("nil") return end
      if parsed.incompatible then print("incompatible|" .. tostring(parsed.schema)) return end
      print(string.format("%d|%d|%d|%s|%s|%.2f|%s|%s|%s|%s|%s",
        #parsed.sessions, parsed.attention, #parsed.new,
        parsed.sessions[1].title or "", parsed.sessions[1].status or "", parsed.sessions[1].cost or 0,
        parsed.sessions[2].host or "-", parsed.sessions[3].host or "-",
        tostring(parsed.sessions[3].offline), parsed.sessions[1].preview or "-",
        (parsed.hosts and parsed.hosts.terra) or "-"))
    '''
    return subprocess.run(["lua5.1", "-e", script], capture_output=True, text=True)


def main() -> int:
    # Redirect every state file the code under test writes. A test that reached
    # into ~/.local/state/agent-board reset the real dedupe ledger, which is what
    # stops a reply being sent twice: the gates must not be able to do that.
    from agentboard import hosts as hosts_state, notify as notify_state

    with tempfile.TemporaryDirectory() as state_tmp:
        sandbox = Path(state_tmp)
        originals = {
            "wowclient": wowclient._STATE_PATH,
            "notify": notify_state.STATE_PATH,
            "hosts_config": hosts_state.CONFIG_PATH,
            "hosts_cache": hosts_state.CACHE_PATH,
        }
        wowclient._STATE_PATH = sandbox / "wow-inbox.json"
        notify_state.STATE_PATH = sandbox / "notify-state.json"
        hosts_state.CONFIG_PATH = sandbox / "hosts.json"
        hosts_state.CACHE_PATH = sandbox / "hosts-cache.json"
        try:
            return _run()
        finally:
            wowclient._STATE_PATH = originals["wowclient"]
            notify_state.STATE_PATH = originals["notify"]
            hosts_state.CONFIG_PATH = originals["hosts_config"]
            hosts_state.CACHE_PATH = originals["hosts_cache"]


def _control_and_completion_gates() -> None:
    from unittest.mock import patch
    from agentboard import backend

    sid = "20260919_123456_abcdef"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = {"dispatched": [], "acked_seq": 0}
        saved = root / "SavedVariables.lua"
        data = root / "Data.lua"
        data.write_text(wowclient.render_data({"sessions": [
            {"id": sid, "host": "local", "status": "needs", "activity_at": 1789771234.123456}
        ]}))
        script = f'''
          local ns = {{}}
          assert(loadfile("{ADDON}/Locale.lua"))("AgentBoard", ns)
          assert(loadfile("{ADDON}/Payload.lua"))("AgentBoard", ns)
          assert(loadfile("{ADDON}/Core.lua"))("AgentBoard", ns)
          AgentBoardDB = {{}}; AgentBoardOutbox = ""
          assert(loadfile("{data}"))()
          local payload = ns:ParsePayload(AgentBoardData)
          ns:QueueMarkRead(payload.sessions[1])
          ns:QueueStop({{id="{sid}", host="local", status="working"}})
          print('AgentBoardOutbox = "' .. AgentBoardOutbox .. '"')
        '''
        # Use the existing stub harness rather than inventing a second API.
        script = 'dofile("' + str(ROOT / "tests/wow_stub_api.lua") + '")\n' + script
        probe = subprocess.run(["lua5.1", "-e", script], capture_output=True, text=True)
        saved.write_text(probe.stdout)
        controls = wowclient.read_outbox(saved)
        check("Lua queues both new controls into the Python outbox", probe.returncode == 0 and
              [e["kind"] for e in controls] == ["mark_read", "stop"] and
              controls[0]["text"] == "1789771234.123456", probe.stderr + probe.stdout)
        # Direct dispatch fixtures also keep Python regressions legible if Lua fails.
        mark = {"seq": "1", "kind": "mark_read", "host": "local", "session_id": sid, "text": "1234.125"}
        stop = {**mark, "seq": "2", "kind": "stop", "text": ""}
        with patch.object(backend, "submit_reply", side_effect=AssertionError("control became reply")), \
             patch.object(backend, "stop_turn", return_value={"status": "interrupted"}) as interrupt:
            outcomes = wowclient.dispatch([mark, stop], state=state)
            check("controls route to suppression and interrupt, never prompt submit",
                  [x["outcome"] for x in outcomes] == ["marked_read", "stopped"] and interrupt.call_count == 1)
            wowclient.dispatch([mark, stop], state=state)
            check("a settled stop is not repeated", interrupt.call_count == 1)
        rows = [{"id": sid, "host": "local", "activity_at": 1234.125, "status": "needs"},
                {"id": sid, "host": "remote", "activity_at": 1234.125, "status": "needs"},
                {"id": sid, "host": "local", "activity_at": 1235, "status": "needs"}]
        suppressed = wowclient.apply_read_suppressions(rows, state)
        check("mark read suppresses only the exact host/session/activity", [r["status"] for r in suppressed] == ["idle", "needs", "needs"])
        check("mark read leaves source roster unchanged", rows[0]["status"] == "needs")
        published = wowclient.publish(root, {"sessions": rows}, hosts_enabled=False)
        check("publishing applies persisted mark read suppression", [r["status"] for r in published["rows"]] == ["idle", "needs", "needs"] and published["attention"] == 2)
        with patch.object(backend, "stop_turn", side_effect=RuntimeError("lost response")) as interrupt:
            failed_state = {"dispatched": []}
            failure = wowclient.dispatch([stop], state=failed_state)
            wowclient.dispatch([stop], state=failed_state)
            check("uncertain stop is acknowledged once and surfaced as an error", interrupt.call_count == 1 and
                  failed_state.get("acked_seq") == 2 and "lost response" in failed_state.get("control_error", "") and
                  failure[0]["outcome"].startswith("stop_failed_or_uncertain"))
        notice = wowclient.publish(root, {"sessions": []}, hosts_enabled=False)
        check("control failure is a notice, not a broken roster", not notice["error"] and "lost response" in notice["notice"] and
              "notice=stop_failed_or_uncertain" in Path(notice["path"]).read_text())
        with patch.object(backend, "call", side_effect=[{"sessions": [{"session_key": sid, "id": "runtime", "status": "working"}]}, {"status": "interrupted"}]) as rpc:
            backend.stop_turn(sid, backend={"url": "test"})
            check("stop resolves live id then uses verified interrupt method", [c.args[0] for c in rpc.call_args_list] == ["session.active_list", "session.interrupt"] and
                  rpc.call_args_list[1].args[1] == {"session_id": "runtime"})
        for snapshot in ({"sessions": []}, {"sessions": [{"session_key": sid, "id": "runtime", "status": "idle"}]}):
            with patch.object(backend, "call", return_value=snapshot) as rpc:
                try:
                    backend.stop_turn(sid, backend={"url": "test"})
                except RuntimeError:
                    rejected = True
                else:
                    rejected = False
                check("stop refuses missing or idle sessions without resuming", rejected and rpc.call_count == 1)

        with patch.object(backend, "find_backend", return_value=None), patch.object(backend, "call") as rpc:
            try:
                backend.stop_turn(sid)
            except RuntimeError as exc:
                absent = "no running Agent backend" in str(exc)
            else:
                absent = False
            check("missing backend gives an explicit stop failure", absent and rpc.call_count == 0)
        from agentboard import hosts
        with patch.object(hosts, "reply") as remote_reply, patch.object(backend, "stop_turn") as interrupt:
            remote_state = {"dispatched": []}
            outcome = wowclient.dispatch([{**stop, "host": "remote"}], state=remote_state)
            check("remote stop never falls through to remote reply", not remote_reply.called and not interrupt.called and
                  "only for a local" in outcome[0]["outcome"])

        with patch.object(backend, "submit_reply") as submit, \
             patch.object(backend, "stop_turn", return_value={"status": "interrupted"}) as interrupt:
            for corruption in (b"\xff", "broken json", '{"dispatched": {}}', '{"acked_seq": []}',
                               '{"pending": {"x": []}}', '{"read_marks": []}'):
                wowclient._STATE_PATH.write_bytes(corruption if isinstance(corruption, bytes) else corruption.encode())
                wowclient.publish(root, {"sessions": []}, hosts_enabled=False)
                wowclient.acked_seq()
                wowclient.dispatch([mark, stop])
                wowclient.dispatch([mark, stop])
                check("publish cannot consume ledger corruption quarantine", submit.call_count == 0 and interrupt.call_count == 0 and
                      wowclient._load_state().get("acked_seq") == 2 and
                      "ledger was unreadable" in wowclient._load_state().get("control_error", ""))
        wowclient._save_state({"dispatched": [], "acked_seq": 0})

        from agentboard import notify
        with patch.object(wowclient, "board", return_value={"sessions": []}), \
             patch.object(notify, "transitions", side_effect=OSError("ledger disk full")), \
             patch.object(wowclient, "savedvars_path", return_value=saved), \
             patch.object(wowclient, "dispatch", return_value=[]) as deliver:
            report = wowclient.watch(addon_dir=root, once=True, hosts_enabled=False)
            check("notification ledger failure does not stop inbox dispatch", report[0].get("notify_error") == "ledger disk full" and deliver.call_count == 1)

        # A crash after the durable stop journal but before the ack must not
        # leave the control pending forever merely because it is already seen.
        wowclient._save_state({"dispatched": [wowclient._entry_key(e) for e in controls], "acked_seq": 0})
        with patch.object(wowclient, "board", return_value={"sessions": []}), \
             patch.object(wowclient, "savedvars_path", return_value=saved), \
             patch.object(backend, "stop_turn") as interrupt:
            wowclient.watch(addon_dir=root, once=True, hosts_enabled=False, notify_enabled=False)
        check("watcher acknowledges previously journaled controls without repeating them", interrupt.call_count == 0 and
              wowclient._load_state()["acked_seq"] == max(int(e["seq"]) for e in controls))

        # Exercise the detached supervisor with a real delayed process. No Agent
        # process is started; the shell fixture only exits after the grace window.
        executable = root / "fake-hermes"
        executable.write_text("#!/bin/sh\nsleep 0.15\nexit 7\n")
        executable.chmod(0o755)
        with patch.object(backend, "_hermes_bin", return_value=str(executable)):
            verdict = backend.submit_reply_cli(sid, "hello", log_dir=root, grace=0)
        with patch.object(backend, "_hermes_bin", return_value=str(executable)), \
             patch.object(backend, "cli_exit", return_value=None):
            no_record = backend.submit_reply_cli(sid, "hello", log_dir=root, grace=0.3)
        check("supervisor exit alone cannot prove CLI delivery", no_record["ok"] is None and no_record["exit"] is None)
        deadline = time.monotonic() + 5
        while backend.cli_exit(verdict["completion_path"]) is None and time.monotonic() < deadline:
            time.sleep(0.02)
        entry = {**mark, "kind": "reply", "text": "hello"}
        key = wowclient._entry_key(entry)
        item = {**entry, "pid": verdict["pid"], "exit": None, "log": str(verdict["log_path"]), "completion": str(verdict["completion_path"])}
        pending = {"pending": {key: item}}
        delivered, refused = wowclient._reconcile_pending(pending)
        check("late nonzero CLI exit is never acknowledged as delivered", backend.cli_exit(verdict["completion_path"]) == 7 and not delivered and len(refused) == 1)
        Path(verdict["completion_path"]).write_text('{"exit": 0}')
        delivered, refused = wowclient._reconcile_pending({"pending": {key: item}})
        check("recorded zero CLI exit settles successfully", delivered == [key] and refused == [])
        Path(verdict["completion_path"]).unlink()
        uncertain = {"pending": {key: item}}
        with patch.object(wowclient, "_pid_running", return_value=False):
            delivered, refused = wowclient._reconcile_pending(uncertain)
        check("missing completion remains uncertain and blocks duplicate launch", not delivered and key in uncertain["pending"] and refused[0]["outcome"].startswith("uncertain"))


def _run() -> int:
    _control_and_completion_gates()
    payload = {
        "generated_at": 1789771234,
        "attention": 2,
        "counts": {"needs": 2, "error": 0, "working": 1, "reply": 1, "idle": 0, "finished": 1},
        "sessions": [
            {
                "id": "20260918_182123_e733d6",
                # Every separator the wire format uses, plus quotes and a
                # backslash: a title must never be able to forge a field or a row.
                "title": 'Quote " and \\ backslash | pipe ;; rowbreak',
                "project": "Projects",
                "profile": "default",
                "status": "needs",
                "status_label": "Needs you",
                "age_s": 30,
                "activity": "",
                "messages": 12,
                "cost_usd": 0.42,
                "activity_at": 1789771200,
                "preview": "Should I ship the panel?",
            },
            {
                "id": "20260918_181445_cd30b7",
                "title": "Verify the booking sync",
                "project": "MachineMind",
                "profile": "default",
                "host": "terra",
                "status": "working",
                "status_label": "Working",
                "age_s": 260,
                "activity": "receiving stream response",
                "messages": 90,
                "cost_usd": 1.2,
                "activity_at": 1789771000,
            },
            {
                "id": "20260918_171002_aa11bb",
                "title": "Nightly sweep on the other box",
                "project": "cssi",
                "profile": "default",
                "host": "foundry",
                "host_offline": True,
                "status": "reply",
                "status_label": "New reply",
                "age_s": 5400,
                "activity": "second pass",
                "messages": 7,
                "cost_usd": 0.05,
                "activity_at": 1789770000,
                "preview": "queued the sweep",
            },
        ],
        "hosts": {"local": "ok", "terra": "ok", "foundry": "offline"},
    }

    with tempfile.TemporaryDirectory() as tmp:
        addon_dir = Path(tmp) / "AddOns" / "AgentBoard"
        addon_dir.mkdir(parents=True)
        for item in sorted(ADDON.iterdir()):
            if item.is_file():
                (addon_dir / item.name).write_bytes(item.read_bytes())

        # ---- Python -> Lua: does the real addon read what we publish? ----
        # hosts are exercised separately below: this call is the pure render path
        result = wowclient.publish(addon_dir, payload, hosts_enabled=False)
        check("publish wrote Data.lua", Path(result["path"]).is_file(), result["path"])
        check("publish reports sessions", result["sessions"] == 3, str(result["sessions"]))

        probe = parse_payload_with_addon(Path(result["path"]))
        check("addon parsed the published payload", probe.returncode == 0, probe.stderr.strip())
        fields = probe.stdout.strip().split("|")
        check("row count survives", fields[0] == "3", probe.stdout.strip())
        check("attention recomputed from the rows", fields[1] == "1", probe.stdout.strip())
        check("new list parsed", fields[2] == "0", probe.stdout.strip())
        check("status survives", fields[4] == "needs", probe.stdout.strip())
        check("cost survives", fields[5] == "0.42", probe.stdout.strip())
        check("host travels with the row", fields[6] == "terra", probe.stdout.strip())
        check("a local row says local", "local" in (fields[6], fields[7]) or fields[7] == "foundry",
              probe.stdout.strip())
        check("an offline host is flagged on the row", fields[8] == "true", probe.stdout.strip())
        check("preview text survives", "ship the panel" in fields[9], probe.stdout.strip())
        check("the header host map is parsed", fields[10] == "ok", probe.stdout.strip())
        check(
            "separators stripped from a hostile title",
            "|" not in fields[3] and ";;" not in fields[3] and "Quote" in fields[3],
            fields[3],
        )

        # The header carries the schema and the new-since-last-sync list.
        header = Path(result["path"]).read_text(encoding="utf-8").splitlines()[-1]
        check("payload declares the schema", "schema=5" in header, header[:80])
        check("payload carries a host map", "hosts=" in header, header[:80])
        check("payload carries the dispatch high-water mark", "acked=" in header, header[:80])
        check("payload names the bridge version", f"bridge={wowclient.BRIDGE_VERSION}" in header, header[:80])
        check("payload starts with the tag", "AgentBoardData = \"HE1|" in header, header[:60])

        # ---- a non-finite number must not take the render down ----------------
        # json.loads accepts Infinity, NaN and an overflowing literal without
        # complaint, so a remote host can put one in a row; float() keeps it, and
        # the int() on the way out refuses it. That raised out of the render, so
        # the publish never happened and the board silently stopped updating.
        for literal in ("Infinity", "-Infinity", "NaN", "1e999"):
            bad = json.loads('{"v": ' + literal + "}")["v"]
            row = dict(payload["sessions"][0], id="non-finite", age_s=bad, messages=bad)
            try:
                rendered = wowclient.render_data({"generated_at": 1789771234, "sessions": [row]})
                ok = "HE1|" in rendered and "non-finite" in rendered
            except Exception:  # noqa: BLE001
                ok = False
            check(f"a row with a non-finite number still renders ({literal})", ok)

        # A payload from a future bridge is refused, not half-read.
        future = addon_dir / "Future.lua"
        future.write_text('AgentBoardData = "HE1|bridge=9.9|schema=99|generated=1|rows=0|new="\n', encoding="utf-8")
        probe = parse_payload_with_addon(future)
        check("a future schema is refused", probe.stdout.strip() == "incompatible|99", probe.stdout.strip())

        # Garbage, and a payload claiming more rows than it carries (a torn
        # write), must read as "no data", never as an exception and never as a
        # half-populated board.
        for name, body in (("garbage", "AgentBoardData = \"not a payload\"\n"),
                           ("short", 'AgentBoardData = "HE1|bridge=0.4|schema=5|generated=1|rows=2|new=;;2026|needs|1|local|default|p|orphan row||1|"')):
            path = addon_dir / f"{name}.lua"
            path.write_text(body, encoding="utf-8")
            probe = parse_payload_with_addon(path)
            check(f"{name} payload reads as unusable", probe.stdout.strip() == "nil", probe.stdout.strip())

        # ---- Lua -> Python: does the addon's outbox parse back? ----
        reply_script = f'''
          dofile("{ROOT}/tests/wow_stub_api.lua")
          local ns = {{}}
          -- Locale first: Core reads ns.L at load time, like the client does.
          local locale = assert(loadfile("{ADDON}/Locale.lua"))
          locale("AgentBoard", ns)
          local chunk = assert(loadfile("{ADDON}/Core.lua"))
          chunk("AgentBoard", ns)
          AgentBoardDB = {{ seq = 0 }}
          AgentBoardOutbox = ""
          ns:QueueReply("20260918_182123_e733d6", "run the tests|again")
          ns:QueueReply("20260918_181445_cd30b7", "and then ship;it", "terra")
          ns:QueueFocus({{ id = "20260918_171002_aa11bb", host = "foundry" }})
          local handle = assert(io.open("{tmp}/savedvars.lua", "w"))
          handle:write("AgentBoardSync = 1789771200\\n")
          handle:write("AgentBoardOutbox = \\"" .. AgentBoardOutbox .. "\\"\\n")
          handle:close()
          print(AgentBoardOutbox)
        '''
        lua = subprocess.run(["lua5.1", "-e", reply_script], capture_output=True, text=True)
        check("addon queued replies under the stub api", lua.returncode == 0, lua.stderr.strip())

        savedvars = Path(tmp) / "savedvars.lua"
        entries = wowclient.read_outbox(savedvars)
        check("python parsed three entries", len(entries) == 3, json.dumps(entries))
        if len(entries) == 3:
            check(
                "first entry intact",
                entries[0]["session_id"] == "20260918_182123_e733d6" and entries[0]["text"] == "run the tests/again",
                json.dumps(entries[0]),
            )
            check(
                "second entry intact",
                entries[1]["session_id"] == "20260918_181445_cd30b7" and entries[1]["text"] == "and then ship/it",
                json.dumps(entries[1]),
            )
            check("a local reply says local, not blank", entries[0]["host"] == "local", json.dumps(entries[0]))
            check("a remote reply keeps its host", entries[1]["host"] == "terra", json.dumps(entries[1]))


        # The sync stamp is how the bridge knows what is new to the player.
        check("sync stamp is readable", wowclient.read_sync_stamp(savedvars) == 1789771200.0,
              str(wowclient.read_sync_stamp(savedvars)))

        # An addon older than the kind field still reads: four fields lose only
        # the kind, three lose the host too. Both are replies.
        legacy = Path(tmp) / "savedvars.legacy.lua"
        legacy.write_text(
            'AgentBoardOutbox = "4|local|20260918_182123_e733d6|four field reply'
            ';;5|20260918_181445_cd30b7|three field reply"\n',
            encoding="utf-8",
        )
        old = wowclient.read_outbox(legacy)
        check("a four-field entry reads as a reply", len(old) == 2 and old[0]["kind"] == "reply"
              and old[0]["host"] == "local" and old[0]["text"] == "four field reply", json.dumps(old))
        check("a three-field entry reads as a local reply",
              len(old) == 2 and old[1]["host"] == "local" and old[1]["text"] == "three field reply",
              json.dumps(old))

        # A half-written file (client killed mid-flush) must read as empty, not raise.
        broken = Path(tmp) / "savedvars.broken.lua"
        broken.write_text('AgentBoardOutbox = "1|abc|trunca', encoding="utf-8")
        check("truncated outbox reads as empty", wowclient.read_outbox(broken) == [])

        # ---- routing: host-aware replies and control messages ----
        from agentboard import backend, hosts as host_module, wowclient as client

        routed: list[tuple] = []
        original_reply = host_module.reply
        original_submit = backend.submit_reply
        original_submit_cli = backend.submit_reply_cli
        original_clipboard = wowclient._clipboard
        try:
            host_module.reply = lambda host, session_id, text: routed.append(("remote", host, session_id, text)) or {"ok": True}
            backend.submit_reply = lambda session_id, text: routed.append(("local", "-", session_id, text))
            backend.submit_reply_cli = lambda session_id, text: routed.append(("cli", "-", session_id, text))
            wowclient._clipboard = lambda text: "stub"

            results = wowclient.dispatch(entries, state={"dispatched": []})
            kinds = {entry["session_id"]: entry["outcome"] for entry in results}
            check("the local reply went to the local session",
                  any(kind == "local" for kind, *_ in routed), json.dumps(routed))
            check("the remote reply was routed to its host",
                  any(kind == "remote" and entry[0] == "terra" for kind, *entry in routed), json.dumps(routed))
            check("the hand-off was answered as a hand-off",
                  kinds.get("20260918_171002_aa11bb") == "handed_off", json.dumps(kinds))
            check("the hand-off sent no words into the session",
                  not any(entry[2] == "20260918_171002_aa11bb" and kind != "handoff" for kind, *entry in routed),
                  json.dumps(routed))

            repeat = wowclient.dispatch(entries, state={"dispatched": [wowclient._entry_key(e) for e in entries]})
            check("a dispatched entry is not sent twice", repeat == [], json.dumps(repeat))
        finally:
            host_module.reply = original_reply
            backend.submit_reply = original_submit
            backend.submit_reply_cli = original_submit_cli
            wowclient._clipboard = original_clipboard

        # ---- a lost dispatch log must not replay the outbox ----
        state_path = Path(tmp) / "state.json"
        original_state = wowclient._STATE_PATH
        try:
            wowclient._STATE_PATH = state_path
            state_path.write_text("{ this is not json", encoding="utf-8")

            replayed: list[tuple] = []
            original_submit2 = backend.submit_reply
            backend.submit_reply = lambda session_id, text: replayed.append((session_id, text))
            try:
                guarded = wowclient.dispatch(entries)
            finally:
                backend.submit_reply = original_submit2

            check("an unreadable dispatch log sends nothing",
                  not replayed, json.dumps(replayed))
            check("...and says so for every entry",
                  all("unreadable" in item["outcome"] for item in guarded), json.dumps(guarded))
            check("...after keeping a copy of the damaged file",
                  state_path.with_suffix(".corrupt.json").is_file(), str(state_path))

            # A healthy log records the high-water mark the payload will carry.
            wowclient._save_state({"dispatched": [wowclient._entry_key(entries[0])], "acked_seq": 0})
            wowclient.dispatch([entries[0]], state=None)
            check("a settled entry raises the acked mark",
                  wowclient._load_state()["acked_seq"] == int(entries[0]["seq"] or 0),
                  json.dumps(wowclient._load_state()))
            # ...and that mark is what the next publish writes into Data.lua, so
            # the addon can drop what the bridge has already dealt with.
            mark = wowclient.acked_seq()
            check("the dispatch mark is the entry's seq", mark == int(entries[0]["seq"] or 0), str(mark))
            republished = wowclient.publish(addon_dir, payload, hosts_enabled=False)
            header_now = Path(republished["path"]).read_text(encoding="utf-8").splitlines()[-1]
            check("the next publish carries that mark", f"acked={mark}" in header_now, header_now[:140])
        finally:
            wowclient._STATE_PATH = original_state


        # ---- the ack mark may only cover the CONTIGUOUS settled prefix ----
        # A failed entry below a later success used to be acknowledged, which let
        # the addon delete a reply that was never delivered.
        original_state_path = wowclient._STATE_PATH
        wowclient._STATE_PATH = Path(tmp) / "ack-state.json"
        original_submit = backend.submit_reply
        original_submit_cli = backend.submit_reply_cli

        def dead_letter(session_id, text):
            if session_id == "20260918_182123_e733d6":
                raise RuntimeError("no channel will take this one")

        two = [
            {"seq": "10", "kind": "reply", "host": "local", "session_id": "20260918_182123_e733d6", "text": "first"},
            {"seq": "11", "kind": "reply", "host": "local", "session_id": "20260918_181445_cd30b7", "text": "second"},
        ]
        try:
            backend.submit_reply = dead_letter
            backend.submit_reply_cli = dead_letter
            wowclient._save_state({"dispatched": [], "acked_seq": 3})
            outcomes = wowclient.dispatch(two, state=None)
            after = wowclient._load_state().get("acked_seq", 0)
        finally:
            backend.submit_reply = original_submit
            backend.submit_reply_cli = original_submit_cli
            wowclient._STATE_PATH = original_state_path

        check("a failure below a success is not acknowledged", after < 11, str(after))
        check("...the settle below it is not lost either", after >= 3, str(after))
        check("...and the failed entry is reported as failed",
              any("no channel" in item.get("outcome", "") for item in outcomes), json.dumps(outcomes))
        check("...while the other one still went out",
              any(item.get("outcome") == "sent" for item in outcomes), json.dumps(outcomes))

        # ---- a torn Lua literal must not raise out of the outbox parser ----
        for hostile in (
            r"1|reply|local|a|\2a",
            r"1|reply|local|a|\12z",
            r"1|reply|local|a|\z",
            # A superscript digit is `isdigit()` but is not `\d`, which is how a
            # non-decimal "digit" after a backslash reached int() and raised out of
            # the watcher loop. The vulgar fraction is the other side of that line:
            # it is not a digit by the same definition, so it never entered the
            # branch, and it is here as the case that must stay quiet.
            "1|reply|local|a|\\\u00b2",
            "1|reply|local|a|\\\u00bd",
        ):
            try:
                wowclient._unescape_lua_literal(hostile)
                ok = True
            except Exception:  # noqa: BLE001
                ok = False
            check(f"an odd escape does not raise ({hostile[-4:]})", ok)

        # ---- the SavedVariables lookup takes either documented directory ----
        addon_leaf = addon_dir
        check("savedvars_path accepts the AddOns directory and the addon directory",
              wowclient.savedvars_path(addon_leaf) == wowclient.savedvars_path(addon_leaf.parent),
              f"{wowclient.savedvars_path(addon_leaf)} vs {wowclient.savedvars_path(addon_leaf.parent)}")


        # ---- a realistic client layout, so the SavedVariables lookup is real ---
        # The addon folder and WTF are siblings under the version directory; the
        # fixture used to be one level too shallow, which hid a bug that made every
        # in-game reply go nowhere.
        version = Path(tmp) / "client" / "_beta_"
        leaf = version / "Interface" / "AddOns" / "AgentBoard"
        leaf.mkdir(parents=True, exist_ok=True)
        for item in sorted(ADDON.iterdir()):
            if item.is_file():
                (leaf / item.name).write_bytes(item.read_bytes())
        saved = version / "WTF" / "Account" / "ACCT#1" / "SavedVariables"
        saved.mkdir(parents=True, exist_ok=True)
        stamp = int(time.time()) - 300
        (saved / "AgentBoard.lua").write_text(
            f"AgentBoardSync = {stamp}\nAgentBoardOutbox = \"\"\n", encoding="utf-8"
        )

        check("the SavedVariables file is found from the addon folder",
              wowclient.savedvars_path(leaf) == saved / "AgentBoard.lua",
              str(wowclient.savedvars_path(leaf)))
        check("...and from the AddOns folder above it",
              wowclient.savedvars_path(leaf.parent) == saved / "AgentBoard.lua",
              str(wowclient.savedvars_path(leaf.parent)))
        check("the sync stamp is read", wowclient.read_sync_stamp(saved / "AgentBoard.lua") == float(stamp),
              str(wowclient.read_sync_stamp(saved / "AgentBoard.lua")))

        fresh_payload = {
            "generated_at": time.time(),
            "counts": {"needs": 1, "error": 1},
            "sessions": [
                dict(payload["sessions"][0], id="asked-while-away", status="needs",
                     activity_at=time.time() - 10),
                dict(payload["sessions"][1], id="errored-while-away", status="error",
                     activity_at=time.time() - 20),
            ],
        }
        published = wowclient.publish(leaf, fresh_payload, hosts_enabled=False)
        header = Path(published["path"]).read_text(encoding="utf-8").splitlines()[-1]
        check("a session that started needing you since the last sync is published as new",
              "new=asked-while-away;errored-while-away" in header, header[-120:])
        check("...and an error counts as new too",
              wowclient.new_since(fresh_payload, stamp) == ["asked-while-away", "errored-while-away"],
              json.dumps(wowclient.new_since(fresh_payload, stamp)))

        # ---- the remote-board wiring inside publish --------------------------
        with tempfile.TemporaryDirectory() as host_tmp:
            from agentboard import hosts as host_module

            original_config = host_module.CONFIG_PATH
            original_fetch = host_module.fetch_all
            try:
                host_module.CONFIG_PATH = Path(host_tmp) / "hosts.json"
                host_module.add_host("terra", ssh="terra")
                host_module.add_host("foundry", ssh="foundry")

                host_module.fetch_all = lambda configured, **kwargs: [
                    {"name": "terra", "ok": True, "sessions": [dict(payload["sessions"][1], id="remote-1",
                                                                    host="terra")],
                     "counts": {}, "attention": 0, "at": time.time()},
                    {"name": "foundry", "ok": False, "sessions": [], "error": "unreachable", "at": time.time()},
                ]
                merged_publish = wowclient.publish(leaf, fresh_payload, host_ttl=1e9)
            finally:
                host_module.CONFIG_PATH = original_config
                host_module.fetch_all = original_fetch

        merged_header = Path(merged_publish["path"]).read_text(encoding="utf-8").splitlines()[-1]
        check("a dead host is named as offline in the payload", "foundry:offline" in merged_header,
              merged_header[:200])
        check("...and the live one as ok", "terra:ok" in merged_header, merged_header[:200])
        check("the remote row is published with its host", "remote-1" in merged_header and "|terra|" in merged_header,
              merged_header[:400])
        check("publish reports the rows it published", len(merged_publish.get("rows") or []) >= 3,
              str(len(merged_publish.get("rows") or [])))

        # ---- a reply that says "!focus" is a reply ---------------------------
        focus_words = [{"seq": "40", "kind": "reply", "host": "local",
                        "session_id": "20260918_182123_e733d6", "text": "!focus"}]
        captured: list[tuple] = []
        backend.submit_reply = lambda session_id, text: captured.append((session_id, text))
        backend.submit_reply_cli = lambda session_id, text, **kwargs: {"ok": True, "pid": 0, "log_path": "", "exit": 0}
        try:
            outcome = wowclient.dispatch(focus_words, state={"dispatched": [], "acked_seq": 0})
        finally:
            backend.submit_reply = original_submit
            backend.submit_reply_cli = original_submit_cli
        check("a reply whose text is !focus is still a reply",
              outcome and outcome[0]["outcome"] == "sent" and captured == [("20260918_182123_e733d6", "!focus")],
              json.dumps(outcome) + " " + json.dumps(captured))

        legacy_focus = [{"seq": "41", "kind": "focus", "host": "local", "legacy": True,
                         "session_id": "20260918_182123_e733d6", "text": "!focus"}]
        handed: list[tuple] = []
        original_hand = wowclient.hand_off
        wowclient.hand_off = lambda entry, **kwargs: handed.append(entry["session_id"]) or {"ok": True, "clipboard": "stub"}
        try:
            legacy_outcome = wowclient.dispatch(legacy_focus, state={"dispatched": [], "acked_seq": 0})
        finally:
            wowclient.hand_off = original_hand
        check("...and an older addon's !focus entry still hands off",
              handed == ["20260918_182123_e733d6"] and legacy_outcome[0]["outcome"].startswith("handed_off"),
              json.dumps(legacy_outcome))

        # ---- the CLI fallback is not called delivered until it finishes -------
        log = Path(tmp) / "reply.log"
        log.write_text("", encoding="utf-8")
        pending_entry = [{"seq": "50", "kind": "reply", "host": "local",
                          "session_id": "20260918_182123_e733d6", "text": "long turn"}]
        pstate = {"dispatched": [], "acked_seq": 0}
        original_cli = backend.submit_reply_cli
        original_submit3 = backend.submit_reply
        try:
            backend.submit_reply = lambda session_id, text: (_ for _ in ()).throw(RuntimeError("no rpc"))
            backend.submit_reply_cli = lambda session_id, text, **kwargs: {
                "ok": None, "pid": os.getpid(), "log_path": log, "exit": None,
            }
            first = wowclient.dispatch(pending_entry, state=pstate)
            check("a fallback still running is reported as pending",
                  first and first[0]["outcome"] == "sent_via_cli_pending", json.dumps(first))
            check("...and is not acknowledged", pstate.get("acked_seq") in (0, None), str(pstate.get("acked_seq")))
            check("...and is remembered for the next round",
                  bool(pstate.get("pending")), json.dumps(list((pstate.get("pending") or {}).keys())))

            # A round that runs while that turn is still running must not launch it
            # again. A pending entry is deliberately not in `dispatched` (it has not
            # been delivered), so nothing else stands between the watcher and one
            # more CLI turn for the same reply, every round, in one live session.
            launches: list[tuple] = []
            original_cli_again = backend.submit_reply_cli
            backend.submit_reply_cli = lambda session_id, text, **kwargs: (
                launches.append((session_id, text))
                or {"ok": None, "pid": os.getpid(), "log_path": log, "exit": None}
            )
            try:
                again = wowclient.dispatch(pending_entry, state=pstate)
            finally:
                backend.submit_reply_cli = original_cli_again
            check("a fallback that is still running is not launched a second time",
                  launches == [], json.dumps(launches))
            check("...and the entry it came from is not re-sent either",
                  again == [], json.dumps(again))
            check("...while the one turn that is running stays pending",
                  len(pstate.get("pending") or {}) == 1,
                  json.dumps(list((pstate.get("pending") or {}).keys())))

            # The child exits having refused it: the next round must say so, and
            # must not acknowledge a reply that never arrived.
            log.write_text("hermes-refusal-reason: SESSION_NOT_OWNED\n", encoding="utf-8")
            original_pid_running = wowclient._pid_running
            wowclient._pid_running = lambda pid: False
            try:
                second = wowclient.dispatch(pending_entry, state=pstate)
            finally:
                wowclient._pid_running = original_pid_running
            check("a refused fallback is reported as failed",
                  any("refused" in item["outcome"] for item in second), json.dumps(second))
            check("...and it is not acknowledged either", pstate.get("acked_seq") in (0, None),
                  str(pstate.get("acked_seq")))
        finally:
            backend.submit_reply = original_submit3
            backend.submit_reply_cli = original_cli

        # ---- a seq that is not a number is dropped, not raised ----------------
        junk_seq = Path(tmp) / "savedvars.junkseq.lua"
        junk_seq.write_text(
            'AgentBoardOutbox = "1e+15|reply|local|20260918_182123_e733d6|hello"', encoding="utf-8"
        )
        check("a non-numeric seq is dropped rather than raising", wowclient.read_outbox(junk_seq) == [],
              json.dumps(wowclient.read_outbox(junk_seq)))

        # ---- new-since-last-sync bookkeeping ----
        fresh = wowclient.new_since(payload, 1789771150)
        check("an attention item newer than the sync counts as new", fresh == ["20260918_182123_e733d6"], json.dumps(fresh))
        check("nothing is new without a sync stamp", wowclient.new_since(payload, None) == [])

    print("")
    if failures:
        print("ROUNDTRIP FAILED: " + "; ".join(failures))
        return 1
    print("ROUNDTRIP OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
