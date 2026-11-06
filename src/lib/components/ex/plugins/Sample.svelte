<!-- Sample plugin for the settings_tab slot. It exists to prove the loop closes end to end: a
manifest declares the slot, the backend resolves it, the host renders it, and disabling the plugin
falls back to the builtin instead of breaking the modal.

Deliberately small. It reports the resolution it was given, so a later context can see that a slot
resolution actually reached a renderer rather than stopping in the registry.

Runes mode, matching this tree.
-->
<script lang="ts">
	import { exUiReport } from '$lib/stores/exSlots';

	const { slot = 'settings_tab' } = $props();

	const myEntry = ($exUiReport?.resolved?.slots ?? []).find((s: any) => s.slot === slot);
</script>

<div class="ex-plugin" data-plugin="sample" data-slot={slot}>
	<p class="text-xs font-mono text-gray-600 dark:text-gray-300">
		plugin sample, slot {slot}: {myEntry?.state ?? 'unresolved'}
	</p>
</div>
