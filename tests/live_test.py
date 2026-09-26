#!/usr/bin/env python3
"""The desktop overlay's live Unix socket, without providers or Electron."""

import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentboard import live, wowclient


class LiveBridgeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.socket_path = Path(self.temp.name) / "live.sock"
        self.actions = []
        self.bridge = live.LiveBridge(
            snapshot=lambda: {"sessions": [{"id": "thread-123"}], "counts": {"working": 1}},
            action=self.action,
            interval=0.1,
            path=self.socket_path,
        )
        self.bridge.start()
        self.addCleanup(self.bridge.stop)
        self.bridge.wait_ready(timeout=2)

    def action(self, payload):
        self.actions.append(payload)
        return {"ok": True, "message": "accepted", "thread_id": "new-thread"}

    def request(self, payload):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(self.socket_path))
            client.sendall((json.dumps(payload) + "\n").encode())
            return json.loads(client.makefile("rb").readline())

    def test_state_is_served_from_the_warm_snapshot(self):
        response = self.request({"type": "state"})
        self.assertTrue(response["ok"])
        self.assertGreaterEqual(response["revision"], 1)
        self.assertEqual(response["board"]["sessions"][0]["id"], "thread-123")

    def test_known_revision_avoids_copying_the_whole_board(self):
        first = self.request({"type": "state"})
        second = self.request({"type": "state", "revision": first["revision"]})
        self.assertTrue(second["ok"])
        self.assertTrue(second["unchanged"])
        self.assertNotIn("board", second)

    def test_an_unchanged_board_keeps_its_revision(self):
        first = self.request({"type": "state"})["revision"]
        time.sleep(0.35)  # several polls of an identical snapshot
        self.assertEqual(self.request({"type": "state"})["revision"], first)

    def test_a_waiting_request_wakes_on_the_next_change(self):
        board = {"sessions": [{"id": "thread-123", "text": "Hel"}]}
        bridge = live.LiveBridge(snapshot=lambda: dict(board), action=self.action, interval=0.05,
                                 path=Path(self.temp.name) / "wait.sock")
        bridge.start()
        self.addCleanup(bridge.stop)
        bridge.wait_ready(timeout=2)
        self.socket_path = Path(self.temp.name) / "wait.sock"
        revision = self.request({"type": "state"})["revision"]
        board["sessions"] = [{"id": "thread-123", "text": "Hello"}]
        started = time.monotonic()
        response = self.request({"type": "state", "revision": revision, "wait": 1.5})
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(response["board"]["sessions"][0]["text"], "Hello")

    def test_a_waiting_request_times_out_as_unchanged(self):
        revision = self.request({"type": "state"})["revision"]
        started = time.monotonic()
        response = self.request({"type": "state", "revision": revision, "wait": 0.4})
        self.assertGreaterEqual(time.monotonic() - started, 0.35)
        self.assertTrue(response["unchanged"])

    def test_actions_go_through_the_bridge_callback(self):
        action = {"kind": "reply", "session_id": "thread-123", "text": "hello"}
        response = self.request({"type": "action", "action": action})
        self.assertTrue(response["ok"])
        self.assertEqual(response["thread_id"], "new-thread")
        self.assertEqual(self.actions, [action])

    def test_bad_requests_do_not_kill_the_socket(self):
        self.assertFalse(self.request({"type": "nonsense"})["ok"])
        self.assertTrue(self.request({"type": "ping"})["ok"])

    def test_socket_is_private_and_removed_on_stop(self):
        mode = self.socket_path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)
        self.bridge.stop()
        self.assertFalse(self.socket_path.exists())
        # Cleanup calls stop twice; it must remain harmless.
        self.bridge.stop()


class LiveDispatchTest(unittest.TestCase):
    def test_new_project_is_a_t3_live_action(self):
        state = {"dispatched": [], "acked_seq": 0, "pending": {}}
        with patch.object(wowclient.t3, "dispatch", return_value={"ok": True, "project_id": "project-1"}), \
             patch.object(wowclient, "_save_state"):
            result = wowclient.dispatch_live(
                {"kind": "new_project", "provider": "t3", "host": "local", "workspace_root": "/tmp/project", "title": "Project"},
                state=state,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["project_id"], "project-1")

    def test_recent_board_drops_old_history_but_keeps_active_work(self):
        data = {
            "sessions": [
                {"id": "old-finished", "status": "finished", "age_s": 30000, "activity_at": 1},
                {"id": "recent-finished", "status": "finished", "age_s": 300, "activity_at": 2},
                {"id": "old-needs", "status": "needs", "age_s": 90000, "activity_at": 3},
                {"id": "working", "status": "working", "age_s": 90000, "activity_at": 4},
                {"id": "idle", "status": "idle", "age_s": 10, "activity_at": 5},
            ],
            "counts": {"finished": 4},
        }
        result = wowclient.recent_board(data)
        self.assertEqual([row["id"] for row in result["sessions"]], ["working", "recent-finished"])
        self.assertEqual(result["counts"], {"working": 1, "finished": 1})
        self.assertEqual(result["total_sessions"], 5)

    def test_game_detection_uses_the_executable_not_shell_arguments(self):
        process = subprocess.Popen(["bash", "-c", "exec -a WowB.exe sleep 5"])
        def stop():
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=2)
        self.addCleanup(stop)
        deadline = time.time() + 2
        while time.time() < deadline and not wowclient.game_running():
            time.sleep(0.05)
        self.assertTrue(wowclient.game_running())

    def test_live_actions_never_advance_the_addon_ack_watermark(self):
        state = {"dispatched": [], "acked_seq": 41, "pending": {}}
        with patch.object(wowclient.t3, "dispatch", return_value={"ok": True, "thread_id": "thread-new"}), \
             patch.object(wowclient, "_save_state"):
            result = wowclient.dispatch_live(
                {"kind": "reply", "provider": "t3", "host": "local", "session_id": "thread-123", "text": "hello"},
                state=state,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(state["acked_seq"], 41)
        self.assertEqual(result["thread_id"], "thread-new")

    def test_live_actions_are_validated_before_provider_dispatch(self):
        state = {"dispatched": [], "acked_seq": 0, "pending": {}}
        with patch.object(wowclient.t3, "dispatch") as provider:
            result = wowclient.dispatch_live(
                {"kind": "reply", "provider": "t3", "session_id": "x", "text": "hello"},
                state=state,
            )
        self.assertFalse(result["ok"])
        provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
