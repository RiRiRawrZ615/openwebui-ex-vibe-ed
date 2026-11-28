"""Compaction: replace the transcript with an index of steps the model can pull by id.

Why the previous design could not work, stated as a mechanism rather than a mystery.

The old rollover fed back a verbatim transcript with per-message truncation. That fails for four
reasons that compound:

1. TRUNCATED TEXT IS INDISTINGUISHABLE FROM COMPLETE TEXT. A message cut at 2000 chars still
   reads like a finished message. The model cannot tell "this tool returned 2000 chars" from
   "this tool returned everything and it was 2000 chars". So it reasons from a body whose tail
   is missing, and nothing in the block warns it.

2. THE BLOCK REORDERS AUTHORITY. A flattened transcript puts the agent's own abandoned drafts
   and the user's actual instructions in the same stream of text. The model then treats its own
   speculation as established context. That is how the overnight run re-derived the same plan
   for 493k tokens: the record kept telling it the plan was still open.

3. VERBATIM TEXT IS PAID FOR TWICE. To stay inside the window the block has to be capped, and
   the cap is always smaller than the history. So the older the chat, the more the block lies by
   omission, while still costing thousands of tokens every turn.

4. IT IS UNBOUNDED WORK PER TURN. Every turn re-reads and re-prefills the whole flattened
   history. The prompt never shrinks past the block size, and prefill cost scales with the chat.

What replaces it: a compact index. One line per step, each with a real id. The model reads the
index, and when it needs a body it pulls exactly that id. The prompt then costs the index, which
is bounded, and the full text stays on disk untouched.

The ids are real journal entries, so this reuses the storage that already exists rather than
inventing a second one.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

BRIEF_MARKER = "[context brief]"
SUMMARY_NAME = "summary.json"

# How much raw text one summariser call may see. Small on purpose: this runs on the same model
# the chat uses, and a huge chunk is exactly the prefill cost being avoided.
CHUNK_CHARS = 5000

# The index is capped in lines, not characters, because one line is the unit the model acts on.
DEFAULT_MAX_LINES = 120
# Per-line ceiling. A summary line is an index entry, not a paragraph.
DEFAULT_LINE_CHARS = 220

SUMMARISER_SYSTEM = (
    "You compress a work log into index lines. Output only lines, one per step, no preamble, "
    "no numbering, no markdown. Each line: WHO did WHAT and what it produced or decided. "
    "Keep file paths, command names, ids, error strings and numbers exactly as written, because "
    "they are the things that must be findable later. Drop reasoning, restatement and anything "
    "repeated from an earlier line. If a step failed or was abandoned, say so in one clause. "
    "Never invent a detail. If the log does not say, do not guess."
)


def _safe(chat_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(chat_id or "unknown")).strip("_") or "unknown"


def summary_path(data_dir: Any, chat_id: str) -> Path:
    root = Path(str(data_dir)) / "ex_journals"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(f"summary dir unavailable: {exc}") from exc
    return root / f"{_safe(chat_id)}.{SUMMARY_NAME}"


def load_state(data_dir: Any, chat_id: str) -> dict:
    """The durable compaction state: the index lines and how far the transcript was consumed."""
    try:
        return json.loads(summary_path(data_dir, chat_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"lines": [], "consumed": 0, "updated_at": 0}


def save_state(data_dir: Any, chat_id: str, state: dict) -> bool:
    path = summary_path(data_dir, chat_id)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError as exc:
        log.warning("EX compaction state write failed: %s", exc)
        return False


def render_brief(state: dict, settings: dict | None = None) -> str:
    """The block the model actually sees. Index lines plus the rule for using them."""
    settings = settings or {}
    lines = list(state.get("lines") or [])
    if not lines:
        return ""
    try:
        max_lines = int(settings.get("compactMaxLines") or DEFAULT_MAX_LINES)
    except (TypeError, ValueError):
        max_lines = DEFAULT_MAX_LINES
    # Oldest lines are dropped first, and the drop is declared rather than silent. An index that
    # quietly lost its beginning would read as "this chat started here", which is a lie.
    dropped = 0
    if max_lines > 0 and len(lines) > max_lines:
        dropped = len(lines) - max_lines
        lines = lines[-max_lines:]

    head = [
        BRIEF_MARKER,
        "This chat is longer than the context window, so everything before the current work is",
        "kept as an index of steps, oldest first. One line per step:",
        "  <id> turn <n> <who>: <what happened>",
        "",
        "THE IDS ARE REAL. To read a step in full, call the journal reader with that id:",
        '  ex_journal__read(entry="<id>")',
        "Write that call exactly as shown. Do not invent a different tool name for it, and do not",
        "reference an id you cannot see in this index. Do not guess at what a step contained: if",
        "the detail matters to the work, read it, and if it does not, do not.",
    ]
    if dropped:
        head.append(
            "The first %d step(s) are no longer indexed. Their full text is still on disk in the "
            "journal and can be searched by keyword with ex_journal__read(needle=\"...\")." % dropped
        )
    head.append("")
    head.append("Index:")
    return "\n".join(head + lines)


def chunk_for_summary(messages: list, chunk_chars: int = CHUNK_CHARS) -> list[str]:
    """Split removed messages into summariser-sized chunks, never splitting a message itself."""
    chunks, current, size = [], [], 0
    for message in messages or []:
        body = _body(message)
        if not body:
            continue
        piece = _one_line_fact(message, body)
        if size + len(piece) > chunk_chars and current:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(piece)
        size += len(piece) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def _body(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
        return "\n".join(p for p in parts if p)
    return str(content or "")


def _one_line_fact(message: dict, body: str) -> str:
    """A labelled, clipped rendering of one message for the summariser. Not for the model."""
    role = message.get("role") or "message"
    if message.get("type") == "reasoning":
        role = "assistant-thinking"
    calls = message.get("tool_calls") or []
    made = []
    for call in calls:
        if isinstance(call, dict):
            name = call.get("name") or (call.get("function") or {}).get("name")
            if name:
                made.append(str(name))
    body = " ".join(body.split())
    if len(body) > 1200:
        body = body[:1200] + "..."
    extra = (" [called: " + ", ".join(made) + "]") if made else ""
    return f"{role}: {body}{extra}"


def parse_lines(text: str, limit: int = DEFAULT_LINE_CHARS) -> list[str]:
    """Turn a summariser reply into clean index entries.

    Fails soft: anything unusable is dropped rather than stored as a bad line the model will then
    treat as fact.
    """
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-*0123456789. ").strip()
        if not line:
            continue
        if line.lower().startswith(("here are", "index:", "summary", "sure", "okay")):
            continue
        if len(line) > limit:
            line = line[:limit].rstrip() + "..."
        out.append(line)
    return out


def fold(state: dict, new_lines: list[str], turn: int, consumed_text: str = "") -> dict:
    """Merge summarised lines into the rolling index and record how far we consumed."""
    lines = list(state.get("lines") or [])
    start = int(state.get("next_step") or len(lines) + 1)
    for offset, text in enumerate(new_lines):
        step = start + offset
        lines.append(f"[s{step}] turn {turn} {text}")
    return {
        "lines": lines,
        "next_step": start + len(new_lines),
        "consumed": int(state.get("consumed") or 0) + len(consumed_text or ""),
        "updated_at": int(time.time()),
        "last_turn": int(turn or 0),
    }


def should_compact(state: dict, pending_chars: int, settings: dict | None = None) -> dict:
    """Whether it is worth spending a summariser call this turn.

    Compaction costs a model call, so it must not fire on every turn. The trigger is a real
    amount of un-indexed text, and the answer says why either way so a silent no-op is visible.
    """
    settings = settings or {}
    try:
        floor = int(settings.get("compactMinChars") or CHUNK_CHARS)
    except (TypeError, ValueError):
        floor = CHUNK_CHARS
    if not (state.get("lines") or []):
        return {"compact": True, "reason": "no index yet"}
    if pending_chars <= 0:
        return {"compact": False, "reason": "nothing new to index"}
    if pending_chars < floor:
        return {"compact": False, "reason": f"{pending_chars} chars pending, under {floor}"}
    return {"compact": True, "reason": f"{pending_chars} chars pending"}


def summarise_prompt(chunk: str) -> list[dict]:
    """The messages for one summariser call. Tool-free, so it cannot make a rejected call."""
    return [
        {"role": "system", "content": SUMMARISER_SYSTEM},
        {"role": "user", "content": "Compress this log. One line per step, nothing else.\n\n" + chunk},
    ]


__all__ = [
    "BRIEF_MARKER",
    "CHUNK_CHARS",
    "SUMMARISER_SYSTEM",
    "chunk_for_summary",
    "fold",
    "load_state",
    "parse_lines",
    "render_brief",
    "save_state",
    "should_compact",
    "summarise_prompt",
    "summary_path",
]
