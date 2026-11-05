"""EX status, journal, and import endpoints.

Read-only by design. The EX settings tab must report truth, so this router only
reads: the launcher handshake, the effective EX settings for the requesting user,
and the per-chat journal the fork already wrote. It never mutates a database except
through the guarded import path, which is dry-run unless explicitly forced.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request

import os

from open_webui.env import DATA_DIR
from open_webui.utils.auth import get_current_user
from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.ex_backend import BACKENDS, INSTALLABLE, auto_select, install_plan, path_allowed
from open_webui.utils.ex_handshake import load_handshake, render_status
from open_webui.utils.ex_import import db_path, import_tables
from open_webui.utils.ex_journal import journal_index, journal_path
from open_webui.utils.ex_settings import resolve_ex_settings
from open_webui.utils.ex_wiring import read_journal

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/status")
async def ex_status(request: Request, user=Depends(get_current_user)):
    """Effective EX settings plus what the launcher actually locks right now.

    If the handshake file is absent this says so plainly rather than showing the
    defaults as if the launcher agreed with them.
    """
    try:
        handshake = load_handshake(DATA_DIR)
    except Exception:
        log.exception("EX handshake read failed")
        handshake = None

    return {
        "settings": resolve_ex_settings(user),
        "launcher": render_status(handshake),
        "data_dir": str(DATA_DIR),
    }


@router.get("/journal")
async def ex_journal_read(
    chat_id: str = Query(...),
    entry: int | None = Query(default=None),
    needle: str = Query(default=""),
    limit: int = Query(default=12, ge=1, le=50),
    user=Depends(get_current_user),
):
    """Read the per-chat journal. Index only when no entry or needle is given."""
    if not is_saved_chat_id(chat_id):
        raise HTTPException(status_code=404, detail="chat not found")

    path = journal_path(DATA_DIR, chat_id)
    if entry is not None or needle:
        return {"path": str(path), "content": read_journal(path, entry_id=entry, needle=needle, limit=limit)}
    return {"path": str(path), "entries": journal_index(path, limit=limit)}


def _allowed_roots() -> list[Path]:
    """Roots the model check may read.

    The pure module does not read the environment; the router is the place that does.
    Without an explicit EX_MODEL_ROOTS the check is limited to the data dir, so a
    settings tab cannot be used to probe arbitrary paths on the machine.
    """
    raw = os.getenv("EX_MODEL_ROOTS", "")
    roots = [DATA_DIR]
    for item in raw.split(":"):
        if item.strip():
            roots.append(Path(item.strip()))
    return roots


@router.get("/backend")
async def ex_backend_check(
    model: str = Query(default=""),
    user=Depends(get_current_user),
):
    """Which backend can actually serve this model.

    Refuses rather than guesses. A path outside the allowed roots is refused, and a
    model no backend can serve comes back as a refusal with the reason, not as a
    backend name picked anyway.
    """
    settings = resolve_ex_settings(user)
    roots = _allowed_roots()
    target = model or settings.get("modelPath", "")

    if not target:
        return {"error": "no model path given", "roots": [str(r) for r in roots]}
    if not path_allowed(target, roots):
        raise HTTPException(status_code=403, detail="model path is outside the allowed roots")

    result = auto_select(target, prefer=settings.get("backend") or "auto")
    return {"roots": [str(r) for r in roots], "available_backends": list(BACKENDS), **result}


@router.get("/install-plan")
async def ex_install_plan(
    components: str = Query(default=""),
    user=Depends(get_current_user),
):
    """What an install would do. Descriptions only, never shell glue."""
    settings = resolve_ex_settings(user)
    picked = [c for c in components.split(",") if c.strip()]
    return install_plan(picked, dry_run=not settings.get("allowInstall", False))


def _fork_db() -> Path:
    return Path(DATA_DIR) / "webui.db"


@router.post("/import")
async def ex_import_run(
    source: str = Query(...),
    target: str = Query(default=""),
    dry_run: bool = Query(default=True),
    force: bool = Query(default=False),
    user=Depends(get_current_user),
):
    """Import a stock Open WebUI database into the fork data dir.

    Dry run unless the caller asks for a write, and a write outside the fork data
    dir needs force. Same rules the CLI path enforces in ex_import.main().
    """
    src = db_path(source)
    if not src.exists():
        raise HTTPException(status_code=404, detail=f"source database not found: {src}")

    target_path = db_path(target) if target else _fork_db()

    if src.resolve() == target_path.resolve():
        raise HTTPException(status_code=400, detail="source and target are the same database")
    if not dry_run and not force and target_path.resolve() != _fork_db().resolve():
        raise HTTPException(status_code=400, detail="refusing to write outside the fork data dir without force")

    try:
        return import_tables(src, target_path, force=force, dry_run=dry_run)
    except Exception as error:
        log.exception("EX import failed")
        raise HTTPException(status_code=500, detail=str(error))
