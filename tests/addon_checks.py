#!/usr/bin/env python3
"""Release-grade lint for the addon folder: the checks that catch the mistakes
that only show up in the client's error frame.

Both bugs that reached the game would have failed here: a frame method that only
exists via a template (the stub suite covers that), and a Bindings.xml whose root
element the client does not accept.

Run: python3 tests/addon_checks.py [path/to/addon]
"""

from __future__ import annotations

import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ADDON = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "addon" / "AgentBoard"
EXPECTED_INTERFACE = "16001"

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + label + (f" ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def check_toc() -> list[str]:
    toc_path = ADDON / "AgentBoard.toc"
    check("toc exists", toc_path.is_file(), str(toc_path))
    if not toc_path.is_file():
        return []

    lines = read(toc_path).splitlines()
    directives = {}
    listed = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("##"):
            if ":" in stripped:
                key, value = stripped[2:].split(":", 1)
                directives[key.strip()] = value.strip()
        elif stripped and not stripped.startswith("#"):
            listed.append(stripped)

    for required in ("Interface", "Title", "Version", "SavedVariables", "Notes"):
        check(f"toc declares {required}", required in directives, str(directives))

    check(
        f"toc interface matches the client ({EXPECTED_INTERFACE})",
        directives.get("Interface") == EXPECTED_INTERFACE,
        directives.get("Interface", "missing"),
    )

    for name in listed:
        check(f"toc file listed and present: {name}", (ADDON / name).is_file())

    check("bindings and icons are loaded by name, not listed twice",
          not {"Bindings.xml", "icon.tga", "icon-128.png"}.intersection(listed))
    on_disk = {path.name for path in ADDON.glob("*.lua")}
    missing_from_toc = on_disk - set(listed)
    check("every .lua is listed in the toc", not missing_from_toc, ", ".join(sorted(missing_from_toc)))

    order = {name: index for index, name in enumerate(listed)}
    if "Core.lua" in order and "UI.lua" in order:
        check("Core.lua loads before UI.lua", order["Core.lua"] < order["UI.lua"])
    if "Data.lua" in order and "Core.lua" in order:
        check("Data.lua loads before Core.lua", order["Data.lua"] < order["Core.lua"])

    return listed


def check_lua_compiles() -> None:
    for path in sorted(ADDON.glob("*.lua")):
        probe = subprocess.run(["luac5.1", "-p", str(path)], capture_output=True, text=True)
        check(f"compiles: {path.name}", probe.returncode == 0, probe.stderr.strip())


def check_bindings() -> None:
    for path in sorted(ADDON.glob("*.xml")):
        try:
            root = ET.fromstring(read(path))
        except ET.ParseError as exc:
            check(f"xml parses: {path.name}", False, str(exc))
            continue

        check(f"xml parses: {path.name}", True)

        if path.name == "Bindings.xml":
            # The client's binding loader expects <Bindings> at the ROOT; wrapped
            # in <Ui> it reports "Unknown node type Bindings" and no keybind
            # ever appears in the Key Bindings panel.
            check("Bindings.xml root element is <Bindings>", root.tag == "Bindings", root.tag)

            lua = "\n".join(read(item) for item in ADDON.glob("*.lua"))
            headers: list[str] = []
            for binding in root.findall("Binding"):
                name = binding.get("name")
                header = binding.get("header")
                # The header attribute REGISTERS a header; a binding that omits
                # it is displayed under the last header registered before it.
                # Naming the same header a second time reports "Binding header X
                # was attempted to be loaded more than once" in the client's
                # error frame, so a grouped file declares it once - on the first
                # binding - and drops the attribute from the rest.
                if header is not None:
                    check(f"binding {name} does not re-declare header {header}", header not in headers)
                    headers.append(header)
                check(f"binding {name} has a header to inherit", bool(headers), str(name))
                check(f"binding {name} is self-closing-safe text", (binding.text or "").strip() != "", str(name))
                check(
                    f"BINDING_NAME_{name} is defined",
                    re.search(rf"BINDING_NAME_{re.escape(str(name))}\s*=", lua) is not None,
                )

            for header in headers:
                check(
                    f"BINDING_HEADER_{header} is defined",
                    re.search(rf"BINDING_HEADER_{re.escape(str(header))}\s*=", lua) is not None,
                )

            # Binding code runs in global scope, so the namespace must be global.
            check("addon namespace is a global for bindings", re.search(r"_G\[addonName\]\s*=\s*ns", lua) is not None)


def check_forbidden_apis() -> None:
    patterns = {
        "os.": r"\bos\.",
        "io.": r"\bio\.",
        "dofile(": r"\bdofile\s*\(",
        "loadstring(": r"\bloadstring\s*\(",
        "require(": r"\brequire\s*\(",
    }

    for path in sorted(ADDON.glob("*.lua")):
        text = read(path)
        for label, pattern in patterns.items():
            check(f"{path.name} does not use {label}", re.search(pattern, text) is None)

    for path in sorted(ADDON.glob("*.lua")):
        text = read(path)
        found = re.search(r"/(home|mnt|Users)/\S+", text)
        check(f"{path.name} has no absolute paths", found is None, found.group(0) if found else "")


def check_reload_is_deliberate() -> None:
    """ReloadUI reloads the whole UI, so it may only fire from the sync path."""
    for path in sorted(ADDON.glob("*.lua")):
        text = read(path)
        for match in re.finditer(r"ReloadUI\s*\(", text):
            before = text[: match.start()]
            enclosing = re.findall(r"function\s+([\w:.]+)\s*\(", before)
            function_name = enclosing[-1] if enclosing else "?"
            check(f"{path.name}: ReloadUI only in the sync path", function_name.endswith("Sync"), function_name)


def check_placeholder_data() -> None:
    path = ADDON / "Data.lua"
    text = read(path)

    # The published file must be data, never code: a table constructor here would
    # mean the bridge can inject Lua into every client that loads the addon.
    check("Data.lua is a string payload, not a table", re.search(r"AgentBoardData\s*=\s*\{", text) is None)
    check("Data.lua defines nothing but the payload", "function" not in text)

    probe = subprocess.run(
        [
            "lua5.1",
            "-e",
            f'local chunk = assert(loadfile("{path}")); chunk(); '
            'if type(AgentBoardData) ~= "string" then print("not-a-string") return end '
            'if AgentBoardData == "" then print("empty-string") return end '
            'local first = string.match(AgentBoardData, "^([^;]+)") '
            'print((string.match(first, "^HE%d+") and "HE-tag" or "bad-tag") .. "|schema=" '
            '.. tostring(string.match(first, "schema=(%d+)")))',
        ],
        capture_output=True,
        text=True,
    )
    check("Data.lua loads under lua5.1", probe.returncode == 0, probe.stderr.strip())

    if probe.returncode == 0:
        out = probe.stdout.strip()
        check("Data.lua payload type", out in ("empty-string", "HE-tag|schema=3"), out)

    toc = read(ADDON / "AgentBoard.toc")
    for name in ("AgentBoardOutbox", "AgentBoardSync", "AgentBoardLastGood"):
        check(f"toc declares SavedVariables {name}", name in toc)


def check_escapes() -> None:
    r"""Lua 5.1 has no \u escape: "\u00b7" is the literal text "u00b7".

    That is a silent bug in exactly the kind of string this addon renders, so it
    is banned outright rather than trusted to memory.
    """
    for path in sorted(ADDON.glob("*.lua")):
        text = read(path)
        for match in re.finditer(r"\\u\{[0-9a-fA-F]+\}|\\u[0-9a-fA-F]{4}", text):
            check(f"{path.name} uses no \\u escape ({match.group(0)})", False, "Lua 5.1 renders this as literal text")


def check_no_onupdate() -> None:
    """OnUpdate runs every frame, forever: only the minimap drag may use one.

    The rule is not "never" but "never left running": a handler that is set and
    never cleared is a permanent per-frame cost in someone's raid.
    """
    for path in sorted(ADDON.glob("*.lua")):
        text = read(path)
        handlers = list(re.finditer(r'SetScript\s*\(\s*"OnUpdate"', text))
        if not handlers:
            continue
        cleared = re.search(r'SetScript\s*\(\s*"OnUpdate"\s*,\s*nil\s*\)', text) is not None
        check(f"{path.name}: an OnUpdate handler is cleared again", cleared, f"{len(handlers)} handler(s)")
        check(f"{path.name}: OnUpdate only in UI.lua", path.name == "UI.lua", path.name)


def check_schema_agreement() -> None:
    """The bridge, the addon parser, and the published file must agree.

    A mismatch is not a crash: it is a board that refuses every payload, which
    looks like "no agents" to a player. Cheap to check, expensive to miss.
    """
    addon_schema = re.search(r"ns\.PAYLOAD_SCHEMA\s*=\s*(\d+)", read(ADDON / "Payload.lua"))
    check("the addon declares a payload schema", addon_schema is not None)
    if not addon_schema:
        return
    schema = addon_schema.group(1)

    bridge = Path(__file__).resolve().parent.parent / "agentboard" / "wowclient.py"
    bridge_schema = re.search(r"^PAYLOAD_SCHEMA\s*=\s*(\d+)", read(bridge), re.M) if bridge.is_file() else None
    check("the bridge declares a payload schema", bridge_schema is not None, str(bridge))
    if bridge_schema:
        check(
            "bridge and addon agree on the payload schema",
            bridge_schema.group(1) == schema,
            f"bridge={bridge_schema.group(1)} addon={schema}",
        )

    published = re.search(r'schema=(\d+)', read(ADDON / "Data.lua"))
    if published:
        check(
            "the published payload matches the addon's schema",
            published.group(1) == schema,
            f"published={published.group(1)} addon={schema}",
        )


def check_status_vocabulary() -> None:
    """One vocabulary for statuses, or a status renders as a blank row.

    The bridge decides what a status is called; the addon has to know every name
    it can send, with a colour and a label, or the row loses its meaning.
    """
    core = read(ADDON / "Core.lua")
    # DOTALL and a lazily-matched body: the colour table has braces of its own
    # inside it, which a [^}]* capture would stop at.
    order = re.search(r"ns\.STATUS_ORDER\s*=\s*\{(.*?)\n\}", core, re.S)
    labels = re.search(r"ns\.STATUS_LABELS\s*=\s*\{(.*?)\n\}", core, re.S)
    colors = re.search(r"ns\.STATUS_COLORS\s*=\s*\{(.*?)\n\}", core, re.S)
    for name, block in (("STATUS_ORDER", order), ("STATUS_LABELS", labels), ("STATUS_COLORS", colors)):
        check(f"Core.lua declares {name}", block is not None)

    if not (order and labels and colors):
        return

    names = set(re.findall(r'"([a-z]+)"', order.group(1)))
    for name, block in (("label", labels), ("colour", colors)):
        defined = set(re.findall(r"\b([a-z]+)\s*=", block.group(1)))
        missing = names - defined
        check(f"every status has a {name}", not missing, ", ".join(sorted(missing)))

    roster = Path(__file__).resolve().parent.parent / "agentboard" / "roster.py"
    if roster.is_file():
        bridge_names = set(re.findall(r'"(needs|error|working|waiting|reply|idle|finished)"', read(roster)))
        missing = bridge_names - names
        check("the addon knows every status the bridge sends", not missing, ", ".join(sorted(missing)))


def check_localization() -> None:
    """User-visible text goes through the locale table, and the loader order holds.

    Locale.lua must load before anything that reads `ns.L` at file scope, and the
    UI files must actually use the table: a hard-coded string is a string no
    translator can reach, and a silent drift back to literals is exactly what
    nobody notices until a German player sees English in the middle of a panel.
    """
    toc = read(ADDON / "AgentBoard.toc")
    listed = [line.strip() for line in toc.splitlines()
              if line.strip() and not line.strip().startswith("#")]
    check("Locale.lua is shipped", "Locale.lua" in listed, ", ".join(listed))
    if "Locale.lua" in listed and "Core.lua" in listed:
        check("Locale.lua loads before Core.lua", listed.index("Locale.lua") < listed.index("Core.lua"),
              ", ".join(listed))

    locale = read(ADDON / "Locale.lua")
    check("Locale.lua defines the table", re.search(r"ns\.L\s*=", locale) is not None)
    check("Locale.lua defines the format helper", re.search(r"function ns\.Lf", locale) is not None)

    for name in ("Core.lua", "UI.lua"):
        text = read(ADDON / name)
        check(f"{name} looks strings up through L", "L[" in text and "ns.Lf(" in text, name)


def check_kind_vocabulary() -> None:
    """The outbox kind set lives in two languages; it has to agree."""
    core = read(ADDON / "Core.lua")
    match = re.search(r"ns\.OUTBOX_KINDS\s*=\s*\{([^}]*)\}", core)
    check("Core.lua declares the outbox kinds", match is not None)
    if not match:
        return

    addon_kinds = set(re.findall(r"([a-z_]+)\s*=\s*true", match.group(1)))
    bridge = Path(__file__).resolve().parent.parent / "agentboard" / "wowclient.py"
    if not bridge.is_file():
        return

    bridge_match = re.search(r"OUTBOX_KINDS\s*=\s*\{([^}]*)\}", read(bridge))
    check("the bridge declares the outbox kinds", bridge_match is not None, str(bridge))
    if not bridge_match:
        return

    bridge_kinds = set(re.findall(r'"([a-z_]+)"', bridge_match.group(1)))
    check("addon and bridge agree on the outbox kinds", addon_kinds == bridge_kinds,
          f"addon={sorted(addon_kinds)} bridge={sorted(bridge_kinds)}")


def check_versions_match() -> None:
    toc = re.search(r"^##\s*Version:\s*(\S+)", read(ADDON / "AgentBoard.toc"), re.M)
    lua = re.search(r'ns\.VERSION\s*=\s*"(\S+)"', read(ADDON / "Core.lua"))
    check("toc and Core.lua agree on the version", bool(toc and lua and toc.group(1) == lua.group(1)),
          f"toc={toc and toc.group(1)} lua={lua and lua.group(1)}")


# Calls that put text in front of a player. Anything written as a bare literal in
# one of these is a string no translator can reach, which is what the Locale pass
# exists to prevent and what a later edit quietly reintroduces.
DISPLAY_CALLS = (":SetText(", ":AddMessage(", ":AddLine(", ":Print(")

# Literals that are not language: buttons made of a symbol, the chat prefix, and
# the ellipsis/format scaffolding the UI is built from.
ALLOWED_LITERALS = re.compile(r"^[A-Za-z0-9]{1,2}$|^[%|]|^\|c[0-9a-fA-F]{8}")


def _first_argument(text: str, start: int) -> str:
    """The first argument of a call, up to its top-level comma, quotes respected."""
    depth = 0
    quoted = None
    out = []
    index = start
    while index < len(text):
        char = text[index]
        if quoted:
            out.append(char)
            if char == quoted and text[index - 1] != "\\":
                quoted = None
        elif char in "\"'":
            quoted = char
            out.append(char)
        elif char in "([{":
            depth += 1
            out.append(char)
        elif char in ")]}":
            if depth == 0:
                break
            depth -= 1
            out.append(char)
        elif char == "," and depth == 0:
            break
        else:
            out.append(char)
        index += 1
    return "".join(out).strip()


def check_user_facing_literals() -> None:
    """Player-visible text goes through the locale table, everywhere.

    Checked by structure rather than by grep: the first argument of a display
    call, when it is a bare string literal with words in it, is a string that
    ships in English only.
    """
    for path in sorted(ADDON.glob("*.lua")):
        text = read(path)
        for call in DISPLAY_CALLS:
            for match in re.finditer(re.escape(call), text):
                argument = _first_argument(text, match.end())

                # A literal is what we are after, whether it stands alone or is one
                # fragment of a concatenation: "needs you " .. count is just as
                # untranslatable as a whole string.
                fragments = [part.strip() for part in re.split(r"\.\.", argument)]
                for fragment in fragments:
                    if not re.fullmatch(r"\"[^\"\\]*\"|'[^'\\]*'", fragment):
                        continue

                    literal = fragment[1:-1]
                    if not re.search(r"[A-Za-z]", literal):
                        continue
                    if ALLOWED_LITERALS.match(literal):
                        continue

                    line = text.count("\n", 0, match.start()) + 1
                    check(
                        f"{path.name}:{line} player-visible text goes through L[...]",
                        False,
                        literal[:50],
                    )


def main() -> int:
    print(f"checking {ADDON}\n")
    listed = check_toc()
    check_lua_compiles()
    check_bindings()
    check_forbidden_apis()
    check_reload_is_deliberate()
    check_placeholder_data()
    check_escapes()
    check_no_onupdate()
    check_schema_agreement()
    check_status_vocabulary()
    check_versions_match()
    check_localization()
    check_kind_vocabulary()
    check_user_facing_literals()

    print("")
    if failures:
        print("ADDON CHECKS FAILED: " + "; ".join(failures))
        return 1
    print(f"ADDON CHECKS OK ({len(listed)} files listed in the toc)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
