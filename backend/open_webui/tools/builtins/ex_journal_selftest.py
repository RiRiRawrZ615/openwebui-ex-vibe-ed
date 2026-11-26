"""Self-check for the journal reader's chat resolution.

Run with the backend on PYTHONPATH:

    python open_webui/tools/builtins/ex_journal_selftest.py

Covers the three defects that made the tool answer "no journal for this chat" while the
journal sat on disk: request.state.chat_id is never assigned in this build, the newest-journal
fallback looked in the wrong directory, and it returned a filename that journal_path() then
suffixed a second time. No pytest needed and no network: it builds its own data dir.
"""
import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

from open_webui.tools.builtins import ex_journal as EJ

CHAT = "11111111-2222-3333-4444-555555555555"
UNSAVED = "local:not-a-saved-chat"


def _req(**kw):
    state = SimpleNamespace()
    for key, value in kw.pop("state", {}).items():
        setattr(state, key, value)
    return SimpleNamespace(state=state, **kw)


def _seed(data_dir: Path) -> None:
    """One journal in the layout the writer actually uses."""
    root = data_dir / "ex_journals"
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{CHAT}.md").write_text(
        "# Reasoning journal\n\n"
        f"chat: {CHAT}\n\n"
        "## entry 1 | turn 1 | reasoning\n"
        "ts: 2026-01-01T00:00:00+00:00\n"
        "\n"
        "a decision worth recovering\n",
        encoding="utf-8",
    )


async def main() -> int:
    failures = []
    tools = EJ.Tools()
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        _seed(data_dir)
        original = EJ._resolve_data_dir
        EJ._resolve_data_dir = lambda _request: data_dir
        try:
            # 1. the fallback must find the journal in ex_journals, by stem, not by filename
            got = await tools.index(__request__=_req())
            if CHAT not in got or "empty" in got:
                failures.append(f"fallback lookup: {got!r}")

            # 2. an explicit id still wins
            got = await tools.index(chat_id=CHAT, __request__=_req())
            if "entry 1" not in got:
                failures.append(f"explicit id: {got!r}")

            # 3. query string
            read = await tools.read(entry=1, __request__=_req(query_params={"chat_id": CHAT}))
            if "a decision worth recovering" not in read:
                failures.append(f"query_params read: {read!r}")

            # 4. referer
            read = await tools.read(
                entry=1, __request__=_req(headers={"referer": f"http://x/c/{CHAT}"})
            )
            if "a decision worth recovering" not in read:
                failures.append(f"referer read: {read!r}")

            # 5. an unsaved chat id must never be accepted as the chat
            got = await tools.index(__request__=_req(query_params={"chat_id": UNSAVED}))
            if UNSAVED in got:
                failures.append(f"unsaved id accepted: {got!r}")
        finally:
            EJ._resolve_data_dir = original

    for line in failures:
        print("FAIL", line)
    print("ok" if not failures else f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
