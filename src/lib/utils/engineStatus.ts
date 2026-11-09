// Engine status, read through the route, which reads the log the launcher writes. Nothing here opens
// the engine, and nothing here asks a model what the engine is doing.
//
// The fetch is cached with a minimum interval. A message component re-runs its reactive statements on
// every streamed chunk, so without a floor this would hit the route on every chunk. The engine prints a
// status line about once a second, so a three-second floor looks live without turning the row into a
// poll loop.

import { getExEngineStatus, type EngineStatusResponse } from '$lib/apis/ex';

const MIN_REFRESH_MS = 3000;

let cached: EngineStatusResponse | null = null;
let fetchedAt = 0;
let inFlight = false;

export function engineStatus(settings: Record<string, unknown>, token: string): EngineStatusResponse | null {
	if (!settings?.engineStatus) return null;

	const now = Date.now();
	if (cached && now - fetchedAt < MIN_REFRESH_MS) return cached;
	if (inFlight) return cached;

	inFlight = true;
	getExEngineStatus(token)
		.then((value) => {
			if (value && !('error' in value)) {
				cached = value as EngineStatusResponse;
				fetchedAt = Date.now();
			}
		})
		.catch(() => {
			// The row keeps whatever it already had. A failed read is not reported as a measurement.
		})
		.finally(() => {
			inFlight = false;
		});

	return cached;
}

export function engineChips(value: EngineStatusResponse | null, settings: Record<string, unknown>) {
	// Labels are kept apart on purpose. The engine's own decode speed and the frontend's windowed
	// arrival rate are different measurements, and on a turn with tool calls they can be far apart.
	const chips: { label: string; value: string; title: string }[] = [];
	const status = value?.status;

	if (!value || !status) return chips;

	// The rate was ungated, so the row always carried both the engine rate and the front-end arrival
	// rate even when the setting said one of them. Same rule as exStats.ts: this side shows only when
	// the source is engine or both.
	const rateSource = settings?.statsTokenSource ?? 'both';
	if ((rateSource === 'engine' || rateSource === 'both') && status.speed) {
		// The log says "reading the prompt"; the row should say what that phase is doing, which is prefill.
	const shownPhase = status.phase === 'reading the prompt' ? 'prefill' : status.phase;
	const phase = status.tool ? `${shownPhase} ${status.tool}` : shownPhase;
		chips.push({
			label: 'engine',
			value: `${status.speed} tok/s`,
			title: `Engine's own rate from its log, during ${phase}. Not the measured arrival rate`
		});
	} else if (status.no_speed) {
		chips.push({ label: 'engine', value: 'no speed', title: 'The log line for this turn carries no speed, so no rate is inferred from it' });
	}

	// The engine's own token count, against the engine's configured window. This is one of the two
	// counters on the row, so it shows only when the row is set to engine or both. The other one is the
	// front-end arrival count in exStats.ts, and the two are not the same measurement.
	const source = settings?.statsTokenSource ?? 'both';
	if (source === 'engine' || source === 'both') {
		const fill = value.fill;
		if (fill?.fill && fill.window) {
			// The window fill, which only grows across a chat. That is what makes it a window
			// measurement rather than a turn measurement.
			chips.push({
				label: 'ctx',
				value: `${fill.fill.toLocaleString('en-US')}/${fill.window.toLocaleString('en-US')}`,
				title: `Window fill, source ${fill.source}. 'engine' is the size of the request the engine read; 'carried' is the last request size plus this turn's output; 'estimate' is the message list only. Not the generation budget`
			});
		} else if (status.tokens !== undefined && value.ceiling?.value) {
			chips.push({
				label: 'ctx',
				value: `${status.tokens.toLocaleString('en-US')}/${value.ceiling.value.toLocaleString('en-US')}`,
				title: `Tokens produced in this turn against the engine's configured window (${value.ceiling.source}). No request size has been read yet, so this is not the window fill`
			});
		}
	}

	if (settings?.engineStatusEta && value?.eta?.seconds) {
		chips.push({
			label: 'eta',
			value: `${value.eta.seconds} s`,
			title: 'Arithmetic on the log line: (budget - tokens) / engine speed. No model call, no prediction'
		});
	}

	if (value.live === false && typeof value.age_seconds === 'number') {
		chips.push({ label: 'log', value: `${value.age_seconds} s`, title: 'The log has not updated for this long, so these figures are not current' });
	}

	return chips;
}