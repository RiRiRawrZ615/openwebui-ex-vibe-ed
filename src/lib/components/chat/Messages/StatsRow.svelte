<!-- Per-message generation stats.

Sits below the message, not above it. The old placement put the row before the answer, which
pushed the text down by a line and, worse, read as a header for the reply rather than a
footnote about it. llama-ui puts its counters under the message, and that is the right place:
the answer is the content, the numbers describe how it arrived.

Per-message, not per-turn. The previous version divided the whole message by elapsed wall time,
so a long agentic turn reported one blended number that described the turn instead of the reply.
Rates here come from a trailing arrival window (see ArrivalRate in exStats.ts), which is what
makes the figure mean "right now".

During streaming the numbers are estimates from a character heuristic and are marked with a
leading tilde. Once the provider sends its usage block they are replaced with real figures.
-->
<script lang="ts">
	import { statChips, groupDigits, contextChips, type Stats, type RolloverState } from '$lib/utils/exStats';

	export let stats: Stats = {};
	export let approximate = false;
	export let windowTokens: number | undefined = undefined;
	// Engine figures from the log, kept as a separate list because they are a different measurement from
	// the ones above. A windowed arrival rate and the engine's decode speed are not the same thing, and
	// folding them into one number would hide which one is which.
	export let engineChips: { label: string; value: string; title: string }[] = [];
	// Which token figure to show. Passed in rather than read here so the caller decides once for both
	// halves of the row.
	export let tokenSource: 'engine' | 'live' | 'both' = 'both';
	// The window fill with its source, from windowFill. When the engine has a request size the ctx chip
	// already shows the fill, so this chip appears only when the figure had to come from the message
	// list, which is an estimate and has to say so.
	export let windowInfo: { fill: number | null; source: string; window: number | null } | null = null;
	// Context rollover state for this chat. Dimmed when nothing has been evicted yet, lit after the
	// first shed, with the number of sheds in the top right. It is a state marker, so the dimmed form
	// matters: a marker that only appears after an event reads as an alarm rather than as a setting
	// that is armed and waiting.
	export let rollover: RolloverState | null = null;

	$: exRoll = contextChips(rollover)[0];

	// The window figure is only meaningful during a turn, when the caller is tracking the
	// message-list estimate. Showing it on a finished message would imply the window is still
	// moving.
	$: chips = [...statChips(stats, approximate, tokenSource), ...engineChips];
</script>

{#if chips.length || rollover}
	<div
		class="ex-stats mt-1.5 flex flex-wrap items-center gap-1.5 font-mono text-[0.6875rem] leading-none"
		aria-label={approximate ? 'generation stats, estimated' : 'generation stats'}
	>
		{#if rollover}
			<span
				class="relative inline-flex items-center rounded-md border px-1.5 py-1 {exRoll.active
					? 'border-gray-300 text-gray-700 dark:border-white/[0.12] dark:text-gray-200'
					: 'border-gray-200/60 text-gray-400 dark:border-white/[0.06] dark:text-gray-600'}"
				title={exRoll.title}
				aria-label={exRoll.title}
			>
				C
				<span class="absolute right-1 top-[-0.25rem] text-[0.5rem] leading-none">{exRoll.count}</span>
			</span>
		{/if}

		{#each chips as chip (chip.label)}
			<span
				class="inline-flex items-center gap-1 rounded-md border border-gray-200/60 bg-gray-100/50 px-1.5 py-1 text-gray-500 dark:border-white/[0.06] dark:bg-white/[0.03] dark:text-gray-400"
				title={chip.title}
			>
				<span class="text-gray-400 dark:text-gray-500">{chip.label}</span>
				<span class="text-gray-600 dark:text-gray-300">{chip.value}</span>
			</span>
		{/each}

		{#if approximate && (windowInfo?.fill ?? windowTokens)}
			{@const info = windowInfo ?? { fill: windowTokens ?? null, source: 'estimate', window: null }}
			<span
				class="inline-flex items-center gap-1 rounded-md border border-gray-200/60 bg-gray-100/50 px-1.5 py-1 text-gray-500 dark:border-white/[0.06] dark:bg-white/[0.03] dark:text-gray-400"
				title={info.source === 'estimate'
					? 'Estimated size of the message list sent to the model. Excludes the system prompt and tool schemas.'
					: `Window fill, source ${info.source}. Against the engine's configured window when known`}
			>
				<span class="text-gray-400 dark:text-gray-500">window</span>
				<span class="text-gray-600 dark:text-gray-300">
					{groupDigits(info.fill ?? 0)}{#if info.window} / {groupDigits(info.window)}{/if}
				</span>
			</span>
		{/if}

		{#if approximate}
			<span class="text-gray-400 dark:text-gray-600" title="Provider has not sent its usage block yet"
				>est</span
			>
		{/if}
	</div>
{/if}