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
