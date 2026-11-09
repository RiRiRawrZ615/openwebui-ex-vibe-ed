// EX generation stats. TypeScript mirror of backend/open_webui/utils/ex_stats.py.
// The mirror is deliberate: the frontend cannot import Python, so both sides implement the
// same rules. If one changes, change the other. The backend version is the tested one
// (Code/vibe-ed/test_ex_stats.py), so treat it as the reference of record.
//
// WHAT CHANGED AND WHY
// --------------------
// The first version measured the wrong thing. It took `message.timestamp` as the start of
// generation and divided the whole message by wall-clock elapsed time. On a long agentic turn
// that reports one number for the entire turn, so a run with forty tool calls averaged out to
// a meaningless figure, and the number moved in the wrong direction as more work happened.
// The observed symptom was exactly that: a single tok/s figure that described the whole turn
// rather than the reply.
//
// The fix is to measure arrival, not elapsed-since-start. Each content update records when it
// arrived and how many token-ish units it added, and tok/s is computed over a short trailing
// window of those samples. That is what llama.cpp and llama-ui show: the instantaneous rate
// for the text currently streaming, not a lifetime average.
//
// Everything here stays honest about what it is: before the provider's final usage block, the
// figures are estimates from a character heuristic and are labelled as such. They are never
// merged into message.usage.

import { estimateTokens } from '$lib/utils/contextUsage';
import { type TokenSource } from '$lib/utils/exSettings';

const MICROSECONDS = 1_000_000;
const MILLISECONDS = 1_000;

function num(v: unknown): number | null {
	if (v === null || v === undefined || typeof v === 'boolean') return null;
	const n = Number(v);
	return Number.isFinite(n) ? n : null;
}

export type Stats = Record<string, number>;

export function generationStats(usage: Record<string, unknown> | undefined | null): Stats {
	const out: Stats = {};
	if (!usage) return out;

	const details = usage['prompt_tokens_details'];
	const outTokens = num(usage['output_tokens'] ?? usage['completion_tokens'] ?? usage['eval_count']);
	let inTokens = num(usage['input_tokens'] ?? usage['prompt_tokens'] ?? usage['prompt_eval_count']);
	if (inTokens === null) {
		const promptN = num(usage['prompt_n']);
		const cacheN = num(usage['cache_n']);
		if (promptN !== null) inTokens = promptN + (cacheN ?? 0);
	}
	const cached =
		num(usage['cache_n'] ?? usage['cached_tokens']) ??
		(typeof details === 'object' && details ? num((details as Record<string, unknown>)['cached_tokens']) : null);

	if (outTokens !== null) out['output_tokens'] = Math.round(outTokens);
	if (inTokens !== null) out['input_tokens'] = Math.round(inTokens);
	if (cached !== null) {
		// Same clamp as ex_stats.py. merge_usage accumulates the per-call cache figure across tool
		// rounds, so the same reused prefix is counted more than once and the chip can read larger
		// than the prompt it was read from.
		out['cached_tokens'] = Math.round(inTokens ? Math.min(cached, inTokens) : cached);
	}

	// llama.cpp and Strata both report a ready-made rate. That field describes one call and is
	// authoritative, so it has to come first. The order here used to be the other way round, which is
	// why tok/s read wrong on an agentic turn: out_tokens is the turn total across tool rounds while
	// t_eval and eval_duration describe the last call only, so a derived figure divides a turn total
	// by a single round's time.
	let tps: number | null =
		num(usage['tokens_per_second']) ??
		num(usage['predicted_per_second']) ??
		num(usage['eval_ts']);

	// ollama-style durations are microseconds.
	if (tps === null) {
		const evalDur = num(usage['eval_duration']);
		if (evalDur && evalDur > 0 && outTokens) tps = outTokens / (evalDur / MICROSECONDS);
	}

	// llama.cpp t_eval is milliseconds.
	if (tps === null) {
		const tEval = num(usage['t_eval']);
		if (tEval && tEval > 0 && outTokens) tps = outTokens / (tEval / MILLISECONDS);
	}

	if (tps !== null) out['tokens_per_second'] = Math.round(tps * 10) / 10;

	// Prompt processing rate, useful on a long context where prefill dominates.
	const promptTps = num(usage['prompt_per_second']) ?? num(usage['prompt_ts']);
	if (promptTps !== null) out['prompt_per_second'] = Math.round(promptTps * 10) / 10;

	const totalDur = num(usage['total_duration'] ?? usage['t']);
	if (totalDur !== null) {
		const secs = totalDur > 10 * MILLISECONDS ? totalDur / MICROSECONDS : totalDur / MILLISECONDS;
		out['wall_seconds'] = Math.round(secs * 10) / 10;
	} else {
		// llama.cpp reports timings as milliseconds, not a total duration, so the wall chip was
		// silently missing on every Strata turn even though the rate chip was present.
		const predictedMs = num(usage['predicted_ms']);
		if (predictedMs !== null && predictedMs > 0) {
			out['wall_seconds'] = Math.round((predictedMs / MILLISECONDS) * 10) / 10;
		}
	}

	return out;
}

export function formatStatsLine(stats: Stats, approximate = false): string {
	const parts: string[] = [];
	if (approximate) parts.push(`~${stats['tokens'] ?? 0} tok`);
	else if (stats['output_tokens'] !== undefined) parts.push(`${stats['output_tokens']} out`);
	if (stats['input_tokens'] !== undefined) parts.push(`${stats['input_tokens']} in`);
	if (stats['cached_tokens']) parts.push(`${stats['cached_tokens']} cached`);
	if (stats['tokens_per_second']) parts.push(`${stats['tokens_per_second']} tok/s`);
	if (stats['wall_seconds']) parts.push(`${stats['wall_seconds']}s`);
	return parts.join(' · ');
}

/**
 * Rolling arrival-rate sampler for one message.
 *
 * Kept as a class rather than module state because each assistant message streams
 * independently, and a multi-response turn has several of these alive at once. Sharing one
 * buffer would blend two models' rates into one wrong number.
 *
 * The window is time-based, not sample-count-based: taking the last N samples means a burst
 * of tiny chunks and a slow trickle of big ones produce different numbers for the same real
 * rate. Keeping only samples inside the last WINDOW_MS makes the figure mean "tokens per
 * second right now", which is what a live counter should mean.
 */
const WINDOW_MS = 1500;

type Sample = { at: number; tokens: number };

export class ArrivalRate {
	private samples: Sample[] = [];
	private lastTokens = 0;

	reset() {
		this.samples = [];
		this.lastTokens = 0;
	}

	/**
	 * Record the current total estimated token count for this message.
	 * Returns the current rate in tok/s, or 0 when there is not enough signal yet.
	 */
	record(totalTokens: number, now = Date.now()): number {
		const delta = Math.max(0, totalTokens - this.lastTokens);
		this.lastTokens = totalTokens;

		if (delta > 0) this.samples.push({ at: now, tokens: delta });

		// Drop samples that fell out of the window. A message that has stopped streaming keeps
		// its last computed rate instead of decaying to zero, because a rate that drops to zero
		// on completion would look like a failure rather than a finish.
		const cutoff = now - WINDOW_MS;
		while (this.samples.length && this.samples[0].at < cutoff) this.samples.shift();

		return this.rate(now);
	}

	rate(now = Date.now()): number {
		if (this.samples.length < 2) return 0;
		const first = this.samples[0];
		const span = now - first.at;
		if (span <= 0) return 0;
		const tokens = this.samples.reduce((sum, s) => sum + s.tokens, 0);
		return (tokens / span) * 1000;
	}
}

// Live row during streaming. Providers usually send usage only in the final chunk,
// so anything shown before that is an estimate and must be labelled as one.
// window_tokens is the message-list estimate (no system prompt here), so it is
// labelled as message-only by the caller.
/**
 * Window fill. Mirror of window_fill in backend/open_webui/utils/ex_stats.py; same rules, same key
 * names. The three figures stay three figures: the engine's request size is the window, the per-turn
 * count is not, and the message-list estimate is only an estimate.
 */
export type WindowFill = {
	fill: number | null;
	source: string;
	window: number | null;
	percent?: number;
};

export function windowFill(
	status: Record<string, any> | null,
	ceiling: number | null,
	lastPrefill?: Record<string, any> | null,
	fallbackEstimate?: number | null
): WindowFill {
	let fill: number | null = null;
	let source = 'none';
	const s = status ?? {};
	const generated = s.phase && s.phase !== 'reading the prompt' ? num(s.tokens) ?? 0 : 0;

	const prompt = num(s.prompt_tokens);
	if (prompt) {
		fill = Math.round(prompt + generated);
		source = 'engine';
	} else {
		const carried = num(lastPrefill?.prompt_tokens);
		if (carried) {
			fill = Math.round(carried + generated);
			source = 'carried';
		} else if (fallbackEstimate) {
			fill = Math.round(fallbackEstimate);
			source = 'estimate';
		}
	}

	const window = ceiling ? Math.round(ceiling) : null;
	const out: WindowFill = { fill, source, window };
	if (fill && window) out.percent = Math.round((100 * fill) / window * 10) / 10;
	return out;
}

export function liveStats(
	content: string,
	elapsedSeconds: number,
	windowTokens: number,
	arrivalRate?: number,
	tokenCount?: number
): { stats: Stats; line: string } {
	// tokenCount is the caller's measured count, and it has to include thinking. Counting only the
	// rendered message text made a thinking turn read as zero tokens, so the rate had no signal to
	// divide and the chip simply never appeared.
	const tokens = tokenCount ?? estimateTokens(content);
	const stats: Stats = { tokens, estimated_window_tokens: Math.max(0, Math.round(windowTokens)) };

	// Prefer the trailing arrival rate. Fall back to the lifetime average only before the
	// sampler has two samples to work with, which is a second or so at the top of a message.
	const rate = arrivalRate && arrivalRate > 0 ? arrivalRate : elapsedSeconds > 0 ? tokens / elapsedSeconds : 0;

	if (rate > 0) stats['tokens_per_second'] = Math.round(rate * 10) / 10;
	return { stats, line: formatStatsLine(stats, true) + ` · window ${stats['estimated_window_tokens']}` };
}

/**
 * Compact per-message stat chips.
 *
 * A single joined line was what made the row feel basic: the eye has nothing to land on, and a
 * missing field silently shifts every remaining label sideways. Separate labelled chips keep
 * their position when a provider omits part of the usage block, which happens constantly
 * because these backends all report slightly different fields.
 */
export type StatChip = { label: string; value: string; title: string };

// ---------------------------------------------------------------------------
// Display formatting
//
// Every value below arrives from the backend as a raw JS number, and raw numbers are not
// presentable. `String(44.833333333333336)` prints the full float, and `String(124243)`
// prints an unreadable digit run. Both were being rendered verbatim, which is exactly the
// kind of thing that reads as unfinished.
//
// Three shapes, three rules:
//   counts  - grouped, because a token count is read as a magnitude
//   rates   - one decimal place, because false precision on a moving average is noise
//   seconds - one decimal place, and never a bare integer, so 12.0s does not become 12s
// Nothing here rounds a value to zero, because a rendered "0"/s next to visible output
// looks like a bug rather than a small number.
// ---------------------------------------------------------------------------

export function groupDigits(n: number): string {
	return Math.round(n).toLocaleString('en-US');
}

function oneDecimal(n: number): string {
	if (!Number.isFinite(n)) return '-';
	if (n === 0) return '0';
	const rounded = Math.round(n * 10) / 10;
	// Small non-zero values would round to 0.0 and read as broken. Show the smallest
	// representation that is still honest.
	if (rounded === 0) return n < 0.1 ? '<0.1' : '0.1';
	return rounded.toFixed(1);
}

export function statChips(
	stats: Stats,
	approximate: boolean,
	tokenSource: TokenSource = 'both'
): StatChip[] {
	const chips: StatChip[] = [];

	// One token figure, from one source. The engine count lives in engineStatus.ts, so this is the
	// front-end side of the choice; gating only here would leave both counters on the row.
	if (tokenSource === 'both' || tokenSource === 'live') {
		if (approximate) {
			if (stats['tokens'] !== undefined)
				chips.push({
					label: 'tok',
					value: `~${groupDigits(stats['tokens'])}`,
					title: 'Estimated tokens generated so far (front-end arrival count, not the engine)'
				});
		} else if (stats['output_tokens'] !== undefined) {
			chips.push({
				label: 'out',
				value: groupDigits(stats['output_tokens']),
				title: 'Tokens generated (provider usage block)'
			});
		}
	}

	if (stats['input_tokens'] !== undefined)
		chips.push({
			label: 'in',
			value: groupDigits(stats['input_tokens']),
			title: 'Prompt tokens sent'
		});

	if (stats['cached_tokens'])
		chips.push({
			label: 'cache',
			value: groupDigits(stats['cached_tokens']),
			title: 'Prompt tokens served from the KV cache'
		});

	// The rate chips were not gated at all, which is why both counters always appeared no matter what
	// the source setting said. The token figures respected the choice and the rates did not, so the row
	// showed the front-end arrival rate and the engine rate side by side and the switch only moved the
	// token half. One gate per side: this side shows when the source is live or both.
	if (tokenSource !== 'engine' && stats['tokens_per_second'])
		chips.push({
			label: 'tok/s',
			value: oneDecimal(stats['tokens_per_second']),
			title: approximate ? 'Current arrival rate, estimated' : 'Generation rate reported by the backend'
		});

	if (tokenSource !== 'engine' && stats['prompt_per_second'])
		chips.push({
			label: 'prefill',
			value: oneDecimal(stats['prompt_per_second']),
			title: 'Prompt processing rate'
		});

	if (stats['wall_seconds'])
		chips.push({
			label: 't',
			value: `${oneDecimal(stats['wall_seconds'])}s`,
			title: 'Total generation time'
		});

	return chips;
}export type RolloverState = {
	rolls: number;
	entries?: number;
	slides?: number;
	last_shed_at?: number;
	last_shed_tokens?: number;
	last_kept_tokens?: number;
};

export type ContextChip = {
	label: string;
	value: string;
	count: number;
	active: boolean;
	title: string;
	phase: string;
};

export function contextChips(state: RolloverState | null): ContextChip[] {
	// Mirror of context_chips in backend/open_webui/utils/ex_stats.py. The icon is gone: a permanent
	// marker on a system that evicts every turn says nothing, and the number under it counted requests
	// rather than anything the agent could act on. The state is still recorded on disk for the log.
	// state is accepted so the caller signature stays the same as the backend mirror; nothing is drawn.
	void state;
	return [];
}
