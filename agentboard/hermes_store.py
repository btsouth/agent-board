"""Session flags (read, archived) written by Hermes's own code.

The bridge never writes Hermes's session database itself. Archiving and
marking read go through Hermes's ``SessionDB``, run in Hermes's own runtime:
``hermes --print-runtime-command`` is the published way to get that runtime,
and the setters there keep a session's compression lineage consistent, which a
raw UPDATE would not.
"""

from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from typing import Any

from . import backend

# Everything after the runtime's own bootstrap: resolve the id Hermes's way,
# apply the requested flags, report one JSON line.
_SNIPPET = """
import json, sys
from hermes_state import SessionDB
request = json.loads(sys.argv[1])
db = SessionDB()
try:
    sid = db.resolve_session_id(request["session_id"])
    if not sid:
        print(json.dumps({"ok": False, "error": "Hermes no longer has this session"}))
    else:
        if "archived" in request:
            db.set_session_archived(sid, bool(request["archived"]))
        if "read" in request:
            db.set_session_read(sid, read=bool(request["read"]))
        print(json.dumps({"ok": True, "session_id": sid}))
finally:
    db.close()
"""


@lru_cache(maxsize=1)
def _runtime() -> tuple[str, str]:
    """Hermes's interpreter and its bootstrap, up to the point it would start the CLI."""
    output = subprocess.run(
        [backend._hermes_bin(), "--print-runtime-command"],
        capture_output=True, text=True, timeout=20, check=True,
    ).stdout
    command = json.loads(output)
    code = command[command.index("-c") + 1]
    bootstrap = code.split("runpy.run_module")[0]
    if "hermes_bootstrap" not in bootstrap:
        raise RuntimeError("unrecognised Hermes runtime command")
    return command[0], bootstrap


def set_flags(session_id: str, **flags: bool) -> dict[str, Any]:
    request = {"session_id": session_id, **{key: bool(value) for key, value in flags.items() if key in {"archived", "read"}}}
    try:
        python, bootstrap = _runtime()
        completed = subprocess.run(
            [python, "-I", "-c", bootstrap + "\n" + _SNIPPET, json.dumps(request)],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as exc:
        _runtime.cache_clear()
        return {"ok": False, "error": f"Hermes is unavailable: {exc}"}
    lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
    if completed.returncode != 0 or not lines:
        detail = (completed.stderr or completed.stdout).strip().splitlines()[-1:] or ["no output"]
        return {"ok": False, "error": f"Hermes could not update the session: {detail[0]}"}
    try:
        return json.loads(lines[-1])
    except ValueError:
        return {"ok": False, "error": "Hermes returned an unreadable answer"}
