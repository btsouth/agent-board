# UI spec: the in-game agent board

The mockup is the spec. This records what each element means, what it is wired
to, and the few places the sandbox forces a visible difference from the mockup.

## Shape

One Blizzard-drawn window, movable, remembers where it is, with a minimap button
next to the clock. Nothing about it is an overlay: the game draws it, so it works
in full screen and nothing can sit on top of it.

```
+--------------------------------------------------------------+
| [wing] Hermes Agents          ( 3 need you )        synced 4m  - X |
+--------------------------------------------------------------+
| [search: agents, threads, projects...]                             |
| [ All ] [ Needs you 3 ] [ Working ] [ Waiting ] [ Finished ]       |
+--------------------------------------------------------------+
| o  Verify Booking App Sync and Runtime         >                  |
|    Needs you · terra / innkeeper                        now        |
| o  Fix Windows VM Installation                 >                  |
|    Error · terra / omarchy                              13d        |
| ...                                                               |
+--------------------------------------------------------------+
| o needs you  o new reply  o working  o finished  o error   [gear]  |
+--------------------------------------------------------------+
```

## Row anatomy

| Element | Source | Notes |
| --- | --- | --- |
| status dot | the session's bucket | colour plus the word in the next line, so colour is never the only signal |
| title | the Hermes session title (or its opening ask when untitled) | truncated with an ellipsis, never wrapped |
| line 2 | `status · profile / project` | profile is the Hermes profile, project is the git repo or cwd basename |
| age | seconds since last activity | `now`, `4m`, `2h`, `3d` |
| chevron | detail affordance | opens the detail pane for that session |

## Detail pane (chevron)

- session title, project, profile, session id
- last thing the agent said, or the last activity line while it is working
- messages count, tokens and estimated cost for the session
- composer: type a reply, `Enter` queues it; sending syncs so the reply leaves
  the bridge dispatches after its next poll; sync again to see the result
- `Hand off` queues a clipboard and desktop notification request
- `Mark read` queues bridge-side dismissal for this host, session and activity;
  newer activity resurfaces the session without changing Hermes database read state
- `Stop turn` appears only for local working sessions and queues an interrupt
  for an already-active desktop session. No resume or remote fallback is used.
  Interrupt clears queued prompts and denies pending approvals; delayed delivery
  can reach a later turn in the same session

## Tabs, defined against real signals

The tab names have to mean something specific, or the counts lie.

| Tab | Meaning |
| --- | --- |
| All | everything in the window, sorted by urgency then recency |
| Needs you | unread, the agent spoke last, and it is asking you something |
| Working | a turn holds a live lease right now |
| Waiting | **waiting on the agent**: background delegation is still running with no turn of its own, or a prompt the agent has not answered yet with no live turn |
| Replies | unread agent output that is not a question, one click away instead of buried in All |
| Finished | ended sessions and idle ones with nothing outstanding |

`Error` is not a tab: it is a status that pins a row to the top of `All` and
shows red wherever it appears, because an error you cannot find is worse than an
error you cannot filter.

Waiting has two real signals behind it, both read from the store: rows in
`async_delegations` with `completed_at IS NULL` (the parent turn returned, so the
lease is gone while the subagents are not), and a session whose newest visible
message is the player's with no reply and no live turn. It deliberately outranks
stale unread output, because work still moving is not the same as work waiting
for a read.

## Remote hosts

`terra` and `foundry` in the mockup are separate machines, so 1.0 merges them.
Each host is configured once (name plus how to reach it, SSH by default since
Hermes already runs there), and the bridge fetches that host's roster on the same
cadence as the local one.

Rules that keep this from becoming a liability:

- the middle line of a row reads `host / profile / project`, so a row is never
  ambiguous about where it lives
- a host that is unreachable keeps its last known rows but marks them `host
  offline`, with the age. Rows do not vanish: a host going down must not look
  like work finishing
- a slow or dead host must never stall the panel or the local publish: each host
  has a short timeout and its own cache, and the local roster is published
  whether or not the remote ones answered
- replies are routed back to the host the session came from, so answering a
  remote session from in game works the same way it does locally

## Elements the mockup does not show, and why they are there

- **`synced 4m`** in the header. The in-game board is a snapshot; a count with no
  age reads as live and would be a lie the first time it drifted.
- **`+N new`** after a sync that brought new attention items, so a refresh
  announces what changed instead of silently redrawing.
- **A first-run card** when the bridge has never run: what Hermes is, the one
  project URL, `hermes-wow setup` for an existing installation, and the instruction
  to finish setup on the computer and sync afterwards. A blank board is a bad first
  impression and a worse support thread.
- **Stale and incompatible states** rendered as such, never as an empty list.

## Minimap button

- the Hermes wing on the client's own minimap-button disc, with the attention
  count as a shadowed number on the disc when it is above zero (a square chip on
  a round button was two shapes where there is room for one)
- left click opens the board, right click syncs
- drag to reposition, remembered
- sits on the map's edge, not in it: the ring radius is measured from the minimap
  as it currently is, and re-measured when the client's UI scale or another
  minimap addon resizes the frame
- must not fight other minimap addons: it lives in its own frame parented to the
  minimap, and it hides if the minimap is hidden

## Keybinds (registered in the game's own Key Bindings panel)

| Action | Default |
| --- | --- |
| toggle the board | unbound, suggested `Alt+H` |
| sync now | unbound, suggested `Alt+S` |
| reply to the selected session | unbound, suggested `Alt+R` |

## Art

Default skin is the dark blue Hermes panel: the palette from the mockup, applied
with the client's own colour textures rather than shipped image files.

That is a deliberate reversal of the first plan (generate `bg.tga` / `edge.tga`).
Reasons, in order of weight:

- a WoW TGA is read bottom-up, and PIL writes top-down. Getting a shipped
  texture's orientation wrong is a cosmetic bug that only shows in game, i.e. the
  most expensive kind to find. Colour textures cannot be flipped.
- the panel has to look right at every UI scale (the client scales frames, and a
  4K player runs a different scale than a 1080p one). A 1px hairline in a bitmap
  becomes a blurry or missing line; a colour fill does not.
- the shape is a plain rounded rectangle with a glow edge, which the backdrop
  already draws. Nothing is lost visually.

What ships instead, all of it resolution-independent:

- `THEMES.dark` / `THEMES.classic` in `UI.lua`: panel body, edge, row, row
  alternate, highlight. `classic` is the Blizzard-native greys, and the theme is
  a toggle in the settings pane (today it repaints; a full native skin is later).
- status is colour plus words, never colour alone.
- the crest is the letter `H` in the mockup's gold, drawn as a font string, and
  the row affordance is a text `>` rather than a bitmap chevron.

Blizzard assets referenced by name (never copied): `UI-Tooltip-Background`,
`UI-Tooltip-Border`, `UIPanelButtonTemplate`, `InputBoxTemplate`,
`UI-Minimap-ZoomButton-Highlight`, `UI-Minimap-Background` (the minimap-button
disc, drawn over a themed square so a client without the art still shows a button
rather than a hole), font objects. Every addon does this.

## What is implemented

- **badge**: movable, clamped, four status dots, the count in words; click to
  open, drag to move, position saved per account. It anchors under the minimap
  cluster until the player drags it, because a fixed screen offset has to guess
  the minimap's height and guessing wrong draws it across the map. The age is not
  on the bar - it is the first thing a 230px label runs out of room for, so it
  lives in the hover tooltip alongside the dot breakdown.
- **panel**: 560x540, movable, wheel-scrolled. Header (crest, title, attention
  count, `synced` stamp with `+N new`, skin/settings/collapse/close), search box,
  six tabs with live counts, twelve 34px rows.
- **row**: status dot, title, then a second line of the status word (in its status
  colour) followed by the muted trail of `host or profile - project - activity`,
  the age, and a chevron. The word and the trail are separate labels: one colour
  across the whole line made every row shout, and the dot and the legend already
  carry the status. Tooltip on hover, detail pane on click.
- **detail pane**: title, where it lives, status, last activity, session id,
  message count, cost, preview of the last output, composer, `Send`, `Hand off`, and a separate `Mark read` / `Stop turn` row.
- **settings pane**: refresh on loading screens, sound, Sync toasts, minimap button, sync on
  send, skin, the current sync policy in plain words, and position reset.
- **minimap button**: our own round button with a live count badge; left click
  opens the panel, right click syncs, drag moves it around the minimap.
- **first-run and degraded states**: no snapshot gets a first-run card; incompatible and stale
  snapshots get an explicit line in the footer; none of them render as an
  empty board. A list with no rows in it says which nothing it is - "nothing
  matches ..." for a search, "no agents yet" for a board that has never had one -
  because a blank rectangle under a header reading "all clear" looks like a
  working board.
- **collapsed**: the title bar keeps the crest, the count, the sync stamp and the
  four controls, and everything the rows own goes with them - the search box, the
  tabs and the footer, whose `n-m of total, scroll` would otherwise be a lie while
  scrolling is disabled.
- **controls**: every button whose face is a glyph rather than a word (`X`, `-`,
  `*`) names itself on hover, because `*` is not a convention anyone knows.
- **contrast**: the three text colours are roles, not shades - `TEXT` for what you
  read, `MUTED` for the trail behind it, `DIM` for what is there if you look. `DIM`
  is held at or above 4.5:1 against the panel, which is the small-text bar, and a
  gate computes that from the palette rather than trusting the value.
- **sync toast**: a 420x36 frame near the top centre, one fitted status and title
  line in its status colour. Click opens that session. It holds for six seconds,
  fades for one second, then hides. First run is silent; transitions have a
  per-session/kind cooldown. The Sync toasts setting controls it.
- **sound**: optional cue for those transitions, independently controlled.
- **hand-off**: puts the session id on the clipboard and raises a desktop
  notification. Hermes exposes no outside API to focus a session in the desktop
  app, so this is the honest maximum; faking it would be worse than nothing.

## Not yet verified in game

The panel is written and lint-clean, and the stub suite exercises every path
(tabs, search, detail, composer, settings, minimap, collapse, scrolling), but a
browser and a Lua stub are not the client.

One configuration has been played on: **1440p, with the game's `Use UI Scale`
checkbox off** (the client's default scale for the display). Everything here is
measured rather than guessed; `fitText` measures with `GetStringWidth`, the
geometry is in pixels at that scale; so an unfamiliar UI scale is the first place
a layout defect would still be hiding, and it is the item no offline gate can
reach.

First-load items to look at with human eyes: frame strata against other addons,
text at a non-default UI scale or on a much smaller display, the minimap button
against other minimap addons (several hook the same ring), and whether the badge's
`MEDIUM` strata sits above the player frame in a raid.

## Open questions this spec does not settle

1. `Waiting`: settled. Waiting on the agent (background delegation, or a prompt
   it has not answered), outranking stale unread output.
2. Remote hosts: settled for 1.0, see the Remote hosts section.
3. Skin: settled. Dark blue default, own textures, Blizzard-native as an option.

Chat session ids are links scoped to the addon prefix; clicking one selects its
host and session without replacing the client hyperlink handler.

## Guided setup and recovery

The first-run card names the project page, `hermes-wow setup` for an existing
installation, and Sync after computer-side setup. The addon cannot install or
start a bridge from inside WoW. A snapshot older than 15 minutes says
`Snapshot is old`; Sync is the first recovery step, followed by setup if the
snapshot remains old. This is not proof that the background service is offline.
An incompatible payload points to `hermes-wow update`; an unreadable snapshot
or session store points to setup while preserving the last known rows.
