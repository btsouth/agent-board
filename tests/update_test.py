#!/usr/bin/env python3
"""Managed updates, including rollback, without network or user services."""
import os
from contextlib import nullcontext
from pathlib import Path
import sys
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentboard import updater


class Updates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "agent-board/app"
        self.root.mkdir(parents=True)
        self.manifest = dict(app_root=str(self.root), addon_dir="/game/AddOns",
                             hermes_home="/home/player/.hermes", python=sys.executable)
        self.calls = []
        self.dirty = ""
        self.origin = "https://github.com/btsouth/agent-board.git"
        self.target = "newhash"
        self.release = "v0.8.0"
        self.fake_setup = types.SimpleNamespace(load_manifest=lambda: self.manifest, lifecycle_lock=lambda: nullcontext(None))
        self.patches = [
            patch.dict(os.environ, XDG_DATA_HOME=self.tmp.name),
            patch.object(updater, "__file__", str(self.root / "agentboard/updater.py")),
            patch("agentboard.setup", self.fake_setup, create=True),
            patch.object(updater, "_git", side_effect=self.git),
            patch.object(updater, "_run", return_value="active"),
            patch.object(updater, "_releases", side_effect=lambda: [dict(tag_name=self.release, published_at="2026-09-19", draft=False)]),
            patch.object(updater, "_apply"),
        ]
        self.mocks = [p.start() for p in self.patches]
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])

    def git(self, root, *args):
        self.calls.append(args)
        if args[:2] == ("remote", "get-url"): return self.origin
        if args[0] == "status": return self.dirty
        if args == ("rev-parse", "HEAD"): return "oldhash"
        if args[0] == "rev-parse": return self.target
        if args[0] == "describe": return "v0.7.0"
        return ""

    def test_update_applies_exact_release(self):
        self.assertEqual(updater.update(), {"updated": True, "version": "v0.8.0"})
        self.assertIn(("checkout", "--detach", "v0.8.0"), self.calls)
        self.mocks[-1].assert_called_once_with(self.root, self.manifest, None)

    def test_missing_install(self):
        self.manifest = None
        with self.assertRaisesRegex(RuntimeError, "No managed"): updater.update()
        self.assertEqual(self.calls, [])

    def test_dirty_refused_before_network(self):
        self.dirty = " M personal.txt"
        with self.assertRaisesRegex(RuntimeError, "local changes"): updater.update()
        self.mocks[-2].assert_not_called()
        self.mocks[-1].assert_not_called()

    def test_unmanaged_refused(self):
        self.manifest["app_root"] = "/other/repo"
        with self.assertRaisesRegex(RuntimeError, "managed installation"): updater.update()
        self.assertEqual(self.calls, [])

    def test_wrong_origin(self):
        self.origin = "https://github.com/other/repo.git"
        with self.assertRaisesRegex(RuntimeError, "origin"): updater.update()
        self.mocks[-2].assert_not_called()

    def test_no_update(self):
        self.target = "oldhash"
        self.assertFalse(updater.update()["updated"])
        self.mocks[-1].assert_not_called()
        self.assertFalse(any(c[0] == "checkout" for c in self.calls))

    def test_no_downgrade(self):
        self.release = "v0.6.0"
        self.assertEqual(updater.update(), {"updated": False, "version": "v0.7.0"})
        self.mocks[-1].assert_not_called()

    def test_network_failure_never_checks_out(self):
        self.mocks[-2].side_effect = OSError("offline")
        with self.assertRaisesRegex(OSError, "offline"): updater.update()
        self.assertFalse(any(c[0] == "checkout" for c in self.calls))

    def test_stopped_service_refused_before_network(self):
        self.mocks[-3].side_effect = RuntimeError("inactive")
        with self.assertRaisesRegex(RuntimeError, "inactive"): updater.update()
        self.mocks[-2].assert_not_called()

    def test_stop_failure_does_not_checkout(self):
        self.mocks[-3].side_effect = ["active", RuntimeError("stop failed"), "active"]
        with self.assertRaisesRegex(RuntimeError, "source was not changed"): updater.update()
        self.assertFalse(any(c[0] == "checkout" for c in self.calls))
        self.mocks[-1].assert_not_called()
        self.assertEqual(self.mocks[-3].call_args.args[0][2], "start")

    def test_apply_inherits_verified_lifecycle_fd(self):
        with patch.object(updater, "_run") as run:
            # Invoke the actual implementation, while other tests mock install.
            self.patches[-1].stop()
            updater._apply(self.root, self.manifest, 42)
            command = run.call_args
            self.assertEqual(command.kwargs["pass_fds"], (42,))
            self.assertEqual(command.kwargs["env"]["AGENT_BOARD_LOCK_FD"], "42")

    def test_apply_failure_restores_source_and_install(self):
        self.mocks[-1].side_effect = [RuntimeError("restart failed"), None]
        with self.assertRaisesRegex(RuntimeError, "previous version was restored"): updater.update()
        self.assertEqual([c for c in self.calls if c[0] == "checkout"],
                         [("checkout", "--detach", "v0.8.0"), ("checkout", "--detach", "oldhash")])
        self.assertEqual(self.mocks[-1].call_count, 2)

    def test_failed_rollback_is_explicit(self):
        self.mocks[-1].side_effect = RuntimeError("disk full")
        with self.assertRaisesRegex(RuntimeError, "recovery also failed"): updater.update()

    def test_release_validation(self):
        self.assertEqual(updater.release_tag([
            dict(tag_name="v0.8.0", published_at="now", prerelease=True),
            dict(tag_name="v9.0.0", published_at="now", draft=True),
            dict(tag_name="--upload-pack=evil", published_at="now"),
            dict(tag_name="v0.7.0", published_at="now"),
        ]), "v0.8.0")
        with self.assertRaisesRegex(RuntimeError, "No published"): updater.release_tag([])


class GitIntegration(unittest.TestCase):
    def test_real_checkout_and_failed_apply_restore_previous_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "agent-board/app"
            root.mkdir(parents=True)
            def git(*args):
                return subprocess.check_output(["git", "-C", str(root), *args], text=True,
                                               stderr=subprocess.DEVNULL).strip()
            git("init")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Test")
            (root / "version").write_text("old")
            git("add", ".")
            git("commit", "-m", "old")
            git("tag", "v0.7.0")
            previous = git("rev-parse", "HEAD")
            (root / "version").write_text("new")
            git("commit", "-am", "new")
            git("tag", "v0.8.0")
            git("checkout", "--detach", "v0.7.0")
            git("remote", "add", "origin", "https://github.com/btsouth/agent-board.git")
            manifest = dict(app_root=str(root), addon_dir="/game/AddOns", hermes_home="/hermes", python=sys.executable)
            original_git = updater._git
            original_run = updater._run
            observed = []
            operations = []
            def local_git(repo, *args):
                operations.append(("git", *args))
                if args[0] == "fetch":
                    # Use an actual local git transport for the remote tag fetch.
                    args = ("fetch", "--no-tags", str(root), args[-1])
                return original_git(repo, *args)
            def local_run(command, **kwargs):
                if command[0] == "systemctl":
                    operations.append(tuple(command))
                    return "active"
                return original_run(command, **kwargs)
            def apply(repo, record, lock_fd):
                observed.append((repo / "version").read_text())
                if len(observed) == 1: raise RuntimeError("simulated setup failure")
            with patch.dict(os.environ, XDG_DATA_HOME=temporary), \
                 patch.object(updater, "__file__", str(root / "agentboard/updater.py")), \
                 patch("agentboard.setup", types.SimpleNamespace(load_manifest=lambda: manifest, lifecycle_lock=lambda: nullcontext(None)), create=True), \
                 patch.object(updater, "_releases", return_value=[dict(tag_name="v0.8.0", published_at="now")]), \
                 patch.object(updater, "_git", side_effect=local_git), \
                 patch.object(updater, "_run", side_effect=local_run), \
                 patch.object(updater, "_apply", side_effect=apply):
                with self.assertRaisesRegex(RuntimeError, "previous version was restored"):
                    updater.update()
            self.assertEqual(observed, ["new", "old"])
            self.assertLess(operations.index(("systemctl", "--user", "stop", "agent-board.service")),
                            operations.index(("git", "checkout", "--detach", "v0.8.0")))
            self.assertEqual(git("rev-parse", "HEAD"), previous)
            self.assertEqual(git("status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
