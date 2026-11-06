# OpenWebVibes

**Version 1.0.0 beta**

A personal fork of [Open WebUI](https://github.com/open-webui/open-webui). This is not a product,
not a competitor, and not an attempt to build a platform. It exists because a handful of things
in upstream were either wrong for this setup or missing entirely, and patching them in place is
faster than waiting for someone else to agree they are problems.

The name is a joke about what happens when a chat app grows an opinion.

---

## Why this fork exists

Three problems drove it, in the order they were hit.

**Context windows were being wasted.** The original code reserved a fixed 16384 tokens for
response generation no matter how large the window was. On a 32768 window that threw away half
the available context. On a 16384 window, it threw away three quarters. The engine behind this
install runs with a 262144 window, so the fixed reserve was leaving 6 percent of the model's
actual capacity unusable and there was no reason for it.

**A chat could silently die mid-stream.** A missing import in the message renderer meant the
component threw on every streaming update. The backend saw a healthy request, the browser
unmounted the message, and the turn was simply gone with no error anywhere a person would look.
The Svelte compiler does not catch a missing named import, and the production build happily
shipped it.

**The launcher lied about what was running.** Several scripts claimed a model engine was up
because a port answered, without checking that the thing on that port was the process they had
started. Auth failures reported as success. A port held by an unrelated server was treated as
a working backend. Two thirds of the log output went somewhere nobody was tailing.

Everything else here is a consequence of fixing those three, plus whatever was convenient at
the time.

---

## What is actually in it

### Working, verified

**Proportional context scaling.** The response reserve is now `max(4%, min(20%, requested))`,
raised to fit the requested `max_tokens`, and capped at half the window. A 262144 window gives
94 percent usable context. Everything from 65536 down to 4096 gives 80 percent. Verified
against a live engine with an agentic conversation history at two different window sizes and a
needle recovered correctly in both.

The client-side window value is also read from the right place now. It was being read from the
request metadata, which never contains client parameters, so a user setting the window in the UI
had no effect on the policy at all. It reads the form parameters first.

**Per-chat retrieval memory.** A separate ChromaDB collection per chat, populated from the
conversation and retrieved at generation time, using local MiniLM embeddings so nothing leaves
the machine. Skips itself entirely on very short conversations where retrieval would only add
noise. Two real bugs were found and fixed in the process: a client cache that returned a stale
handle after a reset, and a collection creation path that raced against itself.

**A theme system that reaches the page.** The gradient stopped at the app shell. The root
layout element carries an opaque background, so a gradient on `html` or `body` sat underneath it
and was never visible. Retinting the surface colours could not fix that, because the cover was
still opaque. The shell is now transparent under a theme class, the sidebar is translucent with
a backdrop blur, and the gradient renders. Verified in the built CSS, not just in source.

**Launchers that check what they are talking about.** `owv.sh` verifies that a port is serving
the thing it expects before reusing it, adopts the terminal API key from the database rather
than inventing one, and resolves real server pids by port when a log pipeline would otherwise
capture the wrong process. `owv-status.sh` goes further and performs an actual authenticated call
to the terminal, because a health endpoint returning 200 while every real request gets rejected
is a failure mode that cost an entire day of debugging.

**Backups that are known to restore.** `owv-backup.sh` records the database, the built frontend,
every git branch as a bundle, the launcher scripts, the terminal key, and a manifest of paths,
ports and versions. `owv-restore.sh` defaults to a dry run and moves the live database aside
rather than deleting it. The restore path has been drilled against a real archive into a scratch
directory, not just written.

**Model backend log fan-out.** The engine's output reaches `engine.log` and `llama.log` as well
as the fork's own log, so a single tail sees all three sources.

**Crash supervision.** `owv-watchdog.sh` restarts on a genuine crash and stays out of the way on
a clean exit, an interrupt, or a kill, because restarting a process someone deliberately stopped
is its own kind of broken.

**Turn buffering.** `owv-turns.sh` keeps a file per chat and per message so a dropped stream can
be recovered rather than lost. Round-trip tested.

### Present but incomplete

**Install from UI.** The plan and validation shape exists and reports honestly that it is not
executing anything yet. It is labelled here rather than counted as finished.

**Database import/export.** Whole-database import and export are wired. Per-chat export is not,
even though upstream has it. That is a regression against upstream and is listed as such.

**Settings exposed in the UI.** Backend settings are editable in files but not yet in a panel.

---

## What is deliberately not here

**No licensing work.** This is a personal fork. Upstream is MIT and this stays MIT. If it ever
leaves this machine, that needs revisiting properly. It is not being revisited now.

**No frozen python environment in the packages.** Measured at 7.3 GB, of which 4.2 GB is CUDA
and NVIDIA wheels. This machine runs ROCm. Shipping that environment would produce a
multi-gigabyte package that is still wrong on different hardware. The packages ship source and
the compiled frontend and resolve dependencies against the machine that actually runs them.

**No claim that the AppImage is fully self-contained.** It carries the application. It creates a
python environment on first run. That is a bootstrap, and pretending otherwise would be the kind
of thing that ends with someone filing a bug about a missing library.

---

## Packages

| Artifact | What it is |
| --- | --- |
| `openwebvibes-1.0.0_beta-1.*.noarch.rpm` | Installs to `/opt/openwebvibes`, adds `openwebvibes` to `PATH` |
| `openwebvibes-1.0.0-beta-x86_64.AppImage` | Portable, self-extracting, python env on first run |
| `openwebvibes-1.0.0-beta.tar.gz` | Source plus built frontend, no installer |
| `SHA256SUMS-1.0.0-beta.txt` | Checksums for the above |

The rpm is `noarch` because the contents are source and static assets. Whether the application
runs is a property of the model backend on the target machine, not of the package.

---

## Running it

From a package install:

```
openwebvibes
```

From a source tree:

```
ai-launchers/owv.sh
```

Check what is actually up, including whether the terminal will authorise real calls:

```
ai-launchers/owv-status.sh
```

Take a backup before touching anything:

```
ai-launchers/owv-backup.sh
```

Restore it, dry run first, which is the default:

```
ai-launchers/owv-restore.sh <archive>
ai-launchers/owv-restore.sh <archive> --apply
```

---

## Ports

| Port | Service |
| --- | --- |
| 8080 | Open WebUI |
| 5000 | Model engine supervisor, spawns the backend on demand |
| 8003 | Open Terminal |
| 8004 | Model backend API, OpenAI compatible |

The distinction between 5000 and 8004 matters. Port 5000 is a supervisor whose job is to bring
the backend up on 8004. A backend already running on 8004 that the supervisor did not start is
treated as a conflict, and that is deliberate rather than a bug.

---

## Build notes

The frontend build needs a bounded heap. An unbounded Node build gets killed on this machine,
which reads as the application being too large to build when it is not.

```
NODE_OPTIONS="--max-old-space-size=8192" NODE_ENV=production npm run build
```

That takes about 75 seconds and peaks well under the cap. See `BUILDING.md` for the full
measurements and for why the earlier out-of-memory failures were attributed to the wrong process.

---

## Known sharp edges

The offline path of one development helper removes the uv cache before a sync. Running it while
offline deletes the only copy of a dependency set. This is documented and not yet fixed because
the fix changes behaviour someone may be relying on.

The testing suite has 13 failing assertions, all in launcher tests that predate this work. They
are not caused by anything above and they are not being quietly ignored; they are simply not yet
the priority.

---

## Licence

MIT, inherited from Open WebUI. See `LICENSE`.