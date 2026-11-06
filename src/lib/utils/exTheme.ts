// TS mirror of backend/open_webui/utils/ex_theme.py.
//
// The Python engine is the tested one (Code/vibe-ed/test_ex_theme.py, 71 checks). This mirror
// exists because a theme must apply per user at runtime, and the frontend cannot import Python.
// The mirror implements the same rules, not a copy of the shipped hex values: a theme built
// from a seed must produce the same tokens on both sides, otherwise the UI would ship a theme
// the engine refused.
//
// Rule carried over from the Python side: a pairing that carries text and fails contrast is
// not emitted as a color. `buildTheme` reports the failure instead of returning a theme the
// page would render unreadable. Contrast is measured with WCAG relative luminance, so the
// threshold (AA for normal text, 4.5) is a standard, not a taste call.

import type { ExSettings } from './exSettings';

export const MIN_CONTRAST_AA = 4.5;
export const ADJUST_CEILING = 60;
export const RAMP_STOPS = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950];

export type ThemePairing = {
	pair: string;
	foreground: string;
	background: string;
	ratio: number | null;
	carriesText: boolean;
	status: 'pass' | 'fail' | 'decorative' | 'unmeasurable';
};

export type Theme = {
	name: string;
	mode: 'dark' | 'light';
	adjustment: number;
	tokens: Record<string, string>;
	appRamp: Record<number, string>;
	ramp: Record<number, string>;
	gradient: { stops: string[]; css: string; refused: string[] };
	pairings: ThemePairing[];
	failures: ThemePairing[];
	ready: boolean;
	measured: boolean;
};

const HEX = /^#?([0-9a-fA-F]{6})$/;

export function normalizeHex(value: string): string | null {
	if (!value) return null;
	const match = HEX.exec(String(value).trim());
	return match ? '#' + match[1].toLowerCase() : null;
}

function toRgb(hex: string): [number, number, number] | null {
	const norm = normalizeHex(hex);
	if (!norm) return null;
	return [parseInt(norm.slice(1, 3), 16), parseInt(norm.slice(3, 5), 16), parseInt(norm.slice(5, 7), 16)];
}

// WCAG relative luminance, using the sRGB linearization the standard defines.
export function luminance(hex: string): number | null {
	const rgb = toRgb(hex);
	if (!rgb) return null;
	const channel = (c: number) => {
		const s = c / 255;
		return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
	};
	return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
}

export function contrast(foreground: string, background: string): number | null {
	const lf = luminance(foreground);
	const lb = luminance(background);
	if (lf === null || lb === null) return null;
	const lighter = Math.max(lf, lb);
	const darker = Math.min(lf, lb);
	return Math.round(((lighter + 0.05) / (darker + 0.05)) * 100) / 100;
}

// Lightness slider, matching adjust() in the engine exactly. Positive lightens by moving each
// channel toward 255, negative darkens multiplicatively, clamped at both ends. A mirror that
// darkened in both directions would produce a different color than the engine for the same
// slider position, which is the disagreement this file exists to prevent.
export function adjust(hex: string, delta: number): string | null {
	const rgb = toRgb(hex);
	if (!rgb) return null;
	const amount = Math.max(-ADJUST_CEILING, Math.min(ADJUST_CEILING, Number(delta) || 0)) / 100;
	const out = rgb.map((v) => (amount >= 0 ? Math.trunc(v + (255 - v) * amount) : Math.trunc(v * (1 + amount))));
	return '#' + out.map((c) => c.toString(16).padStart(2, '0')).join('');
}

// Python round() is round-half-to-even. Math.round() is round-half-up. That is not a cosmetic
// detail: at a ramp stop whose value lands exactly on a half, the two produce different hex
// values, so the page would render a theme slightly different from the one the engine measured.
// The mirror states the tie rule explicitly instead of inheriting a language default.
function roundHalfEven(value: number): number {
	const floor = Math.floor(value);
	const remainder = value - floor;
	if (Math.abs(remainder) < 1e-9) return floor;
	if (Math.abs(remainder - 0.5) < 1e-9) return floor % 2 === 0 ? floor : floor + 1;
	return remainder > 0.5 ? floor + 1 : floor;
}

// Blend toward a target, the same rule mix() uses, so the ramp is built the same way on both
// sides.
export function mix(hex: string, target: string, amount: number): string | null {
	const a = toRgb(hex);
	const b = toRgb(target);
	if (!a || !b) return null;
	const t = Math.max(0, Math.min(1, Number(amount) || 0));
	const out = [0, 1, 2].map((i) => roundHalfEven(a[i] + (b[i] - a[i]) * t));
	return '#' + out.map((c) => c.toString(16).padStart(2, '0')).join('');
}

export function ramp(seed: string, dark: boolean): Record<number, string> {
	const norm = normalizeHex(seed);
	if (!norm) return {};
	// Target and amount come from the engine: near-black for a dark theme, near-white for a
	// light one, and the amount is derived from the stop number over 1000, not over the last
	// stop. Those two details are where a mirror drifts.
	const target = dark ? '#0b0b0b' : '#fafafa';
	const table: Record<number, string> = {};
	for (const stop of RAMP_STOPS) {
		const amount = dark ? stop / 1000 : 1 - stop / 1000;
		const value = mix(norm, target, amount);
		if (value) table[stop] = value;
	}
	return table;
}

// Scale factors for the four tokens the app paints surfaces with. Mirrors APP_RAMP_FACTORS in
// backend/open_webui/utils/ex_theme.py.
//
// Why scaling rather than mix() toward black: mixing desaturates as it darkens, so a navy seed
// reaches the dark end as neutral charcoal. That is exactly the failure being fixed here, and
// reusing ramp() for these four would have reintroduced it. Scaling every channel by the same
// factor leaves each channel ratio untouched, so hue is preserved and the color stays itself
// at a lower brightness.
export const APP_RAMP_FACTORS: readonly (readonly [number, number])[] = [
	[800, 1.0],
	[850, 0.82],
	[900, 0.62],
	[950, 0.45]
];

export function appRamp(accent: string): Record<number, string> {
	const norm = normalizeHex(accent);
	if (!norm) return {};
	const rgb = toRgb(norm);
	if (!rgb) return {};
	const out: Record<number, string> = {};
	for (const [stop, factor] of APP_RAMP_FACTORS) {
		const scaled = rgb.map((channel) => Math.min(255, Math.max(0, Math.round(channel * factor))));
		out[stop] = '#' + scaled.map((c) => c.toString(16).padStart(2, '0')).join('');
	}
	return out;
}

export function gradient(stops: string[], angleDeg = 135): { stops: string[]; css: string; refused: string[] } {
	const clean = stops.map((s) => normalizeHex(s));
	const refused = stops.filter((_, i) => clean[i] === null).map((s) => String(s));
	const valid = clean.filter((s): s is string => s !== null);
	return {
		stops: valid,
		refused,
		// A gradient needs two stops. One stop is not a gradient, so it is not emitted, the same
		// rule as the engine.
		css: valid.length >= 2 ? `linear-gradient(${angleDeg}deg, ${valid.join(', ')})` : ''
	};
}

export function buildTheme(settings: ExSettings): Theme {
	const accent = adjust(settings.themeAccent, settings.themeAdjust) ?? normalizeHex(settings.themeAccent) ?? '';
	const surface = normalizeHex(settings.themeSurface) ?? '';
	const text = normalizeHex(settings.themeText) ?? '';
	const dark = (luminance(surface) ?? 0) < 0.5;
	const name = (settings.themeName || 'custom').trim().toLowerCase();

	const grad = gradient(settings.themeGradient && settings.themeGradient.length ? settings.themeGradient : [accent, surface]);

	const pairings: ThemePairing[] = [
		{ pair: 'text on surface', foreground: text, background: surface, ratio: contrast(text, surface), carriesText: true, status: 'unmeasurable' },
		{ pair: 'text on accent', foreground: text, background: accent, ratio: contrast(text, accent), carriesText: true, status: 'unmeasurable' },
		{ pair: 'accent on surface', foreground: accent, background: surface, ratio: contrast(accent, surface), carriesText: false, status: 'decorative' }
	];

	for (const pair of pairings) {
		if (pair.ratio === null) {
			pair.status = 'unmeasurable';
			continue;
		}
		if (!pair.carriesText) {
			pair.status = 'decorative';
			continue;
		}
		pair.status = pair.ratio >= MIN_CONTRAST_AA ? 'pass' : 'fail';
	}

	const failures = pairings.filter((p) => p.status === 'fail');
	const unmeasurable = pairings.filter((p) => p.status === 'unmeasurable');

	const tokens: Record<string, string> = {
		'--owv-accent': accent,
		'--owv-surface': surface,
		'--owv-text': text,
		'--owv-gradient': grad.css
	};

	return {
		name,
		mode: dark ? 'dark' : 'light',
		adjustment: Number(settings.themeAdjust) || 0,
		tokens,
		appRamp: appRamp(accent),
		ramp: ramp(accent, dark),
		gradient: grad,
		pairings,
		failures,
		ready: failures.length === 0 && grad.refused.length === 0,
		measured: pairings.length > 0 && unmeasurable.length === 0
	};
}

// Wallpaper is not validated the same way on both sides, and the mirror should not pretend it
// is. The engine validates a local path against the allowed roots; that is a server rule with
// server knowledge of the data dir. The mirror only accepts a remote URL, so a local path never
// reaches the page through a rule the mirror invented. The layout applies the engine's decision
// when the API returns it, and here only what is safe to apply without roots.
export function wallpaperCss(value: string): string {
	if (!value) return '';
	const raw = String(value).trim();
	if (/^https?:\/\//i.test(raw)) return `url(${raw})`;
	return '';
}

// Only tokens that measured or validated are emitted, the same rule as css_tokens() in the
// Python engine. A failed pairing never becomes a color on the page.
export function cssTokens(theme: Theme): string {
	// Enforced here, not just promised: a theme with a failing text pairing emits nothing, so
	// the UI cannot render an unreadable theme by skipping the ready check.
	if (theme.failures.length) return '';

	const lines: string[] = [];
	for (const [name, value] of Object.entries(theme.tokens)) {
		if (value) lines.push(`\t${name}: ${value};`);
	}
	// The app ramp. These four are the ones that change what is on screen: upstream resolves
	// `dark:bg-gray-900` to var(--color-gray-900), so retinting these retints every panel at
	// once. Emitting only the --owv-* names was the original wiring mistake. Upstream reads
	// none of them, so a perfectly correct engine had no visible effect.
	for (const stop of [800, 850, 900, 950]) {
		const value = theme.appRamp?.[stop];
		if (value) lines.push(`\t--color-gray-${stop}: ${value};`);
	}
	for (const stop of RAMP_STOPS) {
		const value = theme.ramp[stop];
		if (value) lines.push(`\t--owv-ramp-${stop}: ${value};`);
	}
	return lines.join('\n');
}
