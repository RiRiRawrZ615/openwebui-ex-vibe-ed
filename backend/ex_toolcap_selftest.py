"""Tests for the tool-output cap and the budget note. No pytest, no network, no model."""
import sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

import importlib.util
spec = importlib.util.spec_from_file_location("ex_toolcap", str(Path(__file__).resolve().parent / "open_webui" / "utils" / "ex_toolcap.py"))
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

FAILS = []

def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  " + str(detail)))
    if not cond:
        FAILS.append(name)

tmp = tempfile.mkdtemp()
small = "a" * 500
big = "HEAD" + ("x" * 50000) + "TAIL"

print("1. a small result is untouched, byte for byte")
r = c.cap_tool_output(small, tmp, "c1", "tool")
check("not capped", r["capped"] is False)
check("identical text", r["text"] == small)
check("no spill file", r["path"] == "")

print("2. a bomb is capped and always smaller")
r = c.cap_tool_output(big, tmp, "c2", "cat_file")
check("capped", r["capped"] is True)
check("result is smaller than input", len(r["text"]) < len(big), len(r["text"]))
check("respects the ceiling", len(r["text"]) <= 24000, len(r["text"]))

print("3. head AND tail survive, not just a prefix")
check("head kept", r["text"].startswith("HEAD"))
check("tail kept", r["text"].endswith("TAIL"))

print("4. the cut is declared in the text, so it cannot read as complete")
check("says TRUNCATED", "TRUNCATED" in r["text"])
check("says NOT complete", "NOT complete" in r["text"])
check("gives the original size", str(len(big)) in r["text"])

print("5. the full text is recoverable from disk")
check("spill path named", r["path"] != "" and r["path"] in r["text"])
spilled = Path(r["path"]).read_text()
check("spill file is the whole result", spilled == big, len(spilled))
check("spill file holds the middle", "x" * 40000 in spilled)

print("6. mode off is a real no-op")
r = c.cap_tool_output(big, tmp, "c3", "t", settings={"toolCapMode": "off"})
check("off does not cap", r["capped"] is False)
check("off returns original", r["text"] == big)

print("7. mode drop keeps only a stub but still preserves the file")
r = c.cap_tool_output(big, tmp, "c4", "t", settings={"toolCapMode": "drop"})
check("drop caps", r["capped"] is True)
check("drop is tiny", len(r["text"]) < 300, len(r["text"]))
check("drop still names the file", r["path"] != "" and r["path"] in r["text"])
check("drop file exists", r["path"] != "" and Path(r["path"]).exists())

print("8. a custom ceiling is honoured")
r = c.cap_tool_output(big, tmp, "c5", "t", settings={"toolCapChars": 5000})
check("under the custom cap", len(r["text"]) <= 5000, len(r["text"]))
check("still smaller than input", len(r["text"]) < len(big))

print("9. head share is respected and clamped")
r = c.cap_tool_output(big, tmp, "c6", "t", settings={"toolCapChars": 10000, "toolCapHeadShare": 0.9})
check("still smaller", len(r["text"]) < len(big))
r2 = c.cap_tool_output(big, tmp, "c7", "t", settings={"toolCapChars": 10000, "toolCapHeadShare": 99})
check("absurd share clamped, no crash", len(r2["text"]) < len(big))

print("10. it never raises, even on junk")
for junk in [None, 42, "", b"bytes"]:
    try:
        rr = c.cap_tool_output(junk, tmp, "c8", "t")
        check("survives " + type(junk).__name__, isinstance(rr["text"], str))
    except Exception as exc:
        check("survives " + type(junk).__name__, False, exc)
r = c.cap_tool_output(big, "/proc/definitely/not/writable", "c9", "t")
check("unwritable data dir still caps without a file", r["capped"] is True and len(r["text"]) < len(big))

print("11. the budget note is honest and off-switchable")
note = c.remaining_context_note(196608, 40000)
check("note mentions both numbers", "196608" in note and "40000" in note)
check("note states remaining", "left" in note)
note_tight = c.remaining_context_note(1000, 900)
check("tight state reported", "nearly full" in note_tight or "tight" in note_tight)
check("off switch works", c.remaining_context_note(1000, 900, {"contextBudgetNote": False}) == "")
check("junk window yields nothing", c.remaining_context_note(0, 5) == "")

print("")
if FAILS:
    print("FAILED: " + ", ".join(FAILS))
    raise SystemExit(1)
print("all tool-cap assertions pass")
