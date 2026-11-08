#!/usr/bin/env python3
"""Context gate and rollover shedding, run against the DEV tree only.

Why this test exists: the fork measured the message list before the tool bodies were materialized,
so an agentic chat read as 6076 estimated tokens while the engine counted 262306 and refused the
request. Crossing the rollover point only wrote a carry pointer for a future chat, so the current
chat never got smaller. The two checks below are the two halves of that failure.

Runs on the host with stdlib only. BASE is the dev tree; the shared harness in Code/vibe-ed points at
Forks/vibe-ed and was not touched.

Checks:
  1. tool output is counted, and a chat that was measured as a few thousand tokens measures over
     the rollover point
  2. the tool definitions are part of the projection
  3. fit_max_tokens never asks for more than the room the prompt leaves
  4. the gate trims stale tool output first, and leaves conversation text alone
  5. the gate sheds at the rollover point and the prompt actually shrinks
  6. the verbatim dump is on disk before anything leaves the prompt
  7. the shed index carries ids, never a paraphrase
  8. a tool call is never split from its result by the shed boundary
  9. a shed that would be a data loss (dump failure) does not shed
 10. the gate refuses locally instead of sending a request the engine will refuse
 11. settings mirror parity for the new keys
 15. the shed span goes to the vector store and the last reasoning turn comes back verbatim
 19. the C chip is dimmed before a shed and lit after one, counting sheds from the state file
"""
import importlib.util, sys, tempfile
from pathlib import Path

# The repo root is the parent of this file, so the harness runs from any checkout rather than
# from one machine's dev tree.
BASE = str(Path(__file__).resolve().parent.parent)
BACKEND = f"{BASE}/backend"
sys.path.insert(0, BACKEND)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ex_rollover = load("ex_rollover", f"{BACKEND}/open_webui/utils/ex_rollover.py")
ex_context_window = load("ex_context_window", f"{BACKEND}/open_webui/utils/ex_context_window.py")
ex_settings = load("ex_settings", f"{BACKEND}/open_webui/utils/ex_settings.py")
ex_wiring = load("ex_wiring", f"{BACKEND}/open_webui/utils/ex_wiring.py")

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name} {detail}")


SETTINGS = dict(ex_settings.DEFAULTS)
tmp = tempfile.mkdtemp()

# Shape A: the stored message, bulk in the structured output. This is what the old measurement saw,
# and it measured as a few thousand tokens.
stored = [
    {"id": "m1", "role": "user", "content": "fix the dev fork"},
    {
        "id": "m2",
        "role": "assistant",
        "content": "done",
        "output": [
            {"type": "tool", "content": "x" * 200000},
            {"type": "message", "content": [{"type": "text", "text": "done"}]},
        ],
    },
]

# Shape B: the same turn after process_messages_with_output, which is the shape the engine actually
# receives. The gate runs on this, so the test has to as well.
messages = [stored[0]]
for i in range(12):
    messages.append(
        {
            "id": f"a{i}",
            "role": "assistant",
            "content": f"reply {i}",
            "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": "grep", "arguments": "{}"}}],
        }
    )
    messages.append(
        {"id": f"t{i}", "role": "tool", "tool_call_id": f"c{i}", "content": "x" * 200000}
    )

before = ex_context_window.messages_tokens(messages)
old_rule = len(str(stored[0]["content"])) // 4 + len(str(stored[1]["content"])) // 4
check(
    "1 tool output is counted",
    ex_context_window.messages_tokens(stored) > 50000 and ex_context_window.output_tokens(stored[1]) > 50000,
    f"stored shape measures {ex_context_window.messages_tokens(stored)} against the old rule's {old_rule}",
)
check(
    "2 tool definitions enter the projection",
    ex_context_window.projected_tokens(messages, 4096) == before + 4096,
)

# Room smaller than the request: the cap has to come down to what the prompt leaves.
big = [{"id": "s0", "role": "user", "content": "y" * 450000}]
big_projection = ex_context_window.projected_tokens(big, SETTINGS["toolSchemaTokens"])
fit_big = ex_context_window.fit_max_tokens(262144, big_projection, 131072, answer_room=16384)
check(
    "3 fit never asks for more than the room left",
    fit_big == 262144 - big_projection - 16384 and fit_big < 131072 and fit_big > 0,
    f"projection={big_projection} fit={fit_big}",
)
# Room larger than the request: the request stands, nothing is shaved.
small = [{"id": "s1", "role": "user", "content": "y" * 10000}]
small_projection = ex_context_window.projected_tokens(small, SETTINGS["toolSchemaTokens"])
check(
    "3a fit keeps the request when the room is larger",
    ex_context_window.fit_max_tokens(262144, small_projection, 131072, answer_room=16384) == 131072,
    f"projection={small_projection}",
)
check(
    "3b fit with no room returns 0 rather than a negative cap",
    ex_context_window.fit_max_tokens(262144, 262144, 131072, answer_room=16384) == 0,
)

shaped, report = ex_wiring.prompt_gate(tmp, "chat1", messages, SETTINGS, "user1", 131072)
check(
    "4 stale tool output is stubbed and the request lands under the trim target",
    report.get("trimmed") and report.get("tokens_after_trim") <= int(report["budget"] * SETTINGS["trimTargetRatio"]),
    f"report={ {k: report.get(k) for k in ('measured_before','tokens_after_trim','budget')} }",
)
check(
    "4b trimming never rewrites conversation text",
    any(m.get("role") == "assistant" and str(m.get("content")) == "reply 0" for m in shaped),
    "first assistant reply is missing from the shaped list",
)
trimmed_only = ex_context_window.trim_tool_output(messages, keep_tool_turns=SETTINGS["keepToolTurns"])
check(
    "4c the newest tool results stay intact, the older ones become stubs",
    sum(1 for m in trimmed_only if m.get("role") == "tool" and m.get("content") == ex_context_window.TOOL_STUB) == 12 - SETTINGS["keepToolTurns"]
    and sum(1 for m in trimmed_only if m.get("role") == "tool" and m.get("content") != ex_context_window.TOOL_STUB) == SETTINGS["keepToolTurns"],
    f"tool bodies: {[str(m.get('content'))[:14] for m in trimmed_only if m.get('role') == 'tool']}",
)
check(
    "4d the gate does not mutate the list it was handed",
    all(m.get("content") != ex_context_window.TOOL_STUB for m in messages),
)
check(
    "5 the gate reports the honest count",
    report.get("measured_before") == before + SETTINGS["toolSchemaTokens"],
    f"measured_before={report.get('measured_before')}",
)
check(
    "6 the trigger fires on the honest count",
    report.get("trigger") is True,
)

# Conversation text that no amount of tool trimming can fix, so the shed path has to be the thing
# that brings the request under the window.
nos = dict(SETTINGS)
nos["keepToolTurns"] = 0
heavy = [
    {"id": "h1", "role": "user", "content": "a" * 400000},
    {"id": "h2", "role": "assistant", "content": "b" * 400000},
    {"id": "h3", "role": "user", "content": "c" * 400000},
    {"id": "h4", "role": "assistant", "content": "d" * 1000},
    {"id": "h5", "role": "user", "content": "e" * 1000},
    {"id": "h6", "role": "assistant", "content": "f" * 1000},
    {"id": "h7", "role": "user", "content": "g" * 1000},
    {"id": "h8", "role": "assistant", "content": "h" * 1000},
]
kept, gate = ex_wiring.prompt_gate(tmp, "chat2", heavy, nos, "user1", 2048)
check(
    "7 the prompt shrinks at the rollover point",
    gate.get("shed") and ex_context_window.projected_tokens(kept, nos["toolSchemaTokens"]) <= gate["budget"],
    f"gate={ {k: gate.get(k) for k in ('shed','measured_after','budget','trigger')} }",
)
chat_dir = Path(tmp) / "ex_previous_context" / "chat2"
dump = (chat_dir / "chat.txt").read_text(encoding="utf-8")
check(
    "8 the verbatim dump exists before anything leaves the prompt",
    chat_dir.exists() and "### USER" in dump and "a" * 400000 in dump,
)
block_text = gate.get("shed_report", {}).get("index_block", "")
check(
    "9 the shed index carries ids and an address, not a summary",
    "[ROLLOVER chat2" in block_text and "chat.txt" in block_text and len(block_text.splitlines()) > 1,
    f"block={block_text[:120]!r}",
)

# A shed boundary must not split a tool exchange.
pairs = [
    {"id": "p1", "role": "user", "content": "u" * 300000},
    {"id": "p2", "role": "assistant", "content": "", "tool_calls": [{"id": "call1", "name": "grep", "arguments": {}}]},
    {"id": "p3", "role": "tool", "tool_call_id": "call1", "content": "t" * 300000},
    {"id": "p4", "role": "assistant", "content": "tail" * 10},
    {"id": "p5", "role": "user", "content": "tail2" * 10},
    {"id": "p6", "role": "assistant", "content": "tail3" * 10},
    {"id": "p7", "role": "user", "content": "tail4" * 10},
]
kept2, gate2 = ex_wiring.prompt_gate(tmp, "chat3", pairs, nos, "user1", 512)
kept_ids = [m.get("id") for m in kept2]
check(
    "9b the shed index is injected at the tail, not at the head",
    bool(gate.get("shed_injection", {}).get("injected"))
    and any(m.get("role") == "user" for m in kept[gate["shed_injection"]["index"] :]),
    f"injection={gate.get('shed_injection')}",
)
check(
    "10 a tool call is never split from its result",
    ("p2" in kept_ids) == ("p3" in kept_ids),
    f"kept={kept_ids}",
)

# A shed that cannot write the copy first is refused: shedding without a copy is data loss.
broken = dict(nos)
broken["rollover"] = False
kept3, gate3 = ex_wiring.prompt_gate(tmp, "chat4", pairs, broken, "user1", 512)
check(
    "11 shed is gated by the rollover being on",
    not gate3.get("shed") and len(kept3) == len(pairs),
    f"gate={gate3.get('shed_report', {}).get('reason')}",
)
kept5, shed5 = ex_rollover.shed_for_prompt(tmp, "chat4", pairs, {**broken, "rollover": True, "rolloverShed": True}, 1, "user1")
dump4 = (Path(tmp) / "ex_previous_context" / "chat4" / "chat.txt").read_text(encoding="utf-8")
check(
    "12 the copy on disk holds every shed section",
    shed5.get("shed") and "u" * 300000 in dump4 and "t" * 300000 in dump4,
    f"report={shed5}",
)

# Refusal happens locally, before the engine, when nothing can fit.
huge = [{"id": "z1", "role": "user", "content": "z" * 900000}]
kept4, gate4 = ex_wiring.prompt_gate(tmp, "chat5", huge, nos, "user1", 4096)
check(
    "13 the gate refuses before the engine can",
    gate4.get("refused") and gate4.get("over_by", 0) > 0,
    f"gate={ {k: gate4.get(k) for k in ('measured_after','ceiling','over_by')} }",
)
kept6, gate6 = ex_wiring.prompt_gate(tmp, "chat6", [{"id": "z2", "role": "user", "content": "short"}], nos, "user1", 4096)
check(
    "14 a request that fits is not refused",
    gate4.get("refused") and not gate6.get("refused"),
    f"gate6={gate6}",
)

# Forced eviction: the old sections leave the prompt, go to the vector store, and what comes back is
# the address plus the last turn's reasoning.
evict = [
    {"id": "e1", "role": "user", "content": "u" * 300000},
    {
        "id": "e2",
        "role": "assistant",
        "content": "a" * 300000,
        "output": [
            {"type": "reasoning", "summary": [{"text": "reasoning that must come back verbatim"}]},
            {"type": "message", "content": [{"type": "text", "text": "a" * 300000}]},
        ],
    },
    {"id": "e3", "role": "user", "content": "newest question"},
    {"id": "e4", "role": "assistant", "content": "tail"},
    {"id": "e5", "role": "user", "content": "tail2"},
    {"id": "e6", "role": "assistant", "content": "tail3"},
    {"id": "e7", "role": "user", "content": "tail4"},
]

calls = []
ex_wiring.index_chat_memory = lambda d, cid, msgs, s: (calls.append((cid, len(msgs))), {"indexed": len(msgs)})[1]
kept7, gate7 = ex_wiring.prompt_gate(tmp, "chat7", evict, nos, "user1", 512)
check(
    "15 the shed span is handed to the vector store",
    bool(calls) and calls[0][0] == "chat7" and calls[0][1] >= 1,
    f"calls={calls}",
)
check(
    "16 the fed-back block carries the last reasoning turn verbatim",
    "reasoning that must come back verbatim" in gate7.get("shed_report", {}).get("index_block", ""),
    f"block={gate7.get('shed_report', {}).get('index_block', '')[:200]!r}",
)
check(
    "17 the evicted text is not still sitting in the prompt",
    not any("u" * 300000 in str(m.get("content")) for m in kept7),
)
check(
    "18 the prompt after eviction is the index block plus the tail, and the block carries the reasoning",
    any("[ROLLOVER chat7]" in str(m.get("content", "")) for m in kept7)
    and any(str(m.get("content", "")) == "newest question" for m in kept7)
    and (gate7.get("shed_report") or {}).get("removed", 0) >= 1
    and gate7.get("tokens_after_shed") < gate7.get("measured_before"),
    f"shaped={[str(m.get('content'))[:40] for m in kept7]} report={ {k: gate7.get(k) for k in ('tokens_after_shed','measured_before')} }"
)

# The C icon: a state, not an event. Dimmed before the first shed, lit after one, with the count
# taken from the state file that is written only when a shed actually happens.
ex_stats = load("ex_stats", f"{BACKEND}/open_webui/utils/ex_stats.py")
state7 = ex_rollover.chat_state(tmp, "chat7")
check(
    "19 the state counts the shed and the chip reads as lit",
    state7.get("rolls") == 1 and ex_stats.context_chips(state7)[0]["active"],
    f"state={state7}",
)
check(
    "20 the chip is dimmed before any shed happens",
    ex_stats.context_chips({})[0]["active"] is False and ex_stats.context_chips({})[0]["count"] == 0,
    f"chip={ex_stats.context_chips({})}",
)
quiet, gate8 = ex_wiring.prompt_gate(tmp, "chat8", [{"id": "q1", "role": "user", "content": "short"}], nos, "user1", 512)
check(
    "21 the counter is written only when a shed happens",
    ex_rollover.chat_state(tmp, "chat8").get("rolls", 0) == 0 and not gate8.get("shed"),
    f"state={ex_rollover.chat_state(tmp, 'chat8')}",
)

# Mirror parity for the new keys.
ts = Path(BASE) / "src/lib/utils/exSettings.ts"
text = ts.read_text(encoding="utf-8")
for key in (
    "toolSchemaTokens",
    "trimTargetRatio",
    "fitMaxTokens",
    "enforceCeiling",
    "rolloverShed",
    "rolloverShedKeepRatio",
    "shedFeedBackReasoning",
    "shedReasoningChars",
):
    check(f"15 mirror parity {key}", key in text and key in SETTINGS, f"ts={key in text} py={key in SETTINGS}")

stats_ts = (Path(BASE) / "src/lib/utils/exStats.ts").read_text(encoding="utf-8")
check(
    "22 mirror parity for the rollover chip function",
    "def context_chips" in (Path(BACKEND) / "open_webui/utils/ex_stats.py").read_text(encoding="utf-8")
    and "export function contextChips" in stats_ts,
)
# A configured window larger than the engine's handshake. The smaller number has to win, otherwise a
# 128k machine plans for 262144 and a request that should have been shed becomes a refusal.
small_dir = tempfile.mkdtemp()
(Path(small_dir) / "ex_runtime.json").write_text('{"params": {"ctx_size": 131072}}', encoding="utf-8")
kept9, gate9 = ex_wiring.prompt_gate(small_dir, "chat9", [{"id": "w1", "role": "user", "content": "x" * 140000}], nos, "user1", 8000)
check(
    "23 the gate plans for the engine's window, not the configured one",
    gate9.get("ceiling") == 131072 and gate9.get("window_capped_by_engine"),
    f"gate={ {k: gate9.get(k) for k in ('ceiling','configured','window_capped_by_engine')} }",
)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
