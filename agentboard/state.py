"""Where this tool keeps its state, and how it writes it.

Four modules used to rebuild the XDG paths and hand-roll their own
write-to-temp-and-rename, which is four chances to get it subtly wrong. The
details that matter and are easy to miss:

* a UNIQUE temp name: a fixed one means two processes (the watcher and a manual
  publish) truncate each other's half-written file
* `fsync` before the rename: without it a power cut can leave a zero-length file
  where the state used to be, which the reader then has to treat as damage
* 0600 on the state files: they hold the player's own reply text and session ids
* a directory `fsync` after the rename, so the rename itself survives
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _xdg(env: str, fallback: str) -> Path:
    return Path(os.environ.get(env) or (Path.home() / fallback))


STATE_DIR = _xdg("XDG_STATE_HOME", ".local/state") / "agent-board"
CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "agent-board"


def atomic_write(path: Path, text: str, *, mode: int = 0o600) -> None:
    """Replace `path` with `text`, all-or-nothing.

    A reader either sees the whole previous file or the whole new one: the addon
    refuses a torn payload, and the bridge's dedupe ledger existing at all is what
    stops a reply being sent twice.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    temp = Path(temp_name)

    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise

    # Make the rename itself durable. Best effort: a filesystem that will not open
    # a directory for reading is not a reason to fail the write.
    try:
        directory = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory)
    except OSError:
        pass
    finally:
        os.close(directory)
