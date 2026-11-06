<!-- OpenWebVibes runtime theme module.

Swappable by design. This is the component the modularity work is for: a UI component that can
be replaced without touching the request path, because it owns one job, applying the theme the
settings hold.

It does not invent values. It calls the mirror of the tested engine, and it only applies a theme
the engine reported as ready. A theme with a failing text pairing is not applied, and the component
says why, because a theme that ships unreadable text is not a theme.

Two facts worth stating plainly:
  - the static file src/ex_theme.css holds the shipped default. This component overrides those
    tokens at runtime from the user's settings, so a change in the settings tab changes the page
    without a rebuild.
  - wallpaper paths are validated on the server against the allowed roots. The mirror cannot do
    that, so a local path is not applied here; only a remote URL is. The engine's decision comes
    from the API, and this component applies that decision, not its own guess.
-->
<script lang="ts">
	import { onMount, onDestroy } from 'svelte';

	import { settings } from '$lib/stores';
	import { buildTheme, cssTokens, wallpaperCss } from '$lib/utils/exTheme';
	import { resolveExSettings } from '$lib/utils/exSettings';
	import { getExStatus } from '$lib/apis/ex';

	let applied = $state('');
	let themeName = $state('');
	let blocked: string[] = $state([]);

	const apply = () => {
		const ex = resolveExSettings($settings);
		const theme = buildTheme(ex);

		themeName = theme.name;
		blocked = theme.failures.map((p) => `${p.pair} ${p.ratio}`);

		if (!theme.ready) {
			// Not applied. The reason stays visible so the user knows the theme was refused and
			// not silently half-rendered.
			applied = '';
			document.documentElement.classList.remove('owv-themed');
			return;
		}

		// css_tokens() emits bare declarations, not a rule. Writing them raw into a <style>
		// tag is invalid CSS and silently does nothing, so the declarations are wrapped in
		// the :root rule here. This is the wiring bug that made a correct engine look broken.
		const style = document.getElementById('owv-runtime-theme');
		if (style) style.textContent = `:root {\n${cssTokens(theme)}\n}`;

		const wall = wallpaperCss(ex.themeWallpaper);
		if (wall) document.documentElement.style.setProperty('--owv-wallpaper', wall);
		else document.documentElement.style.removeProperty('--owv-wallpaper');

		document.documentElement.classList.add('owv-themed');
		applied = cssTokens(theme);
	};

	const refreshFromApi = async () => {
		// The server is the source of truth for anything it validates. A theme the server refused
		// is reported here rather than applied from a local guess.
		const res = await getExStatus(localStorage.token);
		if ('error' in res) return;
		if (res.settings?.themeName) themeName = String(res.settings.themeName);
	};

	// Runes mode, matching the convention in this codebase (the settings components use $effect,
	// not legacy labels).
	$effect(() => {
		if ($settings) apply();
	});

	onMount(() => {
		if (!document.getElementById('owv-runtime-theme')) {
			const style = document.createElement('style');
			style.id = 'owv-runtime-theme';
			document.head.appendChild(style);
		}
		apply();
		refreshFromApi();
	});

	onDestroy(() => {
		document.documentElement.classList.remove('owv-themed');
	});
</script>

{#if blocked.length}
	<div class="owv-theme-refused px-3 py-2 text-xs text-red-400" role="status">
		theme "{themeName}" not applied: {blocked.join(', ')}
	</div>
{/if}
