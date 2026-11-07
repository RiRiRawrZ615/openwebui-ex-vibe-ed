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

	if (status.speed) {
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

	if (status.tokens !== undefined && value.ceiling?.value) {
		chips.push({
			label: 'ctx',
			value: `${status.tokens.toLocaleString('en-US')}/${value.ceiling.value.toLocaleString('en-US')}`,
			title: `Message-list estimate against the engine's configured window (${value.ceiling.source}). Not the generation budget`
		});
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