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
	import { statChips, type Stats } from '$lib/utils/exStats';

	export let stats: Stats = {};
	export let approximate = false;
	export let windowTokens: number | undefined = undefined;

	// The window figure is only meaningful during a turn, when the caller is tracking the
	// message-list estimate. Showing it on a finished message would imply the window is still
	// moving.
	$: chips = statChips(stats, approximate);
</script>

{#if chips.length}
	<div
		class="ex-stats mt-1.5 flex flex-wrap items-center gap-1.5 font-mono text-[0.6875rem] leading-none"
		aria-label={approximate ? 'generation stats, estimated' : 'generation stats'}
	>
		{#each chips as chip (chip.label)}
			<span
				class="inline-flex items-center gap-1 rounded-md border border-gray-200/60 bg-gray-100/50 px-1.5 py-1 text-gray-500 dark:border-white/[0.06] dark:bg-white/[0.03] dark:text-gray-400"
				title={chip.title}
			>
				<span class="text-gray-400 dark:text-gray-500">{chip.label}</span>
				<span class="text-gray-600 dark:text-gray-300">{chip.value}</span>
			</span>
		{/each}

		{#if approximate && windowTokens}
			<span
				class="inline-flex items-center gap-1 rounded-md border border-gray-200/60 bg-gray-100/50 px-1.5 py-1 text-gray-500 dark:border-white/[0.06] dark:bg-white/[0.03] dark:text-gray-400"
				title="Estimated size of the message list sent to the model. Excludes the system prompt and tool schemas."
			>
				<span class="text-gray-400 dark:text-gray-500">window</span>
				<span class="text-gray-600 dark:text-gray-300">{windowTokens}</span>
			</span>
		{/if}

		{#if approximate}
			<span class="text-gray-400 dark:text-gray-600" title="Provider has not sent its usage block yet"
				>est</span
			>
		{/if}
	</div>
{/if}