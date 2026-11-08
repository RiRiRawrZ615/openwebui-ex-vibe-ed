"""Engine status read from the log the launcher already writes.

The engine prints one line per second while a turn is running, and that line already carries what
the stats row needs:

    [strata] thinking: 1220 of budget 126258 (max 126258) tokens, 47.2 tok/s, 263 s
    [strata] writing a tool call: run_command: 932 of max 116842 tokens, 48.4 tok/s, 22 s
    [strata] reading the prompt: 145,289 of 145,294 tokens, 2 s so far
    [strata] done: 979 tokens in 23 s (48.8 tok/s) (stop, cancel=False)

Why the log instead of the frontend estimate: the live row's rate is a windowed arrival measurement
over the last ten seconds and says nothing until the first chunk lands. The engine line is the
engine's own number, it updates continuously, and it is the only place that knows how full the
window is during a turn. The ETA is arithmetic on that line, so it costs no model call.

Labels matter. A figure taken from the log is reported as the engine's, never as the measured one.
They are not the same measurement, and on a turn with tool calls they can differ by a lot.

Pure module. stdlib only, no open_webui imports, no network, no database, and it never opens the
engine: it reads a file the launcher writes.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

# Tail window rather than the whole file. The log grows across a session and a per-turn read of the
# full file would be the context bomb this module exists to avoid.
DEFAULT_LINES = 400

# Numbers in the log are sometimes grouped with commas ("145,289 of 145,294 tokens"), so the number
# pattern accepts both shapes.
NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"

# The word after "of" is not always there. A prefill line reads "of 145,294 tokens", so the label is
# optional and the number is what carries the budget.
BUDGET = re.compile(rf"of\s+(?:budget|max|gen max)?\s*({NUM})")
SPEED = re.compile(rf"({NUM}) tok/s")
NO_SPEED = re.compile(r"\(no speed\)")
# Two shapes carry the elapsed time: "tokens, 47.2 tok/s, 263 s" on a running line and "406 tokens in
# 12 s" on a completed one. A pattern that only accepted the comma form dropped elapsed for every
# finished turn.
ELAPSED = re.compile(rf"(?:[ ,]|in)\s+({NUM})\s+s(?:\b|$)")
DONE_TOKENS = re.compile(rf"^({NUM}) tokens in")

PHASES = ("thinking", "answering", "writing a tool call", "reading the prompt", "done")


def normalise_number(text: str) -> int | float | None:
    text = str(text or "").replace(",", "")
    if not text:
        return None
    try:
        return float(text) if "." in text else int(text)
    except ValueError:
        return None


def parse_line(line: str) -> dict | None:
    """One log line to a status record. Anything the shape does not carry is left as None rather
    than inferred: a cancelled turn prints (no speed), and an ETA from a missing speed would be a
    fabricated number."""
    line = str(line or "").strip()
    if not line.startswith("[strata] "):
        return None

    rest = line[len("[strata] "):]
    phase, _, body = rest.partition(":")
    phase = phase.strip().lower()
    if phase not in PHASES:
        return None

    tool = ""
    if phase == "writing a tool call":
        tool, _, body = body.partition(":")
        tool = tool.strip()

    tokens = None
    match = DONE_TOKENS.match(body.strip()) if phase == "done" else re.match(rf"^({NUM})", body.strip())
    if match:
        tokens = normalise_number(match.group(1))

    budget_match = BUDGET.search(body)
    budget = normalise_number(budget_match.group(1)) if budget_match else None

    speed_match = SPEED.search(body)
    speed = normalise_number(speed_match.group(1)) if speed_match else None
    no_speed = bool(NO_SPEED.search(body))

    elapsed_match = ELAPSED.search(body)
    elapsed = normalise_number(elapsed_match.group(1)) if elapsed_match else None

    if tokens is None and budget is None and speed is None:
        return None

    # On a prefill line the number is the size of the request the engine is reading, which is the
    # only figure that says how full the window is. On a thinking or answering line the same number
    # means tokens produced in this turn. Both were landing in the same field, so the row showed a
    # per-turn count as if it were the window fill and the fraction moved in the wrong direction.
    prompt_tokens = tokens if phase == "reading the prompt" else None

    return {
        "phase": phase,
        "tool": tool,
        "tokens": tokens,
        "prompt_tokens": prompt_tokens,
        "budget": budget,
        "speed": speed,
        "no_speed": no_speed,
        "elapsed": elapsed,
        "line": line,
    }


def tail(path: Path, max_lines: int = DEFAULT_LINES) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    limit = max(1, int(max_lines or DEFAULT_LINES))
    return lines[-limit:]


def read_status(path: Path | str, max_lines: int = DEFAULT_LINES, max_age: int = 30) -> dict:
    """The most recent running line, plus the last completed turn.

    Reported as the engine's own figures. Nothing here is presented as a measured arrival rate, and
    the age of the file is part of the answer because a log that stopped updating is not live
    evidence of what the engine is doing now.
    """
    log_path = Path(path)
    lines = tail(log_path, max_lines)
    records = [parse_line(line) for line in lines]
    records = [record for record in records if record]

    running = None
    last_done = None
    for record in reversed(records):
        if record["phase"] == "done":
            if last_done is None:
                last_done = record
        elif running is None:
            running = record
        if running and last_done:
            break

    # The last request size the engine reported. A turn that is still generating has no request size
    # of its own, so the window fill has to come from the most recent prefill reading plus what this
    # turn has produced. Without this the row falls back to a per-turn count and reads as though the
    # window emptied at the start of every turn.
    last_prefill = None
    for record in reversed(records):
        if record.get("prompt_tokens"):
            last_prefill = record
            break

    age = None
    try:
        age = max(0, int(time.time() - log_path.stat().st_mtime))
    except OSError:
        age = None

    return {
        "source": "engine log",
        "last_prefill": last_prefill,
        "path": str(log_path),
        "live": age is not None and age <= int(max_age or 30),
        "age_seconds": age,
        "status": running,
        "last_done": last_done,
        "lines_read": len(lines),
    }


def eta(status: dict | None, settings: dict) -> dict | None:
    """Seconds to fill the remaining window at the engine's own speed.

    Only produced when the line actually carries a speed, a budget, and a token count. A cancelled
    turn prints (no speed), and an ETA from nothing would be a guess dressed as a measurement.
    """
    if not settings.get("engineStatusEta") or not status:
        return None
    if status.get("phase") == "done":
        return None

    tokens = status.get("tokens")
    budget = status.get("budget")
    speed = status.get("speed")
    if not tokens or not budget or not speed:
        return None

    remaining = budget - tokens
    if remaining <= 0:
        return None

    return {
        "remaining_tokens": int(remaining),
        "seconds": round(remaining / speed, 1),
        "based_on": "engine speed",
    }


def resolve_log_path(settings: dict, data_dir: str = "") -> Path:
    """The launcher writes the engine log; a configured path wins over the default, and the default
    is the file the launcher actually uses rather than a path invented here."""
    configured = str(settings.get("engineLogPath") or "").strip()
    if configured:
        return Path(os.path.expanduser(configured))
    # The instance's own state dir, passed by the launcher. Without this the dev instance reads the
    # log of whichever instance wrote to the shared state dir, and the figures shown in the dev row
    # are that instance's turn, not the one being looked at.
    env_path = str(os.environ.get("OWV_ENGINE_LOG") or "").strip()
    if env_path:
        return Path(os.path.expanduser(env_path))
    if data_dir:
        state = Path(os.environ.get("XDG_STATE_HOME") or (Path(data_dir).parent if data_dir else Path.home()))
        candidate = state / "openwebvibes" / "engine.log"
        if candidate.exists():
            return candidate
    return Path.home() / "Documents" / "engine.log"