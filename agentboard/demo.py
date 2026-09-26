"""A scripted, fictional board for screenshots and screen recordings.

``agent-board demo`` serves this world on its own socket and tells the running
overlay to use it instead of the real bridge, so a recording shows the real
product without anyone's real sessions. Nothing here touches T3 Code or Hermes.

The world is a clock and a few sessions. A running session is a queue of timed
events (a tool step, an activity line, a reply that streams in, the end of the
turn); replying, approving, stopping or starting a session in the overlay adds
more of them, so the demo answers back while you record.
"""

from __future__ import annotations

import json
import os
import signal
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import control, live

T3_CAPABILITIES = ["reply", "new", "approve", "decline", "answer", "mark_read", "mark_unread", "stop", "settle", "archive"]
HERMES_CAPABILITIES = ["reply", "approve", "decline", "mark_read", "mark_unread", "stop", "archive"]
STREAM_RATE = 70.0  # characters per second, about the pace of a fast model

PROJECTS = [
    {"id": "demo-project-uploader", "title": "uploader", "workspace_root": "/home/you/code/uploader"},
    {"id": "demo-project-billing", "title": "billing", "workspace_root": "/home/you/code/billing"},
    {"id": "demo-project-docs", "title": "docs-site", "workspace_root": "/home/you/code/docs-site"},
    {"id": "demo-project-infra", "title": "infra", "workspace_root": "/home/you/code/infra"},
]

HERO_PROMPT = "Uploads to the archive bucket keep hammering the API after a 404. Can you find out why and fix it?"
HERO_REPLY = """Found it. The uploader retries **every** failure, including 4xx responses that will never succeed.

- `retryUpload()` catches any thrown error and tries again
- the backoff never resets after a success, so one bad file slows the whole batch

```ts
if (error.status >= 400 && error.status < 500 && error.status !== 429) {
  throw error // not retryable: the request itself is wrong
}
```

| Case | Before | After |
|---|---|---|
| 429 rate limit | 5 retries, fixed delay | backoff, then retry |
| 404 missing object | 5 retries | fails fast with the key in the error |
| 503 unavailable | 5 retries | backoff, then retry |

All 48 upload tests pass, and I added two for the 404 and 429 paths. Want me to open a PR?"""

HERMES_REPLY = """Here's the incident in short:

1. **14:02** a config push set the cache TTL on `/api/session` to one year
2. **14:09** logins started failing for anyone whose token rotated
3. **14:31** the push was reverted and the CDN purged

Nobody lost data. The follow-ups are a TTL guard in the deploy check and an alert on login error rate. I drafted both as tickets."""

APPROVE_REPLY = """The webhook handler now uses the v18 client.

- signature checks moved to `constructEventAsync`
- the two deprecated event names are mapped in `events.ts`

Replayed last week's 312 webhook fixtures against it with no differences."""

DECLINE_REPLY = "Okay, I left the dependency alone. I can pin the current version and patch the two deprecated calls instead if you prefer."

FOLLOW_UP_REPLIES = [
    "On it. I checked the latest state and there is nothing blocking. I'll keep going and ping you when it's done.",
    """Done. Here's what changed:

- tightened the error message so it names the failing key
- added a test for the empty-batch case

Everything is green.""",
    "Good call. I switched to that approach and reran the suite; still passing, and the diff is smaller now.",
]
FOLLOW_UP_STEPS = [
    ["Read src/index.ts", "Ran `npm test`"],
    ["Searched files for handleError", "Edited src/errors.ts", "Ran `npm test -- errors`"],
    ["Edited src/config.ts", "Ran `npm run lint`"],
]
NEW_SESSION_STEPS = ["Read README.md", "Searched files for TODO", "Ran `git status`"]

# Answers matched to what you typed, so a recorded exchange reads naturally.
# The first keyword found wins; anything else gets the rotating replies above.
KEYWORD_REPLIES = [
    (("pr", "pull request", "review"), ["Ran `git push -u origin fix/retry-4xx`", "Ran `gh pr create --fill`"],
     "Opened **#482** with the fix and the two new tests, and requested a review. CI is running now; I'll tell you if anything goes red."),
    (("test", "coverage"), ["Ran `npm test`", "Ran `npm run coverage`"],
     "All 214 tests pass. Coverage on the changed files is 96%; the one uncovered branch is the legacy v1 path, which is behind a flag."),
    (("ship", "deploy", "release", "merge"), ["Ran `gh pr merge 482 --squash`", "Ran `npm run deploy -- --env staging`"],
     "Merged and deployed to staging. Health checks are green and error rate is flat. Say the word and I'll promote it to production."),
    (("why", "explain", "how"), ["Read src/upload.ts"],
     "Short version: the old code treated every error as temporary. A 404 means the request is wrong, so retrying it only adds load. Now only 429s and 5xx responses retry, with backoff."),
]


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


class DemoWorld:
    def __init__(self, *, speed: float = 1.0, lead: float = 0.0, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._speed = max(0.1, float(speed))
        # Seconds before the scripted agents start: autoplay uses it to open the
        # board first, so the recording catches the first tool step.
        self._lead = max(0.0, float(lead))
        self._lock = threading.Lock()
        self._events: list[tuple[float, int, Callable[[], None]]] = []
        self._order = 0
        self._replies = 0
        self._new = 0
        self.sessions: dict[str, dict[str, Any]] = {}
        self._seed()

    # ------------------------------------------------------------- building

    def _now(self) -> float:
        return self._clock()

    def _at(self, delay: float, action: Callable[[], None]) -> None:
        self._order += 1
        self._events.append((self._now() + delay / self._speed, self._order, action))

    def _session(self, sid: str, provider: str, title: str, project: str, status: str, ago: float, **extra: Any) -> dict[str, Any]:
        now = self._now()
        session = {
            "id": sid,
            "provider": provider,
            "provider_label": "T3 Code" if provider == "t3" else "Hermes",
            "host": "local",
            "title": title,
            "project": project,
            "profile": "claude / opus" if provider == "t3" else "hermes / default",
            "status": status,
            "unread": False,
            "settled": False,
            "snippet": "",
            "activity": "",
            "activity_at": now - ago,
            "capabilities": list(T3_CAPABILITIES if provider == "t3" else HERMES_CAPABILITIES),
            "conversation": [],
            "stream": None,
            **extra,
        }
        self.sessions[sid] = session
        return session

    def _message(self, session: dict[str, Any], role: str, text: str, ago: float = 0.0) -> None:
        session["conversation"].append({
            "id": f"{session['id']}-m{len(session['conversation'])}",
            "role": role,
            "text": text,
            "created_at": _iso(self._now() - ago),
        })

    def _seed(self) -> None:
        hero = self._session("demo-uploader", "t3", "Audit the retry logic in the uploader", "uploader", "working", 20,
                             activity="Reading the upload path")
        self._message(hero, "user", HERO_PROMPT, ago=20)
        self._turn(hero, ["Searched files for retryUpload", "Read src/upload.ts", "Read src/backoff.ts",
                          "Ran `npm test -- upload`", "Edited src/upload.ts", "Edited test/upload.test.ts",
                          "Ran `npm test -- upload`"], HERO_REPLY, start=2.0 + self._lead, gap=1.6,
                   activities=["Reading the upload path", "Running the upload tests", "Fixing the retry rule", "Rerunning the tests"])

        incident = self._session("demo-incident", "hermes", "Summarise the incident thread", "ops", "working", 45,
                                 activity="Reading 42 messages")
        self._message(incident, "user", "Can you summarise yesterday's login incident thread for the team?", ago=45)
        self._turn(incident, ["Read #incident-login (42 messages)", "Searched deploy log for cache TTL"], HERMES_REPLY,
                   start=14.0 + self._lead, gap=5.0, activities=["Reading 42 messages", "Checking the deploy log", "Writing the summary"])

        billing = self._session("demo-billing", "t3", "Migrate the billing webhooks", "billing", "needs", 70,
                                approval_request_id="demo-approval-1",
                                approval_summary="Run: npm install stripe@18",
                                activity="Waiting for approval to update the Stripe client")
        self._message(billing, "user", "Move the billing webhooks to the new Stripe client.", ago=300)
        self._message(billing, "agent", "The new client changes how signatures are verified. I need to update the dependency first.", ago=70)

        notes = self._session("demo-notes", "t3", "Draft the 2.1 release notes", "docs-site", "reply", 240, unread=True,
                              snippet="The draft is in docs/releases/2.1.md with three highlights and the migration notes.")
        self._message(notes, "user", "Draft release notes for 2.1 from the merged PRs.", ago=600)
        self._message(notes, "agent", "The draft is in `docs/releases/2.1.md` with three highlights and the migration notes.\n\n"
                      "- **Faster sync**: large projects index about 3x faster\n- **Offline mode** for the CLI\n"
                      "- **New theme API** for plugins\n\nThe migration notes cover the renamed config key.", ago=240)

        cdn = self._session("demo-cdn", "hermes", "Check the CDN cache headers", "infra", "reply", 1300, unread=True,
                            snippet="Static assets are cached for a year; the HTML was set to no-store.")
        self._message(cdn, "user", "Are our CDN cache headers sane?", ago=1500)
        self._message(cdn, "agent", "Mostly. Static assets are cached for a year with hashed names, which is right. "
                      "The HTML was set to `no-store`; `no-cache` would let the CDN revalidate instead of refetching.", ago=1300)

        for sid, provider, title, project, ago in (
            ("demo-scheduler", "t3", "Fix the flaky scheduler test", "scheduler", 3 * 3600),
            ("demo-fixtures", "hermes", "Trim unused test fixtures", "cli", 5 * 3600),
            ("demo-completion", "t3", "Add shell completion to the installer", "cli", 26 * 3600),
            ("demo-onboarding", "t3", "Write the onboarding guide", "docs-site", 30 * 3600),
        ):
            done = self._session(sid, provider, title, project, "finished", ago)
            self._message(done, "user", f"{title}.", ago=ago + 900)
            self._message(done, "agent", "Done. Tests pass and the change is on a branch.", ago=ago)

    # ---------------------------------------------------------------- turns

    def _turn(self, session: dict[str, Any], steps: list[str], reply: str, *, start: float = 1.0, gap: float = 1.2,
              activities: list[str] | None = None) -> None:
        """Queue one agent turn: tool steps, then the reply streaming in, then the end."""
        session.update(status="working", unread=False, stream=None, approval_request_id="", approval_summary="")
        session["turn"] = session.get("turn", 0) + 1
        turn = session["turn"]
        activities = activities or ["Working"]

        def still(fn: Callable[[], None]) -> Callable[[], None]:
            # A stop or a new turn cancels whatever this one still had queued.
            return lambda: fn() if session.get("turn") == turn and session["status"] == "working" else None

        for index, step in enumerate(steps):
            def add_step(step: str = step, index: int = index) -> None:
                session["conversation"].append({"id": f"{session['id']}-t{turn}-{index}", "role": "tool", "text": step,
                                                "created_at": _iso(self._now())})
                session["activity"] = activities[min(len(activities) - 1, index * len(activities) // max(1, len(steps)))]
                session["activity_at"] = self._now()
            self._at(start + index * gap, still(add_step))

        streaming_at = start + len(steps) * gap + 0.8
        duration = len(reply) / STREAM_RATE

        def begin_stream() -> None:
            session["activity"] = activities[-1]
            session["stream"] = {"id": f"{session['id']}-r{turn}", "text": reply, "start": self._now(), "created_at": _iso(self._now())}

        def finish() -> None:
            self._finish(session, reply)

        self._at(streaming_at, still(begin_stream))
        self._at(streaming_at + duration + 0.4, still(finish))

    def _finish(self, session: dict[str, Any], text: str, *, stopped: bool = False) -> None:
        stream = session.get("stream") or {}
        if text.strip():
            session["conversation"].append({"id": stream.get("id") or f"{session['id']}-final{len(session['conversation'])}",
                                            "role": "agent", "text": text, "created_at": stream.get("created_at") or _iso(self._now())})
        session.update(status="reply", unread=True, stream=None, activity="Stopped" if stopped else "Turn complete",
                       activity_at=self._now(), snippet=_first_line(text))

    def _streamed(self, session: dict[str, Any]) -> str:
        stream = session.get("stream")
        if not stream:
            return ""
        shown = int((self._now() - stream["start"]) * STREAM_RATE * self._speed)
        text = stream["text"][: max(0, shown)]
        # End on a word, the way tokens arrive, not in the middle of one.
        cut = text.rfind(" ")
        return text if len(text) >= len(stream["text"]) or cut < 0 else text[:cut]

    # ------------------------------------------------------------ interface

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = self._now()
            due = sorted(event for event in self._events if event[0] <= now)
            self._events = [event for event in self._events if event[0] > now]
            for _when, _order, action in due:
                action()
            rows = []
            for session in self.sessions.values():
                row = {key: value for key, value in session.items() if key not in {"stream", "turn"}}
                conversation = list(session["conversation"])
                partial = self._streamed(session)
                if partial:
                    stream = session["stream"]
                    conversation.append({"id": stream["id"], "role": "agent", "text": partial,
                                         "created_at": stream["created_at"], "streaming": True})
                row["conversation"] = conversation
                row["age_s"] = max(0, int(now - session["activity_at"]))
                row["status_label"] = {"working": "Working", "needs": "Needs you", "reply": "New reply",
                                       "finished": "Finished"}.get(session["status"], session["status"].title())
                rows.append(row)
            rows.sort(key=lambda row: row["age_s"])
            return {"generated_at": now, "sessions": rows, "providers": {"t3": "ok", "hermes": "ok"},
                    "projects": PROJECTS, "counts": {}, "t3_notice": ""}

    def action(self, action: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            kind = str(action.get("kind") or "")
            sid = str(action.get("session_id") or "")
            text = str(action.get("text") or "")
            if kind == "new":
                return self._new_session(sid, text)
            session = self.sessions.get(sid)
            if session is None:
                return {"ok": False, "error": "That session is gone."}
            if kind == "reply":
                self._message(session, "user", text)
                session["activity_at"] = self._now()
                if session["status"] != "working":
                    steps, reply = self._answer_for(text)
                    self._turn(session, steps, reply, start=1.2, gap=1.3)
                return {"ok": True, "message": "Delivered."}
            if kind in {"approve", "decline"} and session.get("approval_request_id"):
                if kind == "approve":
                    self._turn(session, ["Ran `npm install stripe@18`", "Edited src/webhooks.ts", "Edited src/events.ts",
                                         "Ran `npm test -- webhooks`"], APPROVE_REPLY, start=0.8, gap=1.5,
                               activities=["Installing the Stripe client", "Updating the handler", "Replaying fixtures"])
                else:
                    self._turn(session, [], DECLINE_REPLY, start=0.5)
                return {"ok": True, "message": "Approved." if kind == "approve" else "Declined."}
            if kind == "stop":
                if session["status"] == "working":
                    session["turn"] = session.get("turn", 0) + 1
                    self._finish(session, self._streamed(session), stopped=True)
                return {"ok": True, "message": "Stopped."}
            if kind == "mark_read":
                session["unread"] = False
            elif kind == "mark_unread":
                session["unread"] = True
            elif kind == "settle":
                session.update(settled=True, unread=False)
            elif kind == "unsettle":
                session["settled"] = False
            elif kind == "archive":
                del self.sessions[sid]
            else:
                return {"ok": True, "message": "Done."}
            return {"ok": True, "message": "Done."}

    def _answer_for(self, text: str) -> tuple[list[str], str]:
        words = set("".join(char if char.isalnum() else " " for char in text.lower()).split())
        lowered = text.lower()
        for keywords, steps, reply in KEYWORD_REPLIES:
            if any((keyword in words) if " " not in keyword else (keyword in lowered) for keyword in keywords):
                return steps, reply
        index = self._replies % len(FOLLOW_UP_REPLIES)
        self._replies += 1
        return FOLLOW_UP_STEPS[index], FOLLOW_UP_REPLIES[index]

    def _new_session(self, project_id: str, prompt: str) -> dict[str, Any]:
        project = next((item for item in PROJECTS if item["id"] == project_id), PROJECTS[0])
        self._new += 1
        sid = f"demo-new-{self._new}"
        title = prompt.strip().splitlines()[0][:60] or "New session"
        session = self._session(sid, "t3", title, project["title"], "working", 0, activity="Starting")
        self._message(session, "user", prompt)
        self._turn(session, NEW_SESSION_STEPS, "I looked around the project first. " + FOLLOW_UP_REPLIES[1], start=1.5, gap=1.4,
                   activities=["Reading the project", "Planning", "Working"])
        return {"ok": True, "message": "Started.", "thread_id": sid}


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip().lstrip("-*#>0123456789. ").replace("**", "").replace("`", "")
        if any(char.isalpha() for char in line):
            return line[:160]
    return ""


# ------------------------------------------------------------------ runner

def marker_path() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / "agent-board-demo.json"


def socket_path() -> Path:
    return marker_path().with_name("agent-board-demo.sock")


def _reset_overlay() -> None:
    # The overlay may be holding a long poll open against the other source.
    try:
        if control.is_running():
            control.send("live-reset")
    except Exception:  # noqa: BLE001 - the overlay picks the switch up on its next request anyway
        pass


def stop() -> int:
    marker = marker_path()
    try:
        pid = int(json.loads(marker.read_text()).get("pid") or 0)
    except (OSError, ValueError):
        pid = 0
    if pid and pid != os.getpid():
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    marker.unlink(missing_ok=True)
    _reset_overlay()
    print("demo stopped; the overlay is back on your real sessions")
    return 0


# The autoplay script: (seconds after the previous step, overlay command). Times
# are at speed 1; the overlay does the clicking and typing itself, through the
# same buttons and composer a person would use.
AUTOPLAY = [
    (2.5, "focus:demo-uploader"),
    (30.0, "focus:demo-billing"),
    (2.5, "demo:click:approve-button"),
    (13.0, "focus:demo-uploader"),
    (2.0, "demo:type:open the PR and ask Sam to review it"),
    (12.0, "badge"),
]
AUTOPLAY_LEAD = 3.0


def _autoplay(speed: float, stopping: threading.Event) -> None:
    for delay, command in AUTOPLAY:
        if stopping.wait(delay / max(0.1, speed)):
            return
        try:
            control.send(command)
        except Exception as exc:  # noqa: BLE001 - say what failed and keep the demo up
            print(f"autoplay: {command.split(':')[0]} failed: {exc}")
            return
    print("autoplay finished. Ctrl+C to go back to your real sessions.")


def run(*, speed: float = 1.0, autoplay: bool = False) -> int:
    world = DemoWorld(speed=speed, lead=AUTOPLAY_LEAD if autoplay else 0.0)
    path = socket_path()
    hub = live.LiveBridge(snapshot=world.snapshot, action=world.action, interval=0.1, path=path)
    hub.start()
    marker = marker_path()
    marker.write_text(json.dumps({"pid": os.getpid(), "socket": str(path)}))
    os.chmod(marker, 0o600)
    _reset_overlay()

    stopping = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopping.set())
    print("demo running: the overlay now shows fictional sessions.")
    print("an agent starts streaming in a few seconds. Ctrl+C (or `agent-board demo --stop`) to go back.")
    if not control.is_running():
        print("the overlay is not running yet; it will pick the demo up when it starts.")
    if autoplay:
        if not control.is_running():
            print("autoplay needs the overlay: start WoW (or `agent-board overlay`) and run this again.")
        else:
            try:
                control.send("badge")
            except Exception:  # noqa: BLE001 - the first autoplay step reports a dead overlay
                pass
            print("autoplay: the board opens in a few seconds and runs the whole story by itself.")
            threading.Thread(target=_autoplay, args=(speed, stopping), daemon=True, name="demo-autoplay").start()
    try:
        stopping.wait()
    finally:
        marker.unlink(missing_ok=True)
        hub.stop()
        _reset_overlay()
        print("\ndemo stopped; the overlay is back on your real sessions")
    return 0
