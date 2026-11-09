# Chat death fix, dev fork (2026-11-08), ported from SillyTavern

Cloned to `refsourcecode/SillyTavern` for this. What was actually read from their source, not
assumed:

- `public/scripts/PromptManager.js:1677`: `tokenBudget = openai_max_context - openai_max_tokens`.
  The prompt never gets the whole window; the response is reserved.
- Only the chat-history block is squeezed. Pinned blocks cannot be detached
  (`isPromptDeletionAllowed`), and SillyTavern warns when history falls under a token threshold
  ("Very little of your chat history is being sent"). Blocks are ordered and rebuilt per request.

The port is `backend/open_webui/utils/ex_prompt_assembler.py`. Their warning is turned into an
action, which is the part that kills chat death.

Why this addresses the screenshot: the agent read a prompt where the only surviving content was
system instructions plus a memory listing, concluded there was no user request, and answered by
acknowledging. That happens when a growing conversation is trimmed until the request itself is
gone. In the port, history is optional and the current request is a named block that is never
dropped. If nothing is left but pinned blocks plus the request, the turn still contains a real
request, so there is no "nothing to do" state to reason from.

Block order: main, persona, journal handoff, summary chain, history, current_request. Pinned set:
main, persona, journal, current_request. History is the only squeezable block. When pinned blocks
alone exceed the budget, the module reports `blocked_reason` rather than silently truncating.

Fresh-turn mode (`assemble_turn`) is her "new chat every turn": no prior conversation array is
carried, the prompt is rebuilt from scratch as pinned blocks plus the handoff plus the request.
It engages when history falls below 15% of the remaining budget.

Verified run, no engine call: budget 1952 on a 4000 window with 2048 reserved, 23 history
messages squeezed, 23 journal entries written, handoff block present, current request present,
and `read(entry=1)` returned the evicted text through the builtin tool. Entries now carry id,
turn, kind, ts, summary, message_id, which is the shape `append_journal` requires.

Remaining wiring: the request path must call `assemble_turn` outside the fork lock with an
`entry_writer` that appends, and pass the engine's real token count for `window` rather than the
fork estimate. The builtin tool still needs the manifest regenerated or UI registration.
