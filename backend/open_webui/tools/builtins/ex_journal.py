"""Open Web Vibes builtin: Journal Reader

Dev-only builtin that exposes the per-chat journal to the model. The journal is written by
`open_webui.utils.ex_journal`; before this tool existed the prompt could only show a pointer
("full text is stored, retrieve with ex_journal.read(entry=N)") with no tool able to answer
that pointer, so an agent whose context had been trimmed had no way to recover its own
earlier reasoning or tool output.

This file is a real builtin, not a wrapper around a DB tool. It resolves the chat from the
request when the caller does not name it, and it reads with plain file access rather than
through the fork lock, because journal reads under the lock are the deadlock the fork rules
already forbid.

Exported functions: index, read
"""
import logging
from pathlib import Path
from typing import Optional

from fastapi import Request
from pydantic import BaseModel, Field

from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.ex_settings import resolve_ex_settings
from open_webui.utils.ex_journal import (
    journal_index,
    journal_path,
    read_journal,
    DEFAULT_READ_MAX_CHARS)
from open_webui.utils.ex_reconstruct import reconstruct_path

log = logging.getLogger(__name__)


def _resolve_data_dir(request: Request) -> Path:
    state = getattr(getattr(request, "app", None), "state", None)
    data_dir = getattr(state, "data_dir", None) or getattr(state, "DATA_DIR", None)
    if data_dir:
        return Path(data_dir)
    from open_webui.env import DATA_DIR

    return Path(DATA_DIR)


def _resolve_chat(request: Request, chat_id: str) -> str:
    state = getattr(request, "state", None)
    for source in (chat_id, getattr(state, "chat_id", None)):
        if source and is_saved_chat_id(str(source)):
            return str(source)
    return ""


def _newest_journal(data_dir: Path) -> str:
    """Fallback when no chat can be resolved: the most recently written journal."""
    root = data_dir / "journal"
    if not root.exists():
        return ""
    candidates = sorted(
        (p for p in root.glob("*.md") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0].name if candidates else ""


class Tools:
    def __init__(self):
        """Initialize the Tool."""
        self.valves = self.Valves()

    class Valves(BaseModel):
        max_entries: int = Field(12, description="Largest number of journal entries one read may return.")
        summary_chars: int = Field(200, description="Length of the summary shown for entries not read in full.")

    async def index(self, chat_id: str = "", limit: int = 25, __request__: Request = None) -> str:
        """List journal entries: id, turn, kind, summary, and whether the full text is stored.

        Use this first when earlier turns have been trimmed from context. It is cheap, and it
        names the entry ids that read() can return in full.
        """
        if __request__ is None:
            return "journal index needs a request context"
        data_dir = _resolve_data_dir(__request__)
        chat = _resolve_chat(__request__, chat_id) or _newest_journal(data_dir)
        if not chat:
            return "no journal for this chat"
        limit = max(1, min(int(limit or 25), 100))
        entries = journal_index(journal_path(data_dir, chat), limit=limit)
        if not entries:
            return f"journal for {chat} is empty"
        lines = [f"journal: {chat}"]
        for e in entries:
            stored = "full text stored" if e.get("has_full_text") else "summary only"
            lines.append(
                f"entry {e.get('id')} | turn {e.get('turn')} | {e.get('kind')} | {stored} | {e.get('summary')}"
            )
        return "\n".join(lines)

    async def read(
        self,
        entry: Optional[int] = None,
        needle: str = "",
        limit: int = 12,
        chat_id: str = "",
        __request__: Request = None,
    ) -> str:
        """Retrieve stored journal text.

        Give `entry` for one entry in full, or `needle` to search the journal. Both may be
        empty, in which case the most recent entries are returned. Nothing here re-runs a
        model or a tool; it reads the journal file the fork already wrote.
        """
        if __request__ is None:
            return "journal read needs a request context"
        data_dir = _resolve_data_dir(__request__)
        chat = _resolve_chat(__request__, chat_id) or _newest_journal(data_dir)
        if not chat:
            return "no journal for this chat"
        limit = max(1, min(int(limit or 12), int(self.valves.max_entries) * 5))
        path = journal_path(data_dir, chat)
        if not path.exists():
            return f"no journal file for {chat}"
        # Bounded, so a read cannot return a whole window. The ceiling comes from settings so
        # it can be raised, and when it is reached the truncation is stated in the text rather
        # than left silent.
        budget = DEFAULT_READ_MAX_CHARS
        try:
            budget = int((resolve_ex_settings(getattr(__request__, "user", None)) or {}).get(
                "readMaxChars") or DEFAULT_READ_MAX_CHARS)
        except Exception:
            pass
        return read_journal(path, entry_id=entry, needle=needle, limit=limit,
                            max_chars=budget)

    async def reconstruct(self, chat_id: str = "", __request__: Request = None) -> str:
        """Read the reconstruction file for this chat.

        This is the minimum set needed to restart the conversation in a new window: prior context,
        the last full reasoning loop, the last response, and the last user message. The assembler
        writes it every turn as plain text next to the journal, so it is readable here, by grep, or by
        the user without the fork running.
        """
        if __request__ is None:
            return "reconstruct needs a request context"
        data_dir = _resolve_data_dir(__request__)
        chat = _resolve_chat(__request__, chat_id) or _newest_journal(data_dir)
        if not chat:
            return "no chat resolved"
        path = reconstruct_path(data_dir, chat)
        if not path.exists():
            return f"no reconstruction file for {chat}"
        return path.read_text(encoding="utf-8")
