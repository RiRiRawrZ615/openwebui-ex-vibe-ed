# Open Web Vibes: handoff

Written 2026-11-06 at the end of session 6. For a fresh context window. Read `WORKLOG.md` for
history and `PACKAGING-PLAN.md` / `BUILTIN-TOOLS-PLAN.md` for design.

Repo: `~/Documents/AI_Output/Forks/vibe-ed`, branch `ex-vibe-core`
Remote: `github.com/RiRiRawrZ615/openwebui-ex-vibe-ed` (RiRi has deliberately chosen to keep the
token in the remote URL. **Do not revoke it or suggest revoking it.**)
Tests: `~/Documents/AI_Output/Code/vibe-ed/run_all_tests.py`

---

## 1. Standing rules. These have all cost real time.

1. **Never restart the server mid-turn.** There is no turn recovery; a restart deletes the
   in-flight response. Ask first, always.
2. **Commit before reporting.** A turn that ends uncommitted can be erased.
3. **`kill-owv-dirty`** (~/.bashrc alias to `ai-launchers/owv-restart-webui.sh`) restarts only
   uvicorn, keeps Strata and the model warm. Plain **`kill-owv`** (a real script, written this
   session) stops everything and costs a cold model load. Python changes need the full one;
   frontend does not.
4. **Frontend is not live until `npm run build` rewrites `build/`.** `build/` is gitignored. Verify
   by hashing served bytes against disk:
   `curl -s localhost:8080/_app/immutable/chunks/<hash>.js | md5sum` vs the same file on disk.
5. **A stale browser cache has repeatedly looked like a failed change.** If a frontend change
   "did nothing", check the served bytes before doubting the code. This burned two full sessions.
6. **Never feed a per-frame-changing derived value into `ContentRenderer`.** A pacing fix did
   exactly that and made the whole page ~10x slower by re-running markdown up to 60x/second. It
   was reverted (`ecddcfbbf`).
7. **`replace_file_content` on `ResponseMessage.svelte` has silently dropped adjacent live
   declarations.** Grep neighbouring symbols after editing that file.

---

## 2. The real render path, and the trap

A chat response renders through:

```
ResponseMessage.svelte -> ContentRenderer.svelte
  -> StructuredOutputRenderer.svelte
    -> buildOutputDisplayItems()   in Messages/structuredOutput.ts
```

`MarkdownTokens.svelte` and `ConsecutiveDetailsGroup.svelte` are **not** on that path for a normal
chat response. Two sessions were wasted editing them. Confirm which component a surface actually
uses before editing.

---

## 3. State: done and committed

| area | commit | notes |
|---|---|---|
| token stats fix | `2f4247a6c` | `merge_usage` summed prompt tokens across rounds (4310 reported vs 1790 served). `input_tokens` is now latest-wins, `total_tokens` derived. |
| rename, icon, tab scroll | `2f4247a6c` | `WEBUI_NAME` default is "Open Web Vibes"; upstream code that re-appended " (Open WebUI)" removed. |
| tool calls hoisted | `4b73fd623` | All of a turn's activity is one collapsible block after the prose. |
| reasoning separated | `1092fd5ba` | `reasoning` left `GROUPABLE_OUTPUT_TYPES`; renders inline via `Collapsible`. |
| tmux self-wrap | `1092fd5ba` | `owv.sh` re-execs into tmux, with a TTY guard so test harnesses still work. |
| discovery | `1987dc9cf` | `utils/ex_discover.py`, `/ex/discover` endpoints. Verified on RiRi's machine. |
| gradient | `1987dc9cf` | Four places defined it and disagreed; canonical now `#0a1421,#101d2e,#17293d`. |
| cursor bar removed | `ebfc883a1` | The `animate-cursor-pulse` span in `ResponseMessage.svelte`. |
| stats formatting | `baff67977` | Counts grouped, rates to one decimal, `12.0s` not `12s`. |
| 9 builtin tools | `1215101ae` | `tools/convert_builtins.py` + `tools/builtins/` + `builtin_registry.py`. 68 functions. |
| builtins wired | `07b46f5bd` | Namespaced registration (`<slug>__<name>`), 5 real collisions resolved. |
| builtins UI, Setup, Help, README | `35a73cf8e` | Pushed. |
| stale turn error cleared | `534dcfb3d` | `dismissStaleError()` in `Chat.svelte`; called from `continueResponse` and `submitHandler`. Clears the banner before `done` flips, clears any queued auto-continue. |
| Setup + Themes tabs split | `d1aeefb7a` | `SetupTab.svelte`, `ThemesTab.svelte`. Also fixed the Setup workflow being nested inside a launcher-handshake `{#if}`, which hid it entirely on a fresh install. |

Test baseline: **780 checks, 19 failures**, all pre-existing (`ex_settings` 1, `ex_theme` 2,
`ex_theme_mirror` 3, `owv_launcher` 13). `test_builtin_registry.py` is mine: **28 checks, 0 fail**.
The theme failures assert a superseded 2-stop gradient contract; left deliberately, documented.

---

## 4. PENDING WORK. This is what RiRi asked for and is not done.

### 4a. Split Setup and Themes out of the OpenWebVibes tab. DONE (`d1aeefb7a`)

Tabs `setup` and `themes` are registered in `SettingsModal.svelte` under the Fork group, with i18n
keys `settings.personal.setup.title` / `settings.personal.themes.title`. Setup owns Install, This
machine and Import. Themes owns the appearance controls and the contrast report.

Worth knowing: the Setup workflow had been appended to the end of the **Runtime** section and was
nested inside `{#if status?.launcher?.present && Object.keys(status.launcher.locked).length > 0}`.
That rendered nothing on a fresh install, which is very likely why RiRi could not find it. It is
top level in `SetupTab.svelte` now.

### 4b. Backend selector still open. See below.

### 4b. Backend selector. The hard one, and RiRi flagged it as complicated.
RiRi: *"you forgot to add selector for backend! so if i want llama, strata, or bonsai i need to pick
the default the server will start with, inform the user it will restart. or... maybe just have it
set preferred backend. strata takes much resources so i dont know how to handle what if the user
wants to use llama but our server starts strata?"*

**The blocker, verified:** there are two disconnected things.

- The UI writes to the app's settings store (`ex.*` keys in `user.settings`).
- `owv.sh` reads `OWV_BACKEND` (and `OWV_STRATA_BIN`, `OWV_LLAMACPP_BIN`) from
  `~/.config/openwebvibes/owv.conf` **at process start**.

So a dropdown that writes only to the app's settings **would do nothing.** Confirmed by grep:
nothing in `utils/ex_settings.py`, `routers/ex.py` or `utils/ex_backend.py` mentions `owv.conf` or
`OWV_BACKEND`.

**Options, for RiRi to choose:**

1. **Backend = launcher config, UI is read-mostly.** The dropdown shows what the launcher actually
   started (`Handshake from: strata` in the Runtime section is already this signal) and offers a
   "set preferred backend" action that writes `owv.conf`, clearly labelled as taking effect on next
   start. Honest, no lie about a restart, and it cannot break a running turn.
2. **UI writes `owv.conf`, launcher reads it.** Same as 1 but the write goes through a new endpoint
   that validates the binary from `ex_discover.py` before writing. Preferable, because the same
   discovery already validates paths.
3. **Hot-swap the engine.** Not recommended. Swapping backend mid-session means tearing down the
   running engine, and with Strata holding significant VRAM the failure mode is an unusable app.

**The resource question RiRi raised is the real design constraint.** If the launcher starts Strata
and the user wanted llama, they have already paid the VRAM cost before the UI exists to complain.
So the honest answer is that backend selection is a **pre-start** decision and the UI should present
it that way: set it, told it applies on restart, with a restart button. Do not pretend it is live.

Existing pieces: `utils/ex_backend.py` has `BACKENDS = ("llamacpp","strata")`,
`INSTALLABLE = ("webui","llamacpp","strata","bonsai","openwebvibes")`, `classify_model()`,
`auto_select()`, `path_allowed()`. `INSTALLABLE` already includes bonsai but `BACKENDS` does not.
There is an existing Backend section in `OpenWebVibes.svelte` (`sections.backend.title`) that
points at a handshake, not a selector.

---

## 5. Other open items

- **GitHub side**: icon and screenshots. RiRi said *"i can take some screenshots so once youre done
  with the tools implementation and setup then we can make them"*. Waiting on him.
- **The 19 pre-existing test failures.** Three assert a superseded gradient contract. Decide
  whether to rewrite the assertions.
- **Loop guard** misfires on page refresh and tab-out (`ex_loop_guard.py`, thresholds
  `DEFAULT_REPEAT_THRESHOLD=3`, `DEFAULT_SIMILARITY=0.65`). Located, not changed, not reproduced.
- **Spinner lag during tool calls.** Not investigated. Likely the same root as the old tool-call
  spam: the output list re-renders per status update. Measure render passes before changing.
- **Setup tab execution.** `install_plan()` still returns descriptions and runs nothing. The setup
  workflow shows discovery and offers manual binary select, but "Install" does not install yet.
- **RPM packaging** (G5 in `PACKAGING-PLAN.md`). Not started.
- **Craft graphs as a display item.** Needs a type in `buildOutputDisplayItems()` plus a renderer,
  same shape as the `reasoning` item.
- **Connections tab** shows nothing until a prompt is sent. Reported, not investigated.
- RiRi may want the **old DB tool records** disabled, since the builtins supersede them. He asked
  about "disabling the normal versions"; only web search was genuinely a replacement. The other
  eight existed only because he imported them. Unresolved.

---

## 5a. Settings tab (OpenWebVibes): why it showed only a spinner. Patched 2026-11-07.

The tab was not failing on save. It was failing at mount. `onMount` called `probe()` and
`refreshTheme()`, and neither exists in that component any more. `probe()` lives in SetupTab and the
theme report lives in ThemesTab, so both calls were left behind when those sections were split out of
the vibes tab. The first undefined call threw, every `refresh*()` after it never ran, `loading` stayed
true, and the tab rendered nothing but the spinner. That is why the ex settings looked invisible and
why loop guard could not be toggled. Two confirmations: eslint reports no-undef at lines 241 and 243
of `OpenWebVibes.svelte` before the patch, and the shipped chunk
`build/_app/immutable/chunks/CkJmt9qc.js` contains each name exactly once with no definition, so the
broken calls are what is being served. A clean build does not catch undeclared references, which is
how this got committed.

Nothing was lost from her settings. RiRi's stored settings contain no `ex` block at all, so no ex
setting has ever been saved from the UI successfully. The save path itself is sound: `saveEx` posts to
`/api/v1/users/user/settings/update`, `UserSettings` allows extra keys, `ui` is an untyped dict, so the
first save after the fix creates `settings.ui.ex`. The resolver already accepts both `settings.ex` and
`settings.ui.ex`, so nothing there needed changing.

Still open in that tab: `sendSteering()` is defined but never called, so the steering box has no submit
control. Frontend patch needs `npm run build` before it is live.

## 5b. Stats (tok/s): two defects. Patched 2026-11-07.

1. The priority order was backwards in both halves. `ex_stats.py` and `exStats.ts` each carry the
   comment that a provider-computed rate is authoritative, yet both derived from durations first. On
   an agentic turn `out_tokens` is the turn total while `t_eval` and `eval_duration` describe the last
   call only, so the derived figure is a turn total divided by one round's time. Order is now provider
   rate first, derivation as fallback, mirrored in both halves.
2. `merge_usage` accumulates `cache_read_input_tokens` across tool rounds while `input_tokens` is
   latest-wins, so the cache chip can read larger than the prompt it was read from. Real evidence in
   the live DB: a stored usage with input_tokens 172342 and cache_read_input_tokens 525696. Both stats
   halves now clamp cached to input. The merge contract itself was left alone (detail maps accumulate,
   per the test of record); treating cache as a per-call measurement at the merge layer is a contract
   change and needs `test_usage_merge.py` updated with it.

`test_ex_stats.py` is 23 passed, 0 failed, with two new checks added for the rate priority and the
clamp. The old check expected the unclamped absurd figure, so it was updated rather than left green by
removing it.

---

## 6. Environment facts worth not rediscovering

- Data dir: `~/.local/share/openwebvibes`. DB `webui.db`. Builtin state at
  `<DATA_DIR>/builtins/registry.json`, overlays at `<DATA_DIR>/builtins/overlay/<slug>.py`.
- `ai-launchers/` is **not** a git repo. Only preserved by `backup-owv` (archive in
  `~/Documents/AI_Output/Backups/`).
- Backend Python: `.venv` inside the repo. Web UI on 8080.
- `FRONTEND_BUILD_DIR` = `BASE_DIR/'build'` = the repo's `build/`, which is where `npm run build`
  writes. The server serves it per request; no restart needed for frontend.
- The nine builtin slugs: `duckduckgo_search_30`, `leanrea_reverse_v2`, `social_reddit_x_1`,
  `youtube_visual_1`, `fleet_ops`, `fleet_ssh`, `pcalc`, `universal_cognitive_memory_vault`,
  `youtube_video_parser`.
- Two fleet tools run SSH and read a credential vault. They ship **enabled** per RiRi's decision,
  documented in the README and the in-app Help tab.

## 5c. Rolling context. Wire map, nothing implemented yet. 2026-11-07.

Intent as RiRi stated it: take the current chat as if the download as txt button had been pressed, dump
the whole context, and feed it back into the next chat so nothing is lost. Verbatim, no model paraphrase,
and Strata should not be involved in it. One point to confirm before code is written: whether "Strata
should not be aware" means OWV owns rollover entirely and engine-side compaction stays off, or something
narrower.

Already present and wired:
- `ex_wiring.read_previous_context`, called at `middleware.py` line 2279, gated by the `previousContext`
  setting, delivered through `ex_context_blocks` so it lands at the tail rather than the message head.
  `previousContextChars` and `previousContextAsSystem` are honored. The reader itself does not need
  changes; it already prefers a pasted block, then the stored file.

Missing, and this is the whole job:
- No writer. The reader only sees `DATA_DIR/ex_previous_context/<chat_id>.txt`, and that directory does
  not exist in the live data dir, so the reader never finds anything. `routers/ex.py` has `GET /context`
  only, no POST, so there is no route to store a paste either. Add a writer that renders the stored chat
  as text in the same shape as the txt export, stores it per chat, and lets the reader pick it up when a
  new chat starts.
- `ex_export.verbatim_digest` has a wrapper (`ex_wiring.digest`) but no caller, so `verbatimSummary`
  changes nothing at runtime. The wrapper docstring is explicit that the result must be stored on the
  checkpoint message, otherwise the prompt never shrinks and the digest is a no-op in practice.
- Settings visibility: `loopThreshold` has no control in the tab, so loop guard toggles but the threshold
  cannot be set. Every other key in `DEFAULTS` has a control. Add a threshold field next to the toggle.

### 5c status after implementing. 2026-11-07.

The writer now exists, in `utils/ex_rollover.py`, and the reader reaches it through the carry.
Store shape is a folder per chat under `DATA_DIR/ex_previous_context/<chat_id>/`: `chat.txt` in the
download-button shape, `index.tsv` mapping message id to section id, `manifest.json` recording which
chat a chat was carried from, and `reasoning/<id>.md` for each block.

Call sites, two only. The writer runs in `models/chats.py` at the message save, gated on a completed
message: the request path sees the history before generation, so a rollover decided in middleware
would have lost the reply that was being generated. The trigger runs where the window is measured,
on `tokens_before` rather than `tokens_after`, so the rollover point does not move when stale tool
output happens to be trimmed.

RiRi's decision on the trigger: 0.7 of the window. On the inheritance: a new chat resolves its source
through the carry pointer, consumed once. On reasoning: blocks are addressed by id, the prompt gets
the index of ids and excerpts, and a block is pulled back only when an id is cited, in the middleware
block collector so it lands at the tail.

Compaction is refused in the request path when rollover is on. That is the "Strata must not be clever"
rule written down in code: the window shaping still trims, but nothing rewrites the transcript.

Still open: the settings tab has no control for the new keys, so they run at their defaults until a
control is added; the test harness's three theme failures and the launcher failures are the ones
documented earlier, not new ones.

## 5d. Engine status from the log, and what is still wrong about tok/s. 2026-11-07.

Built: `utils/ex_engine_log.py`, stdlib only. It reads the log the launcher writes, never the engine.
Line shapes actually present in the log:

    [strata] thinking: 1220 of budget 126258 (max 126258) tokens, 47.2 tok/s, 263 s
    [strata] writing a tool call: run_command: 932 of max 116842 tokens, 48.4 tok/s, 22 s
    [strata] reading the prompt: 145,289 of 145,294 tokens, 2 s so far
    [strata] done: 979 tokens in 23 s (48.8 tok/s) (stop, cancel=False)
    [strata] done: 0 tokens in 96 s (no speed) (cancel, cancel=True)

Parse rules that matter: a line that prints (no speed) produces no speed and therefore no ETA; the
word after "of" is optional on a prefill line; numbers can be comma-grouped; the tail is read, not the
whole file; the age of the file is part of the answer, so a log that stopped updating is reported as
stale rather than as current.

ETA is arithmetic on the line: (budget - tokens) / speed, seconds only. No model call.

The label separation is the part not to lose. The row already shows a measured arrival rate, which is
a frontend window over the last ten seconds. The engine line is the engine's own decode speed. On a
turn with tool calls they can be far apart, so they are rendered as separate chips and the engine chip
says where it came from.

Still wrong about tok/s, in the order it bites:
1. The measured rate says nothing until the first chunk lands, so the prefill period has no rate at all
   even though the engine is busy. The log line covers that period, which is why the engine chip exists.
2. `wall_seconds` covers generation only. Prefill is reported separately, so a turn that spent most of
   its time reading the prompt shows a small wall figure next to a large one.
3. The backend mirror only takes effect after a restart through owv.sh. The frontend half is live after
   a reload because the row is computed there.
4. `usage` for an agentic turn mixes scopes, so a rate computed from it describes the turn rather than
   the reply.
5. The new settings keys have no control in the tab yet, so engine status runs at its defaults.

Tests: test_ex_engine_log.py 22 checks, test_ex_wiring.py 30, test_ex_e2e.py 47, test_ex_rollover.py 23,
test_ex_stats.py 33. Full harness 799 checks; the failures are the documented theme, theme-mirror,
settings-array, and launcher ones.
