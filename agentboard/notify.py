"""Out-of-game notification: the live half of freshness.

In game the client only re-reads addon files when the UI loads, so nothing the
bridge does can appear in game until the player syncs. What the bridge *can* do
is notice a transition the moment it happens and tell the player through a
channel that is not the game: a desktop notification, or a message on whatever
platform their Agent gateway is paired to.

Only transitions notify, never states. A fresh install seeds its state silently
so it cannot fire a wall of alerts about sessions that went stale last week, and
each (session, kind) pair has a cooldown so a flapping session cannot spam.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable

from . import state as state_module

STATE_PATH = state_module.STATE_DIR / "notify-state.json"

KIND_LABELS = {
    "needs": "needs you",
    "error": "hit an error",
    "finished": "finished",
}

# Which transition is worth interrupting for when a pass finds more of them than
# the cap allows. `needs` is the actionable one; an error is worth seeing too. The
# cap used to take an arbitrary three, because every event in a pass carries the
# same timestamp and the sort key was that timestamp.
KIND_RANK = {"needs": 0, "error": 1, "finished": 2}

# One notification per (session, kind) per window. Long enough that a session
# oscillating between working and needs-you cannot machine-gun the user.
COOLDOWN_SECONDS = 600
MAX_PER_PASS = 3


def load_state(path: Path | None = None) -> dict:
    path = path or STATE_PATH
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("known", {}), dict) or not isinstance(value.get("notified", {}), dict):
            return {"known": {}, "notified": {}}
        value["notified"] = {key: stamp for key, stamp in value.get("notified", {}).items()
                             if isinstance(stamp, (int, float)) and 0 <= stamp < float("inf")}
        return value
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"known": {}, "notified": {}}


def save_state(state: dict, path: Path | None = None) -> None:
    """Atomic: a torn ledger loses the cooldowns, which means a burst of
    duplicate notifications rather than a missed one."""
    target = path or STATE_PATH
    text = json.dumps(state, indent=2, sort_keys=True)
    # Most passes change nothing; skipping those saves a durable write per round.
    try:
        if target.read_text(encoding="utf-8") == text:
            return
    except (OSError, UnicodeError):
        pass
    state_module.atomic_write(target, text)


def session_key(session: dict) -> str:
    host = str(session.get("host") or "local")
    sid = str(session.get("id") or "")
    return sid if host == "local" else f"{host}/{sid}"


def event_kind(session: dict, previous: dict[str, str]) -> str | None:
    if session.get("host_offline") or session.get("dismissed"):
        return None
    status = str(session.get("status", ""))
    before = previous.get(session_key(session))
    if before == status:
        return None
    if status in ("needs", "error"):
        return status
    if before in ("working", "waiting") and status in ("reply", "idle", "finished"):
        return "finished"
    return None


def detect(
    sessions: Iterable[dict],
    *,
    previous: dict[str, str],
    seeded: bool,
    now: float | None = None,
    limit: int | None = MAX_PER_PASS,
) -> list[dict]:
    """Transitions worth telling someone about.

    `previous` maps session id to the status last seen; `seeded` says whether the
    watcher has ever run (False means this pass only records, never notifies).
    """
    stamp = now if now is not None else time.time()
    events: list[dict] = []

    for session in sessions:
        status = str(session.get("status", ""))
        session_id = str(session.get("id", ""))
        kind = event_kind(session, previous)
        if not session_id or not kind or not seeded:
            continue

        events.append(
            {
                "id": session_id,
                "kind": kind,
                "key": session_key(session),
                "title": str(session.get("title", "")),
                "project": str(session.get("project", "")),
                "detail": str(session.get("preview", ""))[:160],
                "project_age": float(session.get("age_s") or 0),
                "at": stamp,
            }
        )

    # Loudest first, then longest-waiting: an arbitrary three of a five-item burst
    # is a coin flip, and the player never learns about the other two.
    events.sort(key=lambda event: (KIND_RANK.get(event["kind"], 9), event["at"]))
    return events if limit is None else events[:limit]


def _cooled(state: dict, events: list[dict], now: float) -> list[dict]:
    """Events past their cooldown. Does NOT consume it: only a notification that
    reached a channel may do that, or a machine without a notification daemon
    goes silent for the whole cooldown after every miss."""
    notified = state.setdefault("notified", {})
    kept: list[dict] = []

    for event in events:
        key = f"{event.get('key', event['id'])}:{event['kind']}"
        # "Never notified" is not the same as "notified at time zero": comparing
        # against 0 silences everything whenever the clock is small, which is how
        # this hid from a test written with a toy timestamp.
        last = notified.get(key)
        if last is not None and now - float(last) < COOLDOWN_SECONDS:
            continue
        kept.append(event)

    return kept


def _stamp_cooled(state: dict, events: Iterable[dict], now: float) -> None:
    """Consume the cooldown for the events that actually reached a channel."""
    notified = state.setdefault("notified", {})
    for event in events:
        notified[f"{event.get('key', event['id'])}:{event['kind']}"] = now
    prune(notified)


def prune(notified: dict, keep: int = 500) -> dict:
    """Keep the ledger bounded: a long-running install must not grow forever."""
    if len(notified) > keep:
        for key, _ in sorted(notified.items(), key=lambda item: item[1])[: len(notified) - keep]:
            notified.pop(key, None)
    return notified


def _headline(event: dict) -> str:
    label = KIND_LABELS.get(event["kind"], event["kind"])
    where = f" [{event['project']}]" if event.get("project") else ""
    return f"{event['title'] or event['id']}{where} {label}"


def notify_desktop(event: dict) -> dict:
    message = _headline(event)
    body = event.get("detail") or ""

    # notify-send takes a summary then a body. The old call passed an internal
    # "kind-id" string as the summary, so the player read that instead of the
    # headline, with the detail in a third positional it does not accept.
    if shutil.which("notify-send"):
        command = ["notify-send", "--app-name=Agent", "--category=IM.received", message, body]
        try:
            done = subprocess.run(command, check=False, timeout=5, capture_output=True)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"channel": "desktop", "event": event.get("key", event["id"]), "sent": False, "error": str(exc)}

        if done.returncode != 0:
            detail = (done.stderr or b"").decode("utf-8", "replace").strip()[:200]
            return {
                "channel": "desktop",
                "event": event.get("key", event["id"]),
                "sent": False,
                "error": detail or f"notify-send exited {done.returncode}",
            }
        return {"channel": "desktop", "event": event.get("key", event["id"]), "sent": True}

    return {"channel": "desktop", "event": event.get("key", event["id"]), "sent": False, "error": "no notify-send"}


def notify_hermes(event: dict, *, platform: str, hermes_bin: str = "hermes") -> dict:
    text = f"Agent: {_headline(event)}. Press your sync key in game for the board."
    try:
        proc = subprocess.run(
            [hermes_bin, "send", "--to", platform, text],
            check=False,
            timeout=30,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"channel": f"hermes:{platform}", "event": event.get("key", event["id"]), "sent": False, "error": str(exc)}

    if proc.returncode != 0:
        return {
            "channel": f"hermes:{platform}",
            "event": event.get("key", event["id"]),
            "sent": False,
            "error": (proc.stderr or proc.stdout or "").strip()[:200],
        }
    return {"channel": f"hermes:{platform}", "event": event.get("key", event["id"]), "sent": True}


def dispatch(
    events: list[dict],
    *,
    desktop: bool = True,
    platforms: Iterable[str] = (),
    dry_run: bool = False,
    state: dict | None = None,
    now: float | None = None,
    persist: bool = True,
) -> list[dict]:
    """Send the events that are out of their cooldown window.

    `persist=False` is for a caller that owns the state and saves it once at the
    end of its own pass: the watcher runs every ten seconds forever, and a second
    write per pass is one more than the data needs.
    """
    stamp = now if now is not None else time.time()
    state = state if state is not None else load_state()

    if dry_run:
        # A dry run must not consume a cooldown, or previewing would silence the
        # real notification that follows it. Preview against a copy.
        probe = {"near": "", "notified": dict(state.get("notified") or {})}
        allowed = _cooled(probe, events, stamp)
    else:
        allowed = _cooled(state, events, stamp)

    results: list[dict] = []
    delivered: list[dict] = []

    for event in allowed:
        if dry_run:
            results.append({"channel": "dry-run", "event": event.get("key", event["id"]), "sent": True, "line": _headline(event)})
            continue

        reached = False
        if desktop:
            outcome = notify_desktop(event)
            results.append(outcome)
            reached = reached or bool(outcome.get("sent"))
        for platform in platforms:
            outcome = notify_hermes(event, platform=platform)
            results.append(outcome)
            reached = reached or bool(outcome.get("sent"))

        if reached:
            delivered.append(event)

    if not dry_run:
        _stamp_cooled(state, delivered, stamp)
        if persist:
            save_state(state)
    return results


def remember(sessions: Iterable[dict], state: dict, only: Iterable[str] | None = None) -> dict:
    """Record the statuses this pass saw, so the next pass has baselines.

    `only` limits the update to sessions that were actually reported: an event
    dropped by the burst cap then keeps its old baseline and is reported on the
    next pass, instead of being recorded as seen and lost forever.
    """
    known = state.setdefault("known", {})
    allowed = {str(item) for item in only} if only is not None else None

    for session in sessions:
        session_id = session_key(session)
        if not session.get("id") or session.get("host_offline"):
            continue
        if allowed is None or str(session_id) in allowed:
            known[str(session_id)] = str(session.get("status"))

    if not state.get("seeded"):
        state["seeded"] = True
    return state


def transitions(
    sessions: list[dict],
    *,
    dry_run: bool = False,
    desktop: bool = True,
    platforms=(),
    now=None,
    state: dict | None = None,
) -> dict:
    """One pass: detect, dispatch, remember. Used by `wow watch` and the CLI.

    A caller that passes its own `state` owns it: this does not write it to the
    shared ledger, so a caller or a test can keep its bookkeeping in memory.
    """
    owned = state is None
    state = state if state is not None else load_state()
    if dry_run:
        state = copy.deepcopy(state)
    seeded = bool(state.get("seeded"))
    stamp = now if now is not None else time.time()
    previous = dict(state.get("known", {}))
    candidates = detect(sessions, previous=previous, seeded=seeded, now=stamp, limit=None)
    # Cooldowns are applied before the burst cap, or a flapping row can starve
    # an unrelated session which has never been reported.
    events = _cooled(state, candidates, stamp)[:MAX_PER_PASS]
    results = dispatch(
        events, desktop=desktop, platforms=platforms, dry_run=dry_run, state=state, now=now, persist=owned
    ) if events else []

    if not seeded:
        # The first pass is the silent baseline: everything is recorded, nothing
        # was reported about it, and the player never sees last week's alerts.
        remember(sessions, state)
    else:
        reported = {str(item.get("event")) for item in results if item.get("sent")}
        pending = {event["key"] for event in candidates}
        # Quiet transitions establish the next baseline too. Only actionable
        # events awaiting delivery keep the old one, so overflow can retry.
        remember(sessions, state, only=reported | {session_key(row) for row in sessions
                                                  if session_key(row) not in pending})

    if owned and not dry_run:
        save_state(state)
    return {"events": events, "results": results, "seeded": seeded}


def seed(sessions: Iterable[dict]) -> dict:
    """Mark the current state as the baseline without notifying about it."""
    state = load_state()
    remember(sessions, state)
    save_state(state)
    return {"known": len(state.get("known", {})), "seeded": True}


def status() -> dict[str, Any]:
    state = load_state()
    return {
        "seeded": bool(state.get("seeded")),
        "known_sessions": len(state.get("known", {})),
        "recent_notifications": sorted(
            ({"key": key, "at": value} for key, value in (state.get("notified") or {}).items()),
            key=lambda item: item["at"],
            reverse=True,
        )[:5],
        "state_path": str(STATE_PATH),
    }
