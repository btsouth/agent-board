"""Local live-state and action socket for the desktop overlay.

The WoW addon can only read files during a UI reload. The overlay has no such
limitation, so it talks to this Unix socket instead of spawning a new CLI process
for every refresh. The bridge keeps one provider snapshot warm and serves it to
the overlay on demand; actions go through the same dispatcher as in-game actions.
"""

from __future__ import annotations

import copy
import json
import os
import socketserver
import threading
import time
from pathlib import Path
from typing import Any, Callable


MAX_MESSAGE = 1_048_576
# How long one state request may wait for a change. The overlay long-polls, so a
# new revision reaches it as soon as it exists instead of at the next poll tick.
MAX_WAIT = 25.0
# Fields that change on every snapshot without anything happening. They do not
# count as a change, but a revision is still forced now and then so ages advance.
VOLATILE = {"generated_at", "age_s"}
AGE_REFRESH = 30.0


def _change_key(board: Any) -> str:
    def strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: strip(item) for key, item in value.items() if key not in VOLATILE}
        if isinstance(value, list):
            return [strip(item) for item in value]
        return value
    try:
        return json.dumps(strip(board), sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return ""


def _focus(board: dict[str, Any], session_id: str) -> dict[str, Any]:
    """Only the open session's conversation: the rest of the board is rows."""
    sessions = []
    def matches(row):
        if row.get('host', 'local') == 'local':
            return row.get('id') == session_id
        try:
            return json.loads(session_id) == [row.get('host'), row.get('provider'), row.get('id')]
        except (ValueError, TypeError):
            return False
    for row in board.get("sessions") or []:
        if isinstance(row, dict) and "conversation" in row:
            messages = row.get("conversation") or []
            # Queue receipts survive transcript filtering. No prompt text leaves
            # the focused conversation; digests distinguish repeated prompts.
            import hashlib
            receipts = [{"id": item.get("id"), "role": item.get("role"),
                         **({"digest": hashlib.sha256(str(item.get("text") or "").encode()).hexdigest()}
                            if item.get("role") == "user" else {})}
                        for item in messages]
            row = {**row, "message_receipts": receipts}
            if not matches(row):
                row = {key: value for key, value in row.items() if key != "conversation"}
        sessions.append(row)
    return {**board, "sessions": sessions}


def socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / "agent-board-live.sock"


class _ThreadingUnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline(MAX_MESSAGE + 1)
        if not raw or len(raw) > MAX_MESSAGE:
            self._write({"ok": False, "error": "request too large"})
            return
        try:
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
        except (UnicodeDecodeError, ValueError, TypeError):
            self._write({"ok": False, "error": "bad json"})
            return
        self._write(self.server.hub.handle(request))  # type: ignore[attr-defined]

    def _write(self, payload: dict[str, Any]) -> None:
        try:
            self.wfile.write(json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n")
        except (BrokenPipeError, ConnectionResetError):
            pass


class LiveBridge:
    """One polling thread, a tiny Unix socket, and no provider-specific logic."""

    def __init__(
        self,
        *,
        snapshot: Callable[[], dict[str, Any]],
        action: Callable[[dict[str, Any]], dict[str, Any]],
        interval: float = 1.0,
        path: Path | None = None,
        idle_interval: float | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._action = action
        self._interval = max(0.2, float(interval))
        self._idle_interval = max(self._interval, idle_interval or self._interval)
        self._interests: set[str] = set()
        self._path = Path(path or socket_path())
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._latest: dict[str, Any] = {"error": "live bridge has not published yet"}
        self._latest_key = ""
        self._latest_at = 0.0
        self._revision = 0
        self._poll_thread: threading.Thread | None = None
        self._server: _ThreadingUnixServer | None = None
        self._server_thread: threading.Thread | None = None

    def start(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        self._server = _ThreadingUnixServer(str(self._path), _Handler)
        self._server.hub = self  # type: ignore[attr-defined]
        os.chmod(self._path, 0o600)
        self._poll_thread = threading.Thread(target=self._poll, name="agent-board-live-state", daemon=True)
        self._poll_thread.start()
        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            name="agent-board-live-socket",
            daemon=True,
        )
        self._server_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._changed:
            self._changed.notify_all()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=3.0)
        self._path.unlink(missing_ok=True)

    def wait_ready(self, timeout: float = 5.0) -> dict[str, Any]:
        self._ready.wait(timeout=max(0.0, timeout))
        return self.latest()

    def interests(self) -> set[str]:
        with self._lock:
            return set(self._interests)

    def latest(self, focus: str | None = None) -> dict[str, Any]:
        with self._lock:
            board = self._latest if focus is None else _focus(self._latest, focus)
            return {
                "revision": self._revision,
                "at": time.time(),
                "board": copy.deepcopy(board),
            }

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        kind = str(request.get("type") or "state")
        if kind == "ping":
            return {"ok": True, "revision": self._revision}
        if kind == "state":
            wanted = request.get("queue_for") or []
            if isinstance(wanted, list):
                with self._lock:
                    before = set(self._interests)
                    self._interests = {str(item) for item in wanted[:128] if isinstance(item, str)}
                    if request.get("conversation_for"):
                        focus = str(request["conversation_for"])
                        try:
                            remote = json.loads(focus)
                            if isinstance(remote, list) and len(remote) == 3:
                                focus = str(remote[2])
                        except ValueError:
                            pass
                        self._interests.add(focus)
                    if self._interests != before:
                        self._wake.set()
            try:
                known_revision = int(request.get("revision", -1))
            except (TypeError, ValueError):
                known_revision = -1
            try:
                wait = min(MAX_WAIT, max(0.0, float(request.get("wait", 0) or 0)))
            except (TypeError, ValueError):
                wait = 0.0
            if wait and known_revision == self._revision:
                with self._changed:
                    self._changed.wait_for(
                        lambda: self._revision != known_revision or self._stop.is_set(), timeout=wait
                    )
            if known_revision == self._revision:
                return {"ok": True, "revision": self._revision, "unchanged": True}
            focus = request.get("conversation_for")
            return {"ok": True, **self.latest(None if focus is None else str(focus))}
        if kind == "action":
            action = request.get("action")
            if not isinstance(action, dict):
                return {"ok": False, "error": "missing action"}
            try:
                result = self._action(action)
                self._wake.set()
            except Exception as exc:  # noqa: BLE001 - a bad action must not kill the live bridge
                return {"ok": False, "error": str(exc)}
            return {"ok": bool(result.get("ok")), **result}
        return {"ok": False, "error": f"unknown request type: {kind}"}

    def _poll(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                board = self._snapshot()
                if not isinstance(board, dict):
                    raise TypeError("snapshot must be an object")
            except Exception as exc:  # noqa: BLE001 - serve the error to the overlay
                board = {"error": str(exc), "sessions": [], "counts": {}}
            # A new revision only when something changed, so an idle board costs
            # the overlay nothing and a waiting request wakes exactly on a change.
            key = _change_key(board)
            now = time.monotonic()
            with self._changed:
                if not key or key != self._latest_key or self._revision == 0 or now - self._latest_at >= AGE_REFRESH:
                    self._latest = board
                    self._latest_key = key
                    self._latest_at = now
                    self._revision += 1
                    self._changed.notify_all()
            self._ready.set()
            active = any(row.get("status") in {"working", "starting", "waiting"}
                         for row in board.get("sessions", []))
            interval = self._interval if active else self._idle_interval
            self._wake.wait(max(0.05, interval - (time.monotonic() - started)))
            self._wake.clear()
