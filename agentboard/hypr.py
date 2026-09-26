"""Hyprland IPC: float + pin + place an overlay window.

Electron's ``alwaysOnTop`` is compositor-owned on native Wayland, and Hyprland
tiles a new toplevel by default, so the overlay asks the compositor directly,
exactly like Agent's own HUD does for its window. Nothing here is needed on a
non-Hyprland session: the calls no-op with a reason.

Two grammars exist for the same dispatch. Hyprland 0.54 with a hyprlang config
takes ``dispatch pin address:0x…``; a Lua config (0.55+, which is what Omarchy
now ships) parses the argument as Lua and rejects that outright. Which one a
session speaks is only discoverable by trying, so every dispatch goes through
``_dispatch`` with the legacy form first and the ``hl.dsp`` form as the fallback.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

DEFAULT_CLASS_HINTS = ("agent-board",)


def _instance_signature() -> str | None:
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if signature:
        return signature

    # Launched from a shell that never inherited the var (a systemd unit, ssh):
    # the socket directory is the source of truth.
    runtime = Path(f"/run/user/{os.getuid()}/hypr")
    if not runtime.is_dir():
        return None
    entries = sorted(entry.name for entry in runtime.iterdir() if entry.is_dir())
    return entries[-1] if entries else None


def hyprctl(args: list[str], *, timeout: float = 5.0) -> str | None:
    if not shutil.which("hyprctl"):
        return None

    env = dict(os.environ)
    signature = _instance_signature()
    if signature:
        env["HYPRLAND_INSTANCE_SIGNATURE"] = signature

    try:
        proc = subprocess.run(
            ["hyprctl", *args], capture_output=True, text=True, timeout=timeout, env=env
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if proc.returncode != 0:
        # hyprctl exits non-zero for a refused dispatch; stdout carries the why,
        # which the grammar probe needs.
        return (proc.stdout or proc.stderr or "").strip() or None
    return proc.stdout


def available() -> bool:
    return bool(_instance_signature()) and hyprctl(["version", "-j"]) is not None


def clients() -> list[dict]:
    raw = hyprctl(["clients", "-j"])
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def find_window(*hints: str, timeout: float = 6.0) -> dict | None:
    """The overlay's own toplevel, once the compositor knows about it."""
    wanted = [hint.lower() for hint in (hints or DEFAULT_CLASS_HINTS)]
    deadline = time.time() + timeout

    while time.time() < deadline:
        for client in clients():
            haystack = " ".join(
                str(client.get(key, "")) for key in ("class", "initialClass", "title")
            ).lower()
            if any(hint in haystack for hint in wanted):
                return client
        time.sleep(0.25)
    return None


def _dispatch(legacy: str, lua: str) -> tuple[bool, str]:
    """Try a dispatch in both grammars. Returns (applied, command_that_worked)."""
    for command in (legacy, lua):
        reply = hyprctl(["dispatch", command])

        if reply is None:
            continue

        text = reply.strip()
        if text == "ok":
            return True, command
        # "error: [string …]:1: ')' expected near 'address'" is the Lua parser
        # rejecting the legacy form; "Invalid dispatcher" is the reverse. Both
        # mean "wrong grammar", not "wrong window".
    return False, ""


def _monitors() -> list[dict]:
    raw = hyprctl(["monitors", "-j"])
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _monitor_for_client(client: dict) -> dict:
    """The monitor a client is actually on.

    Not the focused monitor: the overlay always has a toplevel of its own, and
    anchoring it with another monitor's width is how it lands off-screen (a
    window on the left-hand display placed at the right-hand display's x).
    Hyprland's client `monitor` is the monitor id; it need not match
    the monitor's index in the returned array.
    """
    monitors = _monitors()
    fallback = {
        "name": "",
        "width": 2560,
        "height": 1440,
        "scale": 1.0,
        "x": 0,
        "y": 0,
        "reserved": [0, 0, 0, 0],
    }

    if not monitors:
        return fallback

    index = client.get("monitor")
    for monitor in monitors:
        if monitor.get("id") == index:
            return monitor
    if isinstance(index, int) and 0 <= index < len(monitors):
        return monitors[index]

    return next((m for m in monitors if m.get("focused")), monitors[0])


def _game_target() -> dict | None:
    """The fullscreen app running right now (the game): its monitor and workspace.

    A pinned window is only visible on its own workspace, so anchoring to the
    game means following it to the workspace it is fullscreen on, not just
    computing a corner of the right display.
    """
    monitors = _monitors()
    candidates = []
    for client in clients():
        if not client.get("mapped", True):
            continue
        class_name = str(client.get("class", "")).lower()
        title = str(client.get("title", "")).lower()
        if any(hint in class_name for hint in DEFAULT_CLASS_HINTS):
            continue  # never anchor to ourselves
        is_wow = title == "world of warcraft" or title.startswith("world of warcraft ")
        if client.get("fullscreen") or is_wow:
            candidates.append(client)

    # Prefer a true fullscreen client, then the WoW title. WoW under Wine often
    # uses a borderless windowed surface, which Hyprland reports as fullscreen=0.
    candidates.sort(key=lambda client: (not str(client.get("title", "")).lower().startswith("world of warcraft"), not bool(client.get("fullscreen"))))
    for client in candidates:
        index = client.get("monitor")
        monitor = next((item for item in monitors if item.get("id") == index), None)
        if monitor is None and isinstance(index, int) and 0 <= index < len(monitors):
            monitor = monitors[index]
        if monitor is None:
            continue
        workspace = client.get("workspace") or {}
        return {
            "monitor": monitor,
            "workspace_id": workspace.get("id"),
            "class": str(client.get("class", "")),
        }
    return None


def _target_monitor(client: dict, anchor: str, game: dict | None) -> dict:
    if anchor == "game" and game:
        return game["monitor"]

    if anchor == "other":
        # The display without the game on it: for a live readout that must never
        # cover the game, and on a two-monitor desk that is simply where it goes.
        game_name = (game or {}).get("monitor", {}).get("name")
        for monitor in _monitors():
            if monitor.get("name") and monitor.get("name") != game_name:
                return monitor
        return _monitor_for_client(client)

    # "focused" is for the odd case where the overlay should live on the display
    # the user is working on, not the one it happened to open on.
    if anchor == "focused":
        monitors = _monitors()
        return next((m for m in monitors if m.get("focused")), _monitor_for_client(client))
    return _monitor_for_client(client)


def _follow_workspace(address: str, client: dict, game: dict | None) -> dict:
    """Put the overlay on the game's workspace so a pin is actually visible."""
    workspace_id = (game or {}).get("workspace_id")
    current = (client.get("workspace") or {}).get("id")

    if workspace_id is None or current == workspace_id:
        return client

    _dispatch(
        f"movetoworkspacesilent {workspace_id},{address}",
        f'hl.dsp.window.move({{ workspace = "{workspace_id}", window = "{address}" }})',
    )
    return find_window(timeout=2.0) or client


def _place(
    address: str, width: int, height: int, margin: int, client: dict, anchor: str = "game", resize: bool = True, position: tuple[int, int] | None = None
) -> dict:
    game = _game_target() if anchor in ("game", "other") else None
    if anchor == "game":
        # Only the game anchor moves workspaces: "other" means the other
        # display's current workspace, whatever that happens to be.
        client = _follow_workspace(address, client, game)
    monitor = _target_monitor(client, anchor, game)
    if position is not None:
        px, py = position
        monitor = next((item for item in _monitors()
                        if item.get("x", 0) <= px < item.get("x", 0) + item.get("width", 0) / (item.get("scale") or 1)
                        and item.get("y", 0) <= py < item.get("y", 0) + item.get("height", 0) / (item.get("scale") or 1)), monitor)
    scale = float(monitor.get("scale") or 1.0) or 1.0
    reserved = monitor.get("reserved") or [0, 0, 0, 0]
    top_inset = int(reserved[1] or 0) if len(reserved) > 1 else 0

    # hyprctl reports physical pixels with `scale` alongside; positions and
    # geometry are logical, so the anchor has to be divided by the scale or the
    # bar lands off-screen on a HiDPI display.
    logical_width = float(monitor.get("width") or 2560) / scale
    origin_x = float(monitor.get("x") or 0)
    origin_y = float(monitor.get("y") or 0)

    x = int(origin_x + logical_width - width - margin)
    y = int(origin_y + top_inset + margin)
    if position is not None:
        logical_height = float(monitor.get("height") or 1440) / scale
        x = int(max(origin_x, min(position[0], origin_x + logical_width - width)))
        y = int(max(origin_y + top_inset, min(position[1], origin_y + logical_height - height)))
    resized = False
    if resize and width > 0 and height > 0:
        resized, _ = _dispatch(
            f"resizewindowpixel exact {width} {height},{address}",
            f'hl.dsp.window.resize({{ action = "exact", x = {width}, y = {height}, window = "{address}" }})',
        )
    moved, _ = _dispatch(
        f"movewindowpixel exact {x} {y},{address}",
        f'hl.dsp.window.move({{ action = "exact", x = {x}, y = {y}, window = "{address}" }})',
    )
    return {
        "resized": resized,
        "moved": moved,
        "x": x,
        "y": y,
        "monitor": monitor.get("name", ""),
        "logical_width": int(logical_width),
    }


def pin(*, width: int, height: int, margin: int = 24, timeout: float = 6.0, anchor: str = "game", return_focus: bool = False, force_focus: bool = False, resize: bool = True, position: tuple[int, int] | None = None) -> dict:
    """Float, pin, and place the overlay. Returns a small result report."""
    if not available():
        return {"pinned": False, "reason": "not a Hyprland session (or hyprctl unavailable)"}

    client = find_window(timeout=timeout)
    if client is None:
        return {"pinned": False, "reason": "overlay window not found in hyprctl clients"}

    address = f"address:{client['address']}"
    floated, _ = (True, "already floating") if client.get("floating") else _dispatch(
        f"setfloating {address}",
        f'hl.dsp.window.float({{ action = "enable", window = "{address}" }})',
    )
    pinned, _ = (True, "already pinned") if client.get("pinned") else _dispatch(
        f"pin {address}",
        f'hl.dsp.window.pin({{ action = "enable", window = "{address}" }})',
    )
    placement = _place(address, width, height, margin, client, anchor, resize=resize, position=position)
    if return_focus:
        restore_focus(client, force=force_focus)

    return {
        "pinned": pinned,
        "floating": floated,
        "placement": placement,
        "address": client["address"],
        "class": client.get("class", ""),
        "monitor": placement.get("monitor", ""),
    }


def restore_focus(overlay: dict, *, force: bool = False) -> None:
    """Collapsing must return keyboard input to the game, not the tiny badge."""
    try:
        active = json.loads(hyprctl(["activewindow", "-j"]) or "{}")
    except ValueError:
        return
    if not force and active.get("address") != overlay.get("address"):
        return
    candidates = [client for client in clients()
                  if client.get("address") != overlay.get("address")
                  and client.get("mapped", True) and not client.get("hidden")
                  and (client.get("workspace") or {}).get("id") == (overlay.get("workspace") or {}).get("id")]
    candidates.sort(key=lambda client: (not str(client.get("title", "")).lower().startswith("world of warcraft"),
                                       client.get("focusHistoryID", 999)))
    if candidates:
        address = f"address:{candidates[0]['address']}"
        _dispatch(f"focuswindow {address}", f'hl.dsp.focus({{ window = "{address}" }})')


def unpin(*, timeout: float = 3.0) -> dict:
    client = find_window(timeout=timeout)
    if client is None:
        return {"unpinned": False, "reason": "overlay window not found"}

    address = f"address:{client['address']}"
    applied, _ = _dispatch(
        f"unpin {address}",
        f'hl.dsp.window.pin({{ action = "disable", window = "{address}" }})',
    )
    return {"unpinned": applied}


def window_size_hint(mode: str) -> tuple[int, int]:
    """Badge and board geometries; the same numbers main.js sizes the window to."""
    return (286, 46) if mode == "badge" else (760, 640)
