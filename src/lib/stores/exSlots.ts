// Slot registry store. The registry is only meaningful because a host reads it, so this store is
// the piece that turns ex_ui from a data structure into something the renderer can use.
//
// Resolution comes from the backend (GET /ex/ui), which is the tested side. The frontend never
// decides on its own whether a plugin may fill a slot; it asks, and renders what the answer says.
// A disabled plugin falls back to the builtin component, so a slot is never blank.

import { writable } from 'svelte/store';
import { getExUi, type ExUiReport } from '$lib/apis/ex';

export type ResolvedSlot = {
	slot: string;
	plugin: string;
	component: string;
	fallback: string;
	resolved: string;
	state: string;
	// The backend says whether this component can be loaded at runtime. A manifest in the data
	// plugin root is validated there, but the host can only render files inside the frontend plugin
	// directory, so the flag is what keeps the UI from showing a plugin that cannot load.
	renderable: boolean;
};

export const exSlots = writable<ResolvedSlot[]>([]);
export const exUiReport = writable<ExUiReport | null>(null);

export const refreshSlots = async (token: string) => {
	const res = await getExUi(token);
	if ('error' in res) return null;

	exUiReport.set(res);
	exSlots.set(res.resolved.slots);
	return res;
};

export function slotFor(slot: string) {
	let resolved: ResolvedSlot | undefined;
	exSlots.subscribe((s) => {
		resolved = s.find((x) => x.slot === slot);
	})();
	return resolved;
}
