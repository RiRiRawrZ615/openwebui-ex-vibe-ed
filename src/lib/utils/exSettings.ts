// TS mirror of backend/open_webui/utils/ex_settings.py. Defaults must match the
// Python resolver exactly; if one changes, change the other. The Python side is the
// tested one (Code/vibe-ed/test_ex_settings.py).

export type ExSettings = {
	enabled: boolean;
	journalWindow: number;
	toolOutputWindow: number;
	toolOutputMaxChars: number;
	loopGuard: boolean;
	loopThreshold: number;
	liveStats: boolean;
	showInfoIcon: boolean;
	launcherStatus: boolean;
	verbatimSummary: boolean;
	backend: string;
	modelPath: string;
	allowInstall: boolean;
};

export const EX_DEFAULTS: ExSettings = {
	enabled: true,
	journalWindow: 1,
	toolOutputWindow: 3,
	toolOutputMaxChars: 1500,
	loopGuard: true,
	loopThreshold: 3,
	liveStats: true,
	showInfoIcon: true,
	launcherStatus: true,
	verbatimSummary: false,
	backend: 'auto',
	modelPath: '',
	allowInstall: false
};

// Settings save per key, and the settings modal writes the ui block, so an EX tab
// lands at settings.ui.ex. Accept both shapes.
export function resolveExSettings(settings: any): ExSettings {
	const merged: ExSettings = { ...EX_DEFAULTS };
	const raw =
		(settings?.ui?.ex && typeof settings.ui.ex === 'object' ? settings.ui.ex : null) ??
		(settings?.ex && typeof settings.ex === 'object' ? settings.ex : null);

	if (raw) {
		for (const [key, value] of Object.entries(raw)) {
			if (key in merged && value !== undefined && value !== null) {
				(merged as any)[key] = value;
			}
		}
	}
	return merged;
}
