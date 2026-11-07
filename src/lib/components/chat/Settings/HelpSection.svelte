<!--
  Help: what this fork is, what it changed, and how the tools behave.

  Why a Help tab rather than a link to the README
  ----------------------------------------------
  The behaviours worth knowing about here are not discoverable by clicking around, and several of
  them surprise people in a bad way if they are not told: the shipped tools arrive switched on,
  web search is replaced rather than added, thinking is streamed to the page rather than hidden,
  and an edit to a tool is stored separately from the tool itself. A README is read once before
  installing; this is read at the moment someone wonders "why is it doing that", which is when it
  actually helps.

  Content rules, so this stays useful:
    - Say what changed from stock Open WebUI, since that is the question being asked.
    - Give the concrete symptom where there is one, so it is findable by someone who has only
      noticed the symptom.
    - No marketing. A help page that oversells stops being trusted.

  Each entry is a { question, answer } pair so the whole thing renders from one list and adding
  an entry does not mean touching markup.
-->
<script lang="ts">
	import UserSettingSection from './UserSettingSection.svelte';

	type Entry = { question: string; answer: string; bullets?: string[] };

	// Grouped because the two audiences differ: someone using the app, and someone administering
	// an install. Mixing them makes both harder to scan.
	const using: Entry[] = [
		{
			question: 'What is Open Web Vibes?',
			answer:
				'A fork of Open WebUI built around a local inference engine. It keeps the chat interface you already know and changes how the app runs and how it talks to its backend, which is the part that made streaming, keeping stats honest, and running tools reliable.',
			bullets: [
				'Open WebUI remains the upstream project. Its licence and notices are kept intact.',
				'Strata is the inference engine the interface talks to by default.',
				'Everything runs locally. No conversation leaves the machine unless you switch on a tool that fetches from the web.'
			]
		},
		{
			question: 'Why does the reply arrive smoothly instead of all at once?',
			answer:
				'Different engines emit text differently. llama.cpp sends small, evenly spaced pieces. Strata does its work and then sends a larger burst, so the text would otherwise appear in lurches.',
			bullets: [
				'The text you read is the reply itself, unchanged. Nothing is reworded or summarised.',
				'Nothing is delayed in a way that can truncate it: what you see is what is stored in the conversation and sent back to the model next turn.'
			]
		},
		{
			question: 'Where did the blinking cursor go?',
			answer:
				'It was removed. Stock Open WebUI draws a small pulsing bar after the text while a reply is generating. With an engine that pauses between bursts, that bar made the pauses more obvious rather than less. The reply still shows as in-progress while it is arriving.'
		},
		{
			question: 'What do the numbers under a reply mean?',
			answer:
				'Totals for that turn. They are presented the way the backend reports them, so counts are grouped and rates have one decimal place.',
			bullets: [
				'in: how much prompt was sent. This is a measurement of the current request, so it is the last round only, not a running total. That is deliberate: every round of a multi-step turn re-sends the whole conversation, so adding prompt counts together would double-count the same context.',
				'out: text actually generated across the whole turn, which does accumulate.',
				'cache: prompt that was served from the engine\u2019s cache instead of being reprocessed.',
				'tok/s and prefill: generation and prompt-processing rates.',
				'window: how much of the context window is in use.'
			]
		},
		{
			question: 'Why is my thinking shown separately from the reply?',
			answer:
				'Reasoning is kept in the message, not bundled into the tool activity panel. You should be able to read the reasoning next to the answer it produced, while tool calls stay out of the way.',
			bullets: [
				'Thinking shows as a collapsed line you can expand, for example "Thought for 3 seconds".',
				'Reading order in the transcript is thinking, then reply, then thinking again where the model reasoned more than once.'
			]
		},
		{
			question: 'Why do tool calls appear in one collapsed box instead of inline?',
			answer:
				'A long turn can make dozens of calls. Drawn where they happen, they interrupt the reply so that the answer is buried in the record of producing it.',
			bullets: [
				'All of a turn\u2019s tool activity is collected into one collapsible panel at the end of the reply.',
				'The reply body stays continuous prose.',
				'Inside the panel, each individual call can still be expanded to see its input and output.'
			]
		},
		{
			question: 'A long reply got cut off mid-way. Why?',
			answer:
				'There is a limit on how many tool-call rounds one turn may take. This fork raises it well above the default so long agentic turns are not interrupted, and it can be set to unlimited explicitly. The value is read at startup, so changing it needs a restart.'
		}
	];

	const tools: Entry[] = [
		{
			question: 'The tools are already installed. Do I need to do anything?',
			answer:
				'No. Nine tools ship with the app and are enabled by default, so they work on first launch. You can switch any of them off in OpenWebVibes settings, under Builtin tools.',
			bullets: [
				'Web Search, Reddit and X Reader, YouTube Video Parser, YouTube Visual Reader',
				'Reverse Engineering helpers, Fleet Ops, Fleet SSH, Calculator',
				'Cognitive Memory Vault, which indexes your conversations for retrieval'
			]
		},
		{
			question: 'Does web search use DuckDuckGo?',
			answer:
				'No. Stock Open WebUI bundled a DuckDuckGo-backed search. That is replaced here rather than added alongside, so there is one search tool and no ambiguity about which one ran.',
			bullets: [
				'It fetches pages once and keeps them on disk, so a follow-up question about the same page does not refetch it.',
				'A page can be searched or sliced rather than pulled into context whole, which keeps a long document from consuming the window.',
				'There is a multi-source research mode for questions that need more than one page.'
			]
		},
		{
			question: 'Can I edit a builtin tool?',
			answer:
				'Yes, and edits are kept separate from the shipped tool. Saving an edit stores your own whole-file copy, which is used instead of the built-in one.',
			bullets: [
				'Because it is stored separately, updating the app does not overwrite it.',
				'The editor shows the whole file and refuses to save a syntax error, so a mistyped edit cannot leave the tool broken.',
				'If the shipped tool changes after you edited it, the row is marked "update available" so you can see that your edit is hiding a newer version.',
				'Revert to shipped discards your copy at any time.'
			]
		},
		{
			question: 'A tool is enabled but the model is not using it.',
			answer:
				'Three things have to line up: the tool group must be on, the model must be allowed to use tools, and the account must have permission. The Builtin tools panel shows the first; the other two live in Admin settings and the model\u2019s own configuration.',
			bullets: [
				'Some models are configured without tool support, in which case no tools are offered at all.',
				'A tool that failed to load is reported at the top of the Builtin tools panel rather than failing silently.'
			]
		},
		{
			question: 'What are Fleet Ops and Fleet SSH for?',
			answer:
				'They operate on a fleet of machines: running commands over SSH, discovering hosts, and keeping credentials in a local vault. They ship enabled but are inert unless a vault exists on the machine.',
			bullets: [
				'Without a vault they will simply report that they have nothing to work with.',
				'They are the two tools worth switching off if you do not run a fleet. This panel is where to do it.'
			]
		}
	];

	const admin: Entry[] = [
		{
			question: 'Where is everything stored?',
			answer:
				'Configuration, conversations, and builtin tool edits live in the data directory, which is per-user by default on a single-user install.',
			bullets: [
				'Your edits to builtin tools: builtins/overlay/ inside the data directory, one file per tool.',
				'Tool switch state: builtins/registry.json in the same place.',
				'Deleting an overlay file is the same as reverting that tool.'
			]
		},
		{
			question: 'How do I keep my setup safe?',
			answer:
				'There is a backup command that captures the database, the launcher scripts, and the tool edits into one archive, and a matching restore. It is the supported way to move an install to another machine.'
		},
		{
			question: 'What changed that I should know about as an admin?',
			answer:
				'The difference from stock Open WebUI, in the order it tends to matter.',
			bullets: [
				'Web search is replaced, not added. An existing web search configuration is not used.',
				'Builtin tools are enabled by default, including two that can run commands on remote hosts over SSH.',
				'Stats report prompt size as the current request rather than a running total, which is a deliberate change in behaviour.',
				'Tool calls are collected into a collapsed panel instead of appearing inline.',
				'A guard that detects repeated output exists and is tunable; if it triggers on legitimate work, its thresholds are in OpenWebVibes settings.'
			]
		},
		{
			question: 'Where do I report something broken?',
			answer:
				'The repository issues page. A useful report says what you did, what you expected, and what happened instead. If a tool failed, its row in the Builtin tools panel shows the exception, and that text is the most useful thing to include.'
		}
	];

	const groups: { title: string; entries: Entry[] }[] = [
		{ title: 'Using Open Web Vibes', entries: using },
		{ title: 'Tools', entries: tools },
		{ title: 'For administrators', entries: admin }
	];
</script>

<UserSettingSection title="Help">
	<p class="mb-4 text-xs leading-relaxed text-gray-500 dark:text-gray-400">
		What this app is, what it changed from stock Open WebUI, and how the builtin tools behave.
	</p>

	<div class="space-y-5">
		{#each groups as group (group.title)}
			<div>
				<div
					class="mb-2 text-[0.6875rem] font-semibold tracking-wide text-gray-400 uppercase dark:text-gray-500"
				>
					{group.title}
				</div>

				<div class="space-y-2">
					{#each group.entries as entry (entry.question)}
						<details class="group rounded-xl border border-gray-100 dark:border-gray-850">
							<summary
								class="cursor-pointer list-none px-3 py-2 text-xs font-medium text-gray-700 transition-colors hover:text-gray-900 dark:text-gray-300 dark:hover:text-white"
							>
								<span class="flex items-start gap-2">
									<span
										class="mt-[0.3em] inline-block size-1.5 shrink-0 rounded-full bg-gray-300 transition-transform group-open:bg-blue-500 dark:bg-gray-700"
									></span>
									<span>{entry.question}</span>
								</span>
							</summary>

							<div class="px-3 pb-3 text-xs leading-relaxed text-gray-600 dark:text-gray-400">
								<p class="pl-3.5">{entry.answer}</p>
								{#if entry.bullets?.length}
									<ul class="mt-1.5 space-y-1 pl-7">
										{#each entry.bullets as bullet}
											<li class="list-disc">{bullet}</li>
										{/each}
									</ul>
								{/if}
							</div>
						</details>
					{/each}
				</div>
			</div>
		{/each}
	</div>

	<div class="mt-4 border-t border-gray-100 pt-3 text-[0.6875rem] text-gray-400 dark:border-gray-850 dark:text-gray-500">
		Built on Open WebUI. Upstream licence and notices are unchanged.
	</div>
</UserSettingSection>
