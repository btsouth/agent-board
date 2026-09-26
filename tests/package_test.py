#!/usr/bin/env python3
"""The release artifact: what a zip is allowed to contain, and what it must refuse.

The two mistakes this catches are the ones that survive a green test suite because
they are about the *bundle*, not the code: a zip rooted one folder too deep (the
client and every installer reject it), and `addon/__audit_copy__/` riding along -
a second copy of the addon with its own `AgentBoard.toc`, which is exactly the sort
of thing that ships once and confuses people for years.

The strongest assertion here is the last one: the *shipped* artifact is unpacked
and put through the release lint, so the zip is proven to be the thing that passed.

Run: python3 tests/package_test.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import package_addon  # noqa: E402

ADDON = ROOT / "addon" / "AgentBoard"
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + label + (f" ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def build_into(directory: Path) -> tuple[Path, list[str]]:
    return package_addon.build(directory, ADDON)


with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "dist"
    zip_path, entries = build_into(out)

    check("a zip is written", zip_path.is_file(), str(zip_path))
    check("it is named for the addon and its version",
          zip_path.name == f"AgentBoard-{package_addon.addon_version(ADDON)}.zip", zip_path.name)

    with zipfile.ZipFile(zip_path) as bundle:
        names = bundle.namelist()
        check("the zip carries no directory entries of its own",
              not any(name.endswith("/") for name in names), str(names[:3]))

        check("both shipped icons are present", {"AgentBoard/icon.tga", "AgentBoard/icon-128.png"}.issubset(names))
        check("bindings are bundled once", names.count("AgentBoard/Bindings.xml") == 1)
        top_level = {name.split("/", 1)[0] for name in names}
        check("everything is under exactly one top-level folder",
              top_level == {package_addon.ADDON_NAME}, ", ".join(sorted(top_level)))

        # The client looks for AddOns/<folder>/<folder>.toc. One level deeper and
        # nothing loads at all.
        check("the toc is at the root of that folder",
              f"{package_addon.ADDON_NAME}/{package_addon.ADDON_NAME}.toc" in names, str(sorted(names)[:4]))

        expected = {f"{package_addon.ADDON_NAME}/{name}" for name in package_addon.packaged_names(ADDON)}
        check("it carries the toc, what it lists, and what the client loads by name - nothing else",
              set(names) == expected,
              "extra: " + ", ".join(sorted(set(names) - expected))
              + " missing: " + ", ".join(sorted(expected - set(names))))

        check("the audit copy of the addon is not in it",
              not any("__audit_copy__" in name for name in names), str(names))
        check("no tests, docs, scripts or Python are in it",
              not any(name.endswith((".py", ".md", ".json", ".yml")) for name in names),
              ", ".join(sorted(names)))

        # `Data.lua` is generated per machine by the bridge. Shipping one is a
        # privacy leak: every downloader would open the author's own sessions.
        packaged_data = bundle.read(f"{package_addon.ADDON_NAME}/Data.lua").decode("utf-8")
        check("the packaged Data.lua carries no payload",
              "|" not in packaged_data and ";;" not in packaged_data, packaged_data.strip()[:80])

        bundle.extractall(Path(tmp) / "unpacked")

    unpacked = Path(tmp) / "unpacked" / package_addon.ADDON_NAME
    check("the zip unpacks to a folder the client would accept", unpacked.is_dir(), str(unpacked))

    # The artifact, not the source tree: this is the thing that has to pass.
    lint = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "addon_checks.py"), str(unpacked)],
        capture_output=True, text=True,
    )
    check("the packaged addon passes the release lint", lint.returncode == 0, lint.stdout.strip()[-200:])

    # ---- and the guards have to bite -----------------------------------------
    # Both refusals are about shipping the wrong thing, so they are proven by
    # trying to ship it rather than by reading the code.
    original_changelog = package_addon.CHANGELOG
    try:
        package_addon.CHANGELOG = Path(tmp) / "unreleased.md"
        package_addon.CHANGELOG.write_text(
            f"## [{package_addon.addon_version(ADDON)}] - unreleased\n", encoding="utf-8")
        try:
            build_into(Path(tmp) / "dist2")
            refused = False
            why = "it packaged an unreleased version"
        except package_addon.PackagingError as exc:
            refused = True
            why = str(exc)
        check("an unreleased version is refused for its release status",
              refused and "still calls" in why and "unreleased" in why, why)
    finally:
        package_addon.CHANGELOG = original_changelog

    # Copied as bytes: the addon folder is no longer all text (it carries
    # `icon.tga` once there is art), and reading a texture as UTF-8 is a crash in
    # the fixture rather than a finding about the product.
    leaky = Path(tmp) / "leaky"
    leaky.mkdir()
    for item in sorted(ADDON.iterdir()):
        if item.is_file():
            shutil.copy2(item, leaky / item.name)
    (leaky / "Data.lua").write_text(
        'AgentBoardData = "HE1|bridge=0.5.0|schema=2|generated=1|rows=1|acked=0|hosts=local:ok|new="\n',
        encoding="utf-8",
    )
    try:
        package_addon.build(Path(tmp) / "dist3", leaky)
        refused_leak = False
        leaky_why = "it packaged the payload"
    except package_addon.PackagingError as exc:
        refused_leak = True
        leaky_why = str(exc)
    check("a Data.lua carrying a payload is refused", refused_leak, leaky_why)

print("")
if failures:
    print("PACKAGE FAILED: " + "; ".join(failures))
    raise SystemExit(1)
print("PACKAGE OK")
