# 0.6.0 validation

Validated on 2026-09-19 with Python 3 and Lua 5.1, without sending prompts to
real sessions or interrupting real work.

`make check lint package` passes. The artifact contains one `HermesAI/` folder,
five Lua files, the toc, one Bindings.xml and both icons. Data.lua is empty.
The unreleased packaging guard was exercised before dating this release.

## Regression proof

Each change below was deliberately removed in an isolated temporary copy. Its
relevant gate failed, then passed with the implementation restored.

| Behavior | Gate and reverted behavior |
| --- | --- |
| Toasts | Lua stub rejects missing transitions, removed cooldown, lost skin repaint |
| First run | Lua stub rejects hidden setup card, duplicate freshness wording, missing scale refit |
| Mark read | Lua and roundtrip gates reject missing outbox kind or disabled suppression |
| Chat links | Lua stub rejects a handler that does not select the linked host and session |
| Stop | Lua and roundtrip gates reject missing controls or a no-op interrupt route |
| Layout | Lua stub rejects over-wide header labels and unbounded detail output |
| CLI replies | Roundtrip rejects the old vanished-PID success inference |
| Recovery | Roundtrip rejects a corruption flag consumed before dispatch |
| Notifications | Notification gates reject lost quiet transitions, lost completion, host collisions, offline alerts and mutating dry runs |
| Preview | Preview gates reject missing setup instructions, controls and bounded detail output |
| Packaging | Packaging gates reject missing icons, double-listed bindings and an ignored unreleased marker |
| Hostile input | Hostile-input gates reject superscript and unbounded sequence numbers |

Board, working-detail, first-run and toast previews were rasterized and inspected.
The public banner contains fictional sessions. The local live-roster preview is
ignored by Git and is not in the release.

## In-game checks still required

1. Reload the upgraded addon and check both skins and your usual UI scale. Open
   a long session and verify the header, facts, composer and action buttons fit.
2. With the bridge watcher running, let a session ask a question or finish, then
   sync. Check the toast, click-through, fade and sound toggle. Re-syncing the same
   snapshot must not repeat the alert.
3. Mark a session read, sync, let the bridge publish, and sync again. It should
   leave attention; fresh activity should bring it back.
4. Run `/hermesai status` and click a session link. It should open that session.
5. On a disposable local desktop session, queue Stop and sync. Confirm the
   intended running turn stops. Delivery targets the then-current turn, and
   clears queued prompts and approvals. Remote and idle rows must not offer it.
6. On a separate fresh test profile, confirm the setup card and a silent first
   snapshot. Confirm Sync refuses during combat and works afterward.

The new controls have not been verified in the real client. This is a preview
release for Classic beta interface 16001, not a claim of support for every WoW
client or operating system.
