"""Context sliding for the EX fork (dev).

What RiRi described, written down as code:

  1. measure the prompt against the real window;
  2. once it passes 70% of that window (the fraction scales with whatever the window is),
     evict all previous context except the last few agent and user responses and the very
     last reasoning turn;
  3. write everything evicted to the journal, keyed by message id, so nothing is lost;
  4. paste a handoff block back into the prompt saying to refer to the following context and
     continue from it, listing the ids the agent can pull with the journal tool.

Two things this module deliberately does not do:

  - it never refuses a request for size, which was the old behaviour and the thing that froze
    the agent; a request that does not fit is slid, not rejected;
  - it does not call the journal writer itself. It takes the writer as a callback, because a
    chat cannot hold the fork lock while its own write is blocked. The caller runs this outside
    the lock and passes a writer that appends. Journal reads stay outside the lock too.

`evict_to_fit` in ex_evict handles the stubbing and pairing rules; this is the policy layer on
top of it that decides when a slide happens, what survives, and what the agent is told.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable

from open_webui.utils.ex_evict import evict_to_fit
from open_webui.utils.ex_journal import summarise
from open_webui.utils.ex_reconstruct import reconstruct_block, write_reconstruct
from open_webui.utils.ex_context_window import (
    is_tool_call_message,
    is_tool_message,
    messages_tokens,
    resolve_window,
)

log = logging.getLogger(__name__)

# Slide once the prompt passes this share of the window. Scales with the actual window, so a
# 262144 window slides at 183500 and a 32768 window slides at 22937.
# Env override exists so a slide can be forced with a small prompt instead of writing 180k
# tokens just to cross the point. Dev only; unset means the real 70%.
SLIDE_FRACTION = float(os.environ.get("OPENWEBVIBES_EX_SLIDE_FRACTION", "0.70"))

# Kept at the tail: the last few agent and user responses, plus the last reasoning turn.
KEEP_TAIL_TURNS = 4

# Pinned head is never slid. It is also what keeps the KV prefix stable.
KEEP_HEAD = 1

ENTRY_WRITER = Callable[[dict], None]


def journal_entry(message: dict, seq: int) -> dict:
    """The shape append_journal requires.

    A raw message dict is not a journal entry. append_journal reads e['id'], e['turn'], e['ts']
    and e['summary'], so a writer that hands it the message alone raises KeyError mid-turn. The
    entry id is also what read(entry=N) takes, so the handoff has to name this number rather
    than the message id, or the pointer resolves to nothing.
    """
    text = str(message.get("content", ""))
    return {
        "id": int(seq),
        "turn": int(seq),
        "kind": message.get("type") or message.get("role") or "message",
        "message_id": message.get("id"),
        "summary": summarise(text),
        "has_full_text": bool(text),
        "text": text,
        "ts": int(time.time()),
    }


def slide_point(ceiling: int, fraction: float = SLIDE_FRACTION) -> int:
    return int(int(ceiling) * fraction)


def _is_kept_tail(message: dict, tail_start: int, index: int) -> bool:
    if index >= tail_start:
        return True
    # The very last reasoning turn survives even if it sits before the tail window.
    return message.get("type") == "reasoning" or "[journal]" in str(message.get("content", ""))


def slide(
    messages: list[dict],
    *,
    data_dir: Any = None,
    window: Any = None,
    entry_writer: ENTRY_WRITER | None = None,
    fraction: float = SLIDE_FRACTION,
    keep_tail: int = KEEP_TAIL_TURNS,
    keep_head: int = KEEP_HEAD,
    chat_id: str = "",
) -> tuple[list[dict], dict]:
    """Slide the oldest content out, journal it by id, and hand the agent a pointer back."""
    msgs = list(messages or [])
    ceiling = int(window or resolve_window(window, data_dir))
    point = slide_point(ceiling, fraction)
    used = messages_tokens(msgs)
    report: dict[str, Any] = {
        "window": ceiling,
        "fraction": fraction,
        "slide_point": point,
        "tokens_before": used,
        "slid": False,
        "journal_ids": [],
        "message_ids": [],
        "kept_head": keep_head,
        "kept_tail": keep_tail,
    }

    if used <= point:
        report["skipped_reason"] = "prompt is under the slide point"
        return msgs, report

    tail_start = max(keep_head, len(msgs) - keep_tail)
    last_reason = -1
    for i, m in enumerate(msgs):
        if m.get("type") == "reasoning":
            last_reason = i

    evicted: list[dict] = []
    kept: list[dict] = []
    for i, m in enumerate(msgs):
        if i < keep_head or i >= tail_start or i == last_reason:
            kept.append(m)
            continue
        evicted.append(m)

    if not evicted:
        report["skipped_reason"] = "nothing outside the protected head and tail"
        return msgs, report

    report["slid"] = True
    report["evicted_turns"] = len(evicted)

    if entry_writer is not None:
        for seq, message in enumerate(evicted, start=1):
            entry = journal_entry(message, seq)
            entry_writer(entry)
            # journal_ids are the numbers read(entry=N) accepts. message_ids are kept beside them
            # so a caller that tracks messages can map back.
            report["journal_ids"].append(entry["id"])
            if entry.get("message_id"):
                report["message_ids"].append(entry["message_id"])

    head = kept[:keep_head]
    tail = kept[keep_head:]
    parts = {}
    if data_dir is not None and chat_id:
        # The four parts that let a fresh window pick the conversation back up, written as a plain
        # text file so the tool, grep, or the user can read it without the fork running.
        parts = write_reconstruct(data_dir, chat_id, msgs)["parts"]
        report["reconstruct"] = {"parts": parts, "evicted": len(evicted)}
    handoff = handoff_block(report, parts, data_dir, chat_id)
    shaped = head + [{"role": "system", "content": handoff}] + tail

    # Hard backstop: even after the slide, the prompt must fit inside the window with room to
    # answer. If it still does not, this reports what is blocked rather than erroring.
    shaped, evict_report = evict_to_fit(shaped, point)
    report["handoff"] = handoff
    report["tokens_after"] = messages_tokens(shaped)
    report["still_over"] = report["tokens_after"] > ceiling
    report["evict"] = evict_report
    return shaped, report


def handoff_block(report: dict, parts: dict | None = None, data_dir: Any = None, chat_id: str = "") -> str:
    """The block pasted back in place of the evicted turns. Deterministic, no model call.

    Delivered as a system instruction that says plainly this is a new conversation and that the block
    is not something the user said. When it arrived as a user turn the model answered the handoff as
    though the user had asked for an acknowledgement.
    """
    parts = parts or {}
    if parts:
        return reconstruct_block(data_dir if data_dir is not None else "", chat_id, parts, report.get("journal_ids") or [])
    ids = report.get("journal_ids") or []
    lines = [
        "[context slide]",
        "This is a new conversation. Nothing from the previous window carries over except what is",
        "written below, and this block is a system instruction rather than a message from the user.",
        f"{report.get('evicted_turns', 0)} earlier turns were evicted to fit the window "
        f"({report.get('tokens_before')} tokens against a {report.get('slide_point')} slide point).",
    ]
    if ids:
        lines.append(
            "Their full text is in the journal. Pull any of them with the journal tool "
            "(index, then read(entry=N)). entries: " + ", ".join(str(i) for i in ids)
        )
    else:
        lines.append(
            "The journal tool can still list this chat by index; ids were not recorded for "
            "these turns because the caller did not supply message ids."
        )
    return "\n".join(lines)
