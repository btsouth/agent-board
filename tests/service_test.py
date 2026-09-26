#!/usr/bin/env python3
"""A background bridge proves health without exposing sessions or racing itself."""
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentboard import wowclient


class Service(unittest.TestCase):
    def test_singleton_refuses_another_process(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'wow-inbox.json'
            lock = state.with_name('watch.lock')
            with lock.open('a+') as stream:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                script = ('from pathlib import Path; from agentboard import wowclient; '
                          'import sys; wowclient._STATE_PATH=Path(sys.argv[1]); '
                          'wowclient._watch=lambda **kw: [{"unwanted": True}]; '
                          'print(wowclient.watch(once=True))')
                child = subprocess.run([sys.executable, '-c', script, str(state)], text=True, capture_output=True)
                self.assertEqual(child.returncode, 0, child.stderr)
                self.assertIn('already running', child.stdout)
                self.assertNotIn('unwanted', child.stdout)
            with patch.object(wowclient, '_STATE_PATH', state), patch.object(wowclient, '_watch', return_value=[{'ran': True}]):
                self.assertEqual(wowclient.watch(once=True), [{'ran': True}])

    def test_health_precedes_slow_dispatch_and_contains_no_content(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            health = base / 'service-health.json'
            snapshot = {'sessions': [{'id': 'private-session', 'preview': 'private prompt'}]}
            def dispatch(*a, **kw):
                self.assertTrue(health.is_file(), 'startup handshake must not wait for delivery')
                self.assertTrue(json.loads(health.read_text())['ok'])
                return []
            with patch.object(wowclient, '_STATE_PATH', base / 'state.json'), patch.object(wowclient, 'board', return_value=snapshot), patch.object(wowclient, 'publish', return_value={'rows': snapshot['sessions']}), patch.object(wowclient, 'savedvars_path', return_value=base/'saved.lua'), patch.object(wowclient, 'read_outbox', return_value=[{}]), patch.object(wowclient, 'dispatch', side_effect=dispatch):
                wowclient.watch(addon_dir=base, once=True, hosts_enabled=False, notify_enabled=False, health_file=health)
            value = json.loads(health.read_text())
            self.assertEqual(set(value), {'ok', 'at', 'problems'})
            self.assertTrue(value['ok'])
            self.assertNotIn('private', health.read_text())

    def test_failed_publish_never_reports_healthy(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            health = base / 'service-health.json'
            with patch.object(wowclient, '_STATE_PATH', base / 'state.json'), patch.object(wowclient, 'board', return_value={'sessions': []}), patch.object(wowclient, 'publish', side_effect=OSError('read only')), patch.object(wowclient, 'savedvars_path', return_value=None):
                wowclient.watch(addon_dir=base, once=True, hosts_enabled=False, notify_enabled=False, health_file=health)
            self.assertFalse(json.loads(health.read_text())['ok'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
