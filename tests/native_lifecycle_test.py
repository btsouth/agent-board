#!/usr/bin/env python3
"""Run only inside omabox: real Electron, Hyprland, and a fake WoW window."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentboard import control, hypr, updater, wowclient

if Path.home() != Path('/home/sbx'):
    raise SystemExit('Run this test inside omabox, never on the real desktop.')
root = Path(__file__).resolve().parent.parent
children = []

def wait_for(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value: return value
        except RuntimeError:
            pass
        time.sleep(.15)
    raise AssertionError('Timed out waiting for lifecycle transition')

def overlay_window():
    return next((client for client in hypr.clients() if 'agent-board' in client.get('class', '').lower()), None)

def game_window():
    child = subprocess.Popen(['bash', '-c', 'exec -a WowClassic.exe foot --app-id wow-fixture --title "World of Warcraft" sleep 180'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    children.append(child)
    wait_for(lambda: any(row.get('pid') == child.pid for row in hypr.clients()))
    return child

try:
    addon = Path.home() / 'game/Interface/AddOns'
    addon.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'AGENT_BOARD_PROVIDERS': ''}
    log = (Path.home() / 'native-watch.log').open('w')
    watcher = subprocess.Popen([sys.executable, '-c', 'from pathlib import Path; from agentboard.wowclient import watch; import sys; watch(addon_dir=Path(sys.argv[1]), notify_enabled=False, hosts_enabled=False, interval=1)', str(addon)], cwd=root, env=env, stdout=log, stderr=log)
    children.append(watcher)
    fake = game_window()
    wait_for(control.is_running)
    wait_for(overlay_window)
    status = control.send('ping')
    assert status['visibility'] == 'automatic', status
    original_pid = overlay_window()['pid']
    control.send('show')
    wait_for(lambda: control.send('ping')['visibility'] == 'manual')
    fake.terminate(); fake.wait(timeout=3)
    wait_for(lambda: not control.send('ping')['game']['running'])
    assert overlay_window()['pid'] == original_pid, 'Manual overlay was replaced or hidden'
    control.send('automatic')
    wait_for(lambda: not overlay_window())
    fake = game_window()
    wait_for(overlay_window)
    assert overlay_window()['pid'] == original_pid
    control.send('pause')
    wait_for(lambda: not overlay_window())
    fake.terminate(); fake.wait(timeout=3)
    wait_for(lambda: control.send('ping')['visibility'] == 'automatic')
    fake = game_window()
    wait_for(overlay_window)
    control.send('show')
    wait_for(lambda: overlay_window() and overlay_window()['size'][0] >= 640)
    updater._restart_overlay(root, control.send('ping'), 'v' + wowclient.BRIDGE_VERSION)
    wait_for(lambda: overlay_window() and overlay_window()['pid'] != original_pid)
    status = control.send('ping')
    assert status['version'] == wowclient.BRIDGE_VERSION, status
    assert status['visibility'] == 'manual' and status['mode'] == 'board', status
    print(json.dumps({'ok': True, 'checks': ['automatic launch', 'manual survives game exit', 'automatic hides', 'restart restores', 'pause resets on exit', 'update restarts overlay preserving mode and policy'], 'version': status['version']}))
finally:
    try: control.send('quit')
    except RuntimeError: pass
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()
            try: child.wait(timeout=3)
            except subprocess.TimeoutExpired: child.kill(); child.wait()
