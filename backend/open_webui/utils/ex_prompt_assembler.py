"""Block prompt assembler for the EX fork (dev). Ported from SillyTavern's prompt manager.

Cloned to refsourcecode/SillyTavern for this, and the three mechanics worth stealing are here:

  1. the prompt is built as named blocks in a fixed order, every request, from scratch;
  2. the budget is window minus response reserve (`openai_max_context - openai_max_tokens`,
     PromptManager.js:1677), not the whole window;
  3. only the chat-history block is squeezed, and pinned blocks cannot be detached
     (`isPromptDeletionAllowed`). SillyTavern only warns when history drops below a threshold.
     Here the warning became an action: once history is too thin, the assembler switches to a
     fresh-turn build, which is what kills chat death.

Why that matters for the failure in the screenshot. The agent read a prompt where the only
content left was system instructions plus a memory listing, concluded there was no user request,
and answered by acknowledging. That is what happens when a growing conversation is trimmed
until the request itself is gone. Per-turn rebuild fixes it structurally: history is optional,
the request is never optional, and the evicted half is reachable by id through the journal.

Blocks, in order. Pinned blocks survive trimming; history is the only block that shrinks.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from open_webui.utils.ex_context_window import CHARS_PER_TOKEN
from open_webui.utils.ex_journal import append_journal, journal_path
from open_webui.utils.ex_reconstruct import reconstruct_block, write_reconstruct

log = logging.getLogger(__name__)

# Response reserve. SillyTavern's rule: never spend the whole window on the prompt.
RESPONSE_RESERVE = 2048

# History below this share of the remaining budget means the conversation is effectively gone,
# so the assembler stops trying to preserve it and rebuilds the turn from scratch.
HISTORY_DANGER_FRACTION = 0.15

BLOCK_ORDER = [
    "main",            # pinned instructions / persona core
    "persona",
    "journal",         # handoff block naming ids of evicted turns
    "summary",         # summary chain, when one exists
    "history",         # the only squeezable block
    "current_request", # never dropped, never summarised away
]

PINNED = frozenset({"main", "persona", "journal", "current_request"})

ENTRY_WRITER = Callable[[dict], None]


def estimate_tokens(text: str) -> int:
    """Coarse count on the same calibration the gate uses.

    It used to be len(text) // 4 while ex_context_window measures 3.4 characters per token. Two
    calibrations on the same prompt disagree, and the assembler would declare a block budget met
    that the gate then measured as over, which is a refusal arriving from inside a module that
    said it fit. One calibration, and the engine's own count is still the authority.
    """
    if not text:
        return 0
    return max(1, int(len(str(text)) / CHARS_PER_TOKEN))


def _block_text(block: Any) -> str:
    if isinstance(block, list):
        return "\n".join(str(m.get("content", "")) for m in block)
    return str(block or "")


def assemble(
    blocks: dict[str, Any],
    *,
    window: int,
    data_dir: Any = None,
    chat_id: str = "",
    entry_writer: ENTRY_WRITER | None = None,
    response_reserve: int = RESPONSE_RESERVE,
    fresh_turn: bool = False,
) -> tuple[list[dict], dict]:
    """Build one request from scratch, in block order. Returns the messages and the budget report.

    `fresh_turn` is the "new chat every turn" mode: no prior conversation array is carried, the
    prompt is pinned blocks plus the handoff plus the current request. It is what runs when
    history has been squeezed below the danger line, because a turn with no real request in it
    produces a model that answers by acknowledging instead of working.
    """
    window = int(window or 0)
    budget = max(1, window - int(response_reserve))
    report: dict[str, Any] = {
        "window": window,
        "response_reserve": int(response_reserve),
        "budget": budget,
        "block_order": BLOCK_ORDER,
        "usage": {},
        "pinned_kept": [],
        "history_squeezed": 0,
        "journal_ids": [],
        "fresh_turn": bool(fresh_turn),
    }

    history = list(blocks.get("history") or [])
    history_tokens = sum(estimate_tokens(_block_text(m)) for m in history)

    pinned = {name: blocks.get(name) for name in BLOCK_ORDER if name in PINNED}
    pinned_tokens = sum(estimate_tokens(_block_text(b)) for b in pinned.values())
    summary_tokens = estimate_tokens(_block_text(blocks.get("summary")))

    # SillyTavern's warning threshold, turned into a decision.
    remaining = budget - pinned_tokens - summary_tokens
    if remaining <= 0:
        report["blocked_reason"] = (
            "pinned blocks alone exceed the budget; history is dropped entirely and nothing "
            "is silently truncated"
        )
        history_kept, history_dropped = [], history
    elif history_tokens > remaining:
        room = max(0, int(remaining * (1.0 if not fresh_turn else HISTORY_DANGER_FRACTION)))
        keep: list[dict] = []
        running = 0
        for message in reversed(history):
            cost = estimate_tokens(_block_text(message))
            if running + cost > room:
                break
            keep.insert(0, message)
            running += cost
        history_kept, history_dropped = keep, [m for m in history if m not in keep]
        report["history_squeezed"] = len(history_dropped)
    else:
        history_kept, history_dropped = history, []

    if entry_writer is not None and history_dropped:
        # Entry ids are numbered here so the handoff names something the journal tool can
        # actually read. A message id alone is not resolvable by read(entry=N), which is the
        # gap that made the handoff a pointer to nothing in the earlier test runs.
        for n, message in enumerate(history_dropped, start=1):
            entry = {
                "id": n,
                "turn": n,
                "kind": message.get("type") or message.get("role") or "message",
                "message_id": message.get("id"),
                "summary": str(message.get("content", ""))[:200],
                "has_full_text": bool(message.get("content")),
                "text": str(message.get("content", "")),
                "ts": int(time.time()),
            }
            entry_writer(entry)
            report["journal_ids"].append(n)

    forced_fresh = bool(fresh_turn) or (
        history_kept
        and remaining > 0
        and sum(estimate_tokens(_block_text(m)) for m in history_kept)
        <= int(remaining * HISTORY_DANGER_FRACTION)
    )
    report["history_below_danger"] = forced_fresh

    out: list[dict] = []
    for name in BLOCK_ORDER:
        if name == "history":
            report["usage"][name] = sum(estimate_tokens(_block_text(m)) for m in history_kept)
            if not forced_fresh:
                out.extend(history_kept)
            continue
        if name == "current_request":
            current = blocks.get("current_request")
            if not current:
                report["blocked_reason"] = "no current request to assemble; refusing to send an empty turn"
                continue
            report["usage"][name] = estimate_tokens(_block_text(current))
            block_entry = current if isinstance(current, dict) else {"role": "user", "content": str(current)}
            block_entry = dict(block_entry)
            block_entry["block"] = "current_request"
            out.append(block_entry)
            continue
        text = _block_text(pinned.get(name) if name in PINNED else blocks.get(name))
        if not text:
            continue
        report["usage"][name] = estimate_tokens(text)
        role = "system" if name != "current_request" else "user"
        out.append({"role": role, "content": text, "block": name})
        if name in PINNED:
            report["pinned_kept"].append(name)

    # The reconstruction is written every turn, not only when something was evicted, because a fresh
    # build needs it every turn. It is the minimum set that lets a weak model pick the conversation
    # back up: prior context, the last full reasoning loop, the last response, the last user message,
    # and the path of the text file holding all four.
    parts = {}
    if data_dir is not None and chat_id:
        report["reconstruct"] = write_reconstruct(
            data_dir, chat_id, history, prior_context=_block_text(blocks.get("summary"))
        )
        parts = report["reconstruct"]["parts"]

    if parts or report["journal_ids"]:
        handoff = reconstruct_block(data_dir if data_dir is not None else "", chat_id, parts, report["journal_ids"])
        insert_at = 1 if out and out[0].get("block") == "main" else 0
        # System role, always. A handoff delivered as a user message reads as something the user
        # said, and the agent answers the handoff instead of the request. That is a smaller version of
        # the same chat death the pinned-block work was meant to stop.
        out.insert(insert_at, {"role": "system", "content": handoff, "block": "journal"})

    # Safety net for the case where the pinned blocks alone exceed the budget. Refusing is not the
    # only move left: the smallest prompt that still contains a real request is main plus the
    # reconstruction block plus the request, and that is what goes out when it fits. When even the
    # minimum does not fit, the block is reported rather than quietly truncated.
    if report.get("blocked_reason"):
        minimum = [m for m in out if m.get("block") in ("main", "journal", "current_request")]
        minimum_tokens = sum(estimate_tokens(str(m.get("content", ""))) for m in minimum)
        report["safety_net"] = {"parts": [m.get("block") for m in minimum], "minimum_tokens": minimum_tokens}
        if minimum_tokens <= budget:
            out = minimum
            report["usage"] = {m.get("block"): estimate_tokens(str(m.get("content", ""))) for m in out}
            report["blocked_reason"] = None
            report["used_minimum_set"] = True
        else:
            report["minimum_still_over"] = minimum_tokens - budget

    report["tokens_after"] = sum(report["usage"].values())
    report["still_over"] = report["tokens_after"] > budget
    return out, report


def assemble_turn(
    blocks: dict[str, Any],
    *,
    window: int,
    data_dir: Any = None,
    chat_id: str = "",
    entry_writer: ENTRY_WRITER | None = None,
    response_reserve: int = RESPONSE_RESERVE,
) -> tuple[list[dict], dict]:
    """Per-turn entry point: always rebuild, never carry a trimmed conversation forward."""
    return assemble(
        blocks,
        window=window,
        data_dir=data_dir,
        chat_id=chat_id,
        entry_writer=entry_writer,
        response_reserve=response_reserve,
        fresh_turn=True,
    )
