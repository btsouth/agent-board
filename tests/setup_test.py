#!/usr/bin/env python3
"""Offline setup rollback, ownership and service tests."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentboard import setup


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.target = self.base / 'World of Warcraft' / 'Interface' / 'AddOns'
        self.target.mkdir(parents=True)
        self.home = self.base / 'Agent % home'
        self.home.mkdir()
        (self.home / 'state.db').write_text('')
        self.t3home = self.base / 't3'
        self.t3home.mkdir()
        self.unit = self.base / 'user/agent-board.service'
        self.manifest = self.base / 'managed/install.json'
        self.launcher = self.base / 'bin/agent-board'
        for key, value in dict(BASE=self.manifest.parent, MANIFEST=self.manifest, UNIT=self.unit, LAUNCHER=self.launcher).items():
            mock = patch.object(setup, key, value)
            mock.start()
            self.addCleanup(mock.stop)
        self.calls = []
        self.active = False
        self.enabled = False
        self.fail = None
        self.health_ok = True

        def ctl(*args, check=True):
            self.calls.append(args)
            if args == self.fail and check:
                raise RuntimeError('synthetic service failure')
            code = 0
            if args[0] == 'is-active':
                code = 0 if self.active else 3
            if args[0] == 'is-enabled':
                code = 0 if self.enabled else 1
            if args[0] in ('restart', 'start'):
                self.active = True
                (self.manifest.parent / 'service-health.json').write_text(json.dumps({'ok': self.health_ok, 'at': setup.time.time()}))
            if args[0] == 'stop' or args[0] == 'disable' and '--now' in args:
                self.active = False
            if args[0] == 'enable':
                self.enabled = True
            if args[0] == 'disable':
                self.enabled = False
            if code and check:
                raise RuntimeError('service not active')
            return subprocess.CompletedProcess(args, code, '', '')

        for mock in (patch.object(setup, 'systemctl', side_effect=ctl), patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)), patch.object(setup.shutil, 'which', return_value='/usr/bin/systemctl'), patch.object(setup.roster, 'board', return_value={'sessions': [], 'counts': {}}), patch.object(setup.wowclient, 'aggregate_board', return_value={'sessions': [], 'counts': {}, 'providers': {'hermes': 'ok', 't3': 'offline'}, 'projects': []}), patch.object(setup.wowclient, 'publish', return_value={}), patch.object(setup.time, 'sleep')):
            mock.start()
            self.addCleanup(mock.stop)

    def install(self, **kwargs):
        return setup.configure(addon_dir=self.target, hermes_home=self.home, t3_home=self.t3home, yes=True, **kwargs)

    def test_install_idempotent(self):
        first = self.install()
        self.assertTrue((self.target / 'AgentBoard/AgentBoard.toc').is_file())
        self.assertTrue(self.active and self.enabled)
        self.assertIn(('is-active', 'agent-board.service'), self.calls)
        self.assertIn('--quiet', self.unit.read_text())
        self.assertIn('Agent %% home', self.unit.read_text())
        self.assertEqual(setup.load_manifest()['addon_dir'], str(self.target))
        self.install()
        self.assertEqual(first['app_root'], setup.load_manifest()['app_root'])

    def test_ambiguity_fails_without_guess(self):
        with patch.object(setup.wowclient, 'find_addon_dirs', return_value=[self.target, self.target.parent]):
            with self.assertRaisesRegex(RuntimeError, 'Multiple WoW'):
                setup.choose_addon(yes=True)
        self.assertFalse(self.manifest.exists())

    def test_missing_dependency_no_mutations(self):
        with patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaisesRegex(RuntimeError, 'dependency'):
                self.install()
        self.assertFalse(self.unit.exists())
        self.assertFalse((self.target / 'AgentBoard').exists())

    def test_bad_store_no_mutations(self):
        with patch.object(setup.roster, 'board', return_value={'error': 'no database'}):
            with self.assertRaisesRegex(RuntimeError, 'session store'):
                self.install()
        self.assertFalse(self.unit.exists())

    def test_service_failure_rolls_back_first_install(self):
        self.fail = ('restart', 'agent-board.service')
        with self.assertRaisesRegex(RuntimeError, 'synthetic'):
            self.install()
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.unit.exists())
        self.assertFalse(self.launcher.exists())
        self.assertFalse((self.target / 'AgentBoard').exists())
        self.assertFalse(self.enabled)

    def test_unmanaged_addon_requires_force_and_backup(self):
        dest = self.target / 'AgentBoard'
        dest.mkdir()
        (dest / 'custom.lua').write_text('personal')
        with self.assertRaisesRegex(RuntimeError, 'unmanaged'):
            self.install()
        result = self.install(force=True)
        self.assertEqual((Path(result['backup']) / 'custom.lua').read_text(), 'personal')

    def test_existing_install_rollback(self):
        self.install()
        old = self.manifest.read_bytes()
        toc = self.target / 'AgentBoard/AgentBoard.toc'
        toc.write_text('old addon')
        self.fail = ('restart', 'agent-board.service')
        with self.assertRaises(RuntimeError):
            self.install()
        self.assertEqual(toc.read_text(), 'old addon')
        self.assertEqual(self.manifest.read_bytes(), old)
        self.assertTrue(self.active)

    def test_uninstall_preserves_data_and_code(self):
        self.install()
        sentinel = self.home / 'state.db'
        sentinel.write_text('untouched')
        setup.uninstall()
        self.assertEqual(sentinel.read_text(), 'untouched')
        self.assertTrue((self.target / 'AgentBoard').exists())
        self.assertFalse(self.unit.exists())
        self.assertFalse(self.launcher.exists())
        self.assertFalse(self.active)
        self.assertFalse(self.enabled)
        self.assertFalse(setup.uninstall())

    def test_uninstall_refuses_foreign_unit(self):
        self.install()
        self.unit.write_text('foreign unit')
        with self.assertRaisesRegex(RuntimeError, 'unmanaged'):
            setup.uninstall()
        self.assertEqual(self.unit.read_text(), 'foreign unit')

    def test_symlink_rejected(self):
        dest = self.target / 'AgentBoard'
        dest.mkdir()
        sentinel = self.base / 'sentinel'
        sentinel.write_text('untouched')
        (dest / 'AgentBoard.lua').symlink_to(sentinel)
        with self.assertRaisesRegex(RuntimeError, 'linked addon'):
            self.install(force=True)
        self.assertEqual(sentinel.read_text(), 'untouched')

    def test_unmanaged_launcher_preserved(self):
        self.launcher.parent.mkdir(parents=True)
        self.launcher.write_text('personal launcher')
        with self.assertRaisesRegex(RuntimeError, 'unmanaged'):
            self.install()
        self.assertEqual(self.launcher.read_text(), 'personal launcher')
        self.assertFalse(self.manifest.exists())

    def test_missing_systemd_no_mutations(self):
        with patch.object(setup.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'systemctl is missing'):
                self.install()
        self.assertFalse(self.unit.exists())

    def test_failed_first_poll_rolls_back(self):
        self.health_ok = False
        with self.assertRaisesRegex(RuntimeError, 'first snapshot'):
            self.install()
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.enabled)

    def test_status_checks_heartbeat(self):
        self.install()
        with patch('builtins.print'):
            self.assertEqual(setup.status(), 0)
            (self.manifest.parent / 'service-health.json').write_text(json.dumps({'ok': True, 'at': 1}))
            self.assertEqual(setup.status(), 1)

    def test_lock_excludes_parallel_setup(self):
        with setup.lifecycle_lock():
            with self.assertRaisesRegex(RuntimeError, 'Another setup'):
                self.install()

    def test_inherited_lock_allows_child_and_preserves_error(self):
        with setup.lifecycle_lock() as descriptor:
            with patch.dict(setup.os.environ, {'AGENT_BOARD_LOCK_FD': str(descriptor)}):
                with setup.lifecycle_lock() as inherited:
                    self.assertEqual(descriptor, inherited)
                with self.assertRaisesRegex(OSError, 'original error'):
                    with setup.lifecycle_lock():
                        raise OSError('original error')

    def test_foreign_service_fragment_prevents_stop(self):
        previous = setup.systemctl.side_effect
        def ctl(*args, **kwargs):
            if args[0] == 'show':
                return subprocess.CompletedProcess(args, 0, '/usr/lib/systemd/user/agent-board.service', '')
            return previous(*args, **kwargs)
        setup.systemctl.side_effect = ctl
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            self.install()
        self.assertNotIn(('stop', 'agent-board.service'), self.calls)

    def test_systemd_quoting(self):
        self.assertEqual(setup.quote('/path with %/$/"'), '"/path with %%/$$/\\""')

    def test_inactive_verification_fails(self):
        self.fail = ('is-active', 'agent-board.service')
        with self.assertRaises(RuntimeError):
            self.install()
        self.assertFalse(self.manifest.exists())


if __name__ == '__main__':
    unittest.main()
