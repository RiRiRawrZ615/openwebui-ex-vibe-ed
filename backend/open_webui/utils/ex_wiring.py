"""EX wiring: thin adapters between the pure EX modules and the request path.

Everything here is importable standalone (no open_webui imports) so it can be
tested without a running instance. The call sites in middleware.py resolve the
arguments (DATA_DIR, chat_id, user settings) and pass them in.

Responsibilities:
  1. journal_window()  - apply the reasoning/tool-output window to stored messages
                         and append new entries to the per-chat journal.
  2. loop_check()      - scan a turn's output items, return the steering note.
  3. stats()           - generation stats for the live row and the i icon.

Idempotency rule for the journal: partition runs on every request, so entries
are only appended for message ids not already in the journal file. Without this
the same reasoning would re-append on every turn. The message_id line in each
entry section is what makes this possible.
"""

from __future__ import annotations

import logging
from pathlib import Path

from open_webui.utils.ex_journal import (
    append_journal,
    entry_ids_by_message,
    journal_index,
    journal_path,
    partition,
    read_journal,
)
from open_webui.utils.ex_loop_guard import pairing_safe_insert_index, scan, steering_note, text_tool_markup_issues
from open_webui.utils.ex_export import verbatim_digest
from open_webui.utils.ex_settings import resolve_ex_settings
from open_webui.utils.ex_stats import estimate_tokens, format_stats_line, generation_stats

log = logging.getLogger(__name__)


def journal_window(
    messages: list[dict],
    *,
    data_dir: Path | str,
    chat_id: str,
    settings: dict,
    title: str = "",
) -> tuple[list[dict], int]:
    """Apply the window and append only the new entries. Returns (model_messages, new_count)."""
    path = journal_path(data_dir, chat_id)
    lookup = entry_ids_by_message(path)
    base_entry_id = max((e["id"] for entries in lookup.values() for e in entries), default=0)

    model_messages, entries = partition(
        messages,
        chat_id=chat_id,
        reasoning_window=settings["journalWindow"],
        tool_output_window=settings["toolOutputWindow"],
        tool_output_max_chars=settings["toolOutputMaxChars"],
        base_entry_id=base_entry_id,
        journal_lookup=lookup,
    )
    if entries:
        append_journal(path, entries, chat_id, title)
    return model_messages, len(entries)


def loop_check(output_items: list[dict], messages: list[dict], settings: dict, available_tools: set[str] | None = None) -> dict:
    """Scan the turn. Returns the verdict plus the note and where it can be inserted safely."""
    hit = scan(output_items, repeat_threshold=settings["loopThreshold"])
    note = steering_note(hit)

    # The prose-markup failure is a separate shape: the model wrote a tool call as
    # message text, so no structured call exists and the tool loop never sees it.
    # Check the last assistant message content for that.
    markup = {}
    for message in reversed(messages):
        if message.get("role") == "assistant" and message.get("content"):
            markup = text_tool_markup_issues(str(message.get("content")), available_tools)
            break
    if markup.get("markup") and hit.get("verdict") == "clean":
        names = ", ".join(sorted({h["name"] for h in markup["markup"]}))
        note = (
            f"[loop guard] tool markup written as message text ({names}). Nothing executed. "
            f"Emit structured tool calls, not markup inside content."
        )
        hit = {**hit, "verdict": "markup", "reason": f"tool markup in message content: {names}", "action": "correct_format"}

    return {
        "hit": hit,
        "note": note,
        "insert_at": pairing_safe_insert_index(messages),
        "markup": markup,
    }



def digest(settings: dict, compacted_messages: list[dict]) -> str | None:
    """Verbatim digest, or None when the setting is off.

    Returning None is what keeps the stock path intact: the caller falls through
    to the model paraphrase. Deterministic, no model call, no network.

    The caller stores the result on the checkpoint message. That is not optional.
    The digest has to survive to the next turn or the prompt never shrinks, so a
    per-request-only digest would be a no-op in practice.
    """
    if not settings.get("enabled") or not settings.get("verbatimSummary"):
        return None
    return verbatim_digest(compacted_messages) or None
def stats(usage: dict | None) -> dict:
    """Stats for display at end of message."""
    return generation_stats(usage)


def live_estimate(text: str) -> dict:
    """Client-side estimate during streaming: providers usually send usage only in the final chunk."""
    return {"tokens": estimate_tokens(text), "approximate": True}


__all__ = [
    "journal_window",
    "loop_check",
    "stats",
    "live_estimate",
    "digest",
    "read_journal",
    "resolve_ex_settings",
    "format_stats_line",
]
