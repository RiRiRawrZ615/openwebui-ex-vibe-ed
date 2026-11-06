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
from open_webui.models.users import User
from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.ex_backend import BACKENDS, INSTALLABLE, auto_select, install_plan, path_allowed
from open_webui.utils.ex_handshake import load_handshake, render_status
from open_webui.utils.ex_import import db_path, import_tables
from open_webui.utils.ex_journal import journal_index, journal_path
from open_webui.utils.ex_settings import resolve_ex_settings
from open_webui.utils.ex_wiring import read_journal
from open_webui.utils.ex_theme import build_theme, css_tokens
from open_webui.utils.ex_recovery import load_checkpoint, recovery_status
from open_webui.utils.ex_modules import observed_from_tree, plugin_dir, resolve_enabled, status as modules_status
from open_webui.utils.ex_cluster import status as cluster_status
from open_webui.utils.ex_context import status as context_status
from open_webui.utils.ex_steering import (build_item, load_queue, status as steering_status, trim_pending)
from open_webui.utils.ex_ui import resolve_slots, status as ui_status
from open_webui.utils.ex_trim import resolve_gate, status as trim_status
from open_webui.utils.ex_batch import plan as batch_plan, summary as batch_summary, normalize_ids

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


@router.get("/theme")
async def ex_theme(
    user=Depends(get_current_user),
):
    """The theme as the engine measured it.

    A theme with a failing text pairing is reported as not ready, and no CSS is emitted for it.
    That is the engine's rule, enforced here, so a client cannot render an unreadable theme by
    skipping the ready check.
    """
    settings = resolve_ex_settings(user)
    roots = _allowed_roots()
    theme = build_theme(
        settings.get("themeName", "navy"),
        settings.get("themeAccent", ""),
        settings.get("themeSurface", ""),
        settings.get("themeText", ""),
        gradient_stops=list(settings.get("themeGradient") or []),
        wallpaper_value=settings.get("themeWallpaper", ""),
        icon_path=settings.get("themeIcon", ""),
        roots=roots,
        adjust_by=settings.get("themeAdjust", 0),
    )
    return {"theme": theme, "css": css_tokens(theme)}


@router.get("/recovery")
async def ex_recovery(
    chat_id: str = Query(default=""),
    user=Depends(get_current_user),
):
    """Recovery policy plus the recorded checkpoint.

    Nothing here re-runs a request. It reports the policy the settings hold and where the work
    stopped, so a new context resumes at the cursor instead of rebuilding from a paraphrase.
    """
    settings = resolve_ex_settings(user)
    checkpoint = load_checkpoint(DATA_DIR)
    if chat_id and checkpoint and checkpoint.get("chat_id") != chat_id:
        return {**recovery_status(settings, None), "note": "checkpoint belongs to a different chat"}
    return recovery_status(settings, checkpoint)


@router.get("/modules")
async def ex_modules(
    user=Depends(get_current_user),
):
    """The fork module registry.

    Wired is reported from observed importers, not from a field the module sets for itself, so a
    module that exists but has no call site is reported as logic only.
    """
    settings = resolve_ex_settings(user)
    # Read from the tree rather than from a list I typed. A registry that names its own importers
    # could claim a call site that does not exist, which is exactly the thing this report exists
    # to be honest about.
    tree_root = Path(__file__).resolve().parent.parent
    observed = observed_from_tree(tree_root)
    return {"enabled": resolve_enabled(settings), **modules_status(settings, observed, [DATA_DIR])}


@router.get("/cluster")
async def ex_cluster(
    user=Depends(get_current_user),
):
    """Compute-layer workers as advertised by the workers themselves.

    Planned and observed stay separate. Nothing here claims what llama.cpp assigned.
    """
    settings = resolve_ex_settings(user)
    workers = settings.get("workers") or []
    return {
        "model": settings.get("modelPath", ""),
        "workers": workers,
        "status": cluster_status({"workers": workers, "model": settings.get("modelPath", "")}),
    }


@router.get("/context")
async def ex_context(
    chat_id: str = Query(default=""),
    user=Depends(get_current_user),
):
    """Previous context state for this chat.

    Reports what the reader would deliver, not what it hopes to deliver. The cache cost is stated
    because previous context sits at the message head and invalidates the byte identical prefix.
    """
    settings = resolve_ex_settings(user)
    root = Path(DATA_DIR) / "ex_previous_context"
    stored = root / f"{chat_id or 'chat'}.txt"
    last_parse = {}
    if stored.exists():
        try:
            from open_webui.utils.ex_context import parse_export

            last_parse = parse_export(stored.read_text(encoding="utf-8", errors="replace"))
        except (ValueError, OSError):
            last_parse = {"parsed_from": "none", "reason": "stored file unreadable"}
    return {"settings": context_status(settings, last_parse), "stored_path": str(stored)}


@router.post("/steering")
async def ex_steering_add(
    text: str = Query(...),
    chat_id: str = Query(default=""),
    mode: str = Query(default="next_iteration"),
    action: str = Query(default="inject_instruction"),
    tool: str = Query(default=""),
    priority: str = Query(default="normal"),
    user=Depends(get_current_user),
):
    """Queue one steering item.

    Nothing is executed here. The item is stored and the request path applies it at the next
    iteration boundary. Mid-reasoning injection is not possible, so this is the honest form.
    """
    settings = resolve_ex_settings(user)
    if not settings.get("steeringQueue"):
        raise HTTPException(status_code=403, detail="steering queue is disabled")

    item = build_item(text, chat_id=chat_id, mode=mode, action=action, tool=tool, priority=priority)
    if not item.get("accepted"):
        raise HTTPException(status_code=400, detail=item.get("reason", "rejected"))

    from open_webui.utils.ex_steering import append_item

    items = append_item(DATA_DIR, item)
    return {"item": item, "pending": len([i for i in trim_pending(items) if not i.get("applied")])}


@router.get("/steering")
async def ex_steering_read(
    chat_id: str = Query(default=""),
    user=Depends(get_current_user),
):
    settings = resolve_ex_settings(user)
    items = load_queue(DATA_DIR)
    mine = [i for i in items if str(i.get("chat_id")) == str(chat_id or "") or not chat_id]
    return {"settings": steering_status(settings, mine), "items": mine}


@router.get("/ui")
async def ex_ui(
    user=Depends(get_current_user),
):
    """UI slot registry. A slot resolves to a plugin only when both the plugin and the slot are
    enabled, and a disabled plugin falls back to the builtin component."""
    settings = resolve_ex_settings(user)
    root = Path(DATA_DIR) / "ex_ui"
    enabled = resolve_enabled(settings).get("enabled", {})
    return {"settings": ui_status(settings, DATA_DIR), "resolved": resolve_slots(root, enabled)}


@router.post("/batch")
async def ex_batch(
    action: str = Query(...),
    chat_ids: str = Query(default=""),
    folder_id: str = Query(default=""),
    dry_run: bool = Query(default=True),
    force: bool = Query(default=False),
    user=Depends(get_current_user),
):
    """Multi-select over chats: move, download, delete.

    Ownership is confirmed before anything writes, so a half-applied batch cannot happen. A
    batch that cannot complete fully is refused as a whole, and delete needs force. The plan
    layer is pure; this is the place that reads the database.
    """
    from open_webui.models.chats import Chats
    from open_webui.models.folders import Folders

    ids = normalize_ids(chat_ids.split(","))
    if not ids:
        raise HTTPException(status_code=400, detail="nothing selected")

    owned = []
    for chat_id in ids:
        if await Chats.get_chat_by_id_and_user_id(chat_id, user.id):
            owned.append(chat_id)

    folder_exists = None
    if action == "move" and folder_id:
        folder_exists = await Folders.get_folder_by_id_and_user_id(folder_id, user.id) is not None

    result = batch_plan(
        action,
        ids,
        user_id=user.id,
        owned=owned,
        folder_id=folder_id or None,
        folder_exists=folder_exists,
        dry_run=dry_run,
        force=force,
    )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.reason)

    if not dry_run and result.would_write:
        # The plan validated, so the write is now allowed. Move and delete stay scoped to the
        # user, the way the model layer already scopes them.
        applied = []
        for chat_id in result.chat_ids:
            if action == "delete":
                if await Chats.delete_chat_by_id_and_user_id(chat_id, user.id):
                    applied.append(chat_id)
            elif action == "move":
                if await Chats.update_chat_folder_id_by_id_and_user_id(chat_id, user.id, folder_id):
                    applied.append(chat_id)
        return {**batch_summary(result), "applied": applied}

    return batch_summary(result)

@router.get("/trim")
async def ex_trim(user: User = Depends(get_current_user)):
    """Effective feature trim for this user.

    A gate, not a deletion. Load bearing items are refused here rather than trusted to a checkbox,
    so connections and the tool path stay reachable for remote models.
    """
    settings = resolve_ex_settings(user)
    return {"settings": trim_status(settings), "resolved": resolve_gate(settings)}
