"""Context window manager for the EX fork.

The stock behaviour compacts at 80000 tokens against an engine that serves 262144, so a chat
gets summarized, and loses detail, while sitting at 31 percent of what the model can actually
hold. This module replaces that with a policy that spends the real ceiling.

Three separate problems, deliberately kept apart:

1. **Prefix stability.** A model server reuses its KV cache only while the prompt prefix is
   byte identical. Anything injected at the head of the message list, or anything that rewrites
   an earlier system message in place, throws away the cache and forces a full re-prefill of
   the whole conversation. Injection therefore happens at the tail, immediately before the
   newest user turn. This is the single most important property in this file, and
   ``inject_at_tail`` is the only sanctioned way to add context.

2. **Budget.** The window is the engine's real ceiling, not a conservative guess. The budget is
   ceiling minus the room reserved for the reply, so the reply itself never truncates.

3. **Overflow.** At some point an endless chat exceeds any ceiling. When it does, the thing to
   drop is stale tool output, because it is the largest and least load bearing part of an
   agentic transcript. Conversation text is only touched when that is not enough.

Nothing here rewrites history that the user can see. The stored chat is untouched; this only
shapes what goes into one request.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Iterable

from open_webui.utils.ex_handshake import HANDSHAKE_NAME

log = logging.getLogger(__name__)

# The engine ceiling. The strata profile passes --max-context 262144, so this is measured
# rather than aspirational. Kept as a constant because a wrong ceiling here means either a
# wasted window or a hard API error mid generation.
DEFAULT_CONTEXT_WINDOW = 262144

# Room for the reply. A request that fills the window leaves the sampler nowhere to write, so
# the budget is always ceiling minus this.
DEFAULT_REPLY_RESERVE = 16384

# Below this there is no point trimming; a window this small is a misconfiguration, not a chat.
MIN_USEFUL_WINDOW = 4096

# Tool output older than this many trailing messages is a trimming candidate once we are over
# budget. Matches the existing EX toolOutputWindow default so the two do not disagree.
DEFAULT_KEEP_TOOL_TURNS = 3

# What a trimmed tool body is replaced with. Explicit, so the model knows something was removed
# and does not treat the stub as the real result.
TOOL_STUB = "[older tool output trimmed to fit the context window; it is still in the chat]"

# Headroom target after trimming. Aiming for exactly the budget means the next turn trips again
# immediately, and every turn then pays a re-prefill. Landing a little under keeps the prefix
# stable for several turns at a time.
TRIM_TARGET_RATIO = 0.85

CONTEXT_MARKER = "[ex context]"


# --------------------------------------------------------------------------------------
# measurement
# --------------------------------------------------------------------------------------


# Calibration. The divisor was 4 characters per token, which is about 25 percent optimistic on
# this tokenizer: the strata bench results measure the real ratio near 3.0 to 3.13 characters per
# token on mixed prose and code. A chat can report a comfortable budget while the engine counts a
# much larger prompt, and that gap is one of the two ways this module was being fooled. 3.4 keeps a
# small margin rather than trimming earlier than the tokenizer actually would.
CHARS_PER_TOKEN = 3.4

# The tool definitions are part of the prompt and were never counted at all. On this instance the
# builtin schema block is several thousand tokens, so the gate needs an allowance. It is a floor,
# not a claim about a specific request.
DEFAULT_TOOL_SCHEMA_TOKENS = 4096

# Smallest room left for an answer. An engine with no answer room refuses the request outright, so
# a fit that lands on zero is not a fit; it is the same failure one token earlier.
ANSWER_ROOM_MIN = 512


def estimate_tokens(text: Any, chars_per_token: float = CHARS_PER_TOKEN) -> int:
    """Rough token count.

    Deliberately cheap. A tokenizer call per message per turn costs more than the accuracy is
    worth here, and being a little high only means trimming a little earlier. Floored at 1 for any
    non-empty string so a short message never counts as free.
    """
    if not text:
        return 0
    if not isinstance(text, str):
        text = str(text)
    ratio = max(1.0, float(chars_per_token or CHARS_PER_TOKEN))
    return max(1, int(len(text) / ratio) + 1)


def message_tokens(message: dict) -> int:
    """Tokens in one message, including the small per message overhead."""
    if not isinstance(message, dict):
        return 0
    total = 4
    content = message.get("content")
    if isinstance(content, str):
        total += estimate_tokens(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                total += estimate_tokens(part.get("text") or "")
                if part.get("type") == "image_url":
                    # An image is priced by the server, not by its URL length. A flat
                    # allowance keeps the estimate from pretending images are free.
                    total += 850
            elif isinstance(part, str):
                total += estimate_tokens(part)
    for key in ("tool_calls", "tool_call_id", "name"):
        if message.get(key):
            try:
                total += estimate_tokens(json.dumps(message[key]))
            except (TypeError, ValueError):
                total += 8
    total += output_tokens(message)
    return total


def output_tokens(message: dict) -> int:
    """Text that only exists in the structured output, and therefore only enters the prompt when the
    request is assembled.

    This is the half the measurement never saw. Tool results live in `output`; the shaping pass ran on
    the message list before `process_messages_with_output` turned those items into tool messages, so a
    chat measured as a few thousand tokens while the engine counted a prompt of 262306. The failing
    chat is still in the database: 24108 characters of stored content against 2327886 characters of
    stored output.

    Tool bodies are counted always. Reasoning is counted only when there is no content, because when
    both exist the prompt carries the content plus the tool result, not two copies of the same text.
    Same rule ex_rollover uses to decide what a section's text is.
    """
    if not isinstance(message, dict):
        return 0
    output = message.get("output")
    if not isinstance(output, list):
        return 0

    has_content = bool(message.get("content"))
    total = 0
    for item in output:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "tool":
            total += estimate_tokens(str(item.get("content") or ""))
            continue
        if kind == "message":
            # Counted only when the reply exists as structured output and nothing else.
            if has_content:
                continue
            body = item.get("content")
            if isinstance(body, list):
                for part in body:
                    if isinstance(part, dict):
                        total += estimate_tokens(str(part.get("text") or ""))
            else:
                total += estimate_tokens(str(body or ""))
            continue
        if kind == "reasoning" and not has_content:
            parts = item.get("summary") or item.get("content") or []
            if isinstance(parts, str):
                total += estimate_tokens(parts)
                continue
            for part in parts:
                if isinstance(part, dict):
                    total += estimate_tokens(str(part.get("text") or ""))
    return total


def messages_tokens(messages: Iterable[dict]) -> int:
    return sum(message_tokens(message) for message in messages or [])


def projected_tokens(messages: Iterable[dict], tool_schema_tokens: int = 0) -> int:
    """What the engine will actually count for this request.

    The message list plus the tool definitions. The tool block is the second thing the estimate never
    saw, and it is a real part of every request with tools active.
    """
    try:
        allowance = max(0, int(tool_schema_tokens or 0))
    except (TypeError, ValueError):
        allowance = 0
    return messages_tokens(messages) + allowance


def fit_max_tokens(
    window: int,
    prompt_tokens: int,
    requested: Any,
    *,
    answer_room: Any = ANSWER_ROOM_MIN,
    chars_per_token: float = CHARS_PER_TOKEN,
) -> int:
    """The output cap that fits the room the prompt leaves, which is the engine's own rule.

    The engine will not truncate a prompt, so a request asking for more than the window allows is
    refused. Asking for less is always allowed. Doing the arithmetic here means the request that
    goes out already satisfies the engine, instead of arriving as a 400 in the middle of a turn.
    """
    try:
        ceiling = int(window)
        used = int(prompt_tokens)
        want = int(requested) if requested is not None else 0
        room = int(answer_room) if answer_room is not None else ANSWER_ROOM_MIN
    except (TypeError, ValueError):
        return 0
    left = ceiling - used - max(ANSWER_ROOM_MIN, room)
    if left <= 0:
        return 0
    return max(0, min(want, left)) if want > 0 else max(0, left)


def is_tool_message(message: dict) -> bool:
    """A tool result, which is the safe thing to trim."""
    if not isinstance(message, dict):
        return False
    if message.get("role") == "tool":
        return True
    return bool(message.get("tool_call_id"))


def is_tool_call_message(message: dict) -> bool:
    """An assistant turn that called tools. Its body is usually empty, so it is cheap to keep,
    but trimming it would orphan the tool result that follows."""
    return isinstance(message, dict) and bool(message.get("tool_calls"))


# --------------------------------------------------------------------------------------
# budget
# --------------------------------------------------------------------------------------


# Ceiling detection. DEFAULT_CONTEXT_WINDOW is a fallback, not the truth. The measured failure
# was a request the engine refused outright: the estimate said the message list fit the budget
# while the engine had been started with a different window, and the engine does not truncate.
# So the ceiling has to come from what the engine actually started with, which the launcher
# writes into the handshake file at startup.
CEILING_CACHE_SECONDS = 300
_ceiling_cache: dict[str, tuple[int, str, float]] = {}


def detect_ceiling(data_dir: Path | str | None = None) -> tuple[int, str]:
    """The engine's real ceiling, as (ceiling, source).

    Source is 'handshake' when the launcher recorded a value, 'default' when nothing was found.
    Cached per data dir for CEILING_CACHE_SECONDS because this is called on every request, and a
    stale handshake is worse than a stale cache only when the engine was restarted with a new
    window, which rewrites the file.
    """
    key = str(data_dir or "")
    now = time.time()
    hit = _ceiling_cache.get(key)
    if hit and now - hit[3] < CEILING_CACHE_SECONDS:
        # The cache is only valid while the handshake has not changed. A time-only cache would keep
        # reporting the ceiling from before an engine restart, which is exactly the moment the value
        # becomes wrong, so the file's mtime is part of the check.
        try:
            stamp = (Path(data_dir) / HANDSHAKE_NAME).stat().st_mtime if data_dir else None
        except OSError:
            stamp = None
        if stamp == hit[2]:
            return hit[0], hit[1]

    value, source = DEFAULT_CONTEXT_WINDOW, "default"
    stamp = None
    if data_dir:
        try:
            from open_webui.utils.ex_handshake import load_handshake

            record = load_handshake(data_dir)
            params = record.get("params") if isinstance(record, dict) else None
            candidate = params.get("ctx_size") if isinstance(params, dict) else None
            if isinstance(candidate, (int, float)) and int(candidate) >= MIN_USEFUL_WINDOW:
                value, source = int(candidate), "handshake"
            stamp = (Path(data_dir) / HANDSHAKE_NAME).stat().st_mtime
        except Exception:
            # A missing or malformed handshake is normal on a machine that has not run the
            # launcher yet, so this is not worth a traceback.
            pass

    value = max(MIN_USEFUL_WINDOW, value)
    _ceiling_cache[key] = (value, source, stamp, now)
    return value, source


def resolve_window(window: Any = None, data_dir: Path | str | None = None) -> int:
    """The usable ceiling, clamped to something sane.

    With no explicit window, falls back to the detected engine ceiling rather than the constant.
    """
    if window is None:
        return detect_ceiling(data_dir)[0]
    try:
        value = int(window)
    except (TypeError, ValueError):
        value = DEFAULT_CONTEXT_WINDOW
    return max(MIN_USEFUL_WINDOW, value)


# A fixed reserve does not scale. Measured before this was proportional: a 16384 reserve left
# 16384 of a 32768 window, so half the context was unusable, and at 16384 it left 4096, which is
# a broken configuration rather than a small one. The reserve is therefore the larger of a
# proportional share and an absolute floor, and the floor itself is capped so it can never eat
# the window.
RESERVE_MIN_RATIO = 0.04
RESERVE_MAX_RATIO = 0.20
RESERVE_FLOOR_TOKENS = 512


def proportional_reserve(
    window: int,
    *,
    preferred: Any = None,
    max_tokens: Any = None,
) -> int:
    """Reply room for a given window.

    Two constraints pull in opposite directions. The reply must have enough room to finish
    without truncating, and the prompt must not lose a large fraction of a small window. The
    answer is a ratio of the window clamped between a sane floor and a hard ceiling, then
    raised if the caller actually asked for more output than that.

    At 262144 with the default preference this lands on 16384, which matches the old constant, so
    large windows behave exactly as before. At 32768 it lands on 2621 instead of 16384, which is the
    whole point. A requested output cap larger than the ratio ceiling does not raise the reserve past
    the ceiling, because that reserve would be taken from the prompt rather than from the answer.
    """
    try:
        stated = int(preferred) if preferred is not None else 0
    except (TypeError, ValueError):
        stated = 0
    try:
        requested = int(max_tokens) if max_tokens is not None else 0
    except (TypeError, ValueError):
        requested = 0

    # The stated reserve is treated as a preference with a ceiling, not as gospel: a user who
    # typed 16384 while running a 16k window would otherwise make the chat unusable.
    ceiling = int(window * RESERVE_MAX_RATIO)
    floor = min(int(window * RESERVE_MIN_RATIO), ceiling)
    floor = max(RESERVE_FLOOR_TOKENS, floor)

    # A large requested output cap is not a promise the reply will be that long. Reserving the whole
    # request turns a 262144 window into a 131072 prompt budget, which is the opposite of keeping as
    # much context as possible. The reserve follows the preference and the ratio; the cap itself is
    # fitted afterwards by fit_max_tokens, which is the engine's rule, so a request for 131072 on a
    # 262144 window costs 20 percent of the window and not half of it.
    demand = min(requested, ceiling) if requested > 0 else 0
    reserve = stated if stated > 0 else DEFAULT_REPLY_RESERVE
    reserve = min(reserve, ceiling)
    reserve = max(reserve, floor, demand)
    # A caller demanding more output than half the window is asking for something the window cannot
    # give. Clamp it rather than hand back a zero budget.
    return max(0, min(reserve, int(window * 0.5)))


def resolve_budget(
    window: Any = None,
    *,
    reply_reserve: Any = DEFAULT_REPLY_RESERVE,
    max_tokens: Any = None,
) -> int:
    """Tokens available for the prompt, scaled to the window.

    The reserve is proportional with clamps, so the same policy works at 262144 and at 16384.
    See proportional_reserve for why a fixed number was wrong.
    """
    ceiling = resolve_window(window)
    reserve = proportional_reserve(
        ceiling, preferred=reply_reserve, max_tokens=max_tokens
    )
    return max(MIN_USEFUL_WINDOW // 2, ceiling - reserve)


# --------------------------------------------------------------------------------------
# trimming
# --------------------------------------------------------------------------------------


def trim_tool_output(
    messages: list[dict],
    *,
    keep_tool_turns: int = DEFAULT_KEEP_TOOL_TURNS,
) -> list[dict]:
    """Replace the bodies of old tool results with a short stub.

    Walks from the end, keeping the last ``keep_tool_turns`` tool results intact. Everything
    older becomes a stub. The message itself is kept rather than removed, because a dangling
    tool_call with no matching tool result is rejected outright by most chat templates, and
    that failure is far worse than a missing body.

    Never mutates the input. Returns a new list with new dicts for the changed messages.
    """
    try:
        keep = max(0, int(keep_tool_turns))
    except (TypeError, ValueError):
        keep = DEFAULT_KEEP_TOOL_TURNS

    seen = 0
    out: list[dict] = []
    for message in reversed(list(messages or [])):
        if not isinstance(message, dict):
            out.append(message)
            continue
        if is_tool_message(message) and not is_tool_call_message(message):
            seen += 1
            if seen > keep:
                existing = message.get("content")
                if existing != TOOL_STUB:
                    trimmed = dict(message)
                    trimmed["content"] = TOOL_STUB
                    out.append(trimmed)
                    continue
        out.append(message)
    out.reverse()
    return out


def trim_to_budget(
    messages: list[dict],
    budget: int,
    *,
    keep_tool_turns: int = DEFAULT_KEEP_TOOL_TURNS,
    protect_tail: int = 4,
) -> list[dict]:
    """Get under budget by trimming tool output first, oldest first.

    Deliberately does NOT drop messages. Dropping a user or assistant message changes what the
    model was told, which is the data loss this whole module exists to avoid. If trimming tool
    output cannot get under budget, the list is returned as trimmed and the caller decides:
    either accept the prefill cost or hand off to the summarizer.

    ``protect_tail`` conversation messages are never considered for trimming beyond the tool
    keep window, because those are the turns being discussed right now.
    """
    result = trim_tool_output(messages, keep_tool_turns=keep_tool_turns)
    if messages_tokens(result) <= budget:
        return result

    # Still over. Trim in passes, each pass dropping the keep window by one, so the most recent
    # tool results are the last to go.
    keep = keep_tool_turns
    while keep > 0:
        keep -= 1
        result = trim_tool_output(messages, keep_tool_turns=keep)
        if messages_tokens(result) <= budget:
            return result

    return result


def apply_overflow_policy(
    messages: list[dict],
    budget: int,
    *,
    keep_tool_turns: int = DEFAULT_KEEP_TOOL_TURNS,
    target_ratio: float = TRIM_TARGET_RATIO,
) -> tuple[list[dict], dict]:
    """Trim toward a headroom target rather than exactly to the budget.

    Returns the shaped messages and a report describing what happened, so the UI can show it
    and a test can assert on it. When stubbing tool output is not enough, the oldest turns are
    evicted per turn rather than the request being refused for size; on dev the size gate does
    not refuse, it evicts.
    """
    incoming = messages_tokens(messages)
    report: dict[str, Any] = {
        "tokens_before": incoming,
        "budget": budget,
        "trimmed": False,
        "tokens_after": incoming,
        "over_budget": incoming > budget,
    }
    if budget <= 0 or incoming <= budget:
        return list(messages or []), report

    target = max(MIN_USEFUL_WINDOW, int(budget * max(0.1, min(1.0, float(target_ratio)))))
    shaped = trim_to_budget(
        messages, target, keep_tool_turns=keep_tool_turns
    )
    after = messages_tokens(shaped)
    report["trimmed"] = after != incoming
    report["tokens_after"] = after
    report["still_over"] = after > budget
    if report["still_over"]:
        # Stubs are not enough, so whole oldest turns go. This is the step the engine used to
        # refuse on instead: a request larger than the window is not served, and the correct
        # response is to make it fit, not to hand back an error. Import is local because
        # ex_evict measures through this module.
        from open_webui.utils.ex_evict import evict_to_fit

        shaped, evict_report = evict_to_fit(shaped, budget, keep_tool_turns=keep_tool_turns)
        report["evict"] = evict_report
        report["evicted_turns"] = evict_report["evicted_turns"]
        report["tokens_after"] = messages_tokens(shaped)
        report["still_over"] = report["tokens_after"] > budget
        if report["still_over"]:
            report["blocked_reason"] = evict_report.get("blocked_reason", "nothing load bearing left to drop")
    return shaped, report


# --------------------------------------------------------------------------------------
# prefix safe injection
# --------------------------------------------------------------------------------------


def tail_insert_index(messages: list[dict]) -> int:
    """Where a context block belongs: immediately before the newest user turn.

    Positioning here is the whole trick. The engine has already cached every token before this
    point, so inserting here invalidates only the newest user turn and the block itself. Inserting
    at index 0 would invalidate the entire transcript and force a full re-prefill on every turn.
    """
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, dict) and message.get("role") == "user":
            return index
    return len(messages)


def inject_at_tail(
    messages: list[dict],
    block: str | None,
    *,
    role: str = "system",
    marker: str = CONTEXT_MARKER,
) -> tuple[list[dict], dict]:
    """Insert one context block before the newest user turn.

    Returns the new list and a report. The input list is not mutated.

    The block carries a marker so a later pass can find and replace it instead of stacking a
    second copy, which would otherwise grow without bound across a long chat.
    """
    messages = list(messages or [])
    report = {"injected": False, "index": None, "tokens": 0}
    if not block or not block.strip():
        return messages, report

    body = block if block.lstrip().startswith(marker) else f"{marker}\n{block.rstrip()}"
    report["tokens"] = estimate_tokens(body)

    # A previous turn's block sits at the tail. Remove it rather than leaving both, so the
    # insertion point stays shallow.
    existing = [
        index
        for index, message in enumerate(messages)
        if isinstance(message, dict)
        and message.get("role") == role
        and str(message.get("content", "")).lstrip().startswith(marker)
    ]
    for index in reversed(existing):
        messages.pop(index)

    index = tail_insert_index(messages)
    messages.insert(index, {"role": role, "content": body})
    report["injected"] = True
    report["index"] = index
    return messages, report


def strip_context_blocks(
    messages: list[dict], *, marker: str = CONTEXT_MARKER
) -> list[dict]:
    """Remove injected blocks, for a caller that wants the bare transcript."""
    return [
        message
        for message in messages or []
        if not (
            isinstance(message, dict)
            and str(message.get("content", "")).lstrip().startswith(marker)
        )
    ]


# --------------------------------------------------------------------------------------
# soft compaction: lossless, the preferred fallback
# --------------------------------------------------------------------------------------


def compaction_fingerprint(messages: list[dict], up_to: int) -> str:
    """A stable digest of the span that would be summarized.

    Two purposes. It lets the caller skip re-summarizing a span that has not changed, and it
    lets a stored summary be validated: if the span it described has changed, the summary is
    stale and must not be trusted. Nothing in the stock implementation does this check, which
    is why a stale summary can quietly contradict the live transcript.
    """
    import hashlib

    digest = hashlib.sha1()
    for message in messages[:up_to]:
        if not isinstance(message, dict):
            continue
        digest.update(str(message.get("role", "")).encode("utf-8", "ignore"))
        content = message.get("content")
        if not isinstance(content, str):
            try:
                content = json.dumps(content, sort_keys=True, default=str)
            except (TypeError, ValueError):
                content = str(content)
        digest.update(b"\x00")
        digest.update(content.encode("utf-8", "ignore"))
        digest.update(b"\x01")
    return digest.hexdigest()


def boundary_for_ratio(messages: list[dict], keep_ratio: float = 0.4) -> int:
    """Index that keeps roughly ``keep_ratio`` of the transcript, on a message boundary.

    The stock implementation keeps a percentage of messages, which is wrong when one tool result
    is 40000 tokens and a hundred chat messages are 2000. This walks by tokens and then snaps to
    a boundary that does not split a tool exchange.
    """
    messages = list(messages or [])
    if not messages:
        return 0
    total = messages_tokens(messages)
    if total <= 0:
        return 0
    keep_ratio = max(0.0, min(1.0, float(keep_ratio)))
    target = total * keep_ratio

    running = 0
    boundary = len(messages)
    for index in range(len(messages) - 1, -1, -1):
        running += message_tokens(messages[index])
        if running >= target:
            boundary = index
            break
    else:
        boundary = 0

    # Never cut between a tool call and its result: that produces an invalid message list.
    while boundary > 0 and boundary < len(messages):
        previous = messages[boundary - 1]
        current = messages[boundary]
        if isinstance(previous, dict) and previous.get("tool_calls"):
            boundary -= 1
            continue
        if isinstance(current, dict) and current.get("tool_call_id"):
            boundary -= 1
            continue
        break
    return max(0, min(boundary, len(messages)))


# --------------------------------------------------------------------------------------
# the whole policy
# --------------------------------------------------------------------------------------


def shape_request(
    messages: list[dict],
    *,
    window: Any = None,
    reply_reserve: Any = DEFAULT_REPLY_RESERVE,
    max_tokens: Any = None,
    keep_tool_turns: int = DEFAULT_KEEP_TOOL_TURNS,
    context_block: str | None = None,
    data_dir: Path | str | None = None,
    tool_schema_tokens: int = DEFAULT_TOOL_SCHEMA_TOKENS,
    chars_per_token: float = CHARS_PER_TOKEN,
    settings: dict | None = None,
) -> tuple[list[dict], dict]:
    """The one function the middleware calls.

    Order matters:

    1. Measure against the real ceiling.
    2. If over budget, trim tool output toward the headroom target. Lossless for conversation
       text.
    3. Inject the context block at the tail, after trimming, so the block is not itself
       counted as something to trim.
    4. Report what happened, including whether the request is still over budget, in which case
       the caller falls back to summarization.

    ``context_block`` is what used to be prepended at the head. Passing it here is how RAG and
    previous-context injection become cache friendly.
    """
    budget = resolve_budget(window, reply_reserve=reply_reserve, max_tokens=max_tokens)
    shaped, report = apply_overflow_policy(
        messages, budget, keep_tool_turns=keep_tool_turns
    )
    if context_block:
        shaped, injection = inject_at_tail(shaped, context_block)
        report["injection"] = injection
        report["tokens_after"] = messages_tokens(shaped)
    report["budget"] = budget
    detected, source = detect_ceiling(data_dir)
    # An explicit window still wins for the budget, but the detected ceiling is reported next to
    # it. When the two disagree the caller is being handed a window the engine cannot honour, and
    # that is the case worth seeing rather than smoothing over.
    report["window"] = resolve_window(window, data_dir)
    report["ceiling"] = detected
    report["ceiling_source"] = source
    report["headroom"] = max(0, budget - report["tokens_after"])
    report["percent"] = (
        round((report["tokens_after"] / budget) * 100) if budget > 0 else 0
    )

    # The engine refuses an over-window request outright and never truncates, so a request that is
    # over the ceiling is a hard failure, not a soft one. Trimming cannot fix it: tool output is
    # the only thing trimmed, and a transcript that grew past the window is not tool output.
    # Reporting it here is what turns an engine 400 into something visible before the request goes
    # out, so the caller can summarize or tell the user instead of eating an error mid turn.
    over = report["tokens_after"] - detected
    if over > 0:
        report["over_ceiling"] = over
        log.warning(
            "EX request is over the engine ceiling: %d tokens against a %d window (source %s), "
            "%d over. Trimming cannot fix this; summarization or a fresh chat can.",
            report["tokens_after"],
            detected,
            source,
            over,
        )
    else:
        report["over_ceiling"] = 0

    # Two figures that the old report did not have. The tool definitions are part of the prompt, so
    # the projection is the message list plus the schema block; and the engine will not truncate, so
    # the only request that survives is one whose output cap fits the room the prompt leaves.
    settings = settings or {}
    allowance = max(0, int(tool_schema_tokens or 0))
    report["tool_schema_tokens"] = allowance
    report["projected"] = report["tokens_after"] + allowance
    # The reply reserve is the answer room. There is no separate answerRoom setting because a setting
    # the code does not read is worse than no setting at all.
    report["max_tokens_fit"] = fit_max_tokens(
        detected,
        report["projected"],
        max_tokens,
        answer_room=reply_reserve,
    )
    report["engine_fits"] = report["projected"] + report["max_tokens_fit"] <= detected
    if not report["engine_fits"]:
        report["would_be_refused"] = report["projected"] + report["max_tokens_fit"] - detected

    return shaped, report