"""Dev-only runtime check for the three fixes, run with plain python (no pytest on this box).

Nothing here calls the engine. The engine is shared with stable and a request from a test
script would compete with the live instance, so these are pure logic checks.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from open_webui.utils import ex_evict, ex_loop_guard

fails = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        fails.append(name)
        print(f"FAIL {name} {detail}")


def call(name, args):
    return {"type": "function_call", "name": name, "arguments": args, "role": "assistant", "content": f"calling {name}", "tool_calls": [{"id": f"c{len(args)}", "name": name, "arguments": args}]}


def out(text):
    return {"role": "tool", "content": text}


def think(text):
    return {"type": "reasoning", "summary": [{"type": "input_text", "text": text}]}


# 1. Similar thinking with no action between the blocks is still a loop.
same = "I will check the manifest file and then patch the wiring module so the gate reports what it evicted instead of refusing the request, which is the behaviour the fork was written to have."
stalled = [think(same), think(same), think(same)]
hit = ex_loop_guard.scan(stalled)
check("stalled reasoning still trips", hit["verdict"] == "loop" and hit["stalled_pairs"] >= 2, hit)

# 2. Similar thinking that keeps making progress must not trip the guard.
progress = [think(same), call("grep_search", {"query": "manifest"}),
            think(same), call("grep_search", {"query": "wiring"}),
            think(same)]
hit2 = ex_loop_guard.scan(progress)
check("progress is not a loop", hit2["verdict"] == "clean", hit2)

note = ex_loop_guard.steering_note(hit, "grep_search")
check("note is an observation, not an order", "not an order to call a tool" in note, note)
check("note allows a plain answer", "plain text" in note, note)
check("no note when clean", ex_loop_guard.steering_note(hit2) == "")

# 3. A refused call note must not force another call.
check("refusal note allows closing in text", "plain text" in ex_loop_guard.refused_call_note("read_file"))

# 4. Over the window means eviction, not refusal.
big = [{"role": "system", "content": "pinned head"}]
for i in range(30):
    big.append(call("run_command", {"command": "a" * 400}))
    big.append(out("b" * 900))
big.append({"role": "user", "content": "newest turn, must survive"})
report_budget = 400
shaped, report = ex_evict.evict_to_fit(big, report_budget)
check("evicted rather than refused", not report["still_over"], report)
check("protected head survived", shaped[0]["content"] == "pinned head")
check("newest turn survived", shaped[-1]["content"] == "newest turn, must survive")
check("some turns were dropped", report["evicted_turns"] > 0, report)

# 5. Pairing: no tool_call left without its result.
broken = any(
    m.get("role") == "assistant" and m.get("tool_calls")
    and (shaped.index(m) + 1 >= len(shaped) or shaped[shaped.index(m) + 1].get("role") != "tool")
    for m in shaped if m.get("tool_calls")
)
check("pairing preserved", not broken)

# 6. Journal block is never evicted.
with_journal = [{"role": "system", "content": "pinned head"},
                {"role": "system", "content": "[journal] turn 3 reasoning, full text stored"},
                {"role": "assistant", "content": "x" * 1200},
                {"role": "user", "content": "newest"}]
shaped2, report2 = ex_evict.evict_to_fit(with_journal, 200)
check("journal pointer survives even when over", any("[journal]" in str(m.get("content")) for m in shaped2), shaped2)

# 7. The policy the middleware calls now evicts instead of reporting a refusal.
from open_webui.utils.ex_context_window import apply_overflow_policy, messages_tokens

shaped3, report3 = apply_overflow_policy(big, 400)
check("overflow policy evicts instead of refusing", report3.get("evicted_turns", 0) > 0 and not report3["still_over"], report3)
check("policy says which path ran", report3.get("evict", {}).get("policy") == "evict_per_turn", report3)
check("no answer room is eaten by the prompt", messages_tokens(shaped3) <= 400, report3)

print("\n" + ("all checks passed" if not fails else f"failures: {fails}"))
sys.exit(1 if fails else 0)
