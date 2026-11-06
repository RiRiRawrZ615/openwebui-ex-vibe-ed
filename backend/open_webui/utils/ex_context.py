"""Previous context as verbatim export text (pure).

The recovery problem this exists to solve. A compaction hands the next context a model
paraphrase, and a paraphrase is how a chat loses the exact facts it was working on. A plain
text export is strictly better because it is not interpreted: every line is what was actually
said.

The format is Open WebUI's own download format, read from the source rather than invented
here. src/lib/components/layout/Sidebar/ChatMenu.svelte:71 builds

    ### {role in upper case}
    {content}

so the reader parses exactly that, and a file produced by the download button round trips
with no loss. The reader also accepts a paste, which is the path RiRi uses: download, then
paste into the new chat.

Rules:
  - verbatim. Nothing here rewrites, summarizes, or reorders a line.
  - newest wins. When the block overflows the budget, rows are dropped from the oldest end,
    the same rule as ex_export.verbatim_digest. Clipping from the head would keep the oldest
    rows and lose the work in progress.
  - an export is reported as an export, never as a summary, so the model does not read it as
    a paraphrase.
  - a paste that is not in export shape is reported as unparseable instead of being forced
    into messages, because a misread role changes who said what.

Cache note, stated because it is real and easy to get wrong: llama.cpp reuses the KV cache
only from the last byte identical prefix. Previous context sits at the head of the messages,
so any change to it invalidates everything after it. That is a cost of this feature, not a
bug in it, and the module reports the cost instead of hiding it.
"""

from __future__ import annotations

import re

CONTEXT_HEADER = "[PREVIOUS CONTEXT, VERBATIM EXPORT]"
RESUME_LINE = "Continue from this record. Do not repeat steps already recorded as done."

DEFAULT_CONTEXT_CHARS = 12000
BLOCK = re.compile(r"^###\s+([A-Za-z_]+)\s*$", re.MULTILINE)

# Role names as they appear in the download format, mapped to message roles.
ROLE_MAP = {
    "USER": "user",
    "ASSISTANT": "assistant",
    "SYSTEM": "system",
    "TOOL": "tool",
}


def looks_like_export(text: str) -> bool:
    """Shape check only. A paste that does not carry export blocks is not a chat export."""
    text = str(text or "")
    return bool(BLOCK.search(text))


def parse_export(text: str) -> dict:
    """Read an export into messages.

    Returns the parsed rows plus what the parse actually did, so a caller can tell a real
    export from a paste that only looked like one. Text before the first block is treated as
    a header and dropped, because the download button writes the chat name above the blocks.
    """
    text = str(text or "")
    if not looks_like_export(text):
        return {"parsed": [], "header": "", "skipped": [], "parsed_from": "none",
                "reason": "no export blocks found"}

    positions = [(m.start(), m.group(1)) for m in BLOCK.finditer(text)]
    header = text[: positions[0][0]].strip()

    rows = []
    skipped = []
    for index, (start, raw_role) in enumerate(positions):
        end = positions[index + 1][0] if index + 1 < len(positions) else len(text)
        content = text[start + len("### " + raw_role):end].strip()
        role = ROLE_MAP.get(raw_role.upper())
        if role is None:
            # An unknown role is reported, not guessed. Guessing a role assigns the wrong
            # speaker, which is the one thing an export must not do.
            skipped.append({"raw_role": raw_role, "content": content[:200]})
            continue
        if not content:
            skipped.append({"raw_role": raw_role, "content": "", "reason": "empty block"})
            continue
        rows.append({"role": role, "content": content})

    return {
        "parsed": rows,
        "header": header,
        "skipped": skipped,
        "parsed_from": "export",
        "blocks": len(positions),
        "reason": "parsed export blocks verbatim",
    }


def previous_context_block(
    messages: list[dict],
    *,
    max_chars: int = DEFAULT_CONTEXT_CHARS,
    header: str = CONTEXT_HEADER,
    resume_line: str = RESUME_LINE,
) -> dict:
    """Build the block the request receives.

    Newest messages win. Rows are dropped from the oldest end when the budget is exceeded,
    and the number dropped is reported. Nothing is paraphrased on the way in.
    """
    messages = [m for m in (messages or []) if str(m.get("content") or "").strip()]
    rows = [f"{m.get('role') or 'unknown'}: {str(m.get('content')).strip()}" for m in messages]

    body = "\n".join(rows)
    dropped = 0
    truncated = False
    while header and len(header) + len(resume_line) + len(body) > int(max_chars) and len(rows) > 1:
        rows = rows[1:]
        dropped += 1
        truncated = True
        body = "\n".join(rows)

    if len(header) + len(resume_line) + len(body) > int(max_chars):
        # A single oversized message. Cut from its end so the newest text survives.
        room = max(0, int(max_chars) - len(header) - len(resume_line))
        body = body[:room]
        truncated = True

    block = f"{header}\n{body}\n\n{resume_line}"

    return {
        "block": block,
        "messages_kept": len(rows),
        "messages_dropped": dropped,
        "truncated": truncated,
        "kept_newest": True,
        "dropped_from_oldest": dropped > 0,
        "chars": len(block),
        "cache_note": "sits at the message head, so a change here invalidates the KV cache prefix",
    }


def previous_context_message(text: str, settings: dict | None) -> dict | None:
    """The single message to prepend, or None when the feature is off.

    Returning None keeps the stock request intact, the same posture as ex_wiring.digest.
    """
    settings = settings or {}
    if not settings.get("enabled") or not settings.get("previousContext"):
        return None

    parsed = parse_export(text)
    if not parsed["parsed"]:
        return None

    built = previous_context_block(parsed["parsed"], max_chars=int(settings.get("previousContextChars", DEFAULT_CONTEXT_CHARS)))
    return {
        "role": "system",
        "content": built["block"],
        "exPreviousContext": True,
        "source": "export",
        "meta": built,
        "skipped": parsed.get("skipped") or [],
    }


def status(settings: dict | None, last_parse: dict | None = None) -> dict:
    """Compact status for the tab, with the honest limits stated."""
    settings = settings or {}
    return {
        "previousContext": bool(settings.get("previousContext", True)),
        "previousContextChars": int(settings.get("previousContextChars", DEFAULT_CONTEXT_CHARS)),
        "feedBackAsSystemMessage": bool(settings.get("previousContextAsSystem", True)),
        "autoRetry": bool(settings.get("autoRetry", True)),
        "lastParse": last_parse or {"parsed_from": "none", "blocks": 0},
        "note": "verbatim export, newest wins, nothing paraphrased",
        "cache_warning": "the block is at the message head, so changing it forces a full re-prefill",
    }


__all__ = [
    "CONTEXT_HEADER",
    "RESUME_LINE",
    "DEFAULT_CONTEXT_CHARS",
    "ROLE_MAP",
    "looks_like_export",
    "parse_export",
    "previous_context_block",
    "previous_context_message",
    "status",
]
