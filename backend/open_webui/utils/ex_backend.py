"""Backend selection and install planning (pure).

Feature E shape, corrected: the backend choice and install options live in the UI.
The fork does not start a process by itself and does not install anything by itself.
It records the choice, computes what each backend can actually serve, and writes an
install plan the launcher executes. A settings tab that pretends it can boot a server
is decoration, so this module only answers two questions:

  1. Given a model, which backends can serve it?
  2. Given a set of components, what would the install do?

The check is factual, based on what each backend consumes:
  - llama.cpp serves a GGUF file directly.
  - Strata serves a prepared pack (a directory with a manifest plus shard files),
    not a loose GGUF, unless that pack was prepared first.

So a .gguf path is a llama.cpp candidate and not a Strata candidate until prepared.
That distinction is the whole point of the "auto by model check".

No network, no subprocess, no imports from open_webui. Profile index reading is
optional and read-only; the caller decides whether to pass it, and the default here
reads nothing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

BACKENDS = ("llamacpp", "strata")

GGUF_SUFFIXES = (".gguf",)
# Components the launcher is allowed to install. Kept explicit so an unknown
# component is refused instead of passed to a shell.
INSTALLABLE = ("webui", "llamacpp", "strata", "openwebvibes")

# Path guard. The router exposes the model check, and a settings tab that will read any
# path on the machine is not acceptable in a multi-user deployment. Roots are passed in
# by the caller; this module does not read the environment itself.
DEFAULT_ROOTS = ()


def path_allowed(path: str | Path, roots: list[Path] | tuple[Path, ...]) -> bool:
    """True when the path resolves inside one of the allowed roots."""
    if not roots:
        return False
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, ValueError):
        return False
    for root in roots:
        try:
            r = Path(root).expanduser().resolve()
        except (OSError, ValueError):
            continue
        if resolved == r or r in resolved.parents or str(resolved).startswith(str(r) + os.sep):
            return True
    return False


def classify_model(model_path: str | Path) -> dict:
    """What is this model, in a form both backends understand?

    Returns {path, format, prepared, backends}. `prepared` means Strata can serve it
    as-is: a directory containing a manifest, or a file whose name matches a manifest
    entry. Nothing is executed to find that out; it is a directory listing.
    """
    p = Path(model_path).expanduser()
    fmt = "unknown"
    if p.is_file():
        if p.suffix.lower() in GGUF_SUFFIXES:
            fmt = "gguf"
        elif p.suffix.lower() == ".safetensors":
            fmt = "safetensors"
    elif p.is_dir():
        manifest = _find_manifest(p)
        if manifest:
            fmt = "strata_pack"

    backends = []
    if fmt == "gguf":
        backends.append("llamacpp")
    if fmt == "strata_pack":
        backends.append("strata")
    if fmt == "safetensors":
        # Both can serve safetensors in principle; llama.cpp needs a converted GGUF,
        # Strata needs a prepared pack. Neither is guaranteed, so the honest answer is
        # "needs preparation" and no backend is claimed.
        backends = []

    return {
        "path": str(p),
        "format": fmt,
        "exists": p.exists(),
        "prepared": fmt == "strata_pack",
        "backends": backends,
        "needs_preparation": fmt in ("safetensors", "unknown") or (fmt == "gguf" and "strata" not in backends),
    }


def _find_manifest(directory: Path) -> dict | None:
    """Look for a Strata-style manifest in a pack directory. Read-only listing."""
    for name in ("manifest.json", "pack.json", "model.json"):
        candidate = directory / name
        if candidate.is_file():
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8", errors="replace"))
            except (ValueError, OSError):
                return None
            return raw if isinstance(raw, dict) else None
    return None


def read_profile_index(index_path: str | Path | None) -> list[dict]:
    """Read a Strata profile index (TSV) if it exists. Pure read, returns [] when absent.

    Row shape is taken as: profile, model, context, kv, vision, mtp. Columns beyond the
    first five are left as-is rather than guessed.
    """
    if not index_path:
        return []
    p = Path(index_path).expanduser()
    if not p.is_file():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        rows.append({
            "profile": parts[0],
            "model": parts[1] if len(parts) > 1 else "",
            "context": parts[2] if len(parts) > 2 else "",
            "kv": parts[3] if len(parts) > 3 else "",
            "vision": parts[4] if len(parts) > 4 else "",
        })
    return rows


def auto_select(model_path: str | Path, *, profile_index: list[dict] | None = None, prefer: str | None = None) -> dict:
    """Pick a backend by what can actually serve the model.

    Order of authority: a backend that can serve the model at all, then the preference,
    then nothing. If no backend can serve it the answer says so instead of picking one
    anyway, because "auto" that guesses is worse than "auto" that refuses.
    """
    info = classify_model(model_path)
    candidates = list(info["backends"])

    if not candidates:
        return {
            "selected": None,
            "reason": f"no backend can serve {info['format']} at {info['path']}; needs preparation",
            "candidates": [],
            "model": info,
        }

    if prefer in candidates:
        selected = prefer
        reason = f"preference {prefer} is among the candidates"
    elif prefer in BACKENDS:
        selected = candidates[0]
        reason = f"preference {prefer} cannot serve this model; using {candidates[0]}"
    else:
        selected = candidates[0]
        reason = "only candidate available"

    matched_profile = None
    if profile_index:
        name = Path(model_path).name.lower()
        for row in profile_index:
            if row.get("model", "").lower() == name:
                matched_profile = row
                break

    return {
        "selected": selected,
        "reason": reason,
        "candidates": candidates,
        "model": info,
        "profile": matched_profile,
    }


def install_plan(components: list[str], *, dry_run: bool = True) -> dict:
    """What the install would do. This module does not run anything.

    Unknown components are refused rather than passed through, so a typo cannot become
    a shell command. Steps are descriptions, not commands joined into a string, because
    a plan the caller might paste into a shell should not be built by string glue.
    """
    known = [c for c in components if c in INSTALLABLE]
    unknown = [c for c in components if c not in INSTALLABLE]

    steps = []
    for component in known:
        if component == "webui" or component == "openwebvibes":
            steps.append({"component": component, "action": "npm ci && npm run build", "note": "frontend build"})
        elif component == "llamacpp":
            steps.append({"component": component, "action": "fetch or build llama.cpp, then serve the GGUF", "note": "backend server"})
        elif component == "strata":
            steps.append({"component": component, "action": "fetch or build Strata, then serve the prepared pack", "note": "backend server"})

    return {
        "dry_run": dry_run,
        "steps": steps,
        "refused": unknown,
        "runs": not dry_run,
    }


__all__ = [
    "BACKENDS",
    "INSTALLABLE",
    "classify_model",
    "read_profile_index",
    "auto_select",
    "install_plan",
]
