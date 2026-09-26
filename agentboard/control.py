"""Control channel for the running overlay: one unix socket, one JSON line."""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path


def socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / "agent-board.sock"


def send(command: str, **payload) -> dict:
    """Send one command to the overlay; raises RuntimeError when it is not up."""
    path = socket_path()
    if not path.exists():
        raise RuntimeError("overlay is not running (start it with `agent-board overlay`)")

    message = json.dumps({"cmd": command, **payload}) + "\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5.0)
        try:
            client.connect(str(path))
            client.sendall(message.encode())
            raw = client.recv(65536)
        except OSError as exc:
            raise RuntimeError(f"overlay did not answer: {exc}") from exc

    if not raw:
        return {"ok": True}
    try:
        return json.loads(raw.decode("utf-8", "replace").strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"ok": True, "raw": raw.decode("utf-8", "replace").strip()}


def is_running() -> bool:
    try:
        reply = send("ping")
    except RuntimeError:
        return False
    return bool(reply.get("ok"))


def wait_until_up(timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_running():
            return True
        time.sleep(0.25)
    return False
