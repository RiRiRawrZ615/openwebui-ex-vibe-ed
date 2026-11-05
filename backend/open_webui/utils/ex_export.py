"""Verbatim digest for compaction (EX mode).

Stock compaction produces a model-generated paraphrase: lossy, unverifiable, and
the reason summaries keep coming out short. When the EX setting `verbatimSummary`
is on, the digest is plain text built from the stored messages themselves.

Rules:
  - no model call, no paraphrasing, deterministic
  - chronological order, oldest first
  - first N lines per message, clipped to a per-message char budget
  - total output capped; when over cap, the newest messages win (they matter more
    for the next turn), oldest get folded to one line each
  - the result is what goes into the prompt as [CONVERSATION DIGEST]; the word
    "digest" is used deliberately so the model does not read it as a paraphrase

The stored context_summary column still holds the model summary when compaction
ran in stock mode. This module does not overwrite it; it only replaces what the
request receives. Nothing here is written to the DB.
"""

from __future__ import annotations

DIGEST_HEADER = "[CONVERSATION DIGEST]"

DEFAULT_LINES_PER_MESSAGE = 2
DEFAULT_MESSAGE_CHARS = 400
DEFAULT_TOTAL_CHARS = 4000


def _message_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("text", "input_text", "output_text"):
                parts.append(str(part.get("text", "")))
        return "\n".join(parts)
    return str(content or "")


def verbatim_digest(
    messages: list[dict],
    *,
    lines_per_message: int = DEFAULT_LINES_PER_MESSAGE,
    message_chars: int = DEFAULT_MESSAGE_CHARS,
    total_chars: int = DEFAULT_TOTAL_CHARS,
) -> str:
    """Build the digest. Deterministic. Returns '' when there is nothing to digest."""
    rows = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            continue
        text = _message_text(message).strip()
        if not text:
            continue
        lines = [line.strip() for line in text.splitlines() if line.strip()][: max(1, int(lines_per_message))]
        rows.append({"role": role, "text": "\n".join(lines)[: int(message_chars)]})

    if not rows:
        return ""

    def render(row: dict) -> str:
        return f"{row['role']}: {row['text']}"

    budget = int(total_chars)
    header = f"{DIGEST_HEADER}\n"

    # Newest messages win. Fold the oldest to one line each until the whole output
    # (header included) fits. If everything is one line and it still overflows, the
    # only correct move is to drop rows from the oldest end. Clipping from the head
    # would keep the oldest and lose the newest, which is the opposite of the rule.
    folded = [False] * len(rows)
    while True:
        lines = [render(rows[i]) if not folded[i] else render(rows[i]).split("\n")[0] for i in range(len(rows))]
        out = "\n".join(lines)
        if len(header) + len(out) <= budget:
            return header + out
        for i in range(len(rows)):
            if not folded[i]:
                folded[i] = True
                break
        else:
            while len(lines) > 1 and len(header) + len("\n".join(lines)) > budget:
                lines = lines[1:]
            out = "\n".join(lines)
            if len(header) + len(out) > budget:
                out = out[-(budget - len(header)):]
            return header + out
