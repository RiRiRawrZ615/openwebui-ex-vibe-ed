"""Tests for compaction. No pytest, no network, no model.

Each test is anchored to a way the previous design failed, so a regression shows up as a named
failure rather than as a vague feeling that the agent got dumber.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from open_webui.utils import ex_compact as c  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   " + name)
    else:
        print("  FAIL " + name + ("  " + detail if detail else ""))
        FAILS.append(name)


print("1. the brief names a tool that actually exists")
state = {"lines": ["[s1] turn 1 user: do the thing", "[s2] turn 2 assistant: did it"]}
brief = c.render_brief(state)
check("brief is marked", brief.startswith(c.BRIEF_MARKER))
check("reader is the namespaced name", "ex_journal__read(entry=" in brief)
check("dotted form is not advertised", "ex_journal.read" not in brief)
check("ids are shown", "[s1]" in brief and "[s2]" in brief)

print("2. ids are never invented by the model's own text")
check("no bare id outside the index", brief.count("ex_journal__read") == 1)

print("3. an empty state produces nothing rather than an empty block")
check("empty state gives empty brief", c.render_brief({"lines": []}) == "")

print("4. old lines drop, and the drop is declared")
state2 = {"lines": ["[s%d] turn %d x: y" % (i, i) for i in range(200)]}
brief2 = c.render_brief(state2, {"compactMaxLines": 100})
check("capped to 100 lines", brief2.count("[s") == 100)
check("keeps the NEWEST lines", "[s199]" in brief2 and "[s0]" not in brief2)
check("declares the drop", "no longer indexed" in brief2)
check("still names the reader", "ex_journal__read" in brief2)

print("5. summariser output is cleaned, not trusted")
raw = """Here are the index lines:
1. user asked to fix the prefill bug
- assistant ran build_tune_rocblas.sh and it produced 112 rows
   * tool grepped ex_wiring.py at line 582 and found per_turn_evict

"""
lines = c.parse_lines(raw)
check("preamble dropped", not any("Here are" in ln for ln in lines))
check("three real lines kept", len(lines) == 3, repr(lines))
check("paths survive verbatim", any("build_tune_rocblas.sh" in ln for ln in lines))
check("error/number survive", any("112" in ln for ln in lines))

print("6. a rambling line is clipped, not stored whole")
long_line = "assistant: " + ("word " * 200)
lines = c.parse_lines(long_line, limit=220)
check("clipped to limit", len(lines[0]) <= 224, str(len(lines[0])))

print("7. folding assigns stable increasing ids")
s = c.fold({}, ["first thing", "second thing"], turn=3)
check("two ids assigned", len(s["lines"]) == 2)
check("ids are s1 and s2", s["lines"][0].startswith("[s1]") and s["lines"][1].startswith("[s2]"))
check("turn recorded", "turn 3" in s["lines"][0])
s2 = c.fold(s, ["third thing"], turn=4)
check("continues from where it stopped", s2["lines"][2].startswith("[s3]"))
check("no duplicate ids", len({ln.split("]")[0] for ln in s2["lines"]}) == 3)

print("8. compaction does not fire on every turn")
r = c.should_compact({}, 0)
check("no index yet means compact", r["compact"] is True)
r = c.should_compact({"lines": ["[s1] x"]}, 10, {"compactMinChars": 5000})
check("a trickle does not compact", r["compact"] is False, r["reason"])
r = c.should_compact({"lines": ["[s1] x"]}, 9000, {"compactMinChars": 5000})
check("a real batch does compact", r["compact"] is True)
check("reason is always stated", bool(r["reason"]))

print("9. the summariser call carries no tools, so it cannot make a rejected call")
msgs = c.summarise_prompt("some log")
check("two messages only", len(msgs) == 2)
check("no tool key anywhere", all("tools" not in m for m in msgs))
check("system says do not invent", "Not" in msgs[0]["content"] or "never" in msgs[0]["content"].lower())

print("10. chunking never splits a message and respects the ceiling")
msgs = [{"role": "user", "content": "x" * 900} for _ in range(20)]
chunks = c.chunk_for_summary(msgs, chunk_chars=2000)
check("more than one chunk", len(chunks) > 1, str(len(chunks)))
check("no chunk wildly over", all(len(ch) <= 2200 for ch in chunks), str([len(ch) for ch in chunks]))
check("nothing lost", sum(ch.count("user:") for ch in chunks) == 20)

print("")
if FAILS:
    print("FAILED: " + ", ".join(FAILS))
    raise SystemExit(1)
print("all compaction assertions pass")
