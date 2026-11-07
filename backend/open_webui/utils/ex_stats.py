"""Generation stats: tokens/s, tokens used, cached, wall time.

Pure. Reads the usage dict exactly as the fork's normalize_usage produces it
plus llama.cpp `timings` merged on top (middleware merges raw_usage.update(data.get('timings'))).

Handles both shapes seen in practice:
  ollama-style   eval_count / eval_duration / prompt_eval_count / prompt_eval_duration (microseconds)
  llama.cpp      eval_count / t_eval / prompt_tokens / t_prompt (values in ms for t_*, us for *_duration)

The end-of-message i icon and the live row both render from this. The live row
during streaming uses estimate_live because providers usually send usage only at
the end; per-delta token counts are estimated client-side and labelled approximate.
"""

from __future__ import annotations

MICROSECONDS = 1_000_000
MILLISECONDS = 1_000


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def estimate_tokens(text: str) -> int:
    """Rough client-side token estimate, same heuristic class as the fork's estimateTokens."""
    if not text:
        return 0
    return max(1, int(len(text) / 4))


def estimate_window_tokens(messages: list[dict]) -> int:
    """Mirror of estimateMessagesTokens in src/lib/utils/contextUsage.ts.

    +4 per message matches the per-message overhead the fork already uses. Same
    convention as estimateTokens, so the backend number and the UI number agree.
    """
    total = 0
    for message in messages:
        next_total = total + 4
        content = message.get('output') or message.get('content')
        next_total += estimate_tokens(content)
        if message.get('files'):
            next_total += estimate_tokens(message.get('files'))
        total = next_total
    return total


def live_estimates(streamed_text: str, window_tokens: int) -> dict:
    """Per-turn estimates for the live row during streaming.

    window_tokens comes from estimate_window_tokens or the UI context estimate.

    Providers usually report usage only at the end of the stream, so during
    generation these are estimates, labelled approximate. Never mixed into the
    confirmed usage dict on the message.
    """
    # Key is 'tokens' to match the TS mirror and format_stats_line, which reads
    # stats['tokens'] in approximate mode. Naming it estimated_output_tokens here
    # would make the live line render ~0.
    return {
        'tokens': estimate_tokens(streamed_text),
        'estimated_window_tokens': int(window_tokens),
        'approximate': True,
    }


def generation_stats(usage: dict | None) -> dict:
    """Compact stats for display. Fields omitted when the provider did not report them."""
    usage = usage or {}
    out: dict = {}

    out_tokens = _num(usage.get("output_tokens") or usage.get("completion_tokens") or usage.get("eval_count"))
    in_tokens = _num(usage.get("input_tokens") or usage.get("prompt_tokens") or usage.get("prompt_eval_count"))
    if in_tokens is None:
        prompt_n = _num(usage.get("prompt_n"))
        cache_n = _num(usage.get("cache_n"))
        if prompt_n is not None:
            in_tokens = prompt_n + (cache_n or 0)
    cached = _num(usage.get("cache_n") or usage.get("cached_tokens")) or _num(
        (usage.get("prompt_tokens_details") or {}).get("cached_tokens") if isinstance(usage.get("prompt_tokens_details"), dict) else None
    )

    if out_tokens is not None:
        out["output_tokens"] = int(out_tokens)
    if in_tokens is not None:
        out["input_tokens"] = int(in_tokens)
    if cached is not None:
        # Cached tokens cannot exceed the prompt they were read from. merge_usage accumulates the
        # per-call cache figure across tool rounds, so the same reused prefix is counted several
        # times and the chip can read larger than the prompt it belongs to. Clamping keeps the
        # reading honest without changing what the merge stored.
        if in_tokens:
            cached = min(cached, in_tokens)
        out["cached_tokens"] = int(cached)

    # A rate is a measurement of one call, so a value the engine already computed is authoritative
    # and must beat anything derived here. Deriving first is what made the tok/s chip read wrong on
    # an agentic turn: out_tokens is cumulative across rounds while t_eval/eval_duration describe the
    # last call only, so the derived number is the turn total divided by one round's time.
    # Engine-computed rates first. The order was tokens_per_second, eval_ts, predicted_per_second,
    # which put an ollama field ahead of the llama.cpp field that Strata actually sends, so the two
    # halves of the stats code disagreed about which number was authoritative.
    tps = (
        _num(usage.get("tokens_per_second"))
        or _num(usage.get("predicted_per_second"))
        or _num(usage.get("eval_ts"))
    )
    if tps is None:
        # ollama-style durations are microseconds
        eval_dur = _num(usage.get("eval_duration"))
        if eval_dur and eval_dur > 0 and out_tokens:
            tps = out_tokens / (eval_dur / MICROSECONDS)
    if tps is None:
        # llama.cpp t_eval is milliseconds
        t_eval = _num(usage.get("t_eval"))
        if t_eval and t_eval > 0 and out_tokens:
            tps = out_tokens / (t_eval / MILLISECONDS)
    if tps is not None:
        out["tokens_per_second"] = round(tps, 1)

    # Prefill is reported separately from generation, and on a long context it is the number that
    # explains a slow turn. The TS mirror already had this; the Python reference did not, which is a
    # divergence between the two halves of the same rule.
    prefill = _num(usage.get("prompt_per_second")) or _num(usage.get("prompt_ts"))
    if prefill is not None:
        out["prompt_per_second"] = round(prefill, 1)

    total_dur = _num(usage.get("total_duration")) or _num(usage.get("t"))
    if total_dur is not None:
        secs = total_dur / MICROSECONDS if total_dur > 10 * MILLISECONDS else total_dur / MILLISECONDS
        out["wall_seconds"] = round(secs, 1)
    else:
        # llama.cpp timings report milliseconds per phase and no total duration, so the wall chip
        # was missing on every Strata turn. The engine's own rate is still the headline number.
        predicted_ms = _num(usage.get("predicted_ms"))
        if predicted_ms is not None and predicted_ms > 0:
            out["wall_seconds"] = round(predicted_ms / MILLISECONDS, 1)

    return out


def format_stats_line(stats: dict, approximate: bool = False) -> str:
    """One-line render used by both the live row and the i icon."""
    parts = []
    if approximate:
        parts.append(f"~{stats.get('tokens', 0)} tok")
    elif stats.get("output_tokens") is not None:
        parts.append(f"{stats['output_tokens']} out")
    if stats.get("input_tokens") is not None:
        parts.append(f"{stats['input_tokens']} in")
    if stats.get("cached_tokens"):
        parts.append(f"{stats['cached_tokens']} cached")
    if stats.get("tokens_per_second"):
        parts.append(f"{stats['tokens_per_second']} tok/s")
    if stats.get("wall_seconds"):
        parts.append(f"{stats['wall_seconds']}s")
    return " · ".join(parts)
