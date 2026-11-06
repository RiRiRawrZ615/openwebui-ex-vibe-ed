/**
 * OpenWebVibes palette, in hex.
 *
 * Why hex and not the oklch values in src/ex_theme.css: the two places that stamp the
 * ramp onto <html> do it with `element.style.setProperty`, which takes a string the browser
 * must be able to parse on its own. Hex is unambiguous there, and it keeps the inline values
 * and the stylesheet fallbacks describing the same colors instead of drifting.
 *
 * These are the same lightness steps as src/tailwind.css (0.32 / 0.27 / 0.20 / 0.16), so
 * every stock `text-gray-*` pairing keeps the contrast it was designed with. Only the hue
 * changes.
 *
 * KEEP IN SYNC with the literal copy in src/app.html. That script runs before any module
 * loads, so it cannot import this file. If these values change, change both, or the first
 * paint will disagree with everything after it.
 */
export const OWV_RAMP_HEX = {
	800: '#24405f',
	850: '#1c3450',
	900: '#14293f',
	950: '#0e1d2e'
} as const;

export const OWV_SURFACE_HEX = '#12203a';
export const OWV_ACCENT_HEX = '#24406b';
export const OWV_TEXT_HEX = '#e6ecf5';
export const OWV_LINE_HEX = '#22405f';

/** Three stop navy. The corner anchor is what makes it read as a gradient on a phone. */
export const OWV_GRADIENT_HEX = 'linear-gradient(160deg, #0b1729 0%, #16304f 46%, #274a78 100%)';

/**
 * True when the runtime theme owns the page. Read from the DOM rather than from settings on
 * purpose: both stampers can run before settings have loaded, and the class is the one piece
 * of state that is always already correct when they do.
 */
export const owvThemeActive = (): boolean =>
	typeof document !== 'undefined' && document.documentElement.classList.contains('owv-themed');

/**
 * Write the navy ramp as inline custom properties.
 *
 * This exists because inline custom properties beat every stylesheet rule. src/app.html
 * boots oled-dark by stamping pure black inline, and Settings/General.svelte::applyTheme
 * stamped #333/#262626/#171717/#0d0d0d inline. Either one silently beat the .owv-themed rules
 * in ex_theme.css, which is exactly what "the theme is broken" looked like from the outside.
 * Writing the same properties from here means the last writer is the palette the user
 * actually asked for.
 */
export const applyOwvRamp = () => {
	if (typeof document === 'undefined') return;
	const style = document.documentElement.style;
	style.setProperty('--color-gray-800', OWV_RAMP_HEX[800]);
	style.setProperty('--color-gray-850', OWV_RAMP_HEX[850]);
	style.setProperty('--color-gray-900', OWV_RAMP_HEX[900]);
	style.setProperty('--color-gray-950', OWV_RAMP_HEX[950]);
};

/**
 * Remove the ramp override and hand the four tokens back to src/tailwind.css.
 *
 * Needed on teardown: without this, switching the runtime theme off would leave a navy page
 * with no way back to stock except a reload.
 */
export const releaseOwvRamp = () => {
	if (typeof document === 'undefined') return;
	const style = document.documentElement.style;
	for (const key of ['800', '850', '900', '950']) {
		style.removeProperty(`--color-gray-${key}`);
	}
};