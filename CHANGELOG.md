# Changelog

Notable changes to Agent Board: one WoW addon and one bridge for Hermes and T3 Code.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the
addon and the bridge version independently and the payload is versioned by
`schema`.

## [0.2.4] - 2026-09-26

### Added

- `agent-board demo --autoplay` drives the board by itself for a hands-free
  recording: it opens the board as the first agent starts, approves a waiting
  session with a visible button press, types a reply into the composer and sends
  it, then collapses to the badge. It only works while a demo is running.

## [0.2.3] - 2026-09-26

### Added

- `agent-board demo` shows a scripted, fictional board in the running overlay
  for screenshots and recordings: an agent works through tool steps and streams
  a reply, another waits for an approval, and replies, approvals, stops and new
  sessions get answers. Your real sessions are never shown while it runs, and
  Ctrl+C or `agent-board demo --stop` switches back. `--speed` plays it faster
  or slower.

### Fixed

- A session you are watching when its reply lands counts as read.
- Settle is not offered while a thread is waiting for your approval.
- The streaming caret stays at the end of a code block instead of below it.

## [0.2.2] - 2026-09-26

### Changed

- Needs you holds only real requests: an approval or a question the agent is
  blocked on. Failures and questions in a finished reply no longer land there.
- Running comes next, then Just finished: anything that ended in the last half
  hour, and unread replies or failures from the last three hours.
- Everything else moves to Earlier, dimmed, including unread news that has gone
  stale. It can still be marked read from the right-click menu.
- The badge counts recently finished work ("2 running · 3 finished").

## [0.2.1] - 2026-09-26

### Added

- Right-click a session (or press the Menu key or Shift+F10 on it) for Open,
  Mark read or Mark unread, Settle or Archive, Stop turn, Copy title and Copy
  session ID. T3 Code threads also offer Archive in T3 Code, which asks for a
  second click.
- Mark unread for T3 Code threads and Hermes sessions. A T3 thread you mark
  unread stays unread until you read it again, even if it is settled.

## [0.2.0] - 2026-09-26

### Added

- The board groups sessions by what they need: Needs you, Running, New replies
  and Seen. Every row says its state in words and unread rows show the first
  line of the reply.
- Agent replies render Markdown: code blocks with copy, inline code, lists,
  tables, quotes and links. Links open in the browser.
- Tool calls appear between messages as one expandable line per run of steps
  (commands run, files edited, searches).
- Replies stream in place. Hermes replies appear while they are written instead
  of after each tool round, and the overlay receives changes as they happen.
- Settle and Unsettle for T3 Code threads and Archive for Hermes sessions, from
  the board or with `E`. Mark read on a Hermes session also marks it read in
  Hermes.
- Opening a session with a new reply marks it read.
- A "Jump to latest" button appears when new text arrives while you are reading
  further up.
- The badge shows new replies next to running work and pulses when something
  new needs you. It can be dragged by its grip and sizes itself to its text.
- The board opens at 980x720 and remembers the size you give it.

### Changed

- A T3 thread you settled in T3 Code no longer counts as unread here, and a
  failure you have already seen moves to Seen instead of staying in Needs you.
- Failed sessions show the reason they failed.
- Only the open conversation is sent to the overlay, and a board that has not
  changed sends nothing.

### Fixed

- The badge now appears when the game starts instead of after the first mode
  change.
- T3 reasoning rows no longer show up as agent messages.
- Mark read on a T3 thread takes effect immediately.

## [0.1.0] - 2026-09-20

### Added

- One native addon and one bridge for both Hermes and T3 Code.
- A live Electron overlay backed by one private Unix socket, with continuous
  state updates, conversation history, new sessions, and direct actions without
  a WoW UI reload.
- Live Omarchy theme integration from the generated T3 Code palette, a themed
  project dropdown, and native folder-picker project creation for T3 Code.
- Game-aware overlay startup and hiding from the existing bridge service.
- Provider-neutral rows and actions, with Hermes and T3 Code running side by
  side behind the same sync model.
- T3 Code replies, approvals, declines, input answers, stops, mark-read actions,
  live overlay hand-off, and new sessions from in game.
- One installer that detects WoW, Hermes, and T3 Code; manages one bridge
  service; and preserves the other products' data.

### Changed

- Payload schema 5 adds provider, provider label, capabilities, approval fields,
  and user-input fields.
- The overlay is now the primary live interface; the native addon is the
  reload-based fallback. Automatic loading-screen refreshes and send-on-sync are
  disabled by default.
- The outbox carries its provider explicitly:
  `seq|kind|provider|host|session|text`.

This release is adapted from the tested HermesWoW 0.7.0 interaction model and
the T3 Code gaming-overlay branch. The unified bridge supersedes the separate
HermesWoW and T3CodeWoW runtimes.

## [0.7.0] - 2026-09-19

### Added

- Guided Linux setup installs the addon and starts the bridge automatically at
  login. It checks Hermes, the game location and the first snapshot before
  reporting success.
- Managed update and uninstall commands preserve personal settings and session
  data.
- First-run guidance explains setup and recovery without requiring a manually
  running watcher.

## [0.6.0] - 2026-09-19

### Added

- Clickable in-game transition toasts for new attention and completed work, with
  a silent first run, cooldowns, optional sound, and a separate toast toggle.
  Alerts arrive only when the UI loads a fresh snapshot.
- A first-run card with the publish command and sync step, plus a visible board
  Sync button that respects the combat guard.
- Mark read in the detail pane. Suppression lives in the bridge, keyed by host,
  session and activity timestamp; new activity resurfaces the session without
  modifying Hermes's database.
- Clickable chat session links that open the correct host and session.
- Stop turn for local sessions owned by the running desktop backend, dispatched
  at sync. It stops the turn active on delivery and clears queued prompts and
  approvals. Uncertain delivery is reported and never automatically retried.

### Fixed

- Detached CLI replies now record their real exit status. A vanished process
  without a completion record is uncertain, never proof of delivery.
- Corrupt dispatch-state recovery survives publishes and restarts. Old queued
  actions are quarantined with a visible notice instead of accidentally replayed.
- Quiet notification transitions establish the next baseline, completion alerts
  are delivered, and different hosts have independent cooldowns. Offline rows
  cannot notify, and cooling alerts cannot consume the burst allowance.
- Notification dry runs preserve state; malformed ledgers recover silently, and
  a notification write failure no longer prevents queued actions from dispatching.
- Header labels share measured space with all controls. Long detail output stays
  above the action row, and new controls follow skin and text-size changes.
- Preview colors, status words, empty states, timestamps, tab counts and detail
  geometry now match the addon. The public banner uses fictional sessions.
- Malformed outbox sequence numbers are rejected before acknowledgement parsing.
- Release automation runs every offline gate. Packaging explicitly verifies both
  icons, single-loaded bindings, and rejection of unreleased versions.

### Changed

- Payload schema 3 carries the displayed activity timestamp for mark read and a
  separate control notice. Update the bridge and addon together.

## [0.5.0] - 2026-09-19

### Fixed (second review pass)

Three independent reviews (mutation testing of the gates, a first-run player
audit, and a concurrency audit of the bridge). Each fix has a gate.

- **Every in-game reply went nowhere.** `savedvars_path` walked up one level too
  few, so it looked for `WTF` inside `Interface` instead of beside it: the CLI
  reported `no SavedVariables`, the `new=` list was always empty and `acked` was
  always 0. It now finds the version directory by looking for the `WTF` that is
  actually there, and the gate builds a real client layout instead of one level of
  a fake one. (The gate that "covered" this was a tautology: it compared a value
  with itself.)
- **A corrupt dispatch state was a permanent, silent blackout.** The recovery path
  was unreachable, so replies stopped being sent until someone deleted a file they
  did not know about. The damaged file is quarantined, a clean one is written, and
  the next round is normal.
- **A refused CLI fallback was reported as delivered, then acknowledged and
  trimmed.** The fallback now reports a verdict: it is watched briefly, a refusal
  is read out of its log, and a still-running child is journaled as pending and
  left unacknowledged until it is seen to finish.
- **One unreadable row hid the whole board.** `rows=` is compared against what the
  bridge wrote rather than against what the addon could read, and unreadable rows
  are counted in the footer.
- **A reply whose text was `!focus` performed a hand-off** (the text form is now
  honoured only for entries from an addon that predates the kind field).
- **A roster error was published as a healthy empty board.** The bridge now puts
  its own error in the payload, the panel says "Hermes is not answering", and the
  CLI exits non-zero instead of printing a session count.
- **The board leaked a sqlite descriptor per refresh**: 29 open files after 26
  calls, in a loop that runs every ten seconds.
- **No snapshot rendered as "all clear".** First run said "0 need you" and the
  badge said "idle"; both now name the state, and the missing-snapshot line says
  `publish` rather than `install`.
- **`roster.py` had no coverage at all** (five plausible mutations survived). There
  is now a `tests/roster_test.py` with a real sqlite fixture: every bucket, the
  attention count, leases, hidden sessions, and a store missing columns.
- Concurrency and state: a host that had only ever failed was re-fetched (live ssh,
  full timeout) on every publish; a stale failed fetch could overwrite a fresher
  healthy cache entry; a non-numeric `seq` raised out of the watcher; a state write
  that failed killed the watcher on one path and not the other; `hosts.reply` had
  no connect timeout; every state file now goes through one atomic writer with a
  unique temp name, an fsync and 0600 permissions.
- **A presentation pass over the whole window, from a fresh audit of it.** Each
  of these was measured or computed, not eyeballed, and each has a gate that fails
  without the fix:
  - **The pane's where-line was the one line in it never fitted.** A long host or
    project ran to the pane edge and was clipped mid-word with no ellipsis (552px
    into a 492px line), while the title, stats, target and fact values all ended in
    one. Clipping reads as data loss; an ellipsis reads as a name that did not fit.
  - **The composer ran 82px under `Hand off`.** Its right inset reserved room for
    `Send` alone, so the right end of the box, and the player's own typing, sat
    behind an opaque button. The action row's widths are now one set of constants,
    so the inset cannot drift from them again.
  - **The footer's two caps added up past the panel** (380 + 230 against 536), so a
    stale snapshot's notice and a long host list drew over each other with the
    trailer on top. The trailer is fitted first and the legend gets what it leaves.
  - **Collapsing kept the footer**, drawing a second text row in a 56px bar -
    including `n-m of total, scroll`, which is a lie while scrolling is disabled.
  - **A skin switch from `/hermesai theme` left the badge grey.** The palette walk
    paints the badge's border with the theme's plain edge, and that border is a
    status channel: it went from needs-orange to the panel grey until the next
    poll. The walk now refreshes the badge, as the settings path always did.
  - **The panes' own buttons were not walked at all** by a skin switch, and the
    settings pane is on screen at the moment its skin line is clicked.
  - **The pane's status line was painted entirely in the status colour** - the
    detail-pane twin of the row fix: the word carries the colour, the age and the
    session id are muted and measured against it.
  - **The header's glyph buttons said nothing.** `X` and `-` are conventions, `*`
    is not one, and nothing on hover explained it. Every glyph button now names
    itself on hover.
  - **A list with nothing in it said nothing.** A search matching no session left a
    blank rectangle under a header reading "all clear", which looks like a working
    board; it now says whether nothing matches or nothing has run yet.
  - **The tab strip was inset 12px on the left and 24px on the right**: six tabs at
    84px left a visibly wider gutter on one side. Sized to fill, it is 12 and 12.
  - **The dimmest text was under the small-text contrast bar.** `DIM` measured
    4.24:1 against the dark panel, and it is the colour of the age, the footer, the
    hint and the fact keys; it is now ~5.6:1. The gate computes the ratio from the
    recorded palette rather than trusting the value.
  - **Two pixels, twice:** the action buttons were 20 tall against a 22px composer,
    and the search placeholder sat 2px right of the text that replaces it.
- **A truncated title could end in half a character.** Truncation measured the
  cut in bytes and then stepped back off a multi-byte character by returning the
  *lead* byte's index, which kept the lead byte on its own - so a title ending in
  one rendered a replacement glyph on every refresh, exactly the corruption the
  helper exists to prevent. It now drops the partial character.
- **A host name containing a colon was mis-split.** The header's host map was cut
  on the first colon, and the producer strips the separators that could forge a
  field but not `:`. A host called `foundry:2222` became a host called `foundry`
  that is down, plus a phantom one, so every row from the real host read as
  offline and the footer named a machine nobody configured. It splits on the last
  colon now: the state never contains one.
- **The row's second line was one colour across its whole width.** The status word
  and the trail that follows it were a single string painted in the status colour,
  so every row carried a full-width shout while the dot and the legend were
  already carrying the status. The line is now two pieces: the status word in its
  status colour, the trail muted, with the trail's room measured from the word
  rather than guessed - so a long status cannot push it under the age column.
- **One player reply could be injected into the same live session once per watch
  round.** `dispatch` decides what is new by membership in `dispatched`, and a
  pending CLI turn is deliberately not in there (it has not been delivered), so
  nothing stood between the next round and a second `hermes chat --resume` for the
  same entry. A turn that runs for minutes was re-launched every ten seconds - each
  launch overwriting the pid of the one already running, so no round could ever
  settle the first child, and the reply landed in a live session again and again.
  What is in flight is now skipped by key. The gate dispatches a still-running
  entry twice and counts the launches; it used to be two.
- **A superscript digit after a backslash took the watcher down.** `str.isdigit()`
  is true for `\²` but `\d` does not match it, so the escape parser fell back to
  the bare character and `int()` raised `ValueError` out of the unescape, which
  `read_outbox` calls unguarded every round - so `wow watch` died on a file. A
  backslash that is not a decimal escape is now left alone like any other unknown
  one. A vulgar fraction is not a digit by that definition and never reached the
  branch; it is in the gate as the case that must stay quiet.
- **A session store that could not be opened took the CLI and the watcher with
  it.** `board()` guarded a *missing* store and every failure after the connect,
  but the connect itself sat outside the `try`, so a `state.db` that was a
  directory, unreadable, or removed between the check and the connect raised
  `OperationalError` instead of returning the error shape every other path returns.
- **One non-finite number in a remote row cost the whole publish.** `json.loads`
  accepts `Infinity`, `NaN` and an overflowing `1e999` without complaint, `float()`
  keeps them, and the `int()` on the render path refuses all three: `render_payload`
  raised, `publish` never wrote, and the board silently stopped updating. `_number`
  in both `wowclient` and `hosts` now treats non-finite as the malformed input it is.
- **The badge painted a border on a client that has no backdrops.** `applyBackdrop`
  asks before it paints, because `backdropFrame` falls back to a plain frame on a
  client without the BackdropTemplate mixin - but the badge's own border colour was
  set by hand, so on that client the error frame the fallback exists to prevent
  arrived at login instead. The stub can now model that client, and the gate builds
  and paints the badge against it.
- **A polish pass over the same chrome, from looking at it in game.** The search
  box and the reply composer were drawing the tooltip's border art at tooltip
  size: a 12px edge with 3px insets is most of a 22px field's height, so its
  corners met in the middle and each field read as two nested rectangles rather
  than one box. Both now use the same art sized for the frame. On the minimap
  button, the disc was drawn at 0.9 alpha, which let the themed square behind it
  show its corners through the soft edge and made the button read as a blob, and
  the count sat in a 15px square chip that covered nearly half of a 32px button -
  a square badge on a round one. The disc is opaque, and the count is a shadowed
  number riding on the disc, which is the contrast the chip was there to provide.
  The gate now records each frame's backdrop table and asserts both fields get the
  small one, at build time and again across a skin switch.
- **The minimap button sat in the middle of the map, and the badge was drawn
  across it.** Both came from the same mistake: a position that belongs to the
  minimap written down as a constant. The button's ring radius was fixed at 80,
  which is the edge of the 140px minimap it was written against and a hole in the
  middle of anything larger; the badge's default was a fixed offset from the
  screen corner that assumed the same 140px height. The radius is now measured
  from `Minimap:GetWidth()` and re-measured when the map is resized, and the badge
  anchors under the minimap cluster until the player drags it. Three other things
  fell out of looking at it: the count was a bare FontString pushed off the
  button's corner, so the number floated over the map beside it, and it is now a
  chip inside the button that hides at zero; the badge's label was cut to an
  ellipsis on its commonest state (`5 need you ...`) because the age was spending
  the width, so the age moved to a new hover tooltip that also carries the dot
  breakdown; and the button is now the client's own round minimap disc instead of
  a square tile. The gate records anchors in the stub and asserts the button stays
  outside a resized map's circle and that the badge is anchored to the minimap.
- **Dragging the board or the badge raised an error and stored nothing, and the
  board's text ran together.** Ending a drag read `GetPoint`'s five return values
  off a four-name list, so `x` took the *relativePoint* string and `y` took an
  offset one place too early: every drag reported `attempt to perform arithmetic
  on local 'x' (a string value)` and no position was ever saved. The detail and
  settings panes were also left at the rows' own frame level, and the refresh
  re-shows the rows on every pass, so the list drew over the pane the player had
  just opened and the two read as one mangled block of text. Both panes now sit a
  level above the rows. The gate drives `OnDragStart`/`OnDragStop` on every
  movable frame, checks the saved point is offsets rather than a point name, and
  asserts each pane outranks the list.
- Smaller: a saved anchor that was not an anchor raised inside `SetPoint` on load;
  truncation could cut a UTF-8 character in half; the badge could not be hidden;
  resetting positions did not reset the minimap button; dragging the badge also
  toggled the board; the burst cap spent itself on an arbitrary three; the README's
  first impression was a broken image (`![...](*.html)`); all five `<Binding>`
  entries in `Bindings.xml` repeated `header="HERMESAI"`, and the attribute
  *registers* a header rather than simply naming it, so the client warned
  `Binding header HERMESAI was attempted to be loaded more than once` four times
  per load (`Count: 4`). It belongs on the first binding only - the rest inherit
  the last header registered - and `tests/addon_checks.py` now fails a re-declared
  header instead of requiring one on every binding.

### Added

- `tests/roster_test.py`: the status engine against a real store.
- `wowmode/state.py`: one place for the XDG paths and the atomic write.
- The badge can be hidden, and `/hermesai badge` toggles it.

### Fixed (sweep pass)

- **A torn record could become a phantom row.** A stray separator shifts the
  payload's records by one, and a shifted record still has a non-empty first
  field, so it was counted as a session. Both halves now require a session id to
  look like one, which also stops a truncated outbox line from being read as a
  reply to a session called `reply`.
- **Text fitted at one UI scale stayed fitted at the next.** The panel now
  refits on `UI_SCALE_CHANGED` and `DISPLAY_SIZE_CHANGED`, which is when the
  client's font metrics change under it.
- **The host-cache lock was held across ssh.** A dead host's timeout could stall
  the refresher thread and the publish path against each other; the fetch now
  happens outside the lock and only the write is serialised.
- **Paging a collapsed panel moved an invisible window.**
- Smaller: the watcher wrote its notification ledger twice per pass.

### Added

- **`tests/hostile_test.py`**: the two halves against a corpus of junk they must
  survive (torn payloads, junk ids, unreadable SavedVariables, a megabyte
  outbox, 200 fuzzed strings), each case stating its expected verdict.
- A lint rule that fails when player-visible text is a bare string literal
  instead of going through the locale table.

- **Panel**: tabs with per-bucket counts, a search box, twelve scrollable rows,
  a detail pane with a composer, a settings pane, and a window range line
  (`1-12 of 15, scroll`) so a list longer than the panel says so.
- **Minimap button**: click for the board, right click to sync, drag to move,
  with a live count badge.
- **Hand off**: puts a session id on the clipboard and raises a notification, for
  a player who wants to answer in the desktop app instead.
- **Remote hosts**: `hermes-wow wow hosts add` merges agents from other machines.
  A host that cannot be reached keeps its last known rows, marked offline.
- **Sound** when a sync brings something that needs the player.
- **Localization scaffolding**: every user-visible string goes through
  `Locale.lua`, so a translation is one file and English needs none.
- **Chat commands for everything a click can do**, with `/hermesai help [command]`
  generated from the command table.
- **Five offline gates** and `make check`, plus CI on every push.
- `acked=` in the payload: the addon drops outbox entries the bridge has settled,
  instead of carrying them until the tail is trimmed.

### Changed

- Payload schema 2 (was 1): rows carry their host, an offline flag and a preview
  of the last output; the header carries the host map.
- Outbox entries are `seq|kind|host|session|text`. The kind is explicit rather
  than inferred from the text, so a reply that says `!focus` is still a reply.
- Text that has to fit is measured with `GetStringWidth` instead of truncated by
  character count.
- The panel skins its own buttons instead of using `UIPanelButtonTemplate`, so a
  theme change repaints the whole window.
- A dispatch log that cannot be parsed is treated as "everything was already
  sent" and reported, rather than replaying the outbox into live sessions.

### Fixed (independent review pass)

Two fresh-eyes reviews of the Lua and the Python found these; each has a gate now.

- **A dead remote host was published as `ok`, and its cache never expired.** A
  cache hit reported reachability it never checked, and `merge` re-stamped the
  entry's timestamp on every pass, so a host was fetched once and then frozen for
  the life of the process. Reachability is now stored with the rows and the
  fetch's own timestamp is kept.
- **Reply text was injected into the remote login shell.** `json.dumps` is not
  shell quoting: a reply containing `$(...)` or backticks ran on the other
  machine. Everything interpolated into the ssh command line is `shlex.quote`d,
  and a gate asserts the text arrives as one argument.
- **The `acked` watermark could delete an undelivered reply.** A failed entry
  below a later success was acknowledged and trimmed with nothing left to retry
  it. The mark now covers only the contiguous settled prefix, and the addon
  refuses a mark from further ahead than its own counter (a wiped WTF folder).
- **A corrupt dispatch log replayed the whole outbox into live sessions.** The
  state file is written atomically now, and an unreadable one fails safe and
  loud: nothing is sent, and every entry says why.
- **`hosts-cache.json` was written by two threads with no lock.**
- **A notification dropped by the burst cap was lost forever**; it now survives
  to the next pass. A failed send no longer consumes its cooldown, and
  `notify-send` is called as (summary, body) with its exit code checked.
- **A leak per reply**: the CLI fallback left its log file descriptor open.
- **`_unescape_lua_literal` could raise** out of the outbox parser on `\2a`.
- **The board could crash on a session store missing a column**; the query is
  wrapped like every other one.
- **`savedvars_path` only accepted the AddOns directory**, so passing the addon
  directory silently produced a valid install with no "new" markers.
- **SavedVariables junk could raise during the UI build** (an unvalidated
  `SetPoint` table), and the badge's default position sat on top of the minimap
  cluster.
- **The footer legend described dots nobody draws** (`finished` was listed,
  `error` was not, `new reply` was pushed out of the dot band).
- **Collapsed panel left its search box and tabs floating** under a 56px bar.
- **The search placeholder drew through the player's typing.**
- **The page keys the docs promised were never wired**; `PageUp`/`PageDown` are
  real bindings now, and the dead `ScrollTo`/`Refresh` were removed.
- Smaller: the CLI's board omitted the `waiting` bucket and crashed on a nil
  title; `/hermesai tab All` failed on case; `/hermesai policy` accepted 0, which
  would have reloaded the UI at every loading screen; `/hermesai handoff` printed
  the internal word `focus`; `install` ignored `--limit/--days/--no-hosts`.

### Fixed

- A refresh while the panel was collapsed brought the rows back.
- Switching skins left the row striping, hover wash, buttons and minimap button
  on the old theme.
- The badge's four status dots were filled from a fixed status list, so
  "new reply" could never appear.
- A new snapshot left the row window scrolled past the end of the list.
- `/hermesai help` raised a Lua error (a closure over a table that was not yet in
  scope).
- The `PLAYER_ENTERING_WORLD` handler read the wrong event arguments.
- `wl-copy` reads as a failure when its input is a pipe, because it forks a child
  that holds the pipe open; the hand-off now writes a temp file.
- A reply to a session id that is no longer in the snapshot is refused with an
  explanation instead of being sent to the wrong place.
- `\u` escapes: Lua 5.1 has no `\u`, so `"\u00b7"` rendered as the literal text
  `u00b7` in every row's meta line.

## [0.3.0] - 2026-09-18

Initial in-game build: badge, board, `Data.lua` handshake, reply outbox, sync
keys, and the stub harness that proves the API surface.
