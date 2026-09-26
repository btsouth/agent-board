#!/usr/bin/env python3
"""Build the release zip.

Every channel wants the same shape: a zip whose single top-level folder is the
addon, containing `AgentBoard/AgentBoard.toc`. Zipping `addon/` gives neither - it
nests the path one level too deep, which the game client and every installer
reject, and it sweeps in `addon/__audit_copy__/`, which is a whole second copy of
the addon with its own `AgentBoard.toc` inside it.

What ships is therefore derived, not globbed: the toc, the files the toc lists,
and `Bindings.xml` (which the client loads on its own, so it is deliberately not
listed). Two things are then refused rather than trusted:

* a version the changelog has not recorded, or one still marked unreleased
* a `Data.lua` carrying session data - the file is generated per machine, and a
  release that ships somebody's roster is a privacy leak, not a packaging bug

Run: python3 scripts/package_addon.py [--out dist]
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDON_NAME = "AgentBoard"
DEFAULT_ADDON = ROOT / "addon" / ADDON_NAME
CHANGELOG = ROOT / "CHANGELOG.md"

# The client loads this by name even though the toc does not list it, which is why
# the toc should not list it either: naming it there loads it twice.
AUTO_LOADED = ("Bindings.xml",)

# Read by name too, but only once `make icon` has produced them: the addon-list
# icon and its large twin for store pages.
EXTRA_IF_PRESENT = ("icon.tga", "icon-128.png")


class PackagingError(RuntimeError):
    """Something that must not reach a release."""


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def toc_directives(text: str) -> dict[str, str]:
    directives: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("##") and ":" in line:
            key, value = line[2:].split(":", 1)
            directives[key.strip()] = value.strip()
    return directives


def toc_listed_files(text: str) -> list[str]:
    listed = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            listed.append(stripped)
    return listed


def addon_version(addon_dir: Path) -> str:
    text = read(addon_dir / f"{ADDON_NAME}.toc")
    version = toc_directives(text).get("Version")
    if not version:
        raise PackagingError(f"{addon_dir / (ADDON_NAME + '.toc')} declares no Version")
    return version


def changelog_version() -> tuple[str, str]:
    """The newest version the changelog records, and its date or `unreleased`."""
    match = re.search(r"^## \[([^\]]+)\]\s*-\s*(.+?)\s*$", read(CHANGELOG), re.MULTILINE)
    if not match:
        raise PackagingError("CHANGELOG.md has no version heading")
    return match.group(1), match.group(2)


def packaged_names(addon_dir: Path) -> list[str]:
    """The file names the release carries, in the order it carries them."""
    toc = addon_dir / f"{ADDON_NAME}.toc"
    names = [toc.name, *toc_listed_files(read(toc)), *AUTO_LOADED]
    names.extend(name for name in EXTRA_IF_PRESENT if (addon_dir / name).is_file())
    return names


def manifest(addon_dir: Path) -> list[Path]:
    """Every file the release carries, as paths."""
    files: list[Path] = []
    for name in packaged_names(addon_dir):
        path = addon_dir / name
        if not path.is_file():
            raise PackagingError(f"{name} is required by the release but missing from {addon_dir}")
        files.append(path)
    return files


def check_data_is_empty(addon_dir: Path) -> None:
    """`Data.lua` is written per machine by the bridge; a release must not carry one."""
    data = addon_dir / "Data.lua"
    if not data.is_file():
        raise PackagingError("Data.lua is missing: the toc lists it, so the client needs it")
    body = read(data)
    if "|" in body or ";;" in body:
        raise PackagingError(
            "Data.lua carries a payload. It is generated per machine and must ship "
            "empty, or the release hands every downloader the author's own sessions."
        )


def build(out_dir: Path, addon_dir: Path = DEFAULT_ADDON) -> tuple[Path, list[str]]:
    """Write the zip. Returns its path and the entry names inside it."""
    version = addon_version(addon_dir)
    recorded, when = changelog_version()

    if version != recorded:
        raise PackagingError(
            f"the toc says {version} and the changelog's newest version is {recorded}: "
            "record the release before packaging it"
        )
    if when.lower() == "unreleased":
        raise PackagingError(f"the changelog still calls {recorded} unreleased")

    check_data_is_empty(addon_dir)

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{ADDON_NAME}-{version}.zip"

    entries: list[str] = []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in manifest(addon_dir):
            # `AgentBoard/Data.lua`, not `addon/AgentBoard/Data.lua`: the client adds the
            # zip's top level to Interface/AddOns.
            name = f"{ADDON_NAME}/{path.name}"
            bundle.write(path, name)
            entries.append(name)

    return zip_path, entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(ROOT / "dist"), help="where to write the zip")
    parser.add_argument("--addon", default=str(DEFAULT_ADDON), help="the addon folder to package")
    args = parser.parse_args()

    try:
        zip_path, entries = build(Path(args.out), Path(args.addon))
    except PackagingError as exc:
        print(f"refusing to package: {exc}", file=sys.stderr)
        return 1

    print(f"packaged {len(entries)} files -> {zip_path}")
    for name in entries:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
