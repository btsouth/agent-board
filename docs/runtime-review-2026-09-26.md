# Runtime review, 26 September 2026

Release 0.2.7 addresses the runtime audit of 892cdef (0.2.6).

| Area | Change | Validation |
| --- | --- | --- |
| WoW lifecycle | One bridge tracker, owned process identities, matching window PIDs, compositor events, cached PID checks, exit grace and launch backoff | Fake processes, false-title fixture, real isolated Hyprland/Electron lifecycle |
| Visibility | Manual ownership, automatic visibility, pause until exit, stale placement cancellation | Exit during a delayed pin, restart, stale bridge and native window checks |
| Updates | Optional Hermes/T3 paths, recovery through the current bootstrap, running overlay restart | Update/rollback fixtures, real local Git checkout, native overlay restart |
| Queues | Per-session receipts, retained queued sessions and uncertain-delivery holds | Fast background turns, restart journal, repeated text and disconnect cases |
| Requests | Outstanding approval/input requests survive age and row limits | Old requests among newer completed sessions |
| Permissions | Explicit approval/full-access choice for new T3 sessions | Actual dispatch command assertions and browser form checks |
| Providers | Configured/offline/error distinction, bounded RPCs, Node version check, restart backoff and cleanup | Offline provider and private-socket fixtures |
| Remote hosts | Cached host rows enter the live board, with offline handling and separate selection/drafts | Cache-only merge and duplicate session ID checks |
| Streaming | Persistent monitor connection, per-session failure isolation, replay deduplication and stable IDs | Hermes event replay fixtures |
| Polling | Store/thread caches, fewer transcript reads, adaptive cadence and event-driven geometry saves | Repeatable roster benchmark below |

The existing service owns the tracker and provider workers. No additional daemon,
provider, settings page or cloud component was added.

## Measurement

On devbox, the same unchanged SQLite fixture contained 200 sessions with 20
messages each. Each case ran for eight seconds with a 15-row board. These
measurements cover roster work, not total application CPU or live-provider load.

| Case | Polls | Database opens | SQLite queries | CPU time |
| --- | ---: | ---: | ---: | ---: |
| 0.2.6 at 250 ms | 32 | 32 | 3,936 | 729.69 ms |
| 0.2.7 idle at 2 s | 4 | 3 | 234 | 44.76 ms |
| 0.2.7 active at 250 ms | 32 | 4 | 312 | 153.95 ms |

Reproduce with `python3 tests/runtime_benchmark.py /path/to/old/agentboard/roster.py`.
Store/WAL changes invalidate the cache immediately; the two-second bound also
refreshes expired leases. Active streaming retains the 250 ms snapshot cadence.
Idle activity detection can take up to two seconds. Geometry polling falls from
500 ms to five seconds, with native move/resize events saving sooner.

## Acceptance boundary

`make check lint` runs offline. `tests/native_lifecycle_test.py` runs only in
omabox and uses a fake WoW window with real Electron and Hyprland. Browser checks
use synthetic conversations. No real desktop settings, provider sessions, or WoW
installation were modified. Real Wine/WoW window attribution and live provider
round trips still need owner acceptance.

T3 permission names were checked against the project's
[protocol definitions](https://github.com/pingdotgg/t3code/blob/main/packages/contracts/src/orchestration.ts).
The new-session default is explicitly shown as “Ask for approvals”; existing
threads retain the mode reported by T3.
