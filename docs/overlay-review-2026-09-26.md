# Agent Board UI review, 2026-09-26

Review baseline: `fc722c95bacd2d6fd7064de9a0b6cbbaa6686afd`.
The fixes are included in version 0.2.6. The older `hermes-wow` project was
left unchanged.

## Findings and fixes

- Streaming replaced the entire Markdown body on every update. Completed blocks
  now retain their DOM nodes. Growing code blocks keep their horizontal scroll
  and copy-button focus, and copying reads the current code.
- Transcript reconciliation discarded subsequent messages when an earlier row
  changed or left the 60-message window. Messages now reconcile by identity,
  preserving expanded tool groups and the reader's visible message position.
  Pending scroll callbacks cannot move a newly selected conversation.
- Tool details with unchanged IDs could display old text. Both the summary and
  expanded text now update. The working indicator retains its node between
  updates and disappears while its provider is unavailable.
- Unread flags and snippets were missing from sidebar cache invalidation.
  Updated rows now reflect read state, grouping, preview text and provider labels.
  Search distinguishes no matches from an empty board without resetting the
  conversation scroll position. Initial selection follows the visible grouping.
- The default sidebar crowded the transcript at smaller window sizes. It now
  scales between 216 and 286 pixels. Actions wrap, prose and tool text wrap,
  and question forms, queued messages and long composers have bounded heights.
  Structured question forms replace the disabled reply composer to leave room
  for the transcript at the 640 by 460 minimum.
- Secondary text and light-theme status colors needed more contrast. Adjusted
  their theme-derived colors and added reduced-motion support.
- Bridge/provider failures now have a persistent notice and disable unavailable
  actions. Drafts remain editable, ordinary messages can queue offline, and
  Send now preserves the draft until reconnection. Empty sends are disabled.
  Action errors remain visible until replaced or the selected session changes.
- New-session keyboard submission could create multiple requests while one was
  pending. A submission guard now prevents that and retains the previous
  session and draft while awaiting the result. Failed starts retain the prompt.
- Added modal focus containment and restoration, keyboard project selection,
  keyboard badge activation, accessible input names, visible focus indicators,
  keyboard-accessible copy buttons and selected question-option styling.
  Session navigation brings the focused row into view. Automatic mark-read
  does not run while the reader is above the latest message.

## Verification

- `make check` passed inside omabox with an isolated copy of the checkout.
  `make lint` and `git diff --check` passed.
- Expanded `tests/overlay_ui_test.cjs` passed inside omabox using Chromium and
  the existing Playwright installation. It covers queues, independent drafts,
  structured answers, badge state, menus, streaming DOM stability, code scroll,
  sliding transcript anchors, stale tool updates, unread grouping, offline
  controls, modal focus/project keys, duplicate submission and reduced motion.
- Screenshot and geometry checks covered 640 by 460, 980 by 720 and 1280 by 900
  in dark and light themes, plus question and new-session forms at minimum size.
  No renderer errors were reported.
- The T3 collaborative preview displayed fictional demo sessions served from
  devbox. Browser tests and screenshots used synthetic provider responses.
- Initial devbox checks stopped at the missing `lua5.1` executable. The complete
  suite was then run successfully in omabox, including Lua and Python gates.

The UI runner accepts `UI_OUTPUT` to retain screenshots; set `PLAYWRIGHT_MODULE`
when Playwright is installed outside this repository. On this machine, run it
through omabox with the Playwright directory mounted read-only.

## Remaining acceptance

The release candidate was not installed or tested over a real game during
this review.
No messages, approvals or stop commands were sent to real provider sessions.
A live T3/Hermes streaming round trip, native Electron window behavior and
in-game focus/visibility still need acceptance with the candidate installed.
The native WoW addon was covered by existing offline gates, not visually
reviewed in the client. Full transcript pagination remains outside this change.
