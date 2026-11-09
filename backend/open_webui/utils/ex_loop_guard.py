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
import difflib
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

# A tool call is refused on its THIRD identical appearance, not its fourth.
#
# The threshold above counts repeats of a signature across the turn. That was the wrong unit:
# a call that appears three times total has been repeated twice, and by then the turn has
# spent two full generations producing a result the model already had. Refusing on the third
# sighting means the model gets one honest retry and a second chance to change approach, and
# the third attempt never runs. That matches what RiRi asked for and what the note says.
REFUSE_ON_OCCURRENCE = 3

# How many consecutive identical errors, arriving faster than the backoff window, mean the
# turn is not going to recover. Three instant failures is a broken request, not a slow one.
MAX_INSTANT_ERRORS = 3


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


def parses_as_args(args: Any) -> bool:
    """Whether the arguments could actually be executed as a call.

    A generation cut off mid-call leaves a half-written arguments string. That is not a call the
    model chose; it is an output limit. Treating it as an empty call blamed the model for the
    truncation and, past the threshold, steered it to call again, which usually produced another
    cut-off call. The two failures were being reported as one.
    """
    if isinstance(args, dict):
        return True
    if args is None:
        return False
    text = str(args).strip()
    if not text:
        return False
    try:
        json.loads(text)
        return True
    except (json.JSONDecodeError, ValueError):
        return False


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


def repeat_index(output_items: list[dict]) -> dict:
    """Map each call signature to the arithmetic index of the next identical call.

    The loop body needs to ask a different question than scan() answers: not "has this been
    repeated too often" (an after-the-fact count) but "if I ran the call I am about to run,
    how many times would that be in total". Refusing on total occurrence is what makes the
    third identical attempt fail instead of running and then being reported.

    Returns {signature: count_so_far}. The caller adds one for the call it is about to make.
    """
    counts: dict[str, int] = {}
    for item in output_items:
        if isinstance(item, dict) and item.get("type") == "function_call":
            sig = call_signature(item.get("name", ""), item.get("arguments", ""))
            counts[sig] = counts.get(sig, 0) + 1
    return counts


def would_repeat(output_items: list[dict], name: str, arguments: Any,
                 threshold: int = REFUSE_ON_OCCURRENCE) -> bool:
    """True when running this call would be the `threshold`-th identical attempt.

    A call with empty arguments is never refused: several tools take no arguments at all
    (listing a directory, reading a clock), and calling one of those repeatedly is normal
    behaviour rather than a loop.
    """
    if not str(arguments or "").strip() or str(arguments).strip() in ("{}", "null", "None"):
        return False
    sig = call_signature(name, arguments)
    return repeat_index(output_items).get(sig, 0) + 1 >= threshold


def refused_call_note(name: str) -> str:
    """What the model is told instead of a result. Deterministic, so it cannot drift."""
    return (
        f"[loop guard] This exact call to {name} has already run twice and returned nothing "
        f"new. It was not executed. You do not have to call a tool again to close this turn: "
        f"change the arguments, choose a different tool, answer in plain text, or state what "
        f"is blocked and end the turn."
    )


def scan(output_items: list[dict], *, repeat_threshold: int = DEFAULT_REPEAT_THRESHOLD,
         similarity_threshold: float = DEFAULT_SIMILARITY) -> dict:
    """Inspect a turn's output items and report what the loop guard sees.

    Returns a dict with counts and, when a loop is detected, the reason and the
    suggested action. No side effects.
    """
    calls: dict[str, int] = {}
    outputs: dict[str, int] = {}
    reasoning: list[str] = []
    malformed = 0
    # For each reasoning block after the first, how many tool calls ran between it and the
    # block before. A similar pair with progress between the blocks is a restated plan, not a
    # loop; the guard used to fire on those and push the model to call a tool it did not need.
    gap_progress: list[int] = []
    calls_since = 0
    empty_calls = 0

    for item in output_items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")

        if item_type == "function_call":
            if not parses_as_args(item.get("arguments", "")):
                malformed += 1
            sig = call_signature(item.get("name", ""), item.get("arguments", ""))
            calls[sig] = calls.get(sig, 0) + 1
            calls_since += 1

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
                gap_progress.append(calls_since)
                calls_since = 0
                reasoning.append(text)

    repeated_call = max(calls.values(), default=0)
    repeated_output = max(outputs.values(), default=0)

    consecutive = 0
    stalled = 0
    best_pair = None
    for i in range(len(reasoning) - 1):
        score = similarity(reasoning[i], reasoning[i + 1])
        if score >= similarity_threshold:
            consecutive += 1
            if gap_progress[i + 1] == 0:
                stalled += 1
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
    elif stalled >= 2:
        verdict = "loop"
        reason = (
            f"reasoning repeated across {stalled + 1} consecutive turns with no tool call "
            f"between them (similarity {best_pair[2]})"
        )
        action = "inject_note"
    elif malformed:
        # Its own verdict, not folded into the loop counts. The call was cut off, so the useful
        # response is a shorter call or a plain answer, not another attempt with the same payload.
        verdict = "truncated"
        reason = (
            f"{malformed} tool call(s) arrived with arguments that do not parse, which reads as a "
            f"generation cut off mid-call"
        )
        action = "shorten_next_call"
    elif consecutive >= 2:
        # Similar thinking, but the agent kept acting between the blocks. Progress is not a
        # loop, so nothing is injected and a plain answer is allowed.
        reason = f"reasoning restated the same plan while tool calls changed ({consecutive} similar pairs)"

    return {
        "verdict": verdict,
        "reason": reason,
        "action": action,
        "calls": len(calls),
        "max_call_repeat": repeated_call,
        "max_output_repeat": repeated_output,
        "reasoning_blocks": len(reasoning),
        "stalled_pairs": stalled,
        "empty_calls": empty_calls,
        "malformed_calls": malformed,
        "pair": best_pair,
    }


def truncated_call_note(hit: dict) -> str:
    """What the model is told when a call arrived cut off, which is not a loop."""
    if hit.get("verdict") != "truncated":
        return ""
    return (
        f"[loop guard] {hit['reason']}. Nothing is wrong with the plan; the response ended before "
        f"the call was complete. Re-issue it with short arguments, or state what you meant in plain "
        f"text and end the turn. Repeating the same long payload will cut off again."
    )


def steering_note(hit: dict, tool_name: str = "") -> str:
    """Deterministic note text. No model call, no paraphrasing, so it cannot drift."""
    if hit.get("verdict") == "truncated":
        return truncated_call_note(hit)
    if hit.get("verdict") != "loop":
        return ""
    target = f" on {tool_name}" if tool_name else ""
    return (
        f"[loop guard] {hit['reason']}. This is an observation, not an order to call a tool. "
        f"If it is really stuck, change approach: alter the arguments, pick a different tool, "
        f"answer in plain text with what is already known, or state what is blocked and end "
        f"the turn."
        f"{target}"
    )


TOOL_MARKUP = re.compile(r"<tool_call>\s*<function=([A-Za-z0-9_]+)>\s*(.*?)</function>\s*</tool_call>", re.S)
INVOKE_MARKUP = re.compile(r"<invoke\s+name=\"([A-Za-z0-9_]+)\"", re.S)
FENCED_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.S)
# The group covers the whole call, not only the name and its opening paren. Without the arguments and
# the closing paren, a prose call leaves its body behind, and that leftover reads as answer text when
# deciding whether a replay is safe.
PROSE_CALL = re.compile(r"(?:^|\n)[ \t]*([A-Za-z_][A-Za-z0-9_]{2,})\([^\n]*\)", re.S)


def text_tool_call_shapes(text: str, available: set[str] | None = None) -> list[dict]:
    """Every shape a tool call can arrive in as message text.

    The guard previously recognised exactly one shape, the template form. That is not the
    failure that keeps happening: a call written as `name(arg)` on its own line, or an
    <invoke> block placed inside a code fence, is never parsed either, and neither produced a
    hit because no name was undeclared. The name was declared and the shape was unreadable,
    so the turn closed as a text answer.

    Shapes are recorded separately from the name check because the two need different
    responses: an undeclared name is a capability miss, an unreadable shape is a formatting
    miss, and both are worth one re-request.
    """
    text = str(text or "")
    shapes = []
    for m in TOOL_MARKUP.finditer(text):
        shapes.append({"name": m.group(1), "shape": "template_markup", "span": m.span()})
    # An invoke shape is recorded with the block it belongs to, not just the opening tag. A span over
    # the tag alone leaves the parameters behind, and those read as answer text.
    fences = [m.span() for m in FENCED_BLOCK.finditer(text)]
    for m in INVOKE_MARKUP.finditer(text):
        span = next((f for f in fences if f[0] <= m.start() < f[1]), m.span())
        shapes.append({"name": m.group(1), "shape": "fenced_or_unwrapped_invoke", "span": span})
    # A call written as prose is only a call when the name is one the request declared, and
    # when it stands on its own line. `min(1, 2)` inside a sentence is not a tool call, so the
    # two conditions are what keep this from flagging ordinary text.
    for m in PROSE_CALL.finditer(text):
        name = m.group(1)
        if available is None or name in available:
            shapes.append({"name": name, "shape": "prose_call", "span": m.span()})
    return shapes


def resolve_near_miss(name: str, available: set[str] | None, *, cutoff: float = 0.62) -> str | None:
    """One plausible declared tool for a mistyped name, or None.

    A single candidate is a correction; two or more is a guess and is left alone. Scored on the
    whole name and on underscore-separated parts, because a name like replace_file_path differs
    from replace_file_content by one word, and a character-ratio test alone can fall below the
    cutoff when the two names differ in length.
    """
    if not available or name in available:
        return None
    scores: list[tuple[float, str]] = []
    for cand in sorted(available):
        ratio = difflib.SequenceMatcher(None, name, cand).ratio()
        a, b = name.split("_"), cand.split("_")
        shared = sum(1 for x, y in zip(a, b) if x == y)
        parts = shared / max(len(a), len(b)) if a and b else 0.0
        scores.append((max(ratio, parts), cand))
    if not scores:
        return None
    scores.sort(reverse=True)
    best, best_name = scores[0]
    if best < cutoff:
        return None
    if [c for s, c in scores[1:] if s == best]:
        return None
    return best_name


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
    shapes = text_tool_call_shapes(text, available)
    unknown = sorted({h["name"] for h in shapes if available is not None and h["name"] not in available})
    # Declared names in a shape the parser cannot read. This is the half that was missing:
    # the name existed, so the old rule called it a formatting miss and stopped there, while
    # nothing executed and the turn answered with the markup itself.
    unparsed = sorted({h["name"] for h in shapes if h["shape"] != "template_markup"})
    unclosed = str(text or "").count("<tool_call>") > str(text or "").count("</tool_call>")
    # Whether the message consists only of call shapes. That distinction is what decides if a replay
    # is safe: text the user actually received cannot be sent twice, but a reply that was nothing but
    # an unreadable call block delivered nothing, and refusing to re-run there is what left the turn
    # answering with markup.
    # Spans, not substitution. Substitution cannot tell a matched shape from the text around it that
    # it did not consume, so a call written as name(arg) left its arguments behind as leftover text and
    # read as an answer. A shape that consumed the whole call is what makes this test mean anything.
    stripped = str(text or "")
    for shape in shapes:
        span = shape.get("span")
        if span:
            stripped = stripped[:span[0]] + "\n" + stripped[span[1]:]
    # The third case the decision needs. A name the request did not declare is a capability
    # miss, but a name that resolves to exactly one declared tool is a typo, and the two want
    # different responses: the typo can be corrected in place, the capability miss cannot.
    corrected = {n: resolve_near_miss(n, available) for n in unknown}
    corrected = {k: v for k, v in corrected.items() if v}
    return {
        "markup": shapes,
        "unknown": unknown,
        "unparsed": unparsed,
        "unclosed": unclosed,
        "corrected": corrected,
        "markup_only": bool(shapes) and not stripped.strip(),
    }

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
