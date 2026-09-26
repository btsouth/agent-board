# Freshness and notification: the whole system

The ask was the best refresh and notification design that does not cost game
performance. This is it end to end, including what is impossible and why.

## The wall (unchanged, and it decides everything)

A WoW addon cannot read a file or open a socket. The client reads addon files
**only when the UI loads**: at login and on `/reload`. Nothing else re-reads
them, ever. So in game, "fresh data" is a function of *when the UI last loaded*,
and the only lever we have is choosing those moments well.

That means the system is not one mechanism. It is three, each with a different
cost, and each covering the gap the others cannot.

## 1. In game: spend reloads deliberately, never accidentally

A sync = write SavedVariables + `ReloadUI()`. The bridge sees the flushed
outbox on its next poll; that reload can precede the updated snapshot. Sync again
to see action results. Cost: roughly 1 to 3 seconds of
frozen UI and a frame rebuild, no world reload, no loading screen. Measured as a
share of play time it is negligible, but the *timing* is what the player feels,
so the policy is strict.

Triggers, in order of who asked:

| Trigger | When | Notes |
| --- | --- | --- |
| Player | keybind, badge click, minimap click, `/hermesai sync`, or the sync button | always honoured outside combat |
| Opportunistic | a zone transition (`PLAYER_ENTERING_WORLD`, not login, not reload) | the player just watched a loading screen, so a UI reload there is invisible |
| Piggyback | any UI load the client does for its own reasons (their own `/reload`, an addon update) | free freshness; the board announces what is new since last look |

Guard rails on the opportunistic path, all configurable, all defaulting
conservative:

- never in combat (`InCombatLockdown()`)
- never inside an instance, raid, arena or battleground: not worth interrupting
- only if the snapshot is older than `staleAfter` (default 300s)
- at most once per `minInterval` (default 600s)
- coordinates so a zone transition and a manual press cannot both fire in the
  same second

And to make a spent reload feel like a refresh instead of a restart, the addon
saves and restores its own state across it: board open or closed, selected row,
active filter, scroll position. It reopens on the same row. The only thing the
player should notice is that the numbers changed.

Rejected on the way here, with reasons:

- **Load-on-demand data addons.** The client does read a LoadOnDemand addon's
  files from disk at `C_AddOns.LoadAddOn`, which would be a cheap re-read with
  no UI reload. But each addon loads exactly once per session, so it needs a
  ring of slot addons (dozens of junk folders in the Addons list, visible to
  every user). Unshippable in a public addon.
- **Input automation to poke the client.** Botting territory. No.
- **An in-game alert fired by the bridge.** The bridge cannot reach into the
  client. Nothing can, except another WoW client.

## 2. Out of game: the live half

The bridge is a normal process with full file access, so it is where liveness
lives. It reads the session store read-only (WAL, no writer locks) on a short
interval and looks for **state transitions**, not states:

- a session becomes `needs you`
- a session errors
- a session you were waiting on finishes

Only transitions notify, only for unread sessions (the store's own
`last_read_at` watermark decides), bursts are coalesced, and the first run seeds
state silently so a fresh install never fires a wall of stale alerts.

Channels, all optional, any combination:

1. **Desktop notification** (`notify-send` on Linux, a toast on Windows).
2. **Hermes messaging** via `hermes send <platform>`: Telegram, Discord, ntfy,
   Signal, whatever the user has paired. This is the real answer to "tell me
   when something needs me while I am playing": a phone buzz, and the board is
   one sync away when they look.
3. **Nothing**. Opt out entirely; the in-game age display still tells the truth.

## 3. In game: never lie about freshness

Because the snapshot can be minutes old, the UI has to say so, permanently:

- badge: the count in words (`3 need you`); the age is one hover away, in the
  tooltip, because it is what a 230px bar runs out of room for first
- board header: `synced 4m ago`, and a `Sync` button next to it
- after a load, anything that changed since the last look is called out:
  `+2 new since you last looked`, and the badge pulses once, with an optional
  sound. A clickable toast shows new attention and completed work at sync,
  seeds first-run history silently, and rate-limits repeated session/kind
  transitions. Sound and Sync toasts have separate settings
- if the loaded snapshot is older than 15 minutes, the board says
  `snapshot is old` and asks the player to Sync, then run setup if it stays old.
  The addon cannot tell a stopped bridge from time spent playing without a sync
- if the file is corrupt or from an incompatible bridge version, the addon falls
  back to the last good snapshot in SavedVariables and labels it as such

## The data contract

`Data.lua` is a **single serialized string**, not executable Lua:

```
HermesAI1|bridge=0.3.0|schema=1|generated=1789788722|new=20260918_230103_39da44;...
20260918_230103_39da44|needs|600|ds-router|default|Add direct DeepSeek API to router||107|0.42
```

Field order is fixed; rows are joined by `;;` and fields by `|`, the same
conventions as the reply outbox. Why a string instead of a Lua table:

- **Trust.** The addon parses data; it never executes code written by another
  program. A bad file yields "no data", not an error frame or injected Lua.
- **Tolerance.** Every row is validated on read; a malformed row is skipped, not
  fatal. A truncated file (bridge killed mid-write, which is why the write is an
  atomic rename) reads as no data instead of an exception.
- **Versioning.** `bridge` and `schema` travel in the payload, so an old addon
  meeting a new bridge can say so plainly.

## Performance budget, stated plainly

- Addon: `OnUpdate` exists only during minimap dragging and the one-second toast
  fade, and clears itself when finished. A timer starts the fade after six
  seconds. No roster polling. Work happens on
  events (UI load, combat end, zone change, clicks) and is bounded by the number
  of rows on screen. Rows are built once and reused; a refresh writes strings.
- Bridge: one read-only query per poll against a WAL database (indexed by the
  same columns the desktop app uses), default every 5s, plus one atomic file
  write per publish (default every 10s, and only when the content changed).
- Network: none in game, by construction.

## What the player is actually promised

"Your agents' state, one keypress away, refreshed whenever the game is already
loading or you ask for it, and a phone or desktop alert the moment something
needs you." Not "live in game". The sandbox does not allow that, and an addon
that pretends otherwise would be lying in the first comment thread.

## Background bridge lifecycle

Guided setup installs the addon and enables `hermes-wow.service` in the Linux
user session. It starts at login and requires no open terminal. Setup records
the selected game and Hermes paths; update reuses them and restarts the service.
The service changes no WoW restriction: files still load only at login or Sync.
Uninstall disables the background bridge while preserving game and Hermes data.
