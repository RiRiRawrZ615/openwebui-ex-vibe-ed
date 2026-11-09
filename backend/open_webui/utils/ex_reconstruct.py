"""The minimum set needed to restart a conversation in a fresh window.

Two facts shape this module.

First, nothing can interrupt a generation mid-turn. The only boundary the fork controls is the
iteration boundary, where the steering queue can stop the next call. So the safety net cannot be a
mid-turn interrupt; it has to be a rebuild at the start of the next turn.

Second, a handoff that reads as a user message makes the model believe the user said it. That is
what the earlier behaviour did. Everything here is written as a system instruction and frames the
turn as a new conversation, which is also what the prompt assembler already does structurally: the
prompt is rebuilt per turn rather than carried forward.

The parts are deliberately few, because the point is that a weak model can reconstruct from them
without re-reading the whole transcript:

  1. prior context (the summary chain or the journal index),
  2. the last full reasoning loop, uncut,
  3. the last assistant response,
  4. the last user message,
  5. the path of the text file holding all of the above, so the tool can read it back.

The file is plain text on purpose. It is readable by the journal tool, by grep, and by the user
without the fork being running.
"""
import re
import time
from pathlib import Path

FILE_NAME = "reconstruct.txt"
PARTS = ("prior_context", "last_reasoning", "last_response", "last_user")

# Inline block size cap. The file holds everything; the prompt carries a bounded copy so the
# handoff itself cannot become the thing that overflows the window.
INLINE_CAP = 1400


def reconstruct_path(data_dir: Path | str, chat_id: str) -> Path:
    """Same folder as the journal, so one lookup finds both."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(chat_id or "unknown")).strip("_") or "unknown"
    root = Path(data_dir) / "ex_journals"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{safe}.{FILE_NAME}"


def _text(message: dict) -> str:
    return str(message.get("content", "")).strip()


def build_reconstruct(messages: list[dict], prior_context: str = "") -> dict:
    """Pull the five parts out of a message list. No model call, no summariser.

    A handoff block that was written by the fork is not a user turn, so it is skipped when looking
    for the last real user message. Without that skip the reconstruction reports its own instruction
    as the user's request, which is the confusion this module exists to prevent.
    """
    last_user = ""
    last_response = ""
    last_reasoning = ""

    for message in messages or []:
        role = message.get("role")
        content = _text(message)
        if not content:
            continue
        if message.get("block") == "journal" or message.get("exSteering"):
            continue
        if role == "user":
            last_user = content
        elif role == "assistant":
            if message.get("type") == "reasoning":
                last_reasoning = content
            else:
                last_response = content

    return {
        "prior_context": str(prior_context or "").strip(),
        "last_reasoning": last_reasoning,
        "last_response": last_response,
        "last_user": last_user,
    }


def write_reconstruct(data_dir: Path | str, chat_id: str, messages: list[dict], prior_context: str = "") -> dict:
    """Write the reconstruction file and report what went into it."""
    parts = build_reconstruct(messages, prior_context)
    path = reconstruct_path(data_dir, chat_id)
    lines = [
        f"# reconstruct for {chat_id}",
        f"written {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}",
        "",
    ]
    for name in PARTS:
        lines.append(f"## {name}")
        lines.append(parts.get(name) or "(none)")
        lines.append("")
    try:
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError as exc:
        return {"path": str(path), "parts": parts, "written": False, "error": str(exc)}
    return {"path": str(path), "parts": parts, "written": True, "chars": int(path.stat().st_size)}


def reconstruct_block(data_dir: Path | str, chat_id: str, parts: dict, journal_ids: list | None = None) -> str:
    """The system instruction pasted back in place of the evicted turns.

    Deterministic. It says plainly that this is a new conversation and that the block is a system
    instruction rather than something the user said, so the model does not answer the handoff as
    though the user had asked it to acknowledge the handoff.
    """
    lines = [
        "[context slide]",
        "This is a new conversation. Nothing from the previous window carries over except what is",
        "written below. This block is a system instruction, not a message from the user; do not",
        "answer it, use it and continue from the actual request that follows.",
        "",
        "Prior context:",
        (parts.get("prior_context") or "(none)")[:INLINE_CAP],
        "",
        "Your last full reasoning loop:",
        (parts.get("last_reasoning") or "(none)")[:INLINE_CAP],
        "",
        "Your last response:",
        (parts.get("last_response") or "(none)")[:INLINE_CAP],
        "",
        "The last user message:",
        (parts.get("last_user") or "(none)")[:INLINE_CAP],
    ]
    ids = journal_ids or []
    if ids:
        lines += [
            "",
            "Earlier turns were evicted to fit the window. Their full text is in the journal; pull",
            "any of them with the journal tool (index, then read(entry=N)). entries: "
            + ", ".join(str(i) for i in ids),
        ]
    lines += ["", "Reconstruction file: " + str(reconstruct_path(data_dir, chat_id))]
    return "\n".join(lines)


def minimum_prompt(
    data_dir: Path | str,
    chat_id: str,
    messages: list[dict],
    *,
    keep_head: int = 1,
    prior_context: str = "",
) -> list[dict]:
    """The smallest prompt that still contains a real request.

    Used when nothing else fits. A generation cannot be interrupted mid-turn, so the only thing the
    fork can do is rebuild at the start of the next turn: pinned head, the reconstruction block, and
    the current request. That is enough for a weak model to continue, and it is what goes out instead
    of a refusal when it fits.
    """
    msgs = list(messages or [])
    head = [m for m in msgs if m.get("role") == "system"][:keep_head]
    request = [m for m in msgs if m.get("role") == "user"][-1:]
    parts = build_reconstruct(msgs, prior_context)
    return head + [{"role": "system", "content": reconstruct_block(data_dir, chat_id, parts)}] + request


__all__ = [
    "PARTS",
    "build_reconstruct",
    "minimum_prompt",
    "reconstruct_block",
    "reconstruct_path",
    "write_reconstruct",
]
