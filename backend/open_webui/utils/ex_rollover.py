"""Rolling context. Verbatim per-chat dumps, plus reasoning blocks addressed by id.

Store shape, one folder per chat, under DATA_DIR/ex_previous_context:

    <chat_id>/chat.txt           the chat in the exact shape the download-as-txt button produces
    <chat_id>/index.tsv          message_id, section id, role, reasoning file, excerpt
    <chat_id>/manifest.json      chat id, the chat it was carried from, last write
    <chat_id>/reasoning/<id>.md  one file per reasoning block

Two halves that only work together. The dump is verbatim because that is the only representation
that can be read back without interpretation: a paraphrase is where facts are lost. Reasoning is
kept as separate files with stable ids because a block that has left the prompt should be pullable
on demand, not summarised on the way back in.

The export shape is fixed by the frontend: ChatMenu.svelte builds `### {ROLE}\n{content}\n\n`, and
ex_context.parse_export maps exactly USER, ASSISTANT, SYSTEM, TOOL. A role the parser cannot map
would come back as content, so an unmapped role is skipped here rather than invented, and no
marker line is written inside a section. The id mapping lives in index.tsv, not in the text.

Pure module. stdlib only, no open_webui imports, no network, no database, no timestamps that
depend on the model. Nothing here ever decides what a message meant.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

FOLDER = "ex_previous_context"
CHAT_FILE = "chat.txt"
INDEX_FILE = "index.tsv"
MANIFEST_FILE = "manifest.json"
REASONING_DIR = "reasoning"
CARRY_FILE = "carry.json"

# Same four roles the export parser accepts.
ROLE_LABELS = {"user": "USER", "assistant": "ASSISTANT", "system": "SYSTEM", "tool": "TOOL"}

# A section id as it appears in the injected index. Read back from the model's own text when it
# cites one, which is the whole on-demand mechanism: the id is the address, the file is the body.
SECTION_ID = re.compile(r"R-[0-9a-f]{6,12}")

INDEX_COLUMNS = ("message_id", "section_id", "role", "reasoning_file", "excerpt")


def chat_dir(data_dir: str, chat_id: str) -> Path:
    return Path(data_dir) / FOLDER / str(chat_id)


def flat_file(data_dir: str, chat_id: str) -> Path:
    # The paste route writes one file per chat. The reader has to keep reading it, so both shapes
    # are supported and neither is preferred by force.
    return Path(data_dir) / FOLDER / f"{chat_id}.txt"


def section_id(message_id: str) -> str:
    """Stable id for a message. Same id for the same message on every write, so an index line the
    model quoted last turn still resolves this turn."""
    return "R-" + hashlib.sha1(str(message_id).encode("utf-8")).hexdigest()[:10]


def message_text(message: dict) -> str:
    """Rendered text for one message, mirroring the download button.

    The save path already stores content as the output text, so this is usually just content. The
    output fallback is there for a message saved before that, and for a turn whose reply only exists
    as structured output.
    """
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content

    output = message.get("output")
    if isinstance(output, list):
        parts = []
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            body = item.get("content")
            if isinstance(body, str):
                parts.append(body)
            elif isinstance(body, list):
                parts.extend(
                    str(part.get("text", ""))
                    for part in body
                    if isinstance(part, dict) and part.get("type") == "text"
                )
        text = "\n".join(p for p in parts if p.strip())
        if text.strip():
            return text
    return ""


def reasoning_text(message: dict) -> str:
    """Reasoning for one message. Same field rule as ex_journal.extract_reasoning_text, kept local
    because pure modules stay import-free."""
    output = message.get("output")
    if not isinstance(output, list):
        return ""
    text = ""
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "reasoning":
            continue
        parts = item.get("summary") or item.get("content") or []
        if isinstance(parts, str):
            text += parts
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "output_text" or "text" in part:
                text += str(part.get("text", ""))
    return text


def branch_order(chat: dict) -> list[dict]:
    """The branch the user is looking at, not the insertion order of the message dict.

    chat_message_list returns dict values, which is insertion order. A chat with a forked branch
    exports the branch through history.currentId, so the dump has to walk parents or a rolled-over
    chat inherits the wrong line of the conversation.
    """
    if not isinstance(chat, dict):
        return []

    history = chat.get("history")
    messages = history.get("messages") if isinstance(history, dict) else None
    if isinstance(messages, dict) and messages:
        current = None
        if isinstance(history, dict):
            current = history.get("currentId") or history.get("current_message_id")
        node = messages.get(current) if current else None
        if node is None:
            return list(messages.values())

        order: list[dict] = []
        seen: set[str] = set()
        while isinstance(node, dict):
            mid = str(node.get("id") or "")
            if not mid or mid in seen:
                break
            seen.add(mid)
            order.append(node)
            parent = node.get("parent_id") or node.get("parentId")
            node = messages.get(parent) if parent else None
        return list(reversed(order))

    stored = chat.get("messages")
    return stored if isinstance(stored, list) else []


def render_chat_text(messages: list[dict]) -> str:
    """The export shape, byte for byte."""
    parts = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").lower()
        if role not in ROLE_LABELS:
            continue
        text = message_text(message)
        if not text.strip():
            continue
        parts.append(f"### {ROLE_LABELS[role]}\n{text}\n\n")
    return "".join(parts)


def store_chat(
    data_dir: str,
    chat_id: str,
    messages: list[dict],
    settings: dict,
    user_id: str = "",
    source_chat_id: str = "",
) -> dict:
    """Write the folder for a chat. Whole-file write, so it is naturally idempotent: the same
    messages produce the same bytes, and an edited message is rewritten rather than appended twice."""
    if not settings.get("rollover"):
        return {"written": False, "reason": "rollover is off"}

    root = chat_dir(data_dir, chat_id)
    root.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    parts: list[str] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").lower()
        if role not in ROLE_LABELS:
            continue
        mid = str(message.get("id") or "")
        text = message_text(message)
        if not text.strip():
            continue

        sid = section_id(mid or text[:32])
        thinking = reasoning_text(message) if settings.get("reasoningFiles") else ""
        rfile = ""
        if thinking.strip():
            rdir = root / REASONING_DIR
            rdir.mkdir(parents=True, exist_ok=True)
            rfile = f"{REASONING_DIR}/{sid}.md"
            (root / rfile).write_text(thinking, encoding="utf-8")

        excerpt_source = thinking.strip() or text.strip()
        limit = max(0, int(settings.get("reasoningExcerptChars") or 160))
        excerpt = excerpt_source.replace("\n", " ")[:limit]

        rows.append(
            {
                "message_id": mid,
                "section_id": sid,
                "role": role,
                "reasoning_file": rfile,
                "excerpt": excerpt,
            }
        )
        parts.append(f"### {ROLE_LABELS[role]}\n{text}\n\n")

    export = "".join(parts)
    (root / CHAT_FILE).write_text(export, encoding="utf-8")
    (root / INDEX_FILE).write_text(
        "\n".join("\t".join(str(row.get(column) or "") for column in INDEX_COLUMNS) for row in rows) + "\n",
        encoding="utf-8",
    )
    _write_manifest(
        root,
        chat_id,
        user_id,
        source_chat_id,
        sections=len(rows),
        chars=len(export),
        blocks=sum(1 for row in rows if row["reasoning_file"]),
    )
    return {
        "written": True,
        "path": str(root),
        "sections": len(rows),
        "chars": len(export),
        "reasoning_blocks": sum(1 for row in rows if row["reasoning_file"]),
    }


def read_chat(data_dir: str, chat_id: str) -> str:
    root = chat_dir(data_dir, chat_id)
    candidate = root / CHAT_FILE
    if candidate.exists():
        try:
            return candidate.read_text(encoding="utf-8")
        except OSError:
            return ""
    flat = flat_file(data_dir, chat_id)
    if flat.exists():
        try:
            return flat.read_text(encoding="utf-8")
        except OSError:
            return ""
    return ""


def read_index(data_dir: str, chat_id: str) -> list[dict]:
    root = chat_dir(data_dir, chat_id)
    path = root / INDEX_FILE
    if not path.exists():
        return []
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            fields = line.split("\t")
            if len(fields) < len(INDEX_COLUMNS):
                fields += [""] * (len(INDEX_COLUMNS) - len(fields))
            rows.append(dict(zip(INDEX_COLUMNS, fields)))
    except OSError:
        return []
    return rows


def read_reasoning(data_dir: str, chat_id: str, sid: str) -> str:
    """One block by id. The id is the only way in, so nothing here guesses which block was meant."""
    sid = str(sid or "").strip()
    if not SECTION_ID.fullmatch(sid):
        return ""
    root = chat_dir(data_dir, chat_id)
    hits = [root / REASONING_DIR / f"{sid}.md"]
    # An index row stores the path relative to the chat folder; follow it rather than assuming.
    for row in read_index(data_dir, chat_id):
        if row.get("section_id") == sid and row.get("reasoning_file"):
            hits.insert(0, root / row["reasoning_file"])
            break
    for hit in hits:
        if hit.exists():
            try:
                return hit.read_text(encoding="utf-8")
            except OSError:
                return ""
    return ""


def _manifest(root: Path) -> dict:
    path = root / MANIFEST_FILE
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_manifest(root: Path, chat_id: str, user_id: str, source_chat_id: str, sections: int, chars: int, blocks: int) -> None:
    existing = _manifest(root)
    manifest = {
        "chat_id": chat_id,
        "user_id": user_id or existing.get("user_id", ""),
        "source_chat_id": source_chat_id or existing.get("source_chat_id", ""),
        "sections": sections,
        "chars": chars,
        "reasoning_blocks": blocks,
        "written_at": int(time.time()),
    }
    try:
        # Provenance can be written before the chat folder exists, because a brand new chat records
        # where it came from before it has any content of its own.
        root.mkdir(parents=True, exist_ok=True)
        (root / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except OSError:
        pass


def manifest(data_dir: str, chat_id: str) -> dict:
    return _manifest(chat_dir(data_dir, chat_id))


def mark_carry(data_dir: str, chat_id: str, user_id: str) -> dict:
    """Record that the next chat should inherit this one. Written when the trigger fires, read once."""
    root = Path(data_dir) / FOLDER
    root.mkdir(parents=True, exist_ok=True)
    carry = {
        "user_id": user_id or "",
        "source_chat_id": chat_id,
        "consumed": False,
        "written_at": int(time.time()),
    }
    try:
        (root / CARRY_FILE).write_text(json.dumps(carry, indent=2), encoding="utf-8")
    except OSError:
        return {}
    return carry


def resolve_source(data_dir: str, chat_id: str, user_id: str = "", consume: bool = True) -> str:
    """Which chat's dump this chat should read.

    Own folder first, because a chat that has been written to has its own dump. Then the recorded
    provenance in the manifest, which is why a chat keeps reading the same source across turns.
    Then the carry pointer, consumed once so a third chat does not inherit a chat it was never
    started from.
    """
    root = chat_dir(data_dir, chat_id)
    recorded = _manifest(root).get("source_chat_id") or ""
    if recorded:
        return str(recorded)

    carry_path = Path(data_dir) / FOLDER / CARRY_FILE
    if carry_path.exists():
        try:
            carry = json.loads(carry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            carry = {}
        if isinstance(carry, dict) and not carry.get("consumed"):
            owner = carry.get("user_id") or ""
            if not owner or not user_id or owner == user_id:
                source = str(carry.get("source_chat_id") or "")
                if source and source != chat_id:
                    if consume:
                        carry["consumed"] = True
                        try:
                            carry_path.write_text(json.dumps(carry, indent=2), encoding="utf-8")
                        except OSError:
                            pass
                    _write_manifest(root, chat_id, user_id, source, 0, 0, 0)
                    return source

    return chat_id


def index_block(data_dir: str, chat_id: str, settings: dict) -> str:
    """The injectable index: ids and short excerpts, never the blocks themselves.

    The point is that the model can name a section it wants without the whole reasoning transcript
    being re-read into the prompt every turn. The index is the cheap part; the block is pulled only
    when it is asked for.
    """
    rows = read_index(data_dir, chat_id)
    if not rows or not settings.get("reasoningOnDemand"):
        return ""
    lines = [f"[REASONING INDEX {chat_id}]"]
    for row in rows:
        if not row.get("reasoning_file"):
            continue
        excerpt = row.get("excerpt") or ""
        lines.append(f"{row['section_id']} {row['role']}: {excerpt}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def on_demand(data_dir: str, chat_id: str, text: str, settings: dict) -> list[dict]:
    """Blocks for the ids that appear in the incoming text. No model call, no similarity guess: an
    id is an address, and a cited address is retrieved exactly."""
    if not settings.get("reasoningOnDemand"):
        return []
    found: list[dict] = []
    seen: set[str] = set()
    for sid in SECTION_ID.findall(str(text or "")):
        if sid in seen:
            continue
        seen.add(sid)
        block = read_reasoning(data_dir, chat_id, sid)
        if block:
            found.append({"section_id": sid, "content": block})
    return found


def threshold_crossed(tokens_before: int, budget: int, ratio: float) -> bool:
    """The trigger reads the count before trimming, not after.

    Trimming makes a chat read emptier than it is, so a post-trim figure would move the rollover
    point depending on how much tool output happened to be stale. The chat's real size is what
    matters here.
    """
    if budget <= 0 or ratio <= 0:
        return False
    return (tokens_before / budget) >= ratio


# --------------------------------------------------------------------------------------
# shedding: the rollover has to shrink the prompt, not only point at the next chat
# --------------------------------------------------------------------------------------


# Kept as a local copy so this module stays import-free. Same rule as
# ex_context_window estimate_tokens, which is calibrated at 3.4 characters per token for this
# tokenizer.
CHARS_PER_TOKEN = 3.4


def estimate_tokens(text: Any, chars_per_token: float = CHARS_PER_TOKEN) -> int:
    if not text:
        return 0
    if not isinstance(text, str):
        text = str(text)
    ratio = max(1.0, float(chars_per_token or CHARS_PER_TOKEN))
    return max(1, int(len(text) / ratio) + 1)


def shed_boundary(
    messages: list[dict],
    target_tokens: int,
    token_counter: Any = None,
    protect_tail: int = 4,
) -> int:
    """Index that keeps the newest tail inside target_tokens.

    Walks from the end and stops when the tail is small enough, then snaps back off any tool
    exchange: a tool call whose result was shed is an invalid message list, and a chat template
    rejects that outright. The tail is protected because those are the turns being discussed.
    """
    messages = list(messages or [])
    if not messages:
        return 0
    counter = token_counter or (lambda m: estimate_tokens(str(m.get("content") or "")) + 4)
    target = max(0, int(target_tokens or 0))

    running = 0
    boundary = len(messages)
    for index in range(len(messages) - 1, -1, -1):
        running += counter(messages[index])
        if running > target:
            boundary = index + 1
            break
    else:
        return 0

    # Do not split a tool call from its result, and do not shed into the protected tail.
    while boundary > 0 and boundary < len(messages):
        previous = messages[boundary - 1]
        current = messages[boundary]
        if isinstance(previous, dict) and previous.get("tool_calls"):
            boundary -= 1
            continue
        if isinstance(current, dict) and current.get("tool_call_id"):
            boundary -= 1
            continue
        break
    return max(0, min(boundary, len(messages) - max(0, int(protect_tail))))


def last_reasoning_block(data_dir: str, chat_id: str, settings: dict) -> str:
    """The most recent reasoning block, verbatim.

    This is the part of the evicted context that has to come back into the prompt. Everything else
    can be pulled by id on demand, but the last turn's reasoning is what the next turn is actually
    continuing from: without it the model has an address and no thought. It is read from the file the
    index already stores, not reconstructed, so what comes back is what was written.
    """
    rows = read_index(data_dir, chat_id)
    if not rows:
        return ""
    newest = None
    for row in reversed(rows):
        if row.get("reasoning_file"):
            newest = row
            break
    if not newest:
        return ""

    root = chat_dir(data_dir, chat_id)
    path = root / str(newest.get("reasoning_file") or "")
    try:
        if not path.exists():
            return ""
        body = path.read_text(encoding="utf-8")
    except OSError:
        return ""

    cap = max(200, int(settings.get("shedReasoningChars") or 2000))
    sid = str(newest.get("section_id") or "")
    if len(body) > cap:
        body = body[:cap] + "\n[truncated at the cap; the full block is on disk at this id]"
    return f"[last reasoning turn {sid} of {chat_id}]\n{body}"


def shed_index_block(
    data_dir: str,
    chat_id: str,
    settings: dict,
    shed_messages: list[dict],
    file_path: str,
) -> str:
    """The block that replaces the shed span: an address, then ids and excerpts.

    Nothing here paraphrases. The ids are the same ids the index stores, so a section the model
    quoted before the shed still resolves after it. The block is the cheap part; the body stays on
    disk and is pulled only when it is asked for.
    """
    rows = {row.get("message_id"): row for row in read_index(data_dir, chat_id)}
    limit = max(0, int(settings.get("reasoningExcerptChars") or 160))
    target = file_path if str(file_path).endswith(CHAT_FILE) else f"{file_path}/{CHAT_FILE}"
    lines = [
        f"[ROLLOVER {chat_id}] the oldest {len(shed_messages)} sections are on disk at "
        f"{target}; the ids below are addresses, pull a section by id instead of assuming "
        "what it said."
    ]
    for message in shed_messages or []:
        if not isinstance(message, dict):
            continue
        mid = str(message.get("id") or "")
        content = str(message.get("content") or "")
        sid = section_id(mid or content[:32])
        role = str(message.get("role") or "").lower()
        excerpt = (rows.get(mid) or {}).get("excerpt") or content.replace("\n", " ")[:limit]
        if not excerpt:
            continue
        lines.append(f"{sid} {role}: {excerpt}")
    if len(lines) == 1:
        return ""
    block = "\n".join(lines)
    cap = max(1000, int(settings.get("rolloverMaxChars") or 60000))
    block = block[:cap]

    # The fed-back context is the address plus the last turn's reasoning, nothing else. That is the
    # shape that keeps a chat coherent after eviction while the body stays on disk and in the vector
    # store, retrievable by meaning rather than carried in the prompt.
    if settings.get("reasoningFiles") and settings.get("shedFeedBackReasoning", True):
        tail = last_reasoning_block(data_dir, chat_id, settings)
        if tail:
            block = f"{block}\n\n{tail}"
    return block


def shed_for_prompt(
    data_dir: str,
    chat_id: str,
    messages: list[dict],
    settings: dict,
    target_tokens: int,
    user_id: str = "",
    token_counter: Any = None,
) -> tuple[list[dict], dict]:
    """Move the oldest sections out of the prompt, keeping the verbatim copy on disk.

    Order matters and is the whole safety property: the dump is written first, so nothing leaves
    the prompt before its copy exists. The stored chat is the source of truth; the prompt keeps an
    addressable index instead of the text.

    Returns a new list and a report. The input list is never mutated.
    """
    messages = list(messages or [])
    counter = token_counter or (lambda m: estimate_tokens(str(m.get("content") or "")) + 4)
    target = max(0, int(target_tokens or 0))
    before = sum(counter(m) for m in messages)
    report: dict[str, Any] = {
        "shed": False,
        "tokens_before": before,
        "target": target,
        "removed": 0,
        "kept_tokens": before,
        "index_block": "",
        "path": "",
        "reason": "",
    }

    if not settings.get("rollover"):
        report["reason"] = "rollover is off"
        return messages, report
    if not settings.get("rolloverShed"):
        report["reason"] = "shed is off"
        return messages, report
    if before <= target:
        report["reason"] = "under target"
        return messages, report

    written = store_chat(data_dir, chat_id, messages, settings, user_id)
    report["path"] = written.get("path", "")
    if not written.get("written"):
        # A failed write means the copy does not exist, so shedding would be data loss. Stop.
        report["reason"] = "dump failed"
        return messages, report

    boundary = shed_boundary(messages, target)
    if boundary <= 0:
        report["reason"] = "nothing to shed"
        return messages, report

    shed = messages[:boundary]
    kept = messages[boundary:]
    report["shed"] = True
    report["removed"] = len(shed)
    # The shed span itself is returned in the report so the wiring can hand it to the vector store.
    # Evicting from the prompt without indexing is what makes the content unrecoverable by meaning.
    report["shed_messages"] = shed
    report["kept_tokens"] = sum(counter(m) for m in kept)
    report["index_block"] = shed_index_block(data_dir, chat_id, settings, shed, report["path"])
    report["reason"] = "over target"
    # The counter is written after the copy exists, so a counted shed is always a shed whose body
    # is recoverable.
    report["state"] = bump_rolls(
        data_dir,
        chat_id,
        sum(counter(m) for m in shed),
        report["reason"],
        report["kept_tokens"],
    )
    return kept, report


# --------------------------------------------------------------------------------------
# per chat state: how many times this chat has been shed
# --------------------------------------------------------------------------------------


STATE_FILE = "state.json"


def chat_state(data_dir: str, chat_id: str) -> dict:
    path = chat_dir(data_dir, chat_id) / STATE_FILE
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def bump_rolls(
    data_dir: str,
    chat_id: str,
    shed_tokens: int = 0,
    reason: str = "",
    kept_tokens: int = 0,
) -> dict:
    """Count a shed. Written only when a shed actually happens.

    That distinction matters: a counter written on every turn is a write per request, and this
    folder is on the same drive as everything else. The shed rate is a few writes over the life of
    a long chat, so this stays cheap without needing a throttle.
    """
    state = chat_state(data_dir, chat_id)
    state["rolls"] = int(state.get("rolls") or 0) + 1
    state["last_shed_at"] = int(time.time())
    state["last_shed_tokens"] = int(shed_tokens or 0)
    state["last_kept_tokens"] = int(kept_tokens or 0)
    if reason:
        state["last_reason"] = str(reason)[:80]
    try:
        (chat_dir(data_dir, chat_id) / STATE_FILE).write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        return state
    return state
