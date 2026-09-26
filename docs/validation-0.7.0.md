# 0.7.0 validation

Validated on 2026-09-19. No prompts were sent to real sessions and no real turns were interrupted.

The release gates are `make check lint package`. They cover the existing addon and bridge behavior plus bootstrap ownership, setup rollback, service health, duplicate watchers, and updates using a temporary Git repository. Packaging refused the unreleased changelog before the release was dated.

Removing service verification, update rollback, and onboarding behavior in isolated test copies caused their relevant gates to fail. First-run and old-snapshot previews were rendered and inspected.

## Live Linux checks

- Installed into the managed application directory and the detected Classic beta addon directory.
- Verified the systemd user service was active, enabled at login, and publishing a healthy snapshot, with no restarts.
- Verified the generated unit with systemd-analyze.
- Uninstalled automatic startup and the launcher. Confirmed the addon, managed application and SavedVariables were preserved.
- Reran the bootstrap script and verified healthy automatic startup returned with SavedVariables unchanged.
- Started a second watcher and confirmed it was rejected while the managed service remained running.

Update checkout and rollback are tested with isolated repositories. Automatic startup is enabled and has been started successfully, but a fresh desktop login has not been exercised.

## In-game checks still required

Restart WoW, enable HermesAI, open `/hermesai`, and press Sync. Check the first-run and stale-snapshot guidance, then follow the [0.6.0 in-game checklist](validation-0.6.0.md#in-game-checks-still-required) for the board and session controls.

This is a preview for Classic beta interface 16001. Offline gates and previews do not establish real-client compatibility. Other WoW clients and bridge operating systems remain unverified.
