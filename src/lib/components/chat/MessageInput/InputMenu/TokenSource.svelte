<!-- Which token counter the stats row shows.

Two counters exist and they are not the same measurement: one is the engine's own count read from
its log, the other is the front-end count of tokens that have arrived. Showing both on a row is two
numbers for the same idea, so this picks one. 'both' is available for the case where someone wants
the two side by side and can tell them apart.

Saved through the fork settings route rather than the settings modal, because this is quick access
from the composer. The store is patched immediately so the row updates without a reload, and the
route echoes the merged settings back.
-->
<script lang="ts">
	import { settings } from '$lib/stores';
	import { setExSettings } from '$lib/apis/ex';
	import { resolveExSettings, type TokenSource } from '$lib/utils/exSettings';

	const OPTIONS: { value: TokenSource; hint: string }[] = [
		{ value: 'engine', hint: "The engine's own count from its log" },
		{ value: 'live', hint: 'The front-end count of tokens that have arrived' },
		{ value: 'both', hint: 'Both, labelled by source' }
	];

	let current = $derived(resolveExSettings($settings).statsTokenSource);
	let saving = $state(false);

	const choose = async (value: TokenSource) => {
		if (value === current) return;

		const merged = { ...resolveExSettings($settings), statsTokenSource: value };
		settings.update((s) => ({ ...s, ui: { ...(s.ui ?? {}), ex: merged } }));

		saving = true;
		const res = await setExSettings(localStorage.token, { statsTokenSource: value });
		saving = false;

		if (res && 'error' in res) {
			// The store already changed, so the row shows the choice. Say plainly that it did not persist.
			console.warn('token counter choice did not save:', res.error);
		}
	};
</script>

<div class="mb-1 px-2 pt-1 pb-2">
	<div class="mb-1 text-[0.8125rem] text-gray-600 dark:text-gray-400">Token counter</div>

	<div class="flex flex-wrap gap-1">
		{#each OPTIONS as option}
			<button
				class="rounded-lg px-2 py-1 text-[0.8125rem] cursor-pointer {option.value === current
					? 'bg-gray-100 text-gray-900 dark:bg-gray-800 dark:text-gray-100'
					: 'text-gray-600 dark:text-gray-400 hover:bg-gray-50/40 dark:hover:bg-gray-800/40'}"
				title={option.hint}
				on:click={() => choose(option.value)}
			>
				{option.value}
			</button>
		{/each}

		{#if saving}
			<span class="text-[0.8125rem] text-gray-500 dark:text-gray-500">saving</span>
		{/if}
	</div>
</div>
