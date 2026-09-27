#!/usr/bin/env python3
"""Runtime regressions using private sockets, fixture processes, and mocked providers."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentboard import backend, control, game, hermes_live, hypr, live, roster, t3, updater, wowclient


class Runtime(unittest.TestCase):
    def test_control_requires_a_complete_valid_confirmation(self):
        for chunks, valid in [([b''], False), ([b'junk\n'], False), ([b'[]\n'], False),
                              ([b'{"ok":true}'], False), ([b'{"ok":', b'true}\n'], True)]:
            with self.subTest(chunks=chunks), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'control.sock'
                server = socket.socket(socket.AF_UNIX)
                server.bind(str(path)); server.listen(1)
                def answer():
                    client, _ = server.accept()
                    with client:
                        client.recv(4096)
                        for chunk in chunks:
                            client.sendall(chunk)
                            time.sleep(.005)
                    server.close()
                worker = threading.Thread(target=answer)
                worker.start()
                with patch.object(control, 'socket_path', return_value=path):
                    if valid:
                        self.assertTrue(control.send('ping')['ok'])
                    else:
                        with self.assertRaises(RuntimeError): control.send('ping')
                worker.join(2)

    def test_game_identity_rejects_mentions_and_other_users(self):
        self.assertTrue(game.matches(['C:\\Games\\WowClassic.exe']))
        self.assertTrue(game.matches(['/usr/bin/wine64', 'C:\\Games\\Wow.exe']))
        self.assertFalse(game.matches(['bash', '-c', 'WowClassic.exe']))
        self.assertFalse(game.matches(['wine-guide', 'Wow.exe']))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); proc = root / '123'; proc.mkdir()
            (proc / 'cmdline').write_bytes(b'WowClassic.exe\0')
            (proc / 'stat').write_text('123 (Wow Classic) ' + ' '.join(['S'] + ['0'] * 18 + ['777']))
            self.assertEqual(game.processes(root), {123: '777'})
            with patch.object(game.os, 'getuid', return_value=os.getuid() + 1):
                self.assertEqual(game.processes(root), {})

    def test_window_target_requires_game_pid(self):
        fake = {'pid': 77, 'title': 'World of Warcraft guide', 'fullscreen': 1, 'monitor': 0}
        real = {'pid': 88, 'title': 'World of Warcraft', 'monitor': 0, 'workspace': {'id': 3}}
        with patch.object(game, 'processes', return_value={88: 'start'}), patch.object(hypr, 'clients', return_value=[fake]), patch.object(hypr, '_monitors', return_value=[{'id': 0}]):
            self.assertIsNone(hypr._game_target())
            self.assertIsNone(game.window_for([fake], {88}))
        self.assertEqual(game.window_for([fake, real], {88}), real)

    def test_detector_caches_known_process_and_has_exit_grace(self):
        tracker = game.Tracker()
        with patch.object(game, 'processes', return_value={88: 'start'}) as scan, patch.object(game, 'identity', return_value='start'), patch.object(hypr, '_instance_signature', return_value=None), patch.object(game.time, 'monotonic', return_value=10):
            self.assertTrue(tracker.sample()['running'])
            for _ in range(20): tracker.sample()
            self.assertEqual(scan.call_count, 1)
        with patch.object(game, 'identity', return_value=None), patch.object(game.time, 'monotonic', return_value=11):
            self.assertTrue(tracker.sample()['running'])
        with patch.object(game, 'identity', return_value=None), patch.object(game.time, 'monotonic', return_value=13):
            self.assertFalse(tracker.sample()['running'])

    def test_t3_only_health_does_not_start_hermes(self):
        with patch.dict(os.environ, AGENT_BOARD_PROVIDERS='t3'), patch.object(wowclient, 'board') as hermes, patch.object(t3, 'snapshot', return_value={'connected': True, 'rows': [], 'received_at': 1}):
            data = wowclient.aggregate_board()
        hermes.assert_not_called()
        self.assertNotIn('error', data)
        self.assertEqual(data['providers'], {'hermes': 'not_configured', 't3': 'ok'})
        with patch.dict(os.environ, AGENT_BOARD_PROVIDERS=''), patch.object(t3, 'snapshot') as provider:
            wowclient.aggregate_board()
            provider.assert_not_called()

    def test_one_shot_board_waits_for_initial_provider_snapshot(self):
        with patch.dict(os.environ, AGENT_BOARD_PROVIDERS='t3'), patch.object(t3, 'snapshot', return_value={}) as snapshot:
            wowclient.aggregate_board()
            snapshot.assert_called_once_with(timeout=1.0)
            snapshot.reset_mock()
            wowclient.aggregate_board(provider_timeout=0)
            snapshot.assert_called_once_with(timeout=0)

    def test_unresolved_requests_and_queue_interests_survive_limits(self):
        rows = [{'id': str(i), 'status': 'finished', 'age_s': 0, 'activity_at': i} for i in range(40)]
        rows += [{'id': 'approval', 'approval_request_id': 'req', 'status': 'needs', 'age_s': 999999},
                 {'id': 'queue', 'status': 'idle', 'age_s': 999999}]
        kept = wowclient.recent_board({'sessions': rows}, retain_ids={'queue'})['sessions']
        self.assertEqual({row['id'] for row in kept[:2]}, {'approval', 'queue'})
        self.assertEqual(len(kept), 32)

    def test_receipts_survive_focus_without_prompt_content(self):
        data = live._focus({'sessions': [{'id': 'a', 'conversation': [
            {'id': 'u', 'role': 'user', 'text': 'private prompt'}, {'id': 'a', 'role': 'agent', 'text': 'answer'}]}]}, 'other')
        row = data['sessions'][0]
        self.assertNotIn('conversation', row)
        self.assertNotIn('private prompt', json.dumps(row))
        self.assertEqual(row['message_receipts'][0]['digest'], hashlib.sha256(b'private prompt').hexdigest())

    def test_ambiguous_addon_delivery_is_not_retried(self):
        entry = {'seq': '1', 'kind': 'reply', 'provider': 'hermes', 'host': 'local', 'session_id': 'session123', 'text': 'hello'}
        with patch.object(backend, 'submit_reply', side_effect=RuntimeError('reply lost')) as submit, patch.object(backend, 'submit_reply_cli') as cli, patch.object(wowclient, '_save_state'):
            state = {'dispatched': []}
            first = wowclient.dispatch([entry], state=state)
            self.assertIn('delivery_uncertain', first[0]['outcome'])
            self.assertEqual(wowclient.dispatch([entry], state=state), [])
            self.assertEqual(submit.call_count, 1)
            cli.assert_not_called()

    def test_interrupted_dispatch_is_not_replayed(self):
        entry = {'seq': '1', 'kind': 'reply', 'provider': 'hermes', 'host': 'local', 'session_id': 'session123', 'text': 'hello'}
        state = {'dispatched': [], 'dispatching': [wowclient._entry_key(entry)]}
        with patch.object(backend, 'submit_reply') as send, patch.object(wowclient, '_save_state'):
            self.assertEqual(wowclient.dispatch([entry], state=state), [])
            send.assert_not_called()
            self.assertIn('uncertain', state['control_error'])

    def test_remote_focus_does_not_expose_local_duplicate_id(self):
        local = {'id': 'same', 'provider': 'hermes', 'host': 'local', 'conversation': [{'text': 'local'}]}
        remote = {**local, 'host': 'remote', 'conversation': [{'text': 'remote'}]}
        rows = live._focus({'sessions': [local, remote]}, json.dumps(['remote', 'hermes', 'same']))['sessions']
        self.assertNotIn('conversation', rows[0])
        self.assertEqual(rows[1]['conversation'][0]['text'], 'remote')

    def test_node_minimum_version_and_no_restart_after_stop(self):
        with patch.object(t3.subprocess, 'run', side_effect=[subprocess.CompletedProcess([], 0, '18.0.0'), subprocess.CompletedProcess([], 0, '24.1.0')]):
            self.assertIn('mise', t3._node_binary())
        provider = t3.T3Provider()
        provider._next_start = time.monotonic() + 30
        with patch.object(t3, '_node_binary') as node:
            provider.start(); node.assert_not_called()
            provider._next_start = 0
            provider.stop(); provider.start(); node.assert_not_called()

    def test_t3_only_update_arguments_preserve_optional_homes(self):
        with patch.object(updater, '_run') as run:
            updater._apply(Path('/app'), {'python': sys.executable, 'addon_dir': '/game', 'hermes_home': None, 't3_home': '/custom/t3'}, 42)
            command = run.call_args.args[0]
            self.assertTrue(all(isinstance(item, str) for item in command))
            self.assertEqual(json.loads(command[-1]), {'addon_dir': '/game', 'hermes_home': None, 't3_home': '/custom/t3'})
            self.assertEqual(run.call_args.kwargs['pass_fds'], (42,))

    def test_optional_paths_cross_a_real_setup_subprocess(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / 'agentboard'; package.mkdir()
            (package / '__init__.py').write_text('')
            (package / 'setup.py').write_text("import json\nfrom pathlib import Path\ndef configure(**kwargs):\n Path('received.json').write_text(json.dumps(kwargs))\n")
            record = {'python': sys.executable, 'addon_dir': '/game', 'hermes_home': None, 't3_home': '/custom/t3'}
            updater._apply(root, record)
            received = json.loads((root / 'received.json').read_text())
            self.assertIsNone(received['hermes_home'])
            self.assertEqual(received['t3_home'], '/custom/t3')
            self.assertTrue(received['yes'])

    def test_update_restarts_overlay_with_mode_and_visibility(self):
        status = {'ok': True, 'mode': 'board', 'visibility': 'paused'}
        with patch.object(control, 'send', side_effect=[{'ok': True}, {'ok': True, 'version': '0.2.7'}]), patch.object(control, 'is_running', return_value=False), patch.object(updater, '_run') as run:
            updater._restart_overlay(Path('/app'), status, 'v0.2.7')
            self.assertEqual(run.call_args.args[0][-2:], ['--mode', 'board'])
            self.assertEqual(run.call_args.kwargs['env']['AGENT_BOARD_VISIBILITY'], 'paused')

    def test_remote_rows_are_merged_from_cache_without_fetch(self):
        from agentboard import hosts
        cache = {'remote': {'ok': True, 'at': time.time(), 'sessions': [{'id': 'remote-thread', 'provider': 'hermes'}]}}
        with patch.object(hosts, 'load_hosts', return_value=[Mock(name='remote', enabled=True)]) as load, patch.object(hosts, 'load_cache', return_value=cache), patch.object(hosts, 'fetch_all') as fetch:
            load.return_value[0].name = 'remote'
            data = wowclient.cached_hosts({'sessions': []})
            fetch.assert_not_called()
            self.assertEqual(data['sessions'][0]['host'], 'remote')
            self.assertEqual(data['hosts']['remote'], 'ok')

    def test_stream_replay_does_not_duplicate_tokens(self):
        stream = hermes_live.Stream()
        batch = {'epoch': 1, 'latest_seq': 2, 'events': [{'seq': 1, 'type': 'message.start'}, {'seq': 2, 'type': 'message.delta', 'payload': {'text': 'Hello'}}]}
        stream.apply(batch); identity = stream.messages()[0]['id']; stream.apply(batch)
        self.assertEqual(stream.current, 'Hello')
        stream.apply({'epoch': 1, 'latest_seq': 3, 'events': [{'seq': 3, 'type': 'message.complete', 'payload': {'text': 'Hello'}}]})
        self.assertEqual(stream.messages()[0]['id'], identity)

    def test_cache_invalidates_on_store_change(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / 'state.db'; db.write_text('old')
            cache = roster.CachedBoard()
            with patch.object(roster, 'board', return_value={'sessions': []}) as read:
                for _ in range(30): cache.read(db_path=db)
                self.assertEqual(read.call_count, 1)
                db.write_text('changed')
                cache.read(db_path=db)
                self.assertEqual(read.call_count, 2)


if __name__ == '__main__': unittest.main()
