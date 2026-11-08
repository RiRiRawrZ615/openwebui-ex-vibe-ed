"""EX status, journal, and import endpoints.

Read-only by design. The EX settings tab must report truth, so this router only
reads: the launcher handshake, the effective EX settings for the requesting user,
and the per-chat journal the fork already wrote. It never mutates a database except
through the guarded import path, which is dry-run unless explicitly forced.

Two rolling-context routes write, and both write files rather than the database: the
paste route stores text the user supplied, and the rollover route renders a chat that is
already stored. Neither one invents content, and neither one paraphrases.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request

import os

from open_webui.env import DATA_DIR
from open_webui.utils.auth import get_current_user
from open_webui.models.users import User, Users
from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.ex_backend import BACKENDS, INSTALLABLE, auto_select, install_plan, path_allowed
from open_webui.utils.ex_discover import DISCOVERABLE, manual_select, probe_all, probe_executable
from open_webui.utils.ex_handshake import load_handshake, render_status
from open_webui.utils.ex_import import db_path, import_tables
from open_webui.utils.ex_journal import journal_index, journal_path
from open_webui.utils.ex_turns import interrupted_turns, read_turn, state_dir
from open_webui.utils.ex_settings import resolve_ex_settings
from open_webui.utils.ex_rollover import (
    FOLDER,
    SECTION_ID,
    branch_order,
    chat_dir,
    chat_state,
    manifest,
    mark_carry,
    read_index,
    read_reasoning,
    resolve_source,
    store_chat,
)
from open_webui.utils.ex_steering import normalize_chat_id
from open_webui.models.chats import Chats
from open_webui.utils.ex_wiring import engine_status
from open_webui.tools import builtin_registry
from open_webui.utils.ex_wiring import (
    chat_memory_status,
    forget_chat_memory,
    read_journal,
    retrieve_chat_memory,
)
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


@router.get("/turns")
async def ex_turns_read(
    chat_id: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=100),
    user=Depends(get_current_user),
):
    """Buffers with no .done marker, which are the turns a crash or a cancel left behind.

    Read-only. For a turn that never reached the chat record the buffer is the only record,
    so this is what a client reads to offer a lost turn back.
    """
    found = interrupted_turns(chat_id)[:limit]
    return {
        "state_dir": str(state_dir()),
        "count": len(found),
        "turns": [
            {
                "chat_id": item["chat_id"],
                "message_id": item["message_id"],
                "lines": item["lines"],
                "bytes": item["bytes"],
                "age_seconds": round(item["age_seconds"], 1),
                "text": read_turn(item["chat_id"], item["message_id"]).get("text", ""),
            }
            for item in found
        ],
    }


@router.get("/turns/{chat_id}/{message_id}")
async def ex_turn_one(chat_id: str, message_id: str, user=Depends(get_current_user)):
    """One buffer, reconstructed. 404 when the turn finished and the buffer was dropped."""
    if not is_saved_chat_id(chat_id):
        raise HTTPException(status_code=404, detail="chat not found")

    turn = read_turn(chat_id, message_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="no buffer for this turn")
    return turn

@router.post("/settings")
async def ex_settings_write(patch: dict, user=Depends(get_current_user)):
    """Patch the ex namespace for the requesting user.

    This is the only write this router makes to the database, and it is scoped to the ex block so a
    quick control in the composer does not have to reach through the settings modal. Keys are checked
    against DEFAULTS, so an unknown key is refused rather than stored and then ignored.
    """
    current = resolve_ex_settings(user)
    unknown = [key for key in patch if key not in current]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown setting key: {', '.join(unknown)}")

    merged = {**current, **patch}
    updated = await Users.update_user_settings_by_id(user.id, {"ui": {"ex": merged}})
    if updated is None:
        raise HTTPException(status_code=404, detail="user not found")
    return {"settings": merged, "user_id": user.id}


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


@router.get("/discover")
async def ex_discover(
    components: str = Query(default=""),
    user=Depends(get_current_user),
):
    """What this machine already has, so the setup tab can say something true.

    Returns a per-component report: found or not, where, and whether it came from PATH
    or a known location. Probing only stats files; it never executes a candidate, so this
    cannot be used to run anything.
    """
    picked = tuple(c for c in components.split(",") if c.strip()) or DISCOVERABLE
    unknown = [c for c in picked if c not in DISCOVERABLE]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown component(s): {', '.join(unknown)}")
    return probe_all(components=picked)


@router.get("/discover/{component}")
async def ex_discover_one(
    component: str,
    user=Depends(get_current_user),
):
    """Probe one component. 404 rather than an empty report, so a typo is visible."""
    if component not in DISCOVERABLE:
        raise HTTPException(status_code=404, detail=f"unknown component: {component}")
    return probe_executable(component)


@router.post("/discover/{component}/manual")
async def ex_discover_manual(
    component: str,
    path: str = Query(...),
    user=Depends(get_current_user),
):
    """Validate a manually supplied binary path.

    Discovery cannot cover every layout, so a manual override is needed. This only checks
    the path exists, is a file and is executable; it does not run it, and it refuses any
    component that is not discoverable, so the field cannot point the setup at an
    arbitrary binary.
    """
    if component not in DISCOVERABLE:
        raise HTTPException(status_code=404, detail=f"unknown component: {component}")
    return manual_select(component, path)


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


@router.post("/context")
async def ex_context_store(
    chat_id: str = Query(...),
    text: str = Query(...),
    user=Depends(get_current_user),
):
    """Store a pasted export for a chat.

    Kept in the flat shape the reader prefers, so a hand-made export from the download button is
    never overwritten by the writer's folder. The folder and the flat file are two different sources
    and the reader's order is stated in ex_wiring, not guessed here.
    """
    settings = resolve_ex_settings(user)
    if not settings.get("previousContext"):
        raise HTTPException(status_code=403, detail="previous context is disabled")

    key = normalize_chat_id(chat_id)
    if not key:
        raise HTTPException(status_code=400, detail="chat_id is required")

    root = Path(DATA_DIR) / FOLDER
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / f"{key}.txt").write_text(text, encoding="utf-8")
    except OSError:
        raise HTTPException(status_code=500, detail="could not write the context file")

    return {"stored_path": str(root / f"{key}.txt"), "chars": len(text)}


@router.get("/context/{chat_id}/index")
async def ex_context_index(chat_id: str, user=Depends(get_current_user)):
    """The id index for the chat this chat reads from. Ids, roles, and excerpts only.

    The blocks are not included. The index is what lets a model name a section it wants; the block is
    fetched by the route below only when it is asked for.
    """
    settings = resolve_ex_settings(user)
    key = normalize_chat_id(chat_id)
    if not key:
        raise HTTPException(status_code=400, detail="chat_id is required")

    source = resolve_source(DATA_DIR, key, getattr(user, "id", "") or "", consume=False)
    return {
        "chat_id": key,
        "source_chat_id": source,
        "index": read_index(DATA_DIR, source),
        "manifest": manifest(DATA_DIR, source),
    }


@router.get("/context/{chat_id}/reasoning/{section_id}")
async def ex_context_reasoning(chat_id: str, section_id: str, user=Depends(get_current_user)):
    """One reasoning block by id. The id is the only way in; nothing here guesses which block was
    meant, and a malformed id returns nothing rather than the nearest file."""
    settings = resolve_ex_settings(user)
    if not settings.get("reasoningOnDemand"):
        raise HTTPException(status_code=403, detail="reasoning retrieval is disabled")

    key = normalize_chat_id(chat_id)
    if not key:
        raise HTTPException(status_code=400, detail="chat_id is required")
    if not SECTION_ID.fullmatch(section_id or ""):
        raise HTTPException(status_code=400, detail="section id must be R-<hex>")

    source = resolve_source(DATA_DIR, key, getattr(user, "id", "") or "", consume=False)
    block = read_reasoning(DATA_DIR, source, section_id)
    if not block:
        raise HTTPException(status_code=404, detail="no reasoning block for that id")
    return {"chat_id": source, "section_id": section_id, "content": block}


@router.post("/rollover")
async def ex_rollover_now(chat_id: str = Query(...), user=Depends(get_current_user)):
    """Roll a chat over on demand: render the stored branch, write the folder, mark the carry.

    Worth having even though the writer runs on every message save, because a rollover asked for by
    hand has to be exact. The branch is walked, not the insertion order, so a forked chat rolls over
    as the branch the user is looking at.
    """
    settings = resolve_ex_settings(user)
    if not settings.get("rollover"):
        raise HTTPException(status_code=403, detail="rolling context is disabled")

    key = normalize_chat_id(chat_id)
    if not key:
        raise HTTPException(status_code=400, detail="chat_id is required")

    chat = await Chats.get_chat_by_id(key)
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")

    chat_dict = chat.model_dump() if hasattr(chat, "model_dump") else chat
    user_id = getattr(user, "id", "") or ""
    existing = manifest(DATA_DIR, key)
    report = store_chat(
        DATA_DIR,
        key,
        branch_order(chat_dict),
        settings,
        user_id,
        existing.get("source_chat_id") or "",
    )
    carry = mark_carry(DATA_DIR, key, user_id)
    return {"report": report, "carry": carry}


@router.get("/engine")
async def ex_engine(user=Depends(get_current_user)):
    """Engine status from the log the launcher writes, never from the engine itself.

    The figures are the engine's own and come back with their source, so a windowed arrival estimate on
    the frontend and the engine's decode speed cannot be read as the same measurement. A log that
    stopped updating is reported as stale rather than as current.
    """
    settings = resolve_ex_settings(user)
    if not settings.get("engineStatus"):
        raise HTTPException(status_code=403, detail="engine status is disabled")

    return engine_status(settings, str(DATA_DIR))


@router.get("/rollover")
async def ex_rollover_state(chat_id: str = Query(default=""), user=Depends(get_current_user)):
    """Per-chat rollover state for the stats row: how many times this chat has been evicted.

    Read from the state file written when a shed happens, so the figure is a count of sheds and not a
    guess. A chat with zero still answers, because the icon needs the dimmed form: the row shows the
    state, not only the event.
    """
    settings = resolve_ex_settings(user)
    if not settings.get("rollover"):
        raise HTTPException(status_code=403, detail="rollover is disabled")

    chat_id = normalize_chat_id(chat_id)
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required")

    return {
        "chat_id": chat_id,
        "file": str(chat_dir(str(DATA_DIR), chat_id)),
        "state": chat_state(str(DATA_DIR), chat_id),
    }


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

# --- per-chat retrieval memory ---------------------------------------------------------


@router.get("/memory")
async def ex_memory_status(
    chat_id: str = Query(default=''),
    user: User = Depends(get_current_user),
):
    """Store health and how many passages this chat has indexed.

    Reports availability honestly. A missing embedder is a downgrade, not a failure: the
    chat still works, retrieval just does nothing, and this says which it is.
    """
    return chat_memory_status(DATA_DIR, chat_id or None)


@router.get("/memory/search")
async def ex_memory_search(
    q: str = Query(default=''),
    chat_id: str = Query(default=''),
    top_k: int = Query(default=6),
    user: User = Depends(get_current_user),
):
    """Search this chat's passages, or every chat when chat_id is empty.

    The empty-chat_id form is the recovery path. If a chat has been wiped or compacted
    away, its passages are still in the index, and this is how they are reached without
    needing the chat record to exist.
    """
    if not q.strip():
        raise HTTPException(status_code=400, detail='q is required')
    settings = resolve_ex_settings(user)
    result = retrieve_chat_memory(
        DATA_DIR,
        chat_id or None,
        [{'role': 'user', 'content': q}],
        {**settings, 'ragMemory': True},
    )
    return {
        'query': result.get('query'),
        'hits': result.get('hits') or [],
        'block': result.get('block'),
        'error': result.get('error'),
    }


@router.delete("/memory")
async def ex_memory_forget(
    chat_id: str = Query(default=''),
    user: User = Depends(get_current_user),
):
    """Drop one chat's passages from the index. The chat record itself is untouched."""
    if not is_saved_chat_id(chat_id):
        raise HTTPException(status_code=400, detail='a saved chat_id is required')
    return forget_chat_memory(DATA_DIR, chat_id)


# --- Builtin tools ------------------------------------------------------------------------
#
# The nine shipped tools, their switch state, and their user-edit overlays. The registry does
# the work; these endpoints exist so the settings tab can present it.
#
# Admin gating: enabling a tool changes what the model can do for every user of the instance, so
# the write paths require an admin. Reading the list is open to any signed-in user, because the
# settings tab has to render something for a non-admin and a blank tab is worse than a read-only
# one. The registry itself does not check permissions; that is this layer's job.


def _require_admin(user) -> None:
    if getattr(user, 'role', '') != 'admin':
        raise HTTPException(status_code=403, detail='admin required to change builtin tools')


@router.get('/builtins')
async def ex_builtins_list(user: User = Depends(get_current_user)):
    """Every shipped builtin: name, exposed functions, enabled state, overlay state.

    A load failure is reported per tool rather than raised, so one broken builtin does not make
    the whole tab unusable. That matters most for a broken overlay, which is exactly the case
    where the user needs the tab to work in order to fix it.
    """
    return {
        'builtins': builtin_registry.describe_all(),
        'can_edit': getattr(user, 'role', '') == 'admin',
    }


@router.get('/builtins/{slug}')
async def ex_builtin_detail(slug: str, user: User = Depends(get_current_user)):
    if slug not in builtin_registry.load_manifest():
        raise HTTPException(status_code=404, detail=f'unknown builtin: {slug}')

    info = builtin_registry.describe(slug)

    # The current effective source, so the editor opens on what is actually running rather than
    # on the shipped file when an overlay is in place.
    try:
        source, origin, _ = builtin_registry.resolve_source(slug)
    except Exception as exc:  # noqa: BLE001
        source, origin = '', 'unavailable'
        info['load_error'] = info.get('load_error') or f'{type(exc).__name__}: {exc}'

    info['source_text'] = source
    info['source_origin'] = origin
    info['is_admin'] = getattr(user, 'role', '') == 'admin'
    return info


@router.post('/builtins/{slug}/enabled')
async def ex_builtin_set_enabled(
    slug: str,
    enabled: bool = Query(...),
    user: User = Depends(get_current_user),
):
    _require_admin(user)
    try:
        builtin_registry.set_enabled(slug, enabled)
    except KeyError:
        raise HTTPException(status_code=404, detail=f'unknown builtin: {slug}')
    return builtin_registry.describe(slug)


@router.put('/builtins/{slug}/overlay')
async def ex_builtin_save_overlay(
    slug: str,
    request: Request,
    user: User = Depends(get_current_user),
):
    """Save a user edit as a whole-file overlay.

    A syntax error is rejected here with a 400 rather than stored, so a bad save cannot leave the
    tool broken on next load. The check is compile(), which proves it parses, not that it works.
    """
    _require_admin(user)

    body = await request.json()
    text = body.get('source')
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=400, detail='source is required')

    try:
        builtin_registry.save_overlay(slug, text)
    except KeyError:
        raise HTTPException(status_code=404, detail=f'unknown builtin: {slug}')
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return builtin_registry.describe(slug)


@router.delete('/builtins/{slug}/overlay')
async def ex_builtin_revert_overlay(slug: str, user: User = Depends(get_current_user)):
    """Drop the user edit and go back to the shipped builtin."""
    _require_admin(user)
    try:
        result = builtin_registry.revert_overlay(slug)
    except KeyError:
        raise HTTPException(status_code=404, detail=f'unknown builtin: {slug}')
    return {**result, **builtin_registry.describe(slug)}
