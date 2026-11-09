# Feature vet, dev fork only, 2026-11-08

Everything below is from running code, not from reading it. Stable untouched, no engine call,
nothing sent to the shared strata instance.

## What was actually broken

1. **The slide never ran in a live request.** `ex_slide.py` and `ex_prompt_assembler.py` had zero
   call sites anywhere in `backend/`. The gate at `middleware.py:2445` called `prompt_gate`, which
   trimmed, shed, and then refused. The modules were correct as units and unreachable as features.
   A growing agentic chat therefore still hit the refusal, which is the chat death being reported.

2. **A real slide crashed when it fired.** `ex_slide` handed the writer a raw message dict, and
   `append_journal` reads `e['id']`, `e['turn']`, `e['ts']`, `e['summary']`. Forced run:
   `KeyError: 'id'` at `ex_journal.py:160`. The harness never showed this because the harness ran at
   `window=262144` with a 5312-token prompt, so it never crossed the slide point and printed passes
   for a slide that had not fired.

3. **User turns could not be evicted.** `evict_to_fit` only shed assistant and tool turns. A chat
   whose growth is user text had nothing left to shed and reported `blocked_reason`, which is the
   refusal. Forced run, 30 user turns plus answers, budget 2000: 29 assistant turns evicted, 30 user
   turns remaining, `still_over=True`.

4. **The C chip counted the wrong thing and had gone stale.** It showed `state.rolls`, written only
   by the shed path in `ex_rollover`. With the gate sliding every turn, the number stopped moving
   and, even when it did move, a figure that increments every request is a request counter wearing a
   context label. That is the pointless icon being reported.

5. **A tool call cut off mid-run was read as a loop.** The guard counted an unparseable arguments
   string as an empty call, so truncation and looping were reported as one failure. They need
   different responses: a cut-off call wants a shorter call, a loop wants a different approach.

## What changed

- `ex_slide.py`: entries are built in the shape `append_journal` requires, and `journal_ids` are now
  the entry numbers `read(entry=N)` accepts. `message_ids` are kept beside them. The handoff names
  entry numbers.
- `evict_to_fit`: user turns are evictable, oldest first, and the current request is protected by
  object identity so deletions before it do not move the guard.
- `ex_wiring.py` `prompt_gate`: calls `ex_slide.slide` per turn outside the lock with a writer that
  appends, records the slide state, and reports `slide`, `slid`, `tokens_after_slide`.
- `ex_rollover.py`: `record_slide` counts entries rather than events.
- `ex_stats.py` and `src/lib/utils/exStats.ts`: the C chip shows readable journal entries for the
  chat. `StatsRow.svelte` renders it only when that count is nonzero, so the permanent dimmed marker
  is gone.
- `ex_loop_guard.py`: `parses_as_args`, a `truncated` verdict, `malformed_calls`, and
  `truncated_call_note`, which does not order another call.
- `ex_prompt_assembler.py`: `estimate_tokens` now uses the gate's 3.4 characters per token instead of
  `len // 4`. Two calibrations on one prompt let the assembler declare a block budget met that the
  gate then measured as over.

## Runs

`tests/dev_check_2026_11_08.py` 15 checks, pass.
`tests/dev_harness_2026_11_08.py` 10 checks, pass. Fixed: window was too large to ever fire the
slide, and the builtin tool methods are async so the checks compared a coroutine against a string.
`tests/dev_vet_2026_11_08.py` 32 checks, pass, including the live gate reaching the slide, the state
record for the chip, user-turn eviction with the request protected, the truncated-call verdict, and
frontend mirror parity.

Gate run at window 4000: measured 4081 before, slid, 719 after, `refused=False`.

## Still open

- **The tool is not registered.** `grep -c ex_journal manifest.json` is 0, and `builtin_registry`
  reads only the manifest, so `ex_journal` is absent from the function list. `convert_builtins.py`
  needs `--snapshot`, and no `tool-*.json` export exists on this machine. Until the dev instance is
  restarted and the tool is registered through the UI or a real export, the journal cannot be called
  in a live chat. This is not a code defect.
- **The dev instance must restart.** It is live on 8003, health 200. The gate change takes effect
  only on reload.
- **Refusal is still possible** when protected content alone exceeds the budget. It is now rare, not
  impossible.
- **Estimate versus engine count** is still a margin, not a guarantee, and it matters most on the
  128k machines where there is less room to absorb a miscount.
- **Truncation cause is the output cap**, not the gate. `replyReserve` is the room left for the
  answer, and a long tool payload inside a small reserve is exactly what gets cut mid-call. Raising
  `replyReserve`, or keeping tool payloads short, is the practical control. The guard now reports it
  honestly instead of blaming the model.
