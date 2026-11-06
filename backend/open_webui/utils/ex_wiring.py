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
import asyncio
import inspect

from open_webui.utils.ex_context import parse_export, previous_context_block, previous_context_message
from open_webui.utils.ex_recovery import decide, retry_permitted
from open_webui.utils.ex_export import verbatim_digest
from open_webui.utils.ex_settings import resolve_ex_settings
from open_webui.utils.ex_stats import estimate_tokens, format_stats_line, generation_stats
from open_webui.utils.ex_steering import load_queue, mark_applied, normalize_chat_id, plan as steering_plan


def read_previous_context(data_dir: Path | str, chat_id: str, settings: dict) -> dict | None:
    """The previous-context message for this request.

    Read order: a pasted block on the request, then the stored file for this chat. The paste is
    the path actually used, because the download button writes a file and the paste carries it
    into a new chat. Returning None when the feature is off keeps the stock request intact, the
    same posture as digest().
    """
    if not settings.get("enabled") or not settings.get("previousContext"):
        return None

    root = Path(data_dir) / "ex_previous_context"
    pasted = ""
    if chat_id:
        stored = root / f"{normalize_chat_id(chat_id)}.txt"
        if stored.exists():
            try:
                pasted = stored.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pasted = ""
    if not pasted:
        return None

    return previous_context_message(pasted, settings)


def drain_steering(messages: list[dict], chat_id: str, settings: dict, data_dir: Path | str, *, turn_open: bool = True) -> dict:
    """Pending steering items for this chat, applied at the iteration boundary.

    Items are applied in descending insert order so an earlier index is not shifted by a later
    insert. An applied item is marked, not deleted, because the record of what was injected has
    to survive for the next context.
    """
    if not settings.get("enabled") or not settings.get("steeringQueue"):
        return {"applied": [], "deferred": [], "stop_turn": False}

    owner = normalize_chat_id(chat_id or "")
    pending = [i for i in load_queue(data_dir) if normalize_chat_id(i.get("chat_id", "")) == owner and not i.get("applied")]
    result = steering_plan(messages, pending, turn_open=turn_open)
    applied = []
    for entry in sorted(result["apply"], key=lambda e: e["insert_at"], reverse=True):
        messages.insert(entry["insert_at"], {"role": "user", "content": entry["note"], "exSteering": True})
        mark_applied(entry["item"], data_dir)
        applied.append({"id": entry["item"].get("id"), "note": entry["note"], "insert_at": entry["insert_at"]})
    return {
        "applied": applied,
        "deferred": result["defer"],
        "stop_turn": result["stop_turn"],
        "refuse_next_call": result["refuse_next_call"],
        "swap_next_tool": result["swap_next_tool"],
    }

log = logging.getLogger(__name__)


async def retry_call(call, settings: dict, *, streamed: bool = False, sleep=asyncio.sleep):
    """Run one request, retrying only what is safe to retry.

    The decision comes from ex_recovery so the policy matches what the settings tab reports. The
    partial output rule is enforced here: a failure before anything streamed can be replayed, a
    failure after items streamed cannot, because a replay would append the same text twice.

    `call` is a zero argument callable. It may be sync or async; the result is awaited only when
    it is awaitable, so a sync caller does not crash. That shape exists so this can be tested with
    a fake function that raises a fixed error, without a live backend.

    `sleep` is injectable so a test does not wait real seconds.
    """
    settings = settings or {}
    attempt = 1
    while True:
        try:
            result = call()
            return await result if inspect.isawaitable(result) else result
        except Exception as error:
            verdict = decide(
                error,
                attempt=attempt,
                max_retries=settings.get("maxRetries", 3),
                shape_retries=settings.get("shapeRetries", 1),
                base_backoff=settings.get("backoffSeconds", 2.0),
            )
            if not settings.get("autoRetry"):
                raise
            if not retry_permitted(verdict, partial_output=streamed)["permitted"]:
                raise
            if attempt > verdict.get("max_retries", 3):
                raise
            await sleep(verdict.get("delay_seconds", 0))
            attempt += 1


def corrective_note(settings: dict, rejected: list[str]) -> str:
    """The note a shape retry needs.

    retry_call honors the shape cap but cannot itself write the corrective note, because a retry
    re-issues the same request. The note has to come from the loop guard's injection point. This
    function exists so the two halves agree on the wording instead of inventing two different ones.
    """
    names = ", ".join(sorted(rejected or []))
    return (
        "[shape retry] the previous attempt wrote a tool call as message text "
        f"({names}). Emit a structured tool call, not markup inside content."
    )


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
        names = sorted({h["name"] for h in markup["markup"]})
        # Single sourced wording, so the loop guard and the shape retry cannot disagree.
        note = corrective_note(settings, names)
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
    "read_previous_context",
    "drain_steering",
    "retry_call",
    "resolve_ex_settings",
    "format_stats_line",
]
