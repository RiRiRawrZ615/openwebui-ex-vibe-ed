<!--
  Builtin tools: the ten shipped tools, their switches, and their user-edit overlays.

  Design notes
  ------------
  Three things drive the layout.

  1. A tool is not one function. Web Search exposes three, leanrea twenty-one. So the row shows
     the count and the names, because "which of the 68 functions did I just disable" is a real
     question when something stops working.

  2. The overlay editor is opened per tool, not inline. Nine editors on one screen would be
     unreadable, and an admin edits one tool at a time in practice.

  3. Errors are surfaced, never swallowed. A tool that fails to import shows the exception on its
     own row rather than vanishing, and a stale overlay is labelled, because that is exactly the
     case where someone needs to know their edit is shadowing a newer shipped version.

  Write actions are admin-only. The backend enforces it; this only avoids offering a control that
  would 403.
-->
<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { settings } from '$lib/stores';
	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import UserSettingSection from './UserSettingSection.svelte';
	import UserSettingRow from './UserSettingRow.svelte';
	import { resolveExSettings } from '$lib/utils/exSettings';
	import { setExSettings } from '$lib/apis/ex';
	import {
		getExBuiltins,
		getExBuiltin,
		setExBuiltinEnabled,
		saveExBuiltinOverlay,
		revertExBuiltinOverlay,
		type ExBuiltin,
		type ExBuiltinDetail
	} from '$lib/apis/ex';

	const dispatch = createEventDispatcher();

	let loading = $state(true);
	let builtins = $state<ExBuiltin[]>([]);
	let canEdit = $state(false);

	// Editor state. `editing` is the slug whose editor is open; the detail carries the source.
	let editing = $state<string | null>(null);
	let detail = $state<ExBuiltinDetail | null>(null);
	let draft = $state('');
	let editorBusy = $state(false);
	let savingSlug = $state<string | null>(null);

	/** Tools with a load problem, surfaced at the top because they need attention. */
	let broken = $derived(builtins.filter((b) => b.load_error));
	/** Tools whose overlay was written against an older shipped version. */
	let stale = $derived(builtins.filter((b) => b.overlay_stale));
	let enabledCount = $derived(builtins.filter((b) => b.enabled).length);
	let totalFunctions = $derived(builtins.reduce((n, b) => n + (b.specs?.length ?? 0), 0));

	const load = async () => {
		loading = true;
		const res = await getExBuiltins(localStorage.token);
		if ('error' in res) {
			toast.error(`${res.error}`);
		} else {
			builtins = res.builtins ?? [];
			canEdit = res.can_edit;
		}
		loading = false;
	};

	$effect(() => {
		load();
	});

	// The switch here is the instance default for a chat that has not picked tools. It is separate
	// from each tool's own switch: the per-tool switch decides whether a tool is active, this decides
	// whether a chat with no selection uses the active set at all.
	let defaultActive = $derived(resolveExSettings($settings).toolsDefaultActive);

	const saveDefault = async (value: boolean) => {
		const res = await setExSettings(localStorage.token, { toolsDefaultActive: value });
		if (res && 'error' in res) {
			toast.error(`${res.error}`);
			return;
		}
		settings.update((s) => ({ ...s, ui: { ...(s.ui ?? {}), ex: { ...resolveExSettings($settings), toolsDefaultActive: value } } }));
	};

	const toggle = async (slug: string, enabled: boolean) => {
		savingSlug = slug;
		const res = await setExBuiltinEnabled(localStorage.token, slug, enabled);
		if ('error' in res) {
			toast.error(`${res.error}`);
		} else {
			// Patch in place rather than refetching, so the list does not flicker on every switch.
			builtins = builtins.map((b) => (b.slug === slug ? { ...b, ...res } : b));
			toast.success(`${res.name} ${enabled ? 'enabled' : 'disabled'}`);
		}
		savingSlug = null;
	};

	const openEditor = async (slug: string) => {
		editorBusy = true;
		editing = slug;
		const res = await getExBuiltin(localStorage.token, slug);
		if ('error' in res) {
			toast.error(`${res.error}`);
			editing = null;
		} else {
			detail = res;
			draft = res.source_text ?? '';
		}
		editorBusy = false;
	};

	const closeEditor = () => {
		editing = null;
		detail = null;
		draft = '';
	};

	const save = async () => {
		if (!editing) return;
		editorBusy = true;
		const res = await saveExBuiltinOverlay(localStorage.token, editing, draft);
		if ('error' in res) {
			// The backend rejects a syntax error rather than storing it, so this is the path a
			// broken edit takes. Showing the message matters more than a tidy toast.
			toast.error(`${res.error}`);
		} else {
			toast.success('Edit saved. It overrides the shipped tool and survives updates.');
			builtins = builtins.map((b) => (b.slug === res.slug ? { ...b, ...res } : b));
			closeEditor();
		}
		editorBusy = false;
	};

	const revert = async (slug: string) => {
		savingSlug = slug;
		const res = await revertExBuiltinOverlay(localStorage.token, slug);
		if ('error' in res) {
			toast.error(`${res.error}`);
		} else {
			toast.success('Reverted to the shipped tool.');
			builtins = builtins.map((b) => (b.slug === slug ? { ...b, ...res } : b));
			if (editing === slug) closeEditor();
		}
		savingSlug = null;
	};

	const reload = async () => {
		await load();
		dispatch('change');
	};
</script>

<UserSettingSection title="Builtin tools">
	<div class="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-500 dark:text-gray-400">
		{#if loading}
			<span class="flex items-center gap-2"><Spinner /> Loading…</span>
		{:else}
			<span>
				<strong class="text-gray-700 dark:text-gray-200">{enabledCount}</strong>
				of {builtins.length} tools enabled
			</span>
			<span class="text-gray-400 dark:text-gray-600">·</span>
			<span>{totalFunctions} functions exposed</span>
			{#if !canEdit}
				<span class="text-gray-400 dark:text-gray-600">·</span>
				<span>read-only (admin required to change)</span>
			{/if}
		{/if}
	</div>

	<p class="mb-3 text-xs leading-relaxed text-gray-500 dark:text-gray-400">
		These tools ship with the app and need no installation. Each can be switched off, and each
		accepts an edit that overrides it without being lost on update.

	<UserSettingRow
		let:labelId
		label="Apply active tools without a per-chat pick"
		description="A chat that has picked specific tools keeps its pick. This covers chats that have picked none, which is what a shipped default should mean."
	>
		<Switch state={defaultActive} ariaLabelledbyId={labelId} on:change={(e) => saveDefault(e.detail)} />
	</UserSettingRow>
	</p>

	{#if broken.length > 0}
		<div
			class="mb-3 rounded-xl border border-red-200 bg-red-50/60 p-3 text-xs dark:border-red-900/50 dark:bg-red-950/30"
		>
			<div class="mb-1 font-medium text-red-700 dark:text-red-300">
				{broken.length} tool{broken.length === 1 ? '' : 's'} failed to load
			</div>
			{#each broken as b (b.slug)}
				<div class="text-red-600 dark:text-red-400">
					<span class="font-mono">{b.slug}</span>: {b.load_error}
				</div>
			{/each}
			<button
				type="button"
				class="mt-2 text-red-700 underline dark:text-red-300"
				on:click={reload}>Reload</button
			>
		</div>
	{/if}

	{#if stale.length > 0}
		<div
			class="mb-3 rounded-xl border border-amber-200 bg-amber-50/60 p-3 text-xs dark:border-amber-900/50 dark:bg-amber-950/30"
		>
			<div class="mb-1 font-medium text-amber-700 dark:text-amber-300">
				{stale.length} edit{stale.length === 1 ? '' : 's'} based on an older version
			</div>
			<div class="text-amber-700/90 dark:text-amber-400/90">
				The shipped tool has changed since the edit was written, so the edit is hiding the newer
				version. Review it, or revert to the shipped tool.
			</div>
			<div class="mt-1 flex flex-wrap gap-2">
				{#each stale as b (b.slug)}
					<button
						type="button"
						class="underline"
						on:click={() => openEditor(b.slug)}>{b.slug}</button
					>
				{/each}
			</div>
		</div>
	{/if}

	{#if loading}
		<div class="py-4 text-center"><Spinner /></div>
	{:else}
		<div class="space-y-0.5">
			{#each builtins as b (b.slug)}
				<div class="rounded-xl border border-transparent px-1 py-1.5 transition-colors hover:border-gray-100 dark:hover:border-gray-850">
					<UserSettingRow let:labelId label={b.name} description={b.slug}>
						<div class="flex items-center gap-2">
							{#if savingSlug === b.slug}
								<Spinner />
							{/if}
							<Switch
								state={b.enabled}
								ariaLabelledbyId={labelId}
								disabled={!canEdit || savingSlug === b.slug}
								on:change={(e) => toggle(b.slug, e.detail)}
							/>
						</div>
					</UserSettingRow>

					<!-- Function list. Collapsed when short, because leanrea has twenty-one. -->
					<div class="mt-0.5 px-1 text-[0.6875rem] leading-relaxed text-gray-400 dark:text-gray-500">
						{(b.specs ?? []).slice(0, 6).join(', ')}{(b.specs?.length ?? 0) > 6
							? `, +${b.specs.length - 6} more`
							: ''}
					</div>

					<div class="mt-1 flex flex-wrap items-center gap-2 px-1 text-[0.6875rem]">
						{#if b.has_overlay}
							<span
								class="rounded-full bg-blue-100 px-1.5 py-0.5 font-medium text-blue-700 dark:bg-blue-950/60 dark:text-blue-300"
								>edited</span
							>
						{/if}
						{#if b.overlay_stale}
							<span
								class="rounded-full bg-amber-100 px-1.5 py-0.5 font-medium text-amber-700 dark:bg-amber-950/60 dark:text-amber-300"
								>update available</span
							>
						{/if}
						{#if b.load_error}
							<span
								class="rounded-full bg-red-100 px-1.5 py-0.5 font-medium text-red-700 dark:bg-red-950/60 dark:text-red-300"
								>error</span
							>
						{/if}

						{#if canEdit}
							<button
								type="button"
								class="text-gray-500 underline transition-colors hover:text-gray-900 dark:text-gray-500 dark:hover:text-white"
								on:click={() => openEditor(b.slug)}>Edit</button
							>
							{#if b.has_overlay}
								<button
									type="button"
									disabled={savingSlug === b.slug}
									class="text-gray-500 underline transition-colors hover:text-gray-900 dark:text-gray-500 dark:hover:text-white"
									on:click={() => revert(b.slug)}>Revert to shipped</button
								>
							{/if}
						{/if}
					</div>

					{#if editing === b.slug}
						<div class="mt-2 rounded-xl border border-gray-100 p-2 dark:border-gray-850">
							{#if editorBusy && !detail}
								<div class="py-3 text-center"><Spinner /></div>
							{:else if detail}
								<div class="mb-1.5 flex flex-wrap items-center gap-2 text-[0.6875rem] text-gray-500 dark:text-gray-400">
									<span>
										The editor holds the <strong>whole file</strong>. Saving stores it
										separately, so it overrides the shipped tool and is not overwritten on
										update.
									</span>
								</div>

								<textarea
									bind:value={draft}
									spellcheck="false"
									rows="16"
									class="owv-scroll w-full rounded-lg border border-gray-100 bg-gray-50/60 p-2 font-mono text-[0.6875rem] leading-relaxed text-gray-700 outline-hidden focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300"
								></textarea>

								{#if detail.source_origin === 'overlay'}
									<div class="mt-1 text-[0.6875rem] text-blue-600 dark:text-blue-400">
										Showing your edit. The shipped tool is still on disk underneath.
									</div>
								{:else}
									<div class="mt-1 text-[0.6875rem] text-gray-500 dark:text-gray-400">
										Showing the shipped tool. Saving creates your own copy.
									</div>
								{/if}

								<div class="mt-2 flex items-center gap-3">
									<button
										type="button"
										disabled={editorBusy}
										class="rounded-lg bg-gray-900 px-2.5 py-1 text-[0.6875rem] font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50 dark:bg-white dark:text-gray-900"
										on:click={save}>Save edit</button
									>
									<button
										type="button"
										class="text-[0.6875rem] text-gray-500 underline hover:text-gray-900 dark:hover:text-white"
										on:click={closeEditor}>Cancel</button
									>
									{#if editorBusy}
										<Spinner />
									{/if}
								</div>
							{/if}
						</div>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</UserSettingSection>
