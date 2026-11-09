"""End-to-end shape test for the slide loop, no engine, no strata request.

Closes the loop the agent needs: slide evicts, the writer journals by message id, and the
builtin tool reads that text back. If a message id in the handoff cannot be resolved through
the tool, this prints it as a failure rather than assuming the mapping works.
"""
import asyncio
import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from open_webui.utils import ex_slide
from open_webui.utils.ex_journal import append_journal, journal_index, journal_path, read_journal
from open_webui.tools.builtins import ex_journal as tool

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

data_dir = Path(tempfile.mkdtemp(prefix="slide_test_"))
chat = "test_chat"
path = journal_path(data_dir, chat)

msgs = [msg("system", "pinned head", "m0")]
for i in range(20):
    msgs.append(msg("user", f"question {i} " + "u" * 120, f"u{i}"))
    msgs.append(msg("assistant", f"answer {i} " + "a" * 400, f"a{i}"))
    msgs.append(msg("assistant", f"reasoning {i} " + "r" * 300, f"r{i}", "reasoning"))
msgs.append(msg("user", "newest question that must survive", "u_new"))

written = []

def writer(entry):
    append_journal(path, [entry], chat)
    written.append(entry)

# Window 4000 rather than the real 262144. At the real window this prompt never crosses the slide
# point, so the harness was printing passes for a slide that had not fired. The fraction is what
# matters and it is asserted separately below.
shaped, report = ex_slide.slide(msgs, data_dir=data_dir, window=4000, entry_writer=writer)

check("slide fired", report["slid"], report)
check("handoff in prompt", any("[context slide]" in str(m.get("content")) for m in shaped))
check("newest turn survived", shaped[-1]["id"] == "u_new")
check("last reasoning survived", any(m.get("type") == "reasoning" for m in shaped))
check("ids recorded", len(report["journal_ids"]) == len(written), (len(report["journal_ids"]), len(written)))
check("fraction scales with window", ex_slide.slide_point(128000) == 89600 and ex_slide.slide_point(32768) == 22937,
      (ex_slide.slide_point(128000), ex_slide.slide_point(32768)))

req = types.SimpleNamespace(
    app=types.SimpleNamespace(state=types.SimpleNamespace(data_dir=str(data_dir))),
    state=types.SimpleNamespace(chat_id=chat),
)
T = tool.Tools()

# The builtin methods are async. Called without awaiting, T.index returns a coroutine and the check
# compares a coroutine against a string, which is why this reported a failure while the tool itself
# was fine.
idx = asyncio.run(T.index(__request__=req))
check("tool index sees the journal", "journal: test_chat" in idx, idx[:120])

mid = report["journal_ids"][0]
text = asyncio.run(T.read(needle=str(mid), __request__=req))
check("handoff id resolves through the tool", str(mid) in text and "question 0" in text, text[:160])

one = asyncio.run(T.read(entry=1, __request__=req))
check("read by entry returns stored text", "question 0" in one or "u" * 60 in one, one[:160])
check("handoff names entry numbers the tool accepts", "entries: " in report["handoff"], report["handoff"][:200])

print(f"\nenv override in use: SLIDE_FRACTION={ex_slide.SLIDE_FRACTION}")
print("report:", {k: v for k, v in report.items() if k != "journal_ids"})
print("\n" + ("all checks passed" if not fails else f"failures: {fails}"))
sys.exit(1 if fails else 0)
