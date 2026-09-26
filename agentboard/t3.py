"""T3 Code provider adapter.

The T3 desktop app owns a live WebSocket RPC server. Its protocol is compact but
not part of a public REST API, so the adapter is kept behind this provider
boundary rather than spread through the addon or Hermes code.

The provider is a persistent Node child. It owns the WebSocket connection,
reads T3's SQLite projections for rich previews, and communicates with this
Python bridge as newline-delimited JSON. One systemd service owns both.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
import atexit
from pathlib import Path
from typing import Any

from . import control
from . import state as state_module

ROOT = Path(__file__).resolve().parent.parent
PROVIDER = ROOT / "agentboard" / "providers" / "t3_provider.mjs"
LOG_PATH = state_module.STATE_DIR / "t3-provider.log"
STATE_DIR = state_module.STATE_DIR


def _node_binary() -> str | None:
    candidates = [Path("/usr/bin/node"), Path.home() / ".local/share/mise/shims/node"]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            try:
                result = subprocess.run(
                    [str(candidate), "-p", "process.versions.node"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if result.returncode == 0:
                return str(candidate)
    return shutil.which("node")


class T3Provider:
    def __init__(self, *, t3_home: Path | None = None) -> None:
        self.t3_home = Path(t3_home or os.environ.get("T3CODE_HOME") or Path.home() / ".t3").expanduser()
        self._process: subprocess.Popen[str] | None = None
        self._log = None
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._condition = threading.Condition()
        self._pending: dict[str, dict[str, Any]] = {}
        self._snapshot: dict[str, Any] = {
            "connected": False,
            "notice": "T3 Code has not published yet.",
            "rows": [],
            "projects": [],
            "received_at": 0.0,
        }
        self._next_id = 1
        self._restart_delay = 2.0

    def start(self) -> None:
        with self._condition:
            if self._process and self._process.poll() is None:
                return
            node = _node_binary()
            if not node:
                self._snapshot["notice"] = "T3 provider requires Node.js 24 or newer."
                return
            if not PROVIDER.is_file():
                self._snapshot["notice"] = f"T3 provider missing at {PROVIDER}."
                return
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            log = LOG_PATH.open("ab", buffering=0)
            self._log = log
            self._process = subprocess.Popen(
                [node, str(PROVIDER), "--jsonl", "--t3-home", str(self.t3_home)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
            thread = threading.Thread(target=self._read_stdout, name="t3-provider-stdout", daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        process = self._process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if message.get("type") == "snapshot":
                    with self._condition:
                        self._snapshot = {
                            "connected": bool(message.get("connected")),
                            "notice": str(message.get("notice") or ""),
                            "rows": list(message.get("rows") or []),
                            "projects": list(message.get("projects") or []),
                            "received_at": time.time(),
                        }
                        self._restart_delay = 2.0
                        self._condition.notify_all()
                elif message.get("type") == "result":
                    request_id = str(message.get("id") or "")
                    with self._condition:
                        pending = self._pending.pop(request_id, None)
                        if pending is not None:
                            pending["result"] = {
                                "ok": bool(message.get("ok")),
                                "message": str(message.get("message") or ""),
                                "thread_id": message.get("threadId") or message.get("thread_id"),
                                "project_id": message.get("projectId") or message.get("project_id"),
                                "message_id": message.get("messageId"),
                            }
                            self._condition.notify_all()
        finally:
            with self._condition:
                self._snapshot = {
                    **self._snapshot,
                    "connected": False,
                    "notice": self._snapshot.get("notice") or "T3 provider disconnected.",
                }
                for pending in self._pending.values():
                    pending["result"] = {"ok": False, "message": "T3 provider disconnected."}
                self._condition.notify_all()
            if not self._stop.is_set():
                time.sleep(self._restart_delay)
                self._restart_delay = min(30.0, self._restart_delay * 2)
                self.start()

    def snapshot(self, *, timeout: float = 1.0) -> dict[str, Any]:
        self.start()
        with self._condition:
            deadline = time.monotonic() + timeout
            while self._snapshot.get("received_at", 0.0) <= 0 and time.monotonic() < deadline:
                self._condition.wait(timeout=min(0.1, max(0.0, deadline - time.monotonic())))
            return json.loads(json.dumps(self._snapshot))

    def dispatch(self, action: dict[str, Any], *, timeout: float = 35.0) -> dict[str, Any]:
        if action.get("kind") == "focus":
            session_id = str(action.get("session_id") or "")
            if not session_id:
                return {"ok": False, "message": "T3 overlay request has no session id."}
            try:
                if not control.is_running():
                    from .wowclient import session_env

                    subprocess.Popen(
                        [str(ROOT / "bin" / "agent-board"), "overlay", "--mode", "board"],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                        env=session_env(),
                    )
                    if not control.wait_until_up(12.0):
                        return {"ok": False, "message": "Agent Board overlay did not start."}
                control.send(f"focus:{session_id}")
            except Exception as exc:  # noqa: BLE001 - report the desktop hand-off failure to the board
                return {"ok": False, "message": f"Could not open the Agent Board overlay: {exc}"}
            return {"ok": True, "message": f"Opened {session_id} in the live overlay."}
        self.start()
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            return {"ok": False, "message": "T3 provider is not running."}
        request_id = f"agent-board-{uuid.uuid4().hex}"
        record = {"event": threading.Event(), "result": None}
        with self._condition:
            self._pending[request_id] = record
        try:
            process.stdin.write(
                json.dumps({"id": request_id, "action": action}, separators=(",", ":")) + "\n"
            )
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            with self._condition:
                self._pending.pop(request_id, None)
            return {"ok": False, "message": f"T3 provider write failed: {exc}"}

        deadline = time.monotonic() + timeout
        with self._condition:
            while record["result"] is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._pending.pop(request_id, None)
                    return {"ok": False, "message": "T3 provider did not answer in time."}
                self._condition.wait(timeout=min(remaining, 1.0))
            return dict(record["result"])


_provider: T3Provider | None = None
_provider_lock = threading.Lock()


def provider() -> T3Provider:
    global _provider
    with _provider_lock:
        if _provider is None:
            _provider = T3Provider()
        return _provider


def snapshot(*, timeout: float = 1.0) -> dict[str, Any]:
    return provider().snapshot(timeout=timeout)


def dispatch(action: dict[str, Any]) -> dict[str, Any]:
    return provider().dispatch(action)


def health() -> dict[str, Any]:
    data = snapshot()
    return {
        "connected": bool(data.get("connected")),
        "rows": len(data.get("rows") or []),
        "projects": len(data.get("projects") or []),
        "notice": str(data.get("notice") or ""),
        "received_at": float(data.get("received_at") or 0.0),
    }


def _shutdown() -> None:
    global _provider
    if _provider is not None:
        _provider.stop()


atexit.register(_shutdown)
