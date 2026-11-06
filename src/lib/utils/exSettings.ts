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
	ragMemory: boolean;
	ragTopK: number;
	ragMaxChars: number;
	ragMinScore: number;
	backend: string;
	modelPath: string;
	allowInstall: boolean;
	autoRetry: boolean;
	maxRetries: number;
	shapeRetries: number;
	backoffSeconds: number;
	resumeOnCrash: boolean;
	exportFallback: boolean;
	previousContext: boolean;
	previousContextChars: number;
	previousContextAsSystem: boolean;
	steeringQueue: boolean;
	drainSteeringAtBoundary: boolean;
	steeringMode: string;
	uiPlugins: boolean;
	themeName: string;
	themeAccent: string;
	themeSurface: string;
	themeText: string;
	themeGradient: string[];
	themeAdjust: number;
	themeWallpaper: string;
	themeIcon: string;
	workers: Array<Record<string, unknown>>;
	modules: Record<string, boolean>;
	trimEnabled: boolean;
	trim: Record<string, boolean>;
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
	ragMemory: true,
	ragTopK: 6,
	ragMaxChars: 4000,
	ragMinScore: 0.25,
	backend: 'auto',
	modelPath: '',
	allowInstall: false,
	autoRetry: true,
	maxRetries: 3,
	shapeRetries: 1,
	backoffSeconds: 2.0,
	resumeOnCrash: true,
	exportFallback: true,
	previousContext: true,
	previousContextChars: 12000,
	previousContextAsSystem: true,
	steeringQueue: true,
	drainSteeringAtBoundary: true,
	steeringMode: 'next_iteration',
	uiPlugins: true,
	themeName: 'navy',
	themeAccent: '#24406b',
	themeSurface: '#12203a',
	themeText: '#e6ecf5',
	themeGradient: ['#24406b', '#3a6ba5'],
	themeAdjust: 0,
	themeWallpaper: '',
	themeIcon: '',
	workers: [],
	modules: {},
	trimEnabled: true,
	trim: {}
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
