"""Mid-generation steering queue (pure).

What exists upstream and what this adds. Open WebUI already has chatRequestQueues
(src/lib/stores/index.ts:148) and processNextInQueue (Chat.svelte:3188). That queue refuses to
run while an assistant message is unfinished: it returns when the last message is a role
assistant with done false. So a message typed during generation waits for generation to end,
then starts a new request. That is a send-after, not a redirect.

A true redirect cannot touch a half-finished thought. Reasoning arrives incrementally from the
provider, so there is no point where text can be inserted inside a thought that is still
streaming. The honest form is: accept the steering item now, hold it, and inject it at the next
iteration boundary, which is where the tool loop already rebuilds messages. That is the same
index ex_loop_guard already uses, so pairing stays intact.

Two delivery modes, and they are different promises:
  - send_after: the current turn finishes, then the item is delivered as a new message. This is
    the stock queue behavior.
  - next_iteration: the item is injected into the message list before the next request, so the
    model sees it mid-turn. This only works while a tool loop is still running, so a steering
    item arriving after the turn closed is reported as pending rather than applied.

Redirect actions are deliberately narrow. A steering item may refuse the next tool call, change
which tool is used next, or add an instruction. It may not rewrite a message that was already
produced, because that would edit the record of what actually happened.

Nothing here executes anything. It decides what the request path should do next.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

QUEUE_NAME = "ex_steering.json"
SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")

MODES = ("next_iteration", "send_after")

# Narrow on purpose. A steering item can stop the next call, swap the tool for the next call,
# or add an instruction. It cannot edit an existing message.
ACTIONS = ("inject_instruction", "refuse_next_call", "swap_next_tool", "stop_turn")

MAX_PENDING = 20
MAX_ITEM_CHARS = 4000


def normalize_chat_id(chat_id: str) -> str:
    cleaned = SAFE_NAME.sub("_", str(chat_id or ""))
    return cleaned or "chat"


def mark_time(item: dict) -> dict:
    return {**item, "applied": True, "applied_at": datetime.now(timezone.utc).isoformat()}


def build_item(
    text: str,
    *,
    chat_id: str,
    mode: str = "next_iteration",
    action: str = "inject_instruction",
    tool: str = "",
    priority: str = "normal",
) -> dict:
    """Make one steering item. Rejects anything the request path cannot honor."""
    text = str(text or "").strip()
    if not text:
        return {"accepted": False, "reason": "empty steering text"}

    if mode not in MODES:
        return {"accepted": False, "reason": f"mode must be one of {MODES}"}
    if action not in ACTIONS:
        return {"accepted": False, "reason": f"action must be one of {ACTIONS}"}
    if len(text) > MAX_ITEM_CHARS:
        return {"accepted": False, "reason": f"text is longer than {MAX_ITEM_CHARS} chars"}
    if action == "swap_next_tool" and not tool:
        # A swap with no tool named is not a decision, it is a wish.
        return {"accepted": False, "reason": "swap_next_tool needs a tool name"}

    return {
        "accepted": True,
        "id": f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
        "chat_id": normalize_chat_id(chat_id),
        "text": text,
        "mode": mode,
        "action": action,
        "tool": str(tool or ""),
        "priority": str(priority or "normal"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "applied": False,
    }


def render_note(item: dict) -> str:
    """Text the model sees. Deterministic, no model call, so it cannot drift from the request."""
    prefix = "[steering]"
    if item.get("action") == "refuse_next_call":
        prefix = "[steering, next tool call refused]"
    elif item.get("action") == "swap_next_tool":
        prefix = f"[steering, use {item.get('tool')} for the next call]"
    elif item.get("action") == "stop_turn":
        prefix = "[steering, end the turn after this]"
    return f"{prefix} {item.get('text', '')}"


def pairing_safe_insert_index(messages: list[dict]) -> int:
    """Same rule as ex_loop_guard: after the last assistant message carrying tool calls, and
    after the tool results belonging to it. Inserting before tool results would leave a call with
    no matching result, which sanitize_tool_pairs strips."""
    last = -1
    for i, message in enumerate(messages):
        if message.get("role") == "assistant" and (message.get("tool_calls") or message.get("output")):
            last = i
    if last == -1:
        return len(messages)
    i = last + 1
    while i < len(messages) and messages[i].get("role") == "tool":
        i += 1
    return i


def plan(messages: list[dict], pending: list[dict], *, turn_open: bool = True) -> dict:
    """What should happen with the queued items at this boundary.

    next_iteration items only apply while a tool loop is still open. If the turn already closed,
    the item is not silently converted into something else; it is reported as pending so the
    caller can deliver it as a new message instead.
    """
    pending = [p for p in (pending or []) if isinstance(p, dict) and not p.get("applied")]
    if not pending:
        # Same key set as the full result. A caller reading one shape cannot assume the other,
        # which is how a quiet empty queue turned into a KeyError.
        return {"apply": [], "defer": [], "stop_turn": False, "refuse_next_call": False,
                "swap_next_tool": "", "turn_open": bool(turn_open),
                "note": "no pending steering items"}

    ordered = sorted(pending, key=lambda p: (p.get("priority") != "high", p.get("created_at") or ""))

    apply = []
    defer = []
    stopped = False
    refused = False
    swapped = ""
    for item in ordered:
        action = item.get("action")

        # Flags are set before placement. An item can carry an action and an instruction at the
        # same time, so deciding placement first and returning early loses the flag, which is what
        # the earlier version did.
        if action == "refuse_next_call":
            refused = True
        elif action == "swap_next_tool":
            swapped = swapped or item.get("tool", "")

        if action == "stop_turn":
            apply.append({"item": item, "insert_at": len(messages), "note": render_note(item)})
            stopped = True
            continue
        if item.get("mode") == "next_iteration" and turn_open:
            # An item that applied cannot also be pending, so this must not fall through.
            apply.append({"item": item, "insert_at": pairing_safe_insert_index(messages), "note": render_note(item)})
            continue
        if action == "send_after":
            defer.append(item)
            continue
        # send_after, or next_iteration with the turn already closed. Reported, not reinterpreted.
        defer.append(item)

    return {
        "apply": apply,
        "defer": defer,
        # Reported only for items that were actually applied. A deferred item cannot change the
        # next request, so reporting its action as live would be a false claim.
        "stop_turn": stopped,
        "refuse_next_call": refused,
        "swap_next_tool": swapped,
        "turn_open": bool(turn_open),
        "note": "applied at the iteration boundary; deferred items go out as new messages",
    }


def mark_applied(item: dict) -> dict:
    return {**item, "applied": True, "applied_at": datetime.now(timezone.utc).isoformat()}


def trim_pending(items: list[dict]) -> list[dict]:
    """Cap the queue. Keeps the newest, the same newest-wins rule used everywhere else."""
    items = [i for i in (items or []) if isinstance(i, dict)]
    return items[-MAX_PENDING:]


def queue_path(data_dir: Path | str, name: str = QUEUE_NAME) -> Path:
    return Path(data_dir) / name


def append_item(data_dir: Path | str, item: dict, name: str = QUEUE_NAME) -> list[dict]:
    """Store one steering item. Newest wins, the same rule used everywhere else."""
    path = queue_path(data_dir, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    items = trim_pending(load_queue(data_dir, name) + ([item] if isinstance(item, dict) else []))
    path.write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")
    return items


def load_queue(data_dir: Path | str, name: str = QUEUE_NAME) -> list[dict]:
    path = queue_path(data_dir, name)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (ValueError, OSError):
        return []
    return [i for i in raw if isinstance(i, dict)] if isinstance(raw, list) else []


def mark_applied(item: dict, data_dir: Path | str | None = None, name: str = QUEUE_NAME) -> dict:
    """Mark one item applied. With a data dir the queue is rewritten; without one this only
    returns the marked copy. An applied item is not deleted, because the record of what was
    injected has to survive for the next context."""
    if data_dir is None:
        return mark_time(item)

    items = load_queue(data_dir, name)
    updated = [mark_time(i) if i.get("id") == item.get("id") else i for i in items]
    if not any(i.get("id") == item.get("id") for i in updated):
        updated.append(mark_time(item))
    path = queue_path(data_dir, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(trim_pending(updated), indent=2) + "\n", encoding="utf-8")
    return updated[-1] if updated else item


def status(settings: dict | None, pending: list[dict] | None) -> dict:
    settings = settings or {}
    pending = [p for p in (pending or []) if isinstance(p, dict)]
    return {
        "steeringQueue": bool(settings.get("steeringQueue", True)),
        "drainAtBoundary": bool(settings.get("drainSteeringAtBoundary", True)),
        "defaultMode": settings.get("steeringMode", "next_iteration"),
        "pending": len([p for p in pending if not p.get("applied")]),
        "applied": len([p for p in pending if p.get("applied")]),
        "max_pending": MAX_PENDING,
        "modes": list(MODES),
        "actions": list(ACTIONS),
        "note": "mid-reasoning injection is impossible; delivery is at the iteration boundary",
        "stock_queue_note": "the stock chatRequestQueues waits for generation to finish, so it is send_after, not a redirect",
    }


__all__ = [
    "QUEUE_NAME",
    "MODES",
    "ACTIONS",
    "MAX_PENDING",
    "MAX_ITEM_CHARS",
    "normalize_chat_id",
    "build_item",
    "render_note",
    "pairing_safe_insert_index",
    "plan",
    "mark_time",
    "mark_applied",
    "trim_pending",
    "queue_path",
    "append_item",
    "load_queue",
    "status",
]
