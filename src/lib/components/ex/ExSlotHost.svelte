<!-- Frontend host for the UI slot registry.

This is the piece that makes modularity real. ex_ui.py resolves a slot to a component, but a
resolution means nothing until something renders it. This host is that something.

Rules carried from the backend, kept in the same shape so the frontend cannot be more permissive
than the registry:
  - a core slot renders the builtin. A plugin claiming a core slot is refused at resolve time, and
    it is not rendered here either, so both sides agree.
  - a plugin component is reached only through the glob. Nothing in the shipped UI imports a plugin
    file by name, which is what keeps a plugin from reaching the host by another route.
  - a missing plugin file falls back to the builtin rather than erroring, so a bad manifest cannot
    break the host. A slot is never blank.

The glob must be statically analyzable, so the pattern is fixed and the slot name selects an entry
from the resulting map. That is why the plugin directory has to sit next to this file.

Runes mode is verified, not assumed: svelte-check reports "Cannot use export let in runes mode" on
OpenWebVibes.svelte:41. So props use $props(), reactive values use $state, and a component held in a
variable is rendered directly, which is why the deprecated svelte:component tag is not used here.

The builtin arrives as a component, and the plugin receives it as a prop. A plugin that renders the
builtin adds to the host instead of hiding it; a plugin that does not render it replaces the builtin,
which is the legitimate reading of "fills a slot". Both are possible and neither breaks the host.
-->
<script lang="ts">
	import { onMount } from 'svelte';

	import { exSlots, refreshSlots } from '$lib/stores/exSlots';

	// Statically analyzable pattern. The slot name picks an entry from this map.
	const plugins: Record<string, () => Promise<any>> = import.meta.glob('./plugins/*.svelte');

	// Event forwarding. The shipped component dispatches 'save' through createEventDispatcher, so the
	// host forwards it to the modal. Passing a callback prop instead of an event directive keeps the
	// host readable, and the directive below is what actually carries the dispatch.
	const { slot = '', Builtin = null, saveSettings = null, onSave = null } = $props();

	let entry: any = $state(null);
	let currentSlots: any[] = $state([]);
	let PluginComp = $state<any>(null);

	$effect(() => {
		entry = (currentSlots ?? []).find((s: any) => s.slot === slot) ?? null;
		PluginComp = null;

		// Only a renderable slot loads a component. A manifest that resolves in the data plugin root
		// but has no file in this directory is reported as not renderable, and the builtin renders
		// instead. Rendering the plugin anyway would mean the frontend accepted a path the bundler
		// cannot see, which is the disagreement this flag exists to prevent.
		if (entry?.renderable) {
			const loader = plugins[`./plugins/${entry.component}`];
			if (loader) {
				loader().then((m: any) => {
					PluginComp = m?.default ?? null;
				});
			}
		}
	});

	// Read through a snapshot so the effect reacts to slot changes without holding a subscription
	// open across the component lifetime.
	exSlots.subscribe((s) => {
		currentSlots = s;
	});

	onMount(() => {
		refreshSlots(localStorage.token);
	});
</script>

<!-- h-full min-h-0 flex flex-col is load-bearing, not decoration. The slot lives inside a
     wrapper that is overflow-hidden, so a plain auto-height div hands the child an unbounded
     height. A tab that asks for h-full + overflow-y-auto then collapses to its content height
     and gets clipped instead of scrolling, which is exactly what happened to the OpenWebVibes
     tab. A flex column with min-h-0 gives the child a resolved height to overflow against. -->
<div
	class="ex-slot h-full min-h-0 flex flex-col"
	data-slot={slot}
	data-plugin={entry?.plugin ?? ''}
	data-state={entry?.state ?? 'unknown'}
>
	{#if entry?.renderable && PluginComp}
		<PluginComp
			slot={slot}
			Builtin={Builtin}
			{saveSettings}
			{onSave}
			on:save={(e: { detail: any }) => onSave?.(e.detail)}
		/>
	{:else if Builtin}
		<Builtin
			slot={slot}
			{saveSettings}
			{onSave}
			on:save={(e: { detail: any }) => onSave?.(e.detail)}
		/>
	{:else}
		<p class="text-xs text-gray-500 dark:text-gray-400">
			{slot}: no plugin resolved and no builtin supplied
		</p>
	{/if}
</div>
