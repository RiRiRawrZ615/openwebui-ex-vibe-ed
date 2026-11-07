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
