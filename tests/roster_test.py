#!/usr/bin/env python3
"""The roster engine: every status the panel can show, decided here.

This file had no coverage at all until an independent mutation pass pointed out
that five plausible edits to `roster.py` survived every gate. It is the code that
decides whether a player is interrupted: a status that reads "waiting" when the
agent is stuck, or "reply" when it is asking a question, is the worst failure this
project has, because the whole point is that the board is worth glancing at.

The fixture is a real sqlite file with the tables the queries name, so the SQL
itself is exercised rather than mocked.

Run: python3 tests/roster_test.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentboard import roster  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(("PASS  " if condition else "FAIL  ") + label + (f" ({detail})" if detail and not condition else ""))
    if not condition:
        failures.append(label)


SCHEMA = """
create table sessions (
  id text primary key, title text, source text, profile_name text, cwd text,
  git_repo_root text, started_at real, ended_at real, end_reason text,
  last_activity_at real, last_read_at real, message_count integer,
  estimated_cost_usd real, last_activity_description text,
  hidden integer default 0, archived integer default 0
);
create table session_turn_leases (
  conversation_id text, holder text, expires_at real
);
create table messages (
  id integer primary key autoincrement, session_id text, role text, content text,
  timestamp real, active integer default 1
);
create table async_delegations (
  origin_session text, dispatched_at real, completed_at real
);
"""


class Fixture:
    """A state.db with one session per case, named for what it proves."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.now = time.time()

    def build(self) -> dict[str, str]:
        conn = sqlite3.connect(self.path)
        conn.executescript(SCHEMA)

        def session(session_id, **fields):
            values = {
                "id": session_id,
                "title": fields.get("title", session_id),
                "source": "local",
                "profile_name": "default",
                "cwd": "/home/bts/Projects/ds-router",
                "git_repo_root": "/home/bts/Projects/ds-router",
                "started_at": self.now - 3600,
                "ended_at": fields.get("ended_at"),
                "end_reason": fields.get("end_reason"),
                "last_activity_at": fields.get("activity", self.now - 120),
                "last_read_at": fields.get("read", self.now - 60),
                "message_count": 4,
                "estimated_cost_usd": 0.01,
                "last_activity_description": fields.get("activity_text", ""),
                "hidden": 0,
                "archived": 0,
            }
            conn.execute(
                "insert into sessions (" + ",".join(values) + ") values ("
                + ",".join("?" * len(values)) + ")",
                tuple(values.values()),
            )

        def message(session_id, role, content, **fields):
            conn.execute(
                "insert into messages (session_id, role, content, timestamp, active) values (?,?,?,?,?)",
                (
                    session_id,
                    role,
                    content,
                    fields.get("at", self.now - 100),
                    1 if fields.get("active", True) else 0,
                ),
            )

        def lease(session_id, holder):
            conn.execute(
                "insert into session_turn_leases (conversation_id, holder, expires_at) values (?,?,?)",
                (session_id, holder, self.now + 600),
            )

        def delegation(session_id, completed=None):
            conn.execute(
                "insert into async_delegations (origin_session, dispatched_at, completed_at) values (?,?,?)",
                (session_id, self.now - 60, completed),
            )

        # An agent that asked a question and is waiting for the answer.
        session("asked", activity=self.now - 30, read=self.now - 300)
        message("asked", "assistant", "Should I ship the panel now?", at=self.now - 30)

        # An agent that asked using the word "confirm" with no question mark: a
        # marker the roster has to know about, or the board stays quiet.
        session("confirm-me", activity=self.now - 30, read=self.now - 300)
        message("confirm-me", "assistant", "Ready to confirm the migration when you are", at=self.now - 30)

        # Unread output that is not a question: a reply, not a demand.
        session("replied", activity=self.now - 30, read=self.now - 300)
        message("replied", "assistant", "Done. The tests pass.", at=self.now - 30)

        # A turn running right now (this process holds the lease).
        session("turning", activity=self.now - 5, read=self.now - 5)
        lease("turning", f"runner pid={os.getpid()}:1")
        message("turning", "assistant", "working", at=self.now - 5)

        # A lease whose holder cannot be parsed: not a live turn.
        session("broken-lease", activity=self.now - 900, read=self.now - 900)
        lease("broken-lease", "runner pid=not-a-number:1")

        # Background subagent work still running after the parent turn returned.
        session("delegating-q", activity=self.now - 400, read=self.now - 400)
        message("delegating-q", "assistant", "Dispatched three reviewers, will report", at=self.now - 400)
        delegation("delegating-q")

        # A prompt the agent has not answered, with no turn running.
        session("queued", activity=self.now - 200, read=self.now - 200)
        message("queued", "user", "now do the same for the other repo", at=self.now - 200)

        # A turn that timed out: an error, whatever else is true.
        session("timed-out", ended_at=self.now - 60, end_reason="timeout", activity=self.now - 60,
                read=self.now - 60)
        message("timed-out", "assistant", "still going", at=self.now - 60)

        # Finished cleanly with nothing unread.
        session("done", ended_at=self.now - 300, end_reason="complete", activity=self.now - 300,
                read=self.now - 300)
        message("done", "assistant", "all done", at=self.now - 300)

        # Nothing has happened in it: not started, nothing unread, not ended.
        # (A session whose newest message is the player's is "waiting" above, by
        # design: the agent has a prompt it has not answered.)
        session("untouched", activity=self.now - 600, read=self.now - 600)

        # A hidden session must not appear at all.
        session("hidden-one")
        conn.execute("update sessions set hidden = 1 where id = 'hidden-one'")

        conn.commit()
        conn.close()
        return {row["id"]: row["status"] for row in roster.board(db_path=self.path, limit=50)["sessions"]}


with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "state.db"
    statuses = Fixture(db).build()
    board = roster.board(db_path=db, limit=50)
    counts = board["counts"]

    check("every fixture session is on the board", len(statuses) >= 10, str(sorted(statuses)))
    check("a hidden session is not on the board", "hidden-one" not in statuses, str(sorted(statuses)))

    check("an unanswered question reads Needs you", statuses.get("asked") == "needs", str(statuses.get("asked")))
    check("an unanswered 'confirm' reads Needs you", statuses.get("confirm-me") == "needs",
          str(statuses.get("confirm-me")))
    check("unread output that is not a question reads New reply", statuses.get("replied") == "reply",
          str(statuses.get("replied")))
    check("a lease held by a live process reads Working", statuses.get("turning") == "working",
          str(statuses.get("turning")))
    check("a lease whose holder cannot be parsed is not a live turn",
          statuses.get("broken-lease") != "working", str(statuses.get("broken-lease")))
    check("running subagent work reads Waiting, not Needs you",
          statuses.get("delegating-q") == "waiting", str(statuses.get("delegating-q")))
    check("an unanswered prompt with no turn running reads Waiting", statuses.get("queued") == "waiting",
          str(statuses.get("queued")))
    check("a timed-out turn reads Error", statuses.get("timed-out") == "error", str(statuses.get("timed-out")))
    check("a finished session reads Finished", statuses.get("done") == "finished", str(statuses.get("done")))
    check("a session with nothing in it reads Idle", statuses.get("untouched") == "idle",
          str(statuses.get("untouched")))

    check("attention counts needs AND errors",
          board["attention"] == counts.get("needs", 0) + counts.get("error", 0),
          f"attention={board['attention']} counts={counts}")
    check("attention is not just the loud bucket", counts.get("error", 0) >= 1 and board["attention"] >= 3,
          json_counts := str(counts))

    order = [session["status"] for session in board["sessions"]]
    urgency = {"needs": 0, "error": 1, "working": 2, "waiting": 3, "reply": 4, "idle": 5, "finished": 6}
    ranks = [urgency.get(status, 9) for status in order]
    check("the board is ordered by urgency", ranks == sorted(ranks), str(order))
    check("needs outranks everything", order and order[0] == "needs", str(order[:3]))

    check("a session carries its project label",
          any(session["project"] == "ds-router" for session in board["sessions"]),
          str([session["project"] for session in board["sessions"]][:3]))

    # An empty (or missing) store must read as an error the caller can show, not
    # as an empty board.
    empty = roster.board(db_path=Path(tmp) / "nope.db", limit=5)
    check("a missing store is an error, not an empty board", bool(empty.get("error")), str(empty)[:120])
    check("...and it still returns the sessions key", empty.get("sessions") == [], str(empty)[:120])

    # A store from an older version: no such columns. Same contract.
    old_db = Path(tmp) / "old.db"
    conn = sqlite3.connect(old_db)
    conn.execute("create table sessions (id text, title text)")
    conn.execute("insert into sessions (id, title) values ('x', 'y')")
    conn.commit()
    conn.close()
    ancient = roster.board(db_path=old_db, limit=5)
    check("a store without the expected columns is an error, not a crash",
          bool(ancient.get("error")) and ancient.get("sessions") == [], str(ancient)[:140])

    # ...and so is a store that exists but cannot be opened. Opening the connection
    # sat outside the try that every other failure returns through, so this was the
    # one path that raised out of board() and took the caller with it.
    unopenable = Path(tmp) / "unopenable.db"
    unopenable.mkdir()
    try:
        opened = roster.board(db_path=unopenable, limit=5)
        raised = False
    except Exception:  # noqa: BLE001
        raised = True
        opened = {}
    check("a store that cannot be opened is an error, not a crash", not raised, str(opened)[:140])
    check("...and it answers in the same shape as the other failures",
          bool(opened.get("error")) and opened.get("sessions") == [], str(opened)[:140])

print("")
if failures:
    print("ROSTER FAILED: " + "; ".join(failures))
    raise SystemExit(1)
print("ROSTER OK")
