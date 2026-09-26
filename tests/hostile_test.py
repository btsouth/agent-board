#!/usr/bin/env python3
"""Hostile input, end to end: what the two halves do with data they did not write.

Both sides read files a player can edit, a crash can truncate, or a future version
can write differently. The contract is narrow and worth pinning down:

* anything unreadable reads as "no data" and never as an exception
* anything half-readable is refused WHOLE, never rendered as a partial board
* what does come through is bounded (field lengths, row counts, entry counts)
* an unknown status or host still renders as a row with a fallback, not a blank

Every case states its expectation, so this file doubles as the contract.

Run: python3 tests/hostile_test.py
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentboard import wowclient  # noqa: E402

ADDON = ROOT / "addon" / "AgentBoard"

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(("PASS  " if condition else "FAIL  ") + label + (f" ({detail})" if detail and not condition else ""))
    if not condition:
        failures.append(label)


HEADER = "HE1|bridge=0.2.3|schema=5|generated=1789771234|rows={rows}|acked=0|hosts=local:ok|new="
GOOD_ROW = "20260918_182123_e733d6|needs|30|local|default|Projects|a title||12|0.4200|0|a preview"


def payload(rows: int, *records: str, header: str | None = None) -> str:
    return (header or HEADER.format(rows=rows)) + ";;" + ";;".join(records)


# name, payload, expectation: "refuse" (nil) or "accept"
PAYLOADS: list[tuple[str, str, str]] = [
    ("empty string", "", "refuse"),
    ("prose", "this is not a payload", "refuse"),
    ("right tag, no records", payload(0), "accept"),
    ("one good row", payload(1, GOOD_ROW), "accept"),
    ("a torn write: rows claims two, one arrives", payload(2, GOOD_ROW), "refuse"),
    # The record is there, it just cannot be read: reported, not taken as proof
    # that the whole file is broken.
    ("a row with no id", payload(1, "|needs|30|local|default|Projects|t||1|0|0|"), "accept-rejected"),
    ("a row with three fields (an older addon's shape)",
     payload(1, "20260918_182123_e733d6|needs|30"), "accept"),
    ("a row with thirteen fields",
     payload(1, GOOD_ROW + "|extra"), "accept"),
    ("an unknown status", payload(1, GOOD_ROW.replace("|needs|", "|banana|")), "accept"),
    ("an unknown host on the row", payload(1, GOOD_ROW.replace("|local|", "|nowhere|")), "accept"),
    ("a 10,000 character title",
     payload(1, GOOD_ROW.replace("a title", "x" * 10_000)), "accept"),
    # A title with raw separators can only come from a hand-written file (the
    # bridge strips them). It shifts the records, the junk record fails the id
    # check, and the board still reads: one row with a cut title is not a lie.
    ("separator characters inside a title",
     payload(1, GOOD_ROW.replace("a title", "a|title;;with|both")), "accept"),
    ("non-ASCII text", payload(1, GOOD_ROW.replace("a title", "排隊 · Ünïcödé · 🎮")), "accept"),
    ("a non-numeric age", payload(1, GOOD_ROW.replace("|30|", "|soon|")), "accept"),
    ("a huge age", payload(1, GOOD_ROW.replace("|30|", "|999999999999|")), "accept"),
    ("a negative age", payload(1, GOOD_ROW.replace("|30|", "|-500|")), "accept"),
    ("a non-numeric generated", payload(1, GOOD_ROW, header=HEADER.format(rows=1).replace("1789771234", "soon")),
     "accept"),
    ("a huge acked", payload(1, GOOD_ROW, header=HEADER.format(rows=1).replace("acked=0", "acked=9007199254740993")),
     "accept"),
    ("a hostile but legal host map",
     payload(1, GOOD_ROW, header=HEADER.format(rows=1).replace("hosts=local:ok", "hosts=:::;terra:ok;:x")),
     "accept"),
    ("a record separator inside the host map",
     payload(1, GOOD_ROW, header=HEADER.format(rows=1).replace("hosts=local:ok", "hosts=a;;b")), "accept"),
    ("no host map at all", payload(1, GOOD_ROW, header=HEADER.format(rows=1).replace("|hosts=local:ok", "")),
     "accept"),
    ("a schema from the future", payload(1, GOOD_ROW).replace("schema=5", "schema=99"), "incompatible"),
    ("a schema from the past", payload(1, GOOD_ROW).replace("schema=5", "schema=1"), "incompatible"),
    ("a payload of forty rows", payload(40, *([GOOD_ROW] * 40)), "accept"),
]


def parse_with_addon(cases: list[tuple[str, str, str]]) -> list[dict]:
    """Run the addon's real parser over the corpus, in one Lua process."""
    script = [
        'local ns = { STATUS_LABELS = { needs = "Needs you" }, PAYLOAD_TAG = "HE1", PAYLOAD_SCHEMA = 5 }',
        f'assert(loadfile("{ADDON}/Payload.lua"))("AgentBoard", ns)',
        "local out = {}",
    ]
    for index, (_label, body, _expect) in enumerate(cases):
        escaped = body.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        script.append(f'do local raw = "{escaped}"')
        script.append("  local ok, parsed = pcall(ns.ParsePayload, ns, raw)")
        script.append("  if not ok then out[#out + 1] = 'raised'")
        script.append("  elseif parsed == nil then out[#out + 1] = 'nil'")
        script.append("  elseif parsed.incompatible then out[#out + 1] = 'incompatible'")
        script.append("  else")
        script.append("    local bad = 0")
        script.append("    for _, row in ipairs(parsed.sessions) do")
        script.append("      if type(row.id) ~= 'string' or row.id == '' then bad = bad + 1 end")
        script.append("      if type(row.status) ~= 'string' or row.status == '' then bad = bad + 1 end")
        script.append("      if type(row.age) ~= 'number' then bad = bad + 1 end")
        script.append("      if type(row.host) ~= 'string' or row.host == '' then bad = bad + 1 end")
        script.append("    end")
        script.append("    out[#out + 1] = 'rows=' .. #parsed.sessions .. ',bad=' .. bad"
                      " .. ',rejected=' .. (tonumber(parsed.rejected) or 0)")
        script.append("  end")
        script.append("end")
    script.append("print(table.concat(out, '|'))")

    probe = subprocess.run(["lua5.1", "-e", "\n".join(script)], capture_output=True, text=True)
    results = []
    for index, (_label, _body, _expect) in enumerate(cases):
        if probe.returncode != 0 and not probe.stdout:
            results.append({"verdict": "raised", "detail": probe.stderr.strip()[:120]})
            continue
        parts = probe.stdout.strip().split("|")
        verdict = parts[index] if index < len(parts) else "missing"
        results.append({"verdict": verdict, "detail": ""})
    return results


print(f"payload corpus: {len(PAYLOADS)} cases\n")
for (label, _body, expect), result in zip(PAYLOADS, parse_with_addon(PAYLOADS)):
    verdict = result["verdict"] or result.get("detail", "")
    if expect == "refuse":
        ok = verdict in ("nil", "refuse")
    elif expect == "incompatible":
        ok = verdict == "incompatible"
    elif expect == "accept-rejected":
        ok = verdict.startswith("rows=") and ",bad=0" in verdict and "rejected=0" not in verdict
    else:
        ok = verdict.startswith("rows=") and ",bad=0" in verdict
    check(f"payload: {label} -> {expect}", ok, verdict)


# ------------------------------------------------------- SavedVariables -------

SAVEDVARS: list[tuple[str, str, str]] = [
    ("a normal outbox", 'AgentBoardOutbox = "1|reply|local|20260918_182123_e733d6|hello"', "keep"),
    ("an empty outbox", 'AgentBoardOutbox = ""', "empty"),
    ("a number instead of a string", "AgentBoardOutbox = 5", "empty"),
    ("a table instead of a string", "AgentBoardOutbox = { 1, 2 }", "empty"),
    ("a field short", 'AgentBoardOutbox = "1|reply|local"', "empty"),
    # A three-field line is what an addon older than the host field sent, so an
    # id-shaped middle field is read as a reply. A short one is debris.
    ("a three-field line with an id-shaped field",
     'AgentBoardOutbox = "1|20260918_182123_e733d6|text"', "keep"),
    ("a reply with an empty session", 'AgentBoardOutbox = "1|reply|local||text"', "empty"),
    ("a junk id from a shifted record", 'AgentBoardOutbox = "1|reply|local|;|text"', "empty"),
    ("a focus entry with no words", 'AgentBoardOutbox = "2|focus|local|20260918_181445_cd30b7|"', "keep"),
    ("an unknown kind", 'AgentBoardOutbox = "3|sneaky|local|20260918_181445_cd30b7|text"', "keep"),
    ("escapes in the text",
     'AgentBoardOutbox = "4|reply|local|20260918_181445_cd30b7|a\\\\nb\\\\tc\\\\"d"', "keep"),
    ("a truncated literal", 'AgentBoardOutbox = "5|reply|local|session-1|trunca', "empty"),
    ("an odd numeric escape",
     'AgentBoardOutbox = "6|reply|local|20260918_181445_cd30b7|\\\\2a badly"', "keep"),
    ("two assignments",
     'AgentBoardOutbox = "7|reply|local|20260918_180001_aaaaaa|first"\n'
     'AgentBoardOutbox = "8|reply|local|20260918_180002_bbbbbb|second"', "first"),
    ("a sync stamp that is not a number", 'AgentBoardSync = "soon"', "stamp-none"),
    ("a sync stamp that is a number", "AgentBoardSync = 1789771200", "stamp-ok"),
    ("superscript sequence is not an integer", 'AgentBoardOutbox = "²|reply|local|session-1|text"', "empty"),
    ("unbounded sequence cannot reach integer conversion", 'AgentBoardOutbox = "' + '9' * 5000 + '|reply|local|session-1|text"', "empty"),
    ("nothing at all", "local x = 1", "empty"),
]

with tempfile.TemporaryDirectory() as tmp:
    for label, body, expect in SAVEDVARS:
        path = Path(tmp) / "AgentBoard.lua"
        path.write_text(body, encoding="utf-8")

        try:
            entries = wowclient.read_outbox(path)
            stamp = wowclient.read_sync_stamp(path)
            raised = False
        except Exception as exc:  # noqa: BLE001 - raising is the failure we test for
            entries, stamp, raised = [], None, str(exc)

        if raised:
            check(f"savedvars: {label} does not raise", False, raised)
            continue

        if expect == "empty":
            ok = entries == []
        elif expect == "keep":
            ok = len(entries) == 1 and entries[0]["session_id"] and entries[0]["host"]
        elif expect == "first":
            ok = len(entries) == 1 and entries[0]["text"] == "first"
        elif expect == "stamp-none":
            ok = stamp is None
        else:
            ok = stamp == 1789771200.0

        check(f"savedvars: {label} -> {expect}", ok, json.dumps(entries)[:100] + f" stamp={stamp}")

    # A megabyte of outbox: the addon only ever keeps twenty-five entries, but a
    # hand-edited or concatenated file must not be read as if it were normal.
    big = Path(tmp) / "big.lua"
    big.write_text(
        'AgentBoardOutbox = "'
        + ";;".join(f"{n}|reply|local|20260918_1814{n:02d}_aaaaaa|text" for n in range(1, 5000))
        + '"',
        encoding="utf-8",
    )
    started = __import__("time").time()
    entries = wowclient.read_outbox(big)
    elapsed = __import__("time").time() - started
    check("a huge outbox is read without hanging", elapsed < 5.0, f"{elapsed:.2f}s")
    check("...and every entry it returns is well formed",
          all(item["session_id"] and item["kind"] in wowclient.OUTBOX_KINDS for item in entries),
          str(len(entries)))

# ------------------------------------------------------------- generated ----

random.seed(20260919)
ALPHABET = list("abc123|;\\\"'(){}[]") + [";;", "|", "\\n", "\\u00b7"]
fuzzed = ["".join(random.choice(ALPHABET) for _ in range(random.randint(0, 60))) for _ in range(200)]
script = [
    'local ns = { STATUS_LABELS = {}, PAYLOAD_TAG = "HE1", PAYLOAD_SCHEMA = 2, L = setmetatable({}, { __index = function(t, k) rawset(t, k, k) return k end }) }',
    f'assert(loadfile("{ADDON}/Payload.lua"))("AgentBoard", ns)',
    "local bad = 0",
]
for body in fuzzed:
    escaped = body.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    script.append(f'if not pcall(ns.ParsePayload, ns, "{escaped}") then bad = bad + 1 end')
script.append("print(bad)")
probe = subprocess.run(["lua5.1", "-e", "\n".join(script)], capture_output=True, text=True)
check("200 random strings never raise in the payload parser",
      probe.returncode == 0 and probe.stdout.strip() == "0",
      f"rc={probe.returncode} bad={probe.stdout.strip()} {probe.stderr.strip()[:80]}")

print("")
if failures:
    print("HOSTILE FAILED: " + "; ".join(failures))
    raise SystemExit(1)
print("HOSTILE OK")
