"""Per-turn eviction for the EX fork (dev).

The engine gate refuses a request whose prompt is larger than the window, and that refusal is
the failure the user sees: "request exceeds the context window ... requests are never
truncated". A request larger than the window is physically impossible to serve, so the
correct bypass is not to raise the ceiling above what the engine holds, it is to stop
refusing and start evicting before the request is built. That is what this module does.

Order, from the fork's evict design, oldest and least load bearing first:
  1. tool output beyond the kept window, replaced with the trim stub;
  2. whole oldest tool turns;
  3. whole oldest conversation turns, user turns included;
  4. never: pinned head, the newest turn, the journal, memory, the current task.

Deterministic, no model call, no summarisation. Every removal is recorded so the UI can show
what left and the journal can still point at the full text.
"""
from __future__ import annotations

import logging
from typing import Any

from open_webui.utils.ex_context_window import (
    TOOL_STUB,
    is_tool_call_message,
    is_tool_message,
    messages_tokens,
    resolve_window,
    trim_tool_output,
)

log = logging.getLogger(__name__)

# Turn whose output is the newest and therefore never evicted.
PROTECT_TAIL = 1

# Head messages are never evicted, which is also what keeps the KV prefix stable.
PROTECT_HEAD = 1


def _journal_turn(message: dict) -> bool:
    text = message.get("content")
    if not isinstance(text, str):
        return False
    return text.lstrip().startswith("[journal]")


def evict_to_fit(
    messages: list[dict],
    budget: int,
    *,
    keep_tool_turns: int = 3,
    head: int = PROTECT_HEAD,
    tail: int = PROTECT_TAIL,
) -> tuple[list[dict], dict]:
    """Make the request fit by dropping the oldest content, not by refusing it.

    Returns the shaped messages and a report. The report is the part the UI and a test can
    assert on: what was trimmed, what was dropped, and whether the request still does not fit.
    """
    msgs = list(messages or [])
    report: dict[str, Any] = {
        "tokens_before": messages_tokens(msgs),
        "budget": int(budget),
        "trimmed": 0,
        "evicted_turns": 0,
        "protected": {"head": head, "tail": tail, "journal": True},
        "policy": "evict_per_turn",
    }

    if budget <= 0:
        report["still_over"] = False
        return msgs, report

    # 1. Tool output outside the kept window becomes a stub. This is the biggest, cheapest win.
    shaped = trim_tool_output(msgs, keep_tool_turns=keep_tool_turns)
    report["trimmed"] = sum(
        1 for a, b in zip(msgs, shaped) if a is not b and b.get("content") == TOOL_STUB
    )
    if messages_tokens(shaped) <= budget:
        report["still_over"] = False
        return shaped, report

    # 2 and 3. Drop whole oldest turns, as units. A tool call and the results that belong to
    # it leave together, because a call left with no matching result is rejected outright by
    # most chat templates, and that failure is worse than a missing body. Head and the newest
    # turn are never touched, and the journal block is never evicted, because the pointer to a
    # journal entry is what lets the agent recover text that already left the window.
    work = list(shaped)
    # The current request is load bearing even when it is not the newest message, which is the
    # usual shape in an agentic turn: the user asked something, then tool output piled up after it.
    # Protect it by identity, not by index, so deletions before it do not move the guard.
    current_request = None
    for message in work:
        if message.get("role") == "user" and not _journal_turn(message):
            current_request = message

    changed = True
    while changed and messages_tokens(work) > budget:
        changed = False
        for i in range(head, len(work) - tail):
            message = work[i]
            if _journal_turn(message) or message is current_request:
                continue
            # User turns must be evictable too. A policy that only sheds assistant and tool
            # turns can never fit a request whose growth is user text, and then it has nothing
            # left to do but refuse, which is the failure this module exists to avoid.
            if message.get("role") not in ("assistant", "user", "tool", "function_call"):
                continue
            stop = i + 1
            while stop < len(work) and is_tool_message(work[stop]):
                stop += 1
            if stop <= head or stop > len(work) - tail:
                continue
            del work[i:stop]
            report["evicted_turns"] += 1
            changed = True
            break

    report["tokens_after"] = messages_tokens(work)
    report["still_over"] = messages_tokens(work) > budget
    if report["still_over"]:
        # Nothing load bearing left to drop. Say what is blocked instead of pretending.
        report["blocked_reason"] = (
            "only protected content remains (pinned head, journal, newest turn); "
            "the window cannot hold this request"
        )
    return work, report


def window_report(window: Any = None, data_dir: Any = None) -> dict:
    """Small, deterministic summary of the window the caller is working inside."""
    ceiling = resolve_window(window, data_dir)
    return {
        "ceiling": ceiling,
        "hard_gate": False,
        "policy": "evict_per_turn",
        "note": "requests are never refused for size on dev; they are evicted to fit",
    }
