"""``agent-board``: the terminal side of Agent WoW mode.

Three surfaces, in order of what the project actually is:

* ``wow *``     the in-game half: install the addon, publish the snapshot, take
                the replies the client queued, watch, notify, manage hosts
* ``board`` and ``reply``
                the same roster from the terminal, without a game running
* ``overlay *`` the primary live gaming interface: a persistent badge and board
                with direct replies, queues, approvals, and input answers.

Nothing here writes to the game client beyond ``Interface/AddOns/AgentBoard``, and
nothing reads the client's files except its SavedVariables.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from . import backend, control, hosts, hypr, notify, roster, state as state_module, wowclient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OVERLAY_DIR = PROJECT_ROOT / "overlay"
STATE_DIR = state_module.STATE_DIR
OVERLAY_GEOMETRY = {"badge": (286, 46), "board": (760, 640)}

# One vocabulary: the roster owns which statuses exist, the addon and this CLI
# only decide how to draw them. A hard-coded list here is how "waiting" went
# missing from the terminal board for a day.
FILLED_DOTS = {"needs", "error", "working", "waiting", "reply"}
ORDER = sorted(roster.STATUS_ORDER, key=lambda status: roster.STATUS_ORDER[status])
DOTS = {status: ("\u25cf" if status in FILLED_DOTS else "\u25cb") for status in ORDER}


def _age(seconds: int) -> str:
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _find_electron() -> str | None:
    candidates = [
        Path("/usr/bin/electron43"),
        Path("/usr/bin/electron42"),
        Path("/usr/lib/electron43/electron"),
        Path("/usr/lib/electron42/electron"),
        Path("/usr/lib/electron/electron"),
        Path("/usr/bin/electron"),
        Path.home() / ".hermes" / "hermes-agent" / "apps" / "desktop" / "node_modules" / "electron" / "dist" / "electron",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return shutil.which("electron43") or shutil.which("electron42") or shutil.which("electron")


def cmd_board(args: argparse.Namespace) -> int:
    data = wowclient.aggregate_board(limit=args.limit, days=args.days)
    if args.json:
        print(json.dumps(data, indent=None if args.compact else 2))
        return 0

    sessions = data.get("sessions", [])
    if data.get("error"):
        print(data["error"], file=sys.stderr)
        return 1

    counts = data.get("counts", {})
    summary = " \u00b7 ".join(
        f"{counts[key]} {roster.STATUS_LABEL.get(key, key).lower()}" for key in ORDER if counts.get(key)
    )
    # The counts cover the rows the store handed back, not the whole history, and
    # this listing is a slice of those. Say so rather than implying both.
    window = f" · window: {args.days:g}d, {max(args.limit * 3, 60)} newest"
    print(f"hermes wow \u00b7 {summary or 'nothing running'}{window}")

    if not sessions:
        print("  (no sessions in the window)")
        return 0

    width = max(len(session.get("title") or "") for session in sessions)
    width = min(max(width, 24), 46)
    for session in sessions:
        dot = DOTS.get(session["status"], "\u25cb")
        title = session.get("title") or ""
        if len(title) > width:
            title = title[: width - 1] + "\u2026"
        trail = " / ".join(
            part for part in (session.get("profile") or session.get("source"), session.get("project")) if part
        )
        activity = f"  {session['activity']}" if session.get("activity") else ""
        print(
            f"  {dot} {session['status_label']:<10} {title:<{width}}  {trail:<28} {_age(session['age_s']):>5}"
            f"{activity}"
        )
    return 0


def cmd_reply(args: argparse.Namespace) -> int:
    text = " ".join(args.text).strip()
    if not text:
        print("refusing to send an empty reply", file=sys.stderr)
        return 2

    channel = args.channel
    if channel in ("auto", "backend"):
        try:
            backend.submit_reply(args.session_id, text)
            print(f"sent via the running backend -> {args.session_id}")
            return 0
        except Exception as exc:  # noqa: BLE001 - any failure means fall back
            if channel == "backend":
                print(f"backend reply failed: {exc}", file=sys.stderr)
                return 1
            print(f"backend unavailable ({exc}); falling back to a resumed CLI turn", file=sys.stderr)

    verdict = backend.submit_reply_cli(args.session_id, text)
    if verdict.get("ok") is False:
        print(f"the resumed turn refused it: {verdict.get('reason')}", file=sys.stderr)
        print(f"  log: {verdict.get('log_path')}", file=sys.stderr)
        return 1
    if verdict.get("ok") is None:
        print(f"sent via resumed CLI turn -> {args.session_id} (still running, log: {verdict.get('log_path')})")
        return 0
    print(f"sent via resumed CLI turn -> {args.session_id} (log: {verdict.get('log_path')})")
    return 0


def cmd_overlay(args: argparse.Namespace) -> int:
    if control.is_running():
        control.send(f"mode:{args.mode or 'toggle'}")
        print("overlay already running; sent the command")
        return 0

    electron = _find_electron()
    if not electron:
        print("no electron runtime found (Agent desktop app installs one)", file=sys.stderr)
        return 1
    if not (OVERLAY_DIR / "main.js").exists():
        print(f"overlay sources missing at {OVERLAY_DIR}", file=sys.stderr)
        return 1

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log = open(STATE_DIR / "overlay.log", "ab", buffering=0)

    env = wowclient.session_env()
    signature = hypr._instance_signature()
    if signature:
        env["HYPRLAND_INSTANCE_SIGNATURE"] = signature
    # Electron uses this switch internally to run its bundled Node runtime. If a
    # parent process exported it, the overlay would start as Node and reject all
    # browser flags instead of opening a window.
    env.pop("ELECTRON_RUN_AS_NODE", None)
    env["AGENT_BOARD_CLI"] = str(PROJECT_ROOT / "bin" / "agent-board")
    env["AGENT_BOARD_PYTHON"] = sys.executable
    env["AGENT_BOARD_MODE"] = args.mode or "badge"
    env["AGENT_BOARD_PIN"] = "0" if args.no_pin else "1"

    # Electron needs the platform as a launch flag; the env hint alone leaves it
    # reaching for X11, which fails outright when there is no DISPLAY.
    electron_args = [str(OVERLAY_DIR)]
    if os.environ.get("WAYLAND_DISPLAY"):
        electron_args.insert(0, "--ozone-platform=wayland")

    command = [electron, *electron_args]
    # Chromium can move its browser process into an app scope while leaving
    # helper processes behind. Start the whole overlay outside the bridge's
    # cgroup, so restarting the bridge cannot kill only its GPU/zygote children.
    if shutil.which("systemd-run") and Path(env.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "bus").exists():
        command = ["systemd-run", "--user", "--scope", "--collect", "--quiet",
                   f"--unit=agent-board-overlay-{uuid.uuid4().hex[:8]}", *command]
    subprocess.Popen(
        command,
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )

    if control.wait_until_up(timeout=args.wait):
        print(f"overlay up (mode: {args.mode or 'badge'}) \u00b7 log: {STATE_DIR / 'overlay.log'}")
        return 0

    print(f"overlay did not answer within {args.wait}s \u00b7 check {STATE_DIR / 'overlay.log'}", file=sys.stderr)
    return 1


def _control_command(command: str) -> int:
    try:
        reply = control.send(command)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(reply))
    return 0


def cmd_wow(args: argparse.Namespace) -> int:
    addon_dir = Path(args.addon_dir).expanduser() if args.addon_dir else None

    if args.wow_command == "status":
        targets = wowclient.find_addon_dirs()
        print(f"clients found    {len(targets)}")
        for target in targets:
            installed = (wowclient.addon_path(target) / "AgentBoard.toc").is_file()
            saved = wowclient.savedvars_path(target)
            print(f"  {target}")
            print(f"    addon         {'installed' if installed else 'not installed'}")
            print(f"    savedvariables {saved if saved else 'none yet (logs in once to create it)'}")
        chosen = wowclient.pick_addon_dir(addon_dir)
        print(f"target           {chosen if chosen else 'none (set AGENT_BOARD_ADDON_DIR)'}")
        return 0

    if args.wow_command == "install":
        target = wowclient.pick_addon_dir(addon_dir)
        if target is None:
            print("no WoW client found; pass --addon-dir", file=sys.stderr)
            return 1
        try:
            result = wowclient.install(target, force=args.force)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except OSError as exc:
            print(f"cannot write to {target}: {exc}", file=sys.stderr)
            print("  check ownership and permissions on the client folder", file=sys.stderr)
            return 1
        print(f"installed {len(result['files'])} files to {result['installed']}")
        print("restart the game client (or the AddOns folder is not rescanned mid-session)")
        published = wowclient.publish(
            target, limit=args.limit, days=args.days, hosts_enabled=not args.no_hosts
        )
        if published.get("error"):
            print(f"published an error instead of a board: {published['error']}", file=sys.stderr)
            return 1
        print(f"published {published['sessions']} sessions, {published['attention']} need you")
        return 0

    if args.wow_command == "publish":
        target = wowclient.pick_addon_dir(addon_dir)
        if target is None:
            print("no WoW client found; pass --addon-dir", file=sys.stderr)
            return 1
        try:
            result = wowclient.publish(
                target, limit=args.limit, days=args.days, hosts_enabled=not args.no_hosts
            )
        except OSError as exc:
            print(f"cannot write the snapshot: {exc}", file=sys.stderr)
            return 1
        if result.get("error"):
            print(f"published an error instead of a board: {result['error']}", file=sys.stderr)
            print(f"  written to {result['path']}", file=sys.stderr)
            return 1
        print(f"published {result['sessions']} sessions ({result['attention']} need you) to {result['path']}")
        return 0

    if args.wow_command == "inbox":
        result = wowclient.inbox(addon_dir=addon_dir, dispatch_replies=args.dispatch, channel=args.channel)
        if result.get("error"):
            print(result["error"], file=sys.stderr)
            return 1
        print(f"{len(result['entries'])} queued {'entry' if len(result['entries']) == 1 else 'entries'} in {result['path']}")
        for entry in result["entries"]:
            print(f"  {entry['seq']}  {entry['session_id']}  {entry['text'][:70]}")
        for outcome in result.get("results", []):
            print(f"  -> {outcome['outcome']}: {outcome['text'][:60]}")
        return 0

    if args.wow_command == "watch":
        reports = wowclient.watch(
            addon_dir=addon_dir,
            health_file=Path(args.health_file) if args.health_file else None,
            interval=args.interval,
            channel=args.channel,
            once=args.once,
            iterations=args.rounds,
            notify_desktop=not args.no_notify,
            notify_platforms=args.notify_platform or (),
            notify_enabled=not args.no_notify,
            limit=args.limit,
            days=args.days,
            hosts_enabled=not args.no_hosts,
        )
        if args.quiet:
            errors = [report.get("error") or report.get("publish_error") for report in reports]
            for error in errors:
                if error:
                    print(error, file=sys.stderr)
            return 1 if any(errors) else 0
        for report in reports:
            if report.get("error"):
                print(report["error"], file=sys.stderr)
                return 1
            published = report.get("published") or {}
            line = f"published {published.get('sessions', '?')} sessions"
            if published.get("new"):
                line += f" | {len(published['new'])} new since the last sync"
            if report.get("dispatched"):
                sent = ", ".join(f"{item['outcome']}:{item['text'][:30]}" for item in report["dispatched"])
                line += f" | dispatched {sent}"
            for result in (report.get("notify") or {}).get("results", []):
                line += f" | notified {result.get('channel')}:{result.get('sent')}"
            print(line)
        return 0

    if args.wow_command == "hosts":
        if args.host_command == "add":
            if not args.name:
                print("usage: agent-board wow hosts add <name> [--ssh target] [--hermes-home path]", file=sys.stderr)
                return 1
            host = hosts.add_host(args.name, ssh=args.ssh or "", hermes_home=args.hermes_home or "")
            print(json.dumps({"added": host.name, "ssh": host.target(), "config": str(hosts.CONFIG_PATH)}))
            return 0

        if args.host_command == "remove":
            if not args.name:
                print("usage: agent-board wow hosts remove <name>", file=sys.stderr)
                return 1
            print(json.dumps({"removed": hosts.remove_host(args.name)}))
            return 0

        if args.host_command == "test":
            configured = [h for h in hosts.load_hosts() if not args.name or h.name == args.name]
            if not configured:
                print("no hosts configured", file=sys.stderr)
                return 1
            results = hosts.fetch_all(configured, limit=args.limit, days=args.days, ttl=0)
            for result in results:
                if result.get("ok"):
                    print(f"  {result['name']}: ok, {len(result.get('sessions', []))} sessions in {result.get('took', '?')}s")
                else:
                    print(f"  {result['name']}: FAILED ({result.get('error')})")
            return 0 if all(result.get("ok") for result in results) else 1

        print(json.dumps(hosts.status_report(), indent=2))
        return 0

    if args.wow_command == "notify":
        if args.seed:
            print(json.dumps(notify.seed(roster.board(limit=args.limit, days=args.days).get("sessions", []))))
            return 0
        if args.status:
            print(json.dumps(notify.status(), indent=2))
            return 0

        sessions = roster.board(limit=args.limit, days=args.days).get("sessions", [])
        result = notify.transitions(
            sessions,
            dry_run=args.dry_run,
            desktop=not args.no_notify,
            platforms=args.notify_platform or (),
        )
        print(json.dumps(result, indent=2))
        return 0

    print("unknown wow subcommand", file=sys.stderr)
    return 2


def cmd_toggle(args: argparse.Namespace) -> int:
    if not control.is_running():
        return cmd_overlay(argparse.Namespace(mode=args.mode or "board", no_pin=False, wait=10.0))
    try:
        reply = control.send("toggle" if args.mode is None else f"mode:{args.mode}")
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    mode = str(reply.get("mode") or control.send("ping").get("mode") or "badge")
    # The overlay serializes its own mode changes and compositor placement.
    print(json.dumps(reply))
    return 0


def cmd_pin(args: argparse.Namespace) -> int:
    width, height = hypr.window_size_hint(args.mode)
    if args.width and args.height:
        width, height = args.width, args.height
    result = hypr.pin(width=width, height=height, margin=args.margin, anchor=args.anchor, return_focus=args.mode == "badge", force_focus=getattr(args, "return_focus", False), resize=not getattr(args, "no_resize", False), position=(args.x, args.y) if args.x is not None and args.y is not None else None)
    print(json.dumps(result))
    return 0 if result.get("pinned") else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    home = roster.hermes_home()
    db = home / "state.db"
    data = roster.board(limit=1000, days=3)
    print(f"HERMES_HOME      {home}")
    print(f"state.db         {'ok' if db.exists() else 'MISSING'} ({db})")
    print(f"sessions found   {len(data.get('sessions', []))} (last 3 days)")
    print(f"electron         {_find_electron() or 'not found'}")
    print(f"hyprland         {'ok' if hypr.available() else 'unavailable (no pinning)'}")
    found = backend.find_backend()
    print(f"backend          {found['url'] if found else 'not running (replies fall back to CLI)'}")
    print(f"overlay          {'running' if control.is_running() else 'stopped'}")
    print(f"socket           {control.socket_path()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-board", description="Agent in Azeroth: triage agents over a game.")
    sub = parser.add_subparsers(dest="command", required=True)

    from . import setup
    for action in ('setup', 'uninstall'):
        lifecycle = sub.add_parser(action, help='configure automatic startup' if action == 'setup' else 'remove automatic startup, preserve data')
        lifecycle.add_argument('--addon-dir')
        lifecycle.add_argument('--hermes-home')
        lifecycle.add_argument('--t3-home')
        lifecycle.add_argument('--yes', action='store_true')
        lifecycle.add_argument('--force', action='store_true')
        lifecycle.add_argument('--no-start', action='store_true', help=argparse.SUPPRESS)
        lifecycle.set_defaults(func=setup.command)
    status = sub.add_parser('status', help='show installation and background bridge health')
    status.set_defaults(func=lambda args: setup.status())
    update = sub.add_parser('update', help='update a managed installation')
    update.set_defaults(func=lambda args: __import__('agentboard.updater', fromlist=['command']).command(args))

    board = sub.add_parser("board", help="print the agent triage board")
    board.add_argument("--json", action="store_true", help="machine-readable roster (used by the overlay)")
    board.add_argument("--compact", action="store_true", help="single-line JSON")
    board.add_argument("--limit", type=int, default=15)
    board.add_argument("--days", type=float, default=3.0, help="how far back to look")
    board.set_defaults(func=cmd_board)

    reply = sub.add_parser("reply", help="answer a session from here")
    reply.add_argument("session_id")
    reply.add_argument("text", nargs="+")
    reply.add_argument("--channel", choices=["auto", "backend", "cli"], default="auto")
    reply.set_defaults(func=cmd_reply)

    overlay = sub.add_parser("overlay", help="start the floating overlay")
    overlay.add_argument("--mode", choices=["badge", "board"], default=None)
    overlay.add_argument("--no-pin", action="store_true", help="skip the Hyprland float/pin step")
    overlay.add_argument("--wait", type=float, default=10.0)
    overlay.set_defaults(func=cmd_overlay)

    toggle = sub.add_parser("toggle", help="show/hide the overlay (bind this to a key)")
    toggle.add_argument("--mode", choices=["badge", "board"], default=None)
    toggle.set_defaults(func=cmd_toggle)

    for name in ("show", "hide", "quit", "ping", "refresh", "badge"):
        cmd = sub.add_parser(name, help=f"send `{name}` to the running overlay")
        cmd.set_defaults(func=lambda _args, _name=name: _control_command(_name))

    unpin = sub.add_parser("unpin", help="unpin the overlay through Hyprland")
    unpin.set_defaults(func=lambda _args: print(json.dumps(hypr.unpin())) or 0)

    demo = sub.add_parser("demo", help="show fictional sessions in the overlay, for screenshots and recordings")
    demo.add_argument("--stop", action="store_true", help="end a running demo and go back to real sessions")
    demo.add_argument("--speed", type=float, default=1.0, help="play the scripted agents faster (2) or slower (0.5)")
    demo.set_defaults(func=lambda args: __import__("agentboard.demo", fromlist=["run"]).stop() if args.stop
                      else __import__("agentboard.demo", fromlist=["run"]).run(speed=args.speed))

    doctor = sub.add_parser("doctor", help="check the pieces this needs")
    doctor.set_defaults(func=cmd_doctor)

    wow = sub.add_parser("wow", help="the in-game side: install the addon, publish, take replies")
    wow.add_argument("wow_command", choices=["status", "install", "publish", "inbox", "watch", "notify", "hosts"])
    wow.add_argument("--addon-dir", default=None, help="Interface/AddOns of the client (auto-detected)")
    wow.add_argument("--health-file", help=argparse.SUPPRESS)
    wow.add_argument("--quiet", action="store_true", help="watch: suppress reports in service logs")
    wow.add_argument("--force", action="store_true", help="install: overwrite an existing copy")
    wow.add_argument("--dispatch", action="store_true", help="inbox: send the queued replies")
    wow.add_argument("--channel", choices=["auto", "backend", "cli"], default="auto")
    wow.add_argument("--interval", type=float, default=10.0, help="watch: seconds between rounds")
    wow.add_argument("--rounds", type=int, default=None, help="watch: stop after N rounds")
    wow.add_argument("--once", action="store_true", help="watch: one round")
    wow.add_argument("--limit", type=int, default=15)
    wow.add_argument("--days", type=float, default=3.0)
    wow.add_argument("--no-hosts", action="store_true", help="publish: skip remote hosts")
    # Second positional: `agent-board wow hosts add terra --ssh terra`
    wow.add_argument("host_command", nargs="?", choices=["list", "add", "remove", "test"], default="list")
    wow.add_argument("name", nargs="?", default="", help="hosts: the host name")
    wow.add_argument("--ssh", default="", help="hosts add: ssh target (user@host), defaults to the name")
    wow.add_argument("--hermes-home", default="", help="hosts add: HERMES_HOME on that machine")
    wow.add_argument("--no-notify", action="store_true", help="do not raise notifications")
    wow.add_argument(
        "--notify-platform",
        action="append",
        metavar="PLATFORM",
        help="also notify through a Agent messaging target (telegram, discord, ntfy); repeatable",
    )
    wow.add_argument("--dry-run", action="store_true", help="notify: report without sending or recording")
    wow.add_argument("--seed", action="store_true", help="notify: record the current state silently")
    wow.add_argument("--status", action="store_true", help="notify: show the watcher's state")
    wow.set_defaults(func=cmd_wow)

    pin = sub.add_parser("pin", help="float+pin the overlay through Hyprland (called by the overlay)")
    pin.add_argument("--mode", choices=["badge", "board"], default="board")
    pin.add_argument("--margin", type=int, default=24)
    pin.add_argument("--anchor", choices=["game", "other", "current", "focused"], default="game")
    pin.add_argument("--width", type=int, default=0)
    pin.add_argument("--x", type=int, default=None)
    pin.add_argument("--y", type=int, default=None)
    pin.add_argument("--height", type=int, default=0)
    pin.add_argument("--return-focus", action="store_true", help=argparse.SUPPRESS)
    pin.add_argument("--no-resize", action="store_true", help=argparse.SUPPRESS)
    pin.set_defaults(func=cmd_pin)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
