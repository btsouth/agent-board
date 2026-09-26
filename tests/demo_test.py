#!/usr/bin/env python3
"""The recording demo: a scripted world that behaves like the real board."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentboard import demo


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


def rows(world):
    return {row["id"]: row for row in world.snapshot()["sessions"]}


class DemoWorldTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.world = demo.DemoWorld(clock=self.clock)

    def advance(self, seconds, step=0.5):
        end = self.clock.now + seconds
        while self.clock.now < end:
            self.clock.now += step
            self.world.snapshot()

    def test_the_board_has_every_group_in_it(self):
        board = rows(self.world)
        statuses = {row["status"] for row in board.values()}
        self.assertTrue({"working", "needs", "reply", "finished"} <= statuses)
        self.assertTrue(board["demo-billing"]["approval_request_id"])
        self.assertTrue(all(row["id"].startswith("demo-") for row in board.values()))

    def test_the_hero_agent_works_streams_and_finishes(self):
        self.advance(6)
        hero = rows(self.world)["demo-uploader"]
        self.assertTrue(any(message["role"] == "tool" for message in hero["conversation"]))
        self.advance(10)
        streaming = [m for m in rows(self.world)["demo-uploader"]["conversation"] if m.get("streaming")]
        self.assertTrue(streaming and 0 < len(streaming[0]["text"]) < len(demo.HERO_REPLY))
        self.advance(20)
        hero = rows(self.world)["demo-uploader"]
        self.assertEqual(hero["status"], "reply")
        self.assertTrue(hero["unread"])
        self.assertEqual(hero["conversation"][-1]["text"], demo.HERO_REPLY)
        self.assertFalse(any(m.get("streaming") for m in hero["conversation"]))

    def test_approving_starts_a_turn(self):
        result = self.world.action({"kind": "approve", "session_id": "demo-billing", "text": "demo-approval-1"})
        self.assertTrue(result["ok"])
        billing = rows(self.world)["demo-billing"]
        self.assertEqual(billing["status"], "working")
        self.assertFalse(billing["approval_request_id"])
        self.advance(20)
        self.assertEqual(rows(self.world)["demo-billing"]["conversation"][-1]["text"], demo.APPROVE_REPLY)

    def test_a_reply_gets_an_answer(self):
        self.world.action({"kind": "reply", "session_id": "demo-cdn", "text": "Switch it to no-cache please"})
        cdn = rows(self.world)["demo-cdn"]
        self.assertEqual(cdn["status"], "working")
        self.assertEqual(cdn["conversation"][-1]["text"], "Switch it to no-cache please")
        self.advance(15)
        self.assertEqual(rows(self.world)["demo-cdn"]["status"], "reply")

    def test_the_autoplay_story_fits_its_script(self):
        world = demo.DemoWorld(clock=self.clock, lead=demo.AUTOPLAY_LEAD, brisk=True)
        self.world = world
        self.advance(sum(delay for delay, _ in demo.AUTOPLAY[:2]))  # when autoplay moves to billing
        self.assertEqual(rows(world)["demo-uploader"]["status"], "reply")
        world.action({"kind": "approve", "session_id": "demo-billing", "text": "demo-approval-1"})
        self.advance(demo.AUTOPLAY[3][0])  # until it moves back to reply
        self.assertEqual(rows(world)["demo-billing"]["status"], "reply")
        world.action({"kind": "reply", "session_id": "demo-uploader", "text": "open the PR and ask Sam to review it"})
        self.advance(demo.AUTOPLAY[5][0] - 3)  # typing takes about three seconds of the last gap
        self.assertIn("#482", rows(world)["demo-uploader"]["conversation"][-1]["text"])

    def test_replies_answer_what_you_asked(self):
        self.world.action({"kind": "reply", "session_id": "demo-notes", "text": "Open the PR and ask Sam to review it"})
        self.advance(20)
        self.assertIn("#482", rows(self.world)["demo-notes"]["conversation"][-1]["text"])

    def test_stop_keeps_what_was_written_and_ends_the_turn(self):
        self.advance(16)
        self.world.action({"kind": "stop", "session_id": "demo-uploader"})
        hero = rows(self.world)["demo-uploader"]
        self.assertEqual(hero["status"], "reply")
        self.assertEqual(hero["activity"], "Stopped")
        self.advance(20)
        self.assertEqual(rows(self.world)["demo-uploader"]["activity"], "Stopped")

    def test_new_session_starts_working(self):
        result = self.world.action({"kind": "new", "session_id": "demo-project-docs", "text": "Tidy the README"})
        self.assertTrue(result["ok"])
        self.assertEqual(rows(self.world)[result["thread_id"]]["status"], "working")

    def test_read_state_and_archive(self):
        self.world.action({"kind": "mark_read", "session_id": "demo-notes"})
        self.assertFalse(rows(self.world)["demo-notes"]["unread"])
        self.world.action({"kind": "settle", "session_id": "demo-notes"})
        self.assertTrue(rows(self.world)["demo-notes"]["settled"])
        self.world.action({"kind": "archive", "session_id": "demo-cdn"})
        self.assertNotIn("demo-cdn", rows(self.world))


if __name__ == "__main__":
    unittest.main(verbosity=2)
