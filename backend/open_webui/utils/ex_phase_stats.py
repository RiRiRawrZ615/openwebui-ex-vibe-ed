"""Engine phase stats: prefill, thinking, answering, reported separately.

The reason this exists is that a single tokens-per-second number hides which part of a
request is actually expensive. On an agentic turn the cost is usually prefill, because the
window is being re-read on every tool iteration; a rate figure averaged over the whole
request makes it look like generation is slow when the request is simply large.

Three separate numbers, from fields the engine already reports:

  prefill   - prompt_tokens, and prompt_ms when the engine reports it
  thinking  - completion_tokens_details.reasoning_tokens
  answering - completion_tokens minus the reasoning tokens

Nothing here is estimated when the engine does not report it. If prompt_ms is absent the
prefill time is left empty rather than inferred, because a guessed timing is what made the
gate's number look trustworthy in the first place.

Written as one JSON line per request under ex_phase_stats/, so the turn is readable without
opening the engine log.
"""

import json
import time
from pathlib import Path
from typing import Any

STATS_DIR = "ex_phase_stats"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _num(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return round(value, 1) if value > 0 else None


def phase_stats(
    usage: dict | None,
    chat_id: str = "",
    iteration: int = 0,
    send_ts: float | None = None,
    first_token_ts: float | None = None,
    gate: dict | None = None,
    flush: dict | None = None,
    data_dir: Path | str = "",
) -> dict:
    usage = usage if isinstance(usage, dict) else {}
    gate = gate if isinstance(gate, dict) else {}
    flush = flush if isinstance(flush, dict) else {}

    prompt_tokens = _int(usage.get("prompt_tokens") or usage.get("input_tokens"))
    completion = _int(usage.get("completion_tokens") or usage.get("output_tokens"))
    details = usage.get("completion_tokens_details") or {}
    if not isinstance(details, dict):
        details = {}
    reasoning = _int(details.get("reasoning_tokens") or details.get("thinking_tokens"))

    prompt_ms = _num(usage.get("prompt_ms"))
    ttft_ms = None
    if send_ts and first_token_ts:
        ttft_ms = round((first_token_ts - send_ts) * 1000, 1)

    stats: dict[str, Any] = {
        "chat_id": chat_id,
        "iteration": iteration,
        "prefill": {
            "tokens": prompt_tokens,
            "ms": prompt_ms,
            "ttft_ms": ttft_ms,
            "tokens_per_second": _num(usage.get("prompt_per_second")),
            # Whether the number the engine billed for this request matches what the gate
            # measured for the same list. This is the number that was missing when the fork
            # sent requests twice the size it thought it was sending.
            "gate_tokens": _int(gate.get("measured_after")),
            "match": bool(prompt_tokens and gate.get("measured_after"))
            and abs(prompt_tokens - _int(gate.get("measured_after"))) <= max(
                512, int(prompt_tokens * 0.08)
            ),
        },
        "thinking": {
            "tokens": reasoning,
            "ms": _num(usage.get("eval_ms")) if reasoning else None,
        },
        "answering": {
            "tokens": max(0, completion - reasoning),
            "ms": None if reasoning else _num(usage.get("eval_ms")),
        },
        "flush": {
            "flushed": bool(flush.get("flushed")),
            "evicted_tokens": _int(flush.get("evicted_tokens")),
            "truncated_results": _int(flush.get("tool_results_truncated")),
        },
    }

    if data_dir and chat_id:
        try:
            path = Path(str(data_dir)) / STATS_DIR
            path.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"ts": time.time(), **stats}) + "\n")
        except OSError:
            # Stats are observability, not correctness. A stats write that fails must not take
            # the request down with it.
            pass

    return stats


def format_phase_line(stats: dict) -> str:
    """One compact line for the engine stat display."""
    if not stats:
        return ""
    prefill = stats.get("prefill") or {}
    thinking = stats.get("thinking") or {}
    answering = stats.get("answering") or {}

    parts = [
        f"prefill {prefill.get('tokens', 0)}"
        + (f" tok / {prefill.get('ms')} ms" if prefill.get("ms") else ""),
        f"thinking {thinking.get('tokens', 0)} tok",
        f"answering {answering.get('tokens', 0)} tok",
    ]

    if prefill.get("gate_tokens") and not prefill.get("match"):
        parts.append(
            f"gate said {prefill.get('gate_tokens')}, engine billed {prefill.get('tokens')}"
        )

    flush = stats.get("flush") or {}
    if flush.get("flushed"):
        parts.append(f"flush evicted {flush.get('evicted_tokens', 0)} tok")

    return " | ".join(parts)
