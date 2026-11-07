<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { createEventDispatcher } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { settings } from '$lib/stores';
	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import UserSettingRow from './UserSettingRow.svelte';
	import UserSettingSection from './UserSettingSection.svelte';
	import {
		getExStatus,
		getExBackendCheck,
		getExRecovery,
		getExModules,
		getExCluster,
	getExContext,
	getExSteering,
	getExUi,
	getExTrim,
		getExMemory,
		searchExMemory,
		type ExMemoryStatus,
		type ExMemoryHit,
	addExSteering,
		runExBatch,
		type ExBackendCheck,
		type ExBatchSummary,
		type ExStatusResponse
	} from '$lib/apis/ex';
	import { resolveExSettings } from '$lib/utils/exSettings';
	import { exSlots, refreshSlots } from '$lib/stores/exSlots';
	import BuiltinsSection from './BuiltinsSection.svelte';
	import HelpSection from './HelpSection.svelte';

	const i18n: Writable<i18nType> = getContext('i18n');
	const dispatch = createEventDispatcher();

	const { saveSettings }: { saveSettings: (updated: Record<string, any>) => void | Promise<void> } = $props();

	let ex = $state(resolveExSettings($settings));
	let status: ExStatusResponse | null = $state(null);
	let backendCheck: ExBackendCheck | null = $state(null);

	let recoveryReport: Record<string, any> | null = $state(null);
	let modulesReport: Record<string, any> | null = $state(null);
	let clusterReport: Record<string, any> | null = $state(null);
	let contextReport: Record<string, any> | null = $state(null);
	let steeringReport: Record<string, any> | null = $state(null);
	let uiReport: Record<string, any> | null = $state(null);
	let trimReport: Record<string, any> | null = $state(null);
	let steeringText = $state('');
	let pendingSteering = $state(0);
	let batchSummary: ExBatchSummary | null = $state(null);

	let batchAction = $state('move');
	let batchIds = $state('');
	let batchFolder = $state('');
	let batchForce = $state(false);
	let batching = $state(false);

	let loading = $state(true);
	let checkingBackend = $state(false);

	let modelPath = $state('');
	let memoryStatus: ExMemoryStatus | null = $state(null);
	let memoryQuery = $state('');
	let memoryHits: ExMemoryHit[] = $state([]);
	let memorySearching = $state(false);

	// Params the fork can control live under settings.params. Params the launcher wrote
	// into the handshake are reported, not overridden, because the launcher is what
	// starts the server and fixed them at startup.
	const uiParams = (key: string) => {
		const value = ($settings?.params as Record<string, any>)?.[key];
		return value === null || value === undefined ? '' : String(value);
	};

	const refresh = async () => {
		loading = true;
		const res = await getExStatus(localStorage.token);
		if ('error' in res) {
			toast.error(`${res.error}`);
		} else {
			status = res;
			ex = resolveExSettings($settings);
			modelPath = ex.modelPath;
		}
		loading = false;
	};

	const saveEx = async (patch: Record<string, any>) => {
		try {
			await saveSettings({ ex: { ...resolveExSettings($settings), ...patch } });
			dispatch('save');
		} catch {
			// The modal reports the save error; do not claim success here.
		}
		await refresh();
	};

	const checkBackend = async () => {
		checkingBackend = true;
		const res = await getExBackendCheck(localStorage.token, modelPath);
		if ('error' in res) {
			backendCheck = null;
			toast.error(`${res.error}`);
		} else {
			backendCheck = res;
		}
		checkingBackend = false;
	};

	const refreshMemoryStatus = async () => {
		try {
			const res = await getExMemory(localStorage.token, currentChatId());
			memoryStatus = 'error' in res ? null : res;
		} catch {
			memoryStatus = null;
		}
	};

	const currentChatId = (): string => {
		// The tab lives in the settings modal, which is opened from a chat. The chat id is not
		// passed down, so it is read from the route. Without this, indexing would be reported
		// for an empty chat id and the count would always read zero.
		try {
			const match = window.location.pathname.match(/\/c\/([^/?#]+)/);
			return match ? match[1] : '';
		} catch {
			return '';
		}
	};

	const runForgetMemory = async () => {
		const chatId = currentChatId();
		if (!chatId) return;
		try {
			const { forgetExMemory } = await import('$lib/apis/ex');
			await forgetExMemory(localStorage.token, chatId);
			memoryHits = [];
			await refreshMemoryStatus();
		} catch (e) {
			console.error('forget memory failed', e);
		}
	};

	const runMemorySearch = async () => {
		const query = memoryQuery.trim();
		if (!query) return;
		memorySearching = true;
		try {
			// Scope to this chat by default. Clearing the chat id searches every indexed chat,
			// which is the only way back to a conversation that no longer exists.
			const res = await searchExMemory(localStorage.token, query, currentChatId(), ex.ragTopK || 6);
			memoryHits = 'error' in res ? [] : res.hits;
			if ('error' in res) toast.error(`${res.error}`);
		} catch (e) {
			memoryHits = [];
			toast.error(`${e}`);
		}
		memorySearching = false;
	};

	const refreshRecovery = async () => {
		const res = await getExRecovery(localStorage.token);
		recoveryReport = 'error' in res ? null : res;
	};

	const refreshModules = async () => {
		const res = await getExModules(localStorage.token);
		modulesReport = 'error' in res ? null : res;
	};

	const refreshCluster = async () => {
		const res = await getExCluster(localStorage.token);
		clusterReport = 'error' in res ? null : res;
	};

	const refreshContext = async () => {
		const res = await getExContext(localStorage.token);
		contextReport = 'error' in res ? null : res;
	};

	const refreshSteering = async () => {
		const res = await getExSteering(localStorage.token);
		if ('error' in res) return;
		steeringReport = res;
		pendingSteering = (res.items ?? []).filter((i) => !i.applied).length;
	};

	const sendSteering = async () => {
		if (!steeringText.trim()) return;
		const res = await addExSteering(localStorage.token, { text: steeringText, mode: ex.steeringMode });
		if ('error' in res) {
			toast.error(`${res.error}`);
			return;
		}
		steeringText = '';
		await refreshSteering();
	};

	const refreshUi = async () => {
		const res = await getExUi(localStorage.token);
		uiReport = 'error' in res ? null : res;
		if (uiReport) exSlots.set(uiReport.resolved.slots);
	};

	const refreshTrim = async () => {
		const res = await getExTrim(localStorage.token);
		trimReport = 'error' in res ? null : res;
	};

	const runBatch = async (dryRun: boolean) => {
		const ids = batchIds
			.split(',')
			.map((id) => id.trim())
			.filter((id) => id);
		if (!ids.length) {
			toast.error($i18n.t('settings.personal.vibes.batch.ids.description'));
			return;
		}
		batching = true;
		const res = await runExBatch(localStorage.token, {
			action: batchAction,
			chatIds: ids,
			folderId: batchFolder,
			dryRun,
			force: !dryRun && batchForce
		});
		batchSummary = 'error' in res ? null : res;
		if ('error' in res) toast.error(`${res.error}`);
		batching = false;
	};

	onMount(() => {
		// probe() belongs to SetupTab and the theme report belongs to ThemesTab, so neither exists in
		// this component any more. They were left here after the tab split, and that is what broke the
		// tab: onMount threw on the first undefined call, every refresh below it never ran, and the
		// loading gate never released, so the tab showed a spinner forever and nothing could be saved.
		refresh();
		refreshRecovery();
		refreshModules();
		refreshCluster();
		refreshContext();
		refreshSteering();
		refreshUi();
		refreshTrim();
		refreshMemoryStatus();
		refreshSlots(localStorage.token);
	});
</script>

<!-- Scrolling this tab needs three things, and the previous attempt only had the first.

     1. A bounded height. The wrapper above is overflow-hidden, so without a resolved height
        overflow-y-auto degrades to overflow-visible and the content is clipped with no bar.
        The parent chain now resolves: modal panel (fixed height, flex col) -> content pane
        (min-h-0 flex-1) -> ExSlotHost (h-full min-h-0 flex-col) -> this div.

     2. min-h-0 on each flex item in that chain, because a flex item defaults to
        min-height:auto and refuses to shrink below its content.

     3. The tab must NOT lay its own content out as a flex column. This was the actual bug:
        `flex flex-col` on this container makes every child a flex item, and flex items shrink
        by default (flex-shrink: 1). So each section compressed to fit the box instead of
        overflowing it, the container's scrollHeight never exceeded its clientHeight, and
        there was nothing to scroll even though the last rows were unreadable and unreachable.
        Block layout lets the children keep their natural height and overflow properly.

     A plain block that scrolls is the fix, not more classes on a flex column. -->
<div id="tab-openwebvibes" class="block h-full min-h-0 overflow-y-auto owv-scroll text-sm">
	<h2 class="text-sm font-medium text-gray-900 dark:text-white">
		{$i18n.t('settings.personal.vibes.title')}
	</h2>
	<p class="mb-3 text-[0.6875rem] text-gray-500 dark:text-gray-400">
		{$i18n.t('settings.personal.vibes.forkExistsBecause.description')}
	</p>

	{#if loading}
		<div class="flex items-center gap-2 text-xs text-gray-500">
			<Spinner />
			{$i18n.t('Loading')}
		</div>
	{:else}
		<UserSettingSection title={$i18n.t('settings.personal.vibes.sections.runtime.title')} first>
			<div class="text-xs">
				{#if status?.launcher?.present}
					<p class="mb-1 text-gray-700 dark:text-gray-300">
						{$i18n.t('settings.personal.vibes.runtime.handshakeFrom.label')}:
						<span class="font-medium">{status.launcher.backend ?? 'unknown'}</span>
						<span class="text-gray-500">/ {status.launcher.model ?? 'unknown model'}</span>
					</p>
					{#if status.launcher.stale}
						<p class="mb-1 text-amber-600 dark:text-amber-400">
							{$i18n.t('settings.personal.vibes.runtime.stale.description')}
						</p>
					{/if}
					{#if Object.keys(status.launcher.locked).length === 0}
						<p class="text-gray-500 dark:text-gray-400">
							{$i18n.t('settings.personal.vibes.runtime.nothingLocked.description')}
						</p>
					{:else}
						<ul class="flex flex-col gap-1">
							{#each Object.entries(status.launcher.locked) as [param, value] (param)}
								<li class="flex items-center justify-between gap-2 text-xs">
									<span class="text-gray-600 dark:text-gray-400">{param}</span>
									<span class="font-mono text-gray-900 dark:text-white">{String(value)}</span>
								</li>
							{/each}
						</ul>
					{/if}
				{:else}
					<p class="text-gray-500 dark:text-gray-400">
						{$i18n.t('settings.personal.vibes.runtime.noHandshake.description')}
					</p>
				{/if}
			</div>

			{#if status?.launcher?.present && Object.keys(status.launcher.locked).length > 0}
				{#each ['temperature', 'top_p', 'top_k', 'ctx_size'] as param (param)}
					{#if status.launcher.locked[param] !== undefined}
						<UserSettingRow
							label={param}
							description={`${$i18n.t('settings.personal.vibes.runtime.lockedByLauncher.description')} ${$i18n.t('settings.personal.vibes.runtime.uiValue.label')}: ${uiParams(param) || 'not set'}`}
						>
							<span class="font-mono text-xs text-gray-500 dark:text-gray-400">
								{String(status.launcher.locked[param])}
							</span>
						</UserSettingRow>
					{/if}
				{/each}
			{/if}
		</UserSettingSection>

		<UserSettingSection title={$i18n.t('settings.personal.vibes.sections.behavior.title')}>
			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.enabled.label')}
				description={$i18n.t('settings.personal.vibes.enabled.description')}
			>
				<Switch
					state={ex.enabled}
					ariaLabelledbyId={labelId}
					on:change={(e) => {
						saveEx({ enabled: e.detail });
					}}
				/>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.journalWindow.label')}
				description={$i18n.t('settings.personal.vibes.journalWindow.description')}
			>
				<input
					type="number"
					min="0"
					step="1"
					id={`${labelId}-control`}
					value={ex.journalWindow}
					aria-label={$i18n.t('settings.personal.vibes.journalWindow.label')}
					class="h-7 w-16 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-right text-xs text-gray-700 outline-hidden transition-colors focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:focus:border-blue-500"
					on:change={(e) => {
						saveEx({ journalWindow: parseInt(e.currentTarget.value, 10) || 0 });
					}}
				/>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.toolOutputWindow.label')}
				description={$i18n.t('settings.personal.vibes.toolOutputWindow.description')}
			>
				<input
					type="number"
					min="0"
					step="1"
					id={`${labelId}-control`}
					value={ex.toolOutputWindow}
					aria-label={$i18n.t('settings.personal.vibes.toolOutputWindow.label')}
					class="h-7 w-16 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-right text-xs text-gray-700 outline-hidden transition-colors focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:focus:border-blue-500"
					on:change={(e) => {
						saveEx({ toolOutputWindow: parseInt(e.currentTarget.value, 10) || 0 });
					}}
				/>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.loopGuard.label')}
				description={$i18n.t('settings.personal.vibes.loopGuard.description')}
			>
				<Switch
					state={ex.loopGuard}
					ariaLabelledbyId={labelId}
					on:change={(e) => {
						saveEx({ loopGuard: e.detail });
					}}
				/>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.liveStats.label')}
				description={$i18n.t('settings.personal.vibes.liveStats.description')}
			>
				<Switch
					state={ex.liveStats}
					ariaLabelledbyId={labelId}
					on:change={(e) => {
						saveEx({ liveStats: e.detail });
					}}
				/>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.tokenSource.label')}
				description={$i18n.t('settings.personal.vibes.tokenSource.description')}
			>
				<div class="flex gap-1">
					{#each ['engine', 'live', 'both'] as option}
						<button
							class="rounded-lg px-2 py-1 text-xs cursor-pointer {ex.statsTokenSource === option
								? 'bg-gray-100 text-gray-900 dark:bg-gray-800 dark:text-gray-100'
								: 'text-gray-600 dark:text-gray-400 hover:bg-gray-50/40 dark:hover:bg-gray-800/40'}"
							on:click={() => saveEx({ statsTokenSource: option })}
						>
							{option}
						</button>
					{/each}
				</div>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.verbatimSummary.label')}
				description={$i18n.t('settings.personal.vibes.verbatimSummary.description')}
			>
				<Switch
					state={ex.verbatimSummary}
					ariaLabelledbyId={labelId}
					on:change={(e) => {
						saveEx({ verbatimSummary: e.detail });
					}}
				/>
			</UserSettingRow>
		</UserSettingSection>

		<UserSettingSection title="Context window">
			<UserSettingRow
				let:labelId
				label="Window (tokens)"
				description="262144 is what the engine actually serves. Lower it only if the engine was lowered."
			>
				<input
					type="number"
					min="4096"
					step="1024"
					id={`${labelId}-control`}
					value={ex.contextWindow}
					aria-label="Window tokens"
					class="h-7 w-24 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-right text-xs text-gray-700 outline-hidden transition-colors focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:focus:border-blue-500"
					on:change={(e) => {
						saveEx({ contextWindow: parseInt(e.currentTarget.value, 10) || 262144 });
					}}
				/>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label="Reply reserve"
				description="Tokens held back from the window so the answer itself never truncates."
			>
				<input
					type="number"
					min="1024"
					step="1024"
					id={`${labelId}-control`}
					value={ex.replyReserve}
					aria-label="Reply reserve tokens"
					class="h-7 w-24 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-right text-xs text-gray-700 outline-hidden transition-colors focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:focus:border-blue-500"
					on:change={(e) => {
						saveEx({ replyReserve: parseInt(e.currentTarget.value, 10) || 16384 });
					}}
				/>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label="Trim stale tool output"
				description="Shrink old tool results before touching conversation text. This is what keeps an agent run going instead of summarizing it."
			>
				<Switch
					state={ex.trimToolOutput}
					ariaLabelledbyId={labelId}
					on:change={(e) => {
						saveEx({ trimToolOutput: e.detail });
					}}
				/>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label="Tool results kept intact"
				description="How many of the newest tool results stay in full, counted from the end."
			>
				<input
					type="number"
					min="0"
					step="1"
					id={`${labelId}-control`}
					value={ex.keepToolTurns}
					aria-label="Tool results kept"
					class="h-7 w-16 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-right text-xs text-gray-700 outline-hidden transition-colors focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:focus:border-blue-500"
					on:change={(e) => {
						saveEx({ keepToolTurns: parseInt(e.currentTarget.value, 10) || 0 });
					}}
				/>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label="Inject context at the tail"
				description="Keeps the prompt prefix byte identical so the engine reuses its KV cache. Off means a full re-prefill every turn."
			>
				<Switch
					state={ex.tailInject}
					ariaLabelledbyId={labelId}
					on:change={(e) => {
						saveEx({ tailInject: e.detail });
					}}
				/>
			</UserSettingRow>
		</UserSettingSection>

		<UserSettingSection title="Memory (RAG)">
			<UserSettingRow
				let:labelId
				label="Per-chat memory"
				description="Index this chat locally and pull relevant passages back each turn. Survives deleting the chat."
			>
				<Switch
					state={ex.ragMemory}
					ariaLabelledbyId={labelId}
					on:change={(e) => {
						saveEx({ ragMemory: e.detail });
					}}
				/>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label="Passages per turn"
				description="How many memories the RAG slider sends to the model."
			>
				<input
					type="range"
					min="0"
					max="20"
					step="1"
					id={`${labelId}-control`}
					value={ex.ragTopK}
					aria-label="Passages per turn"
					class="w-40 accent-blue-500"
					on:input={(e) => {
						saveEx({ ragTopK: parseInt(e.currentTarget.value, 10) || 0 });
					}}
				/>
				<span class="ml-2 text-xs text-gray-500 dark:text-gray-400">{ex.ragTopK}</span>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label="Relevance floor"
				description="Cosine score below which a hit is treated as noise."
			>
				<input
					type="number"
					min="0"
					max="1"
					step="0.05"
					id={`${labelId}-control`}
					value={ex.ragMinScore}
					aria-label="Relevance floor"
					class="h-7 w-20 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-right text-xs text-gray-700 outline-hidden transition-colors focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:focus:border-blue-500"
					on:change={(e) => {
						saveEx({ ragMinScore: parseFloat(e.currentTarget.value) || 0 });
					}}
				/>
			</UserSettingRow>

			<div class="px-1 py-2 text-xs text-gray-500 dark:text-gray-400">
				{memoryStatus
					? `${memoryStatus.chat_count} passages stored for this chat, ${memoryStatus.dimensions} dims, ${memoryStatus.embed_model}`
					: 'memory store not reporting'}
			</div>

			<div class="flex items-center gap-2 px-1 py-1">
				<input
					type="text"
					bind:value={memoryQuery}
					placeholder="Search memory (empty chat = search everything)"
					class="h-8 flex-1 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden transition-colors focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:focus:border-blue-500"
					on:keydown={(e) => {
						if (e.key === 'Enter') runMemorySearch();
					}}
				/>
				<button
					class="rounded-lg bg-gray-100 px-3 py-1.5 text-xs font-medium text-gray-700 transition hover:bg-gray-200 disabled:opacity-50 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
					disabled={memorySearching}
					on:click={runMemorySearch}
				>
					{memorySearching ? 'Searching' : 'Search'}
				</button>
			</div>

			{#if memoryHits.length}
				<div class="flex flex-col gap-1 px-1 py-1">
					{#each memoryHits as hit, i (i)}
						<div class="rounded-lg bg-gray-50 px-2 py-1.5 text-xs dark:bg-gray-850">
							<span class="mr-2 font-mono text-[10px] text-gray-400">{hit.score.toFixed(3)}</span>
							<span class="text-gray-700 dark:text-gray-300">{hit.text.slice(0, 220)}</span>
						</div>
					{/each}
				</div>
			{/if}

			<div class="flex gap-2 px-1 py-2">
				<button
					class="rounded-lg bg-gray-100 px-3 py-1.5 text-xs font-medium text-gray-700 transition hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
					on:click={refreshMemoryStatus}
				>
					Refresh
				</button>
				<button
					class="rounded-lg bg-red-50 px-3 py-1.5 text-xs font-medium text-red-600 transition hover:bg-red-100 dark:bg-red-900/20 dark:text-red-400 dark:hover:bg-red-900/30"
					on:click={runForgetMemory}
				>
					Forget this chat
				</button>
			</div>
		</UserSettingSection>

		<UserSettingSection title={$i18n.t('settings.personal.vibes.sections.backend.title')}>
			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.backend.label')}
				description={$i18n.t('settings.personal.vibes.backend.description')}
			>
				<select
					id={`${labelId}-control`}
					bind:value={ex.backend}
					class="h-7 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300"
					on:change={() => {
						saveEx({ backend: ex.backend });
					}}
				>
					<option value="auto">auto</option>
					<option value="llamacpp">llama.cpp</option>
					<option value="strata">Strata</option>
				</select>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.modelPath.label')}
				description={$i18n.t('settings.personal.vibes.modelPath.description')}
			>
				<input
					type="text"
					id={`${labelId}-control`}
					bind:value={modelPath}
					placeholder="/path/to/model.gguf"
					aria-label={$i18n.t('settings.personal.vibes.modelPath.label')}
					class="h-7 w-48 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300"
				/>
			</UserSettingRow>

			<button
				class="text-xs text-gray-500 transition-colors hover:text-gray-900 dark:text-gray-500 dark:hover:text-white"
				type="button"
				on:click={checkBackend}
			>
				{$i18n.t('settings.personal.vibes.checkModel.label')}
			</button>

			{#if checkingBackend}
				<div class="flex items-center gap-2 text-xs text-gray-500">
					<Spinner />
					{$i18n.t('Loading')}
				</div>
			{:else if backendCheck}
				<div class="text-xs text-gray-700 dark:text-gray-300">
					<p class="font-mono">{backendCheck.model.path}</p>
					<p class="text-gray-500 dark:text-gray-400">
						{backendCheck.model.format}
						{backendCheck.model.prepared ? ' - prepared pack' : ''}
					</p>
					<p class="mt-1">
						{#if backendCheck.selected}
							<span class="font-medium">{backendCheck.selected}</span>
							<span class="text-gray-500"> - {backendCheck.reason}</span>
						{:else}
							<span class="text-red-600 dark:text-red-400">refused: {backendCheck.reason}</span>
						{/if}
					</p>
					{#if backendCheck.candidates.length}
						<p class="text-gray-500 dark:text-gray-400">
							candidates: {backendCheck.candidates.join(', ')}
						</p>
					{/if}
				</div>
			{/if}
		</UserSettingSection>


		<UserSettingSection title={$i18n.t('settings.personal.vibes.sections.agentic.title')}>
			<p class="mb-2 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('settings.personal.vibes.agentic.autoRetry.description')}
			</p>

			{#each ['autoRetry', 'resumeOnCrash', 'exportFallback'] as key (key)}
				<UserSettingRow
					let:labelId
					label={$i18n.t(`settings.personal.vibes.agentic.${key}.label`)}
					description={$i18n.t(`settings.personal.vibes.agentic.${key}.description`)}
				>
					<Switch
						state={ex[key]}
						ariaLabelledbyId={labelId}
						on:change={(e) => saveEx({ [key]: e.detail })}
					/>
				</UserSettingRow>
			{/each}

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.agentic.maxRetries.label')}
				description={$i18n.t('settings.personal.vibes.agentic.backoff.description')}
			>
				<input
					type="range"
					id={`${labelId}-control`}
					min="0"
					max="12"
					step="1"
					value={ex.maxRetries}
					aria-label={$i18n.t('settings.personal.vibes.agentic.maxRetries.label')}
					on:change={(e) => saveEx({ maxRetries: Number(e.target.value) })}
					class="w-48 accent-blue-600"
				/>
				<span class="text-xs font-mono text-gray-500">{ex.maxRetries}</span>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.agentic.shapeRetries.label')}
				description={$i18n.t('settings.personal.vibes.agentic.shapeRetries.description')}
			>
				<input
					type="range"
					id={`${labelId}-control`}
					min="0"
					max="12"
					step="1"
					value={ex.shapeRetries}
					aria-label={$i18n.t('settings.personal.vibes.agentic.shapeRetries.label')}
					on:change={(e) => saveEx({ shapeRetries: Number(e.target.value) })}
					class="w-48 accent-blue-600"
				/>
				<span class="text-xs font-mono text-gray-500">{ex.shapeRetries}</span>
			</UserSettingRow>

			{#if recoveryReport}
				<div class="mt-2 text-xs">
					<p class="text-gray-500 dark:text-gray-400">
						{$i18n.t('settings.personal.vibes.agentic.checkpoint.label')}: {recoveryReport.checkpoint?.has_checkpoint ? 'present' : 'none'}
					</p>
					{#if recoveryReport.checkpoint?.has_checkpoint}
						<p class="mt-1 font-mono text-gray-600 dark:text-gray-300">
							cursor {recoveryReport.checkpoint.cursor}, done {recoveryReport.checkpoint.done.length}, pending {recoveryReport.checkpoint.pending.length}
						</p>
					{/if}
				</div>
			{/if}
		</UserSettingSection>

		<UserSettingSection title={$i18n.t('settings.personal.vibes.sections.batch.title')}>
			<p class="mb-2 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('settings.personal.vibes.batch.ownership.description')}
			</p>

			<UserSettingRow let:labelId label={$i18n.t('settings.personal.vibes.batch.action.label')}>
				<select
					id={`${labelId}-control`}
					bind:value={batchAction}
					aria-label={$i18n.t('settings.personal.vibes.batch.action.label')}
					class="h-7 w-32 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300"
				>
					<option value="move">move</option>
					<option value="download">download</option>
					<option value="delete">delete</option>
				</select>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.batch.ids.label')}
				description={$i18n.t('settings.personal.vibes.batch.ids.description')}
			>
				<input
					type="text"
					id={`${labelId}-control`}
					bind:value={batchIds}
					placeholder="id1,id2,id3"
					aria-label={$i18n.t('settings.personal.vibes.batch.ids.label')}
					class="h-7 w-48 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs font-mono text-gray-700 outline-hidden focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300"
				/>
			</UserSettingRow>

			<UserSettingRow let:labelId label={$i18n.t('settings.personal.vibes.batch.folder.label')}>
				<input
					type="text"
					id={`${labelId}-control`}
					bind:value={batchFolder}
					aria-label={$i18n.t('settings.personal.vibes.batch.folder.label')}
					class="h-7 w-48 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs font-mono text-gray-700 outline-hidden focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300"
				/>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.import.force.label')}
				description={$i18n.t('settings.personal.vibes.import.force.description')}
			>
				<Switch
					state={batchForce}
					ariaLabelledbyId={labelId}
					on:change={(e) => {
						batchForce = e.detail;
					}}
				/>
			</UserSettingRow>

			<div class="flex items-center gap-3">
				<button
					class="text-xs text-gray-500 transition-colors hover:text-gray-900 dark:text-gray-500 dark:hover:text-white"
					type="button"
					on:click={() => runBatch(true)}
				>
					{$i18n.t('settings.personal.vibes.import.dryRun.label')}
				</button>
				<button
					class="text-xs text-gray-500 transition-colors hover:text-gray-900 dark:text-gray-500 dark:hover:text-white"
					type="button"
					on:click={() => runBatch(false)}
				>
					{$i18n.t('settings.personal.vibes.batch.run.label')}
				</button>
			</div>

			{#if batching}
				<div class="flex items-center gap-2 text-xs text-gray-500">
					<Spinner />
					{$i18n.t('Loading')}
				</div>
			{:else if batchSummary}
				<div class="mt-1 text-xs text-gray-700 dark:text-gray-300">
					<p class="text-gray-500 dark:text-gray-400">
						{$i18n.t('settings.personal.vibes.batch.summary.description')}
					</p>
					<p class="font-mono">
						selected {batchSummary.selected}, would apply {batchSummary.would_apply}{batchSummary.applied ? `, applied ${batchSummary.applied.length}` : ''}
					</p>
					{#if batchSummary.refused.length}
						<p class="mt-1 text-red-600 dark:text-red-400">refused: {batchSummary.refused.join(', ')}</p>
					{/if}
					<p class="mt-1 text-gray-500 dark:text-gray-400">{batchSummary.reason}</p>
				</div>
			{/if}
		</UserSettingSection>

		<UserSettingSection title={$i18n.t('settings.personal.vibes.sections.modules.title')}>
			<p class="mb-2 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('settings.personal.vibes.modules.description')}
			</p>
			<p class="mb-2 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('settings.personal.vibes.modules.wired.description')}
			</p>

			{#if modulesReport}
				<ul class="flex flex-col gap-1 text-xs font-mono">
					{#each modulesReport.modules as mod (mod.name)}
						<li class="text-gray-600 dark:text-gray-300">
							{mod.name}{mod.core ? ' (core)' : ''}: {mod.state} {mod.call_site.length ? `via ${mod.call_site.join(', ')}` : ''}
						</li>
					{/each}
				</ul>
				{#if (modulesReport.dependency_refusals ?? []).length}
					<p class="mt-1 text-red-600 dark:text-red-400">
						refused: {(modulesReport.dependency_refusals ?? []).map((r) => `${r.module} needs ${r.missing.join(', ')}`).join('; ')}
					</p>
				{/if}
				{#if (modulesReport.plugins ?? []).length}
					<p class="mt-1 text-gray-500 dark:text-gray-400">
						plugins: {modulesReport.plugins.map((p) => p.name).join(', ')}
					</p>
				{/if}
			{/if}
		</UserSettingSection>

		<UserSettingSection title={$i18n.t('settings.personal.vibes.sections.cluster.title')}>
			<p class="mb-2 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('settings.personal.vibes.cluster.description')}
			</p>
			<p class="mb-2 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('settings.personal.vibes.cluster.lanOnly.description')}
			</p>
			<p class="mb-2 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('settings.personal.vibes.cluster.plannedVsObserved.description')}
			</p>

			{#if clusterReport}
				<div class="text-xs">
					<p class="font-mono text-gray-600 dark:text-gray-300">
						{clusterReport.status?.status ?? 'no plan'}{clusterReport.status?.reason ? `: ${clusterReport.status.reason}` : ''}
					</p>
					<ul class="flex flex-col gap-1 mt-1 font-mono">
						{#each (clusterReport.status?.workers ?? []) as w (w.name)}
							<li class="text-gray-600 dark:text-gray-300">
								{w.name} ({w.transport ?? 'unknown'}): reported {w.reported_mb ?? 'n/a'} MB, usable {w.usable_mb ?? 'n/a'} MB, assigned {w.assigned_mb ?? 'n/a'} MB{w.estimated ? ' (speed estimated)' : ''}
							</li>
						{/each}
				</ul>
				{#if (clusterReport.status?.unresolved ?? []).length}
					<p class="mt-1 text-gray-500 dark:text-gray-400">
						unresolved: {(clusterReport.status?.unresolved ?? []).map((u) => `${u.name} (${u.reason})`).join('; ')}
					</p>
				{/if}
			</div>
		{/if}
		</UserSettingSection>
		<UserSettingSection title={$i18n.t('settings.personal.vibes.sections.context.title')}>
			<p class="mb-2 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('settings.personal.vibes.context.previousContext.description')}
			</p>
			<p class="mb-2 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('settings.personal.vibes.context.format.description')}
			</p>
			<p class="mb-2 text-[0.6875rem] text-amber-600 dark:text-amber-400">
				{$i18n.t('settings.personal.vibes.context.cache.warning')}
			</p>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.context.previousContext.label')}
			>
				<Switch
					state={ex.previousContext}
					ariaLabelledbyId={labelId}
					on:change={(e) => saveEx({ previousContext: e.detail })}
				/>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.context.chars.label')}
			>
				<input
					type="range"
					id={`${labelId}-control`}
					min="0"
					max="60000"
					step="1000"
					value={ex.previousContextChars}
					aria-label={$i18n.t('settings.personal.vibes.context.chars.label')}
					on:change={(e) => saveEx({ previousContextChars: Number(e.target.value) })}
					class="w-48 accent-blue-600"
				/>
				<span class="text-xs font-mono text-gray-500">{ex.previousContextChars}</span>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.context.deliveredAs.label')}
				description={$i18n.t('settings.personal.vibes.context.deliveredAs.description')}
			>
				<select
					id={`${labelId}-control`}
					value={ex.previousContextAsSystem ? 'system' : 'user'}
					on:change={(e) => saveEx({ previousContextAsSystem: e.target.value === 'system' })}
					class="text-xs"
				>
					<option value="system">system</option>
					<option value="user">user</option>
				</select>
			</UserSettingRow>

			{#if contextReport}
				<p class="mt-2 text-xs font-mono text-gray-600 dark:text-gray-300">
					{$i18n.t('settings.personal.vibes.context.stored.label')}: {contextReport.stored_path}
				</p>
			{/if}
		</UserSettingSection>

		<UserSettingSection title="Memory">
			<div class="flex flex-col gap-2 py-2">
				<p class="text-xs text-gray-500">
					Keeps this chat's original text in a local vector index and puts the most relevant
					passages back on the next turn. This is what survives a compaction: the digest
					shrinks the transcript, this keeps a copy of it on disk.
				</p>

				<div class="flex flex-wrap items-center gap-3 text-xs">
					{#if memoryStatus === null}
						<span class="text-gray-400">checking store...</span>
					{:else if memoryStatus.available}
						<span class="font-medium text-green-600 dark:text-green-400">store ready</span>
						<span class="text-gray-500">
							{memoryStatus.chat_count ?? 0} passages in this chat
						</span>
					{:else}
						<span class="font-medium text-amber-600 dark:text-amber-400">retrieval unavailable</span>
						<span class="text-gray-500">{memoryStatus.reason ?? 'unknown reason'}</span>
					{/if}
					<button
						type="button"
						class="rounded-lg px-2 py-1 text-xs text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-white/5"
						on:click={refreshMemoryStatus}>refresh</button
					>
				</div>

				<UserSettingRow
					let:labelId
					label="Retrieve from this chat"
					description="Index the chat and offer the closest passages each turn. Off means no indexing and no retrieval."
				>
					<Switch
						id={`${labelId}-control`}
						state={ex.ragMemory}
						on:change={(e) => saveEx({ ragMemory: e.detail })}
					/>
				</UserSettingRow>

				<UserSettingRow
					let:labelId
					label="Passages per turn"
					description="How many chunks are offered to the model. More is not better; it competes with the live conversation."
				>
					<input
						type="number"
						min="1"
						max="20"
						step="1"
						id={`${labelId}-control`}
						value={ex.ragTopK}
						class="h-7 w-16 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-right text-xs text-gray-700 outline-hidden dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300"
						on:change={(e) => saveEx({ ragTopK: parseInt(e.currentTarget.value, 10) || 6 })}
					/>
				</UserSettingRow>

				<UserSettingRow
					let:labelId
					label="Character budget"
					description="Cap on the whole retrieved block, so retrieval cannot quietly eat the context window."
				>
					<input
						type="number"
						min="500"
						step="500"
						id={`${labelId}-control`}
						value={ex.ragMaxChars}
						class="h-7 w-20 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-right text-xs text-gray-700 outline-hidden dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300"
						on:change={(e) => saveEx({ ragMaxChars: parseInt(e.currentTarget.value, 10) || 4000 })}
					/>
				</UserSettingRow>

				<div class="mt-1 flex flex-col gap-2 border-t border-gray-100 pt-3 dark:border-white/5">
					<p class="text-xs text-gray-500">
						Search this chat, or every indexed chat. The cross-chat search is the
						recovery path: if a chat was wiped, its passages are still on disk.
					</p>
					<div class="flex items-center gap-2">
						<input
							type="text"
							bind:value={memoryQuery}
							placeholder="what did we decide about..."
							class="flex-1 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 py-1 text-xs text-gray-700 outline-hidden dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300"
							on:keydown={(e) => e.key === 'Enter' && runMemorySearch()}
						/>
						<button
							type="button"
							class="rounded-lg bg-gray-100 px-3 py-1 text-xs font-medium text-gray-700 hover:bg-gray-200 disabled:opacity-50 dark:bg-white/10 dark:text-gray-200"
							disabled={memorySearching || !memoryQuery.trim()}
							on:click={runMemorySearch}
							>{memorySearching ? 'searching...' : 'search'}</button
						>
					</div>
					{#if memoryHits.length > 0}
						<div class="flex flex-col gap-1">
							{#each memoryHits as hit, i (i)}
								<div class="rounded-lg border border-gray-100/60 px-2 py-1 text-xs dark:border-white/5">
									<span class="mr-2 font-mono text-[10px] text-gray-400">{hit.score.toFixed(3)}</span>
									<span class="text-gray-600 dark:text-gray-300">{hit.text}</span>
								</div>
							{/each}
						</div>
					{/if}
				</div>
			</div>
		</UserSettingSection>

		<UserSettingSection title={$i18n.t('settings.personal.vibes.sections.steering.title')}>
			<p class="mb-2 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('settings.personal.vibes.steering.queue.description')}
			</p>
			<p class="mb-2 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('settings.personal.vibes.steering.stock_queue.description')}
			</p>
			<p class="mb-2 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('settings.personal.vibes.steering.actions.description')}
			</p>

			<UserSettingRow let:labelId label={$i18n.t('settings.personal.vibes.steering.queue.label')}>
				<Switch
					state={ex.steeringQueue}
					ariaLabelledbyId={labelId}
					on:change={(e) => saveEx({ steeringQueue: e.detail })}
				/>
			</UserSettingRow>

			<UserSettingRow let:labelId label={$i18n.t('settings.personal.vibes.steering.boundary.label')}>
				<Switch
					state={ex.drainSteeringAtBoundary}
					ariaLabelledbyId={labelId}
					on:change={(e) => saveEx({ drainSteeringAtBoundary: e.detail })}
				/>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.steering.mode.label')}
				description={$i18n.t('settings.personal.vibes.steering.mode.description')}
			>
				<select
					id={`${labelId}-control`}
					value={ex.steeringMode}
					on:change={(e) => saveEx({ steeringMode: e.target.value })}
					class="text-xs"
				>
					<option value="next_iteration">next iteration</option>
					<option value="next_turn">next turn</option>
					<option value="stop_turn">stop turn</option>
				</select>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.steering.send.label')}
				description={$i18n.t('settings.personal.vibes.steering.send.description')}
			>
				<input
					id={`${labelId}-control`}
					type="text"
					bind:value={steeringText}
					placeholder="redirect the current turn"
					class="w-full text-xs"
				/>
			</UserSettingRow>

			{#if steeringReport}
				<div class="mt-2 text-xs">
					<p class="text-gray-500 dark:text-gray-400">
						{$i18n.t('settings.personal.vibes.steering.pending.label')}: {pendingSteering}
					</p>
					<ul class="flex flex-col gap-1 mt-1 font-mono">
						{#each (steeringReport.items ?? []) as item (item.ref)}
							<li class="text-gray-600 dark:text-gray-300">
								{item.ref} [{item.action}] {item.applied ? 'applied' : 'pending'}: {item.text}
							</li>
						{/each}
					</ul>
					<p class="mt-1 text-[0.6875rem] text-gray-500 dark:text-gray-400">
						{$i18n.t('settings.personal.vibes.steering.applied.description')}
					</p>
				</div>
			{/if}
		</UserSettingSection>

		<UserSettingSection title={$i18n.t('settings.personal.vibes.sections.ui.title')}>
			<p class="mb-2 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('settings.personal.vibes.ui.plugins.description')}
			</p>
			<p class="mb-2 text-[0.6875rem] text-amber-600 dark:text-amber-400">
				{$i18n.t('settings.personal.vibes.ui.host.description')}
			</p>
			<p class="mb-2 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('settings.personal.vibes.ui.core.description')}
			</p>

			<UserSettingRow let:labelId label={$i18n.t('settings.personal.vibes.ui.plugins.label')}>
				<Switch
					state={ex.uiPlugins}
					ariaLabelledbyId={labelId}
					on:change={(e) => saveEx({ uiPlugins: e.detail })}
				/>
			</UserSettingRow>

			{#if uiReport}
				<div class="mt-2 text-xs">
					<p class="text-gray-500 dark:text-gray-400">{$i18n.t('settings.personal.vibes.ui.slots.label')}</p>
					<ul class="flex flex-col gap-1 mt-1 font-mono">
						{#each (uiReport.resolved.slots ?? []) as s (s.slot)}
							<li class="text-gray-600 dark:text-gray-300">
								{s.slot}: {s.state}{s.plugin ? ` (${s.plugin} -> ${s.component})` : ''} fallback {s.fallback}
							</li>
						{/each}
					</ul>
					{#if (uiReport.resolved.refused ?? []).length}
						<p class="mt-1 text-red-600 dark:text-red-400">
							refused: {(uiReport.resolved.refused ?? []).map((r) => `${r.plugin ?? 'unknown'}: ${r.reason}`).join('; ')}
						</p>
					{/if}
				</div>
			{/if}
		</UserSettingSection>

		<BuiltinsSection />

		<UserSettingSection title={$i18n.t('settings.personal.vibes.sections.trim.title')}>
			<p class="mb-2 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('settings.personal.vibes.trim.gate.description')}
			</p>
			<p class="mb-2 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('settings.personal.vibes.trim.connections.description')}
			</p>
			<p class="mb-2 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('settings.personal.vibes.trim.license.description')}
			</p>

			<UserSettingRow let:labelId label={$i18n.t('settings.personal.vibes.trim.enabled.label')}>
				<Switch
					state={ex.trimEnabled}
					ariaLabelledbyId={labelId}
					on:change={(e) => saveEx({ trimEnabled: e.detail })}
				/>
			</UserSettingRow>

			{#if trimReport}
				<p class="mt-2 text-[0.6875rem] text-gray-500 dark:text-gray-400">
					{$i18n.t('settings.personal.vibes.trim.act.description')}
				</p>
				<ul class="flex flex-col gap-1 mt-1 text-xs">
					{#each (trimReport.settings?.feature_keys ?? []) as key (key)}
						<UserSettingRow let:rowLabelId label={key}>
							<Switch
								state={!(trimReport.resolved?.gate ?? {})[key]}
								ariaLabelledbyId={rowLabelId}
								on:change={(e) => saveEx({ trim: { ...ex.trim, [key]: e.detail } })}
							/>
						</UserSettingRow>
					{/each}
				</ul>

				<p class="mt-2 text-[0.6875rem] text-gray-500 dark:text-gray-400">
					{$i18n.t('settings.personal.vibes.trim.reportedOnly.description')}
				</p>
				<ul class="flex flex-col gap-1 mt-1 font-mono text-xs">
					{#each (trimReport.settings?.record_keys ?? []) as name (name)}
						<li class="text-gray-600 dark:text-gray-300">{name}: {(trimReport.resolved?.gate ?? {})[name] ? 'on' : 'reported off'}</li>
					{/each}
				</ul>

				{#if (trimReport.resolved?.refused_overrides ?? []).length}
					<p class="mt-2 text-xs text-red-600 dark:text-red-400">
						refused: {(trimReport.resolved?.refused_overrides ?? []).map((r) => `${r.name} (${r.reason})`).join('; ')}
					</p>
				{/if}
				<p class="mt-1 text-[0.6875rem] text-gray-500 dark:text-gray-400">
					{$i18n.t('settings.personal.vibes.trim.refused.description')}
				</p>
			{/if}
		</UserSettingSection>

		{#if status}
			<p class="mt-3 text-[0.6875rem] text-gray-400 dark:text-gray-600">
				{$i18n.t('settings.personal.vibes.dataDir.label')}: <span class="font-mono">{status.data_dir}</span>
			</p>
		{/if}
	{/if}
		<HelpSection />
	</div>
