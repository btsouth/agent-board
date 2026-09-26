# Live Agent Board overlay

The overlay is the primary WoW interface to T3 Code and Hermes. Its private
Unix socket connects to the bridge, which attaches to the providers already
running on this machine. It does not require the retired native addon UI.

`Super+Alt+C` toggles between badge and board. Click the badge to open the board;
Escape closes it and returns keyboard focus to the previous app/game. The board
focuses the composer on opening. Drag its header, or use Hyprland's window-move
gesture on the badge. Each shape remembers its position across toggles and
restarts; saved positions are clamped into connected displays.

The badge shows running sessions and the latest reply's title/age. Attention
appears only for a concrete pending approval or question, never because an old
assistant message contains a question. Queued messages are counted separately.

While a session works, Enter queues a message for its next turn. Use **Send now**
on a queued row or Ctrl+Enter in the composer to steer immediately. With an
empty composer, Ctrl+Enter sends the oldest queued message. Shift+Enter inserts
a newline. Queued messages can be edited or removed. Stop pauses unsent messages
so they cannot immediately restart the agent.

Drafts and the queue are persisted in the overlay's local app storage. Automatic
queue draining continues while the badge is showing, but requires the overlay
to be running. It resumes after reopening. Failed or ambiguous sends remain
visible for review; check the conversation before retrying an uncertain delivery.
The bridge never launches a duplicate Hermes CLI agent after an uncertain live
RPC response. T3 uses its own turn-start steering path; Hermes uses session.steer.

Approvals and questions have dedicated controls, including multi-question forms.
The transcript shows up to 60 recent messages, preserves the reading position,
and follows new replies when scrolled to the bottom. Each message can be copied.

The overlay follows the live Omarchy palette. On Hyprland it uses native Wayland
and compositor-controlled bounds, with serialized transitions and a transparent
native background. Its process scope is separate from the bridge so restarting
the bridge does not kill Electron's helper processes. Electron 43/42 installed
on the system takes priority over Hermes's bundled runtime.

```bash
agent-board overlay --mode board
agent-board toggle
agent-board badge
agent-board quit
```

The service starts the overlay when WoW appears and hides its game-aware window
when WoW exits. A manually opened desktop overlay stays available without WoW.

The installed Hyprland rule disables compositor animation, blur, dimming, border,
and shadow for this app only. See `contrib/hyprland-window.lua`. Live desktop
verification was performed on Hyprland with 125% display scaling; other Wayland
compositors are not covered by that verification.
