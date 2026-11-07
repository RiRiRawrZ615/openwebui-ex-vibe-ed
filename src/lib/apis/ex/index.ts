import { WEBUI_API_BASE_URL } from '$lib/constants';

export type LauncherStatus = {
	present: boolean;
	backend: string | null;
	model: string | null;
	locked: Record<string, unknown>;
	stale: boolean;
};

export type ExStatusResponse = {
	settings: Record<string, unknown>;
	launcher: LauncherStatus;
	data_dir: string;
};

export type JournalIndexEntry = { id: number; turn: number; kind: string };

export type ExJournalResponse = {
	path: string;
	entries?: JournalIndexEntry[];
	content?: string;
};

export type ExImportReport = {
	tables: Record<string, { rows: number; attempted?: number; columns: number }>;
	skipped: string[];
	rows: number;
	error?: string;
};

const jsonRequest = async (url: string, token: string, method = 'GET', body?: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}${url}`, {
		method,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		...(body ? { body: JSON.stringify(body) } : {})
	})
		.then((res) => res.json())
		.then((json) => {
			if (json && json.error) error = json.error;
			return json;
		})
		.catch((err) => {
			error = `${err}`;
		});

	return error ? { error } : res;
};

export type ExBackendCheck = {
	selected: string | null;
	reason: string;
	candidates: string[];
	model: Record<string, unknown>;
	profile?: Record<string, unknown> | null;
	roots: string[];
	available_backends: string[];
};

export type ExInstallPlan = {
	dry_run: boolean;
	runs: boolean;
	steps: { component: string; action: string; note: string }[];
	refused: string[];
};

/** One component's discovery result, as returned by the backend probe. */
export type ExDiscoverReport = {
	component: string;
	found: boolean;
	path: string;
	/** 'path' when it came from PATH, 'candidate' from a known location, 'missing'. */
	source: 'path' | 'candidate' | 'manual' | 'missing' | string;
	executable: string;
	candidates_tried: string[];
	/** One-line plain language summary, built server side so both sides agree. */
	hint: string;
};

/**
 * The full probe.
 *
 * `missing` is the setup workflow's trigger: a fresh machine is the case where it is not
 * empty, and there is nothing else to detect.
 */
export type ExDiscover = {
	components: Record<string, ExDiscoverReport>;
	missing: string[];
	ready: boolean;
};

/** Result of validating a manually entered path. */
export type ExManualSelection = {
	component: string;
	accepted: boolean;
	path: string;
	source?: string;
	executable?: string;
	reason: string;
};

export const getExStatus = async (token: string): Promise<ExStatusResponse | { error: string }> =>
	jsonRequest('/ex/status', token);

export type EngineStatusLine = {
	phase: string;
	tool?: string;
	tokens?: number;
	budget?: number;
	speed?: number;
	no_speed?: boolean;
	elapsed?: number;
	line: string;
};

export type EngineStatusResponse = {
	source: string;
	path?: string;
	live: boolean;
	age_seconds?: number | null;
	status?: EngineStatusLine | null;
	last_done?: EngineStatusLine | null;
	lines_read?: number;
	ceiling?: { value: number; source: string } | null;
	eta?: { remaining_tokens: number; seconds: number; based_on: string } | null;
	error?: string;
};

// Engine status comes from the log the launcher writes, not from the engine. The figures are the
// engine's own, and the response keeps its source so a frontend arrival estimate and an engine rate
// cannot be read as the same number.
export const getExEngineStatus = async (token: string): Promise<EngineStatusResponse | { error: string }> =>
	jsonRequest('/ex/engine', token);

export const getExBackendCheck = async (
	token: string,
	modelPath: string
): Promise<ExBackendCheck | { error: string }> =>
	jsonRequest(`/ex/backend?${new URLSearchParams({ model: modelPath }).toString()}`, token);

export const getExInstallPlan = async (
	token: string,
	components: string[]
): Promise<ExInstallPlan | { error: string }> =>
	jsonRequest(`/ex/install-plan?${new URLSearchParams({ components: components.join(',') }).toString()}`, token);

/**
 * What this machine already has.
 *
 * Backs the setup workflow: a fresh machine is simply the case where `missing` is
 * non-empty. The backend only stats files, so this is cheap enough to call on mount.
 */
export const getExDiscover = async (
	token: string,
	components: string[] = []
): Promise<ExDiscover | { error: string }> => {
	const params = new URLSearchParams();
	if (components.length > 0) params.set('components', components.join(','));
	const query = params.toString();
	return jsonRequest(`/ex/discover${query ? `?${query}` : ''}`, token);
};

/** Probe a single component. */
export const getExDiscoverOne = async (
	token: string,
	component: string
): Promise<ExDiscoverReport | { error: string }> =>
	jsonRequest(`/ex/discover/${encodeURIComponent(component)}`, token);

/**
 * Validate a manually entered binary path.
 *
 * Discovery cannot cover every layout, so the setup workflow offers an override. The
 * check is deliberately narrow: exists, is a file, is executable. Nothing is run, and the
 * backend refuses any component that is not discoverable.
 */
export const postExDiscoverManual = async (
	token: string,
	component: string,
	path: string
): Promise<ExManualSelection | { error: string }> =>
	jsonRequest(
		`/ex/discover/${encodeURIComponent(component)}/manual?${new URLSearchParams({ path }).toString()}`,
		token,
		'POST'
	);

export const readExJournal = async (
	token: string,
	chatId: string,
	{ entry = null, needle = '', limit = 12 }: { entry?: number | null; needle?: string; limit?: number } = {}
): Promise<ExJournalResponse | { error: string }> => {
	const params = new URLSearchParams({ chat_id: chatId, limit: String(limit) });
	if (entry !== null) params.set('entry', String(entry));
	if (needle) params.set('needle', needle);
	return jsonRequest(`/ex/journal?${params.toString()}`, token);
};

export type ExThemeResponse = {
	theme: Record<string, unknown>;
	css: string;
};

export const getExTheme = async (token: string): Promise<ExThemeResponse | { error: string }> =>
	jsonRequest('/ex/theme', token);

export const getExRecovery = async (
	token: string,
	chatId = ''
): Promise<Record<string, any> | { error: string }> => {
	const params = new URLSearchParams({ chat_id: chatId });
	return jsonRequest(`/ex/recovery?${params.toString()}`, token);
};

export const getExModules = async (token: string): Promise<Record<string, any> | { error: string }> =>
	jsonRequest('/ex/modules', token);

export const getExCluster = async (token: string): Promise<Record<string, any> | { error: string }> =>
	jsonRequest('/ex/cluster', token);

export type ExBatchSummary = {
	action: string;
	selected: number;
	would_apply: number;
	refused: string[];
	blocked: string[];
	applied?: string[];
	reason: string;
	ok: boolean;
};

export const runExBatch = async (
	token: string,
	{
		action,
		chatIds,
		folderId = '',
		dryRun = true,
		force = false
	}: { action: string; chatIds: string[]; folderId?: string; dryRun?: boolean; force?: boolean }
): Promise<ExBatchSummary | { error: string }> => {
	const params = new URLSearchParams({
		action,
		chat_ids: chatIds.join(','),
		dry_run: String(dryRun),
		force: String(force)
	});
	if (folderId) params.set('folder_id', folderId);
	return jsonRequest(`/ex/batch?${params.toString()}`, token, 'POST');
};

export const runExImport = async (
	token: string,
	{
		source,
		target = '',
		dryRun = true,
		force = false
	}: { source: string; target?: string; dryRun?: boolean; force?: boolean }
): Promise<ExImportReport | { error: string }> => {
	const params = new URLSearchParams({ source, dry_run: String(dryRun), force: String(force) });
	if (target) params.set('target', target);
	return jsonRequest(`/ex/import?${params.toString()}`, token, 'POST');
};

export type ExContextReport = {
	settings: Record<string, unknown>;
	stored_path: string;
};

export type ExSteeringReport = {
	settings: Record<string, unknown>;
	items: Array<Record<string, unknown>>;
};

export type ExUiReport = {
	settings: Record<string, unknown>;
	resolved: {
		slots: Array<{
			slot: string;
			plugin: string;
			component: string;
			fallback: string;
			resolved: string;
			state: string;
		}>;
		refused: Array<Record<string, unknown>>;
		plugin_root: string;
		core_slots: string[];
		counts: Record<string, number>;
		host_note: string;
	};
};

export const getExContext = async (token: string, chatId = ''): Promise<ExContextReport | { error: string }> => {
	const params = new URLSearchParams({ chat_id: chatId });
	return jsonRequest(`/ex/context?${params.toString()}`, token);
};

export const getExSteering = async (token: string, chatId = ''): Promise<ExSteeringReport | { error: string }> => {
	const params = new URLSearchParams({ chat_id: chatId });
	return jsonRequest(`/ex/steering?${params.toString()}`, token);
};

export const addExSteering = async (
	token: string,
	{
		text,
		chatId = '',
		mode = 'next_iteration',
		action = 'inject_instruction',
		tool = '',
		priority = 'normal'
	}: { text: string; chatId?: string; mode?: string; action?: string; tool?: string; priority?: string }
): Promise<{ item: Record<string, unknown>; pending: number } | { error: string }> => {
	const params = new URLSearchParams({
		text,
		chat_id: chatId,
		mode,
		action,
		tool,
		priority
	});
	return jsonRequest(`/ex/steering?${params.toString()}`, token, 'POST');
};

export const getExUi = async (token: string): Promise<ExUiReport | { error: string }> => {
	return jsonRequest('/ex/ui', token);
};
export type ExTrimReport = {
	settings: Record<string, unknown>;
	resolved: { gate: Record<string, boolean>; refused_overrides: Array<Record<string, unknown>> };
};

export const getExTrim = async (token: string): Promise<ExTrimReport | { error: string }> => {
	return jsonRequest('/ex/trim', token);
};

export type ExMemoryHit = {
	text: string;
	score: number;
	role?: string;
	chat_id?: string;
	message_id?: string;
};

export type ExMemoryStatus = {
	available: boolean;
	reason?: string | null;
	collection?: string;
	embed_model?: string;
	dimensions?: number;
	path?: string;
	chat_count?: number;
};

export type ExMemorySearch = {
	query?: string;
	hits: ExMemoryHit[];
	block?: string | null;
	error?: string | null;
};

export const getExMemory = async (
	token: string,
	chatId = ''
): Promise<ExMemoryStatus | { error: string }> => {
	const params = new URLSearchParams();
	if (chatId) params.set('chat_id', chatId);
	const qs = params.toString();
	return jsonRequest(`/ex/memory${qs ? `?${qs}` : ''}`, token);
};

export const searchExMemory = async (
	token: string,
	query: string,
	chatId = '',
	topK = 6
): Promise<ExMemorySearch | { error: string }> => {
	const params = new URLSearchParams({ q: query });
	if (chatId) params.set('chat_id', chatId);
	params.set('top_k', String(topK));
	return jsonRequest(`/ex/memory/search?${params.toString()}`, token);
};

export const forgetExMemory = async (
	token: string,
	chatId: string
): Promise<{ deleted: number } | { error: string }> => {
	return jsonRequest(`/ex/memory?chat_id=${encodeURIComponent(chatId)}`, token, 'DELETE');
};

/** One shipped builtin tool, as reported by the registry. */
export type ExBuiltin = {
	slug: string;
	name: string;
	tool_id: string;
	/** Function names this tool exposes to the model. */
	specs: string[];
	enabled: boolean;
	/** 'builtin' when running the shipped file, 'overlay' when a user edit is in place. */
	source: 'builtin' | 'overlay' | string;
	has_overlay: boolean;
	overlay_stale: boolean;
	based_on?: string;
	builtin_hash?: string;
	updated_at?: number;
	/** Populated instead of throwing, so one broken tool cannot blank the whole tab. */
	load_error: string | null;
};

export type ExBuiltinList = {
	builtins: ExBuiltin[];
	can_edit: boolean;
};

/** Detail view, which additionally carries the source for the editor. */
export type ExBuiltinDetail = ExBuiltin & {
	source_text: string;
	source_origin: string;
	is_admin: boolean;
};

// --- Builtin tools -----------------------------------------------------------------------

export const getExBuiltins = async (token: string): Promise<ExBuiltinList | { error: string }> =>
	jsonRequest('/ex/builtins', token);

export const getExBuiltin = async (
	token: string,
	slug: string
): Promise<ExBuiltinDetail | { error: string }> =>
	jsonRequest(`/ex/builtins/${encodeURIComponent(slug)}`, token);

export const setExBuiltinEnabled = async (
	token: string,
	slug: string,
	enabled: boolean
): Promise<ExBuiltin | { error: string }> =>
	jsonRequest(
		`/ex/builtins/${encodeURIComponent(slug)}/enabled?${new URLSearchParams({ enabled: String(enabled) }).toString()}`,
		token,
		'POST'
	);

/**
 * Save a whole-file user edit. The backend compile-checks the source and returns 400 on a
 * syntax error, so a broken save never reaches disk.
 */
export const saveExBuiltinOverlay = async (
	token: string,
	slug: string,
	source: string
): Promise<ExBuiltin | { error: string }> =>
	jsonRequest(`/ex/builtins/${encodeURIComponent(slug)}/overlay`, token, 'PUT', { source });

/** Drop the user edit and fall back to the shipped builtin. */
export const revertExBuiltinOverlay = async (
	token: string,
	slug: string
): Promise<ExBuiltin | { error: string }> =>
	jsonRequest(`/ex/builtins/${encodeURIComponent(slug)}/overlay`, token, 'DELETE');
