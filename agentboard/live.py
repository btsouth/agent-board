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
    ) -> None:
        self._snapshot = snapshot
        self._action = action
        self._interval = max(0.2, float(interval))
        self._path = Path(path or socket_path())
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._latest: dict[str, Any] = {"error": "live bridge has not published yet"}
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
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=3.0)
        self._path.unlink(missing_ok=True)

    def wait_ready(self, timeout: float = 5.0) -> dict[str, Any]:
        self._ready.wait(timeout=max(0.0, timeout))
        return self.latest()

    def latest(self) -> dict[str, Any]:
        with self._lock:
            return {
                "revision": self._revision,
                "at": time.time(),
                "board": copy.deepcopy(self._latest),
            }

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        kind = str(request.get("type") or "state")
        if kind == "ping":
            return {"ok": True, "revision": self._revision}
        if kind == "state":
            try:
                known_revision = int(request.get("revision", -1))
            except (TypeError, ValueError):
                known_revision = -1
            if known_revision == self._revision:
                return {"ok": True, "revision": self._revision, "unchanged": True}
            return {"ok": True, **self.latest()}
        if kind == "action":
            action = request.get("action")
            if not isinstance(action, dict):
                return {"ok": False, "error": "missing action"}
            try:
                result = self._action(action)
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
            with self._lock:
                self._latest = board
                self._revision += 1
            self._ready.set()
            self._stop.wait(max(0.05, self._interval - (time.monotonic() - started)))
