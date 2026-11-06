"""Batch chat operations (pure).

Multi-select over chats: move to a folder, download, delete. This module does not touch a
database. It validates a selection and produces a plan the router executes, so the rules
are testable without a live instance.

Rules that matter, and why:
  - delete is destructive, so it is dry-run unless explicitly forced. Move is not, because
    a move is reversible by moving back.
  - every id in the selection must be owned by the requesting user. The model layer in
    models/chats.py already scopes by user_id (delete_chat_by_id_and_user_id,
    update_chat_folder_id_by_id_and_user_id), but this layer refuses an unowned id before
    anything runs, so a partial write cannot happen halfway through a batch.
  - a batch that cannot complete fully is refused as a whole rather than done halfway. A
    half-applied batch is worse than no batch, because the user cannot tell which chats
    moved.
  - the download shape is reported, not performed. Download means reading existing chats,
    so it is read-only by definition and never enters the force path.

Measured fact about the current tree: multi-select exists only in FileNav.svelte (the file
browser), around line 1716. The sidebar chat list has per-chat menus with Move, Download,
Delete, but no selection set. So this is a new capability, not an extension of one.
"""

from __future__ import annotations

from dataclasses import dataclass

ACTIONS = ("move", "download", "delete")

# Download is read-only, so it is never a force case. Delete is the only action that can
# destroy data, so it needs force.
FORCE_REQUIRED = ("delete",)


@dataclass(frozen=True)
class BatchPlan:
    action: str
    chat_ids: tuple[str, ...]
    folder_id: str | None
    dry_run: bool
    force: bool
    refused: tuple[str, ...]
    blocked: tuple[str, ...]
    ok: bool
    reason: str

    @property
    def would_write(self) -> bool:
        """Derived, stored once as a property so to_dict and the plan cannot disagree on
        whether this batch writes. A field would be a second source of truth.
        """
        return (not self.dry_run) and self.action in FORCE_REQUIRED

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "chat_ids": list(self.chat_ids),
            "folder_id": self.folder_id,
            "dry_run": self.dry_run,
            "force": self.force,
            "refused": list(self.refused),
            "blocked": list(self.blocked),
            "ok": self.ok,
            "reason": self.reason,
            "count": len(self.chat_ids),
            "would_write": self.would_write,
        }


def normalize_ids(chat_ids: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Keep order, drop blanks and duplicates. A selection that repeats an id is one id."""
    seen = set()
    out = []
    for raw in chat_ids or []:
        item = str(raw).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return tuple(out)


def plan(
    action: str,
    chat_ids: list[str] | tuple[str, ...],
    *,
    user_id: str,
    owned: set[str] | list[str] | None = None,
    folder_id: str | None = None,
    folder_exists: bool | None = None,
    dry_run: bool = True,
    force: bool = False,
) -> BatchPlan:
    """Validate a selection and say what it would do.

    `owned` is the set of chat ids this user owns. Nothing here queries a database; the
    caller supplies it. Without it the plan cannot confirm ownership, and a plan that
    cannot confirm ownership refuses the batch instead of assuming it.
    """
    ids = normalize_ids(chat_ids)
    action = (action or "").strip().lower()

    if action not in ACTIONS:
        return BatchPlan(action, ids, folder_id, dry_run, force, (), (), False, f"unknown action: {action}")

    if not ids:
        return BatchPlan(action, ids, folder_id, dry_run, force, (), (), False, "nothing selected")

    if action == "move":
        if not folder_id:
            return BatchPlan(action, ids, folder_id, dry_run, force, (), (), False, "move needs a target folder")
        if folder_exists is False:
            return BatchPlan(action, ids, folder_id, dry_run, force, (), (), False, "target folder does not exist")
        if folder_exists is None:
            return BatchPlan(action, ids, folder_id, dry_run, force, (), (), False, "target folder could not be confirmed")

    if not user_id:
        return BatchPlan(action, ids, folder_id, dry_run, force, (), (), False, "no user to scope the batch to")

    if owned is None:
        return BatchPlan(
            action, ids, folder_id, dry_run, force, (), tuple(ids), False,
            "ownership cannot be confirmed without the owned set"
        )

    owned_set = {str(x) for x in owned}
    refused = tuple(i for i in ids if i not in owned_set)
    if refused:
        # Refused up front, so the batch is not applied to the rest. A partial batch is a
        # state the user cannot see clearly in the sidebar.
        return BatchPlan(action, ids, folder_id, dry_run, force, refused, (), False,
                         f"{len(refused)} chat(s) are not owned by this user")

    if action in FORCE_REQUIRED and dry_run and not force:
        # Not a failure, just a plan that will not write. The reason says which.
        return BatchPlan(action, ids, folder_id, dry_run, force, (), (), True,
                         f"dry run, {action} would not write without force")

    return BatchPlan(action, ids, folder_id, dry_run, force, (), (), True,
                     "plan is valid" if dry_run else f"would {action} {len(ids)} chat(s)")


def summary(plan_result: BatchPlan | dict) -> dict:
    """Compact form for the UI. Refused ids are listed, not merged into the count, so a
    count that looks like it will happen is never a count that includes something refused.
    """
    data = plan_result.to_dict() if isinstance(plan_result, BatchPlan) else dict(plan_result)
    return {
        "action": data.get("action"),
        "selected": len(data.get("chat_ids", [])),
        "would_apply": len(data.get("chat_ids", [])) - len(data.get("refused", [])),
        "refused": data.get("refused", []),
        "blocked": data.get("blocked", []),
        "folder_id": data.get("folder_id"),
        "dry_run": data.get("dry_run"),
        "force": data.get("force"),
        "would_write": data.get("would_write"),
        "ok": data.get("ok"),
        "reason": data.get("reason"),
    }


__all__ = ["ACTIONS", "FORCE_REQUIRED", "BatchPlan", "normalize_ids", "plan", "summary"]
