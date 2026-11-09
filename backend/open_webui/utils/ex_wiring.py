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
from typing import Any

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
from open_webui.utils.ex_stats import estimate_tokens, format_stats_line, generation_stats, window_fill
from open_webui.utils.ex_rag import ChatMemory, should_retrieve
from open_webui.utils.ex_engine_log import (
    eta as engine_eta,
    parse_line as engine_parse_line,
    read_status as engine_read_status,
    resolve_log_path as engine_log_path,
)
from open_webui.utils.ex_rollover import (
    branch_order,
    index_block,
    manifest as rollover_manifest,
    mark_carry,
    on_demand,
    record_slide,
    read_chat,
    read_index,
    read_reasoning,
    resolve_source,
    shed_for_prompt,
    store_chat,
    threshold_crossed,
)
from open_webui.utils.ex_context_window import (
    ANSWER_ROOM_MIN,
    DEFAULT_KEEP_TOOL_TURNS,
    DEFAULT_TOOL_SCHEMA_TOKENS,
    TRIM_TARGET_RATIO,
    fit_max_tokens,
    inject_at_tail,
    message_tokens,
    projected_tokens,
    proportional_reserve,
    resolve_window,
    trim_to_budget,
)
from open_webui.utils.ex_steering import load_queue, mark_applied, normalize_chat_id, plan as steering_plan


def read_previous_context(
    data_dir: Path | str,
    chat_id: str,
    settings: dict,
    user_id: str = "",
) -> dict | None:
    """The previous-context message for this request.

    Read order: a pasted block on the request, then the stored file for this chat. The paste is
    the path actually used, because the download button writes a file and the paste carries it
    into a new chat. Returning None when the feature is off keeps the stock request intact, the
    same posture as digest().
    """
    if not settings.get("enabled") or not settings.get("previousContext"):
        return None

    root = Path(data_dir) / "ex_previous_context"
    key = normalize_chat_id(chat_id) if chat_id else ""
    pasted = ""
    if key:
        stored = root / f"{key}.txt"
        if stored.exists():
            try:
                pasted = stored.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pasted = ""

    # The paste is still first because it is what the download button produced by hand. The folder is
    # the written half of the feature, and a new chat reaches it through the carry pointer rather
    # than by being told which chat it came from.
    if not pasted and key:
        source = resolve_source(data_dir, key, "", consume=True)
        pasted = read_chat(data_dir, source)

    if not pasted:
        return None

    return previous_context_message(pasted, settings)


def rollover_check(shape_report: dict, settings: dict) -> bool:
    """Whether this turn crossed the rollover point.

    Reads the count before trimming. A post-trim count would move the rollover point depending on how
    much tool output happened to be stale, which is not a property of the chat.
    """
    try:
        return threshold_crossed(
            int(shape_report.get("tokens_before") or 0),
            int(shape_report.get("budget") or 0),
            float(settings.get("rolloverThreshold") or 0.7),
        )
    except (TypeError, ValueError):
        return False


def mark_rollover(data_dir: Path | str, chat_id: str, user_id: str = "") -> dict:
    """Record that the next chat inherits this one. Consumed once, by the first new chat that reads it."""
    try:
        return mark_carry(data_dir, normalize_chat_id(chat_id), user_id)
    except Exception:
        log.exception("EX rollover carry failed")
        return {}


def rollover_index_block(data_dir: Path | str, chat_id: str, settings: dict, user_id: str = "") -> str:
    """Id index for the chat this one is reading from. Ids and excerpts only; the blocks stay on disk
    until they are cited."""
    try:
        source = resolve_source(data_dir, normalize_chat_id(chat_id), user_id, consume=False)
        return index_block(data_dir, source, settings)
    except Exception:
        log.exception("EX reasoning index failed")
        return ""


def reasoning_on_demand(data_dir: Path | str, chat_id: str, text: str, settings: dict, user_id: str = "") -> list[dict]:
    """Blocks for ids cited in the incoming text. An id is an address, so this is a lookup, not a
    similarity guess, and it costs nothing when no id is cited."""
    try:
        source = resolve_source(data_dir, normalize_chat_id(chat_id), user_id, consume=False)
        blocks = on_demand(data_dir, source, text, settings)
        return [{"role": "system", "content": f"[REASONING {block['section_id']}]\n{block['content']}"} for block in blocks]
    except Exception:
        log.exception("EX reasoning retrieval failed")
        return []


def record_saved_message(
    data_dir: Path | str,
    chat_id: str,
    chat: dict,
    settings: dict,
    user_id: str = "",
) -> dict:
    """Writer call site, reached from the message save path.

    Written from the whole branch rather than from the single message, so an edit rewrites its section
    instead of appending a second copy, and the id mapping stays in step with the text.
    """
    if not settings.get("rollover"):
        return {"written": False, "reason": "rollover is off"}
    try:
        key = normalize_chat_id(chat_id)
        existing = rollover_manifest(data_dir, key)
        return store_chat(
            data_dir,
            key,
            branch_order(chat),
            settings,
            user_id,
            existing.get("source_chat_id") or "",
        )
    except Exception:
        log.exception("EX rolling context write failed")
        return {"written": False, "reason": "error"}


def engine_status(settings: dict, data_dir: Path | str = "") -> dict:
    """Engine status from the log the launcher writes.

    Two labels are kept apart here on purpose. The live row shows a measured arrival rate, which is a
    frontend window over the last ten seconds. The log carries the engine's own speed, which is what the
    ETA is built from. Presenting one as the other would make a tool-call turn look like a decoding turn.
    """
    if not settings.get("engineStatus"):
        return {"source": "engine log", "live": False, "status": None, "eta": None}

    try:
        path = engine_log_path(settings, str(data_dir or ""))
        status = engine_read_status(path, settings.get("engineStatusLines"), settings.get("engineStatusMaxAge"))
        status["eta"] = engine_eta(status.get("status"), settings)
        # The engine's configured window, from the handshake the launcher writes. Kept apart from the
        # generation budget on purpose: a message list can sit inside the budget while the request is
        # over the engine's window, and the engine does not truncate.
        try:
            from open_webui.utils.ex_context_window import detect_ceiling
            value, source = detect_ceiling(data_dir)
            status["ceiling"] = {"value": value, "source": source}
        except Exception:
            status["ceiling"] = None
        # The window fill is reported as its own figure, with the source, so the row cannot show a
        # per-turn count as if it were the window. The ceiling here is the engine's configured window
        # from the handshake; the number on a generation line is not comparable to it.
        status["fill"] = window_fill(
            status.get("status"),
            (status.get("ceiling") or {}).get("value"),
            status.get("last_prefill"),
        )
        return status
    except Exception:
        log.exception("EX engine status read failed")
        return {"source": "engine log", "live": False, "status": None, "eta": None, "error": "read failed"}


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
        # System role. A note inserted as a user turn reads as something the user said, and the model
        # answers the note instead of the request it was given. The guard's note is an observation
        # about the turn, so it belongs on the instruction side of the prompt.
        messages.insert(entry["insert_at"], {"role": "system", "content": entry["note"], "exSteering": True})
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
        names = sorted(set(markup.get("unknown") or []) | set(markup.get("unparsed") or []))
        # Single sourced wording, so the loop guard and the shape retry cannot disagree.
        note = corrective_note(settings, names)
        hit = {**hit, "verdict": "markup", "reason": f"tool markup in message content: {names}", "action": "correct_format"}

    return {
        "hit": hit,
        "note": note,
        "insert_at": pairing_safe_insert_index(messages),
        "markup": markup,
    }


def shape_retry_decision(settings: dict, markup: dict, *, attempt: int = 1, streamed: bool = False) -> dict:
    """Whether prose tool markup should force another request.

    The observed failure is a reply that writes <tool_call> as message text with a name the
    request never declared, or in a form the chat template does not emit. Strata cannot execute it,
    so the block comes back as text, and the turn answers with markup instead of running a tool.
    Detection alone is not the fix: the turn has to be re-run with the corrective note attached.

    Two classes now count. An undeclared name is a capability miss. A declared name written in a
    shape the parser cannot read is a formatting miss, and the earlier rule refused to re-request
    for it, which is what kept producing a text answer here: the name was real, the call was
    written as prose or inside a fence, nothing executed, and the note alone did not change the
    shape that came back. A formatting miss that produces no execution still needs one
    re-request, bounded by the same shapeRetries budget.

    The partial output rule still applies. Text already streamed cannot be replayed without
    appending it twice, so a shape retry after streaming is refused here and reported as such.
    """
    settings = settings or {}
    names = sorted(set(markup.get("unknown") or []) | set(markup.get("unparsed") or []))
    if not names:
        return {"retry": False, "names": [], "note": "", "reason": "no undeclared name to correct"}

    verdict = decide(
        "tool name the request did not declare",
        attempt=attempt,
        max_retries=settings.get("maxRetries", 3),
        shape_retries=settings.get("shapeRetries", 1),
        base_backoff=settings.get("backoffSeconds", 2.0),
    )
    # Partial output blocks a replay because already-streamed text would be sent twice. A reply that
    # was nothing but an unreadable call block delivered nothing the user received, so there is
    # nothing to duplicate and the replay is the correct response. That was the case being refused.
    partial = bool(streamed) and not markup.get("markup_only")
    permitted = retry_permitted(verdict, partial_output=partial)
    return {
        "retry": bool(permitted.get("permitted")) and bool(settings.get("autoRetry", True)),
        "names": names,
        "note": corrective_note(settings, names),
        "reason": permitted.get("reason", ""),
        "class": verdict.get("class"),
    }



def _memory(data_dir) -> ChatMemory:
    """One store handle per call. Cheap to build, and stateless between requests."""
    return ChatMemory(data_dir)


def index_chat_memory(data_dir, chat_id: str, messages: list[dict], settings: dict) -> dict:
    """Index this chat's messages. Never raises into the request path.

    Indexing failure is always survivable: the turn proceeds with whatever context the
    prompt already has. A broken vector store must not become a broken chat.
    """
    if not should_retrieve(settings) or not chat_id:
        return {"indexed": 0, "skipped": 0, "error": None, "disabled": True}
    try:
        return _memory(data_dir).index_messages(chat_id, messages)
    except Exception as exc:
        log.exception("EX memory index failed; continuing without indexing")
        return {"indexed": 0, "skipped": 0, "error": f"{type(exc).__name__}: {exc}"}


def retrieve_chat_memory(
    data_dir,
    chat_id: str | None,
    messages: list[dict],
    settings: dict,
) -> dict:
    """Retrieve a context block for this turn. Never raises into the request path."""
    empty = {"block": None, "hits": [], "query": "", "error": None, "disabled": True}
    if not should_retrieve(settings):
        return empty
    try:
        return _memory(data_dir).retrieve_block(
            messages,
            chat_id=chat_id or None,
            top_k=int(settings.get("ragTopK") or 6),
            max_chars=int(settings.get("ragMaxChars") or 4000),
            min_score=float(settings.get("ragMinScore") or 0.25),
        )
    except Exception as exc:
        log.exception("EX memory retrieve failed; continuing without retrieval")
        return {**empty, "disabled": False, "error": f"{type(exc).__name__}: {exc}"}


def chat_memory_status(data_dir, chat_id: str | None = None) -> dict:
    """Report store health and how many passages this chat has. For the settings tab."""
    try:
        store = _memory(data_dir)
        status = store.status()
        status["chat_count"] = store.count(chat_id or None)
        return status
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}", "chat_count": 0}


def forget_chat_memory(data_dir, chat_id: str) -> dict:
    """Drop one chat's passages. The chat record is untouched; only the index is."""
    try:
        return {"deleted": _memory(data_dir).forget_chat(chat_id)}
    except Exception as exc:
        return {"deleted": 0, "error": f"{type(exc).__name__}: {exc}"}


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
    "index_chat_memory",
    "retrieve_chat_memory",
    "chat_memory_status",
    "forget_chat_memory",
]


def prompt_gate(
    data_dir: Path | str,
    chat_id: str,
    messages: list[dict],
    settings: dict,
    user_id: str = "",
    max_tokens: Any = None,
    tool_schema_tokens: Any = None,
) -> tuple[list[dict], dict]:
    """The measurement point that cannot be fooled.

    Two things were wrong with the old pass.

    1. It measured the message list before the tool bodies were materialized. Tool results live in
       the structured output, and `process_messages_with_output` turns them into tool messages after
       the shaping pass had already decided the list fit. On the failing chat that read as 6076
       estimated tokens against an engine count of 262306.
    2. Crossing the rollover point only wrote a carry pointer for the next chat. It never made the
       current chat smaller, so a chat kept growing until the engine refused it, and the engine does
       not truncate.

    Order here is the fix: measure the materialized list, trim stale tool output first because it is
      the largest and least load bearing part of an agentic transcript, shed the oldest sections only
      when the honest count crosses the rollover point, put the shed index at the tail so the cached
      prefix survives, then fit the output cap to the room left. If nothing can be made to fit, the
      request is refused here with a readable reason rather than as an engine error mid turn.

    Returns the shaped list and a report. The input list is not mutated.
    """
    messages = list(messages or [])
    settings = settings or {}

    # Never plan for more room than the engine has. A configured window larger than the handshake
    # turns a request that should have been shed into a request the engine refuses, which is how a
    # 128k machine gets context death while the settings say 262144. The handshake is the smaller of
    # the two, so it wins.
    configured = settings.get("contextWindow") or None
    detected = resolve_window(None, data_dir)
    ceiling = int(configured) if configured else detected
    report = {}
    report["window_capped_by_engine"] = bool(configured and detected and int(configured) > detected)
    if report["window_capped_by_engine"]:
        ceiling = detected

    reserve = proportional_reserve(
        ceiling,
        preferred=settings.get("replyReserve"),
        max_tokens=max_tokens,
    )
    budget = max(1, ceiling - reserve)
    # The caller can hand over the measured tool block. When it cannot, the settings allowance is a
    # floor rather than a measurement: for a normal chat request the tools are attached after this
    # point, so the real block size is not knowable here. Counting it as a floor is the honest version
    # of that, and a machine with a larger builtin set can raise the floor.
    try:
        measured = int(tool_schema_tokens or 0)
    except (TypeError, ValueError):
        measured = 0
    allowance = measured if measured > 0 else max(
        0, int(settings.get("toolSchemaTokens") or DEFAULT_TOOL_SCHEMA_TOKENS)
    )
    ratio = float(settings.get("rolloverThreshold") or 0.7)
    keep_ratio = float(settings.get("rolloverShedKeepRatio") or 0.7)
    keep_tool_turns = int(settings.get("keepToolTurns") or DEFAULT_KEEP_TOOL_TURNS)

    before = projected_tokens(messages, allowance)
    report["ceiling"] = ceiling
    report["configured"] = int(configured) if configured else None
    report["budget"] = budget
    report["tool_schema_tokens"] = allowance
    report["tool_schema_source"] = "measured" if measured > 0 else "allowance"
    report["measured_before"] = before
    report["trigger"] = threshold_crossed(before, budget, ratio)
    report["trimmed"] = False
    report["shed"] = False
    report["max_tokens_fit"] = 0
    report["refused"] = False

    if not settings.get("enabled"):
        return messages, report

    # 1. Tool output first. Lossless for conversation text, and this is the part that usually
    #    brings a chat back under the window without shedding anything.
    target = int(budget * float(settings.get("trimTargetRatio") or TRIM_TARGET_RATIO))
    shaped = trim_to_budget(messages, target, keep_tool_turns=keep_tool_turns)
    report["trimmed"] = projected_tokens(shaped, allowance) != before
    report["tokens_after_trim"] = projected_tokens(shaped, allowance)

    # 2a. Slide: the policy layer on top of trimming. Past the configured fraction of the real
    # window, the oldest turns leave the prompt and are journalled by entry id, and the handoff
    # block names those ids so the agent can read its own evicted text back. Without this call
    # the slide module never ran in a live request, which is why a growing agentic chat still
    # reached the refusal below. The writer is passed as a callback so the append happens here,
    # in the request path, not inside a module that could be holding the fork lock.
    if settings.get("contextSlide", True):
        from open_webui.utils import ex_slide

        jpath = journal_path(data_dir, chat_id)
        shaped, slide_report = ex_slide.slide(
            shaped,
            data_dir=data_dir,
            window=ceiling,
            entry_writer=lambda entry: append_journal(jpath, [entry], chat_id),
            fraction=float(settings.get("slideFraction") or ex_slide.SLIDE_FRACTION),
            keep_tail=int(settings.get("slideKeepTail") or ex_slide.KEEP_TAIL_TURNS),
            chat_id=chat_id,
        )
        report["slide"] = slide_report
        report["slid"] = bool(slide_report.get("slid"))
        report["tokens_after_slide"] = projected_tokens(shaped, allowance)

        # The stats row needs something it can act on. Counting turns that left the prompt is
        # useless once this runs every turn, so the state stores the journal entries the tool can
        # read back. Written only on a real slide, same as the shed counter was.
        if report["slid"]:
            report["state"] = record_slide(
                str(data_dir),
                chat_id,
                entries=len(slide_report.get("journal_ids") or []),
                evicted_tokens=max(0, report["tokens_after_trim"] - report["tokens_after_slide"]),
                kept_tokens=report["tokens_after_slide"],
            )

    # 2b. The reconstruction block, every turn, whether or not a slide fired. It is the minimum needed
    # to pick the conversation back up in a fresh window: prior context, the last full reasoning loop,
    # the last response, the last user message, and the path of the text file holding all four. System
    # role, and the block says plainly that it is not something the user said.
    if settings.get("reconstructBlock", True) and chat_id:
        from open_webui.utils.ex_reconstruct import reconstruct_block, write_reconstruct

        parts = write_reconstruct(data_dir, chat_id, shaped)["parts"]
        # One copy per turn. A block left by an earlier turn is replaced rather than stacked, which is
        # the same rule inject_at_tail already applies to the RAG block.
        # Checked by content rather than by prefix because the block reaches the prompt through
        # inject_at_tail, which carries the fork's own marker in front of it.
        shaped = [m for m in shaped if "[context slide]" not in str(m.get("content", ""))]
        shaped, inject_report = inject_at_tail(shaped, reconstruct_block(data_dir, chat_id, parts))
        report["reconstruct"] = {
            "parts": list(parts.keys()),
            "injected": bool(inject_report.get("injected")),
            "tokens": inject_report.get("tokens"),
        }
        report["tokens_after_reconstruct"] = projected_tokens(shaped, allowance)

    # 2. Shed only when the honest count crosses the rollover point. The dump is written before
    #    anything leaves the prompt, so the copy on disk exists first.
    shed_target = max(1, int(budget * keep_ratio))
    if settings.get("rollover") and settings.get("rolloverShed") and report["trigger"]:
        kept, shed_report = shed_for_prompt(
            str(data_dir), chat_id, shaped, settings, shed_target, user_id, message_tokens
        )
        report["shed_report"] = shed_report
        report["shed"] = bool(shed_report.get("shed"))
        if report["shed"]:
            shaped = kept
            report["tokens_after_shed"] = projected_tokens(shaped, allowance)

            # Forced eviction only works if the evicted text is still reachable. The dump keeps it
            # verbatim on disk; the vector store is what makes it findable by meaning instead of by
            # id alone. Indexing here is what turns a shed from a deletion into a move.
            if should_retrieve(settings):
                report["memory_indexed"] = index_chat_memory(
                    data_dir, chat_id, shed_report.get("shed_messages") or [], settings
                )

            if shed_report.get("index_block"):
                shaped, injection = inject_at_tail(shaped, shed_report["index_block"])
                report["shed_injection"] = injection
                report["tokens_after_shed"] = projected_tokens(shaped, allowance)

    projected = projected_tokens(shaped, allowance)
    report["measured_after"] = projected

    # 3. Fit the output cap to the room left, which is the engine's own rule. Doing it here means
    #    the request that goes out already satisfies the engine.
    fit = fit_max_tokens(ceiling, projected, max_tokens, answer_room=reserve)
    report["max_tokens_fit"] = fit

    # 4. If the projection still cannot fit, stop before the engine does. An over-window request is
    #    a hard failure, and a local refusal says what happened in terms the user can act on.
    if settings.get("enforceCeiling", True) and projected + max(fit, ANSWER_ROOM_MIN) > ceiling:
        report["refused"] = True
        report["over_by"] = projected + max(fit, ANSWER_ROOM_MIN) - ceiling
        log.warning(
            "EX request refused before send: %d projected tokens against a %d window, %d over. "
            "Trimming and shedding cannot fix a transcript that grew past the window; a shed or a "
            "fresh chat can.",
            projected,
            ceiling,
            report["over_by"],
        )

    # 5. Safety net for the case where nothing can fit. There is no mid-turn interrupt; the only
    #    boundary the fork controls is the iteration boundary, where the steering queue can stop the
    #    next call. When a prompt is over the window the fork therefore builds the smallest prompt that
    #    still contains a real request, from the reconstruction file, and reports it as what would go
    #    out instead of stopping at a refusal. If even the minimum does not fit, the refusal stands.
    if report["refused"] and settings.get("safetyNet", True) and chat_id:
        from open_webui.utils.ex_reconstruct import minimum_prompt

        minimum = minimum_prompt(data_dir, chat_id, shaped)
        minimum_tokens = projected_tokens(minimum, allowance)
        fits = minimum_tokens + max(fit, ANSWER_ROOM_MIN) <= ceiling
        report["safety_net"] = {
            "parts": ["pinned head", "reconstruction block", "current request"],
            "tokens": minimum_tokens,
            "usable": fits,
        }
        if fits:
            report["refused"] = False
            report["safety_net_used"] = True
            shaped = minimum
            report["measured_after"] = minimum_tokens

    return shaped, report
