# Feature vet part 2, dev fork only, 2026-11-08

Runs again, not readings. Stable untouched, no engine call, nothing sent to the shared strata
instance. Tests run with plain python and `WEBUI_SECRET_KEY` set locally.

## What changed on this pass

**1. The journal tool is registered.** `manifest.json` now carries `ex_journal` with `index`, `read`,
`reconstruct`. Run: the registry resolves all three callables. It was previously absent, which is why
a chat could print a pointer to the journal and have no tool able to answer it. The earlier rule about
not hand-editing the manifest still applies to stable; this is the dev tree, at your call.

**2. The C icon is gone.** `ex_stats.context_chips` returns an empty list, the frontend mirror returns
an empty list, and `StatsRow.svelte` no longer renders it or takes the rollover prop. The eviction
state is still written to the per-chat state file for the log and the tool; it is just not drawn. The
reason it was pointless is the reason you gave: on a system that evicts every turn, a permanent marker
with a per-request number under it is not a signal.

**3. The token-rate duplication was a gating bug, not a styling one.** The token figures respected
`statsTokenSource` and the rate chips did not, so both counters always appeared and the switch only
moved the token half. `exStats.ts` now shows the arrival rate and prefill only when the source is live
or both; `engineStatus.ts` shows the engine rate only when the source is engine or both. The duplicate
control in the composer menu was removed, leaving one place: the Open Web Vibes settings page.

**4. Handoff and guard notes are system instructions.** `drain_steering` inserted notes as a user turn,
so the model answered the note as though the user had said it. It now inserts as system. The handoff
block says plainly that this is a new conversation and that the block is not a message from the user.

**5. The minimum reconstruction set, saved as a text file.** New module
`backend/open_webui/utils/ex_reconstruct.py`. Four parts plus the path: prior context, the last full
reasoning loop uncut, the last response, the last user message. Written every turn to
`<DATA_DIR>/ex_journals/<chat>.reconstruct.txt`, read back through the tool's `reconstruct` method.
The gate injects the block every turn, not only when a slide fires, and replaces the previous copy
instead of stacking one.

**6. Safety net instead of refusal.** When the gate cannot fit, it builds the minimum set (pinned head,
reconstruction block, current request) and uses it when it fits. Run: 20 long user turns measured 5565
against a 1488 budget, refused, minimum measured 231 tokens, so the net replaced the refusal with a
servable prompt. When even the minimum does not fit, the refusal stands and says so.

**7. The source of the token-budget behaviour was found.** It was not the gate. `leanrea_reverse_v2`
appended a line to every tool result the model read: `budget: 179200 of 256000 (70%) - trim output,
stop re-verifying`. That is an instruction aimed at the model, arriving in the tool output, which is
how an agent that was never told its window started shortening its own reasoning and answers. The line
is now accounting only and defaults off behind `CONTEXT_BUDGET_NOTES`.

## Runs

- `tests/dev_check_2026_11_08.py`: 15 checks, pass.
- `tests/dev_harness_2026_11_08.py`: 10 checks, pass.
- `tests/dev_vet_2026_11_08.py`: 35 checks, pass. The chip checks were rewritten to assert that nothing
  is drawn.
- `tests/dev_vet2_2026_11_08.py`: 37 checks, pass.

Gate run with the safety net: 5565 measured, budget 1488, refused True at the point of the check,
minimum set 231 tokens, `safety_net_used` True, `refused` False after.

## Honest limits

- There is no mid-turn interrupt. The only boundary the fork controls is the iteration boundary, where
  the steering queue can refuse the next call or stop the turn. Mid-generation, an engine that hits its
  window stops the turn from its side, and nothing in the fork can reach into that.
- The safety net does not make an over-window request servable when the minimum set itself is over
  budget. It reports that case instead of silently truncating.
- The estimate against the engine's real count is still a margin. It matters most on the 128k machines.
- The dev instance still has to restart for the gate changes to take effect, and the tool needs the
  restart to appear in the function list.
