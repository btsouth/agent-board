#!/usr/bin/env python3
"""Exercise the pasteable installer without network access or user config writes."""
from pathlib import Path
import json
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
ORIGIN = "https://github.com/btsouth/agent-board.git"
failures = []


def check(label, condition):
    print(("PASS  " if condition else "FAIL  ") + label)
    if not condition:
        failures.append(label)


with tempfile.TemporaryDirectory(prefix="hermes-bootstrap-") as temporary:
    base = Path(temporary)
    tools = base / "tools"
    tools.mkdir()
    fake_git = tools / "git"
    fake_git.write_text('''#!/usr/bin/env python3
from pathlib import Path
import os,sys,json
args=sys.argv[1:]
if args[0] == 'clone':
    if os.environ.get('FAIL_DOWNLOAD'): sys.exit(7)
    assert args[:6] == ['clone','--quiet','--depth','1','--branch','v0.1.0'],args
    assert args[6] == 'https://github.com/btsouth/agent-board.git',args
    target=Path(args[-1]); (target/'.git').mkdir(parents=True); (target/'bin').mkdir()
    launcher=target/'bin/agent-board'
    launcher.write_text('#!/usr/bin/env python3\\nimport os,sys,json\\nfrom pathlib import Path\\nPath(os.environ["CAPTURE"]).write_text(json.dumps(sys.argv[1:]))\\nsys.exit(int(os.environ.get("SETUP_EXIT", "0")))\\n')
    launcher.chmod(0o755)
elif args[0] == '-C':
    print(os.environ.get('TEST_ORIGIN','https://github.com/btsouth/agent-board.git'))
else: sys.exit(8)
''')
    fake_git.chmod(0o755)
    systemctl = tools / "systemctl"
    systemctl.write_text("#!/bin/sh\nexit 0\n")
    systemctl.chmod(0o755)
    data = base / 'path with spaces % $ and "quotes"'
    capture = base / "capture.json"
    env = dict(os.environ, XDG_DATA_HOME=str(data), PATH=str(tools) + os.pathsep + os.environ["PATH"], CAPTURE=str(capture))

    def run(*args, extra=None):
        return subprocess.run(["bash", str(ROOT / "install.sh"), *args], input="", text=True,
                              capture_output=True, env={**env, **(extra or {})}, start_new_session=True)

    addon = '/games/a space $(not-a-command)/Interface/AddOns'
    first = run("--yes", "--addon-dir", addon)
    check("bootstrap downloads the pinned release and runs setup", first.returncode == 0)
    check("paths and arguments survive shell quoting", json.loads(capture.read_text()) == ["setup", "--yes", "--addon-dir", addon])
    app = data / "agent-board/app"
    check("managed checkout lives at the XDG data location", (app / "bin/agent-board").exists())
    (app / "retained.txt").write_text("personal edit")
    repeated = run("--yes")
    check("rerunning setup preserves the existing checkout", repeated.returncode == 0 and (app / "retained.txt").read_text() == "personal edit")
    refused = run("--yes", extra={"TEST_ORIGIN": "https://example.com/unrelated.git"})
    check("unrelated checkout is refused without replacement", refused.returncode != 0 and (app / "retained.txt").exists())
    failed_setup = run("--yes", extra={"SETUP_EXIT": "9"})
    check("a setup failure is not reported as success", failed_setup.returncode == 9)
    check("bootstrap lock is released after failure", not (data / "agent-board/.bootstrap-lock").exists())
    failed_data = base / "failed download"
    failed_download = run("--yes", extra={"XDG_DATA_HOME": str(failed_data), "FAIL_DOWNLOAD": "1"})
    check("failed download leaves no installed app", failed_download.returncode != 0 and not (failed_data / "agent-board/app").exists())
    check("failed download cleans only its temporary checkout", not list((failed_data / "agent-board").glob(".download.*")))
    unrelated = base / "unrelated data"
    (unrelated / "agent-board/app").mkdir(parents=True)
    (unrelated / "agent-board/app/keep").write_text("keep")
    foreign = run("--yes", extra={"XDG_DATA_HOME": str(unrelated)})
    check("unmanaged directory is retained", foreign.returncode != 0 and (unrelated / "agent-board/app/keep").read_text() == "keep")
    relative = run("--yes", extra={"XDG_DATA_HOME": "relative-location"})
    check("relative XDG directory is refused", relative.returncode != 0)

if failures:
    raise SystemExit("BOOTSTRAP FAILED: " + "; ".join(failures))
print("\nBOOTSTRAP OK")
