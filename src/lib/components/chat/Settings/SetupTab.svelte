<!--
  Setup: everything about making this machine able to run.

  Design notes
  ------------
  1. This used to live inside the OpenWebVibes tab, appended to the end of the Runtime section and
     nested inside `{#if status?.launcher?.present && Object.keys(status.launcher.locked).length > 0}`.
     That condition means "a launcher handshake arrived AND it locked at least one sampler". On a
     fresh install neither is true, so the whole workflow rendered nothing, which is the exact
     situation it exists for. It is top level here, and the only thing that guards it is whether
     the probe has run.

  2. There is one workflow, not a first-run one and a returning one. A machine that already has the
     engines is the case where probe() finds them, so what differs between new and existing users
     is only whether `missing` is empty when the page opens.

  3. Discovery reports where each engine was found and where it looked. "checked 6 locations" is
     printed rather than a bare red dot, because the useful question is never "is it missing" but
     "did it look where mine is".

  4. Write actions need `ex.allowInstall`. The backend enforces it; the switch here is honest about
     that rather than hiding the controls.
-->
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
		getExInstallPlan,
		getExDiscover,
		postExDiscoverManual,
		runExImport,
		type ExDiscoverReport,
		type ExImportReport,
		type ExInstallPlan
	} from '$lib/apis/ex';
	import { resolveExSettings } from '$lib/utils/exSettings';

	const i18n: Writable<i18nType> = getContext('i18n');
	const dispatch = createEventDispatcher();

	const { saveSettings }: { saveSettings: (updated: Record<string, any>) => void | Promise<void> } =
		$props();

	let ex = $state(resolveExSettings($settings));

	let plan: ExInstallPlan | null = $state(null);
	let planning = $state(false);
	let components = $state('webui,llamacpp,strata');

	let discover: Record<string, ExDiscoverReport> | null = $state(null);
	let discoverMissing: string[] = $state([]);
	let discovering = $state(false);
	let manualFor = $state<string | null>(null);
	let manualPath = $state('');
	let manualBusy = $state(false);

	let importSource = $state('');
	let importTarget = $state('');
	let importForce = $state(false);
	let importing = $state(false);
	let importReport: ExImportReport | null = $state(null);

	const saveEx = async (patch: Record<string, any>) => {
		try {
			await saveSettings({ ex: { ...resolveExSettings($settings), ...patch } });
			dispatch('save');
		} catch {
			// The modal reports the save error; do not claim success here.
		}
		ex = resolveExSettings($settings);
	};

	const probe = async () => {
		discovering = true;
		const res = await getExDiscover(localStorage.token, []);
		if ('error' in res) {
			toast.error(`${res.error}`);
		} else {
			discover = res.components;
			discoverMissing = res.missing ?? [];
		}
		discovering = false;
	};

	const buildPlan = async () => {
		planning = true;
		const wanted = components
			.split(',')
			.map((s) => s.trim())
			.filter(Boolean);
		const res = await getExInstallPlan(localStorage.token, wanted, false);
		if ('error' in res) {
			toast.error(`${res.error}`);
			plan = null;
		} else {
			plan = res;
		}
		planning = false;
	};

	const submitManual = async (component: string) => {
		manualBusy = true;
		// The backend validates that the path exists, is a file and is executable. It does not run
		// it, so a wrong binary is caught here rather than at first use.
		const res = await postExDiscoverManual(localStorage.token, component, manualPath);
		if ('error' in res) {
			toast.error(`${res.error}`);
		} else if (!res.accepted) {
			toast.error(res.reason);
		} else {
			toast.success(`${component}: ${res.path}`);
			manualFor = null;
			manualPath = '';
			await probe();
		}
		manualBusy = false;
	};

	const runImport = async (dryRun: boolean) => {
		importing = true;
		const res = await runExImport(
			localStorage.token,
			importSource || undefined,
			importTarget || undefined,
			importForce
		);
		if ('error' in res) {
			importReport = { error: `${res.error}` } as ExImportReport;
		} else {
			importReport = res;
		}
		importing = false;
	};

	onMount(() => {
		// Probing on mount is what makes a machine that is already set up show "nothing to do"
		// immediately instead of advertising install steps it does not need.
		probe();
	});
</script>

<div class="flex flex-col">
	<UserSettingSection title={$i18n.t('settings.personal.vibes.sections.install.title')} first>
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

	<UserSettingSection title={$i18n.t('settings.personal.vibes.sections.setup.title')}>
		<div class="mb-2 flex items-center justify-between">
			<span class="text-xs text-gray-700 dark:text-gray-300">
				{$i18n.t('settings.personal.vibes.setup.thisMachine.label')}
			</span>
			<button
				type="button"
				class="text-[0.6875rem] text-gray-500 underline hover:text-gray-900 dark:hover:text-white"
				on:click={probe}>Re-check</button
			>
		</div>

		{#if discovering}
			<div class="flex items-center gap-2 py-2 text-xs text-gray-500">
				<Spinner /> Checking for installed engines...
			</div>
		{:else if discover}
			{#if discoverMissing.length === 0}
				<p class="mb-2 text-xs text-green-700 dark:text-green-400">
					{$i18n.t('settings.personal.vibes.setup.allPresent.description')}
				</p>
			{:else}
				<p class="mb-2 text-xs text-gray-500 dark:text-gray-400">
					Not found: {discoverMissing.join(', ')}. Install them above, or point at the binary
					you already have.
				</p>
			{/if}

			<div class="space-y-1.5">
				{#each Object.entries(discover) as [component, report] (component)}
					<div
						class="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-gray-100 px-2 py-1.5 dark:border-gray-850"
					>
						<div class="min-w-0">
							<div class="flex items-center gap-1.5 text-xs">
								{#if report.found}
									<span class="text-green-600 dark:text-green-400">●</span>
								{:else}
									<span class="text-gray-300 dark:text-gray-600">○</span>
								{/if}
								<span class="font-medium text-gray-700 dark:text-gray-300">{component}</span>
								{#if report.source === 'path'}
									<span class="text-gray-400 dark:text-gray-500">on PATH</span>
								{:else if report.source === 'candidate'}
									<span class="text-gray-400 dark:text-gray-500">known location</span>
								{/if}
							</div>
							<div
								class="truncate font-mono text-[0.625rem] text-gray-400 dark:text-gray-500"
								title={report.path || ''}
							>
								{report.found ? report.path : `checked ${report.candidates_tried.length} locations`}
							</div>
						</div>

						<button
							type="button"
							class="shrink-0 text-[0.6875rem] text-gray-500 underline hover:text-gray-900 dark:hover:text-white"
							on:click={() => {
								manualFor = manualFor === component ? null : component;
								manualPath = report.path || '';
							}}>{report.found ? 'Change' : 'Locate'}</button
						>
					</div>

					{#if manualFor === component}
						<div class="flex items-center gap-2 pl-2">
							<input
								type="text"
								bind:value={manualPath}
								placeholder="/path/to/binary"
								class="h-7 min-w-0 flex-1 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 font-mono text-[0.6875rem] text-gray-700 outline-hidden focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300"
							/>
							<button
								type="button"
								disabled={manualBusy || !manualPath.trim()}
								class="shrink-0 rounded-lg bg-gray-900 px-2 py-1 text-[0.6875rem] font-medium text-white disabled:opacity-50 dark:bg-white dark:text-gray-900"
								on:click={() => submitManual(component)}>Use</button
							>
							{#if manualBusy}<Spinner />{/if}
						</div>
					{/if}
				{/each}
			</div>
		{:else}
			<button
				type="button"
				class="text-[0.6875rem] text-gray-500 underline hover:text-gray-900 dark:hover:text-white"
				on:click={probe}>Check for installed engines</button
			>
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
</div>