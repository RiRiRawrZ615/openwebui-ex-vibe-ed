"""Cap a tool result before it enters the prompt, so one call cannot eat the window.

The failure this exists for: a single tool returns something enormous (a whole file, a recursive
grep, a directory dump, a 900 KB journal entry measured in the logs) and it goes into the prompt
verbatim. One such call can push a long chat past the window and kill it. Nothing in the old path
capped a FRESH tool result; the journal only windowed results that were already old.

Design rules, each one learned the hard way:

1. HEAD AND TAIL, NOT A PREFIX. A truncated prefix is often useless: for a file dump the useful
   part is usually the end, and for a grep the useful part is often both ends. Keep a share of
   each, and put the elision in the middle where the model can see it.

2. SAY IT WAS CUT, IN THE TEXT. A clipped body that reads as complete is the single most damaging
   thing here, because the model reasons from it as if it were whole. So the cap is declared inline,
   with the original size, and with how to get the rest.

3. THE FILE WINS. The full result is written to disk and the cap names the path, so nothing is
   destroyed, and recovery does not depend on a tool that might not be loaded.

4. NEVER CAP A SMALL RESULT. If a result fits under the cap it is returned untouched, byte for byte.
   A capper that reformats everything makes every diff noisy.

5. NEVER RETURN SOMETHING BIGGER. The notice is counted against the budget, so the capped output is
   always materially smaller than the original. An eviction that grows the prompt is pure loss.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Default ceiling for one tool result, in characters. Deliberately generous: a normal tool result is
# a few KB, and the point is to stop the 100KB+ bombs, not to starve the agent.
DEFAULT_MAX_CHARS = 24000
# Of the budget, how much goes to the head. The tail gets the rest, because the end of a command's
# output is usually where the error is.
HEAD_SHARE = 0.55
# Below this there is no point creating a file and a notice; just return the text.
MIN_CAP_CHARS = 2000

SPILL_DIR = "tool_spill"

NOTICE = (
    "\n\n[... TRUNCATED: {kept} of {total} characters shown, {cut} omitted from the middle. "
    "This result is NOT complete: do not treat the heading or the tail as the whole answer. "
    "The full text is on disk at {path} and can be read with "
    'read_cache(ref="{path}", start_line=N, end_line=M), or narrowed with grep_cache.'
    "{extra}]\n\n"
)


def spill_path(data_dir: Any, call_id: str, tool_name: str = "") -> Path:
    """Where a spilled body lives. Named by call id so a rerun cannot clobber another result."""
    root = Path(str(data_dir)) / SPILL_DIR
    stamp = time.strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha1((str(call_id) + str(tool_name) + str(time.time())).encode()).hexdigest()[:10]
    slug = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(tool_name or "tool"))[:40]
    return root / f"{stamp}-{slug}-{digest}.txt"


def cap_tool_output(
    text: str,
    data_dir: Any = "",
    call_id: str = "",
    tool_name: str = "",
    max_chars: int = DEFAULT_MAX_CHARS,
    settings: dict | None = None,
) -> dict:
    """Cap one tool result. Returns {text, capped, kept, total, path, mode}.

    Never raises. A capper that can throw would be able to kill a turn, which is worse than the
    bomb it is defending against.
    """
    result = {
        "text": text if isinstance(text, str) else str(text or ""),
        "capped": False,
        "kept": 0,
        "total": 0,
        "path": "",
        "mode": "off",
    }
    try:
        settings = settings or {}
        body = result["text"]
        result["total"] = len(body)

        # Mode comes from settings. "off" must be a real no-op, because the user asked for a switch.
        mode = str(settings.get("toolCapMode") or "cap").lower()
        result["mode"] = mode
        if mode == "off":
            return result

        try:
            limit = int(settings.get("toolCapChars") or max_chars or DEFAULT_MAX_CHARS)
        except (TypeError, ValueError):
            limit = DEFAULT_MAX_CHARS
        if limit <= 0:
            return result
        # A cap smaller than this cannot pay for its own notice.
        limit = max(MIN_CAP_CHARS, limit)

        if len(body) <= limit:
            result["kept"] = len(body)
            return result

        share = HEAD_SHARE
        try:
            head_share = float(settings.get("toolCapHeadShare") or HEAD_SHARE)
            share = min(0.9, max(0.1, head_share))
        except (TypeError, ValueError):
            share = HEAD_SHARE

        # Measure the notice rather than guessing its size. A long spill path made the capped
        # result 10 characters longer than the ceiling in testing, so the real length is used here
        # and the split gets only what is left.
        placeholder = "/data/openwebvibes/tool_spill/" + ("x" * 60) + ".txt"
        probe = NOTICE.format(kept=0, total=len(body), cut=0, path=placeholder, extra="")
        budget = max(MIN_CAP_CHARS // 2, limit - len(probe) - 8)
        head_len = int(budget * share)
        tail_len = max(0, budget - head_len)
        head = body[:head_len]
        tail = body[-tail_len:] if tail_len else ""

        path_str = ""
        extra = ""
        if data_dir and mode in ("cap", "spill", "drop"):
            try:
                path = spill_path(data_dir, call_id, tool_name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body, encoding="utf-8")
                path_str = str(path)
            except OSError as exc:
                # A failed spill must not lose the result, so fall back to no path in the notice.
                log.warning("EX tool spill failed, capping without a file: %s", exc)
                extra = " (the spill file could not be written, so this is all that survives)"

        if mode == "drop":
            # Explicitly the harshest mode: keep a stub and point at the file.
            result["text"] = (
                "[tool result withheld: %d characters, mode is drop. Full text at %s]"
                % (len(body), path_str or "unknown")
            )
            result["capped"] = True
            result["kept"] = len(result["text"])
            result["path"] = path_str
            return result

        notice = NOTICE.format(
            kept=len(head) + len(tail),
            total=len(body),
            cut=len(body) - len(head) - len(tail),
            path=path_str or "(no path recorded)",
            extra=extra,
        )
        capped = head + notice + tail
        # Never return something larger than what went in.
        if len(capped) >= len(body):
            result["kept"] = len(body)
            return result

        result["text"] = capped
        result["capped"] = True
        result["kept"] = len(capped)
        result["path"] = path_str
        return result
    except Exception:  # noqa: BLE001
        log.exception("EX tool cap failed; returning the result untouched")
        return {
            "text": text if isinstance(text, str) else str(text or ""),
            "capped": False,
            "kept": 0,
            "total": 0,
            "path": "",
            "mode": "error",
        }


def remaining_context_note(
    window: int,
    used: int,
    settings: dict | None = None,
) -> str:
    """A one line budget note, because nothing currently tells the model how much room is left.

    This addresses the observed behaviour where the model shortens its own replies as a chat fills
    up. That is the model guessing from the shape of the transcript, since the fork never reports a
    budget. An explicit number is cheaper than a guess and does not depend on the model noticing.
    """
    settings = settings or {}
    if not settings.get("contextBudgetNote", True):
        return ""
    try:
        window = int(window or 0)
        used = int(used or 0)
    except (TypeError, ValueError):
        return ""
    if window <= 0:
        return ""
    left = max(0, window - used)
    pct = int(round(100.0 * left / window))
    state = "plenty" if pct > 50 else ("tight" if pct > 15 else "nearly full")
    return (
        "[context budget] %d of %d tokens used, %d left (%d%%, %s). "
        "Plan around this: prefer targeted reads over large dumps, and if %d tokens is not enough "
        "to finish, say what remains instead of running out."
        % (used, window, left, pct, state, left)
    )


__all__ = [
    "DEFAULT_MAX_CHARS",
    "cap_tool_output",
    "remaining_context_note",
    "spill_path",
]
