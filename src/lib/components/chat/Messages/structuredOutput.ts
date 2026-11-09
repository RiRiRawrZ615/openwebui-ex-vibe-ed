import { encode } from 'html-entities';

// Set by the renderer from the resolved ex settings. A plain module variable so this file stays a
// pure formatter with no store dependency.
export let thinkingDisplayMode = 'collapsed';

export function setThinkingDisplayMode(mode: string): void {
	thinkingDisplayMode = mode === 'list' || mode === 'expand' ? mode : 'collapsed';
}

export type OutputContentPart = {
	type?: string;
	text?: unknown;
	[key: string]: unknown;
};

export type OutputItem = {
	type?: string;
	id?: string;
	call_id?: string;
	name?: string;
	status?: string;
	arguments?: unknown;
	content?: OutputContentPart[];
	summary?: OutputContentPart[] | string;
	output?: OutputContentPart[];
	files?: unknown;
	embeds?: unknown;
	code?: string;
	lang?: string;
	duration?: number | string | null;
	action?: Record<string, unknown>;
	actions?: Array<Record<string, unknown>>;
	queries?: unknown[];
	[key: string]: unknown;
};

export type OutputDetailToken = {
	summary: string;
	text: string;
	attributes: {
		type: string;
		id?: string;
		name?: string;
		done?: string;
		duration?: string;
		arguments?: string;
		files?: string;
		embeds?: string;
		output?: string;
		status?: string;
	};
};

export type OutputDisplayItem =
	| {
			type: 'message';
			id: string;
			text: string;
	  }
	| {
			type: 'image';
			id: string;
			src: string;
	  }
	| {
			type: 'detail_single';
			id: string;
			token: OutputDetailToken;
	  }
	| {
			type: 'detail_group';
			id: string;
			tokens: OutputDetailToken[];
	  }
	| {
			// Thinking, rendered inline in the message flow rather than inside the
			// collapsed activity card, so reasoning stays readable next to the answer.
			type: 'reasoning';
			id: string;
			token: OutputDetailToken;
	  }
	| {
			type: 'file';
			id: string;
			item: Record<string, unknown>;
	  }
	| {
			type: 'context_compaction';
			id: string;
			summary: string;
	  };

type ResponseStreamEvent = {
	type?: string;
	item_id?: string;
	output_index?: number;
	content_index?: number;
	summary_index?: number;
	item?: OutputItem;
	part?: OutputContentPart;
	delta?: unknown;
	text?: unknown;
	arguments?: unknown;
	response?: {
		output?: OutputItem[];
		[key: string]: unknown;
	};
	[key: string]: unknown;
};

// Tool and action types that belong in the collapsed activity card.
//
// 'reasoning' is deliberately NOT here. Thinking is part of how the answer reads: the user
// wants thinking, then the response, then thinking again, with only the tool calls kept in a
// separate collapsed surface. Grouping reasoning with tool calls buried the model's actual
// reasoning inside the thing it was reasoning about, which is the opposite of useful.
const GROUPABLE_OUTPUT_TYPES = new Set([
	'function_call',
	'open_webui:code_interpreter',
	'web_search_call',
	'file_search_call',
	'computer_call'
]);

const OPENAI_TOOL_NAMES: Record<string, string> = {
	web_search_call: 'Web Search',
	file_search_call: 'File Search',
	computer_call: 'Computer Use'
};

function getTextFromParts(parts: OutputContentPart[] = []): string {
	return parts
		.map((part) => {
			if (part?.text === undefined || part?.text === null) {
				return '';
			}
			return typeof part.text === 'string' ? part.text : String(part.text);
		})
		.join('');
}

function stringifyAttribute(value: unknown): string {
	if (value === undefined || value === null) {
		return '';
	}
	if (typeof value === 'string') {
		return value;
	}
	try {
		return JSON.stringify(value);
	} catch {
		return String(value);
	}
}

function isDoneStatus(status?: string): boolean {
	return status === 'completed' || status === 'failed' || status === 'incomplete';
}

function getMessageText(item: OutputItem): string {
	return getTextFromParts(item.content ?? []);
}

function getReasoningText(item: OutputItem): string {
	const summary = Array.isArray(item.summary) && item.summary.length ? item.summary : null;
	return getTextFromParts(summary ?? item.content ?? []);
}

function getToolResultText(item?: OutputItem): string {
	return (item?.output ?? [])
		.filter((part) => part?.type !== 'input_image')
		.map((part) => {
			if (part?.text === undefined || part?.text === null) {
				return '';
			}
			return typeof part.text === 'string' ? part.text : String(part.text);
		})
		.join('');
}

function parseJSONStringValue(value: unknown): unknown {
	if (typeof value !== 'string') {
		return value;
	}

	let parsed: unknown = value.trim();
	while (typeof parsed === 'string') {
		try {
			parsed = JSON.parse(parsed);
		} catch {
			break;
		}
	}
	return parsed;
}

function getInlineFileFromToolOutput(callItem?: OutputItem, resultItem?: OutputItem) {
	if (!callItem || !resultItem || callItem.name !== 'display_file') {
		return null;
	}

	const args = parseJSONStringValue(callItem.arguments) as Record<string, unknown>;
	if (!args || typeof args !== 'object') {
		return null;
	}

	const result = parseJSONStringValue(getToolResultText(resultItem)) as Record<string, unknown>;
	const isTerminalFile = result?.source === 'open_terminal' && !!result.terminal_selector;
	const isOpenWebUIFile = result?.source === 'open_webui';
	if (
		!result ||
		typeof result !== 'object' ||
		result.type !== 'file' ||
		!result.path ||
		result.exists === false ||
		!(isTerminalFile || isOpenWebUIFile)
	) {
		return null;
	}

	return result.page === undefined && args.page !== undefined
		? { ...result, page: args.page }
		: result;
}

function buildToolCallToken(item: OutputItem, toolOutputByCallId: Record<string, OutputItem>) {
	const callId = item.call_id ?? item.id ?? '';
	const resultItem = toolOutputByCallId[callId];
	const status = String(item.status ?? '');
	const isPending = status === 'pending';
	const isDone = !!resultItem || status === 'failed' || status === 'incomplete';
	const isExecuting = !isDone && status === 'completed';
	let name = item.name ?? '';
	if (name === 'delegate_task') {
		try {
			const args =
				typeof item.arguments === 'string'
					? JSON.parse(item.arguments || '{}')
					: (item.arguments ?? {});
			const task = typeof args.task === 'string' && args.task ? args.task : '?';
			const label = args.background ? 'Background sub-agent' : 'Sub-agent';
			name = `${label}: "${task.length > 60 ? `${task.slice(0, 60)}...` : task}"`;
		} catch {
			name = 'Sub-agent';
		}
	}

	return {
		summary: isPending
			? 'Tool Approval Needed'
			: isDone
				? 'Tool Executed'
				: isExecuting
					? 'Executing...'
					: 'Preparing...',
		text: getToolResultText(resultItem),
		attributes: {
			type: 'tool_calls',
			id: callId,
			name,
			done: isDone ? 'true' : 'false',
			status,
			// ToolCallDisplay HTML-decodes these, like legacy <details> attributes.
			arguments: encode(stringifyAttribute(item.arguments ?? '')),
			files: encode(stringifyAttribute(resultItem?.files)),
			embeds: encode(stringifyAttribute(resultItem?.embeds))
		}
	};
}

function buildReasoningToken(item: OutputItem, isLastItem: boolean) {
	const duration = item.duration ?? '';
	const isDone = isDoneStatus(item.status) || item.duration !== undefined || !isLastItem;
	const text = getReasoningText(item)
		.split('\n')
		.map((line) => (line.startsWith('>') ? line : `> ${line}`))
		.join('\n');

	return {
		summary: isDone ? `Thought for ${duration || 0} seconds` : 'Thinking...',
		text,
		attributes: {
			type: 'reasoning',
			done: isDone ? 'true' : 'false',
			duration: String(duration)
		}
	};
}

function buildCodeInterpreterToken(item: OutputItem, isLastItem: boolean) {
	const duration = item.duration ?? '';
	const isDone = isDoneStatus(item.status) || item.duration !== undefined || !isLastItem;
	const code = item.code ?? '';
	const lang = item.lang ?? 'python';

	return {
		summary: isDone ? 'Analyzed' : 'Analyzing...',
		text: code ? `\`\`\`${lang}\n${code}\n\`\`\`` : '',
		attributes: {
			type: 'code_interpreter',
			done: isDone ? 'true' : 'false',
			duration: String(duration),
			output: stringifyAttribute(item.output)
		}
	};
}

function getOpenAIToolSummary(item: OutputItem): string {
	if (item.type === 'web_search_call') {
		const action = item.action ?? {};
		const actionType = action.type;
		if (actionType === 'search') {
			const queries = Array.isArray(action.queries) ? action.queries : [];
			const query = typeof action.query === 'string' ? action.query : '';
			return queries.length ? `Search: ${queries.join(', ')}` : query ? `Search: ${query}` : '';
		}
		if (actionType === 'open_page' && typeof action.url === 'string') {
			return `Open page: ${action.url}`;
		}
		if (actionType === 'find_in_page' && typeof action.pattern === 'string') {
			return `Find in page: ${action.pattern}`;
		}
	}

	if (item.type === 'file_search_call') {
		const queries = item.queries ?? [];
		return queries.length ? `Queries: ${queries.join(', ')}` : '';
	}

	if (item.type === 'computer_call') {
		if (item.action?.type) {
			return `Action: ${item.action.type}`;
		}
		if (Array.isArray(item.actions) && item.actions.length) {
			return `Actions: ${item.actions.map((action) => action.type ?? '?').join(', ')}`;
		}
	}

	return '';
}

function buildOpenAIToolToken(item: OutputItem, isLastItem: boolean) {
	const isDone = isDoneStatus(item.status) || !isLastItem;
	return {
		summary: isDone ? 'Tool Executed' : 'Executing...',
		text: getOpenAIToolSummary(item),
		attributes: {
			type: 'tool_calls',
			id: item.id ?? '',
			name: OPENAI_TOOL_NAMES[item.type ?? ''] ?? item.type ?? '',
			done: isDone ? 'true' : 'false',
			arguments: ''
		}
	};
}

function buildDetailToken(
	item: OutputItem,
	isLastItem: boolean,
	toolOutputByCallId: Record<string, OutputItem>
): OutputDetailToken | null {
	if (item.type === 'function_call') {
		return buildToolCallToken(item, toolOutputByCallId);
	}
	if (item.type === 'reasoning') {
		return buildReasoningToken(item, isLastItem);
	}
	if (item.type === 'open_webui:code_interpreter') {
		return buildCodeInterpreterToken(item, isLastItem);
	}
	if (item.type && OPENAI_TOOL_NAMES[item.type]) {
		return buildOpenAIToolToken(item, isLastItem);
	}
	return null;
}

export function buildOutputDisplayItems(output: OutputItem[] = []): OutputDisplayItem[] {
	const displayItems: OutputDisplayItem[] = [];
	const currentDetailTokens: OutputDetailToken[] = [];
	const toolOutputByCallId: Record<string, OutputItem> = {};
	const toolCallByCallId: Record<string, OutputItem> = {};

	// Tool activity is hoisted out of the message flow and emitted once at the end.
	//
	// This is the real render path for a chat response (ResponseMessage -> ContentRenderer ->
	// StructuredOutputRenderer), so changing the markdown token path does not affect it.
	//
	// Previously each block of detail tokens was flushed inline, at the position where it
	// happened. An agentic turn alternates prose and calls, so the message read as
	// paragraph, call row, paragraph, call row, each row carrying its own full-width
	// Input/Output panel. The result was that the answer was buried under the transcript of
	// producing it.
	//
	// Now every detail token is collected into one list and emitted as a single
	// detail_group after all message text. The body stays readable prose; the whole turn's
	// activity is one collapsible block at the bottom, in original order, and each call is
	// still individually inspectable inside it.
	const hoistedDetailTokens: OutputDetailToken[] = [];

	for (const item of output) {
		if (item?.type === 'function_call_output' && item.call_id) {
			toolOutputByCallId[item.call_id] = item;
		} else if (item?.type === 'function_call' && (item.call_id || item.id)) {
			toolCallByCallId[item.call_id ?? item.id ?? ''] = item;
		}
	}

	// Kept as a named no-op so the call sites below stay readable and the inline-flush
	// behaviour is trivially restorable. Nothing is emitted here any more: detail tokens
	// are appended to hoistedDetailTokens and emitted once, after the whole output has been
	// walked, by the single flush at the end of this function.
	const flushDetails = () => {};

	output.forEach((item, index) => {
		if (!item) {
			return;
		}

		if (item.type === 'function_call_output') {
			const inlineFile = getInlineFileFromToolOutput(toolCallByCallId[item.call_id ?? ''], item);
			if (inlineFile) {
				flushDetails();
				displayItems.push({
					type: 'file',
					id: item.id ?? `file-${index}`,
					item: inlineFile
				});
			}
			return;
		}

		if (
			item.type === 'function_call' &&
			item.name === 'ask_user' &&
			(item.status === 'pending' || item.status === 'in_progress')
		) {
			return;
		}

		if (item.type && GROUPABLE_OUTPUT_TYPES.has(item.type)) {
			const token = buildDetailToken(item, index === output.length - 1, toolOutputByCallId);
			if (token) {
				hoistedDetailTokens.push(token);
			}
			return;
		}

		// Reasoning is kept in the message flow rather than in the activity card, so the
		// transcript reads thinking, response, thinking, while the tool calls sit apart.
		// It renders through the existing Collapsible, collapsed by default, which is what
		// produces the familiar 'Thought for N seconds' line.
		if (item.type === 'reasoning') {
			if (getReasoningText(item).trim()) {
				const token = buildReasoningToken(item, index === output.length - 1);
				// Three presentations of the same group. The grouping already existed; what was missing
				// was control over it, so a long agentic turn produced one collapsible per reasoning
				// block and the chat became a wall of thinking headers.
				if (thinkingDisplayMode === 'list') {
					token.text = String(token.text ?? '')
						.split('\n\n')
						.map((block) => block.split('\n')[0].slice(0, 220))
						.join('\n');
				}
				(token as OutputDetailToken & { open?: boolean }).open = thinkingDisplayMode === 'expand';
				displayItems.push({
					type: 'reasoning',
					id: item.id ?? `reasoning-${index}`,
					token
				});
			}
			return;
		}

		if (item.type === 'open_webui:context_compaction') {
			flushDetails();
			displayItems.push({
				type: 'context_compaction',
				id: item.id ?? `context-compaction-${index}`,
				summary: typeof item.summary === 'string' ? item.summary : ''
			});
			return;
		}

		if (item.type === 'message') {
			const text = getMessageText(item);
			if (text.trim()) {
				flushDetails();
				displayItems.push({
					type: 'message',
					id: item.id ?? `message-${index}`,
					text
				});
			}
			return;
		}

		if (item.type === 'image_generation_call') {
			if (typeof item.result === 'string' && item.result) {
				flushDetails();
				displayItems.push({
					type: 'image',
					id: item.id ?? `image-${index}`,
					src: item.result.startsWith('data:')
						? item.result
						: `data:image/${item.output_format || 'png'};base64,${item.result}`
				});
			}
			return;
		}

		const fallbackText = getMessageText(item);
		if (fallbackText.trim()) {
			flushDetails();
			displayItems.push({
				type: 'message',
				id: item.id ?? `output-${index}`,
				text: fallbackText
			});
		}
	});

	// The single flush for the whole turn. Everything the turn did is one block, appended
	// after the last piece of message text.
	if (hoistedDetailTokens.length > 0) {
		displayItems.push({
			type: 'detail_group',
			id: `detail-group-${displayItems.length}`,
			tokens: hoistedDetailTokens
		});
	}
	currentDetailTokens.length = 0;
	const seenIds = new Set<string>();
	displayItems.forEach((displayItem, index) => {
		if (seenIds.has(displayItem.id)) {
			displayItem.id = `${displayItem.id}-${index}`;
		}
		seenIds.add(displayItem.id);
	});

	return displayItems;
}

export function getOutputText(output?: OutputItem[] | null): string {
	return (output ?? [])
		.filter((item) => item?.type === 'message')
		.map(getMessageText)
		.filter((text) => text.trim())
		.join('\n');
}

/**
 * Streaming text used for measurement only, never for rendering.
 *
 * getOutputText filters to type 'message', so a turn that is still thinking contributes nothing.
 * The arrival sampler and the live token estimate both read this, which is why the live row could
 * show a token count of zero while a thinking block was visibly growing on screen.
 */
export function getReasonedText(output?: OutputItem[] | null): string {
	return (output ?? [])
		.filter((item) => item?.type === 'reasoning')
		.map(getReasoningText)
		.filter((text) => text.trim())
		.join('\n');
}

function appendDelta(current: unknown, delta: unknown): unknown {
	if (typeof current === 'string' || typeof delta === 'string') {
		return `${current ?? ''}${delta ?? ''}`;
	}
	if (
		current &&
		delta &&
		typeof current === 'object' &&
		typeof delta === 'object' &&
		!Array.isArray(current) &&
		!Array.isArray(delta)
	) {
		return { ...(current as Record<string, unknown>), ...(delta as Record<string, unknown>) };
	}
	return delta ?? current ?? '';
}

function ensureOutputItem(
	output: OutputItem[],
	outputIndex: number,
	fallback?: OutputItem
): OutputItem {
	while (output.length <= outputIndex) {
		// Only the addressed slot gets the event's item; filler slots must not reuse its id.
		const item =
			output.length === outputIndex && fallback
				? { ...fallback }
				: { type: 'message', status: 'in_progress', role: 'assistant', content: [] };
		output.push(item);
	}
	output[outputIndex] = { ...output[outputIndex] };
	return output[outputIndex];
}

function ensurePart(parts: OutputContentPart[], index: number, fallback?: OutputContentPart) {
	while (parts.length <= index) {
		parts.push(fallback ?? { type: 'output_text', text: '' });
	}
	parts[index] = { ...parts[index] };
	return parts[index];
}

function setPart(
	parts: OutputContentPart[],
	index: number,
	part: OutputContentPart,
	fallback?: OutputContentPart
): void {
	// Assigning past the end leaves a hole that later spreads turn into undefined parts.
	ensurePart(parts, index, fallback);
	parts[index] = part;
}

function findOutputItemIndex(output: OutputItem[], item: OutputItem): number {
	return output.findIndex(
		(existing) =>
			(!!item.id && existing?.id === item.id) ||
			(!!item.call_id && existing?.type === item.type && existing?.call_id === item.call_id)
	);
}

function responseEventUpdatesOutputItem(eventType: string): boolean {
	return (
		eventType === 'response.content_part.added' ||
		eventType === 'response.reasoning_summary_part.added' ||
		eventType.endsWith('.delta') ||
		eventType.endsWith('.done')
	);
}

export function applyResponseStreamEvent(
	output: OutputItem[] = [],
	event: ResponseStreamEvent
): OutputItem[] {
	const eventType = event?.type ?? '';
	if (!eventType.startsWith('response.')) {
		return output;
	}

	if (eventType === 'response.completed') {
		if (!event.response?.output?.length) return output;

		// Completion covers one provider response, not the earlier tool-call rounds.
		const nextOutput = [...output];
		for (const item of event.response.output) {
			const index = findOutputItemIndex(nextOutput, item);
			if (index >= 0) {
				nextOutput[index] = item;
			} else {
				nextOutput.push(item);
			}
		}
		return nextOutput;
	}

	const nextOutput = [...output];
	const eventItemIndex = event.item_id
		? nextOutput.findIndex((item) => item?.id === event.item_id || item?.call_id === event.item_id)
		: -1;
	const outputIndex =
		eventItemIndex >= 0 ? eventItemIndex : (event.output_index ?? Math.max(output.length - 1, 0));

	if (eventType === 'response.output_item.added') {
		if (!event.item) {
			return output;
		}
		const item = { ...event.item };
		const existingIndex = findOutputItemIndex(nextOutput, item);
		if (existingIndex >= 0) {
			nextOutput[existingIndex] = item;
		} else if (outputIndex < nextOutput.length) {
			nextOutput.splice(outputIndex, 0, item);
		} else {
			nextOutput.push(item);
		}
		return nextOutput;
	}

	if (eventType === 'response.output_item.done') {
		if (!event.item) {
			return output;
		}
		const item = { ...event.item };
		const existingIndex = findOutputItemIndex(nextOutput, item);
		if (existingIndex >= 0) {
			nextOutput[existingIndex] = item;
		} else if (outputIndex < nextOutput.length) {
			nextOutput[outputIndex] = item;
		} else {
			nextOutput.push(item);
		}
		return nextOutput;
	}

	if (!responseEventUpdatesOutputItem(eventType)) {
		return output;
	}

	const item = ensureOutputItem(nextOutput, outputIndex, {
		id: event.item_id,
		type: eventType.includes('reasoning')
			? 'reasoning'
			: eventType.includes('function_call')
				? 'function_call'
				: 'message',
		status: 'in_progress',
		role: 'assistant',
		content: []
	});

	if (eventType === 'response.content_part.added') {
		if (item.type === 'reasoning' || !event.part) {
			return nextOutput;
		}
		item.content = [...(item.content ?? [])];
		setPart(item.content, event.content_index ?? item.content.length, { ...event.part });
		return nextOutput;
	}

	if (eventType === 'response.reasoning_summary_part.added') {
		if (!event.part) {
			return nextOutput;
		}
		item.summary = Array.isArray(item.summary) ? [...item.summary] : [];
		const summaryIndex = event.summary_index ?? item.summary.length;
		setPart(item.summary, summaryIndex, { ...event.part }, { type: 'summary_text', text: '' });
		return nextOutput;
	}

	if (eventType.endsWith('.delta')) {
		const deltaType = eventType.split('.')[1];
		if (deltaType === 'function_call_arguments') {
			item.arguments = appendDelta(item.arguments ?? '', event.delta);
			return nextOutput;
		}

		if (deltaType === 'reasoning_summary_text') {
			const summaryIndex = event.summary_index ?? 0;
			item.summary = Array.isArray(item.summary) ? [...item.summary] : [];
			const part = ensurePart(item.summary, summaryIndex, { type: 'summary_text', text: '' });
			part.text = appendDelta(part.text ?? '', event.delta);
			return nextOutput;
		}

		if (item.type === 'open_webui:code_interpreter') {
			item.code = `${item.code ?? ''}${event.delta ?? ''}`;
			return nextOutput;
		}

		const key = deltaType === 'output_text' || deltaType === 'reasoning_text' ? 'text' : deltaType;
		item.content = [...(item.content ?? [])];
		const part = ensurePart(item.content, event.content_index ?? 0);
		part[key] = appendDelta(part[key], event.delta);
		return nextOutput;
	}

	if (eventType.endsWith('.done')) {
		const typeName = eventType.split('.')[1];
		if (typeName === 'content_part' && event.part) {
			item.content = [...(item.content ?? [])];
			const contentIndex = event.content_index ?? Math.max(item.content.length - 1, 0);
			setPart(item.content, contentIndex, { ...event.part });
		} else if (typeName === 'function_call_arguments' && event.arguments !== undefined) {
			item.arguments = event.arguments;
		} else if (
			(typeName === 'output_text' || typeName === 'text' || typeName === 'reasoning_text') &&
			event.text !== undefined
		) {
			item.content = [...(item.content ?? [])];
			const part = ensurePart(item.content, event.content_index ?? 0);
			part.text = event.text;
		}
	}

	return nextOutput;
}

export function replaceOutputMessageText(
	output: OutputItem[] = [],
	oldContent: string,
	newContent: string
): OutputItem[] {
	if (!oldContent) {
		return output;
	}

	let replaced = false;
	const nextOutput = output.map((item) => {
		if (replaced || item?.type !== 'message' || !Array.isArray(item.content)) {
			return item;
		}

		const partIndex = item.content.findIndex(
			(part) => typeof part?.text === 'string' && part.text.includes(oldContent)
		);
		if (partIndex === -1) {
			return item;
		}

		replaced = true;
		const nextContent = [...item.content];
		const part = nextContent[partIndex];
		nextContent[partIndex] = {
			...part,
			text: (part.text as string).replace(oldContent, () => newContent)
		};

		return {
			...item,
			content: nextContent
		};
	});

	return replaced ? nextOutput : output;
}

export function setOutputText(output: OutputItem[], text: string): OutputItem[] {
	if (text === getOutputText(output)) {
		return output;
	}

	const lastMessage = output.filter((item) => item?.type === 'message').at(-1);
	if (!lastMessage) {
		return [
			...output,
			{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text }] }
		];
	}

	return output.map((item) =>
		item?.type === 'message'
			? { ...item, content: item === lastMessage ? [{ type: 'output_text', text }] : [] }
			: item
	);
}
