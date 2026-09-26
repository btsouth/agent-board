#!/usr/bin/env python3
"""Render the panel's ACTUAL look against live data, as HTML.

Why this exists: a WoW frame cannot be iterated on quickly (every change costs a
client restart, and the panel is drawn by the client, not by me). So the layout,
wording and density get settled in a browser first, against the real roster.

This is not a mockup. It mirrors `addon/AgentBoard/UI.lua`: the same 560x540 frame,
34px rows, twelve of them, the same palette table, the same tab labels and counts,
the same truncation, the same footer. When this and the addon disagree, the addon
is right and this file is the bug.

Run: python3 scripts/panel_preview.py [output.html]
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentboard import roster  # noqa: E402

# The palette and the status colours are READ OUT OF THE ADDON. A hand-copied hex
# is a slow-motion bug: the preview would keep looking right after the addon
# changed, which is the one thing this script exists to prevent.
ADDON = ROOT / "addon" / "AgentBoard"


def lua_tables(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def to_hex(triple: list[float]) -> str:
    return "#" + "".join(f"{int(round(value * 255)):02x}" for value in triple[:3])


def to_rgba(triple: list[float]) -> str:
    red, green, blue, alpha = (list(triple) + [1.0])[:4]
    return f"rgba({int(round(red * 255))},{int(round(green * 255))},{int(round(blue * 255))},{round(alpha, 3)})"


def parse_theme(source: str, name: str) -> dict[str, list[float]]:
    """The named theme's entries, as {key: [r, g, b, a?]}."""
    block = re.search(rf"\n  {name} = \{{(.*?)\n  \}},", source, re.S)
    if not block:
        raise SystemExit(f"panel_preview: no '{name}' theme in UI.lua: the preview would be lying")

    entries = {}
    for key, values in re.findall(r"(\w+) = \{([^}]*)\}", block.group(1)):
        numbers = [float(item) for item in values.replace(" ", "").split(",") if item]
        if numbers:
            entries[key] = numbers
    return entries


def parse_status_colors(source: str) -> dict[str, str]:
    block = re.search(r"ns\.STATUS_COLORS = \{(.*?)\n\}", source, re.S)
    if not block:
        raise SystemExit("panel_preview: no STATUS_COLORS in Core.lua: the preview would be lying")
    return {
        key: to_hex([float(item) for item in values.replace(" ", "").split(",") if item])
        for key, values in re.findall(r"(\w+) = \{([^}]*)\}", block.group(1))
    }


UI_SOURCE = lua_tables(ADDON / "UI.lua")
CORE_SOURCE = lua_tables(ADDON / "Core.lua")

THEME = parse_theme(UI_SOURCE, "dark")
COLORS = parse_status_colors(CORE_SOURCE)

PANEL_BG = to_hex(THEME["bg"])
PANEL_EDGE = to_hex(THEME["edge"])
ROW = to_rgba(THEME["row"])
ROW_ALT = to_rgba(THEME["rowAlt"])
ROW_HIGHLIGHT = to_rgba(THEME["highlight"])
BUTTON = to_hex(THEME["button"])
BUTTON_HOVER = to_hex(THEME["buttonHover"])
BUTTON_ACTIVE = to_rgba(THEME["buttonActive"])
FIELD = to_rgba(THEME["field"])

# Text roles change with contrast fixes too, so read them with the theme.
def text_color(name: str) -> str:
    match = re.search(rf"local {name} = \{{([^}}]+)\}}", UI_SOURCE)
    if not match:
        raise ValueError(f"missing addon text role {name}")
    return to_hex([float(value.strip()) for value in match[1].split(",")])


GOLD, TEXT, MUTED, DIM = (text_color(name) for name in ("GOLD", "TEXT", "MUTED", "DIM"))

TABS = [
    ("all", "All", None),
    ("needs", "Needs you", "needs"),
    ("working", "Working", "working"),
    ("waiting", "Waiting", "waiting"),
    ("reply", "Replies", "reply"),
    ("finished", "Finished", "finished"),
]

ROW_COUNT = 12
SEP = " \u00b7 "

# The words the addon puts on each status (Core.lua, STATUS_LABELS). A roster
# supplies them; the made-up one has to be told, or every row reads "reply" where
# the panel says "New reply".
STATUS_LABELS = {
    "needs": "Needs you",
    "error": "Error",
    "working": "Working",
    "waiting": "Waiting",
    "reply": "New reply",
    "idle": "Idle",
    "finished": "Finished",
}


def age_phrase(seconds) -> str:
    """"just now", not "now ago"."""
    compact = age_label(seconds)
    if compact == "now":
        return "just now"
    return compact + " ago"


def age_label(seconds) -> str:
    """UI.lua ageLabel: compact, because a row has no space for prose."""
    seconds = float(seconds or 0)
    if seconds < 90:
        return "now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def truncate(text, limit: int) -> str:
    """Cut to fit, at the last sentence end when one is close by."""
    text = "" if text is None else str(text)
    if len(text) <= limit:
        return text

    head = text[: limit - len("...")]
    boundary = re.match(r"(?s)^.*[.!?\n]\s?", head) if head else None
    if boundary and len(boundary.group(0)) >= limit // 2:
        head = boundary.group(0).rstrip()
    return head + "..."


def new_label(seconds) -> str:
    seconds = float(seconds or 0)
    if seconds < 90:
        return "just now"
    return age_label(seconds) + " ago"


def row_meta_parts(session: dict, offline: bool = False) -> tuple[str, str]:
    """The row's second line in two pieces: the status word, and the trail.

    Split because the addon draws them differently — the word in its status colour
    and the trail muted — so a preview that paints the whole line in one colour
    looks louder than the panel it is supposed to be previewing.
    """
    word = session.get("status_label") or STATUS_LABELS.get(session["status"], session["status"])

    parts = []
    trail = session["host"] if session.get("host") not in (None, "", "local") else session.get("profile")
    if trail:
        parts.append(trail)
    if session.get("project"):
        parts.append(session["project"])
    if offline:
        parts.append("host offline")
    elif session.get("activity"):
        parts.append(session["activity"])
    return word, SEP.join(parts)


def row_meta(session: dict, offline: bool = False) -> str:
    """The whole line as one string, for anything that wants it in one piece."""
    word, trail = row_meta_parts(session, offline)
    return word + SEP + trail if trail else word


def is_offline(session: dict, offline_hosts: set[str]) -> bool:
    """The addon's rule: the row's own flag, or its host being down."""
    if session.get("host_offline"):
        return True
    host = session.get("host")
    return bool(host and host != "local" and host in offline_hosts)


def render_row(session: dict, index: int, *, offline: bool = False) -> str:
    color = COLORS.get(session["status"], COLORS["idle"])
    title_color = DIM if offline else TEXT
    word, trail = row_meta_parts(session, offline)
    return f"""
        <div class="row" style="background:{ROW_ALT if index % 2 == 0 else ROW}">
          <i class="dot" style="background:{color};opacity:{0.45 if offline else 1}"></i>
          <div class="body">
            <div class="title" style="color:{title_color}">{html.escape(session['title'] or '')}</div>
            <div class="meta"><span style="color:{color}">{html.escape(word)}</span>{
                f'<span style="color:{MUTED}">{html.escape(SEP + trail)}</span>' if trail else ''}</div>
          </div>
          <div class="age">{age_label(session['age_s'])}</div>
          <div class="chev">&gt;</div>
        </div>"""


def render_detail(session: dict | None) -> str:
    """The pane behind a row: what it is, what it has done, and the reply box."""
    if not session:
        return '<div class="rows"><div class="empty">Click a row for detail, reply and hand-off.</div></div>'

    where = []
    host = session.get("host")
    where.append(host if host and host != "local" else "this machine")
    if session.get("profile"):
        where.append(session["profile"])
    if session.get("project"):
        where.append(session["project"])

    extras = []
    if session.get("messages"):
        extras.append(f"{session['messages']} messages")
    if session.get("cost_usd"):
        extras.append(f"${session['cost_usd']:.2f}")
    if session.get("activity"):
        extras.append(session["activity"])
    body = session.get("preview") or "no output in the last turn"

    facts = [
        ("session", session["id"]),
        ("machine", host if host and host != "local" else "this machine"),
        ("project", session.get("project") or "-"),
        ("profile", session.get("profile") or "-"),
        ("messages", str(session.get("messages") or 0)),
        ("cost", f"${session.get('cost_usd') or 0:.2f}"),
    ]
    fact_rows = "".join(
        f'<div class="fact"><span class="fkey">{html.escape(key)}</span>'
        f'<span class="fvalue">{html.escape(value)}</span></div>'
        for key, value in facts
    )

    color = COLORS.get(session["status"], COLORS["idle"])
    return f"""
      <div class="detail">
        <div class="dtitle">{html.escape(session['title'] or '')}</div>
        <div class="dmeta">{html.escape(' / '.join(where))}</div>
        <div class="dstatus"><span style="color:{color}">{html.escape(session.get('status_label') or STATUS_LABELS[session['status']])}</span><span class="status-trail">last activity {age_phrase(session['age_s'])}{SEP}{html.escape(session['id'])}</span></div>
        <div class="dpreview">{html.escape(truncate(body, 900))}</div>
        <div class="dstats">{html.escape(session.get("activity") or "not reporting an activity")}</div>
        <div class="dtarget">the reply goes to {html.escape(host if host and host != 'local' else 'this machine')}</div>
        <div class="facts">{fact_rows}</div>
        <div class="session-actions">{'<span class="button mark-read">Mark read</span>' if session.get('activity_at') is not None else ''}{'<span class="button stop-turn">Stop turn</span>' if session['status'] == 'working' and host in (None, '', 'local') else ''}</div>
        <div class="composer">
          <div class="input">Reply to {html.escape(session['id'][-8:])}...  <span class="caret"></span></div>
          <span class="button">Hand off</span>
          <span class="button primary">Send</span>
        </div>
        <div class="hint">Enter sends the reply and syncs. Hand off puts the session on your clipboard for the desktop app.</div>
      </div>"""


def render_first_run() -> str:
    return '<div class="first-run"><strong>Your agents, in Azeroth</strong><p>See what needs you and reply from the game.</p><code>github.com/btsouth/agent-board</code><p>Already installed? Run agent-board setup</p><p>Finish setup on your computer, then press Sync.</p></div>'


def render_toast(session: dict | None) -> str:
    if not session:
        return ""
    status = session["status"]
    return f'<div class="toast" style="color:{COLORS.get(status, DIM)}">{html.escape(STATUS_LABELS.get(status, status) + ": " + session.get("title", ""))}</div>'


def row_counts(sessions: list[dict]) -> dict:
    """The counts the ADDON shows: computed from the rows it actually received.

    The roster's own counts cover the whole three-day window, which is more than
    the payload carries. The panel tabs must count what the panel can show, or
    the preview stops matching the thing it previews.
    """
    counts: dict[str, int] = {}
    for session in sessions:
        counts[session["status"]] = counts.get(session["status"], 0) + 1
    return counts


def render_panel(data: dict, *, mode: str) -> str:
    sessions = [] if mode == "first-run" else data.get("sessions", [])
    if mode == "toast":
        return render_toast(sessions[0] if sessions else None)
    if mode == "working":
        sessions = sorted(sessions, key=lambda row: row["status"] != "working")
        mode = "detail"
    counts = row_counts(sessions)
    attention = counts.get("needs", 0) + counts.get("error", 0)
    # The roster reports when it was generated; there is no separate age field,
    # and reading one that never exists made every render say "just now".
    generated_at = time.time() - 901 if mode == "old-snapshot" else float(data.get("generated_at") or time.time())
    age = "never synced" if mode == "first-run" else age_phrase(max(0.0, time.time() - generated_at))

    headline = "no snapshot" if mode == "first-run" else None
    notice = "No snapshot: run agent-board setup on your computer." if mode == "first-run" else None
    if mode == "old-snapshot" or (mode != "first-run" and time.time() - generated_at > 900):
        headline = "snapshot is old"
        notice = "Snapshot older than 15m: Sync. Still old? agent-board setup"
    if mode == "incompatible":
        headline = "bridge version mismatch"
        notice = "payload v99 / addon v3: run agent-board update, then Sync"

    tabs = []
    for key, label, bucket in TABS:
        count = len(sessions) if bucket is None else counts.get(bucket, 0) + (counts.get("idle", 0) if bucket == "finished" else 0)
        active = " active" if key == "all" else ""
        tabs.append(f'<span class="tab{active}">{html.escape(label)} {count}</span>')

    if mode == "detail":
        body = render_detail(sessions[0]) if sessions else render_detail(None)
    else:
        offline_hosts = {name for name, state in (data.get("hosts") or {}).items() if state != "ok"}
        rows = "".join(
            render_row(session, index + 1, offline=is_offline(session, offline_hosts))
            for index, session in enumerate(sessions[:ROW_COUNT])
        )
        if mode == "first-run":
            rows = render_first_run()
        elif not rows:
            rows = '<div class="empty">No agents yet. Start a session in Agent, then sync.</div>'
        body = f'<div class="rows">{rows}</div>'

    offline_hosts = data.get("hosts_offline") or []

    trailer = ""
    if len(sessions) > ROW_COUNT:
        trailer = f"1-{ROW_COUNT} of {len(sessions)}, scroll"

    return f"""    <div class="panel">
      <div class="header">
        <span class="crest">H</span>
        <span class="name">Agent Board</span>
        <span class="badge" style="color:{COLORS['error'] if headline else COLORS['needs' if attention else 'reply']}">{headline or (('1 needs you' if attention == 1 else str(attention) + ' need you') if attention else 'all clear')}</span>
        <span class="synced">{'+' + str(data.get('new_count', 0)) + ' new, ' if data.get('new_count') else ''}{age if mode == 'first-run' else 'synced ' + age}</span>
        <span class="button sync">Sync</span><span class="button tiny">*</span><span class="button tiny">-</span><span class="button tiny">X</span>
      </div>
      <div class="search">Search agents, threads, or projects...<span class="caret"></span></div>
      <div class="tabs">{''.join(tabs)}</div>
{body}
      <div class="footer">
        <span class="legend">{notice or ('host offline: ' + ', '.join(offline_hosts) if offline_hosts else 'needs you / new reply / working / waiting / finished')}</span>
        <span style="margin-left:auto">{trailer}</span>
      </div>
    </div>"""


# A made-up roster, for anything that needs a panel picture without a panel's worth
# of somebody's real work in it: store pages, the banner, docs. Deliberately generic
# and deliberately not derived from a live store - a screenshot of this looks like
# the product and contains nothing that belongs to anyone.
DEMO_SESSIONS = [
    dict(id="20260919_101500_a1b2c3", host="local", profile="default", project="billing-service",
         status="needs", title="Review the migration plan for the billing service",
         age_s=300, messages=24, cost_usd=1.87, activity="asking which migration window to use",
         preview="Two windows would work. Saturday 02:00 UTC needs the read replicas drained first; "
                 "Sunday 03:00 UTC does not, but it overlaps the invoice run."),
    dict(id="20260919_100240_d4e5f6", host="local", profile="default", project="scheduler",
         status="working", title="Trace the flaky test in the scheduler",
         age_s=720, messages=41, cost_usd=2.40, activity="running the suite under load",
         preview="Reproduced twice in fifty runs, both times with the clock mocked forward."),
    dict(id="20260919_095512_g7h8i9", host="local", profile="default", project="docs-site",
         status="reply", title="Draft the release notes for 2.1",
         age_s=1500, messages=18, cost_usd=0.94, activity="",
         preview="First pass is up. I left the breaking change at the top and cut the two internal refactors."),
    dict(id="20260919_094108_j1k2l3", host="terra", profile="work", project="uploader",
         status="needs", title="Audit the retry logic in the uploader",
         age_s=2400, messages=33, cost_usd=1.62, activity="waiting on your answer",
         preview="Three retries back off linearly and the fourth does not back off at all. Is that intended?"),
    dict(id="20260919_093355_m4n5o6", host="local", profile="default", project="reports",
         status="needs", title="Investigate the slow query on the reports page",
         age_s=3600, messages=52, cost_usd=3.05, activity="comparing two query plans",
         preview="The index is used for the filter and ignored for the sort, so every page load sorts "
                 "the whole table."),
    dict(id="20260919_092730_p7q8r9", host="local", profile="default", project="infra",
         status="reply", title="Check the CDN cache headers for the docs site",
         age_s=5400, messages=12, cost_usd=0.31, activity="",
         preview="HTML is cached for five minutes and assets for a year, which is backwards for a site "
                 "that redeploys its assets in place."),
    dict(id="20260919_091200_s1t2u3", host="local", profile="default", project="cli",
         status="needs", title="Write the onboarding guide for the command line tool",
         age_s=7200, messages=27, cost_usd=1.20, activity="asking what to assume",
         preview="Do you want me to assume Homebrew, or cover the manual install as well?"),
    dict(id="20260919_085540_v4w5x6", host="terra", profile="work", project="search",
         status="working", title="Verify the search index rebuild",
         age_s=10800, messages=64, cost_usd=4.10, activity="re-running the rebuild against the snapshot",
         preview="First pass matched the old index row for row; the second is still running."),
    dict(id="20260919_084000_y7z8a9", host="local", profile="default", project="testing",
         status="reply", title="Trim the unused fixtures from the test suite",
         age_s=14400, messages=9, cost_usd=0.22, activity="",
         preview="Eleven fixtures are referenced by nothing. I left the two that look like they are "
                 "waiting for a feature."),
    dict(id="20260919_082115_b1c2d3", host="local", profile="default", project="billing-service",
         status="needs", title="Summarise yesterday's incident thread",
         age_s=21600, messages=38, cost_usd=1.44, activity="reading the thread",
         preview="The short version: a retry storm after a partial deploy, cleared by rolling back the "
                 "worker. Two follow-ups are still open."),
    dict(id="20260919_080000_e4f5g6", host="local", profile="default", project="docs-site",
         status="waiting", title="Collect the open questions from the design doc",
         age_s=28800, messages=15, cost_usd=0.48, activity="waiting on another agent",
         preview="Six questions, two of which the doc already answers further down."),
    dict(id="20260918_190000_h7i8j9", host="local", profile="default", project="cli",
         status="finished", title="Add shell completion to the installer",
         age_s=172800, messages=22, cost_usd=0.76, activity="",
         preview="Done and merged. Completion covers the three subcommands people actually type."),
]


def demo_board() -> dict:
    """The made-up roster in the shape `render` expects.

    `generated_at` is set relative to now rather than fixed, so the synced stamp
    reads the same on every run instead of ageing with the calendar.
    """
    return {
        "generated_at": time.time() - 120,
        "sessions": [dict(session, activity_at=time.time() - session["age_s"], status_label=STATUS_LABELS[session["status"]])
                     for session in DEMO_SESSIONS],
        "hosts": {"local": "ok", "terra": "ok"},
        "hosts_offline": [],
        "new_count": 0,
    }


def style_block() -> str:
    return f"""<style>
  :root {{ --edge: {PANEL_EDGE}; --text: {TEXT}; --muted: {MUTED}; --dim: {DIM}; --gold: {GOLD}; }}
  body {{ margin: 0; padding: 16px; background: #05070d; color: var(--text);
          font: 13px/1.3 system-ui, "Segoe UI", sans-serif; }}
  .wrap {{ display: flex; gap: 18px; align-items: flex-start; flex-wrap: wrap; }}
  .panel {{ width: 560px; height: 540px; box-sizing: border-box; display: flex; flex-direction: column;
            background: linear-gradient(180deg, #141b2e, {PANEL_BG});
            border: 1px solid var(--edge); border-radius: 4px;
            box-shadow: 0 0 18px rgba(47,107,216,.35), inset 0 0 40px rgba(47,107,216,.05); }}
  .header {{ display: flex; align-items: center; gap: 4px; padding: 9px 8px 6px 12px; }}
  .crest {{ color: var(--gold); font-weight: 700; }}
  .name {{ font-weight: 600; }}
  .badge {{ color: #ffa843; font-size: 12px; margin-left: 4px; }}
  .synced {{ margin-left: auto; color: var(--dim); font-size: 11.5px; }}
  .button {{ padding: 1px 7px; border: 1px solid rgba(148,158,185,.45); border-radius: 3px;
             color: var(--muted); font-size: 11.5px; background: {BUTTON}; }}
  .button.tiny {{ padding: 0; width: 22px; height: 18px; box-sizing: border-box; text-align: center; }}
  .button.sync {{ padding: 0; width: 48px; height: 18px; box-sizing: border-box; text-align: center; }}
  .button.primary {{ border-color: var(--edge); color: var(--text); background: rgba(47,107,216,.28); }}
  .search {{ margin: 2px 12px 0; padding: 4px 8px; border: 1px solid rgba(148,158,185,.35); border-radius: 3px;
             background: {FIELD}; color: var(--dim); font-size: 12px; }}
  .caret {{ display: inline-block; width: 1px; height: 11px; background: var(--muted); vertical-align: -1px; }}
  .tabs {{ display: flex; gap: 4px; padding: 8px 12px; }}
  .tab {{ padding: 2px 8px; border: 1px solid rgba(148,158,185,.35); border-radius: 3px;
          background: {BUTTON}; color: var(--muted); font-size: 11.5px; }}
  .tab.active {{ background: rgba(47,107,216,.35); border-color: var(--edge); color: var(--text); }}
  .rows {{ padding: 0 10px; flex: 1; overflow: hidden; }}
  .row {{ display: flex; align-items: center; gap: 8px; height: 34px; padding: 0 8px 0 6px; }}
  .dot {{ width: 8px; height: 8px; border-radius: 50%; flex: 0 0 8px; }}
  .body {{ flex: 1; min-width: 0; }}
  /* The addon measures text with GetStringWidth and trims to fit; the browser's
     equivalent is a fixed box and an ellipsis. Same intent, same widths. */
  .body {{ flex: 1; min-width: 0; }}
  .title {{ font-weight: 600; font-size: 12.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .meta {{ font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .age {{ color: var(--dim); font-size: 11px; }}
  .chev {{ color: var(--muted); }}
  .empty {{ padding: 40px 12px; color: var(--dim); font-size: 12px; text-align: center; }}
  .detail {{ margin: 0 10px; padding: 12px; flex: 1; display: flex; flex-direction: column;
             background: rgba(20,27,45,.97); border: 1px solid rgba(47,107,216,.35); border-radius: 3px; }}
  .dtitle {{ font-weight: 600; font-size: 14px; }}
  .dmeta {{ color: var(--muted); font-size: 11.5px; margin-top: 2px; }}
  .dstatus {{ font-size: 11.5px; margin-top: 8px; }}
  .dpreview {{ height: 56px; flex-shrink: 0; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; color: var(--text); font-size: 12px; margin-top: 10px; line-height: 1.45; }}
  .dstats {{ color: var(--muted); font-size: 11.5px; margin-top: 12px; }}
  .dtarget {{ color: var(--dim); font-size: 11.5px; margin-top: 6px; }}
  /* The pane is taller than one paragraph needs, so the facts the player would
     otherwise have to guess at live in the space instead of in a hole. */
  .facts {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px 18px; margin-top: 14px; }}
  .fact {{ display: flex; gap: 8px; font-size: 11.5px; }}
  .fkey {{ color: var(--dim); min-width: 62px; }}
  .fvalue {{ color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .composer {{ display: flex; align-items: center; gap: 6px; margin-top: auto; }}
  .input {{ flex: 1; padding: 3px 8px; border: 1px solid rgba(47,107,216,.35); border-radius: 3px;
            background: rgba(0,0,0,.35); color: var(--dim); font-size: 12px; }}
  .hint {{ color: var(--dim); font-size: 11px; margin-top: 10px; }}
  .footer {{ display: flex; padding: 5px 12px 8px; color: var(--dim); font-size: 11px; }}
  .panel {{ position: relative; }}
  .header {{ height: 34px; box-sizing: border-box; }}
  .name, .button {{ flex-shrink: 0; }}
  .badge, .synced {{ min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .badge {{ flex: 1; }}
  .synced {{ max-width: 130px; }}
  .search {{ position: absolute; top: 39px; left: 0; right: 0; height: 22px; box-sizing: border-box; }}
  .tabs {{ position: absolute; top: 66px; left: 0; right: 0; padding-top: 0; padding-bottom: 0; }}
  .rows {{ position: absolute; top: 95px; left: 0; right: 0; bottom: 28px; }}
  .footer {{ position: absolute; bottom: 0; left: 0; right: 0; gap: 24px; }}
  .legend {{ flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .footer > :last-child {{ max-width: 230px; flex-shrink: 0; }}
  .detail {{ position: absolute; top: 95px; left: 0; right: 0; bottom: 27px; box-sizing: border-box; overflow: hidden; }}
  .dtitle, .dmeta, .dstats, .dtarget {{ overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }}
  .dstatus {{ display: flex; gap: 8px; }}
  .dstatus > :first-child {{ flex-shrink: 0; }}
  .status-trail {{ color: var(--muted); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }}
  .session-actions {{ position: absolute; bottom: 78px; left: 12px; display: flex; gap: 8px; }}
  .session-actions .button {{ width: 90px; height: 22px; box-sizing: border-box; text-align: center; }}
  .composer {{ position: absolute; bottom: 44px; left: 12px; right: 12px; }}
  .composer .input {{ min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }}
  .composer .button {{ width: 118px; height: 22px; box-sizing: border-box; text-align: center; }}
  .composer .primary {{ width: 72px; }}
  .hint {{ position: absolute; bottom: 22px; left: 12px; right: 12px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }}
  .first-run {{ margin: 54px 29px 0; width: 480px; text-align: left; color: var(--text); }}
  .first-run strong {{ display: block; height: 32px; font-size: 14px; color: var(--text); }}
  .first-run p {{ margin: 0; height: 32px; }}
  .first-run code {{ display: block; height: 32px; font: inherit; }}
  .toast {{ width: 420px; height: 36px; box-sizing: border-box; padding: 8px 12px; background: {PANEL_BG}; border: 1px solid var(--edge); border-radius: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .note {{ width: 380px; color: var(--muted); font-size: 12px; }}
  .note h3 {{ color: var(--text); font-size: 12.5px; margin: 0 0 6px; }}
  .note li {{ margin-bottom: 5px; }}
  code {{ color: var(--text); }}
</style>"""


def render_bare(data: dict, *, mode: str = "board") -> str:
    """One panel and nothing else, at 1:1 on a transparent page.

    For rasterising: no body padding, no note column, no page background, so a
    headless screenshot of it is the panel itself with its own glow and no context
    to crop off. See `make_banner.py`.
    """
    return f"""<!doctype html>
<html><head><meta charset="utf-8">{style_block()}
<style>
  /* 30px of slack on every side so the panel's own box-shadow is inside the shot. */
  body {{ margin: 0; padding: 30px; background: transparent; }}
  .panel {{ box-shadow: 0 0 26px rgba(47,107,216,.45); }}
</style></head>
<body>
{render_panel(data, mode=mode)}
</body></html>
"""


def render(data: dict, *, now: float, source: str = "your live roster") -> str:
    sessions = data.get("sessions", [])
    generated = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    attention = data.get("attention", 0)

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>AgentBoard panel, as the addon draws it</title>
{style_block()}
</head>
<body>
  <div class="wrap">
{render_panel(data, mode="board")}
{render_panel(data, mode="detail")}
    <div class="note">
      <h3>Rendered from {source}</h3>
      <ul>
        <li>{len(sessions)} sessions, {attention} needing you, generated {generated}.</li>
        <li>Left: the board. Right: the detail pane that opens when you click a row (composer focused, ready to type).</li>
        <li>Same numbers the addon computes: tab counts, the synced stamp, per-row age, host and project.</li>
        <li>Not pictured: the movable badge, minimap button and settings pane. Use --only first-run or --only toast for onboarding and notifications.</li>
        <li>Fonts and chrome are the client's in game, so text is a little tighter than this.</li>
      </ul>
    </div>
  </div>
</body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", nargs="?",
                        default=str(ROOT / "docs" / "panel-preview.html"),
                        help="where to write the html")
    parser.add_argument("--demo", action="store_true",
                        help="the built-in made-up roster, for pictures that should not "
                             "contain anyone's real work")
    parser.add_argument("--only", choices=("board", "detail", "first-run", "toast", "working", "old-snapshot", "incompatible"),
                        help="one bare panel on a transparent page, for rasterising")
    args = parser.parse_args()

    output = Path(args.output)
    source = "a made-up roster"

    if args.demo:
        data = demo_board()
    else:
        data = roster.board(limit=15, days=3)
        # Present the panel the way the player will most often see it: something
        # wanting a decision, plus whatever is running.
        data["new_count"] = 0
        data["hosts_offline"] = []
        source = "your live roster"

    page = render_bare(data, mode=args.only) if args.only else render(data, now=time.time(), source=source)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")
    print(json.dumps({
        "path": str(output),
        "sessions": len(data.get("sessions", [])),
        "attention": data.get("attention", 0),
        "source": source,
        "only": args.only,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
