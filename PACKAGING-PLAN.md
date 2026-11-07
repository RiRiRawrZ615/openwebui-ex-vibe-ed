# Open Web Vibes: packaging, setup, and 1.1 plan

Written 2026-11-06 from RiRi's direction. This is the design of record for the production
push. Read `WORKLOG.md` for what has already been fixed.

---

## 1. What RiRi actually specified

The packaged version **replaces stock Open WebUI with the fork**. It is not a coexistence
story. That removes the hardest question I had been about to ask (replace vs run alongside),
and it simplifies everything downstream: one product, one port, one data dir.

The installer is an **RPM**. The launcher ships **inside the package**. Two user paths:

| user state | experience |
|---|---|
| fresh machine, nothing installed | launcher runs a setup workflow, installs what the user picks (llama.cpp, Strata, bonsai), then starts |
| already has the stack (RiRi's machines) | launcher opens normally; user points it at their llama and Strata bin dirs |

That is one workflow with two entry conditions, not two code paths. The setup workflow and
the dependencies tab are the same surface: what differs is only whether it auto-runs.

Plan-driven vs install is decided by a single settings flag that already exists:
`ex.allowInstall`. When false the backend returns a description; when true it executes.

RiRi's machines keep hardcoded paths. Everyone else gets paths derived from `HOME`.

---

## 2. What already exists (verified, not assumed)

Much more is already built than expected. Do not rebuild these.

### Backend

- `utils/ex_backend.py` — pure, no subprocess, no network.
  - `BACKENDS = ("llamacpp", "strata")`
  - `INSTALLABLE = ("webui", "llamacpp", "strata", "openwebvibes")`
  - `install_plan(components, dry_run=)` returns `{dry_run, steps, refused, runs}`
  - `classify_model()` / `auto_select()` — which backend can serve a given model, decided on
    what the backend actually consumes: llama.cpp serves a `.gguf` directly, Strata serves a
    prepared pack (dir with manifest + shards), so a bare `.gguf` is a llama.cpp candidate and
    is not a Strata candidate until prepared
  - `path_allowed()` — model path must resolve inside allowed roots, because a settings tab
    that reads any path on the machine is not acceptable in a multi-user deployment
- `routers/ex.py` — `GET /install-plan`, and the model/backend probe endpoints
- `utils/ex_import.py` — import a stock Open WebUI DB into the fork data dir

### Frontend

`Settings/OpenWebVibes.svelte` already contains these sections, in order:

1. Runtime (`sections.runtime`) — points at `Handshake from: strata`
2. Behavior (`sections.behavior`)
3. Context window
4. Backend (`sections.backend`)
5. **Install (`sections.install`)** — `allowInstall` switch, a components text input
   (placeholder `webui,llamacpp,strata`), and a plan button
6. Import (`sections.import`)
7. Theme (`sections.theme`)
8. Agentic (`sections.agentic`)

So the "dependencies tab" is **not missing**. It exists as the Install section and it is
plan-only: `install-plan` is called with `dry_run=not settings.get("allowInstall")`, and even
when it runs, `install_plan` returns *descriptions*, never commands, on purpose.

`SettingsModal.svelte` mounts the tab through a `settings_tab` slot with a `Builtin={OpenWebVibes}`,
and carries a `forkHiddenTabs` set (currently only `admin:evaluations`).

---

## 3. The actual gaps

Ordered by what blocks 1.1.

### G1. `bonsai` is not installable
`INSTALLABLE` is `webui, llamacpp, strata, openwebvibes`. RiRi named bonsai as a component the
setup must install. Add it, with the same refuse-unknown guarantee.

### G2. The install executes nothing
`install_plan` returns steps as text. Nothing runs them. This is deliberate and correct as a
foundation, but the Setup tab needs execution. Design constraint: **the plan stays
descriptions; a separate runner executes them, one step at a time, with output streamed to
the log the UI already needs.** Never build a shell command by string concatenation.

### G3. No binary discovery or manual select
RiRi's requirement: "you pick your llama and strata bin dirs". Needed:
- probe a list of candidate locations, including RiRi's (`~/llama.cpp-rdna2-fa/build/bin`,
  `~/llama.cpp-nvidia/build/bin`, `~/llama.cpp/build/bin`), then `$PATH`
- report what was found with version, so the UI can show real state
- accept a manual path, validate it is executable, and persist it through the existing
  settings store
The reference for candidate order and backend auto-detection is
`~/Documents/openwebui_llamacpp_safe_moe_v4.sh` (AMD/NVIDIA/generic selection) and
`~/Documents/strata_openwebui_launcher.sh`.

### G4. Boot order
RiRi's note, and the correction is his own: UI first, then the engine, probably does not work,
because the UI has nothing to talk to until the engine is up. The honest answer is the launcher
starts the engine and the UI in parallel and the UI reports engine readiness in the Setup tab.
`Handshake from: strata` in the Runtime section is already that handshake. Do not invert the
order expecting the UI to come up first; make the UI honest about waiting instead.

### G5. No RPM packaging
Needed for the release:
- `openwebvibes.spec` with `%install` placing the launcher and the prebuilt frontend
- `Requires:` on python, node (build-time only if we ship a prebuilt `build/`), tmux
- a watching service: install-time detection of existing stack vs fresh machine
- the launcher must resolve its own location so it works from `/usr/lib/openwebvibes/` as
  well as from a git checkout. `owv.sh` already derives from `HOME`/`XDG` with zero hardcoded
  `/home/amd` paths, so this is mostly a matter of adding a config search that prefers
  `/etc/openwebvibes/owv.conf` then `~/.config/openwebvibes/owv.conf`

### G6. Builtin tools
Port the 9 exports from `Code/openwebui-tools/repo_snapshot/` as native builtins:
`duckduckgo_search`, `leanrea_reverse_v2`, `social_reddit_x`, `youtube_visual`, `fleet_ops`,
`fleet_ssh`, `pcalc`, `cognitive_memory_vault`, `youtube_video_parser`.
- our web search replaces their DuckDuckGo
- each is individually toggleable
- an **edit overlay**: a user edit is stored separately and takes precedence over the builtin,
  so a fork release can update the builtin without clobbering the user's version. This is the
  same shape as the existing backup/original pairing used for branding.

---

## 4. Portability audit result

Verified by grep across every launcher:

| file | hardcoded `/home/amd` | notes |
|---|---|---|
| `owv.sh` | **0** | already derives from `HOME`/`XDG` |
| `kill-owv`, `owv.aliases`, `owv-restart-webui.sh` | **0** | |
| `owv-backup.sh`, `owv-restore.sh`, `owv-release.sh`, `owv-status.sh` | **0** | |
| `openwebui_llamacpp_safe_moe_v4.sh` | 1 | legacy, not on the fork path |
| `owv_swap_to_fork.sh` | 6 | one-off migration, expected |
| `tavern_launcher.sh` | 2 | unrelated project |

Conclusion: the fork's launcher is already portable. The work is the packaging layer (G5),
not a rewrite. This is a much smaller task than it appeared.

---

## 5. Suggested order

1. **G1 + G3 + G2** — make the Install section real: bonsai installable, binary discovery and
   manual select, and a real runner with a streamed log. This IS the Setup tab.
2. **G5** — RPM packaging, since the Setup tab is the only thing the package needs to be
   useful and it now works.
3. **G6** — builtin tools, before the 1.1 freeze.
4. **G4** — readiness reporting polish.

---

## 6. Standing rules that this file inherits

- Never restart the server mid-turn. There is no turn recovery; a restart deletes the response.
  Ask, always.
- Commit before reporting. Uncommitted work dies with a restart.
- Frontend changes are not live until `npm run build` rewrites `build/`. Verify by hashing
  served bytes against disk:
  `curl -s localhost:8080/_app/immutable/chunks/<hash>.js | md5sum` vs the same file on disk.
- The real chat render path is
  `ResponseMessage -> ContentRenderer -> StructuredOutputRenderer -> buildOutputDisplayItems()`
  in `Messages/structuredOutput.ts`. Editing `MarkdownTokens.svelte` for a normal chat response
  is editing a file that is not on the path.
- `ai-launchers/` is not a git repo. It is only preserved by `backup-owv`.
