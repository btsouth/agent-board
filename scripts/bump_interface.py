#!/usr/bin/env python3
"""Move the interface pin, in both places it is written down.

The toc's `## Interface` is what the client compares against its own build, and
`tests/addon_checks.py` holds the expected value deliberately: a lint that read the
toc would be checking the toc against itself, which is the kind of gate this
project has already removed once. So two copies is the right shape - and also
exactly how they drift, which is why they move together here rather than by hand.

Read the number in game, then run it:

    /run print(GetBuildInfo())     # -> 1.60.1 69913 Sep 17 2026 16001
    make interface IFACE=16001

The third place the number appears is prose: `docs/design/wow-mode.md` records the
build it was confirmed against, which is a fact rather than a setting, so this
prints the line to update instead of rewriting it.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOC = ROOT / "addon" / "AgentBoard" / "AgentBoard.toc"
LINT = ROOT / "tests" / "addon_checks.py"


def replace(path: Path, pattern: str, replacement: str) -> tuple[bool, str]:
    """Rewrite one line. Returns (changed, the line as it now reads)."""
    text = path.read_text(encoding="utf-8")
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        raise SystemExit(f"cannot find the interface pin in {path}")

    current = match.group(0)
    if current == replacement:
        return False, current

    path.write_text(re.sub(pattern, replacement, text, count=1, flags=re.MULTILINE), encoding="utf-8")
    return True, replacement


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("interface", help="the build number from /run print(GetBuildInfo()), e.g. 16001")
    args = parser.parse_args()

    if not args.interface.isdigit() or not 4 <= len(args.interface) <= 6:
        print(f"{args.interface!r} is not an interface number (four to six digits)", file=sys.stderr)
        return 1

    changed = []
    for path, pattern, replacement in (
        (TOC, r"^## Interface: \d+$", f"## Interface: {args.interface}"),
        (LINT, r'^EXPECTED_INTERFACE = "\d+"$', f'EXPECTED_INTERFACE = "{args.interface}"'),
    ):
        did, line = replace(path, pattern, replacement)
        changed.append(did)
        print(f"{'pinned ' if did else 'already'} {path.relative_to(ROOT)}: {line}")

    if not any(changed):
        print(f"\nnothing to do: the project is already pinned at {args.interface}")
        return 0

    print(
        "\nrecord the build in docs/design/wow-mode.md, so the pin has a source:\n"
        '  `/run print(GetBuildInfo())` returned `<paste the whole line>`.\n'
        "Then run the gates: make check"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
