"""Roster engine for agent-board: every Agent session as one triage row.

Read-only against the canonical session store (``$HERMES_HOME/state.db``). Two
independent signals, because neither alone is enough:

* the DB is cross-process truth — titles, projects, unread, end state, and the
  rate-limited ``last_activity_description`` the agent writes while it works;
* ``session_turn_leases`` is the one row a *running* turn must hold, so a live
  turn is visible from outside the process that owns it (that is what makes
  "Working" honest in a second window).

Status priority, highest first: needs > error > working > reply > idle/finished.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote
from typing import Any

# Bucket order is the board's sort order: what wants you first, dead work last.
STATUS_ORDER = {"needs": 0, "error": 1, "working": 2, "waiting": 3, "reply": 4, "idle": 5, "finished": 6}

STATUS_LABEL = {
    "needs": "Needs you",
    "error": "Error",
    "working": "Working",
    "waiting": "Waiting",
    "reply": "New reply",
    "idle": "Idle",
    "finished": "Finished",
}

# A lease outlives its turn by design (it is a crash-recovery window), so its
# presence alone would paint dead sessions as Working. Holder liveness is the
# cheap check that keeps that from happening.
_LEASE_PID = "pid="

_QUESTION_TAIL = 400
_QUESTION_MARKERS = (
    "which would you",
    "want me to",
    "should i",
    "do you want",
    "would you like",
    "let me know",
    "your call",
    "pick one",
    "confirm",
)


def hermes_home() -> Path:
    value = os.environ.get("HERMES_HOME")
    return Path(value) if value else Path.home() / ".hermes"


def _connect(db_path: Path) -> sqlite3.Connection:
    # Quote the path before handing it to a URI parser: a '?' or '#' in a
    # directory name would otherwise truncate the connection string.
    conn = sqlite3.connect(f"file:{quote(str(db_path))}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _pid_alive(holder: str) -> bool:
    if _LEASE_PID not in holder:
        return True
    try:
        pid = int(holder.split(_LEASE_PID, 1)[1].split(":", 1)[0])
    except (IndexError, ValueError):
        # A lease we cannot parse is a lease we cannot confirm: reporting it as
        # live would paint "Working" on a session forever.
        return False
    return Path(f"/proc/{pid}").exists()


def _live_leases(conn: sqlite3.Connection, now: float) -> dict[str, str]:
    """conversation_id -> activity description for turns running right now."""
    out: dict[str, str] = {}
    try:
        rows = conn.execute(
            "select conversation_id, holder, expires_at from session_turn_leases where expires_at > ?",
            (now,),
        ).fetchall()
    except sqlite3.Error:
        return out

    for row in rows:
        holder = str(row["holder"] or "")
        if _pid_alive(holder):
            out[str(row["conversation_id"])] = holder
    return out


def _last_speaker(conn: sqlite3.Connection, session_id: str) -> tuple[str, str]:
    """(role, text) of the newest message a human would see, or ('', '')."""
    try:
        row = conn.execute(
            """
            select role, content from messages
            where session_id = ? and role in ('assistant', 'user')
              and coalesce(content, '') <> ''
              and coalesce(active, 1) = 1
            order by timestamp desc, id desc limit 1
            """,
            (session_id,),
        ).fetchone()
    except sqlite3.Error:
        return "", ""

    if row is None:
        return "", ""
    return str(row["role"] or ""), str(row["content"] or "")


def _first_user_message(conn: sqlite3.Connection, session_id: str) -> str:
    """The session's opening ask, used as a label when it was never titled."""
    try:
        row = conn.execute(
            """
            select content from messages
            where session_id = ? and role = 'user' and coalesce(content, '') <> ''
            order by timestamp asc, id asc limit 1
            """,
            (session_id,),
        ).fetchone()
    except sqlite3.Error:
        return ""
    return str(row["content"] or "") if row else ""


def _conversation(conn: sqlite3.Connection, session_id: str, limit: int = 60) -> list[dict[str, str]]:
    """Newest visible messages in chronological order for the desktop overlay."""
    try:
        rows = conn.execute(
            """
            select id, role, content, timestamp from messages
            where session_id = ? and role in ('assistant', 'user')
              and coalesce(content, '') <> ''
              and coalesce(active, 1) = 1
            order by timestamp desc, id desc limit ?
            """,
            (session_id, max(1, limit)),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [
        {
            "id": str(row["id"]),
            "role": "user" if str(row["role"]) == "user" else "agent",
            "text": str(row["content"]),
            "created_at": str(row["timestamp"] or ""),
        }
        for row in reversed(rows)
    ]


def _snippet(text: str) -> str:
    """First line of prose in a reply, without Markdown punctuation."""
    fenced = False
    for raw in (text or "").splitlines():
        if re.match(r"^\s*(`{3,}|~{3,})", raw):
            fenced = not fenced
            continue
        if fenced or raw.lstrip().startswith("|"):
            continue
        line = re.sub(r"^\s*(#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s?)", "", raw)
        line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"[`*_]", "", line).strip()
        # Prose has words; a stray brace or rule line is not a summary.
        if re.search(r"[^\W\d_]{2,}", line):
            return line if len(line) <= 160 else line[:159] + "\u2026"
    return ""


def _asks_a_question(text: str) -> bool:
    tail = text.strip()[-_QUESTION_TAIL:]
    if not tail:
        return False
    if "?" in tail:
        return True
    lowered = tail.lower()
    return any(marker in lowered for marker in _QUESTION_MARKERS)


def _project_label(row: sqlite3.Row) -> str:
    for value in (row["git_repo_root"], row["cwd"]):
        if value:
            return Path(str(value)).name or str(value)
    return ""


def _running_delegations(conn: sqlite3.Connection, now: float, window: float = 21600.0) -> set[str]:
    """Sessions with background subagent work still running.

    This is the "waiting on the agent" signal that is not a live turn: the parent
    turn already returned, so the lease is gone while the work is not.
    """
    try:
        rows = conn.execute(
            """
            select origin_session from async_delegations
            where completed_at is null and dispatched_at > ?
            """,
            (now - window,),
        ).fetchall()
    except sqlite3.Error:
        return set()
    return {str(row["origin_session"]) for row in rows if row["origin_session"]}


def _bucket(
    *,
    live: bool,
    delegating: bool,
    queued_for_agent: bool,
    unread: bool,
    ended: bool,
    end_reason: str,
    last_role: str,
    last_text: str,
) -> str:
    if end_reason and any(token in end_reason.lower() for token in ("error", "crash", "timeout", "fail")):
        return "error"
    if live:
        return "working"
    # Waiting on the agent outranks stale unread output: the work is still moving.
    if delegating or queued_for_agent:
        return "waiting"
    if unread and last_role == "assistant":
        return "needs" if _asks_a_question(last_text) else "reply"
    if unread:
        return "reply"
    return "finished" if ended else "idle"


def board(*, limit: int = 25, days: float = 7.0, db_path: Path | None = None, include_ids=()) -> dict[str, Any]:
    """The triage board: recent sessions, statuses resolved, sorted by urgency."""
    db = db_path or (hermes_home() / "state.db")
    now = time.time()
    cutoff = now - days * 86400

    if not db.exists():
        return {"error": f"no session store at {db}", "sessions": [], "counts": {}}

    try:
        conn = _connect(db)
    except sqlite3.Error as exc:
        # A store that exists but cannot be opened - a directory in its place, a
        # file the user cannot read, a store deleted between the check above and
        # here - has to answer in the same shape as every other failure. Opening is
        # on this side of the `try` below, so it was the one path that raised out of
        # board() and took the CLI, the watcher and the remote driver with it.
        return {"error": f"cannot open the session store: {exc}", "sessions": [], "counts": {}}

    # Every path out of here closes the connection. The watcher calls this every
    # ten seconds forever, and an early return that leaked one descriptor per call
    # ran the process out of them inside an hour.
    try:
        return _board_from(conn, limit=limit, now=now, cutoff=cutoff, include_ids=include_ids)
    finally:
        conn.close()


def _board_from(conn: sqlite3.Connection, *, limit: int, now: float, cutoff: float, include_ids=()) -> dict[str, Any]:
    try:
        leases = _live_leases(conn, now)
        delegating_ids = _running_delegations(conn, now)
    except sqlite3.Error as exc:
        return {"error": f"cannot read the session store: {exc}", "sessions": [], "counts": {}}

    try:
        include_ids = tuple(dict.fromkeys((*include_ids, *leases)))
        placeholders = ','.join('?' for _ in include_ids) or 'NULL'
        rows = conn.execute(
            f"""
            select id, title, source, profile_name, cwd, git_repo_root, started_at, ended_at,
                   end_reason, last_activity_at, last_read_at, message_count,
                   estimated_cost_usd, last_activity_description
            from sessions
            where coalesce(hidden, 0) = 0 and coalesce(archived, 0) = 0
              and (coalesce(last_activity_at, started_at) > ? or id in ({placeholders}))
            order by (id in ({placeholders})) desc, coalesce(last_activity_at, started_at) desc
            limit ?
            """,
            (cutoff, *include_ids, *include_ids, max(limit * 3, 60, len(include_ids))),
        ).fetchall()
    except sqlite3.Error as exc:
        # An older or foreign store may lack a column this query names. The
        # caller gets the same error shape every other path uses, instead of an
        # exception out of board() taking the CLI, the watcher and the remote
        # driver down with it.
        return {"error": f"session store not readable: {exc}", "sessions": [], "counts": {}}

    try:
        sessions: list[dict[str, Any]] = []
        for row in rows:
            sid = str(row["id"])
            activity_at = float(row["last_activity_at"] or row["started_at"] or 0)
            read_at = float(row["last_read_at"] or 0)
            ended = bool(row["ended_at"])
            live = sid in leases
            role, text = _last_speaker(conn, sid)

            status = _bucket(
                live=live,
                delegating=sid in delegating_ids,
                # A prompt the agent has not answered yet, with no turn running:
                # queued behind the session's own work, or stranded by a dead turn.
                queued_for_agent=not live and role == "user",
                unread=activity_at > read_at,
                ended=ended,
                end_reason=str(row["end_reason"] or ""),
                last_role=role,
                last_text=text,
            )

            title = (row["title"] or "").strip()
            if not title:
                # Untitled sessions are the common case in a fast week of work;
                # showing the opening ask beats a wall of "(untitled)".
                opening = _first_user_message(conn, sid).strip().replace("\n", " ")
                title = (opening[:60] + "\u2026") if len(opening) > 60 else opening

            sessions.append(
                {
                    "id": sid,
                    "provider": "hermes",
                    "provider_label": "Hermes",
                    "title": title or "(untitled)",
                    "project": _project_label(row),
                    "source": row["source"] or "",
                    "profile": row["profile_name"] or "",
                    "status": status,
                    "status_label": STATUS_LABEL[status],
                    "unread": activity_at > read_at,
                    "age_s": max(0, int(now - activity_at)),
                    # Epoch seconds, so the bridge can tell which sessions started
                    # needing the player since their last in-game sync.
                    "activity_at": activity_at,
                    "activity": (row["last_activity_description"] or "") if live else "",
                    "messages": int(row["message_count"] or 0),
                    "cost_usd": round(float(row["estimated_cost_usd"] or 0), 4),
                    "preview": text.strip().replace("\n", " ")[:180],
                    "snippet": _snippet(text) if role == "assistant" else "",
                    "capabilities": ["reply", "focus", "mark_read", "mark_unread", "stop", "archive"],
                }
            )
        sessions.sort(key=lambda row: (row["id"] not in include_ids, STATUS_ORDER.get(row["status"], 9), row["age_s"]))
        for row in sessions[:max(limit, len(include_ids))]:
            row["conversation"] = _conversation(conn, row["id"])
    finally:
        conn.close()

    sessions.sort(key=lambda s: (STATUS_ORDER.get(s["status"], 9), s["age_s"]))

    counts: dict[str, int] = {}
    for session in sessions:
        counts[session["status"]] = counts.get(session["status"], 0) + 1

    trimmed = [row for row in sessions if "conversation" in row]
    return {
        "generated_at": now,
        "counts": counts,
        "attention": counts.get("needs", 0) + counts.get("error", 0),
        "sessions": trimmed,
    }


class CachedBoard:
    """Reuse unchanged SQLite snapshots, with a bounded refresh for lease expiry."""
    def __init__(self):
        self.key = None
        self.at = 0.0
        self.data = None

    def read(self, **kwargs):
        import copy
        db = Path(kwargs.get('db_path') or hermes_home() / 'state.db')
        stamps = []
        for path in (db, Path(str(db) + '-wal')):
            try:
                stat = path.stat()
                stamps.append((stat.st_ino, stat.st_mtime_ns, stat.st_size))
            except OSError:
                stamps.append(None)
        key = (str(db), tuple(stamps), repr(kwargs))
        now = time.monotonic()
        if self.data is None or key != self.key or now - self.at >= 2:
            self.data = board(**kwargs)
            self.key, self.at = key, now
        data = copy.deepcopy(self.data)
        for row in data.get('sessions', []):
            row['age_s'] = max(0, int(time.time() - row['activity_at']))
        return data
