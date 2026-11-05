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
		runExImport,
		type ExBackendCheck,
		type ExImportReport,
		type ExInstallPlan,
		type ExStatusResponse
	} from '$lib/apis/ex';
	import { resolveExSettings } from '$lib/utils/exSettings';

	const i18n: Writable<i18nType> = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let saveSettings: (updated: Record<string, any>) => void | Promise<void>;

	let ex = resolveExSettings($settings);
	let status: ExStatusResponse | null = null;
	let backendCheck: ExBackendCheck | null = null;
	let plan: ExInstallPlan | null = null;
	let importReport: ExImportReport | null = null;

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

	onMount(() => {
		refresh();
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

		{#if status}
			<p class="mt-3 text-[0.6875rem] text-gray-400 dark:text-gray-600">
				{$i18n.t('settings.personal.vibes.dataDir.label')}: <span class="font-mono">{status.data_dir}</span>
			</p>
		{/if}
	{/if}
</div>
