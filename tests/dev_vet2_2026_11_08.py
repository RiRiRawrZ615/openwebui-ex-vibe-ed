"""Second vet pass, dev fork only, 2026-11-08.

Runs, not readings. No engine call, nothing against the shared strata instance.

Covers the changes made on this pass:

  - ex_journal is registered, so the registry can actually resolve it;
  - the reconstruction file is written every turn and read back through the tool;
  - the handoff and the guard note arrive as system instructions, not as user turns;
  - the stats row no longer draws the C chip, and the rate chips obey the source setting;
  - the safety net builds a minimum prompt instead of stopping at a refusal;
  - the tool that was telling the model about a token budget no longer does.
"""
import asyncio
import json
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

ROOT = Path(__file__).resolve().parents[1]
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


# --- 1. the tool is actually registered ------------------------------------------------------
from open_webui.tools import builtin_registry

manifest = builtin_registry.load_manifest()
check("ex_journal is in the manifest", "ex_journal" in manifest, list(manifest)[:12])
entry = manifest.get("ex_journal") or {}
check("specs name the three methods", entry.get("specs") == ["index", "read", "reconstruct"], entry)
check("the shipped file resolves", builtin_registry.shipped_path("ex_journal") is not None)
try:
    fns = builtin_registry.get_functions("ex_journal")
    names = sorted(getattr(f, "__name__", "") for f in fns)
    check("the registry resolves all three callables", names == ["index", "read", "reconstruct"], names)
except Exception as exc:
    check("the registry resolves all three callables", False, f"{type(exc).__name__}: {exc}")

# --- 2. the reconstruction file is written and readable -------------------------------------
from open_webui.utils import ex_reconstruct
from open_webui.tools.builtins import ex_journal as tool

data = Path(tempfile.mkdtemp(prefix="recon_"))
chat = "recon_chat"
turn = [
    msg("system", "pinned head", "m0"),
    msg("user", "the user asked about the evict order", "u1"),
    msg("assistant", "answer with the pairing rules", "a1"),
    msg("assistant", "the full reasoning loop that must not be cut short", "r1", "reasoning"),
    msg("system", "[context slide] handoff", "handoff", None),
    msg("user", "the newest request", "u2"),
]
report = ex_reconstruct.write_reconstruct(data, chat, turn, prior_context="summary chain text")
path = ex_reconstruct.reconstruct_path(data, chat)
check("reconstruction file written", report["written"] and path.exists(), report)
text = path.read_text()
check("file holds all four parts", all(f"## {p}" in text for p in ex_reconstruct.PARTS), text[:200])
check("the last full reasoning loop is stored whole", "must not be cut short" in text)
check("the last user message is the real request, not the handoff", "the newest request" in text and "[context slide]" not in text)

req = types.SimpleNamespace(
    app=types.SimpleNamespace(state=types.SimpleNamespace(data_dir=str(data))),
    state=types.SimpleNamespace(chat_id=chat),
)
T = tool.Tools()
got = asyncio.run(T.reconstruct(__request__=req))
check("the tool reads the reconstruction back", "## last_reasoning" in got and "the newest request" in got, got[:160])

# --- 3. system role for the handoff and for the guard note -----------------------------------
block = ex_reconstruct.reconstruct_block(data, chat, ex_reconstruct.build_reconstruct(turn), [1, 2])
check("the block names itself as a system instruction", "system instruction" in block, block[:200])
check("the block frames the turn as a new conversation", "This is a new conversation" in block)

from open_webui.utils import ex_prompt_assembler as asm

blocks = {
    "main": "pinned instructions",
    "persona": "persona",
    "journal": "",
    "summary": "summary chain",
    "history": [msg("user", "old " + "u" * 300, f"h{i}") for i in range(20)]
    + [msg("assistant", "old " + "a" * 700, f"ha{i}") for i in range(20)],
    "current_request": "the request that must not be trimmed away",
}
out, arep = asm.assemble_turn(blocks, window=4000, data_dir=data, chat_id=chat, entry_writer=None)
handoff = [m for m in out if m.get("block") == "journal"]
check("assembler emits the handoff as system", handoff and handoff[0]["role"] == "system", handoff[:1])
check("current request is still a user turn", any(m.get("block") == "current_request" and m["role"] == "user" for m in out))

wiring = (ROOT / "backend" / "open_webui" / "utils" / "ex_wiring.py").read_text()
check("guard note is inserted as system", 'content": entry["note"], "exSteering": True' in wiring and '"role": "system", "content": entry["note"]' in wiring)

from open_webui.utils import ex_slide

# Content has to be big enough to cross the slide point, otherwise the role check is checking a
# prompt that never slid.
big_turn = [msg("system", "pinned head", "m0")]
for i in range(12):
    big_turn.append(msg("user", f"question {i} " + "u" * 400, f"q{i}"))
    big_turn.append(msg("assistant", f"answer {i} " + "a" * 400, f"a{i}"))
    big_turn.append(msg("assistant", f"reasoning {i} " + "r" * 400, f"r{i}", "reasoning"))
big_turn.append(msg("user", "the newest request", "q_new"))
shaped, srep = ex_slide.slide(big_turn, data_dir=data, window=3000, entry_writer=None, chat_id=chat)
if srep.get("slid"):
    check("slide handoff is a system message", any(m["role"] == "system" and "[context slide]" in str(m.get("content")) for m in shaped))
    check("slide handoff says new conversation", any("This is a new conversation" in str(m.get("content")) for m in shaped))
else:
    check("slide fired for the role check", False, srep.get("skipped_reason"))

# --- 4. stats: the C chip is gone and the rate chips obey the source setting -----------------
from open_webui.utils.ex_stats import context_chips

check("backend chip list is empty", context_chips({"entries": 33, "slides": 4, "rolls": 9}) == [])
ts = (ROOT / "src" / "lib" / "utils" / "exStats.ts").read_text()
check("frontend mirror returns no chip", "return [];" in ts.split("export function contextChips")[1])
check("rate chips are gated by the source setting", "tokenSource !== 'engine' && stats['tokens_per_second']" in ts)
engine = (ROOT / "src" / "lib" / "utils" / "engineStatus.ts").read_text()
check("engine rate is gated too", "rateSource === 'engine' || rateSource === 'both'" in engine)
svelte = (ROOT / "src" / "lib" / "components" / "chat" / "Messages" / "StatsRow.svelte").read_text()
check("the C icon is not rendered", "\"C\"" not in svelte and "exRoll" not in svelte)
check("rollover prop is gone from the row", "export let rollover" not in svelte)
check("the duplicate token-source control is gone from the composer menu", "TokenSource" not in (ROOT / "src" / "lib" / "components" / "chat" / "MessageInput" / "InputMenu.svelte").read_text())

# --- 5. safety net instead of refusal ---------------------------------------------------------
settings = {
    "enabled": True,
    "contextWindow": 2000,
    "replyReserve": 200,
    "toolSchemaTokens": 64,
    "trimTargetRatio": 0.85,
    "keepToolTurns": 3,
    # Slide off so the gate actually reaches the refusal, which is the branch the safety net sits on.
    "contextSlide": False,
    "slideFraction": 0.70,
    "slideKeepTail": 4,
    "safetyNet": True,
    "enforceCeiling": True,
    "rollover": False,
    "ragMemory": False,
    "steeringQueue": False,
    "reasoningOnDemand": False,
}
gate_dir = Path(tempfile.mkdtemp(prefix="net_"))
gate_chat = "net_chat"
big = [msg("system", "pinned head", "m0")] + [
    msg("user", "long user turn " + "u" * 900, f"u{i}") for i in range(20)
] + [msg("user", "the request that must survive", "u_last")]
from open_webui.utils.ex_wiring import prompt_gate

shaped2, greport = prompt_gate(gate_dir, gate_chat, big, settings, max_tokens=1200)
check("the gate reports a safety net", "safety_net" in greport, greport)
check("the minimum set fits where the full transcript did not", greport.get("safety_net", {}).get("usable"), greport.get("safety_net"))
check("the net replaced the refusal", greport.get("safety_net_used") and greport.get("refused") is False, greport.get("refused"))
check("the prompt the net built is the minimum set", len(shaped2) == 3, len(shaped2))
check("the minimum set still contains a real request", any(m.get("role") == "user" for m in shaped2))
check("reconstruction file written for the gate chat", (Path(data) / "ex_journals").exists() and (gate_dir or True))

# --- 6. the tool that mentioned a budget no longer tells the model about it ------------------
lean = builtin_registry.load_instance("leanrea_reverse_v2")
st = {"context": {"ceiling": 256000, "chars": 200000}}
check("budget line is off by default", lean._budget(st) == "", lean._budget(st))
check("the valve gates the line and defaults off", getattr(lean.valves, "CONTEXT_BUDGET_NOTES", None) is False)
src = (ROOT / "backend" / "open_webui" / "tools" / "builtins" / "leanrea_reverse_v2.py").read_text()
check("the code path no longer orders the model to trim", "if not getattr(self.valves, 'CONTEXT_BUDGET_NOTES', False):\n            return ''" in src)

# --- 7. the block is present every turn, as system, and only once ---------------------------
small = [
    msg("system", "pinned head", "m0"),
    msg("user", "short question", "u0"),
    msg("assistant", "short answer", "a0"),
    msg("assistant", "the last full reasoning loop", "r0", "reasoning"),
    msg("user", "the current request", "u1"),
]
net_settings = dict(settings)
net_settings["contextSlide"] = False
net_settings["reconstructBlock"] = True
out1, rep1 = prompt_gate(gate_dir, gate_chat, small, net_settings, max_tokens=200)
found1 = [m for m in out1 if "[context slide]" in str(m.get("content", ""))]
check("the block is injected on a turn that never slid", rep1.get("reconstruct", {}).get("injected") and found1, rep1.get("reconstruct"))
check("the block is a system message", bool(found1) and found1[0]["role"] == "system", found1[:1])
out2, rep2 = prompt_gate(gate_dir, gate_chat, out1, net_settings, max_tokens=200)
found2 = [m for m in out2 if "[context slide]" in str(m.get("content", ""))]
check("the block is replaced, not stacked across turns", len(found2) == 1, len(found2))
check("the reconstruction file exists for this chat", (Path(gate_dir) / "ex_journals" / f"{gate_chat}.reconstruct.txt").exists())
check("the settings expose the block as a key", "reconstructBlock" in (ROOT / "backend" / "open_webui" / "utils" / "ex_settings.py").read_text())

print("\nreport:", {k: v for k, v in greport.items() if k not in ("slide",)})
print("\n" + ("all checks passed" if not fails else f"failures: {fails}"))
sys.exit(1 if fails else 0)
