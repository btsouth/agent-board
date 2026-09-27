"""WoW process identity and compositor events, owned by the bridge watcher."""
from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

NAMES = {"wow.exe", "wowclassic.exe", "wowb.exe", "wowt.exe", "wowclassic", "wowb", "wowt"}


def matches(arguments: list[str]) -> bool:
    def basename(value):
        return value.replace('\\', '/').rsplit('/', 1)[-1].lower()
    if not arguments:
        return False
    executable = basename(arguments[0])
    return executable in NAMES or (executable in {'wine', 'wine64', 'wine-preloader', 'wine64-preloader'}
                                  and any(basename(arg) in NAMES for arg in arguments[1:]))


def identity(process: Path):
    try:
        if process.stat().st_uid != os.getuid():
            return None
        arguments = process.joinpath('cmdline').read_bytes().decode('utf-8', 'replace').split('\0')
        if not matches([arg for arg in arguments if arg]):
            return None
        # The start time disambiguates a recycled PID.
        return process.joinpath('stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def processes(root=Path('/proc')) -> dict[int, str]:
    result = {}
    try:
        entries = list(root.iterdir())
    except OSError:
        return result
    for entry in entries:
        if entry.name.isdigit() and (stamp := identity(entry)) is not None:
            result[int(entry.name)] = stamp
    return result


def window_for(clients, pids):
    return next((client for client in clients if client.get('mapped', True)
                 and client.get('pid') in pids), None)


class Tracker:
    def __init__(self):
        self.pids = {}
        self.next_scan = 0.0
        self.next_windows = 0.0
        self.windows = []
        self.last_present = 0.0

    def sample(self, *, changed=False):
        from . import hypr
        now = time.monotonic()
        self.pids = {pid: stamp for pid, stamp in self.pids.items()
                     if identity(Path('/proc') / str(pid)) == stamp}
        if changed or now >= self.next_scan:
            self.pids = processes()
            self.next_scan = now + (30 if self.pids else 5)
        if changed or now >= self.next_windows:
            self.windows = hypr.clients() if hypr._instance_signature() else []
            self.next_windows = now + 15
        window = window_for(self.windows, self.pids)
        present = bool(self.pids) and (window is not None or not hypr._instance_signature())
        if present:
            self.last_present = now
        # A brief gap during a client restart should not flash the overlay.
        running = present or bool(self.last_present and now - self.last_present < 2)
        return {'running': running, 'pids': sorted(self.pids), 'window': {
            key: window.get(key) for key in ('address', 'pid', 'monitor', 'workspace')
        } if window else None, 'at': time.time()}


class Events:
    """Wake detection on compositor events, with polling as a reconnect fallback."""
    def __init__(self, stop: threading.Event):
        self.changed = threading.Event()
        self.stop = stop
        self.thread = threading.Thread(target=self._listen, daemon=True, name='agent-board-game-events')
        self.thread.start()

    def _listen(self):
        from . import hypr
        while not self.stop.is_set():
            signature = hypr._instance_signature()
            runtime = os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
            if not signature:
                self.stop.wait(5)
                continue
            try:
                with socket.socket(socket.AF_UNIX) as client:
                    client.settimeout(1)
                    client.connect(str(Path(runtime) / 'hypr' / signature / '.socket2.sock'))
                    self.changed.set()
                    buffer = b''
                    while not self.stop.is_set():
                        try:
                            chunk = client.recv(4096)
                        except socket.timeout:
                            continue
                        if not chunk:
                            break
                        buffer += chunk
                        while b'\n' in buffer:
                            line, buffer = buffer.split(b'\n', 1)
                            if line.split(b'>>', 1)[0] in {b'openwindow', b'closewindow', b'movewindow', b'movewindowv2', b'monitoradded', b'monitorremoved', b'fullscreen'}:
                                self.changed.set()
                        if len(buffer) > 65536:
                            buffer = b''
            except OSError:
                pass
            self.stop.wait(2)
