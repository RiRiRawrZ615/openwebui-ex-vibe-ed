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

---

## 2026-11-06 · Session 5: native builtin tools

RiRi's decisions, all now implemented: every tool in `repo_snapshot/` becomes a builtin, our
web search replaces the bundled one outright, leanrea ships too, the overlay is whole-file, and
the tools are **enabled by default** with the GitHub README documenting it.

### What shipped

1. **`tools/convert_builtins.py`** — reproducible conversion from a WebUI export to a builtin
   module. Nine tools, ~220 KB of source, 68 functions.
2. **`backend/open_webui/tools/builtins/`** — the nine converted modules plus `manifest.json`.
3. **`backend/open_webui/tools/builtin_registry.py`** — resolution (overlay then shipped),
   per-tool enable state, overlay save/revert, stale-overlay detection, lazy module loading.
4. **`utils/tools.py`** — the registry is wired into `get_builtin_tools`, web search is swapped,
   and registration is namespaced.

### The conversion approach, and why the first one failed

The first attempt rewrote each method into a module-level free function with `self` dropped and
`self.valves.X` flattened to a constant. It failed on real code, three distinct ways:

- `asyncio.to_thread(self._fetch_sync, url)` passes a **bound method as a value**, so attribute
  rewriting on calls does not reach it.
- `fleet_ssh` passes the **whole Valves object** to a helper (`_ssh_base(self.valves, host)`), so
  flattening valves into module constants is wrong in principle, not just mechanically.
- `social_reddit_x_1` **mutates `self._token`** and `self._last_request` across calls. A free
  function has nowhere to keep that, and hoisting to globals would silently change the threading
  of a rate limiter and a token cache.

The shipped approach keeps each `Tools` class **verbatim** and appends a module-level singleton
plus one wrapper per public method, forwarding the original signature. A conversion therefore
cannot change behaviour. Cost: one stack frame per call.

Three further real findings the converter had to handle:

- **Two method shapes.** `pcalc`, the YouTube tools and the social reader are `async def`;
  `leanrea`, `fleet_ops` and `fleet_ssh` are plain `def`, built on blocking subprocess and SSH.
  Wrapping a sync tool in an async shell would put blocking SSH work on the event loop, so the
  wrapper kind matches the original exactly.
- **`fleet_ssh` has `from __future__ import annotations`**, which must be the first statement
  after the module docstring. Module ordering is therefore built as an AST (docstring, then
  future imports, then the rest) rather than by splicing unparsed text. An earlier line-splicing
  version produced an `IndentationError`.
- **Leading frontmatter.** `fleet_ssh` starts with a YAML block, not a docstring. Both are just a
  leading string constant, and both are dropped, because the generated header supplies the
  docstring and the frontmatter is WebUI tool metadata rather than Python.

### The namespacing bug, caught by measurement

`tools_dict` is keyed by **function name**. The nine exports contain five genuine collisions:
`check`, `fleet`, `run`, `rustdesk` and `search` each appear in two tools, because the two fleet
tools and the web-search and leanrea tools were written independently. Left alone, the later
registration silently replaces the earlier, with no error anywhere.

`enabled_functions()` now tags every function with `__owv_slug__`, and registration uses
`<slug>__<name>` with the spec name rewritten to match. Only tagged functions are renamed, so no
existing native builtin is affected. Verified: 68 entries, **zero collisions**.

### The allowlist, which is not cosmetic

A converted module's namespace holds its own imports. `youtube_visual_1` ends up with
`parse_qs`, `urlopen`, `urlparse`; `pcalc` with pydantic's `Field`; every module with its `Tools`
and `Valves` classes. Offering those to a model as tools would be useless and confusing, so
exposure is restricted to exactly the names the manifest declares. Tested explicitly.

### Web search swap

Upstream registered `search_web` and `fetch_url`. The builtin exposes `search`, `fetch_url` and
`research`, so `fetch_url` collides deliberately (the native one is not also added) and
`research` is the genuinely new capability. If the builtin fails to load, the code logs and falls
back to the native pair, so a broken builtin cannot leave a user with no web access at all.

### Verification

`test_builtin_registry.py` in `Code/vibe-ed`: 28 checks, all passing. Covers conversion integrity,
the allowlist, all five collisions, toggles, overlay precedence and revert, rejection of an
overlay with a syntax error, and stale-overlay detection after the shipped builtin changes.

### Still outstanding
- The Builtins UI in the OpenWebVibes tab (toggles, editor, revert). Backend `describe_all()`
  returns everything the UI needs.
- The Setup tab.
- README section documenting that the tools ship enabled, plus the GitHub icon.

---

## 2026-11-06 · Session 6: builtin UI, Setup tab, Help tab, README

### Backend
Five endpoints in `routers/ex.py`: `GET /ex/builtins` (list + can_edit), `GET /ex/builtins/{slug}`
(detail + current source for the editor), `POST /ex/builtins/{slug}/enabled`,
`PUT /ex/builtins/{slug}/overlay`, `DELETE /ex/builtins/{slug}/overlay`.

Reads are open to any signed-in user because the settings tab must render for a non-admin, and a
blank tab is worse than a read-only one. Writes require admin, checked in the router rather than
the registry, because the registry has no notion of the request.

An overlay save with a syntax error returns 400 and is **not** written, so a mistyped edit cannot
break the tool on next load. The check is `compile()`, which proves it parses, not that it works.

### Frontend
- **`BuiltinsSection.svelte`** — per-tool switch, exposed function names, `edited` badge,
  `update available` badge for a stale overlay, `error` badge plus the exception for a tool that
  fails to import, whole-file editor, and revert. Patch-in-place on toggle so the list does not
  flicker, and `$derived` values declared as `let x = $derived(...)` (the bare form is a parse
  error).
- **`HelpSection.svelte`** — 15 entries in three groups: using the app, tools, administrators.
  Written to be read at the moment something looks odd, so entries give the symptom where there
  is one.
- **Setup workflow** folded into the existing Install section rather than made a separate tab. It
  probes discovery on mount, so a machine that already has the engines shows that immediately and
  the install steps only appear for what is actually missing. One workflow, two entry conditions.
  Manual binary locate is per component, validated server-side (exists, file, executable, and the
  component must be discoverable), with the reason shown on rejection.

### README
Added an `## Open Web Vibes` section after the upstream header, without altering upstream content.
Documents that the tools ship enabled, the tool table, the SSH warning for the two fleet tools,
the search replacement, the overlay behaviour, the stats semantics, and the in-app Help tab.

### Build
Clean. `Builtin tools`, `This machine`, `Revert to shipped` and a Help answer string all verified
present in `build/_app/immutable/chunks/DPkvbdca.js`.

## Session 2026-11-07 (late): token counters, packaging, and two packaging mistakes

## Token counters: one shown, not two

The stats row carried two token figures at once and they were not the same measurement. One is the
engine's own count read from its log; the other is the front-end count of tokens that have arrived.
Both were labeled `tok`, so the row looked like it was reporting the same thing twice.

Fix: a setting with three values, `engine`, `live`, `both`, defaulting to `engine`, mirrored on both
sides (`ex_settings.py` DEFAULTS and `exSettings.ts` type plus `EX_DEFAULTS`). Both halves read the
same value, which is the point: gating only one side would have left two numbers on the row.

- `exStats.statChips` takes the choice as a third argument, so the front-end token chip appears only
  for `live` or `both`.
- `engineStatus.engineChips` gates the ctx chip for `engine` or `both`.
- `StatsRow` takes the choice as a prop and passes it down; `ResponseMessage` resolves it from
  settings at both call sites.
- Titles now say which side each figure came from. The ctx chip title was corrected, because the
  value it shows is the engine count against the engine's window, not the message-list estimate.

Quick access is in the composer menu, gated on `liveStats` being on, and the settings tab carries the
same control. Saving from the composer goes through `POST /api/ex/settings`, which is the only write
this router makes to the database and is scoped to the `ex` namespace; unknown keys are refused
rather than stored and then ignored. The store is patched immediately so the row updates without a
reload, and a failed save is reported rather than swallowed.

## Packaging

Release 1.0.0-rc1 built with `owv-release.sh --with-data`:

- rpm `openwebvibes-1.0.0_rc1-1.fc44.noarch.rpm` (88M)
- AppImage `openwebvibes-1.0.0-rc1-x86_64.AppImage` (244M)
- tarball `openwebvibes-1.0.0-rc1.tar.gz` (244M), `SHA256SUMS-1.0.0-rc1.txt`
- staging carries `launchers/` (10 scripts plus `owv.aliases`), the bootstrap entry, `README.md` and
  `FEATURES.md`, and the opt-in snapshot: `data/webui.db`, `data/ex_memory`, `data/tools/` (2 exports),
  `data/restore-data.sh`. Journals skipped unless `--with-journals` (181 MB here).

Cleanliness bug found while verifying the first run: the package shipped `app/backend/data`, which is
runtime state from whichever machine last ran the app (a 798 KB webui.db, uploads, cache, a chroma
dir). Two mistakes were made before it was fixed, and both are worth keeping in the record:

1. A comment was first put inside a backslash continuation. The continuation is joined before the
   comment token is read, so it would have swallowed the rest of the `rsync` command. Comments go
   above the call.
2. `--exclude '/data'` did not exclude it, because the transfer root is `backend` itself, so the path
   rsync sees is `backend/data`. The working pattern is `--exclude '/backend/data'`, verified by a
   probe run before the script was re-run. `backend/open_webui/data/readme.txt` is still shipped.

The re-run produced a tarball with no runtime leftovers and the intended snapshot, aliases and
scripts present.

## Checks

- New test `Code/vibe-ed/test_ex_token_source.py`: 21 checks, all passing. Covers both mirrors, the
  gate on both halves, titles, the prop chain, the composer control, the settings tab, the route guard,
  and that the guard compares against the real DEFAULTS dict.
- Full harness: 957 asserted checks, 19 failed. Same 19 as before (settings mirror 1, theme 2, theme
  mirror 3, launcher 13). Nothing regressed.
- i18n cross-check: clean, with the two new labels present in the translation file.
- Svelte compile check on the five components touched: all compile. The build ran in a capped user
  cgroup (`MemoryMax=8G`, node heap 8192) and finished at 15:28 with the engine resident at about
  42 GB, so the build is the thing that dies if it overreaches, not the engine.

## Still open

- Thinking italics and expand-all per chat: the renderer was not identified. `structuredOutput.ts`
  keeps blocks as output items and `ResponseMessage.svelte` renders `visibleResponseContent` without
  moving blocks into the reply, so the size difference comes from markdown defaults rather than a fork
  style. Needs the component that iterates `message.output`.
- File context add/remove in the composer menu: `InputMenu.svelte` has a files tab gated on
  `chat.attach_files`; the quick block is not there yet.
- Backend auto-selection killing the wrong backend: not touched.
- 13 launcher harness failures are genuine gaps, not stale assertions.
- Screenshots for the release notes still owed.

Two things added after that entry, both committed:

- **`BuiltinsSection.svelte`** said nine shipped tools while the manifest carries ten, and the
  instance default had no control of its own. The count now matches the manifest, and a switch
  exposes whether a chat that picked no tools falls back to the active set. That is separate from
  each tool's own switch. Commit `35e191b2c`.
- **Checksum gap in the release script.** `SHA256SUMS-1.0.0-rc1.txt` had only the tarball and the
  AppImage, because the rpm name uses `1.0.0_rc1` and the glob matched the version string with a
  hyphen. The sums file looked complete while omitting the package most people install. Both
  spellings are now covered, and the final listing matches the sums.

Final numbers for this session: 960 asserted checks, 19 failed (settings mirror 1, theme 2, theme
mirror 3, launcher 13). `test_ex_tools.py` is 21 checks including the new UI checks, `test_ex_turns.py`
34, `test_ex_token_source.py` 21, `check_i18n_usage.py` clean. Build ran in a capped user cgroup and
finished at 15:44; three new UI strings verified present in `build/_app/immutable`.

## 2026-11-07, tool crash and the dev launcher (commits 760b00cda, 0c768c6f6)

### The tool bug in the screenshot

`get_builtin_tools` assigned `_web_search_builtin` inside the web_search gate (line 855) and read
it in the bulk registration block (line 876), which runs unconditionally. When web search was off,
or the model lacked the `web_search` capability, or the feature flag was false for the chat, the
gate never ran and the read raised UnboundLocalError. That took down tool resolution for the whole
request, so it looked like a tools bug while the trigger was a settings state unrelated to tools.

Fixed by hoisting to a module constant `WEB_SEARCH_BUILTIN_SLUG`. Two proofs, not one:

- symtable: before the change the name is function-local and assigned; after, it is a module
  global with no assignment inside the function, and the read at 875 sits outside the gate.
- runtime: the gate forced to False. The pre-fix shape raises, the fixed shape does not.

`test_ex_tools.py` now runs the detection across the whole function rather than one name. It also
carries an embedded positive control, because `Forks/vibe-ed.broken` was deleted mid-session and
the control quietly dropped out of the run without failing anything. A control that can be deleted
is not a control.

Two false positives the guard had to be written around: for-loop targets are rebound every
iteration, and a read inside the same conditional as the assignment is safe. Both were wrongly
flagged in earlier drafts.

### Your rollback was correct, and the committed version was not

`8dfe3ed07` left `statChips` in `exStats.ts` with an unclosed brace. esbuild reports "Unexpected
end of file" on the committed version and parses your working copy cleanly. The whole `src` tree is
byte-identical between the fork and the backup you reconstructed, so nothing else was lost.

The reason nothing caught it: `test_ex_stats.py` compares extracted values by regex, and
`scripts/check_svelte.mjs` only compiles `.svelte` files. Nothing in the suite checked whether a
`.ts` file is syntactically valid. New `test_ex_ts_parse.py` covers all 136 authored ts files.
`.d.ts` is excluded as ambient declaration syntax, which is what produced the first false failure.

### Dev launcher, `ai-launchers/owv-dev.sh`

Three guarantees, all asserted in `test_owv_dev_launcher.py` (29 checks):

1. never writes to the live data dir. The production launcher points DATA_DIR at
   `~/.open-webui-llamacpp-safe-v4` on purpose. The dev instance gets a snapshot copy via
   `sqlite3 .backup`, so a test run cannot mutate the database in use.
2. never takes over a port. `OWV_TAKE_OVER_PORT=0` is forced, and a held dev port is refused.
3. never re-execs into tmux. `OWV_NO_TMUX=1` is forced.

Dev ports are 8090/8005/8009/8006 so both instances can be up at once. It is a wrapper, not a
second implementation; all startup logic stays in `owv.sh`.

One thing I initially got wrong and then fixed: I exported `OWV_REQUIRE_FRESH_BUILD` without
anything reading it, which is the same "declared and never read" defect already flagged for
`OWV_INSTALL`. It is now read in `owv.sh`, and a stale bundle causes a refusal in a dev run rather
than a warning. A stale build is the most common reason a correct fix looks broken.

`OWV_RUNTIME_DIR` is derived from XDG_CACHE_HOME in `owv.sh` and is not affected by `OWV_LOG_DIR`,
so the dev instance had to override it explicitly. Otherwise dev pid files landed in the production
runtime dir and `kill-owv` could kill the dev instance.

### Harness

1010 asserted checks, 19 failed: settings mirror 1, theme 2, theme mirror 3, launcher 13.
No new failures. `run_all_tests.py` now runs through the fork venv, because system python has no
typer and `test_ex_rag.py` reported a false failure while passing 37 checks under the fork venv.

Launchers and the harness are not in a git repo, so those fixes are disk-only and cannot be pushed
to the fork.

Two things found in the dev launcher itself while verifying it:

- `--shell` printed the env block but ran the database copy first, so it created a 584 MB dev
  database while its own usage text promised it writes nothing. The check that should have caught
  it asserted only the output shape, so it passed. Now `--shell` is a no-write path and the test
  asserts that nothing is created, not just that the output looks right.
- `--shell` also printed a copy line, which contradicts "print the environment block only". The
  reporting belongs to `--dry-run`; `--shell` prints the block and nothing else.

A stray dev database created by the first version was removed. The live database was never touched;
its mtime is 17:15 and the run that created the copy was 17:30.

## 2026-11-07, settings tabs: the duplicate Connections tab and the Help tab

### Reported
Two tabs in settings were both named Connections. One showed only tokenharbor.

### What the two tabs actually were (verified in the live database, read only)
The personal `connections` tab renders `settings.directConnections`, which for this instance holds
exactly `["https://tokenharbor.ai/v1"]`. The `admin:connections` tab reads the stored connection
list: 8005, 8004, 5000, 192.168.1.37, tokenharbor, groq, plus the ollama, terminal and tool server
sections. Same thing at two scopes reads as a duplicate, so the narrow tab is removed and the full
one, the one with the model paths, is kept.

Help moved from a section at the bottom of the OpenWebVibes tab to its own tab, with a locale key so
its title is translatable. The component no longer wraps itself in a section titled Help, since the
tab header already says Help.

### Harness
`test_ex_settings_tabs.py`, 14 checks, reads the source so it can be pointed at the tree about to be
committed. The harness previously hardcoded the stable fork, so a change made on the dev copy could
not be proved by it; `test_ex_tools.py` now takes the same `EX_FORK_DIR` override with the same
default.

## 2026-11-07, tool wiring: a helper was listed as a tool, and extract read from the wrong directory

### Verified
`hashlib_sha1` was in leanrea's `exposed` list but not in `specs`. `specs` is the allowlist
`get_function` uses, so nothing failed at runtime, but `exposed` was claiming a tool that is not one,
and that is what the tools page shows. The converter's own docstring says helpers stay unexposed, so
the rule was wrong rather than the data: `exposed` is now built from the declared specs and public
module-level helpers go in a separate `helpers` field.

`extract()` resolved a bare filename against the process cwd, so a target sitting in the project work
dir reported "No extraction method for unreadable". Added `_resolve()`, tried against the project
dir, then its work dir, then the root. Verified with a synthetic zip in a scratch project; the scratch
dir was removed after, and no sample target was used.

The tool export snapshot was patched the same way, because builtins are generated from the exports:
fixing only the generated file would be lost on the next regeneration. The snapshot lives in
`Code/openwebui-tools/repo_snapshot` and is not in this repo, so that half is disk-only.

### Harness
`test_ex_tools.py`, 29 checks, 5 added for the manifest rule and the extract resolution.
