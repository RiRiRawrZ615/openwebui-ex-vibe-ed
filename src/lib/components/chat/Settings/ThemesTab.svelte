<!--
  Themes: the appearance controls, split out of the OpenWebVibes tab.

  Design notes
  ------------
  1. This was a section inside OpenWebVibes. It is its own tab because theming is something you
     tune while looking at the app, and hunting for it seventeen sections down a single scroll was
     the actual complaint.

  2. The contrast report is the reason this tab earns its own space. `buildTheme()` computes the
     pairings and their ratios locally, so a value that produces unreadable text is flagged while
     you are still typing it, not after a reload. `themePreview` is derived, and nothing here is
     fed into ContentRenderer, so re-running it per keystroke is cheap.

  3. Color fields are spelled out rather than looped where Svelte cannot take a dynamic key inside
     bind:value. The loop that does exist binds on the change handler instead, which is allowed.
-->
<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { createEventDispatcher } from 'svelte';

	import { settings } from '$lib/stores';
	import UserSettingRow from './UserSettingRow.svelte';
	import UserSettingSection from './UserSettingSection.svelte';
	import { getExTheme } from '$lib/apis/ex';
	import { resolveExSettings } from '$lib/utils/exSettings';
	import { buildTheme } from '$lib/utils/exTheme';

	const i18n: Writable<i18nType> = getContext('i18n');
	const dispatch = createEventDispatcher();

	const { saveSettings }: { saveSettings: (updated: Record<string, any>) => void | Promise<void> } =
		$props();

	let ex = $state(resolveExSettings($settings));
	let themeReport: Record<string, any> | null = $state(null);

	let themePreview = $derived(buildTheme(ex));

	const saveEx = async (patch: Record<string, any>) => {
		try {
			await saveSettings({ ex: { ...resolveExSettings($settings), ...patch } });
			dispatch('save');
		} catch {
			// The modal reports the save error; do not claim success here.
		}
		ex = resolveExSettings($settings);
	};

	onMount(async () => {
		const res = await getExTheme(localStorage.token);
		themeReport = 'error' in res ? null : res;
	});
</script>

<div class="flex flex-col">
	<UserSettingSection title={$i18n.t('settings.personal.vibes.sections.theme.title')} first>
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

		<!-- A dynamic key inside bind:value is not something Svelte accepts, so the three color
		     fields are spelled out with the change handler setting the key. -->
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
				on:change={(e) =>
					saveEx({ themeGradient: e.target.value.split(',').map((s) => s.trim()) })}
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
					{$i18n.t('settings.personal.vibes.theme.ready.label')}: {themePreview.mode}, {themePreview.measured
						? 'measured'
						: 'not measured'}
				</p>
				<ul class="flex flex-col gap-1 mt-1 font-mono">
					{#each themePreview.pairings as pair (pair.pair)}
						<li
							class={pair.status === 'fail'
								? 'text-red-600 dark:text-red-400'
								: 'text-gray-600 dark:text-gray-300'}
						>
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
</div>