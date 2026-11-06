# Building OpenWebVibes

The frontend build is the step that blocks everything else: packaging, screenshots, and any UI
change reaching a user. It has failed repeatedly on this machine, and the reason was never
memory pressure from the app itself.

## The trap

`npm run build` was being killed by the kernel OOM handler. Measuring it settled what was
actually holding the RAM:

```
39.8 GB  strata-host     <- the local model engine
 2.0 GB  python          <- the webui server
```

The engine holds roughly 40 GB of the 62 GB box. Unbounded, V8 then grows past what is left
during the SvelteKit SSR pass and the kernel kills it. The fix is not to stop the engine; it is
to bound the heap so V8 collects instead of assuming it can take the machine.

## The build

```bash
NODE_OPTIONS="--max-old-space-size=8192" NODE_ENV=production npm run build
```

Measured on this machine with the engine running: **75 seconds, exit 0**. The same build
unbounded was OOM-killed every time.

An 8 GB cap is generous for this app. If it still dies, try 4096 before assuming something is
wrong with the code.

## Verifying the build actually contains the change

A successful build is not the same as a build containing your change. Check for a string that
only exists in the new code:

```bash
grep -rl "Passages per turn" build/_app/immutable/ | wc -l
```

Non-zero means the bundle has it. This matters because `build/` is committed, so a stale
directory can sit there looking current while the source has moved on.

## Notes

- `build/` is ~248 MB.
- The output uses `@sveltejs/adapter-static`; the site is written to `build`.
- Run the build from the fork root, not from `backend/`.
- The Python side needs no build step, but `WEBUI_SECRET_KEY` must be set for any direct
  `uvicorn` launch, otherwise startup aborts with a hard error.