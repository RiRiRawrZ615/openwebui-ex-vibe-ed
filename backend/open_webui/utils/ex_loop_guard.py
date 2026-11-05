"""Deterministic loop guard for the tool loop.

The stock code has no loop detection. The only guard is a ceiling on iterations,
which fires after the loop already happened and just ends the turn with an error
(middleware.py, tool-call limit reached). That is not detection. It is a timeout.

This module detects the actual loop shapes:

1. Identical tool call repeated. Same tool, same normalised arguments. Normalising
   matters because models re-emit the same call with different key order or spacing,
   which a naive string compare misses.
2. Identical tool output repeated. This is the stronger signal. A repeated call that
   returns different results is a model retrying on purpose. A repeated call returning
   byte-identical results is a loop.
3. Repeated reasoning with no progress. Near-identical chain of thought across
   consecutive turns, measured with word-set overlap. Cheap and deterministic, no
   model call, no embeddings.
4. Empty cycling. A tool call whose result comes back blank, counted from the result
   item, not from the call's arguments.

Every decision is a pure function over already-stored output items, so this runs
without a GPU, without a network, and without touching a live instance.

Mid-reasoning injection is not possible. Reasoning is produced incrementally from the
provider; there is no point at which text can be inserted into a half-finished thought.
The honest form of steering is: detect at the iteration boundary, then either refuse the
next call or inject a note into the message list before the next request. That is the
place the tool loop already rebuilds messages, so injection there is safe.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "so", "then", "now", "i", "we", "it", "its",
    "to", "of", "in", "on", "for", "with", "that", "this", "is", "are", "was", "be",
    "should", "need", "must", "can", "let", "me", "try", "use", "get", "check",
}

DEFAULT_REPEAT_THRESHOLD = 3
DEFAULT_SIMILARITY = 0.65
MIN_CONTENT_WORDS = 6


def normalise_args(args: Any) -> str:
    """Canonical form of tool arguments so key order and spacing cannot hide a repeat."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (ValueError, TypeError):
            return re.sub(r"\s+", " ", args.strip()).lower()
    if isinstance(args, dict):
        return json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False).lower()
    if isinstance(args, list):
        return json.dumps(args, sort_keys=False, separators=(",", ":"), ensure_ascii=False).lower()
    return str(args).strip().lower()


def call_signature(name: str, args: Any) -> str:
    basis = f"{str(name).strip().lower()}|{normalise_args(args)}"
    return hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()[:12]


def output_signature(text: str) -> str:
    basis = re.sub(r"\s+", " ", str(text).strip()).lower()
    return hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()[:12]


def word_set(text: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9_]{3,}", str(text).lower()))
    return words - STOPWORDS


def similarity(a: str, b: str) -> float:
    """Word-set Jaccard. Deterministic, cheap, no model call."""
    sa, sb = word_set(a), word_set(b)
    if len(sa) < MIN_CONTENT_WORDS or len(sb) < MIN_CONTENT_WORDS:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def scan(output_items: list[dict], *, repeat_threshold: int = DEFAULT_REPEAT_THRESHOLD,
         similarity_threshold: float = DEFAULT_SIMILARITY) -> dict:
    """Inspect a turn's output items and report what the loop guard sees.

    Returns a dict with counts and, when a loop is detected, the reason and the
    suggested action. No side effects.
    """
    calls: dict[str, int] = {}
    outputs: dict[str, int] = {}
    reasoning: list[str] = []
    empty_calls = 0

    for item in output_items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")

        if item_type == "function_call":
            sig = call_signature(item.get("name", ""), item.get("arguments", ""))
            calls[sig] = calls.get(sig, 0) + 1

        elif item_type == "function_call_output":
            text = ""
            for part in item.get("output") or []:
                if isinstance(part, dict) and part.get("type") == "input_text":
                    text += str(part.get("text", ""))
            if text.strip():
                sig = output_signature(text)
                outputs[sig] = outputs.get(sig, 0) + 1
            else:
                # A call whose result comes back blank is its own signal. Note this
                # must not fall back to the arguments as the "body", because a call
                # with arguments and a blank result is exactly the stalled case.
                empty_calls += 1

        elif item_type == "reasoning":
            text = ""
            for part in item.get("summary") or item.get("content") or []:
                if isinstance(part, dict):
                    text += str(part.get("text", ""))
            if text.strip():
                reasoning.append(text)

    repeated_call = max(calls.values(), default=0)
    repeated_output = max(outputs.values(), default=0)

    consecutive = 0
    best_pair = None
    for i in range(len(reasoning) - 1):
        score = similarity(reasoning[i], reasoning[i + 1])
        if score >= similarity_threshold:
            consecutive += 1
            best_pair = (i, i + 1, round(score, 3))

    verdict = "clean"
    reason = ""
    action = "continue"

    # Strongest signal first. Byte-identical results mean nothing changed. A blank result
    # is next because a stalled call is worse than a merely repeated one: the agent is
    # spending a full generation for nothing. Reasoning similarity is the weakest signal
    # because a model can legitimately restate the same plan while making progress.
    if repeated_output >= repeat_threshold:
        verdict = "loop"
        reason = f"identical tool output repeated {repeated_output} times"
        action = "refuse_next_call"
    elif empty_calls >= repeat_threshold:
        verdict = "loop"
        reason = f"{empty_calls} tool calls returned a blank result"
        action = "refuse_next_call"
    elif repeated_call >= repeat_threshold:
        verdict = "loop"
        reason = f"identical tool call repeated {repeated_call} times"
        action = "inject_note"
    elif consecutive >= 2:
        verdict = "loop"
        reason = f"reasoning repeated across {consecutive + 1} consecutive turns (similarity {best_pair[2]})"
        action = "inject_note"

    return {
        "verdict": verdict,
        "reason": reason,
        "action": action,
        "calls": len(calls),
        "max_call_repeat": repeated_call,
        "max_output_repeat": repeated_output,
        "reasoning_blocks": len(reasoning),
        "empty_calls": empty_calls,
        "pair": best_pair,
    }


def steering_note(hit: dict, tool_name: str = "") -> str:
    """Deterministic note text. No model call, no paraphrasing, so it cannot drift."""
    if hit.get("verdict") != "loop":
        return ""
    target = f" on {tool_name}" if tool_name else ""
    return (
        f"[loop guard] {hit['reason']}. Stop repeating this. Change approach: alter the "
        f"arguments, pick a different tool, or state what is blocked and end the turn."
        f"{target}"
    )


TOOL_MARKUP = re.compile(r"<tool_call>\s*<function=([A-Za-z0-9_]+)>\s*(.*?)</function>\s*</tool_call>", re.S)


def text_tool_markup(text: str) -> list[dict]:
    """Find tool markup that was emitted as message content instead of a structured call.

    This is the failure the loop guard could not see before. When a model writes
    <tool_call><function=name>...</function></tool_call> as prose, nothing in the backend
    parses it, so no tool runs and the turn simply ends because the content was complete.
    Detection has to look at message text, not output items. It also reports names that are
    not in the available tool set, which is the second half of the same failure.
    """
    found = []
    for m in TOOL_MARKUP.finditer(str(text or "")):
        found.append({"name": m.group(1), "body": m.group(2).strip()})
    return found



def text_tool_markup_issues(text: str, available: set[str] | None = None) -> dict:
    """Report tool markup written as prose, and names that do not exist.

    Both halves of the observed failure. The markup was message content, so nothing
    executed and the turn closed because the content was complete. The name, run_file,
    was not a tool in this instance. A guard that only checks repetition cannot see
    either one.
    """
    hits = text_tool_markup(text)
    unknown = sorted({h["name"] for h in hits if available is not None and h["name"] not in available})
    unclosed = str(text or "").count("<tool_call>") > str(text or "").count("</tool_call>")
    return {"markup": hits, "unknown": unknown, "unclosed": unclosed}

def pairing_safe_insert_index(messages: list[dict]) -> int:
    """Where a note can be inserted without breaking tool call/output pairing.

    The note goes after the last assistant message that carries tool calls, and after any
    tool results belonging to it. Inserting before tool results would leave a call with no
    matching result, which sanitize_tool_pairs would strip.
    """
    last = -1
    for i, m in enumerate(messages):
        if m.get("role") == "assistant" and (m.get("tool_calls") or m.get("output")):
            last = i
    if last == -1:
        return len(messages)
    i = last + 1
    while i < len(messages) and messages[i].get("role") == "tool":
        i += 1
    return i
