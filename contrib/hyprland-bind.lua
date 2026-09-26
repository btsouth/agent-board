-- Agent Board keybind (Omarchy / Hyprland, Lua config).
--
-- Append this to ~/.config/hypr/bindings.lua, then reload with `hyprctl reload`
-- (or run the file's command once to check it works first).
--
-- SUPER ALT C is free on the tested Omarchy install; if you have already bound
-- it, pick any other chord. The toggle starts the overlay when it is not up and
-- flips badge <-> board when it is.
--
-- Nothing here is required for the overlay itself: `agent-board toggle` works
-- from any shell, so a keybind is only the convenient front door.

-- agent-board:bindings:start
o.bind("SUPER + ALT + C", "Agent Board", "agent-board toggle")
-- agent-board:bindings:end
