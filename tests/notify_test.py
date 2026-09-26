#!/usr/bin/env python3
"""Notification behaviour: transitions, not states.

Offline and deterministic. The rule that matters most is the first one: a fresh
install must not fire a wall of alerts about sessions that went stale before the
watcher ever ran.

Run: python3 tests/notify_test.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentboard import notify  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(("PASS  " if condition else "FAIL  ") + label + (f" ({detail})" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def session(session_id: str, status: str, title: str = "a session") -> dict:
    return {"id": session_id, "status": status, "title": title, "project": "Projects", "preview": "hello?"}


# ------------------------------------------------- review findings, closed ---

def test_a_failed_send_keeps_its_cooldown():
    """A machine with no notification daemon must retry, not go quiet."""
    state = {"known": {}, "notified": {}, "seeded": True}
    events = [{"id": "s1", "kind": "needs", "title": "t", "project": "", "detail": "", "at": 100.0}]

    original = notify.notify_desktop
    try:
        notify.notify_desktop = lambda event: {"channel": "desktop", "event": event["id"], "sent": False}
        notify.dispatch(events, state=state, now=100.0)
        first = dict(state.get("notified") or {})

        notify.notify_desktop = lambda event: {"channel": "desktop", "event": event["id"], "sent": True}
        results = notify.dispatch(events, state=state, now=101.0)
        second = dict(state.get("notified") or {})
    finally:
        notify.notify_desktop = original

    check("a send that failed does not consume the cooldown", first == {}, str(first))
    check("a send that worked does", any(item.get("sent") for item in results), str(results))
    check("...and records the cooldown", bool(second), str(second))


def test_desktop_summary_is_the_headline():
    """notify-send takes a summary then a body: the player must not read an id."""
    captured = {}

    class Done:
        returncode = 0
        stdout = ""
        stderr = b""

    original_which, original_run = notify.shutil.which, notify.subprocess.run
    try:
        notify.shutil.which = lambda name: "/usr/bin/notify-send"
        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            return Done()

        notify.subprocess.run = fake_run
        notify.notify_desktop(
            {"id": "s1", "kind": "needs", "title": "Build the panel", "project": "Projects", "detail": "wants a decision"}
        )
    finally:
        notify.shutil.which, notify.subprocess.run = original_which, original_run

    argv = captured.get("argv") or []
    check("notify-send is called with the headline as the summary",
          len(argv) >= 5 and argv[-2] == "Build the panel [Projects] needs you", str(argv))
    check("...and the detail as the body", argv[-1] == "wants a decision", str(argv))
    check("...and nothing else", len(argv) == 5, str(argv))


def test_a_capped_burst_is_reported_next_pass():
    """An event dropped by the burst cap must survive to the next pass."""
    state = {"known": {}, "notified": {}, "seeded": True}
    sessions = [
        {"id": f"s{index}", "status": "needs", "title": f"t{index}", "project": "", "age_s": 1}
        for index in range(1, 6)
    ]

    original = notify.notify_desktop
    try:
        notify.notify_desktop = lambda event: {"channel": "desktop", "event": event["id"], "sent": True}
        first = notify.transitions(sessions, state=state, now=100.0)
        second = notify.transitions(sessions, state=state, now=200.0)
    finally:
        notify.notify_desktop = original

    check("a burst is capped in one pass", len(first["events"]) == notify.MAX_PER_PASS, str(len(first["events"])))
    check("the overflow is reported on the next pass", len(second["events"]) > 0, str(len(second["events"])))



def test_the_cap_spends_itself_on_the_loudest():
    """A burst is cut to three, and which three is not a coin flip."""
    state = {"known": {}, "notified": {}, "seeded": True}
    sessions = [
        {"id": "r1", "status": "reply", "title": "quiet", "project": "", "age_s": 5},
        {"id": "e1", "status": "error", "title": "broke", "project": "", "age_s": 5},
        {"id": "r2", "status": "reply", "title": "quiet", "project": "", "age_s": 5},
        {"id": "n1", "status": "needs", "title": "asks", "project": "", "age_s": 5},
        {"id": "n2", "status": "needs", "title": "asks", "project": "", "age_s": 5},
    ]

    original = notify.notify_desktop
    try:
        notify.notify_desktop = lambda event: {"channel": "desktop", "event": event["id"], "sent": True}
        events = notify.detect(sessions, previous={}, seeded=True, now=100.0)
    finally:
        notify.notify_desktop = original

    kinds = [event["kind"] for event in events]
    check("the cap keeps the actionable transitions", kinds == ["needs", "needs", "error"], str(kinds))


def test_the_cooldown_is_the_documented_window():
    """The value matters: every other cooldown test is relative to it."""
    check("the cooldown is ten minutes", notify.COOLDOWN_SECONDS == 600, str(notify.COOLDOWN_SECONDS))


def test_a_delivered_channel_keeps_its_cooldown_when_another_fails():
    """One channel failing must not un-consume the cooldown of the one that worked."""
    state = {"known": {}, "notified": {}, "seeded": True}
    events = [{"id": "s1", "kind": "needs", "title": "t", "project": "", "detail": "", "at": 100.0}]

    original = notify.notify_desktop
    original_hermes = notify.notify_hermes
    try:
        notify.notify_desktop = lambda event: {"channel": "desktop", "event": event["id"], "sent": True}
        notify.notify_hermes = lambda event, platform="", **kwargs: {
            "channel": f"hermes:{platform}", "event": event["id"], "sent": False, "error": "no gateway",
        }
        first = notify.dispatch(events, platforms=("telegram",), state=state, now=100.0)
        second = notify.dispatch(events, platforms=("telegram",), state=state, now=105.0)
    finally:
        notify.notify_desktop = original
        notify.notify_hermes = original_hermes

    check("the delivered channel counted", any(item.get("sent") for item in first), str(first))
    check("...so the same alert is not re-sent seconds later", second == [], str(second))

def test_transition_history_and_completion():
    original = notify.notify_desktop
    notify.notify_desktop = lambda event: {"event": event.get("key", event["id"]), "sent": True}
    try:
        state = {"known": {}, "notified": {}}
        notify.transitions([session("s", "needs")], state=state, now=10)
        notify.transitions([session("s", "working")], state=state, now=20)
        renewed = notify.transitions([session("s", "needs")], state=state, now=30)
        check("quiet transitions allow the next request for attention", len(renewed["results"]) == 1)
        notify.transitions([session("s", "working")], state=state, now=40)
        done = notify.transitions([session("s", "reply")], state=state, now=50)
        check("a working session producing output reports completion", [e["kind"] for e in done["events"]] == ["finished"])
        again = notify.transitions([session("s", "reply")], state=state, now=60)
        check("completed state is not repeatedly announced", not again["events"])
        before = json.dumps(state, sort_keys=True)
        notify.transitions([session("s", "error")], state=state, now=70, dry_run=True)
        check("a transition dry run leaves all caller state unchanged", json.dumps(state, sort_keys=True) == before)
        offline = dict(session("s", "error"), host_offline=True)
        result = notify.transitions([offline], state=state, now=80)
        check("an offline cached row never notifies or replaces its baseline", not result["events"] and state["known"]["s"] == "reply")
        rows = [dict(session("shared", "needs"), host=host) for host in ("local", "other")]
        first = notify.transitions(rows, state=state, now=90)
        check("same id on different hosts has independent notifications", len(first["results"]) == 2)
        check("host-qualified cooldowns are separate", "shared:needs" in state["notified"] and "other/shared:needs" in state["notified"])
        # Three cooling rows must not consume the three available delivery slots.
        cooling = {"known": {}, "notified": {f"s{i}:needs": 90 for i in range(3)}, "seeded": True}
        burst = notify.transitions([session(f"s{i}", "needs") for i in range(4)], state=cooling, now=100)
        check("cooling rows cannot starve an unrelated alert", [e["id"] for e in burst["events"]] == ["s3"])
        finished = notify.detect([session("old", "finished")], previous={}, seeded=True)
        check("an unseen old finished session is not new completion", not finished)
    finally:
        notify.notify_desktop = original


def test_corrupt_state_is_silent():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "state.json"
        path.write_bytes(b"\xff")
        check("invalid UTF-8 ledger seeds silently", notify.load_state(path) == {"known": {}, "notified": {}})
        for raw in ('[]', '{"known": []}', '{"notified": []}', '{"notified": {"s:needs": "bad"}}'):
            path.write_text(raw)
            state = notify.load_state(path)
            outcome = notify.transitions([session("s", "needs")], state=state, dry_run=True)
            check("malformed notification state seeds silently: " + raw, not outcome["events"])


def main() -> int:
    # Never write the player's real ledger from a gate.
    original_state = notify.STATE_PATH
    sandbox = Path(tempfile.mkdtemp())
    notify.STATE_PATH = sandbox / "notify-state.json"

    try:
        return _main()
    finally:
        notify.STATE_PATH = original_state


def _main() -> int:
    test_transition_history_and_completion()
    test_corrupt_state_is_silent()
    test_a_failed_send_keeps_its_cooldown()
    test_the_cap_spends_itself_on_the_loudest()
    test_the_cooldown_is_the_documented_window()
    test_a_delivered_channel_keeps_its_cooldown_when_another_fails()
    test_desktop_summary_is_the_headline()
    test_a_capped_burst_is_reported_next_pass()

    now = 1_789_771_234.0

    with tempfile.TemporaryDirectory() as tmp:
        state_path = Path(tmp) / "notify.json"

        # ---- first run seeds, silently ----
        state = notify.load_state(state_path)
        events = notify.detect([session("a", "needs")], previous={}, seeded=False, now=now)
        check("an unseeded watcher notifies about nothing", events == [], json.dumps(events))

        notify.remember([session("a", "needs"), session("b", "working")], state)
        notify.save_state(state, state_path)
        reloaded = notify.load_state(state_path)
        check("state round-trips", reloaded["known"] == {"a": "needs", "b": "working"}, json.dumps(reloaded["known"]))
        check("state records that it has been seeded", reloaded.get("seeded") is True)

        # ---- a transition notifies once ----
        events = notify.detect([session("a", "needs"), session("b", "needs")], previous=reloaded["known"],
                               seeded=True, now=now)
        check("an entering session is reported", [event["id"] for event in events] == ["b"], json.dumps(events))

        results = notify.dispatch(events, desktop=False, dry_run=True, state=reloaded, now=now)
        check("dry run reports without sending", results[0]["channel"] == "dry-run", json.dumps(results))
        check("dry run does not consume the cooldown", reloaded.get("notified") in ({}, None), json.dumps(reloaded))

        results = notify.dispatch(events, desktop=False, platforms=(), state=reloaded, now=now)
        check("no channels means no sends", results == [], json.dumps(results))

        # ---- same transition again: suppressed by the cooldown ----
        notify.remember([session("a", "needs"), session("b", "needs")], reloaded)
        check("a non-transition is not re-reported",
              notify.detect([session("a", "needs"), session("b", "needs")], previous=reloaded["known"],
                            seeded=True, now=now + 1) == [])

        # ---- a session that flaps back and needs you again, outside the cooldown ----
        notify.remember([session("a", "working"), session("b", "needs")], reloaded)
        later = notify.detect([session("a", "needs"), session("b", "needs")], previous=reloaded["known"],
                              seeded=True, now=now + notify.COOLDOWN_SECONDS + 5)
        check("a re-entering session is reported again after the cooldown", [e["id"] for e in later] == ["a"],
              json.dumps(later))

        # ---- bursts are bounded ----
        many = [session(f"s{index}", "needs") for index in range(10)]
        burst = notify.detect(many, previous={}, seeded=True, now=now)
        check("a burst is capped", len(burst) <= notify.MAX_PER_PASS, str(len(burst)))

        # ---- errors notify too, and the ledger stays bounded ----
        notify.remember([session("e1", "working")], reloaded)
        errored = notify.detect([session("e1", "error")], previous=reloaded["known"], seeded=True, now=now)
        check("an erroring session is reported", [e["kind"] for e in errored] == ["error"], json.dumps(errored))
        check("headline reads like a sentence", "hit an error" in notify._headline(errored[0]),
              notify._headline(errored[0]))

        ledger = {f"s{index}:needs": now + index for index in range(600)}
        notify.prune(ledger)
        check("the notification ledger is pruned", len(ledger) == 500, str(len(ledger)))
        check("pruning drops the oldest first", "s0:needs" not in ledger and "s599:needs" in ledger)

    print("")
    if failures:
        print("NOTIFY TEST FAILED: " + "; ".join(failures))
        return 1
    print("NOTIFY TEST OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
