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
            deadline = time.monotonic() + 5.0
            raw = bytearray()
            while b"\n" not in raw:
                client.settimeout(max(0.001, deadline - time.monotonic()))
                chunk = client.recv(4096)
                if not chunk:
                    raise RuntimeError("overlay closed before confirming the command")
                raw.extend(chunk)
                if len(raw) > 65536 or time.monotonic() >= deadline:
                    raise RuntimeError("overlay response exceeded its size or time limit")
        except OSError as exc:
            raise RuntimeError(f"overlay did not answer: {exc}") from exc

    try:
        reply = json.loads(bytes(raw).split(b"\n", 1)[0])
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("overlay returned invalid JSON") from exc
    if not isinstance(reply, dict) or not isinstance(reply.get("ok"), bool):
        raise RuntimeError("overlay returned an invalid confirmation")
    return reply


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
