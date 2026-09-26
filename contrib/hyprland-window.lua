-- Optional Omarchy/Hyprland Lua rule for the live overlay.
-- Add to a user-loaded Hyprland Lua file (never to packaged Omarchy defaults).
o.window({ class = "^agent-board-overlay$" }, {
  tag = "-default-opacity",
  float = true,
  no_anim = true,
  no_dim = true,
  no_follow_mouse = true,
  rounding = 0,
  border_size = 0,
  no_blur = true,
  no_shadow = true,
  opacity = "1 1",
})
