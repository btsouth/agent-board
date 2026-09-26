# WoW mode: what the game allows, and what we build on it

The ask was a native WoW plugin, no overlays. This records what the client
actually permits, because the constraint decides the whole design and it is not
obvious from the outside.

## The constraint

A WoW addon runs in a sandbox. It has:

- **no filesystem** (no read, write, or listing; `os`, `io` and `debug` are
  removed from the Lua environment)
- **no network** (no sockets, no HTTP)
- **no process or OS access**
- **no memory access**

Its only persistence is `SavedVariables`, and those are managed by the client:
read when the UI loads, written when you log out or `/reload`. There is no API
to flush them mid-session and no API to re-read a file while playing.

The only live channel an addon has to the outside world is chat traffic
(`SendAddonMessage`), and chat ingress requires *another WoW client* to be
sending it. That is the part people miss:

| Direction | Native mechanism | Live? |
| --- | --- | --- |
| addon -> outside | SavedVariables file | no, written at logout `/reload` |
| addon -> outside | chat / whisper traffic | yes, but the reader is another client, not a local process |
| outside -> addon | a Lua file in the addon folder | no, read at UI load |
| outside -> addon | chat | only from another WoW client |

So: **a pure in-game board cannot show live external data.** Every real WoW
bridge lands on the same shape. TradeSkillMaster's desktop app syncs through
SavedVariables and asks you to `/reload`. Guild-to-Discord relays need a
dedicated relay character logged in. RCLootCouncil exports the same way.

Two escape hatches exist and both were rejected:

- **A relay character** (second account or a friend driving one) would make it
  live and bidirectional. Automating a client is botting; Blizzard's anti-cheat
  and ToS treat it as such, and the ban is not worth a status badge.
- **Input automation** (typing `/run ...` into the game window from outside)
  is the same violation with extra steps.

That is why the t3code screenshot is an OS overlay: over a game, an overlay is
the only live option, not a lazy one.

## The name

The addon is **HermesAI**: folder `HermesAI`, SavedVariables `HermesAI*`,
slash command `/hermesai` (short form `/hai`). This distinguishes the addon from
other projects called Hermes. No store-name availability is implied.

## What we build instead

Two surfaces, each doing what it is actually good at.

### In game: the HermesAI addon (native, no overlay)

- Badge near the top of the screen: `3 need you`, plus status dots. Movable,
  remembers where you put it.
- Board: rows per session, status colour, project, age, live activity text.
  Click a row, type a reply, `Enter`.
- `Sync` (button, keybind, or `/hermesai sync`) is a UI reload. That one gesture
  writes queued actions into SavedVariables and reads `Data.lua` on the way
  back. The bridge dispatches on its next poll, so another sync may be needed
  before the action result appears.
  Reload, not a loading screen, and it refuses while you are in combat.
- The snapshot refreshes for free on login, so the board is current every time
  you start playing.

### Outside: the bridge, which is where "live" lives

- `hermes-wow wow watch` publishes the roster into `Data.lua` continuously and
  reads/dispatches queued replies out of SavedVariables.
- The live pulse (a real-time "N need you") cannot be in the game, so if you
  want it, it goes on the other monitor: `hermes-wow overlay` with
  `--anchor other` places the badge on the display that does not have the game
  on it. Nothing covers the game, and no keystroke is taken.

## Honest limits

- Between syncs the board shows the snapshot from the last UI load. Statuses
  move under it while you play; you press Sync when you care.
- A reply only reaches Hermes at the next sync, which is why the composer
  offers to sync on send.
- Approvals waiting inside the desktop process still read as `Working`; the
  store has no approval state, and only the owning process has it.
- The addon folder appearing mid-session is not picked up: a new addon needs a
  client restart, not just `/reload`.

## Open items

- Interface number is pinned at `16001`, confirmed against the client:
  `/run print(GetBuildInfo())` returned `1.60.1 69913 Sep 17 2026 16001`.

  The pin is the build the addon was last *tested* against, and it is deliberately
  the beta this project is developed against rather than the newest number anyone
  has seen. A client with a newer build shows the addon as out of date until the
  pin moves, and moving it is one command from that build line:
  `make interface IFACE=<the last number>`. That rewrites the toc and the lint's
  expected value together, because those two drifting apart is the entire failure
  mode - `tests/addon_checks.py` holds the number separately on purpose, since a
  lint that read the toc would be checking it against itself.

  If this ever targets more than one client family, the mechanism is a per-flavour
  toc (`HermesAI_Vanilla.toc`, `HermesAI_Mainline.toc`, ...) with `HermesAI.toc` as
  the fallback. A single wider pin would be claiming support on clients the addon
  has never run on.
- Refreshing on loading screens is **on** by default (`opportunistic` in the
  settings pane). `/hermesai opportunistic` turns it off. When it is on it spends
  a UI reload only at a moment the player was already waiting - landing in a new
  zone with a snapshot older than the staleness window - and never in combat or
  inside an instance. There is no `autosync` verb; an earlier draft of this
  document named one and it was never built.
- Learned the hard way: `SetBackdrop` only exists with the `BackdropTemplate`
  mixin on this client family, and a permissive test stub let that reach the
  game. The stub now gates template-only methods the way the client does, and
  the suite is run against a deliberately broken copy to prove the gate bites.
