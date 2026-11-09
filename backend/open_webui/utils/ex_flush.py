"""Agentic flush: the prompt for each tool-loop iteration is rebuilt from nothing but
what that iteration needs, instead of carrying the conversation forward.

Why this is the right shape for the failure this chat kept dying on: the engine has no
memory of a chat. Every request is already a new request. What made a request grow was
the message list the fork kept building up between calls, plus the tool results appended
after the gate had already measured. So the fix is not a trick to make the engine
believe the chat is new; it is to actually send a new request each time, with the only
carried memory being what is written to disk.

The exception list is not optional. These pieces have to survive a flush or the loop
stops working, and the honest design is to say which ones:

  1. the system/preamble block    - without instructions the model cannot call tools at all
  2. the reconstruction index      - this is the memory that replaces the evicted turns
  3. the current user message      - the thing being answered
  4. the current tool call and its result - splitting a call from its result produces an
     invalid message list, and dropping the result the model is acting on makes it re-call
     the same tool forever
  5. prefill, when configured       - part of the request template, not history

Everything else is evicted. Each tool result is capped, because a fresh prompt can still
exceed the window when one result is bigger than the window itself; flushing history does
not help if a single result is 60k tokens.
"""

import json
from pathlib import Path
from typing import Any

from open_webui.utils.ex_context_window import estimate_tokens, message_tokens
from open_webui.utils.ex_loop_guard import call_signature

DEFAULT_TOOL_RESULT_CAP = 12000


def cap_tool_result(message: dict, cap_tokens: int, chat_id: str = "", data_dir: Path | str = "") -> tuple[dict, bool]:
    """Keep the head of a tool result and mark the rest as evicted to disk.

    The full text is still on disk in the journal; the prompt carries a stub that says where
    to read it, so the model can pull the rest with read_cache instead of holding it in the
    window for the rest of the turn.
    """
    cap = max(256, int(cap_tokens or DEFAULT_TOOL_RESULT_CAP))
    content = message.get("content")
    text = content if isinstance(content, str) else json.dumps(content)
    if estimate_tokens(text) <= cap:
        return message, False

    # Cut at a character budget, not a token count, so the stub is readable rather than a
    # mid-word slice. The reported numbers are estimates; the gate re-measures the whole list.
    cut = int(cap * 4)
    head = text[:cut]
    note = (
        f"\n[tool result truncated at approximately {cap} tokens. "
        f"Full text ({estimate_tokens(text)} tokens) is on disk at "
        f"{Path(str(data_dir)) / 'ex_journals' / f'{chat_id}.journal.json'}; "
        "read it with read_cache if the omitted part matters.]"
    )
    out = dict(message)
    out["content"] = head + note
    return out, True


def flush_prompt(
    chat_id: str,
    messages: list[dict],
    settings: dict,
    data_dir: Path | str = "",
    index_block: str = "",
    prefill: list[dict] | None = None,
) -> tuple[list[dict], dict]:
    """Build the message list for one agentic iteration."""
    settings = settings or {}
    messages = list(messages or [])

    if not settings.get("agenticFlush"):
        return messages, {"flushed": False, "reason": "flush off"}

    cap = int(settings.get("toolResultCapTokens") or DEFAULT_TOOL_RESULT_CAP)
    report: dict[str, Any] = {
        "flushed": True,
        "mode": "every_iteration" if settings.get("flushEveryIteration") else "every_turn",
        "pieces": [],
        "kept": [],
        "evicted": [],
        "tool_results_truncated": 0,
    }

    # The pieces that must survive, in the order the engine reads them.
    preamble = [m for m in messages if m.get("role") in ("system", "developer")]

    # Everything from the actual user turn onward is the exchange being worked on. A message
    # injected by the gate as an index is not a user turn, so it is identified by content marker
    # rather than by role.
    last_user = -1
    for index, message in enumerate(messages):
        if message.get("role") == "user" and not str(message.get("content") or "").startswith("[ex "):
            last_user = index
    tail = messages[last_user:] if last_user >= 0 else messages[-1:]

    kept_preamble = []
    for message in preamble:
        capped, truncated = cap_tool_result(message, cap, chat_id, data_dir)
        kept_preamble.append(capped)

    carried = []
    for message in messages:
        if message in tail or message in kept_preamble:
            continue
        entry = {
            "id": message.get("id"),
            "role": message.get("role"),
            "tokens": message_tokens(message),
        }
        # A dropped tool turn still has to be visible as a fact. The guard reads the copy on disk and
        # keeps refusing the repeat, but the model can only see what is in the prompt; when the flush
        # removes the call, the model no longer knows it already made it and calls again. Keeping the
        # signature is what makes the guard and the model agree on what has already happened.
        calls = message.get("tool_calls") or []
        if calls:
            entry["signature"] = call_signature(
                calls[0].get("name", ""), calls[0].get("arguments", "")
            )
            entry["tool"] = calls[0].get("name", "")
        report["evicted"].append(entry)

    index_message = None
    if index_block and settings.get("reconstructBlock", True):
        made = [f"- {item.get('tool')} ({item.get('signature')})" for item in report["evicted"] if item.get("signature")]
        body = f"[ex memory, previous turns evicted]\n{index_block}"
        if made:
            body += "\nTool calls already made, results evicted:\n" + "\n".join(made)
        # Injected as system, which is what inject_at_tail does for every context block. As a user
        # message it reads like something the user said, and the model answers it.
        index_message = {"role": "system", "content": body}

    out = kept_preamble + ([index_message] if index_message else []) + list(tail)

    for index, message in enumerate(out):
        if message.get("role") == "tool":
            capped, truncated = cap_tool_result(message, cap, chat_id, data_dir)
            if truncated:
                report["tool_results_truncated"] += 1
            out[index] = capped

    # Prefill goes last. It is part of the template, so it is not history and is not evicted.
    for message in list(prefill or []):
        out.append(message)

    report["pieces"] = [
        "preamble",
        "memory_index" if index_message else "no_index",
        "current_user_turn",
        "current_tool_exchange",
        "prefill",
    ]
    report["kept"] = [
        {
            "id": m.get("id"),
            "role": m.get("role"),
            "tokens": message_tokens(m),
        }
        for m in out
    ]
    report["tokens_before"] = sum(message_tokens(m) for m in messages)
    report["tokens_after"] = sum(message_tokens(m) for m in out)
    report["evicted_tokens"] = sum(item["tokens"] for item in report["evicted"])

    return out, report


def flush_requested(settings: dict, agentic: bool) -> bool:
    """Flush only on agentic work, which is what the setting is for."""
    return bool(settings.get("agenticFlush")) and bool(agentic)
