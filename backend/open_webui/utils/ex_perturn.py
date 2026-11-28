"""Per-turn eviction: the transcript leaves the prompt every turn, as a flat text export.

This is the policy RiRi described, written down as code:

  1. every turn, everything except the pinned head, the live turn, and the live tool exchange
     is removed from the prompt;
  2. what was removed is appended to a plain text transcript on disk, verbatim, chronological,
     role-labelled, in the same shape a chat export would have;
  3. that transcript is pasted back in as a system block, capped, so the model reads it exactly
     as it would read a chat it had been handed as a .txt file;
  4. the last full reasoning turn goes back in verbatim beside it, so the agent keeps hold of
     what it was in the middle of doing;
  5. the real context window is then only a safety net. It is no longer the thing that decides
     when the chat breaks.

Why this shape and not a threshold: a threshold policy keeps the transcript in the prompt right
up until it is nearly too big, so the prompt grows to the window and the window is what ends the
chat. Evicting every turn means the prompt is roughly the size of the export plus the live turn
every turn, which is bounded and small, and a chat can run for as long as the disk allows.

The cost is honest and worth stating: the handoff block changes every turn, so the KV prefix past
the pinned head cannot be reused between turns. That is prefill time paid on every request instead
of cache hits. It is the right trade when context death is the failure being fixed, and it is
switchable off with evictEveryTurn.

Nothing here decides whether a request fits; ex_fit and the gate still do that afterwards. This
module only decides what is in the prompt.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from open_webui.utils.ex_context_window import estimate_tokens, message_tokens

log = logging.getLogger(__name__)

MARKER = "[context transcript]"

# A journal pointer is a reference, not content. The journal writes lines shaped like
#   [journal turn 2 tool_output id=9 1470 chars: ... | retrieve with ex_journal.read(entry=9)]
# and those were being copied verbatim into the durable transcript, so the export handed
# the model something that reads as a tool result it is meant to follow. One bad pointer
# became a rejected tool call, and a rejected call is what ends the turn.
POINTER_PREFIX = "[journal"

# The block must say HOW to get the part that was cut. A pointer with no reader is a dead end:
# read_cache in the web_search tool accepts a local file path explicitly, so that is the reader
# named here. Kept short because it is paid for on every single turn.
READER_HINT = (
    "The full transcript is a plain text file on disk. Read any part of it with the "
    "read_cache tool by passing this path as ref (it accepts a local file), for example "
    "read_cache(ref=\"<path>\", start_line=120, end_line=200)."
)
TRANSCRIPT_NAME = "transcript.txt"
INDEX_NAME = "transcript_index.json"

# One line per message is not a chat export, it is a table of contents. A tool result is kept in
# full up to this many characters because the agent may be mid-task on it; beyond that the head is
# kept and the pointer says where the rest lives.
DEFAULT_MESSAGE_CHARS = 2000
DEFAULT_EXPORT_CHARS = 20000

# Never evicted: this many live messages at the tail in addition to the current user turn.
DEFAULT_KEEP_LIVE = 0


def _safe(chat_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(chat_id or "unknown")).strip("_") or "unknown"


def transcript_dir(data_dir: Any) -> Path:
    root = Path(str(data_dir)) / "ex_journals"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # Surface as OSError, which every caller here already handles. A bad data dir must not
        # become an unexpected exception in a request path.
        raise OSError(f"transcript dir unavailable: {exc}") from exc
    return root


def transcript_path(data_dir: Any, chat_id: str) -> Path:
    return transcript_dir(data_dir) / f"{_safe(chat_id)}.{TRANSCRIPT_NAME}"


def _index_path(data_dir: Any, chat_id: str) -> Path:
    return transcript_dir(data_dir) / f"{_safe(chat_id)}.{INDEX_NAME}"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, value: Any) -> None:
    try:
        path.write_text(json.dumps(value, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("EX transcript index write failed: %s", exc)


def _text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(p for p in parts if p)
    return str(content or "")


def _label(message: dict) -> str:
    role = message.get("role") or "message"
    kind = message.get("type")
    if kind == "reasoning":
        return "assistant reasoning"
    if role == "tool":
        return "tool"
    return role


def _stamp(message: dict) -> str:
    for key in ("timestamp", "created_at", "ts", "time"):
        value = message.get(key)
        if isinstance(value, (int, float)) and value:
            try:
                return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(value)))
            except (OSError, ValueError):
                continue
        if isinstance(value, str) and len(value) >= 10:
            return value[:19]
    return ""


def render_export(messages: list[dict], message_chars: int = DEFAULT_MESSAGE_CHARS) -> str:
    """Verbatim, chronological, role-labelled. The shape of a chat saved as text."""
    lines = []
    for message in messages or []:
        # Scratch reasoning never enters the durable transcript. The design feeds back the LAST
        # turn's reasoning exactly once, from the live message list; writing every earlier block
        # here is the opposite of that and hands the model its own abandoned drafts as the
        # record of what happened.
        if message.get("type") == "reasoning":
            continue
        body = _text(message)
        # A pointer is a reference, not content. Re-publishing it as text is what made the
        # model chase a tool that is not loaded.
        if body.lstrip().startswith(POINTER_PREFIX):
            continue
        calls = message.get("tool_calls") or []
        if calls:
            made = []
            for call in calls:
                if not isinstance(call, dict):
                    continue
                name = call.get("name") or (call.get("function") or {}).get("name") or "tool"
                made.append(str(name))
            if made:
                body = (body + "\n" if body else "") + "[called: " + ", ".join(made) + "]"
        body = body.strip()
        if not body:
            continue
        if len(body) > message_chars:
            body = body[:message_chars] + f"\n...[{len(body) - message_chars} more characters in the transcript file]"
        stamp = _stamp(message)
        head = f"--- {_label(message)}" + (f"  {stamp}" if stamp else "") + " ---"
        lines.append(head)
        lines.append(body)
        lines.append("")
    return "\n".join(lines).strip()


def append_transcript(data_dir: Any, chat_id: str, messages: list[dict],
                      message_chars: int = DEFAULT_MESSAGE_CHARS) -> dict:
    """Append evicted messages to the chat's text transcript, once each.

    Identity is by message id when there is one, and by a content digest when there is not, so a
    second pass over the same list cannot duplicate lines. The transcript is append-only and never
    rewritten, so it is the durable copy of everything that has ever left the prompt.
    """
    path = transcript_path(data_dir, chat_id)
    index = _read_json(_index_path(data_dir, chat_id), {"ids": [], "count": 0})
    seen = set(index.get("ids") or [])
    fresh = []
    added = 0
    for message in messages or []:
        body = _text(message).strip()
        calls = message.get("tool_calls") or []
        if not body and not calls:
            continue
        ident = str(message.get("id") or "")
        if not ident:
            # A stable content digest. Python's hash() is salted per process, so the previous
            # fallback id was a different value after every restart, nothing was recognised as
            # already written, and the entire block was appended again each boot. That is how
            # one turn became nine copies in a single file.
            digest = hashlib.sha1((
                "|".join([str(message.get("role")), body[:200], str(len(body))])
            ).encode("utf-8")).hexdigest()[:24]
            ident = "d" + digest
        if ident in seen:
            continue
        seen.add(ident)
        fresh.append(message)
        added += 1
    if not fresh:
        return {"appended": 0, "path": str(path), "count": index.get("count", 0)}

    block = render_export(fresh, message_chars)
    try:
        with path.open("a", encoding="utf-8") as handle:
            if path.stat().st_size if path.exists() else 0:
                handle.write("\n\n")
            handle.write(block)
    except OSError as exc:
        # A failed write means the copy does not exist, so the eviction must not happen.
        return {"appended": 0, "path": str(path), "error": str(exc)}

    index["ids"] = sorted(seen)
    index["count"] = int(index.get("count") or 0) + added
    index["updated_at"] = int(time.time())
    _write_json(_index_path(data_dir, chat_id), index)
    return {"appended": added, "path": str(path), "count": index["count"]}


def read_transcript(data_dir: Any, chat_id: str, cap_chars: int = DEFAULT_EXPORT_CHARS) -> str:
    """The tail of the transcript, capped, newest end kept.

    When a chat is longer than the cap, the most recent part is what the agent needs to pick up the
    thread. The head is not lost; it is in the file, and the block says so.
    """
    path = transcript_path(data_dir, chat_id)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    text = text.strip()
    if not text or cap_chars <= 0:
        return ""
    if len(text) <= cap_chars:
        return text
    cut = text[-cap_chars:]
    # Start on a message boundary so the block does not open mid sentence.
    boundary = cut.find("\n--- ")
    if boundary > 0:
        cut = cut[boundary + 1:]
    return "[earlier part of the transcript is in the file]\n" + cut


def last_reasoning(messages: list[dict]) -> str:
    for message in reversed(messages or []):
        if message.get("type") == "reasoning":
            body = _text(message).strip()
            if body:
                return body
    return ""


def _is_handoff(message: dict) -> bool:
    return str(message.get("content") or "").lstrip().startswith(MARKER)


def handoff_block(data_dir: Any, chat_id: str, settings: dict, reasoning: str = "",
                  removed: list[dict] | None = None) -> str:
    """The block pasted back in place of the evicted turns.

    Two modes. With a chat id the text comes from the durable transcript on disk, which is what
    makes the copy survive across turns and lets the model read back anything, by id, later.

    Without a chat id (a stateless API call, where the client resends the whole history itself)
    there is no stable identity to append a transcript to, and inventing one would merge unrelated
    conversations into one file. In that case the export is rendered directly from the messages
    being removed this turn. Nothing is persisted, and nothing needs to be: the client is already
    the source of truth, and the prompt still shrinks.
    """
    cap = int(settings.get("evictExportChars") or DEFAULT_EXPORT_CHARS)
    # A caller may hand down a smaller cap for this turn, computed from what is being evicted, so
    # the block that goes back in can never be larger than the text that left the prompt.
    if settings.get("_exportCapChars"):
        cap = min(cap, max(400, int(settings["_exportCapChars"])))
    if removed is not None:
        body = render_export(removed, int(settings.get("evictMessageChars") or DEFAULT_MESSAGE_CHARS))
        if len(body) > cap:
            body = body[-cap:]
            cut = body.find("\n--- ")
            if cut > 0:
                body = body[cut + 1:]
    else:
        body = read_transcript(data_dir, chat_id, cap)
    lines = [
        MARKER,
        "This is the running transcript of this conversation, saved as plain text and pasted back.",
        "Read it the way you would read a chat log: it is what happened, in order. It is not",
        "something the user said just now. Continue the work from where it ends.",
        READER_HINT,
        "",
        body or "(the transcript is empty)",
    ]
    if reasoning:
        cap_r = int(settings.get("shedReasoningChars") or 2000)
        lines += ["", "Your own last reasoning, verbatim:", reasoning[:cap_r]]
    if removed is None:
        try:
            lines += ["", "Transcript file: " + str(transcript_path(data_dir, chat_id))]
        except Exception:
            pass
    return "\n".join(lines)


def per_turn_evict(
    messages: list[dict],
    settings: dict,
    data_dir: Any,
    chat_id: str,
    *,
    keep_live: int = DEFAULT_KEEP_LIVE,
) -> tuple[list[dict], dict]:
    """Remove the transcript from the prompt and paste it back as text. Returns (messages, report).

    The list returned is always safe to send: it always contains the pinned head, the current user
    turn, and the live tool exchange. If anything about the cut would be ambiguous, the messages are
    returned unchanged and the report says why, because a wrong cut is worse than no cut.
    """
    settings = settings or {}
    msgs = list(messages or [])
    report: dict[str, Any] = {
        "per_turn": True,
        "evicted": 0,
        "tokens_before": sum(message_tokens(m) for m in msgs),
    }
    if not msgs:
        report["reason"] = "nothing to evict"
        return msgs, report

    # The handoff from a previous turn is replaced, not stacked.
    stripped = [m for m in msgs if not _is_handoff(m)]

    # The live turn: the last real user message, and everything after it. A gate-injected block is
    # not a user turn, so it is identified by its marker rather than by role alone.
    live_start = -1
    for index, message in enumerate(stripped):
        if message.get("role") != "user":
            continue
        text = str(message.get("content") or "").lstrip()
        if text.startswith("[ex ") or text.startswith(MARKER):
            continue
        live_start = index
    if live_start < 0:
        report["reason"] = "no live user turn to anchor on"
        return msgs, report

    head = [m for m in stripped[:live_start] if m.get("role") in ("system", "developer")]
    between = stripped[:live_start]
    between = [m for m in between if m.get("role") not in ("system", "developer")]

    tail_start = live_start
    if keep_live > 0:
        # Optionally keep a few messages before the live turn, so a short back and forth stays in
        # the prompt verbatim instead of being flattened into the export.
        tail_start = max(0, live_start - int(keep_live))
    tail = stripped[tail_start:]

    # Inside one agentic turn the live tail is not small: a single user message can be followed by
    # dozens of tool calls and results, and every one of them sits after the last user message, so
    # nothing above would ever remove them. That is the same death as a long transcript, just
    # compressed into one turn, so older tool exchanges are moved into the transcript too and only
    # the most recent few are kept verbatim for the model to act on.
    keep_exchanges = int(settings.get("keepToolTurns") or 3)
    exchange_starts = [
        i for i, m in enumerate(tail)
        if m.get("role") == "assistant" and m.get("tool_calls")
    ]
    if len(exchange_starts) > keep_exchanges:
        cut_at = exchange_starts[len(exchange_starts) - keep_exchanges]
        within_turn = [m for m in tail[:cut_at] if m.get("role") not in ("system", "developer")]
        if within_turn:
            between = list(between) + within_turn
            keep_ids = set(id(m) for m in tail[cut_at:])
            tail = [m for m in tail if id(m) in keep_ids or m.get("role") in ("system", "developer")]

    if not between:
        report["reason"] = "nothing outside the pinned head and the live turn"
        report["tokens_after"] = report["tokens_before"]
        return msgs, report

    # STATELESS: with no chat id there is no file to append to, so the export is rendered from the
    # removed messages instead. See handoff_block for why inventing an id here would be wrong.
    stateless = not str(chat_id or "").strip()
    written: dict = {}
    if stateless:
        report["stateless"] = True
    else:
        try:
            written = append_transcript(data_dir, chat_id, between,
                                        int(settings.get("evictMessageChars") or DEFAULT_MESSAGE_CHARS))
        except OSError as exc:
            report["reason"] = "transcript unavailable; nothing evicted"
            report["error"] = str(exc)
            report["tokens_after"] = report["tokens_before"]
            return msgs, report
        if written.get("appended", 0) <= 0 and not written.get("count"):
            report["reason"] = "transcript write failed; nothing evicted"
            report["error"] = written.get("error")
            report["tokens_after"] = report["tokens_before"]
            return msgs, report
    if written.get("appended", 0) <= 0 and not written.get("count"):
        report["reason"] = "transcript write failed; nothing evicted"
        report["error"] = written.get("error")
        return msgs, report

    reasoning = last_reasoning(stripped) if settings.get("shedFeedBackReasoning", True) else ""
    evicted_tokens = sum(message_tokens(m) for m in between)

    # The block that goes back in must be smaller than the text that left, or the eviction is pure
    # loss: same prompt, a disk write, and a prefill to redo. Scale its character cap to what was
    # actually removed. Four characters per token is the usual English ratio, used only as a
    # conservative upper bound here.
    local = dict(settings)
    local["_exportCapChars"] = max(400, int(evicted_tokens * 1.2))
    block = {"role": "system", "content": handoff_block(data_dir, chat_id, local, reasoning,
                                                       removed=between if stateless else None)}
    shaped = head + [block] + tail

    if sum(message_tokens(m) for m in shaped) >= report["tokens_before"]:
        # Nothing was gained, so put the chat back exactly as it was. This happens on a short chat
        # where the export overhead outweighs the turns being exported.
        report["reason"] = "export overhead would exceed what eviction frees; nothing evicted"
        report["tokens_after"] = report["tokens_before"]
        return msgs, report

    report["evicted"] = len(between)
    report["evicted_tokens"] = evicted_tokens
    report["appended"] = written.get("appended")
    report["transcript_count"] = written.get("count")
    report["tokens_after"] = sum(message_tokens(m) for m in shaped)
    report["export_tokens"] = message_tokens(block)
    report["kept_head"] = len(head)
    report["kept_live"] = len(tail)
    report["reason"] = "evicted every turn"
    return shaped, report


__all__ = [
    "MARKER",
    "append_transcript",
    "handoff_block",
    "per_turn_evict",
    "read_transcript",
    "render_export",
    "transcript_path",
]
