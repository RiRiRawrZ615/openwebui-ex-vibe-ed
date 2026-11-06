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
		getExInstallPlan,
		getExTheme,
		getExRecovery,
		getExModules,
		getExCluster,
	getExContext,
	getExSteering,
	getExUi,
	getExTrim,
	addExSteering,
		runExBatch,
		runExImport,
		type ExBackendCheck,
		type ExBatchSummary,
		type ExImportReport,
		type ExInstallPlan,
		type ExStatusResponse
	} from '$lib/apis/ex';
	import { resolveExSettings } from '$lib/utils/exSettings';
	import { exSlots, refreshSlots } from '$lib/stores/exSlots';
	import { buildTheme } from '$lib/utils/exTheme';

	const i18n: Writable<i18nType> = getContext('i18n');
	const dispatch = createEventDispatcher();

	const { saveSettings }: { saveSettings: (updated: Record<string, any>) => void | Promise<void> } = $props();

	let ex = resolveExSettings($settings);
	let status: ExStatusResponse | null = null;
	let backendCheck: ExBackendCheck | null = null;
	let plan: ExInstallPlan | null = null;
	let importReport: ExImportReport | null = null;

	let themeReport: Record<string, any> | null = null;
	let recoveryReport: Record<string, any> | null = null;
	let modulesReport: Record<string, any> | null = null;
	let clusterReport: Record<string, any> | null = null;
	let contextReport: Record<string, any> | null = null;
	let steeringReport: Record<string, any> | null = null;
	let uiReport: Record<string, any> | null = null;
	let trimReport: Record<string, any> | null = null;
	let steeringText = '';
	let pendingSteering = 0;
	let batchSummary: ExBatchSummary | null = null;

	let batchAction = 'move';
	let batchIds = '';
	let batchFolder = '';
	let batchForce = false;
	let batching = false;

	let loading = true;
	let checkingBackend = false;
	let planning = false;
	let importing = false;

	let modelPath = '';
	let components = 'webui';
	let importSource = '';
	let importTarget = '';
	let importForce = false;

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
		planning = false;
		plan = null;
		checkingBackend = false;
	};

	const buildPlan = async () => {
		planning = true;
		const res = await getExInstallPlan(localStorage.token, components.split(',').map((c) => c.trim()));
		if ('error' in res) {
			plan = null;
			toast.error(`${res.error}`);
		} else {
			plan = res;
		}
		planning = false;
	};

	const runImport = async (dryRun: boolean) => {
		if (!importSource) {
			toast.error($i18n.t('settings.personal.vibes.importRequiresSource.description'));
			return;
		}
		importing = true;
		const res = await runExImport(localStorage.token, {
			source: importSource,
			target: importTarget,
			dryRun,
			force: !dryRun && importForce
		});
		if ('error' in res) {
			importReport = null;
			toast.error(`${res.error}`);
		} else {
			importReport = res;
		}
		importing = false;
	};

	// Theme is measured, not asserted. The mirror computes the pairings from the settings, and
	// the report is what the user sees: a theme with a failing text pairing is not applied, and
	// the failing pairing is named here instead of being silently half-rendered.
	// Runes mode, matching the rest of this codebase: no legacy reactive labels. The preview is
	// recomputed when the settings change, so the pairing report always reflects the values the
	// user actually has, not the values at mount time.
	let themePreview = buildTheme(ex);

	$effect(() => {
		themePreview = buildTheme(ex);
	});

	const refreshTheme = async () => {
		const res = await getExTheme(localStorage.token);
		themeReport = 'error' in res ? null : res;
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
		refresh();
		refreshTheme();
		refreshRecovery();
		refreshModules();
		refreshCluster();
		refreshContext();
		refreshSteering();
		refreshUi();
		refreshTrim();
		refreshSlots(localStorage.token);
	});
</script>

<div id="tab-openwebvibes" class="flex flex-col h-full overflow-y-auto text-sm">
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

		<UserSettingSection title={$i18n.t('settings.personal.vibes.sections.install.title')}>
			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.allowInstall.label')}
				description={$i18n.t('settings.personal.vibes.allowInstall.description')}
			>
				<Switch
					state={ex.allowInstall}
					ariaLabelledbyId={labelId}
					on:change={(e) => {
						saveEx({ allowInstall: e.detail });
					}}
				/>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.components.label')}
				description={$i18n.t('settings.personal.vibes.components.description')}
			>
				<input
					type="text"
					id={`${labelId}-control`}
					bind:value={components}
					placeholder="webui,llamacpp,strata"
					aria-label={$i18n.t('settings.personal.vibes.components.label')}
					class="h-7 w-48 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300"
				/>
			</UserSettingRow>

			<button
				class="text-xs text-gray-500 transition-colors hover:text-gray-900 dark:text-gray-500 dark:hover:text-white"
				type="button"
				on:click={buildPlan}
			>
				{$i18n.t('settings.personal.vibes.planInstall.label')}
			</button>

			{#if planning}
				<div class="flex items-center gap-2 text-xs text-gray-500">
					<Spinner />
					{$i18n.t('Loading')}
				</div>
			{:else if plan}
				<div class="text-xs text-gray-700 dark:text-gray-300">
					<p class="text-gray-500 dark:text-gray-400">
						{plan.runs
							? $i18n.t('settings.personal.vibes.install.wouldRun.description')
							: $i18n.t('settings.personal.vibes.install.dryRun.description')}
					</p>
					{#if plan.steps.length}
						<ul class="flex flex-col gap-1 mt-1">
							{#each plan.steps as step (step.component + step.action)}
								<li>
									<span class="font-medium">{step.component}</span>
									<span class="font-mono text-gray-500 dark:text-gray-400"> - {step.action}</span>
								</li>
							{/each}
						</ul>
					{/if}
					{#if plan.refused.length}
						<p class="mt-1 text-red-600 dark:text-red-400">
							refused: {plan.refused.join(', ')}
						</p>
					{/if}
				</div>
			{/if}
		</UserSettingSection>

		<UserSettingSection title={$i18n.t('settings.personal.vibes.sections.import.title')}>
			<p class="mb-2 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('settings.personal.vibes.import.readOnly.description')}
			</p>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.import.source.label')}
			>
				<input
					type="text"
					id={`${labelId}-control`}
					bind:value={importSource}
					placeholder="~/.open-webui/webui.db"
					aria-label={$i18n.t('settings.personal.vibes.import.source.label')}
					class="h-7 w-48 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300"
				/>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.import.target.label')}
				description={$i18n.t('settings.personal.vibes.import.target.description')}
			>
				<input
					type="text"
					id={`${labelId}-control`}
					bind:value={importTarget}
					placeholder="defaults to the fork data dir"
					aria-label={$i18n.t('settings.personal.vibes.import.target.label')}
					class="h-7 w-48 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300"
				/>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.import.force.label')}
				description={$i18n.t('settings.personal.vibes.import.force.description')}
			>
				<Switch
					state={importForce}
					ariaLabelledbyId={labelId}
					on:change={(e) => {
						importForce = e.detail;
					}}
				/>
			</UserSettingRow>

			<div class="flex items-center gap-3">
				<button
					class="text-xs text-gray-500 transition-colors hover:text-gray-900 dark:text-gray-500 dark:hover:text-white"
					type="button"
					on:click={() => runImport(true)}
				>
					{$i18n.t('settings.personal.vibes.import.dryRun.label')}
				</button>
				<button
					class="text-xs text-gray-500 transition-colors hover:text-gray-900 dark:text-gray-500 dark:hover:text-white"
					type="button"
					on:click={() => runImport(false)}
				>
					{$i18n.t('settings.personal.vibes.import.run.label')}
				</button>
			</div>

			{#if importing}
				<div class="flex items-center gap-2 text-xs text-gray-500">
					<Spinner />
					{$i18n.t('Loading')}
				</div>
			{:else if importReport}
				<div class="text-xs text-gray-700 dark:text-gray-300">
					{#if importReport.error}
						<p class="text-red-600 dark:text-red-400">{importReport.error}</p>
					{:else}
						<p class="text-gray-500 dark:text-gray-400">
							{importReport.rows} rows over {Object.keys(importReport.tables ?? {}).length} tables
						</p>
						<ul class="flex flex-col gap-1 mt-1">
							{#each Object.entries(importReport.tables ?? {}) as [table, info] (table)}
								<li class="font-mono">
									{table}: {info.rows} rows{info.attempted ? ` (attempted ${info.attempted})` : ''}
								</li>
							{/each}
						</ul>
						{#if (importReport.skipped ?? []).length}
							<p class="mt-1 text-gray-500 dark:text-gray-400">
								skipped: {(importReport.skipped ?? []).join(', ')}
							</p>
						{/if}
					{/if}
				</div>
			{/if}
		</UserSettingSection>

		<UserSettingSection title={$i18n.t('settings.personal.vibes.sections.theme.title')}>
			<p class="mb-2 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('settings.personal.vibes.theme.contrast.description')}
			</p>

			<UserSettingRow let:labelId label={$i18n.t('settings.personal.vibes.theme.name.label')}>
				<input
					type="text"
					id={`${labelId}-control`}
					value={ex.themeName}
					aria-label={$i18n.t('settings.personal.vibes.theme.name.label')}
					on:change={(e) => saveEx({ themeName: e.target.value })}
					class="h-7 w-48 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300"
				/>
			</UserSettingRow>

			<!-- Explicit rows per part. A dynamic key inside bind:value is not something Svelte
			     accepts, so the three color fields are spelled out rather than looped. -->
			{#each [['accent', 'themeAccent'], ['surface', 'themeSurface'], ['text', 'themeText']] as [part, key] (part)}
				<UserSettingRow let:labelId label={$i18n.t(`settings.personal.vibes.theme.${part}.label`)}>
					<div class="flex items-center gap-2">
						<input
							type="text"
							id={`${labelId}-control`}
							value={ex[key]}
							aria-label={$i18n.t(`settings.personal.vibes.theme.${part}.label`)}
							class="h-7 w-24 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs font-mono text-gray-700 outline-hidden focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300"
							on:change={(e) => saveEx({ [key]: e.target.value })}
						/>
						<span
							class="inline-block h-5 w-5 rounded-md border border-gray-200"
							style={`background:${ex[key]}`}
						></span>
					</div>
				</UserSettingRow>
			{/each}

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.theme.gradient.label')}
				description={$i18n.t('settings.personal.vibes.theme.gradient.description')}
			>
				<input
					type="text"
					id={`${labelId}-control`}
					value={ex.themeGradient.join(', ')}
					aria-label={$i18n.t('settings.personal.vibes.theme.gradient.label')}
					class="h-7 w-48 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs font-mono text-gray-700 outline-hidden focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300"
					on:change={(e) => saveEx({ themeGradient: e.target.value.split(',').map((s) => s.trim()) })}
				/>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.theme.adjust.label')}
				description={$i18n.t('settings.personal.vibes.theme.adjust.description')}
			>
				<input
					type="range"
					id={`${labelId}-control`}
					min="-60"
					max="60"
					step="1"
					value={ex.themeAdjust}
					aria-label={$i18n.t('settings.personal.vibes.theme.adjust.label')}
					on:change={(e) => saveEx({ themeAdjust: Number(e.target.value) })}
					class="w-48 accent-blue-600"
				/>
				<span class="text-xs font-mono text-gray-500">{ex.themeAdjust}</span>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.theme.wallpaper.label')}
				description={$i18n.t('settings.personal.vibes.theme.wallpaper.description')}
			>
				<input
					type="text"
					id={`${labelId}-control`}
					value={ex.themeWallpaper}
					placeholder="https:// or a path inside the allowed roots"
					on:change={(e) => saveEx({ themeWallpaper: e.target.value })}
					aria-label={$i18n.t('settings.personal.vibes.theme.wallpaper.label')}
					class="h-7 w-48 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300"
				/>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.vibes.theme.icon.label')}
				description={$i18n.t('settings.personal.vibes.theme.icon.description')}
			>
				<input
					type="text"
					id={`${labelId}-control`}
					value={ex.themeIcon}
					placeholder="static/ex_assets/icon.png"
					on:change={(e) => saveEx({ themeIcon: e.target.value })}
					aria-label={$i18n.t('settings.personal.vibes.theme.icon.label')}
					class="h-7 w-48 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs font-mono text-gray-700 outline-hidden focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300"
				/>
			</UserSettingRow>

			{#if themePreview}
				<div class="mt-2 text-xs">
					<p class="text-gray-500 dark:text-gray-400">
						{$i18n.t('settings.personal.vibes.theme.ready.label')}: {themePreview.mode}, {themePreview.measured ? 'measured' : 'not measured'}
					</p>
					<ul class="flex flex-col gap-1 mt-1 font-mono">
						{#each themePreview.pairings as pair (pair.pair)}
							<li class={pair.status === 'fail' ? 'text-red-600 dark:text-red-400' : 'text-gray-600 dark:text-gray-300'}>
								{pair.pair}: {pair.ratio ?? 'unmeasurable'} ({pair.status})
							</li>
						{/each}
					</ul>
					{#if themePreview.failures.length}
						<p class="mt-1 text-red-600 dark:text-red-400">
							{$i18n.t('settings.personal.vibes.theme.refused.description')}
						</p>
					{/if}
				</div>
			{/if}

			{#if themeReport}
				<p class="mt-1 text-[0.6875rem] text-gray-400 dark:text-gray-600">
					server css emitted: {themeReport.css ? 'yes' : 'no'}
				</p>
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
</div>
