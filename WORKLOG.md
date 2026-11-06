# Open Web Vibes: work log

Location: `~/Documents/AI_Output/Forks/vibe-ed/WORKLOG.md`
Repo: `~/Documents/AI_Output/Forks/vibe-ed` (branch `ex-vibe-core`)
Tests: `~/Documents/AI_Output/Code/vibe-ed/run_all_tests.py`

This file exists because work has twice been lost mid-turn: the assistant's response was
destroyed by a server restart, and the only record of what had been changed was whatever
happened to be left on disk. From now on every session appends here **before** reporting,
so the next session can recover state without guessing.

Rules for this log
- Append, never rewrite history. Correct errors with a new dated entry.
- Record what is *verified on disk* separately from what is merely claimed.
- Note the exact command used to verify anything non-obvious.
- `build/` is gitignored, so a rebuilt frontend is never evidence of committed source.

---

## 2026-11-06 · Session 1: token/context stats bug

### Reported
Token count "adds onto itself". Context window believed to drift upward too.

### Root cause (verified)
`backend/open_webui/utils/response.py`, `merge_usage()`. It folded every model call in a
turn into one cumulative usage dict and treated **all** token keys as additive:

```python
for key in USAGE_SUMMABLE_KEYS:      # included input_tokens and total_tokens
    result[key] = current_value + incoming_value
```

Every call in a turn re-sends the whole conversation, so call N's prompt already contains
calls 1..N-1's prompt plus their completions. Summing prompts re-counts the same context
once per round.

Reproduced by executing the real functions in isolation:

| case | before | after |
|---|---|---|
| 3-round turn, prompt 1000 -> 1300 -> 1650, out 100/120/140 | `total_tokens = 4310` | `2010` |
| engine actually served | 1790 prompt, 360 generated | same |

Error grew with each additional tool round, which is why it looked like self-addition.

### Fix (verified)
Introduced `USAGE_LATEST_KEYS = {'input_tokens', 'total_tokens'}` and split the merge:

- `input_tokens` — latest call wins. Prompt occupancy is a measurement, not a contribution.
- `output_tokens` — stays additive. Generated text really does accumulate.
- `total_tokens` — derived, always `input + output`, so it cannot disagree.
- costs and `*_tokens_details` — stay additive (each call was billed separately).
- a measured `0` stays `0`, distinct from missing.

Also fixed while in the merge path: `prompt_per_second` was never emitted by the Python
side even though `src/lib/utils/exStats.ts` reads it for the prefill chip. `prompt_ts` is
now honoured.

### Why the context window drifted too
Not a second bug. `input_tokens` was inflated and it is what feeds the ratio:
`Chat.svelte` / `MessageInput.svelte` -> `getUsageTokenCount(usage)` -> usage checkpoint ->
`estimated_tokens / threshold`. Fixing the merge fixes both.

### Test added
`Code/vibe-ed/test_usage_merge.py` — 14 checks, including the exact regression and a
ceiling guard asserting `input_tokens` never exceeds the largest prompt seen. All pass.
Nothing covered usage merging before, which is why this survived.

### Known limitation
Fix applies to new messages only. Already-stored messages keep the usage written at
generation time. A backfill over `chat_messages` is possible but was not done here.

---

## 2026-11-06 · Session 2: recovery after a lost turn

A turn was lost to a server restart. Most of its work **did** land on disk but was never
committed. Verified state at recovery time (18:33), all confirmed by md5sum or file read:

### Verified already done on disk

| item | evidence |
|---|---|
| Rename to "Open Web Vibes" | `env.py:975` `WEBUI_NAME = os.getenv('WEBUI_NAME', 'Open Web Vibes')`, with the upstream code that re-appended " (Open WebUI)" removed; `src/app.html` `<title>Open Web Vibes</title>`; `OnBoarding.svelte` eyebrow text |
| Icon replaced | served `/static/favicon.png` = md5 `c219ec4b`, 244865 bytes, differs from `.upstream_branding_backup/favicon.png` (`23b815fa`). Same file now in `backend/open_webui/static/`, `build/static/`, and `static/` |
| Gradient dimmed | `src/ex_theme.css:58` far stop `#274a78` -> `#17293d`; `#0a1421 0%, #101d2e 44%, #17293d 100%`. Mirrored in `src/lib/utils/exPalette.ts:32` |
| Tool-call rows use theme colour | `ConsecutiveDetailsGroup.svelte` check icon now `text-[color:var(--owv-ok)]` instead of `text-green-*` |
| Tool-call run grouping | `structuredOutput.ts` `flushDetails()` no longer emits `detail_single`; one call is still a run, so single calls stop rendering as bare full-width rows |
| Code fence wrap + vertical scroll | `CodeBlock.svelte` `owv-code-fence` class (line ~589) + `src/app.css` rules |
| OpenWebVibes tab scrolling | root cause was three-part, documented in-file: bounded height via parent chain, `min-h-0` on each flex item, and the tab must not lay out as its own flex column. Applied in `ExSlotHost.svelte` (`h-full min-h-0 flex flex-col`) and `OpenWebVibes.svelte` |
| Visible scrollbars | `src/app.css` `.owv-scroll` with webkit + Firefox fallbacks |
| `kill-owv-dirty` | `~/.bashrc:303` aliases `owv-restart-webui.sh`; script restarts only uvicorn, cloning argv/environ/cwd from `/proc/<pid>` so it cannot drift from `owv.sh` |
| Port preflight | `owv.sh:579-590` documents and guards the `Errno 98` case: a second server on a held port boots, fails to bind, exits, while the old server keeps serving, so the stack *looks* healthy |

### Notes on the `Errno 98`
The guard exists. The error in the pasted boot log is the exact scenario the guard
describes: the log shows lifespan completing (`Initialized 1 terminal server(s)`) and then
the bind failing, which only happens when something else already owns the port. It is not a
second bug, it is the pre-existing instance winning.

### Stray artefacts found
- `backend/open_webui/static/.upstream_branding_backup/` — original upstream icons, kept
  as a restore reference.
- `static/*.png` at the repo root — new icon variants so SvelteKit emits both `/favicon.png`
  and `/static/favicon.png`, mirroring upstream's duplicated layout.

---

## Standing rules for this project

1. **Commit before reporting.** A turn that ends uncommitted can be erased by a restart.
2. Frontend fixes are not live until `build/` is rebuilt. `build/` is gitignored, so verify
   by grepping the emitted CSS/JS for a string unique to the change:
   `grep -rl "<marker>" build/_app/immutable/`
3. Restarting the web UI does not require touching Strata. Use `kill-owv-dirty`. Plain
   `kill-owv` is blunt and runs the shutdown trap, killing the engine and forcing a full
   model reload.
4. Never claim a UI change works without checking the served bytes or the built asset.
---

## 2026-11-06 · Session 2b: tool-call presentation, gradient, check icons

Reported from a screenshot of a real message: horizontal scrollbars in tool call
Input/Output panels, command spam (one row per call), a "dark green" tick that is not the
new icon, and the bottom-right of the background gradient still too bright.

### Root causes, all verified before editing

1. **Horizontal scrollbars.** The panels that scroll horizontally are in
   `common/ToolCallDisplay.svelte`, not `CodeBlock.svelte`. My earlier code-fence fix was
   real but touched markdown fences, which are a different component, so it never affected
   these. Lines using `whitespace-pre` + `overflow-x-auto` are the actual cause.
2. **Command spam.** `MarkdownTokens.getDisplayTokens()` broke a tool-call run on *any*
   non-detail token. A turn interleaves narration with calls, so "let me check X", call,
   "now Y", call produced one group per call, each with its own full-width panel.
3. **Bright gradient.** `DEFAULT_GRADIENT` in `backend/open_webui/utils/ex_theme.py` was
   `("#24406b", "#3a6ba5")`. That far stop is saturated cobalt, and this engine applies the
   gradient **at runtime**, writing `--owv-gradient` from here. Editing `src/ex_theme.css`
   therefore had no visible effect, which is why the earlier palette/gradient edit appeared
   to do nothing. The `navy` preset also still carried `#274a78`.
4. **Dark green tick.** Two separate tick renderers. `ConsecutiveDetailsGroup` (the run
   header) was already `--owv-ok`, but `ToolCallDisplay` (the individual call row) was still
   `text-emerald-500 dark:text-emerald-400`.

### Changes

- `ToolCallDisplay.svelte`: all three `<pre>` blocks now use one `tool-call-pre` class with
  `whitespace-pre-wrap break-words [overflow-wrap:anywhere] max-h-80 overflow-y-auto
  overflow-x-hidden`. Wrapping, vertical scroll, no horizontal scrollbar. Parent wrappers
  gained `min-w-0` so the flex item can actually shrink instead of forcing overflow.
- `ToolCallDisplay.svelte`: per-row tick uses `text-[color:var(--owv-ok)]`.
- `MarkdownTokens.svelte`: a run only ends on another non-groupable details block or the end
  of the message. Paragraph/text tokens are absorbed into the live run rather than breaking
  it, so one turn produces one group.
- `ConsecutiveDetailsGroup.svelte`: new `absorbed` prop; absorbed narration renders inside
  the expanded panel in original order, so nothing is hidden.
- `ex_theme.py`: `DEFAULT_GRADIENT` -> `("#1b3150", "#27415f")`, navy preset far stop
  `#274a78` -> `#1e3350`. `py_compile` clean.

### Verification

`npm run build` completed (1m25s, adapter-static, wrote `build`). Confirmed in the emitted
artefacts:
- `tool-call-pre` and `absorbed` present in `chunks/2whVLftx.js`
- `owv-ok)` and `overflow-wrap:anywhere` present in `assets/0.DpFm12_h.css`
- the old `...p-2 overflow-x-auto` tool-call class returns zero matches

### Deliberately NOT done
Server was not restarted, per instruction. The visual changes are not live until
`kill-owv-dirty` is run. The theme change also needs a restart since it lives in Python.
