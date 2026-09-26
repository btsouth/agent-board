# Agent Board overlay review — 2026-09-20

Implemented in `/home/bts/Projects/agent-board`. The separate `hermes-wow`
checkout was inspected initially but was not modified.

## Messaging and state

- Added a persistent, per-session FIFO queue. Enter queues during an active turn;
  Send now/Ctrl+Enter uses provider steering. An empty composer can send the
  oldest queued item with Ctrl+Enter.
- Queued messages support edit/remove. Reconnects, reloads and restarts preserve
  unsent text. Uncertain sends are held for review instead of automatically
  repeated. Repeated identical text is distinguished using message identities.
- Automatic draining waits for a turn boundary and holds during approvals,
  questions, provider outages and errors. Stop pauses queued work and waits for
  a pending send before issuing the stop.
- T3 replies retain the thread's runtime and interaction modes. Hermes immediate
  delivery uses `session.steer`; ordinary queue draining uses `queued: true`.
  An uncertain Hermes live RPC never falls through to a duplicate CLI turn.
- Added complete multi-question forms, Hermes outstanding-request discovery and
  request-scoped approval/clarification responses. Expired requests fail visibly.
- Fixed failures being presented as successful Stop actions, stale/overlapping
  refreshes, missing connection-close handling, and promises leaving controls
  stuck. Drafts persist separately per session. New messages follow the scroll
  only when already near the bottom. Conversations show 60 recent messages and
  support copying message text.

## Badge and desktop behavior

- The badge shows running agents plus the latest reply's title/age. Old assistant
  questions and proposed plans do not count as requests for attention. Actual
  approvals/questions and queued work remain visible. Offline provider snapshots
  do not inflate the running count.
- Transparent native background and rounded, compact badge content remove the
  opaque black surround. The renderer fills the native surface at fractional
  display scaling.
- Compositor requests are serialized; obsolete mode changes cannot win a later
  toggle. The renderer acknowledges its new frame before the window is revealed.
- Badge and board positions are saved independently, sampled from Hyprland's
  actual coordinates rather than Electron's unreliable Wayland x/y values.
  Positions survive toggles/restarts and clamp to available displays.
- Pinning is idempotent. Monitor ids are matched as ids, and WoW takes precedence
  over unrelated fullscreen windows when choosing the game monitor.
- Collapse releases keyboard focus; opening focuses the composer. Escape is a
  single-step collapse. The existing Super+Alt+C binding remains in place.
- Prefer installed Electron 43/42 over the older Hermes-bundled Electron.
- Launch the overlay in a separate systemd scope. A bridge restart had killed
  Electron helper processes while its main process lived in a different scope,
  producing a GPU-process fatal error. Restart verification now preserves the
  overlay process and its helpers.
- Updated the user Hyprland rule to disable animation/dimming/focus-following for
  this app; configuration reload reports no errors. Its prior file is backed up
  as `~/.config/hypr/hyprland.lua.agent-board-backup`.

The matching reusable rule is in `contrib/hyprland-window.lua`. Syntax was checked
against the official [window-rule documentation](https://wiki.hypr.land/Configuring/Basics/Window-Rules/)
and [dispatcher documentation](https://wiki.hypr.land/Configuring/Basics/Dispatchers/).

## Verification

- `make check`: passed all repository suites, including new queue, badge and
  overlay regression tests.
- `make lint`: passed shell, Lua and JavaScript checks.
- `tests/overlay_ui_test.cjs`: passed browser-level queue/send-now, draft isolation,
  structured questions, Escape and badge checks, without renderer errors. Run
  with an installed `playwright-core` (or set `PLAYWRIGHT_MODULE`) and Chromium.
- Native Hyprland/Wayland checks on 125% displays: repeated board/badge toggles,
  760×640/286×46 compositor sizes, moved-position restoration, application restart
  restoration, and a rapid toggle burst all passed. Normal sampled transitions
  reached their target in approximately 0.13–0.20 seconds before the final frame.
- Bridge restart preserved the overlay PID. No new overlay crash was observed
  after separating the process scopes.
- In-game visual inspection verified the full board and transparent window
  background. Further native tests continued on the desktop after WoW closed.

The Hermes desktop backend was not running during verification. Its send,
steering and request paths were tested with protocol fixtures; no live Hermes
round trip is claimed. No verification messages were sent into the user's
working agent conversations. Other Wayland compositors were not tested.

The queue drains while the overlay is running (including badge mode). Closing
it preserves the queue but pauses draining until it reopens. This review does
not add file attachments, full historical transcript pagination, or replace
T3 Code/Hermes's native project/settings interfaces.
