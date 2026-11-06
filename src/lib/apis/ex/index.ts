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

export const getExStatus = async (token: string): Promise<ExStatusResponse | { error: string }> =>
	jsonRequest('/ex/status', token);

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
