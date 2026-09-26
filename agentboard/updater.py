"""Update an owned installation to a published GitHub release."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from urllib.request import Request, urlopen

ORIGINS = {
    "https://github.com/btsouth/agent-board.git",
    "https://github.com/btsouth/agent-board",
    "git@github.com:btsouth/agent-board.git",
    "ssh://git@github.com/btsouth/agent-board.git",
}
RELEASES_URL = "https://api.github.com/repos/btsouth/agent-board/releases?per_page=100"
TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")


def _run(command: list[str], *, cwd: Path | None = None, env=None, pass_fds=()) -> str:
    result = subprocess.run(command, cwd=cwd, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180, pass_fds=pass_fds)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or
                           f"Command failed: {command[0]}")
    return result.stdout.strip()


def _git(root: Path, *args: str) -> str:
    return _run(["git", "-C", str(root), *args])


def _releases() -> list[dict]:
    request = Request(RELEASES_URL, headers={"Accept": "application/vnd.github+json",
                                           "User-Agent": "agent-board-updater"})
    with urlopen(request, timeout=20) as response:
        data = json.load(response)
    if not isinstance(data, list):
        raise RuntimeError("GitHub returned an invalid release list.")
    return data


def release_tag(releases: list[dict]) -> str:
    """Preview releases are intentional; drafts and arbitrary git refs are not."""
    candidates = []
    for release in releases:
        if not isinstance(release, dict) or release.get("draft") or not release.get("published_at"):
            continue
        tag = release.get("tag_name", "")
        match = TAG.fullmatch(tag) if isinstance(tag, str) else None
        if match:
            candidates.append((tuple(int(part) for part in match.groups()), tag))
    if not candidates:
        raise RuntimeError("No published versioned release is available. Nothing changed.")
    return max(candidates)[1]


def _apply(root: Path, manifest: dict, lock_fd=None) -> None:
    env = os.environ.copy()
    env["AGENT_BOARD_ROOT"] = str(root)
    if lock_fd is not None:
        env["AGENT_BOARD_LOCK_FD"] = str(lock_fd)
    # A fresh process imports the version just checked out, not this updater's
    # already imported setup implementation.
    _run([manifest["python"], "-c",
          "from agentboard.setup import configure; import sys; "
          "configure(addon_dir=sys.argv[1], hermes_home=sys.argv[2], yes=True)",
          manifest["addon_dir"], manifest["hermes_home"]], cwd=root, env=env,
         pass_fds=(lock_fd,) if lock_fd is not None else ())


def update(args=None) -> dict:
    from . import setup
    with setup.lifecycle_lock() as lock_fd:
        return _update(args, lock_fd)


def _update(args=None, lock_fd=None) -> dict:
    from . import setup
    manifest = setup.load_manifest()
    if not manifest:
        raise RuntimeError("No managed installation found. Run agent-board setup first.")
    expected = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "agent-board/app"
    root = Path(manifest["app_root"]).resolve()
    if root != expected.resolve() or Path(__file__).resolve().parent.parent != root:
        raise RuntimeError("Update only supports the managed installation. Your checkout was left untouched.")
    if _git(root, "remote", "get-url", "origin") not in ORIGINS:
        raise RuntimeError("Unexpected repository origin. Nothing changed.")
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("The managed checkout has local changes. Preserve them before updating; nothing changed.")
    try:
        _run(["systemctl", "--user", "is-active", "agent-board.service"])
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise RuntimeError("The bridge is not running. Run agent-board setup to repair it before updating. " + str(exc)) from exc
    previous = _git(root, "rev-parse", "HEAD")
    tag = release_tag(_releases())
    # Fetching a tag never changes installed files. Existing conflicting tags
    # are refused by git rather than silently replaced.
    _git(root, "fetch", "--no-tags", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
    target = _git(root, "rev-parse", f"{tag}^{{commit}}")
    if target == previous:
        return {"updated": False, "version": tag}
    # Never downgrade a newer versioned checkout, even if GitHub's release list
    # is stale. An untagged development checkout is not a managed release.
    current_tag = _git(root, "describe", "--tags", "--exact-match", "HEAD")
    current_match = TAG.fullmatch(current_tag)
    target_match = TAG.fullmatch(tag)
    if not current_match:
        raise RuntimeError("Installed checkout is not a versioned release. Nothing changed.")
    if tuple(map(int, current_match.groups())) >= tuple(map(int, target_match.groups())):
        return {"updated": False, "version": current_tag}
    try:
        _run(["systemctl", "--user", "stop", "agent-board.service"])
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        try:
            _run(["systemctl", "--user", "start", "agent-board.service"])
        except (OSError, RuntimeError, subprocess.SubprocessError) as recovery:
            raise RuntimeError(f"Could not stop the bridge; source was not changed. "
                               f"Could not restore the running bridge: {recovery}") from exc
        raise RuntimeError(f"Could not stop the bridge; source was not changed: {exc}") from exc
    try:
        _git(root, "checkout", "--detach", tag)
        _apply(root, manifest, lock_fd)
    except Exception as exc:
        try:
            _git(root, "checkout", "--detach", previous)
            _apply(root, manifest, lock_fd)
        except Exception as rollback:
            raise RuntimeError(f"Update failed: {exc}. Automatic recovery also failed: {rollback}. "
                               "Run agent-board setup to repair the installation.") from exc
        raise RuntimeError(f"Update failed; the previous version was restored: {exc}") from exc
    return {"updated": True, "version": tag}


def command(args) -> int:
    import sys
    try:
        result = update(args)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        print(f"Update failed: {exc}", file=sys.stderr)
        return 1
    if result["updated"]:
        print(f"Updated to {result['version']}. The bridge is running. Reload WoW to load the addon update.")
    else:
        print(f"Already up to date ({result['version']}).")
    return 0
