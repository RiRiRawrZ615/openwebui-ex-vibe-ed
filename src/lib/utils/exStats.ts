// EX generation stats. TypeScript mirror of backend/open_webui/utils/ex_stats.py.
// The mirror is deliberate: the frontend cannot import Python, so both sides
// implement the same rules. If one changes, change the other. The backend version
// is the tested one (Code/vibe-ed/test_ex_stats.py), so treat it as the reference.

import { estimateTokens } from '$lib/utils/contextUsage';

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
	if (cached !== null) out['cached_tokens'] = Math.round(cached);

	let tps: number | null = null;

	// ollama-style durations are microseconds.
	const evalDur = num(usage['eval_duration']);
	if (evalDur && evalDur > 0 && outTokens) tps = outTokens / (evalDur / MICROSECONDS);

	// llama.cpp t_eval is milliseconds.
	if (tps === null) {
		const tEval = num(usage['t_eval']);
		if (tEval && tEval > 0 && outTokens) tps = outTokens / (tEval / MILLISECONDS);
	}

	tps = tps ?? num(usage['tokens_per_second']) ?? num(usage['eval_ts']);
	if (tps !== null) out['tokens_per_second'] = Math.round(tps * 10) / 10;

	const totalDur = num(usage['total_duration'] ?? usage['t']);
	if (totalDur !== null) {
		const secs = totalDur > 10 * MILLISECONDS ? totalDur / MICROSECONDS : totalDur / MILLISECONDS;
		out['wall_seconds'] = Math.round(secs * 10) / 10;
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

// Live row during streaming. Providers usually send usage only in the final chunk,
// so anything shown before that is an estimate and must be labelled as one.
// window_tokens is the message-list estimate (no system prompt here), so it is
// labelled as message-only by the caller.
export function liveStats(content: string, elapsedSeconds: number, windowTokens: number): { stats: Stats; line: string } {
	const tokens = estimateTokens(content);
	const stats: Stats = { tokens, estimated_window_tokens: Math.max(0, Math.round(windowTokens)) };
	if (elapsedSeconds > 0) stats['tokens_per_second'] = Math.round((tokens / elapsedSeconds) * 10) / 10;
	return { stats, line: formatStatsLine(stats, true) + ` · window ${stats['estimated_window_tokens']}` };
}
