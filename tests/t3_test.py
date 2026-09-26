#!/usr/bin/env python3
"""T3 provider boundary checks that do not need a running T3 Code server."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentboard import t3  # noqa: E402


class T3Focus(unittest.TestCase):
    def test_focus_reuses_a_running_overlay(self):
        provider = t3.T3Provider()
        with patch.object(t3.control, "is_running", return_value=True), \
             patch.object(t3.control, "send", return_value={"ok": True}) as send, \
             patch.object(provider, "start") as start:
            result = provider.dispatch({"kind": "focus", "session_id": "thread-123"})

        self.assertTrue(result["ok"])
        send.assert_called_once_with("focus:thread-123")
        start.assert_not_called()

    def test_focus_starts_the_overlay_when_needed(self):
        provider = t3.T3Provider()
        with patch.object(t3.control, "is_running", return_value=False), \
             patch.object(t3.control, "wait_until_up", return_value=True), \
             patch.object(t3.control, "send", return_value={"ok": True}) as send, \
             patch.object(t3.subprocess, "Popen") as popen, \
             patch("agentboard.wowclient.session_env", return_value={"DISPLAY": ":0"}):
            result = provider.dispatch({"kind": "focus", "session_id": "thread-456"})

        self.assertTrue(result["ok"])
        command = popen.call_args.args[0]
        self.assertEqual(command[-3:], ["overlay", "--mode", "board"])
        send.assert_called_once_with("focus:thread-456")

    def test_focus_rejects_a_missing_session(self):
        result = t3.T3Provider().dispatch({"kind": "focus", "session_id": ""})
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
