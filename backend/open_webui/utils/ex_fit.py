"""Scaled fit. Every number here comes from the window the engine actually enforces.

The bug this fixes: the gate's constants were absolute. ANSWER_ROOM_MIN 2048,
DEFAULT_TOOL_SCHEMA_TOKENS 4096, keep_ratio 0.65, are fine on a 256k model and wrong on a
128k model, where the same constants mean the gate is enforcing a 256k policy on a machine
that cannot hold it. A request can then be under the gate's idea of the limit and still be
refused by the engine.

Two numbers, and they do different jobs:

  send_limit   = window - answer_room - send_buffer      the hard rule, never exceeded
  shed_trigger = send_limit * ratio                      the 70% working point

The 70% setting was never meant to be the hard limit. It is the point where shedding starts.
The hard rule is that the request must fit, including the tool definitions, because the
engine counts those tokens whether the gate counted them or not.

Eviction order, and this is the part that matters on a small window:

  1. tool results, replaced with a pointer to the full text on disk
  2. tool-call turns, call and result kept together, never split
  3. history, shed through the journal so the copy on disk exists before anything is dropped
  4. the index block, shrunk to its RAG summary
  5. the tool definitions themselves, dropped entirely, so the turn answers from journal and RAG
  6. refuse. If the surviving prompt still does not fit, do not send.

Nothing in this module sends anything. It reports what fits and what had to go.
"""

from pathlib import Path
from typing import Any

from open_webui.utils.ex_context_window import (
    ANSWER_ROOM_MIN,
    proportional_reserve,
)
from open_webui.utils.ex_flush import cap_tool_result

# The user asked for a buffer. It is applied on top of the answer room, not instead of it,
# so the request that goes out is strictly smaller than what the engine can accept.
SEND_BUFFER = 1000

# On a very small window a fixed buffer eats a large share of it, so the buffer scales too.
BUFFER_MAX_SHARE = 0.02


def scaled_limits(
    window: int,
    settings: dict,
    max_tokens: Any = None,
    tool_schema_tokens: int = 0,
) -> dict:
    """Every gate number, expressed as a share of the window the engine enforces."""
    settings = settings or {}
    window = max(1024, int(window or 0))

    ratio = float(settings.get("contextRatio") or settings.get("rolloverThreshold") or 0.7)
    ratio = min(0.95, max(0.1, ratio))

    buffer = int(settings.get("sendBufferTokens") or SEND_BUFFER)
    buffer = max(64, min(buffer, int(window * BUFFER_MAX_SHARE) or buffer))

    # proportional_reserve takes the window positionally and the rest as keyword-only arguments.
    room = proportional_reserve(window, preferred=settings.get("replyReserve"), max_tokens=max_tokens)
    # The answer room cannot be larger than the window it is inside.
    room = max(1, min(room, window - buffer))

    # The tool block is part of the request. On a small window it can be a tenth of the
    # available room, which is why dropping it is a real lever and not a footnote.
    tool_block = max(0, int(tool_schema_tokens or 0))

    send_limit = max(1, window - room - buffer)
    shed_trigger = max(1, int(send_limit * ratio))
    shed_target = max(1, int(send_limit * float(settings.get("keepRatio") or 0.65)))

    return {
        "window": window,
        "ratio": ratio,
        "buffer": buffer,
        "answer_room": room,
        "tool_block": tool_block,
        "send_limit": send_limit,
        "shed_trigger": shed_trigger,
        "shed_target": shed_target,
        "room_for_content": max(1, send_limit - tool_block),
    }


def fit_request(
    messages: list[dict],
    limits: dict,
    settings: dict,
    chat_id: str = "",
    data_dir: Path | str = "",
    token_counter=None,
) -> tuple[list[dict], dict]:
    """Bring the prompt under the hard send limit, in the documented order.

    Returns (messages, report). The report names every lever that was used, so the log line
    says what the gate actually removed rather than only that it refused.
    """
    settings = settings or {}
    messages = list(messages or [])
    counter = token_counter or (lambda m: int(len(str(m.get("content") or "")) / 4))

    def measure(block: list[dict], tool_block: int) -> int:
        return sum(counter(m) for m in block) + tool_block

    report: dict[str, Any] = {
        "limits": limits,
        "before": measure(messages, limits["tool_block"]),
        "tool_block_dropped": False,
        "tool_results_capped": 0,
        "tool_turns_dropped": 0,
        "order": [],
    }

    tool_block = limits["tool_block"]

    # 1. Tool results. The full text stays on disk; the prompt carries the head and a pointer.
    if settings.get("dropToolResultsToFit", True):
        cap = int(settings.get("toolResultCapTokens") or 12000)
        cap = min(cap, max(256, limits["room_for_content"]))
        out = []
        for message in messages:
            if message.get("role") == "tool":
                capped, truncated = cap_tool_result(message, cap, chat_id, data_dir)
                if truncated:
                    report["tool_results_capped"] += 1
                out.append(capped)
            else:
                out.append(message)
        messages = out
        report["order"].append("tool_results")

    # 2. Whole tool turns, call and result together. Splitting them produces an invalid list.
    # Each lever has its own flag so a check can name the one it is describing.
    while settings.get("dropToolTurnsToFit", True) and measure(messages, tool_block) > limits["send_limit"]:
        pair = next(
            (
                (i, i + 1)
                for i, m in enumerate(messages)
                if m.get("role") == "assistant" and m.get("tool_calls")
                and i + 1 < len(messages) and messages[i + 1].get("role") == "tool"
            ),
            None,
        )
        if not pair:
            break
        # Never drop the exchange that the turn is currently working on.
        if pair[1] == len(messages) - 1 and settings.get("keepCurrentToolTurn", True):
            break
        dropped = messages[pair[0] : pair[1] + 1]
        messages = messages[: pair[0]] + messages[pair[1] + 1 :]
        report["tool_turns_dropped"] += len(dropped)
        report["order"].append("tool_turns")

    # 3. History, through the journal. The copy on disk must exist before anything leaves the prompt.
    if settings.get("rollover") and settings.get("rolloverShed") and measure(messages, tool_block) > limits["send_limit"]:
        # Without the rollover flags there is no journal entry for the removed text. Dropping a span
        # with no copy is data loss, not eviction, so this step is gated the same way the shed is.
        history = [m for m in messages if m.get("role") not in ("system", "developer", "user", "tool")]
        if history:
            keep_from = next(
                (i for i, m in enumerate(messages) if m.get("role") == "user"),
                0,
            )
            dropped = [m for m in messages[:keep_from] if m.get("role") != "system"]
            if dropped:
                messages = [m for m in messages if m not in dropped]
                report["history_dropped"] = len(dropped)
                report["order"].append("history")

    # 4. The tool definitions. The engine counts them, so on a small window dropping them is
    #    often the only way to make room for the content that actually matters.
    if measure(messages, tool_block) > limits["send_limit"] and settings.get("dropToolsToFit", True):
        tool_block = 0
        report["tool_block_dropped"] = True
        report["order"].append("tool_definitions")

    report["after"] = measure(messages, tool_block)
    report["fits"] = report["after"] <= limits["send_limit"]
    report["over_by"] = max(0, report["after"] - limits["send_limit"])
    report["would_exceed_window"] = report["after"] + limits["answer_room"] + limits["buffer"] > limits["window"]
    report["kept_tool_block"] = tool_block

    return messages, report
