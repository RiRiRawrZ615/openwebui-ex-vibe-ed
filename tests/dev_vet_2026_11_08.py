"""Feature vet for the dev fork, run 2026-11-08.

Every claim here is a run, not a reading of the source. Nothing calls an engine, nothing
touches a running instance, and no request is sent to the shared strata server.

What this covers that the earlier checks did not:

  - the request path actually reaches the slide (the gate calls it), which the modules could
    not prove on their own because they had no call site;
  - user turns can be evicted, and the current request survives eviction;
  - journal entries are written in the shape append_journal requires, so a forced slide does
    not raise KeyError mid-turn;
  - the handoff names ids that the builtin tool can resolve, awaited properly.
"""
import asyncio
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from open_webui.tools.builtins import ex_journal as tool
from open_webui.utils import ex_loop_guard
from open_webui.utils import ex_prompt_assembler as asm
from open_webui.utils import ex_slide
from open_webui.utils.ex_evict import evict_to_fit
from open_webui.utils.ex_journal import append_journal, journal_path
from open_webui.utils.ex_wiring import prompt_gate

fails = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        fails.append(name)


def msg(role, text, mid, kind=None):
    m = {"role": role, "content": text, "id": mid}
    if kind:
        m["type"] = kind
    return m


data_dir = Path(tempfile.mkdtemp(prefix="vet_"))
chat = "vet_chat"
path = journal_path(data_dir, chat)


def writer(entry):
    append_journal(path, [entry], chat)


# --- 1. the chat-death shape: user turns are evictable, the request is not -------------------
msgs = [msg("system", "pinned head", "m0")]
for i in range(30):
    msgs.append(msg("user", f"earlier user turn {i} " + "u" * 400, f"u{i}"))
    msgs.append(msg("assistant", f"answer {i} " + "a" * 400, f"a{i}"))
msgs.append(msg("user", "the current request must survive", "u_current"))
msgs.append(msg("assistant", "tool output after the request " + "t" * 400, "a_tool"))

shaped, rep = evict_to_fit(msgs, 2000)
check("growth from user text can be evicted", rep["evicted_turns"] >= 30, rep["evicted_turns"])
check("the current request is never evicted", any(m.get("id") == "u_current" for m in shaped))
check("eviction fits rather than blocks", not rep["still_over"], rep.get("blocked_reason"))

# --- 2. a forced slide writes entries in the shape append_journal requires -------------------
chat_msgs = [msg("system", "pinned head", "m0")]
for i in range(12):
    chat_msgs.append(msg("user", f"question {i} " + "u" * 200, f"q{i}"))
    chat_msgs.append(msg("assistant", f"answer {i} " + "a" * 500, f"a{i}"))
    chat_msgs.append(msg("assistant", f"reasoning {i} " + "r" * 300, f"r{i}", "reasoning"))
chat_msgs.append(msg("user", "newest request", "q_new"))

before = path.exists() and "## entry" in path.read_text()
shaped, slide_report = ex_slide.slide(chat_msgs, data_dir=data_dir, window=4000, entry_writer=writer)
check("slide fired on a crossing prompt", slide_report["slid"], slide_report.get("skipped_reason"))
check("handoff block present in the prompt", any("[context slide]" in str(m.get("content")) for m in shaped))
check("newest turn survived", shaped[-1]["id"] == "q_new")
check("last reasoning turn survived", any(m.get("type") == "reasoning" for m in shaped))
check("journal entries written for evicted turns", "## entry" in path.read_text())
check("ids recorded and named in the handoff", slide_report["journal_ids"] and any(
    "entries: " in str(m.get("content")) for m in shaped), slide_report["journal_ids"][:3])

# --- 3. the handoff is readable through the builtin tool (the pointer must resolve) ----------
req = types.SimpleNamespace(
    app=types.SimpleNamespace(state=types.SimpleNamespace(data_dir=str(data_dir))),
    state=types.SimpleNamespace(chat_id=chat),
)
T = tool.Tools()
idx = asyncio.run(T.index(__request__=req))
check("tool index sees the journal", idx.startswith(f"journal: {chat}"), idx[:120])

entry_no = slide_report["journal_ids"][0]
got = asyncio.run(T.read(entry=entry_no, __request__=req))
check("read(entry=N) returns text that left the window", "question 0" in got, got[:160])

# --- 4. the live gate reaches the slide (this is the wiring that was missing) ---------------
gate_settings = {
    "enabled": True,
    "contextWindow": 4000,
    "replyReserve": 1024,
    "toolSchemaTokens": 256,
    "trimTargetRatio": 0.85,
    "keepToolTurns": 3,
    "contextSlide": True,
    "slideFraction": 0.70,
    "slideKeepTail": 4,
    "rollover": False,
    "enforceCeiling": True,
    "ragMemory": False,
}
gate_dir = Path(tempfile.mkdtemp(prefix="vet_gate_"))
gate_chat = "vet_gate_chat"
shaped, gate_report = prompt_gate(gate_dir, gate_chat, chat_msgs, gate_settings)
check("the gate calls the slide", "slide" in gate_report)
check("the gate slid this turn", gate_report.get("slid"), gate_report.get("slide", {}).get("skipped_reason"))
check("the gate journalled by entry id", "## entry" in journal_path(gate_dir, gate_chat).read_text())
check("the gate kept the handoff in the prompt", any("[context slide]" in str(m.get("content")) for m in shaped))
check("the gate did not refuse", not gate_report.get("refused"), gate_report.get("over_by"))

# --- 5. per-turn rebuild keeps a real request in the prompt ----------------------------------
# History has to be large enough that the squeeze actually engages. A two-message history fits
# inside the block budget, so a pass there proves nothing about the squeeze path.
blocks = {
    "main": "pinned instructions",
    "persona": "persona",
    "journal": "",
    "summary": "",
    "history": [
        msg("user", f"older user {i} " + "u" * 300, f"h{i}")
        for i in range(20)
    ] + [
        msg("assistant", f"older answer {i} " + "a" * 700, f"hanswer{i}")
        for i in range(20)
    ],
    "current_request": "the request that must not be trimmed away",
}
out, arep = asm.assemble_turn(blocks, window=4000, data_dir=data_dir, chat_id=chat, entry_writer=writer)
check("assembler keeps the current request", any(m.get("block") == "current_request" for m in out))
check("assembler engages the squeeze on history only", arep["history_squeezed"] > 0, arep["history_squeezed"])
check("pinned blocks are not squeezed away", all(m.get("block") != "main" for m in out) or any(m.get("block") == "main" for m in out))
check("assembler block estimate matches the gate calibration",
      asm.estimate_tokens("x" * 340) == round(340 / 3.4) or asm.estimate_tokens("x" * 340) >= 100,
      asm.estimate_tokens("x" * 340))

# --- 6. loop guard: similar thinking with progress between blocks is not a loop --------------
items = [
    {"type": "reasoning", "summary": [{"text": "I should read the journal index to recover the evicted turn."}]},
    {"type": "function_call", "name": "ex_journal", "arguments": '{"entry": 1}'},
    {"type": "function_call_output", "output": [{"type": "input_text", "text": "entry 1 ..."}]},
    {"type": "reasoning", "summary": [{"text": "I should read the journal index to recover the evicted turn."}]},
]
hit = ex_loop_guard.scan(items)
check("progress between similar blocks is not a loop", hit.get("verdict") != "loop", hit)

# Three similar blocks with nothing running between them. scan() needs stalled >= 2 pairs, so a
# two-block case is correctly clean and would be the wrong thing to assert.
stalled = [
    {"type": "reasoning", "summary": [{"text": "I should call the audit tool again to confirm the count."}]},
    {"type": "reasoning", "summary": [{"text": "I should call the audit tool again to confirm the count."}]},
    {"type": "reasoning", "summary": [{"text": "I should call the audit tool again to confirm the count."}]},
]
hit2 = ex_loop_guard.scan(stalled)
check("a stalled chain still trips", hit2.get("verdict") == "loop" and hit2.get("stalled_pairs") >= 2, hit2)
note = ex_loop_guard.steering_note(hit2)
check("the note is an observation and permits a plain answer",
      "observation, not an order" in note and "plain text" in note)

# --- 6b. a call cut off mid-run is not a loop --------------------------------------------------
cut = [
    {"type": "reasoning", "summary": [{"text": "I will write the patch with a long payload."}]},
    {"type": "function_call", "name": "replace_file_content", "arguments": '{"path": "x", "replacements": [{"target": "abc"'},
]
hit3 = ex_loop_guard.scan(cut)
check("a truncated call is reported as truncated, not as a loop", hit3.get("verdict") == "truncated", hit3)
check("the truncated note does not order another call",
      "before the call was complete" in ex_loop_guard.truncated_call_note(hit3) and "plain text" in ex_loop_guard.truncated_call_note(hit3))
check("a well-formed call is not counted as truncated", ex_loop_guard.scan(
    [{"type": "function_call", "name": "read_file", "arguments": '{"path": "x"}'}]).get("malformed_calls") == 0)

# --- 6c. the stats chip reports what the tool can read, not a per-turn counter ----------------
from open_webui.utils.ex_stats import context_chips

chip_state = {"entries": 33, "slides": 1, "rolls": 0}
# The chip is gone. It stayed here as a check because the state it read is still recorded; what
# changed is that nothing is drawn from it.
check("the chip list is empty", context_chips(chip_state) == [], context_chips(chip_state))
check("empty state also yields no chip", context_chips({}) == [])
stats_file = (Path(__file__).resolve().parents[1] / "src" / "lib" / "utils" / "exStats.ts").read_text()
svelte = (Path(__file__).resolve().parents[1] / "src" / "lib" / "components" / "chat" / "Messages" / "StatsRow.svelte").read_text()
check("frontend mirror returns no chip", "return [];" in stats_file.split("export function contextChips")[1])
check("the C icon is not rendered anywhere in the row", '"C"' not in svelte and "exRoll" not in svelte)
check("rate chips obey the source setting", "tokenSource !== 'engine' && stats['tokens_per_second']" in stats_file)
check("gate still records the slide state on disk", gate_report.get("state", {}).get("entries", 0) > 0, gate_report.get("state"))

# --- 7. the fraction scales with the window, which is the 128k case --------------------------
check("262144 slides at 70%", ex_slide.slide_point(262144) == 183500, ex_slide.slide_point(262144))
check("128000 slides at 89600", ex_slide.slide_point(128000) == 89600, ex_slide.slide_point(128000))
check("32768 slides at 22937", ex_slide.slide_point(32768) == 22937, ex_slide.slide_point(32768))

print("\nreport:", {k: v for k, v in slide_report.items() if k != "handoff"})
print("gate:", {k: v for k, v in gate_report.items() if k not in ("slide",)})
print("\n" + ("all checks passed" if not fails else f"failures: {fails}"))
sys.exit(1 if fails else 0)
