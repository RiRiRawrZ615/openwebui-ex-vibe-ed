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

---

## 2026-11-06 · Session 2c: tool calls moved out of the message body

### Clarified requirement
Tool calls must not be drawn inline where the assistant's prose is. They belong in one
separate collapsible surface at the bottom of the message, like the Tasks panel: expandable
as a whole, with each individual call inspectable.

Earlier attempts only changed *how* inline rows looked. That was the wrong problem. The rows
were still interleaved with prose, so the message still read as spam regardless of styling.

### Change
`MarkdownTokens.getDisplayTokens()` now partitions tokens: every groupable detail token is
collected into one `activity` array and emitted as a **single** `detail_group` appended after
the prose. Non-detail tokens pass through untouched and in order.

Consequence: the message body is pure prose. All of the turn's tool activity is one
collapsible block at the end, in original execution order. The absorbed-prose machinery is
no longer needed for run-splitting and passes an empty array.

### Why it looked broken before this
`FRONTEND_BUILD_DIR` is `BASE_DIR/'build'` = `Forks/vibe-ed/build/`, which is exactly where
`npm run build` writes. Verified the running server serves the current build by hashing what
it returns against disk:
- served `chunks/BuWDkdzi.js` md5 `7cf312e9c5ca` == disk md5 `7cf312e9c5ca`
- served `/` md5 `f114b9f59efddb3e347dfad5854d0435` == `build/index.html`

So the server was never stale. Repeated "no change" reports were the browser serving a cached
bundle. Hard reload / cache bypass is required to see any frontend change.

### Backup taken first
`~/Documents/AI_Output/Backups/owv-backup-20261106-190011.tar.gz` (716M, 269 chats,
commit 44af293f1, integrity ok).

### Still outstanding
- Native builtin tools from `Code/openwebui-tools/repo_snapshot/` (9 exports), with our web
  search replacing their DuckDuckGo, plus an edit-overlay so a user edit beats the builtin.
- Setup tab under OpenWebVibes settings: install/select llama.cpp, Strata, bonsai binaries.
- Connections tab does not list existing connections until a prompt is sent.
- Chat title renders over the top of the window instead of truncating to the corner.

---

## 2026-11-06 · Session 2d: found the REAL render path

### The mistake, stated plainly
Two sessions of edits went into `MarkdownTokens.svelte`. That file is **not** used for a
normal chat response. The real chain is:

    ResponseMessage.svelte
      -> ContentRenderer.svelte
        -> StructuredOutputRenderer.svelte
          -> buildOutputDisplayItems()   in Messages/structuredOutput.ts

So every edit to the markdown token path was invisible. Verified by grepping for the
consumers of `buildOutputDisplayItems`.

Before concluding "cache" a third time, the served bytes were hashed against disk:
- served `chunks/BuWDkdzi.js` md5 `7cf312e9c5ca` == disk md5 `7cf312e9c5ca`
- served `/` md5 `f114b9f59efddb3e347dfad5854d0435` == `build/index.html`
The server was serving the current build. The problem was that the build did not contain the
intended change, because the change was in the wrong file.

### Now fixed in the correct place
`buildOutputDisplayItems()` collects every groupable detail token into `hoistedDetailTokens`
and emits **one** `detail_group` after all message text. `flushDetails()` remains as a named
no-op so the call sites read unchanged and the inline behaviour is trivially restorable.
Message body is pure prose; the turn's activity is one collapsible block at the end.

Verified present in the emitted bundle: `build/_app/immutable/chunks/BwIFtHcj.js` contains
the hoist. Build current relative to `src`.

### Tool-call iteration cap
`CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS` was never set by the launcher, so it fell back to
the upstream default. `owv.sh` now passes `${CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS:--1}`
explicitly, and `-1` is interpreted by `env.py` as uncapped. `bash -n owv.sh` passes.
Historical note from CHANGELOG: the default was raised 30 -> 256 in one release, then
256 -> 1024, with `-1` for unlimited.

### Loop guard (reported: fires on refresh / tab-out, spams logs)
Not yet changed. Located:
- `backend/open_webui/utils/ex_wiring.py:178 loop_check()`
- called at `backend/open_webui/utils/middleware.py:5853`, only inside
  `if ex_settings.get('loopGuard')`, guarded by `try/except` (bare, so a raise is swallowed)
- thresholds in `ex_loop_guard.py`: `DEFAULT_REPEAT_THRESHOLD = 3`,
  `DEFAULT_SIMILARITY = 0.65`, `MIN_CONTENT_WORDS = 6`, `REFUSE_ON_OCCURRENCE = 3`
- `similarity()` is word-set Jaccard over non-stopword tokens, needs >= 6 content words on
  both sides; `consecutive >= 2` trips a note
Because `loop_check` runs on `prior_output + output` per iteration, a replayed or partial
output set can look like repetition. Not yet reproduced, so not yet changed. Deliberately
left alone rather than guessed at.

### Icons
All `.ico` files on disk are already the new icon (118528 bytes, converted from the PNG), and
served `/static/favicon.png` is 244865 bytes, md5 differing from the upstream backup. The
remaining "old icon" report is the browser tab, which resolves `/favicon.ico` and caches it
hard.

### Still outstanding (not started)
- Native builtin tools from `Code/openwebui-tools/repo_snapshot/`
- Setup tab under OpenWebVibes (llama.cpp / Strata / bonsai install + manual select)
- Connections tab lists nothing until a prompt is sent
- Chat title draws over the top of the window instead of truncating
- Loop guard retuning

---

## 2026-11-06 · Session 3: packaging plan, discovery, gradient drift

RiRi specified the release direction: the RPM **replaces** stock Open WebUI, the launcher
ships inside the package, and the setup workflow plus the dependencies tab are the same
surface gated by `ex.allowInstall`. Full design of record is now in `PACKAGING-PLAN.md`.

### Discovery: the setup surface is not missing
`Settings/OpenWebVibes.svelte` already has an **Install** section (allowInstall switch,
components input, plan button) plus Backend, Import, Theme, Agentic, Runtime, Behavior and
Context window sections. `utils/ex_backend.py` already has `install_plan()`, which returns
*descriptions* and never commands, deliberately. So the work is execution and discovery, not
a new tab.

### Built this session
1. **`bonsai` added to `INSTALLABLE`.** It was named as a required engine but was being
   silently dropped into `refused`, so the setup tab could never have offered it.
2. **`utils/ex_discover.py`** — new, pure. Probes llama.cpp / strata / bonsai: PATH first,
   then known candidate directories. Candidate order mirrors RiRi's own launchers
   (`llama.cpp-rdna2-fa/build/bin`, `llama.cpp-nvidia/build/bin`, `llama.cpp/build/bin`,
   then `~/strata/bin`, `~/strata/target/release`, `~/.local/bin`). It never executes a
   candidate, only stats it, so a settings tab cannot become a remote-code surface.
   `manual_select()` validates a user-supplied path: exists, is a file, is executable, and
   the component must be discoverable, so the field cannot point at an arbitrary binary.
   Verified on this machine: found `/home/amd/llama.cpp-rdna2-fa/build/bin/llama-server` and
   `/home/amd/strata/bin/strata`, bonsai legitimately missing.
3. **Endpoints** `GET /ex/discover`, `GET /ex/discover/{component}`,
   `POST /ex/discover/{component}/manual`, plus the matching `src/lib/apis/ex/index.ts`
   functions and types.
4. **owv.sh self-wraps in tmux**, with a TTY guard. The first version broke the launcher test
   harness (a non-interactive run re-execs into tmux and dies with "terminal does not support
   clear"), which is exactly the kind of break a test should catch. Guard is now
   `[[ -t 1 && -z $TMUX && OWV_NO_TMUX != 1 ]]`.
5. **Gradient drift repaired.** Four places describe the gradient and they disagreed:
   `ex_theme.DEFAULT_GRADIENT`, `ex_settings.DEFAULTS['themeGradient']`, `src/ex_theme.css`,
   and the TS mirror. Python still held the old bright 2-stop `('#24406b','#3a6ba5')` while
   the stylesheet and mirror had moved to the dimmed 3-stop. Because the engine applies the
   gradient at runtime from Python, the Python value is the one the user sees, which is why
   dimming the CSS appeared to do nothing. Now canonical: `#0a1421, #101d2e, #17293d`.

### Test state, measured against a real baseline
Baseline taken by stashing the fork changes and re-running. Not guessed.

| suite | baseline | now | delta |
|---|---|---|---|
| test_ex_stats.py | 20 p / 1 f | 21 p / **0 f** | fixed |
| test_ex_settings.py | 41 p / 2 f | 42 p / 1 f | improved |
| test_ex_theme.py | 69 p / 2 f | 69 p / 2 f | pre-existing |
| test_ex_theme_mirror.py | 50 p / 3 f | 50 p / 3 f | pre-existing |
| test_owv_launcher.py | 35 p / 13 f | 35 p / 13 f | pre-existing |
| test_usage_merge.py | 14 p / 0 f | 14 p / 0 f | holds |

### Known remaining failures, and why they are left
- `test_ex_theme.py` asserts the **old** gradient contract: `len(DEFAULT_GRADIENT) == 2` and
  `DEFAULT_GRADIENT[0] == DEFAULT_THEME_SEED`. The canonical default is now 3 stops starting
  darker than the seed, which is the dimming RiRi asked for. The test encodes superseded
  intent. Rewriting an assertion to match new behaviour needs to be a deliberate, stated
  change, not a silent one, so it is called out here rather than quietly edited.
- `test_ex_theme_mirror.py` (3) compares emitted CSS against a TS mirror; same root cause.
- `test_owv_launcher.py` (13) fail on harness path resolution
  (`..._t21fz37e/data/ex_runtime.json`), unrelated to this work.

### Not started
- Bonsai install step execution and the step-runner with a streamed log (G2)
- RPM spec (G5)
- The 9 builtin tools from `Code/openwebui-tools/repo_snapshot/`, web-search replacement and
  the edit overlay (G6)

---

## 2026-11-06 · Session 4: streaming pacing, cursor bar, stats accuracy

### 1. The streaming cursor bar (removed)
Found it, and it took a while because it is not drawn anywhere I first looked.
`ResponseMessage.svelte:993` rendered a 2px `<span>` with `animate-cursor-pulse` after the
text while generating. The animation is `--animate-cursor-pulse: cursor-pulse 1.5s ...` in
`src/tailwind.css:6`. It was appended by upstream, not by this fork.

Removed. It reads as a stray cursor sitting in the prose, and with a chunked backend the text
already starts and stops on its own, so the blink draws the eye to the gaps rather than the
words. Streaming state is still conveyed by the message not being marked done.

Note for anyone searching later: `grep animate-cursor-pulse src/` returns one hit, which is
now the explanatory comment in place of the removed span.

### 2. Streaming pacing for chunked backends (new)
`src/lib/utils/exStreamSmoother.ts`.

The problem is arrival shape, not speed. llama.cpp emits small, evenly spaced deltas that
already look smooth. Strata emits large bursts separated by real pauses, so rendering each
burst on arrival makes text appear in lurches, and the eye reads the pauses as stalling.

The fix animates a *reveal position* across the real content. Rate is adaptive: it accelerates
as the backlog grows and settles to the base rate when caught up, so a fast burst does not fall
ever further behind. Verified numerically with a port of the pacing math:

| case | max lag | frames |
|---|---|---|
| Strata-like bursts with 800-1200ms pauses | 564 chars | 151 |
| llama.cpp-like 8-char deltas every frame | 77 chars | 214 |
| one 8000-char chunk | 0 (hard catch-up) | 1 |

**Correctness constraint, and the reason the smoother lives in ResponseMessage and not in the
socket handler:** `message.content` is the transcript and is what gets sent back to the model.
The smoothed string is display-only, passed to the renderer as `displayContent`, and never
written back. A slow reveal therefore cannot truncate what the next turn sees.

An earlier draft derived the hard catch-up ceiling from a lag budget, which worked out to ~170
characters and meant a routine 400-character Strata burst dumped instantly instead of
smoothing, defeating the point. The ceiling is now `HARD_CATCHUP_CHARS = 4000`, a genuine
safety net rather than a routine path.

### 3. Self-inflicted break, caught and fixed
While editing the edit-state declarations I removed `editedOutput` and `editTextAreaElement`,
which are live. The edit flow then referenced undeclared variables. Caught by grepping the file
after the edit rather than by the build, and both were restored with the smoother's state in
the same block. Recorded here because it is exactly the class of mistake that a build alone
does not always surface in Svelte.

### Still to do from RiRi's list
- Stats accuracy during generation: `exWindowTokens` counts message-list tokens only, not the
  system prompt or tool schemas, and the live figure is a character heuristic until the final
  usage chunk lands. `StatsRow.svelte` already labels estimates, but the window figure deserves
  a check against what the engine is actually given.
- The spinner lagging during tool calls, and general UI stutter.
- The craft graphs JS file implemented natively as a builtin.
