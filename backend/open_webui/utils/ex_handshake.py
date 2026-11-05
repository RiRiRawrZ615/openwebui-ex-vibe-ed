"""Launcher settings handshake (pure).

The launcher is what starts the server, so its hardcoded params win over the
frontend. That is not a bug to hide; it is the actual authority order. The EX tab
therefore reports truth: which params the launcher currently locks, and which the
UI controls.

The launcher writes a small JSON file into the webui data dir at startup:

  {
    "source": "ex_launcher_v2",
    "backend": "strata" | "llamacpp",
    "model": "...",
    "params": {"temperature": 0.2, "top_p": 0.8, "top_k": 20, "ctx_size": 256000},
    "written_at": "iso8601"
  }

The fork reads it read-only. If the file is absent, nothing is locked and the UI
owns the params. A param present in the file is reported as launcher-locked, so
the EX tab never shows a value the server is not actually using.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

HANDSHAKE_NAME = "ex_runtime.json"
HANDSHAKE_SOURCE = "ex_launcher_v2"
SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


def handshake_path(data_dir: Path | str, name: str = HANDSHAKE_NAME) -> Path:
    return Path(data_dir) / name


def load_handshake(data_dir: Path | str, name: str = HANDSHAKE_NAME) -> dict | None:
    """Read the handshake. Returns None when absent, unreadable, or malformed.

    Takes the same name argument as handshake_path and write_handshake. Without it a
    writer using a custom name would write to a path this reader cannot see, which is
    the exact drift the module is supposed to prevent.
    """
    p = handshake_path(data_dir, name)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (ValueError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def write_handshake(data_dir: Path | str, record: dict, name: str = HANDSHAKE_NAME) -> Path:
    """Write the handshake the reader expects. Kept in this module so the writer and
    the reader cannot drift on the shape.

    Only source/backend/model/params are accepted. Keys outside that set are dropped
    rather than written, because the reader would ignore them anyway and a stale key
    in the file reads like a real value. written_at is stamped here, in UTC ISO 8601.
    """
    record = record if isinstance(record, dict) else {}
    params = record.get("params")
    payload = {
        "source": record.get("source") or HANDSHAKE_SOURCE,
        "backend": record.get("backend"),
        "model": record.get("model"),
        "params": {str(k): v for k, v in params.items()} if isinstance(params, dict) else {},
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    p = handshake_path(data_dir, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return p


def launcher_locked_params(handshake: dict | None) -> dict:
    """Params the launcher actually controls right now. Empty when the UI owns them."""
    if not handshake:
        return {}
    params = handshake.get("params")
    if not isinstance(params, dict):
        return {}
    return {str(k): v for k, v in params.items()}


def stale(handshake: dict | None, max_age_seconds: int = 6 * 3600) -> bool:
    """True when the handshake is older than max_age. A launcher that died should not
    keep locking the UI forever, so the tab labels it stale instead of authoritative."""
    if not handshake:
        return False
    ts = handshake.get("written_at")
    if not isinstance(ts, str):
        return True
    try:
        when = datetime.fromisoformat(ts)
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds() > max_age_seconds


def render_status(handshake: dict | None) -> dict:
    """Compact status the EX tab shows. No invented values."""
    if not handshake:
        return {"present": False, "backend": None, "model": None, "locked": {}, "stale": False}
    return {
        "present": True,
        "backend": handshake.get("backend"),
        "model": handshake.get("model"),
        "locked": launcher_locked_params(handshake),
        "stale": stale(handshake),
    }
