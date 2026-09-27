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

    def test_remote_mark_read_applies_to_the_live_board(self):
        remote = {'id': 'session123', 'host': 'box', 'status': 'reply', 'unread': True, 'activity_at': 5.5}
        local = dict(remote, host='local')
        state = {'dispatched': []}
        with patch.object(wowclient, '_save_state'):
            self.assertTrue(wowclient.dispatch_live({'kind': 'mark_read', 'host': 'box', 'session_id': 'session123', 'text': '5.5'}, state=state)['ok'])
        rows = wowclient.remote_read_marks({'sessions': [local, remote]}, state)['sessions']
        self.assertIs(rows[0], local)
        self.assertEqual((rows[1]['status'], rows[1]['unread']), ('idle', False))

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

    def test_live_poll_shares_transcripts_instead_of_copying_them(self):
        conversation = [{'id': 'm', 'role': 'agent', 'text': 'reply'}]
        t3_row = {'id': 'thread', 'provider': 't3', 'host': 'local', 'conversation': conversation}
        provider = t3.T3Provider(); provider.start = lambda: None
        provider._snapshot = {'connected': True, 'rows': [t3_row], 'received_at': time.time()}
        with patch.object(t3, '_provider', provider):
            snapshot = t3.snapshot(timeout=0)
        snapshot['connected'] = False
        snapshot['rows'][0]['status'] = 'changed'
        self.assertTrue(provider._snapshot['connected'])
        self.assertNotIn('status', t3_row)
        self.assertIs(snapshot['rows'][0]['conversation'], conversation)
        bridge = live.LiveBridge(snapshot=dict, action=dict, path=Path('/unused'))
        bridge._latest = {'sessions': [t3_row]}
        served = bridge.latest()['board']['sessions'][0]
        served['status'] = 'changed'
        self.assertNotIn('status', t3_row)
        self.assertIs(served['conversation'], conversation)
        hermes_row = {'id': 'session123', 'provider': 'hermes', 'host': 'local', 'status': 'needs', 'conversation': conversation}
        data = {'sessions': [t3_row, hermes_row]}
        enriched = hermes_live.enrich(data, [{'session_key': 'session123', 'status': 'working', 'open_requests': []}])
        self.assertEqual(hermes_row['status'], 'needs')
        self.assertEqual(enriched['sessions'][1]['status'], 'working')
        self.assertIs(enriched['sessions'][0], t3_row)
        with tempfile.TemporaryDirectory() as temp, patch.object(roster, 'board', return_value={'sessions': [dict(hermes_row, activity_at=1)]}):
            cache = roster.CachedBoard()
            first, second = cache.read(db_path=Path(temp) / 'state.db'), cache.read(db_path=Path(temp) / 'state.db')
            first['sessions'][0]['status'] = 'changed'
            self.assertEqual(second['sessions'][0]['status'], 'needs')
            self.assertIs(first['sessions'][0]['conversation'], second['sessions'][0]['conversation'])
        board = {'generated_at': 1, 'sessions': [dict(t3_row, age_s=5)]}
        key = live._change_key(board)
        self.assertEqual(key, live._change_key({'generated_at': 2, 'sessions': [dict(t3_row, age_s=9)]}))
        self.assertNotEqual(key, live._change_key({'sessions': [dict(t3_row, conversation=[{'id': 'm', 'text': 'more'}])]}))

    def test_status_files_skip_unchanged_writes(self):
        from agentboard import notify, state
        with tempfile.TemporaryDirectory() as temp:
            clock = Mock(return_value=100.0)
            status = state.StatusFile(Path(temp) / 'game-state.json', clock=clock)
            self.assertTrue(status.write({'running': False, 'at': 1}))
            clock.return_value = 110.0
            self.assertFalse(status.write({'running': False, 'at': 2}))
            self.assertTrue(status.write({'running': True, 'at': 3}))
            clock.return_value = 141.0
            self.assertTrue(status.write({'running': True, 'at': 4}))
            self.assertEqual(json.loads((Path(temp) / 'game-state.json').read_text())['at'], 4)
            ledger = Path(temp) / 'notify-state.json'
            notify.save_state({'known': {'a': 'idle'}}, ledger)
            written = ledger.stat().st_ino
            notify.save_state({'known': {'a': 'idle'}}, ledger)
            self.assertEqual(ledger.stat().st_ino, written)
            notify.save_state({'known': {'a': 'reply'}}, ledger)
            self.assertNotEqual(ledger.stat().st_ino, written)

    def test_failed_cli_turn_after_submission_is_not_resent(self):
        entry = {'seq': '7', 'kind': 'reply', 'provider': 'hermes', 'host': 'local', 'session_id': 'session123', 'text': 'continue'}
        with tempfile.TemporaryDirectory() as temp:
            fake = Path(temp) / 'hermes'
            # `hermes chat --oneshot` exits 1 when a turn fails after taking the prompt.
            fake.write_text(f'#!/bin/sh\necho started >> {temp}/launches\nexit 1\n'); fake.chmod(0o755)
            state = {'dispatched': [], 'acked_seq': 0}
            with patch.object(backend, 'find_backend', return_value=None), patch.object(backend, '_hermes_bin', return_value=str(fake)), \
                 patch.object(backend, 'hermes_home', return_value=Path(temp)), patch.object(wowclient, '_save_state'):
                first = wowclient.dispatch([entry], state=state)
                self.assertTrue(first[0]['outcome'].startswith('delivery_uncertain'))
                self.assertEqual(wowclient.dispatch([entry], state=state), [])
                self.assertEqual(state['acked_seq'], 7)
                self.assertEqual(len((Path(temp) / 'launches').read_text().splitlines()), 1)
                # A delivered reply that quotes the marker is not a refusal.
                fake.write_text('#!/bin/sh\necho "The log says hermes-refusal-reason: SESSION_NOT_OWNED"\n')
                quoted = wowclient.dispatch([dict(entry, seq='8', text='what does it mean?')], state=state)
                self.assertEqual(quoted[0]['outcome'], 'sent_via_cli')
                fake.write_text('#!/bin/sh\necho "hermes-refusal-reason: SESSION_NOT_OWNED" >&2\nexit 1\n')
                refused = wowclient.dispatch([dict(entry, seq='9')], state=state)
                self.assertIn('refused (SESSION_NOT_OWNED)', refused[0]['outcome'])
                self.assertEqual(state['acked_seq'], 8)

    def test_live_result_belongs_to_its_own_action(self):
        old = {'seq': '5', 'kind': 'reply', 'provider': 'hermes', 'host': 'local', 'session_id': 'session123', 'text': 'old'}
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / 'reply.log'; log.write_text('hermes-refusal-reason: SESSION_NOT_OWNED\n')
            state = {'dispatched': [], 'pending': {wowclient._entry_key(old): {**old, 'pid': 0, 'exit': None, 'log': str(log), 'completion': ''}}}
            with patch.object(wowclient, '_pid_running', return_value=False), patch.object(wowclient, '_save_state'), \
                 patch.object(t3, 'dispatch', return_value={'ok': True, 'message': 'Approved'}):
                result = wowclient.dispatch_live({'kind': 'approve', 'provider': 't3', 'session_id': 'thread123', 'text': 'request'}, state=state)
        self.assertTrue(result['ok'])
        self.assertEqual(state['pending'], {})

    def test_remote_reply_runs_detached_and_uncertain_exits_are_final(self):
        from agentboard import hosts, state as state_module
        entry = {'seq': '3', 'kind': 'reply', 'provider': 'hermes', 'host': 'box', 'session_id': 'session123', 'text': 'hi'}
        with tempfile.TemporaryDirectory() as temp:
            ssh = Path(temp) / 'ssh'
            ssh.write_text('#!/bin/sh\nsleep 2\nexit 255\n'); ssh.chmod(0o755)
            state = {'dispatched': [], 'acked_seq': 0}
            with patch.object(hosts, 'reply_command', return_value=[str(ssh), 'box', 'hermes chat']), \
                 patch.object(state_module, 'STATE_DIR', Path(temp)), patch.object(wowclient, '_save_state'):
                started = time.monotonic()
                live_result = wowclient.dispatch_live(dict(entry, host='box', delivery='queued'), state=state)
                self.assertLess(time.monotonic() - started, 1.9)
                self.assertTrue(live_result['ok'] and live_result['pending'])
                first = wowclient.dispatch([entry], state=state)
                self.assertEqual(first[0]['outcome'], 'sent_via_cli_pending')
                self.assertEqual(state['acked_seq'], 0)
                deadline = time.monotonic() + 5
                while any(backend.cli_exit(item['completion']) is None for item in state['pending'].values()) and time.monotonic() < deadline:
                    time.sleep(0.05)
                settled = wowclient.dispatch([entry], state=state)
                self.assertTrue(all(item['outcome'].startswith('delivery_uncertain') for item in settled))
                self.assertEqual(state['acked_seq'], 3)
                self.assertEqual(state['pending'], {})
                self.assertEqual(wowclient.dispatch([entry], state=state), [])

    def test_ledger_keeps_outbox_keys_and_leaves_out_live_actions(self):
        stuck = {'seq': '9', 'kind': 'reply', 'provider': 't3', 'host': 'local', 'session_id': 'gone-thread', 'text': 'stuck'}
        waiting = [{'seq': str(n), 'kind': 'reply', 'provider': 't3', 'host': 'local', 'session_id': 'thread123', 'text': f'reply {n}'} for n in (10, 11)]
        sent = []
        def fake(action):
            if action['session_id'] == 'gone-thread':
                return {'ok': False, 'message': 'Session is no longer available'}
            if action['kind'] == 'reply': sent.append(action['text'])
            return {'ok': True}
        state = {'dispatched': [f'{n}|old' for n in range(600)], 'acked_seq': 8}
        with patch.object(t3, 'dispatch', side_effect=fake), patch.object(wowclient, '_save_state'):
            wowclient.dispatch([stuck, *waiting], state=state)
            for _ in range(3):
                wowclient.dispatch_live({'kind': 'mark_read', 'provider': 't3', 'session_id': 'thread456', 'text': '1790000000'}, state=state)
            wowclient.dispatch([stuck, *waiting], state=state)
        self.assertEqual(sent, ['reply 10', 'reply 11'])
        self.assertEqual(len(state['dispatched']), 500)
        self.assertFalse(any('thread456' in key for key in state['dispatched']))

if __name__ == '__main__': unittest.main()
