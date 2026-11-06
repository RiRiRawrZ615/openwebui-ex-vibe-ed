"""Agentic continuation: retry, resume, and the export fallback (pure).

What actually happened on this machine, and what this module is for:
  - a tool call was emitted as message text instead of a structured function_call, so
    nothing executed and the turn ended. Raising max output tokens did not help. The
    name the model wrote was replace_file_file_content, which is not a declared tool, so
    it came back as text and the response carried it as strata_rejected_tool_calls.
  - turns cut off mid tool call. A compaction or a 400 can end a turn while work is
    half-applied.

The policy below separates two things that are easy to confuse:

  1. A transport failure (400, 429, 5xx, connection reset, timeout). Retrying the same
     request can succeed, so retry is meaningful.
  2. A shape failure (a tool name the request did not declare, a call form the template
     does not use). Retrying the identical request produces the identical bad output, so
     blind retry is not a fix. This is reported as a shape failure and only retried with a
     corrective note, capped separately from transport retries.

Nothing here runs a network call. It decides what should happen next and records where the
work stopped, so a new context can pick up at the cursor instead of rebuilding state from
a paraphrase.

Checkpoint rule: a step is recorded as done only when its result exists. A step whose
result is unknown stays pending, because a half-applied step recorded as done is how a
later context repeats a write.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

# Named choices, stated as choices so a slider maps to them without guessing.
DEFAULT_MAX_RETRIES = 3
MAX_RETRIES_CEILING = 12
DEFAULT_BACKOFF_SECONDS = 2.0
BACKOFF_CEILING_SECONDS = 30.0
DEFAULT_SHAPE_RETRIES = 1

# Classes that are retryable by repeating the request.
RETRYABLE_CLASSES = (
    "transport_400",
    "transport_429",
    "transport_5xx",
    "connection_reset",
    "timeout",
    "context_compaction",
)

# A turn that ended with tool markup as text. Repeating it changes nothing unless the
# request changes, so this is not a transport class.
SHAPE_CLASS = "shape_rejected_tool_call"

RECOVERY_NAME = "ex_recovery.json"
SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")
CONTINUE_MESSAGE = "Continue from the checkpoint. Do not repeat steps already recorded as done."


def classify_error(error: str | int | None) -> dict:
    """Classify a failure as transport (retryable by repeating) or shape (not).

    A bare number is read as an HTTP status. Text is matched on phrases that appear in the
    actual messages, so a message that says something else is reported as unclassified
    rather than forced into a retry bucket.
    """
    if error is None:
        return {"class": "none", "retryable": False, "reason": "no error given"}

    if isinstance(error, int):
        status = error
        if status == 400:
            return {"class": "transport_400", "retryable": True, "reason": "status 400"}
        if status == 429:
            return {"class": "transport_429", "retryable": True, "reason": "status 429"}
        if 500 <= status <= 599:
            return {"class": "transport_5xx", "retryable": True, "reason": f"status {status}"}
        return {"class": f"status_{status}", "retryable": False, "reason": f"status {status} is not retryable"}

    text = str(error).lower()
    if "tool" in text and ("not declare" in text or "did not read as a call" in text or "rejected" in text):
        return {"class": SHAPE_CLASS, "retryable": False, "reason": "tool name the request did not declare"}
    if "compact" in text:
        return {"class": "context_compaction", "retryable": True, "reason": "compaction ended the context"}
    if "reset" in text or "connection closed" in text:
        return {"class": "connection_reset", "retryable": True, "reason": "connection dropped"}
    if "timeout" in text or "timed out" in text:
        return {"class": "timeout", "retryable": True, "reason": "timed out"}
    if "400" in text:
        return {"class": "transport_400", "retryable": True, "reason": "400 in the message"}
    if re.search(r"\b5\d\d\b", text):
        return {"class": "transport_5xx", "retryable": True, "reason": "5xx in the message"}
    return {"class": "unclassified", "retryable": False, "reason": "message does not match a known class"}


def rejected_tool_names(text: str) -> list[str]:
    """Names the model wrote that are not declared tools.

    These are extracted so the next attempt can say plainly which names failed. This does
    not execute them; a name in a report is not a call.
    """
    names = re.findall(r"[a-z_]+_[a-z_]+", str(text or ""))
    seen = set()
    out = []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def retry_permitted(verdict: dict, *, partial_output: bool = False) -> dict:
    """Whether the retry the verdict asks for is actually safe to perform.

    A transport failure before any output streamed is a clean replay. A failure after the stream
    already produced items is not, because replaying would append the same text twice. This is the
    difference between retry being a fix and retry being a corruption, and the caller must be able
    to see it separately from the class decision.
    """
    verdict = verdict or {}
    action = verdict.get("action")
    if action not in ("retry", "correct_and_retry"):
        return {"permitted": False, "reason": f"action {action} is not a replay"}
    if partial_output:
        return {"permitted": False, "reason": "attempt already streamed partial output, a replay would duplicate it"}
    return {"permitted": True, "reason": "nothing streamed for this attempt, replay is safe"}


def backoff(attempt: int, base: float = DEFAULT_BACKOFF_SECONDS, ceiling: float = BACKOFF_CEILING_SECONDS) -> float:
    """Capped exponential backoff. attempt 1 gives the base."""
    attempt = max(1, int(attempt))
    base = max(0.0, float(base))
    value = base * (2 ** (attempt - 1))
    return round(min(value, float(ceiling)), 2)


def decide(
    error: str | int | None,
    *,
    attempt: int = 1,
    max_retries: int = DEFAULT_MAX_RETRIES,
    shape_retries: int = DEFAULT_SHAPE_RETRIES,
    base_backoff: float = DEFAULT_BACKOFF_SECONDS,
) -> dict:
    """What should happen next. Refuses to claim a retry when the class is not retryable.

    `action` is one of: retry, correct_and_retry, stop, export_and_continue.
    """
    info = classify_error(error)
    attempt = max(1, int(attempt))
    max_retries = max(0, min(int(max_retries), MAX_RETRIES_CEILING))
    shape_retries = max(0, min(int(shape_retries), MAX_RETRIES_CEILING))

    if info["class"] == "none":
        return {"action": "stop", "class": info["class"], "retryable": False,
                "attempt": attempt, "max_retries": max_retries, "delay_seconds": 0.0,
                "reason": "no error to act on"}

    if info["class"] == SHAPE_CLASS:
        # Repeating the identical request repeats the identical bad shape. The only
        # meaningful next step is a corrective note, and that is capped separately.
        if shape_retries > 0:
            return {"action": "correct_and_retry", "class": info["class"], "retryable": True,
                    "attempt": attempt, "max_retries": shape_retries,
                    "delay_seconds": backoff(attempt, base_backoff),
                    "reason": "shape failure, retried with a corrective note"}
        return {"action": "export_and_continue", "class": info["class"], "retryable": False,
                "attempt": attempt, "max_retries": shape_retries, "delay_seconds": 0.0,
                "reason": "shape retries used up, fall back to the chat export"}

    if not info["retryable"]:
        return {"action": "stop", "class": info["class"], "retryable": False,
                "attempt": attempt, "max_retries": max_retries, "delay_seconds": 0.0,
                "reason": info["reason"]}

    if attempt > max_retries:
        return {"action": "export_and_continue", "class": info["class"], "retryable": True,
                "attempt": attempt, "max_retries": max_retries, "delay_seconds": 0.0,
                "reason": "retries used up, fall back to the chat export"}

    return {"action": "retry", "class": info["class"], "retryable": True,
            "attempt": attempt, "max_retries": max_retries,
            "delay_seconds": backoff(attempt, base_backoff), "reason": info["reason"]}


def checkpoint_path(data_dir: Path | str, name: str = RECOVERY_NAME) -> Path:
    return Path(data_dir) / name


def write_checkpoint(data_dir: Path | str, record: dict, name: str = RECOVERY_NAME) -> Path:
    """Store the resume point. Keys outside the agreed shape are dropped, the same rule the
    handshake uses, so a stale key cannot read like a real value.
    """
    record = record if isinstance(record, dict) else {}
    payload = {
        "chat_id": str(record.get("chat_id") or ""),
        "cursor": int(record.get("cursor") or 0),
        "done": [str(x) for x in (record.get("done") or [])],
        "pending": [str(x) for x in (record.get("pending") or [])],
        "last_error": record.get("last_error"),
        "class": record.get("class"),
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    path = checkpoint_path(data_dir, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_checkpoint(data_dir: Path | str, name: str = RECOVERY_NAME) -> dict | None:
    """Read the resume point. Takes the same name as the writer, so a custom name cannot
    write to a path the reader cannot see.
    """
    path = checkpoint_path(data_dir, name)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (ValueError, OSError):
        return None
    return raw if isinstance(raw, dict) else None


def resume_plan(checkpoint: dict | None) -> dict:
    """What a new context should do with a checkpoint.

    A step whose result is unknown stays pending. Nothing is called done here that the
    checkpoint did not record as done.
    """
    if not checkpoint:
        return {"has_checkpoint": False, "cursor": 0, "done": [], "pending": [],
                "message": CONTINUE_MESSAGE, "note": "no checkpoint, nothing to resume"}

    done = [str(x) for x in (checkpoint.get("done") or [])]
    pending = [str(x) for x in (checkpoint.get("pending") or [])]
    return {
        "has_checkpoint": True,
        "chat_id": checkpoint.get("chat_id"),
        "cursor": int(checkpoint.get("cursor") or 0),
        "done": done,
        "pending": pending,
        "last_error": checkpoint.get("last_error"),
        "class": checkpoint.get("class"),
        "message": CONTINUE_MESSAGE,
        "note": "resume at the cursor; steps already done are not repeated",
    }


def export_fallback(chat_id: str, messages: list[dict], max_chars: int = 20000) -> dict:
    """Plain-text export used as the attachment for the next chat.

    Newest messages win, so the clip drops from the oldest end. This is the same rule as the
    verbatim digest: a head clip would keep the oldest rows and drop the work that was
    actually in progress, which is the opposite of a resume.
    """
    chat_id = SAFE_NAME.sub("_", str(chat_id or "")) or "chat"
    lines = []
    for message in messages or []:
        role = str(message.get("role") or "unknown")
        content = str(message.get("content") or "")
        if not content:
            continue
        lines.append(f"{role}: {content}")

    body = "\n".join(lines)
    truncated = False
    if max_chars and len(body) > int(max_chars):
        body = body[-int(max_chars):]
        truncated = True

    return {
        "chat_id": chat_id,
        "filename": f"{chat_id}.txt",
        "content": body,
        "kept_newest": True,
        "dropped_from_oldest": truncated,
        "truncated": truncated,
        "messages": len(messages or []),
        "continue_with": CONTINUE_MESSAGE,
    }


def recovery_status(settings: dict, checkpoint: dict | None) -> dict:
    """Compact status for the tab. Reports the policy the settings actually hold, not an
    assumption about what the runtime is doing.
    """
    settings = settings or {}
    return {
        "auto_retry": bool(settings.get("autoRetry", True)),
        "max_retries": int(settings.get("maxRetries", DEFAULT_MAX_RETRIES)),
        "shape_retries": int(settings.get("shapeRetries", DEFAULT_SHAPE_RETRIES)),
        "backoff_seconds": float(settings.get("backoffSeconds", DEFAULT_BACKOFF_SECONDS)),
        "resume_on_crash": bool(settings.get("resumeOnCrash", True)),
        "export_fallback": bool(settings.get("exportFallback", True)),
        "ceiling": MAX_RETRIES_CEILING,
        "backoff_ceiling": BACKOFF_CEILING_SECONDS,
        "checkpoint": resume_plan(checkpoint),
    }


__all__ = [
    "DEFAULT_MAX_RETRIES",
    "MAX_RETRIES_CEILING",
    "DEFAULT_BACKOFF_SECONDS",
    "BACKOFF_CEILING_SECONDS",
    "DEFAULT_SHAPE_RETRIES",
    "RETRYABLE_CLASSES",
    "SHAPE_CLASS",
    "CONTINUE_MESSAGE",
    "RECOVERY_NAME",
    "classify_error",
    "rejected_tool_names",
    "backoff",
    "decide",
    "checkpoint_path",
    "write_checkpoint",
    "load_checkpoint",
    "resume_plan",
    "retry_permitted",
    "export_fallback",
    "recovery_status",
]
