# Slide spec, dev fork (2026-11-08)

Implemented in `backend/open_webui/utils/ex_slide.py`. Verified with a pure logic run, no engine
call, no strata request.

Behaviour, as specified:

- window is whatever the real window is; `slide_point(ceiling, fraction)` scales, so a 262144
  window slides at 183500 and a 32768 window slides at 22937;
- past the slide point, everything older than the last `KEEP_TAIL_TURNS` (4) agent/user
  responses is evicted, plus the pinned head;
- the **very last reasoning turn** is always kept, even when it sits before the tail window;
- evicted content goes to the journal keyed by message id, and the prompt gets back a
  deterministic block: "Refer to the following context and continue the conversation based on
  this" plus the list of ids the journal tool can pull;
- nothing is refused for size. If nothing load bearing is left, the report says what is blocked.

Check run (window 262144, forced slide point): slid 72 messages, 72 journal ids recorded, last
reasoning kept, newest user kept, handoff block present, no slide when under the point.

## Wiring still needed

`slide()` takes `entry_writer` as a callback rather than calling `append_journal` itself, because
a chat cannot hold the fork lock while its own write is blocked. In `ex_wiring.py` the overflow
path must:

1. run this outside the fork lock;
2. pass an `entry_writer` that appends to the chat's journal and records `message_id`;
3. ensure every message entering the path has an `id` (the journal already keys on
   `message.get("id")`, so ids exist for journalled turns; the request path must carry them
   through so the handoff can name them).

## Strata side

`/home/amd/strata` is 14G, so a copy of the whole tree is not the move. The refusal itself is
`serve/server.py:5291` ("prompt (N tokens) leaves no room to answer in the context (C); requests
are never truncated"). For the dev copy that branch must become evict-and-continue, using the
same 70% slide point, instead of returning 400.

That file is shared with stable and the running instance is the one this chat is on, so the
change cannot be applied live: it only takes effect on a restart, and restarting drops this chat.
RiRi controls when that happens. The dev fork side is already safe: it never builds a prompt
past the slide point, so the strata refusal should stop firing.
