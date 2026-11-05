"""Per-chat reasoning journal.

Two things this solves, in order of importance:

1. Reasoning is replayed into the model-facing history on every turn. In the
   stock code that is controlled by ``get_reasoning_format()``, which returns
   ``'reasoning_content'`` for llama.cpp models, so ``convert_output_to_messages()``
   re-feeds prior thinking. On a small quant with a multi-second prefill, that is
   pure cost, and it is also the mechanism that lets the model treat its own scratch
   text as something a user said. The fix is a window: keep reasoning for the current
   turn, move everything older out of the prompt head.

2. Once reasoning leaves the window, the stock code drops it permanently. It stays on
   the message for rendering, but the model cannot get it back. This module keeps it as
   data instead: an append-per-chat markdown journal that is greppable, exportable,
   user-readable, and reachable by a tool on demand. Nothing enters the prompt unless a
   lookup actually fires, so the stable prompt head stays cacheable.

The same mechanism is applied to long tool outputs, because ``truncate_content()`` is
lossy in the model-facing copy. The raw item survives on the message, but the model only
ever sees the truncated version. Here the full text goes to the journal and the prompt
carries a pointer.

Tool-call pairing is not disturbed: ``function_call`` items and their
``function_call_output`` results are left exactly as they are. Only reasoning text and
oversized tool output bodies are swapped for pointers, and pairing survives that.

The pure functions here do no IO and import nothing from open_webui, so they can be
tested without touching a running instance.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

POINTER_PREFIX = "[journal"

# Reasoning items and tool outputs the model is allowed to still see.
DEFAULT_REASONING_WINDOW = 1
DEFAULT_TOOL_OUTPUT_WINDOW = 3
DEFAULT_TOOL_OUTPUT_MAX_CHARS = 1500

SUMMARY_CHARS = 120


def summarise(text: str, limit: int = SUMMARY_CHARS) -> str:
    """Cheap deterministic summary: first non-empty line, clipped. No model call."""
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= limit else line[:limit] + "..."
    return (text or "").strip()[:limit]


def extract_reasoning_text(item: dict) -> str:
    """Pull text out of a reasoning output item, same fields the UI uses."""
    parts = item.get("summary") or item.get("content") or []
    if isinstance(parts, str):
        return parts
    text = ""
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "output_text" or "text" in part:
            text += str(part.get("text", ""))
    return text


def extract_tool_output_text(item: dict) -> str:
    """Pull text out of a function_call_output item."""
    parts = item.get("output") or []
    if isinstance(parts, str):
        return parts
    text = ""
    for part in parts:
        if isinstance(part, dict) and part.get("type") == "input_text":
            text += str(part.get("text", ""))
    return text


def journal_path(data_dir: Path | str, chat_id: str) -> Path:
    """Per-chat journal file. chat_id is sanitised so it cannot escape the directory."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(chat_id or "unknown")).strip("_") or "unknown"
    root = Path(data_dir) / "ex_journals"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{safe}.md"


def _pointer(entry_id: int, kind: str, turn: int, summary: str, chars: int) -> str:
    return (
        f"{POINTER_PREFIX} turn {turn} {kind} id={entry_id} {chars} chars: {summary} | "
        f"full text is stored, retrieve with ex_journal.read(entry={entry_id})"
    )


def partition(
    messages: list[dict],
    *,
    chat_id: str,
    reasoning_window: int = DEFAULT_REASONING_WINDOW,
    tool_output_window: int = DEFAULT_TOOL_OUTPUT_WINDOW,
    tool_output_max_chars: int = DEFAULT_TOOL_OUTPUT_MAX_CHARS,
    base_entry_id: int = 0,
    journal_lookup: dict[str, list[dict]] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Split messages into what the model should see and what belongs in the journal.

    Returns (model_messages, new_entries). Input messages are not mutated: a shallow
    copy is made for the model-facing path so the stored message keeps everything the
    UI needs to render the reasoning block.

    ``reasoning_window`` counts assistant messages from the end. Only the last N keep
    their reasoning in the prompt head. Everything older becomes a journal entry and the
    model-facing copy carries a pointer instead.
    """
    model_messages: list[dict] = []
    entries: list[dict] = []
    next_entry_id = base_entry_id

    assistant_positions = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    if assistant_positions:
        # Positions inside the window: the last N assistant messages.
        first_kept = assistant_positions[max(0, len(assistant_positions) - max(1, int(reasoning_window)))]
    else:
        first_kept = -1

    # Tool outputs are windowed the same way: the last N of them stay in full, counted
    # from the end, not from the start. Counting from the start would journal the oldest
    # turn forever while the newest one always stayed, which is backwards.
    tool_output_slots = []
    for i, m in enumerate(messages):
        if not isinstance(m.get("output"), list):
            continue
        for j, item in enumerate(m["output"]):
            if isinstance(item, dict) and item.get("type") == "function_call_output":
                tool_output_slots.append((i, j))
    first_kept_tool = tool_output_slots[max(0, len(tool_output_slots) - max(1, int(tool_output_window)))] if tool_output_slots else None

    for i, message in enumerate(messages):
        role = message.get("role")
        output = message.get("output")

        if not isinstance(output, list) or role != "assistant":
            model_messages.append(message)
            continue

        copy = dict(message)
        new_output = []
        reasoning_in_window = i >= first_kept
        turn_number = assistant_positions.index(i) + 1 if i in assistant_positions else len(assistant_positions)

        for pos, item in enumerate(output):
            if not isinstance(item, dict):
                new_output.append(item)
                continue

            item_type = item.get("type")

            if item_type == "reasoning":
                text = extract_reasoning_text(item)
                if text:
                    if reasoning_in_window:
                        new_output.append(item)
                        continue
                    if journal_lookup:
                        already = [e for e in journal_lookup.get(message.get("id"), []) if e["kind"] == "reasoning"]
                        if already:
                            # Idempotency. partition runs every turn, so a turn already in the
                            # journal must not append again. The model-facing copy still gets
                            # the pointer, not the full text, otherwise the window silently
                            # stops applying to older turns.
                            new_output.append(
                                {
                                    "type": "message",
                                    "content": [{"type": "output_text", "text": _pointer(already[0]["id"], "reasoning", turn_number, summarise(text), len(text))}],
                                }
                            )
                        else:
                            next_entry_id += 1
                            entries.append(
                                {
                                    "id": next_entry_id,
                                    "chat_id": chat_id,
                                    "turn": turn_number,
                                    "message_id": message.get("id"),
                                    "kind": "reasoning",
                                    "ts": datetime.now(timezone.utc).isoformat(),
                                    "summary": summarise(text),
                                    "text": text,
                                }
                            )
                            new_output.append(
                                {
                                    "type": "message",
                                    "content": [{"type": "output_text", "text": _pointer(next_entry_id, "reasoning", turn_number, summarise(text), len(text))}],
                                }
                            )
                        continue
                    next_entry_id += 1
                    entries.append(
                        {
                            "id": next_entry_id,
                            "chat_id": chat_id,
                            "turn": turn_number,
                            "message_id": message.get("id"),
                            "kind": "reasoning",
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "summary": summarise(text),
                            "text": text,
                        }
                    )
                    new_output.append(
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": _pointer(next_entry_id, "reasoning", turn_number, summarise(text), len(text))}],
                        }
                    )
                else:
                    new_output.append(item)
                continue

            if item_type == "function_call_output":
                text = extract_tool_output_text(item)
                # Only swap oversized bodies, and only outside the tool window. Pairing is
                # preserved because call_id is untouched.
                inside_tool_window = first_kept_tool is not None and (i, pos) >= first_kept_tool
                if len(text) > tool_output_max_chars and not inside_tool_window:
                    if journal_lookup:
                        already = [
                            e
                            for e in journal_lookup.get(message.get("id"), [])
                            if e["kind"] == "tool_output" and e.get("call_id") == item.get("call_id")
                        ]
                        if already:
                            new_output.append(
                                {
                                    "type": "message",
                                    "content": [{"type": "output_text", "text": _pointer(already[0]["id"], "tool_output", turn_number, summarise(text), len(text))}],
                                }
                            )
                            continue
                    next_entry_id += 1
                    entries.append(
                        {
                            "id": next_entry_id,
                            "chat_id": chat_id,
                            "turn": turn_number,
                            "message_id": message.get("id"),
                            "kind": "tool_output",
                            "call_id": item.get("call_id"),
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "summary": summarise(text),
                            "text": text,
                        }
                    )
                    kept = dict(item)
                    kept["output"] = [{"type": "input_text", "text": _pointer(next_entry_id, "tool_output", turn_number, summarise(text), len(text))}]
                    new_output.append(kept)
                    continue
                new_output.append(item)
                continue

            new_output.append(item)

        copy["output"] = new_output
        model_messages.append(copy)

    return model_messages, entries


def render_markdown(entries: list[dict], chat_id: str, title: str = "") -> str:
    """Human-readable mirror of the journal. One entry per section, grep-friendly."""
    lines = [f"# Reasoning journal{f' - {title}' if title else ''}", "", f"chat: {chat_id}", ""]
    for e in entries:
        lines.append(f"## entry {e['id']} | turn {e['turn']} | {e['kind']}")
        lines.append(f"ts: {e['ts']}")
        if e.get("message_id"):
            lines.append(f"message_id: {e['message_id']}")
        if e.get("call_id"):
            lines.append(f"call_id: {e['call_id']}")
        lines.append(f"summary: {e['summary']}")
        lines.append("")
        lines.append(e["text"].strip())
        lines.append("")
    return "\n".join(lines)


def append_journal(path: Path | str, entries: list[dict], chat_id: str, title: str = "") -> Path:
    """Append new entries to the per-chat markdown journal. Creates it on first write.

    Only the first write emits the header. Appends write entry sections only, so the file
    stays one clean document instead of repeating a title block on every turn.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(render_markdown([], chat_id, title), encoding="utf-8")
    if not entries:
        return p
    body = "\n".join(
        f"## entry {e['id']} | turn {e['turn']} | {e['kind']}\n"
        f"ts: {e['ts']}"
        + (f"\nmessage_id: {e['message_id']}" if e.get("message_id") else "")
        + (f"\ncall_id: {e['call_id']}" if e.get("call_id") else "")
        + f"\nsummary: {e['summary']}\n\n{e['text'].strip()}\n"
        for e in entries
    )
    with p.open("a", encoding="utf-8") as f:
        f.write("\n" + body)
    return p


def read_journal(path: Path | str, *, entry_id: int | None = None, needle: str = "", limit: int = 12) -> str:
    """Read one entry, or focus windows around a needle. Returns text, not a page dump."""
    p = Path(path)
    if not p.exists():
        return f"[journal not created yet: {p}]"
    raw = p.read_text(encoding="utf-8", errors="replace")
    # re.split returns the whole string when nothing matches, so a header-only file would
    # survive as a "section" here. Filter to actual entries only.
    sections = [s for s in re.split(r"\n(?=## entry )", raw) if s.strip().startswith("## entry ")]
    if not sections:
        # A header-only file is not a hit. Same discipline as the empty page rule: a
        # journal that holds nothing must not look like a successful read.
        return f"[journal has no entries yet: {p}]"

    if entry_id is not None:
        for s in sections:
            if s.startswith(f"## entry {entry_id} "):
                return s.strip()
        return f"[entry {entry_id} not found in {p} ({len(sections)} entries)]"

    if not needle:
        return "\n\n".join(s.strip() for s in sections[-limit:])

    hits = [s.strip() for s in sections if needle.lower() in s.lower()]
    if not hits:
        return f"[no journal match for {needle!r} in {len(sections)} entries]"
    return "\n\n".join(hits[:limit])


def entry_ids_by_message(path: Path | str) -> dict[str, list[dict]]:
    """message_id -> entries already in the journal. Makes partition idempotent across
    turns, and lets the model-facing copy show the pointer for a turn already journalled."""
    p = Path(path)
    if not p.exists():
        return {}
    raw = p.read_text(encoding="utf-8", errors="replace")
    out: dict[str, list[dict]] = {}
    for section in re.split(r"\n(?=## entry )", raw):
        if not section.strip().startswith("## entry "):
            continue
        head = re.match(r"^## entry (\d+) \| turn (\d+) \| (\w+)$", section.strip())
        msg = re.search(r"^message_id: (\S+)$", section, re.M)
        call = re.search(r"^call_id: (\S+)$", section, re.M)
        if head and msg:
            out.setdefault(msg.group(1), []).append(
                {"id": int(head.group(1)), "kind": head.group(3), "call_id": call.group(1) if call else None}
            )
    return out


def journal_index(path: Path | str, limit: int = 25) -> list[dict]:
    """Compact listing of what the journal holds, without reading the bodies."""
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^## entry (\d+) \| turn (\d+) \| (\w+)$", line.strip())
        if m:
            out.append({"id": int(m.group(1)), "turn": int(m.group(2)), "kind": m.group(3)})
    return out[:limit]
