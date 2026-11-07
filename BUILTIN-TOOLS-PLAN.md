# Open Web Vibes builtin tools registry

Design of record for making the 9 repo tools native, toggleable, and editable. Read with
`PACKAGING-PLAN.md` (release direction) and `WORKLOG.md` (what is already fixed).

---

## 1. Requirement

From RiRi, for the 1.1 release:

1. The 9 tools become **builtins**, shipped inside the product rather than installed by users.
2. Each is **individually toggleable**.
3. Our **web search replaces** the bundled DuckDuckGo one.
4. An **edit overlay**: a user edit is stored separately and wins over the builtin, so a
   future release can update the builtin without clobbering the user's version.
5. The craft graphs JS renderer becomes native UI, as a builtin.

---

## 2. Inventory, measured from the exports

Read from `Code/openwebui-tools/repo_snapshot/`. Counts are actual functions in `specs`.

| id | name | fns | chars | needs network |
|---|---|---|---|---|
| `custom_duckduckgo_search_30` | Web Search | 3 | 13,291 | yes |
| `custom_leanrea_reverse_v2` | leanrea Reverse Engineer | 21 | 86,883 | no |
| `custom_social_reddit_x_1` | Reddit + X Reader | 8 | 30,503 | yes |
| `custom_youtube_visual_1` | YouTube Visual Reader | 4 | 25,604 | yes |
| `fleet_ops` | Fleet Ops | 14 | 31,433 | no |
| `fleet_ssh` | Fleet SSH + RustDesk | 7 | 11,477 | no |
| `pcalc` | Calculator | 4 | 11,025 | no |
| `universal_cognitive_memory_vault` | Cognitive Memory Vault | 6 | 7,434 | no |
| `youtube_video_parser` | YouTube Video Parser | 1 | 6,818 | yes |

Function names, for collision checking against the native toolset:

- **Web Search**: `fetch_url`, `research`, `search`
- **leanrea**: `toolchain`, `install`, `ensure`, `rules`, `plan`, `route`, `copy`, `recon`,
  `run`, `search`, `slice`, `outgrep`, `extract`, `decompile`, `parse`, `find`, `answer`,
  `log`, `resume`, `handoff`, `stop`
- **Reddit + X**: `reddit_timeline`, `reddit_thread`, `reddit_user`, `reddit_subreddit`,
  `x_tweet`, `x_profile`, `social_search`, `fetch_social`
- **YouTube Visual**: `read_youtube_video`, `youtube_frame_at`, `youtube_frame_urls`,
  `youtube_visual_scenes`
- **Fleet Ops**: `adb`, `check`, `fleet`, `ingest`, `password`, `probe`, `run`, `rustdesk`,
  `scan`, `session_open`, `session_read`, `session_send`, `sessions`, `vault`
- **Fleet SSH**: `check`, `fleet`, `pull`, `push`, `run`, `run_sudo`, `rustdesk`
- **Calculator**: `calculate`, `evaluate_batch`, `solve_linear`, `solve_quadratic`
- **Cognitive Memory**: `knowledge_guidance`, `list_paths`, `read_path`, `recall`,
  `remember`, `update_memories`
- **YouTube Parser**: `get_youtube_video`

### Collisions that matter
`search` appears in both Web Search and leanrea. `check`, `fleet`, `run` and `rustdesk`
appear in both fleet tools. `find`, `plan`, `copy`, `parse`, `log`, `stop` overlap
leanrea's internals. So a flat namespace is not viable: builtins must be namespaced per
tool on load, and the toggle must operate per tool, not per function.

### The web search replacement
`custom_duckduckgo_search_30` exposes `search`, `fetch_url`, `research`. Our own web search
already provides exactly those three names with the same shape (cache-first, `read_cache`,
`grep_cache`, `cache_index`, `cache_prune`). So the replacement is a drop-in at the spec
level: the builtin of the same name resolves to our implementation, and the DuckDuckGo one
is not shipped at all rather than shipped and disabled. Shipping dead network code in a
release is a liability, not a feature.

---

## 3. Proposed storage layout

```
<DATA_DIR>/builtins/
    registry.json          # which builtins exist, enabled state, version stamp
    overlay/<id>.py        # a user edit, takes precedence over the shipped copy
    shipped/<id>.py        # copied in at first boot from the package for reference
```

Resolution order for a tool id: `overlay/<id>.py` then the in-package builtin. The overlay
is a whole-file replacement, deliberately, because a line-level patch would silently rot as
the builtin changes across releases. A whole-file override is honest: it either still works
or it visibly fails.

`registry.json` holds `{ id: {enabled: bool, source: 'builtin'|'overlay', updated_at}}`.
Toggles write here. An overlay edit records a hash of the builtin it was based on, so the
UI can warn "this edit is based on an older version" rather than silently shadowing an
update.

---

## 4. Where it hooks in

Native tool loading is `utils/tools.py:365 get_tools(request, tool_ids, user, extra_params)`,
with `utils/plugin.py:319 get_tools_cache(request)`. Builtins should join at the same layer
so they appear in the tool picker and are governed by the same permission model, rather than
being injected lower down where they would bypass it.

The toggle surface belongs in the OpenWebVibes settings tab, next to Install, and the edit
UI belongs in the existing Workspace tool editor if it can be reused instead of building a
second editor.

---

## 5. Craft graphs

The renderer is a JS file rather than a Python tool, and the upstream frontend never allowed
arbitrary component display, which is why it could only exist as a tool before. Now that the
fork owns the renderer, the graph can render as a real UI component: a builtin tool whose
output carries a structured payload, plus a renderer registered in
`src/lib/components/chat/Messages/` that StructuredOutputRenderer dispatches to.

That means it needs a display-item type, exactly like the `reasoning` item added this
session. The existing `buildOutputDisplayItems()` is the right place to detect the payload
and emit the item.

---

## 6. Order of work

1. Registry + resolution + toggle persistence, with `pcalc` as the first ported tool because
   it is pure, dependency-free, and verifiable.
2. Our web search as the `search`/`fetch_url`/`research` builtin, replacing DuckDuckGo.
3. Overlay edit path with the base-hash warning.
4. Remaining tools, checking each for imports that are not in the shipped environment. The
   fleet tools in particular shell out and read credentials, so they need an explicit
   decision about whether they ship enabled.
5. Craft graphs renderer as a display item.

---

## 7. Open questions for RiRi

1. **Do the fleet tools ship enabled by default?** They run commands over SSH and read a
   credential vault. For a stranger's machine they are inert without a vault, but shipping
   them enabled is a choice worth making deliberately rather than by default.
2. **Does the overlay edit byte-replace the file, or patch by function?** I have proposed
   whole-file replacement because it cannot rot silently.
3. **Which tools ship in the RPM** versus remaining optional? The leanrea tool is 87k of
   reverse-engineering helpers, which is a large surface to ship to everyone.
