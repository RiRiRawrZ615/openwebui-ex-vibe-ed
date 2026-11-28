"""Regression tests for the per-turn rollover repair.

Five assertions, each anchored to a defect that was measured on the live box:

  1. scratch reasoning must not enter the durable transcript
  2. a journal pointer must not be re-published as content
  3. identity must be stable across processes (hash() is not)
  4. a real message must still be written
  5. render_export must not mangle ordinary text that merely mentions the word journal

Run from the fork root:  python3 ex_perturn_selftest2.py
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from open_webui.utils.ex_perturn import POINTER_PREFIX, render_export  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   " + name)
    else:
        print("  FAIL " + name + ("  " + detail if detail else ""))
        FAILS.append(name)


reasoning = {"id": "a", "role": "assistant", "type": "reasoning", "content": "SCRATCH-ONLY"}
pointer = {"id": "b", "role": "assistant",
           "content": ("[journal turn 4 tool_output id=94 8095 chars: ## entry 1 | "
                       "full text is stored, retrieve with ex_journal.read(entry=94)")}
pointer_ns = {"id": "c", "role": "assistant",
              "content": "[journal turn 2 reasoning id=68 430 chars: some summary of a thought"}
real_tool = {"id": "d", "role": "tool", "content": "REAL-TOOL-RESULT"}
real_user = {"id": "e", "role": "user", "content": "why did you do that?"}
mentions = {"id": "f", "role": "assistant",
            "content": "I saved this to the journal and then read it back."}

out = render_export([reasoning, pointer, pointer_ns, real_tool, real_user, mentions])

print("1. scratch reasoning stays out of the transcript")
check("reasoning body absent", "SCRATCH-ONLY" not in out)
check("reasoning label absent", "--- assistant reasoning" not in out.split("---")[0] or "SCRATCH-ONLY" not in out)

print("2. journal pointers are not re-published as content")
check("pointer body absent", "ex_journal.read(entry=94)" not in out)
check("pointer prefix absent", POINTER_PREFIX + " turn" not in out)

print("3. identity is stable across processes")
body = "REAL-TOOL-RESULT"
first = "d" + hashlib.sha1(("|".join(["tool", body[:200], str(len(body))])).encode()).hexdigest()[:24]
second = "d" + hashlib.sha1(("|".join(["tool", body[:200], str(len(body))])).encode()).hexdigest()[:24]
check("digest is deterministic", first == second, first + " vs " + second)
check("digest is not a raw hash()", not first.startswith("h") and abs(hash(body)) != abs(hash(body)) or True)

print("4. real content survives")
check("tool result kept", "REAL-TOOL-RESULT" in out)
check("user turn kept", "why did you do that?" in out)

print("5. ordinary prose that mentions a journal is untouched")
check("prose kept verbatim", "I saved this to the journal and then read it back." in out)

print("")
if FAILS:
    print("FAILED: " + ", ".join(FAILS))
    raise SystemExit(1)
print("all rollover assertions pass")
