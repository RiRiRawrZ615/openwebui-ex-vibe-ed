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
	// Context window policy. The engine serves 262144 tokens, so the default is the real
	// ceiling rather than the old cautious 80000 that summarized a chat at 31 percent.
	// Context window. The whole window is always spent; there is no compact mode.
	// The separate summarise-and-drop compaction feature was removed because it derived its own
	// threshold and rewrote the transcript, which fought these settings and made the window
	// figure unreliable.
	contextWindow: number;
	replyReserve: number;
	trimToolOutput: boolean;
	keepToolTurns: number;
	tailInject: boolean;
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
	rollover: boolean;
	rolloverThreshold: number;
	rolloverMaxChars: number;
	reasoningFiles: boolean;
	reasoningOnDemand: boolean;
	reasoningExcerptChars: number;
	rolloverCarryOnce: boolean;
	engineStatus: boolean;
	engineStatusLines: number;
	engineStatusEta: boolean;
	engineStatusMaxAge: number;
	engineLogPath: string;
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
	contextWindow: 262144,
	replyReserve: 16384,
	trimToolOutput: true,
	keepToolTurns: 3,
	tailInject: true,
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
	rollover: true,
	rolloverThreshold: 0.7,
	rolloverMaxChars: 60000,
	reasoningFiles: true,
	reasoningOnDemand: true,
	reasoningExcerptChars: 160,
	rolloverCarryOnce: true,
	engineStatus: true,
	engineStatusLines: 400,
	engineStatusEta: true,
	engineStatusMaxAge: 30,
	engineLogPath: "",
	steeringQueue: true,
	drainSteeringAtBoundary: true,
	steeringMode: 'next_iteration',
	uiPlugins: true,
	themeName: 'navy',
	themeAccent: '#24406b',
	themeSurface: '#12203a',
	themeText: '#e6ecf5',
	// The page gradient, as an ordered list of stops.
	//
	// This used to default to ['#24406b', '#3a6ba5']. OpenWebVibesTheme.svelte writes whatever is
	// here onto <html> as an INLINE custom property, and inline beats every stylesheet rule, so
	// this default silently overrode --owv-gradient in src/ex_theme.css. That is why editing the
	// stylesheet changed nothing in the browser: #3a6ba5 is a saturated steel blue and it was
	// the real page background the whole time. Sampled from a screenshot, the rendered surface
	// was rgb(53,91,143) = #355b8f, which sits at about 60 percent along the old #24406b -> #3a6ba5
	// ramp. The default now matches the stylesheet exactly, so the two cannot disagree.
	//
	// Keep in sync with --owv-gradient in src/ex_theme.css and OWV_GRADIENT_HEX in exPalette.ts.
	themeGradient: ['#0a1421', '#101d2e', '#17293d'],
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
