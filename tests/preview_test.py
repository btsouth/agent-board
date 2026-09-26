#!/usr/bin/env python3
"""The panel previewer: the file that produces the store-page pictures.

It had no gate, and the cost of that showed up twice in one sitting - a refactor
moved the CSS out of its `<style>` tag so the browser drew the stylesheet as body
text, and the row's second line was still painted in one colour a week after the
addon split it into a coloured word and a muted trail. Both are visible only by
looking at a render, and both are checkable without one.

Run: python3 tests/preview_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import panel_preview  # noqa: E402

CORE = ROOT / "addon" / "AgentBoard" / "Core.lua"
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + label + (f" ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def lua_status_labels() -> dict[str, str]:
    """The addon's own status words, out of Core.lua."""
    text = CORE.read_text(encoding="utf-8")
    block = re.search(r"ns\.STATUS_LABELS = \{(.*?)\n\}", text, re.DOTALL)
    if not block:
        return {}
    return dict(re.findall(r'(\w+)\s*=\s*L\["([^"]+)"\]', block.group(1)))


page = panel_preview.render(panel_preview.demo_board(), now=1700000000.0, source="a made-up roster")
bare = panel_preview.render_bare(panel_preview.demo_board(), mode="board")

# ---- the two vocabularies have to agree -------------------------------------
# The addon's words for each status live in Core.lua; the previewer needs its own
# copy to render a roster it made up. Two copies of one vocabulary is the exact
# drift the release lint exists to catch elsewhere, so it is checked here too.
lua_labels = lua_status_labels()
check("the addon's status words were found in Core.lua", len(lua_labels) >= 7, str(lua_labels))
check("the previewer's status words match the addon's",
      panel_preview.STATUS_LABELS == lua_labels,
      f"preview {panel_preview.STATUS_LABELS} vs addon {lua_labels}")

# ---- the stylesheet is inside a style element ------------------------------
# Without this, a browser draws the CSS as text and the render looks like a
# catastrophe that no other assertion would notice.
for name, html in (("the page", page), ("the bare panel", bare)):
    check(f"{name} has balanced style tags",
          html.count("<style>") > 0 and html.count("<style>") == html.count("</style>"),
          f"{html.count('<style>')} open, {html.count('</style>')} close")
    first_rule = html.find(":root {")
    check(f"{name} puts its rules inside one", html.find("<style>") < first_rule < html.find("</style>"),
          f"style at {html.find('<style>')}, rules at {first_rule}")

# ---- the rows look like the addon's rows -----------------------------------
check("the made-up roster renders a full page of rows", page.count('class="row"') == panel_preview.ROW_COUNT,
      str(page.count('class="row"')))
check("the tab counts come from those rows", ">All 12<" in page.replace(" ", " ") or "All 12" in page,
      re.search(r"All \d+", page).group(0) if re.search(r"All \d+", page) else "no All tab")
check("attention is counted, not hardcoded", "5 need you" in page,
      re.search(r"\d+ need you", page).group(0) if re.search(r"\d+ need you", page) else "none")

# The second line is two pieces: the word in its status colour, the trail muted.
check("the row's status word is a span of its own",
      'class="meta"><span style="color:#' in page, page[page.find('class="meta"') - 20:][:90])
check("...and the trail after it is muted, not painted with the status colour",
      f'<span style="color:{panel_preview.MUTED}">' in page, panel_preview.MUTED)
check("...and the word is the addon's word, not the raw status key",
      "New reply" in page and ">reply<" not in page,
      "found 'reply' unlabelled" if ">reply<" in page else "ok")

# ---- the bare render is only the panel ------------------------------------
check("the bare render is one panel", bare.count('class="panel"') == 1, str(bare.count('class="panel"')))
check("...with no note column", 'class="note"' not in bare)
# The bare page shares the base stylesheet, so it inherits a page background and
# then overrides it. What matters is the cascade order, not the string's absence.
check("...and its page background is overridden to transparent",
      bare.find("background: transparent") > bare.find("#05070d"),
      f"transparent at {bare.find('background: transparent')}, base at {bare.find('#05070d')}")
check("the page names where it came from", "a made-up roster" in page)
check("...and never claims a live roster in demo mode", "your live roster" not in page)

# Visible states use the same controls and text roles as the client.
first_run = panel_preview.render_bare({}, mode="first-run")
empty = panel_preview.render_bare({"sessions": []}, mode="board")
working = panel_preview.render_bare(panel_preview.demo_board(), mode="working")
remote = dict(panel_preview.DEMO_SESSIONS[1], host="terra")
check("first run gives setup help and sync step", "agent-board setup" in first_run and "github.com/btsouth/agent-board" in first_run and "then press Sync" in first_run)
check("first run has no false freshness or all-clear claim", "synced never synced" not in first_run and "never synced" in first_run and ">no snapshot<" in first_run and ">all clear<" not in first_run)
check("old snapshot points to sync then setup", "Still old? agent-board setup" in panel_preview.render_bare(panel_preview.demo_board(), mode="old-snapshot"))
check("incompatible preview points to update", "agent-board update" in panel_preview.render_bare(panel_preview.demo_board(), mode="incompatible"))
check("an empty published roster is distinct from first run", "No agents yet" in empty and "Your agents, in Azeroth" not in empty)
check("detail offers mark read", 'class="button mark-read"' in page)
check("local working detail offers stop", 'class="button stop-turn"' in working)
check("nonworking and remote detail omit stop", 'class="button stop-turn"' not in page and 'class="button stop-turn"' not in panel_preview.render_detail(remote))
check("toast uses the session status color", f'color:{panel_preview.COLORS["needs"]}' in panel_preview.render_toast(panel_preview.DEMO_SESSIONS[0]))
check("text colors follow the addon palette", panel_preview.DIM == panel_preview.to_hex([.52, .55, .63]))
check("finished tab includes idle sessions", "Finished 1" in panel_preview.render_panel({"sessions": [dict(panel_preview.DEMO_SESSIONS[0], status="idle")]}, mode="board"))
check("long output is bounded above detail actions", "height: 56px; flex-shrink: 0; overflow: hidden" in page and "-webkit-line-clamp: 4" in page)
check("detail status trail is muted separately", 'class="status-trail"' in page)
check("header carries the visible sync control", 'class="button sync">Sync<' in page)
check("header and composer can shrink text without covering controls", ".badge, .synced { min-width: 0" in page and ".composer .input { min-width: 0" in page)

print("")
if failures:
    print("PREVIEW FAILED: " + "; ".join(failures))
    raise SystemExit(1)
print("PREVIEW OK")
