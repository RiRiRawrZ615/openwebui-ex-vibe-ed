<!-- OpenWebVibes runtime theme module.

Swappable by design. This is the component the modularity work is for: a UI component that can
be replaced without touching the request path, because it owns one job, applying the theme the
settings hold.

It does not invent values. It calls the mirror of the tested engine, and it only applies a theme
the engine reported as ready. A theme with a failing text pairing is not applied, and the component
says why, because a theme that ships unreadable text is not a theme.

Three facts worth stating plainly, because each one was a real failure:

  1. The visible problem was never the gradient. It was the surfaces. Every panel is Tailwind
     `dark:bg-gray-900`, which resolves to `--color-gray-900`, and two separate places stamped
     that token with near-black hex *inline*. Inline custom properties beat every stylesheet,
     so no rule in src/ex_theme.css could win. The ramp is written from here instead, which is
     the only writer left standing.

  2. src/app.html runs before Svelte exists. It cannot call this component, so it reads a
     localStorage flag instead and writes the same hex ramp itself. That is what stops a flash
     of stock dark on load. The two writers must agree; the constants live in exPalette.ts and
     app.html carries a literal copy because it cannot import a module.

  3. Wallpaper paths are validated on the server against the allowed roots. The mirror cannot
     do that, so a local path is not applied here; only a remote URL is. The engine's decision
     comes from the API, and this component applies that decision, not its own guess.
-->
<script lang="ts">
	import { onMount, onDestroy } from 'svelte';

	import { settings } from '$lib/stores';
	import { buildTheme, cssTokens, wallpaperCss } from '$lib/utils/exTheme';
	import { resolveExSettings } from '$lib/utils/exSettings';
	import { applyOwvRamp, releaseOwvRamp, OWV_GRADIENT_HEX } from '$lib/utils/exPalette';
	import { getExStatus } from '$lib/apis/ex';

	let applied = $state('');
	let themeName = $state('');
	let blocked: string[] = $state([]);

	/** Persisted so the pre-hydration script in app.html can paint navy on the next load. */
	const flagOn = () => {
		try {
			localStorage.setItem('owvTheme', 'on');
		} catch {
			/* private mode; the boot script just falls back to its own default */
		}
	};

	const flagOff = () => {
		try {
			localStorage.setItem('owvTheme', 'off');
		} catch {
			/* same */
		}
	};

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
			releaseOwvRamp();
			flagOff();
			return;
		}

		// css_tokens() emits bare declarations, not a rule. Writing them raw into a <style>
		// tag is invalid CSS and silently does nothing, so the declarations are wrapped in
		// the :root rule here. This is the wiring bug that made a correct engine look broken.
		const style = document.getElementById('owv-runtime-theme');
		if (style) style.textContent = `:root {\n${cssTokens(theme)}\n}`;

		// The ramp is the part that actually changes what is on screen. Written after the
		// token sheet so a stale inline value from the boot script cannot survive.
		applyOwvRamp();

		// Gradient double-write. The stylesheet already declares it on .owv-themed, but a
		// user-supplied gradient from the settings tab needs a per-user value, and the token
		// sheet above only carries the engine's default. Writing it here keeps the user's
		// gradient authoritative without needing a rebuild.
		//
		// Two bugs lived here, and together they explained "the bottom right is still too bright"
		// even after the stylesheet stop was darkened:
		//
		// 1. Only stops[0] and stops[1] were used. A three-stop ramp lost its last stop entirely,
		//    so a deliberately darkened end colour was never painted.
		// 2. The endpoints were hardcoded to 0% and 100%, so two stops stretched across the whole
		//    viewport. With a 160deg ramp that puts the brightest colour in the bottom-right
		//    corner, which is exactly the corner that read as too bright.
		//
		// Positions are now distributed evenly across however many stops the user supplied, the
		// same way the stylesheet does it, so both paths produce identical output for the same
		// input and the last stop actually lands where it was designed to.
		const stops = (ex.themeGradient ?? []).filter(Boolean);
		const gradient =
			stops.length >= 2
				? `linear-gradient(160deg, ${stops
						.map((stop, i) => `${stop} ${Math.round((i / (stops.length - 1)) * 100)}%`)
						.join(', ')})`
				: OWV_GRADIENT_HEX;
		document.documentElement.style.setProperty('--owv-gradient', gradient);

		const wall = wallpaperCss(ex.themeWallpaper);
		if (wall) document.documentElement.style.setProperty('--owv-wallpaper', wall);
		else document.documentElement.style.removeProperty('--owv-wallpaper');

		document.documentElement.classList.add('owv-themed');
		flagOn();
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
		document.documentElement.style.removeProperty('--owv-gradient');
		releaseOwvRamp();
	});
</script>

{#if blocked.length}
	<div class="owv-theme-refused px-3 py-2 text-xs text-red-400" role="status">
		theme "{themeName}" not applied: {blocked.join(', ')}
	</div>
{/if}