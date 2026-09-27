# Bridge review, 26 September 2026

Review of 0.2.7 (`8d9f34a`). Each finding was reproduced before it was fixed,
and each regression test fails against 0.2.7.

## Confirmed and fixed

| Finding | Trigger and result | Code | Coverage |
| --- | --- | --- | --- |
| CLI reply sent again after a failed turn | In-game reply through the CLI fallback; `hermes chat --oneshot` exits 1 after taking the prompt. 0.2.7 called it refused and launched it every round. Now only a refusal line or exit 127 is retried; other exits are held as uncertain. | `backend.not_started`, `wowclient._reconcile_pending`, `_one_shot` | `runtime_test`, `roundtrip` |
| Ledger trim re-sent waiting entries | An in-game entry stuck on a failure holds later ones in the outbox. After 500 overlay actions, `sorted(seen)[-500:]` dropped keys like `10\|…` and those replies were sent again. Live keys stay out of the ledger now, and trimming is oldest first and keeps outbox keys. | `wowclient._ledger` | `runtime_test` |
| Overlay shown another action's result | A pending CLI reply settled during a later overlay action; `dispatch_live` returned `results[0]`, so a successful approval read as refused. | `wowclient.dispatch_live` | `runtime_test` |
| Remote reply held the ledger lock | A remote reply ran the whole remote turn over ssh inside the lock (up to 180 s). The overlay gave up after 40 s and showed a failure, while other actions waited and ran later. Remote replies now run detached like the CLI fallback. | `hosts.reply_command`, `wowclient.dispatch` | `runtime_test`, `hosts_test` |
| Remote mark read had no effect | Overlay reported "marked_read" but the live row stayed unread; only the in-game payload applied the mark. | `wowclient.remote_read_marks` | `runtime_test` |
| Deep copies on every poll | The installed bridge used about 35% of a core. `t3.snapshot`, `hermes_live.enrich`, `CachedBoard.read` and `LiveBridge.latest` deep-copied every transcript. | `t3.py`, `hermes_live.py`, `roster.py`, `live.py` | `runtime_test` |
| Status file writes | Two fsync'd writes per second for game state measured at about 265 KiB of btrfs writes each, about 23 GB a day. Status files now change-or-heartbeat; unchanged notification and T3 provider state are skipped. | `state.StatusFile`, `notify.save_state`, `t3_provider.mjs` | `runtime_test`, `provider_runtime_test` |
| Tests started a real T3 provider | `service_test` and two `roundtrip` watcher rounds mocked Hermes only, so a machine with T3 installed connected to it and wrote the real state directory. | tests | verified with a fake T3 home |

The overlay's action timeout now says the action may still go through.

## Measurements

One live poll with synthetic T3 rows shaped like 187 real threads, plus the
real Hermes store opened read-only:

| Board | 0.2.7 | Now |
| --- | ---: | ---: |
| 1 visible row | 158 ms | 2 ms |
| 32 visible rows with transcripts | 210 ms | 7 ms |

Idle watcher with an empty store on btrfs, 40 s: 259 KiB/s before, 18 KiB/s
after. The remainder is the in-game `Data.lua`, still written every 10 s.

## Checked, not changed

- `_merge_stream` hides a streamed reply only while its full text equals a
  recent stored reply; Hermes flushes the reply before the session goes idle,
  so the stored copy follows. No valid reply is lost.
- A stream can vanish for one snapshot if the store flush and the idle status
  both land between the roster read and the monitor merge. The window is a
  few milliseconds; not reproduced.
- Remote Hermes rows keep the question heuristic's `needs` status, which holds
  their queue until the row is read. Local rows map it to `reply`.
- The T3 provider publishes every non-archived thread's transcript, about
  5 MB per publish for 187 threads.
- `t3-provider-state.json.<pid>.tmp` files left by 0.2.6 and earlier are not
  cleaned up; 0.2.7 removes its own.

## Acceptance still open

`make check lint` and the native lifecycle test passed in omabox. Real
Wine/WoW window attribution, a live remote host reply and live provider round
trips were not exercised. The installed service was not changed.
