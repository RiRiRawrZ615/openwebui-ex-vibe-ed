"""Fork module registry (pure).

Modularity goal from RiRi: after the move, components can be swapped or updated freely
without rewiring the request path. The shape that makes that possible is a registry. Each
module declares what it is, whether it can be turned off, and which entrypoints the request
path is allowed to call. The request path calls entrypoints of enabled modules only.

Rules that matter, and why:
  - a feature module can be disabled, but a module that another module depends on cannot be
    disabled while the dependent is on. That is reported as a refusal, not applied silently,
    because a silent dependency break looks like a working app that is missing behavior.
  - core modules are not disableable. The settings resolver is the thing every other module
    reads, so turning it off would make "disabled" meaningless.
  - a drop-in manifest may add a module, but it may not claim a core entrypoint and it may
    not reuse a builtin name. Otherwise a file in the plugin dir could impersonate the
    journal or the loop guard.
  - plugin files are read from the fork plugin dir only. Absolute paths and traversal out of
    that dir are refused, the same posture the model check uses.
  - nothing here executes code. It reports what is enabled, what a load would do, and what is
    known only as declared rather than observed.

Wiring is reported from observation, not from a field I set myself. `status()` takes the
observed import set from the caller. A module with no observed importer is reported as
"logic only, no call site", which is the honest state for batch, cluster, recovery and theme
right now.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REGISTRY_NAME = "modules"

# Core entries are not disableable. Every feature reads the resolver, so disabling it would
# make the word "enabled" incoherent.
CORE = ("settings", "modules", "wiring")

# Layer is declared so a UI change does not look like a backend change. Only the backend
# layer can be reached by the request path; the ui layer is display, and the plugin layer is
# loaded from the plugin dir.
LAYERS = ("backend", "ui", "plugin")

# Module name to entrypoint names the request path may call. Kept as data so a registry
# change does not need a code change, and so a test can assert a plugin cannot add one.
ENTRYPOINTS = {
    "settings": ("resolve_ex_settings",),
    "journal": ("journal_window", "read_journal", "journal_index"),
    "loop_guard": ("loop_check",),
    "stats": ("stats", "live_estimate", "format_stats_line"),
    # Keyed by the actual file name. The registry called this "digest" while the module is
    # ex_export.py, so a read of the tree could not resolve it to a registry key. Keying by file
    # name is the only way the derivation and the registry agree.
    "export": ("digest", "verbatim_digest"),
    "handshake": ("load_handshake", "render_status"),
    "backend": ("auto_select", "install_plan", "path_allowed"),
    "import": ("db_path", "import_tables"),
    "batch": ("plan", "summary"),
    "cluster": ("plan", "rpc_args", "status", "parse_advert", "parse_qr"),
    "recovery": ("classify_error", "decide", "retry_permitted", "write_checkpoint", "load_checkpoint", "resume_plan", "export_fallback"),
    "theme": ("build_theme", "css_tokens", "default_theme"),
    "modules": ("resolve_enabled", "status", "load_manifest"),
    "context": ("parse_export", "previous_context_block", "previous_context_message", "status"),
    "steering": ("build_item", "plan", "render_note", "pairing_safe_insert_index", "append_item", "load_queue"),
    "ui": ("resolve_manifest", "resolve_slots", "status"),
    "trim": ("resolve_gate", "gate_features", "status"),
    # The adapter the request path actually calls. It is a registry entry because a feature
    # module cannot be disabled while the adapter still calls into it.
    "wiring": ("journal_window", "loop_check", "stats", "live_estimate", "digest", "read_journal"),
}

# Declared dependency edges. Kept flat on purpose: a deep graph makes a refusal message
# impossible to read, and every real edge here is one hop.
DEPENDS = {
    "journal": ("settings",),
    "loop_guard": ("settings",),
    "stats": ("settings",),
    "export": ("settings",),
    "handshake": ("settings",),
    "backend": ("settings",),
    "import": ("settings",),
    "batch": ("settings",),
    "cluster": ("settings",),
    "recovery": ("settings",),
    "theme": ("settings",),
    "context": ("settings",),
    "steering": ("settings",),
    "ui": ("settings",),
    "trim": ("settings",),
    "modules": (),
    # Every feature depends only on the resolver, which is core and cannot be turned off, so
    # a feature edge alone can never produce a refusal. The adapter is the entry where a real
    # refusal can happen: disabling journal while the request path still calls through it.
    "wiring": ("journal", "loop_guard", "stats", "export", "context", "steering", "trim"),
    "settings": (),
}

SAFE_NAME = re.compile(r"^[a-z0-9_]{1,40}$")
PLUGIN_DIR_NAME = "ex_plugins"


def normalize(name: str) -> str:
    """Module names are lowercase, no path separators, no spaces. A plugin name that fails
    this is refused rather than stored as-is, because a name is used as a key and as a path."""
    candidate = str(name or "").strip().lower().replace("-", "_")
    return candidate if SAFE_NAME.match(candidate) else ""


def plugin_dir(data_dir: Path | str) -> Path:
    """Fork plugin dir under the data dir. The fork does not write inside upstream dirs."""
    return Path(data_dir) / PLUGIN_DIR_NAME


def resolve_enabled(settings: dict | None) -> dict:
    """Effective enabled map. A settings block may override a feature; a core entry cannot be
    turned off, so an override on a core entry is reported in refused_overrides instead of
    being applied silently."""
    settings = settings or {}
    raw = settings.get("modules") or {}
    enabled = {}
    refused = []

    for name in ENTRYPOINTS:
        if name in CORE:
            enabled[name] = True
            if isinstance(raw, dict) and raw.get(name) is False:
                refused.append({"name": name, "reason": "core entry cannot be disabled"})
            continue
        enabled[name] = bool(raw.get(name, True)) if isinstance(raw, dict) else True

    return {"enabled": enabled, "refused_overrides": refused}


def dependency_refusals(enabled: dict) -> list[dict]:
    """A module cannot be on when something it depends on is off."""
    out = []
    for name, deps in DEPENDS.items():
        if not enabled.get(name):
            continue
        missing = [d for d in deps if not enabled.get(d)]
        if missing:
            out.append({"module": name, "missing": missing, "reason": "dependency is disabled"})
    return out


def load_manifest(path: Path | str, roots: list[Path] | tuple[Path, ...]) -> dict:
    """Read a plugin manifest. Refuses anything outside the plugin roots, and refuses a
    manifest that claims a core entrypoint or reuses a builtin name."""
    path = Path(path)
    roots = [Path(r) for r in roots]

    if not roots:
        return {"accepted": False, "reason": "no plugin root supplied", "refused": True}

    resolved = path.resolve()
    allowed = any(resolved == root or resolved.is_relative_to(root) for root in roots)
    if not allowed:
        return {"accepted": False, "refused": True, "reason": "plugin file is outside the plugin roots", "path": str(resolved)}

    if not path.exists():
        return {"accepted": False, "refused": True, "reason": "plugin file does not exist", "path": str(resolved)}

    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (ValueError, OSError) as error:
        return {"accepted": False, "refused": True, "reason": f"manifest unreadable: {error}", "path": str(resolved)}

    if not isinstance(raw, dict):
        return {"accepted": False, "refused": True, "reason": "manifest is not an object", "path": str(resolved)}

    name = normalize(raw.get("name"))
    if not name:
        return {"accepted": False, "refused": True, "reason": "manifest name is not a safe module name", "path": str(resolved)}

    if name in ENTRYPOINTS:
        return {
            "accepted": False,
            "refused": True,
            "reason": f"manifest reuses a builtin module name: {name}",
            "path": str(resolved),
            "name": name,
        }

    claimed = [str(e) for e in (raw.get("entrypoints") or [])]
    claimed_core = [e for e in claimed if e in {ep for eps in ENTRYPOINTS.values() for ep in eps}]
    if claimed_core:
        return {
            "accepted": False,
            "refused": True,
            "reason": "plugin cannot claim a core entrypoint",
            "claimed": claimed_core,
            "path": str(resolved),
            "name": name,
        }

    return {
        "accepted": True,
        "refused": False,
        "name": name,
        "entrypoints": claimed,
        "depends_on": [normalize(d) for d in (raw.get("depends_on") or []) if normalize(d)],
        "layer": "plugin",
        "path": str(resolved),
    }


def observed_from_tree(root: Path | str) -> dict:
    """Which files actually import each module, read from the tree.

    This is the definition of wired used by status(). It is derived, not asserted, because a
    registry that names its own importers could claim a call site that does not exist. A module
    with no importer here has no call site, and is reported that way.
    """
    root = Path(root)
    found = {}
    if not root.exists():
        return found

    for path in sorted(root.rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if not line.startswith("from open_webui.utils.ex_"):
                continue
            # The module name is the segment after utils, and it can be followed by an import
            # clause or an alias, so the name is the first token of that segment.
            raw = normalize(line.split(".")[2].split()[0])
            if not raw:
                continue
            # The import name carries the ex_ prefix; the registry key does not. Resolve to the
            # registry key, otherwise the observed map and the registry disagree and every module
            # reports as having no call site.
            name = raw[3:] if raw.startswith("ex_") else raw
            if name not in ENTRYPOINTS:
                # An import of something the registry does not know is reported as itself, so a
                # stray file is not silently dropped.
                name = raw
            found.setdefault(name, set()).add(str(path.relative_to(root)))

    return {name: sorted(files) for name, files in found.items()}


def call_site_kinds(importers: list[str]) -> list[str]:
    """Where a module is actually reached from.

    A router that reads a module is an endpoint. A helper the request path calls through is an
    adapter. The request path itself is the runtime path. These are named separately because
    'wired' covers all three, and a module reached only from a settings endpoint is not the same
    thing as a module the pipeline executes.
    """
    kinds = []
    if any("middleware.py" in f or "context_compaction.py" in f for f in importers):
        kinds.append("runtime request path")
    if any("ex_wiring.py" in f for f in importers):
        kinds.append("adapter")
    if any("routers/" in f for f in importers):
        kinds.append("endpoint")
    return kinds or ["none"]


def runtime_reachable(name: str, observed: dict) -> bool:
    """Can the request path actually reach this module.

    Two ways, and they are not the same claim:
      - directly, a file on the request path imports it,
      - through the adapter, ex_wiring imports it and ex_wiring itself is imported by the
        request path.

    Reporting only direct importers understates reachability. journal and loop_guard are called
    through the adapter, so a report that says "imported by ex_wiring" without following that
    edge would read as not wired, which is wrong. Both fields are kept so the distinction stays
    visible.
    """
    importers = observed.get(name, [])
    if any("middleware.py" in f or "context_compaction.py" in f for f in importers):
        return True
    if any("ex_wiring.py" in f for f in importers):
        adapter_importers = observed.get("wiring", [])
        return any("middleware.py" in f or "context_compaction.py" in f for f in adapter_importers)
    return False


def status(settings: dict | None, observed_imports: dict | None = None, roots: list[Path] | tuple[Path, ...] = ()) -> dict:
    """Report the registry as it actually is.

    `observed_imports` maps a module name to the modules that import it. The caller supplies
    it from the tree. Nothing here claims it is wired from a field I set, so a module with no
    importer is reported as logic only.
    """
    resolved = resolve_enabled(settings)
    enabled = resolved["enabled"]
    observed = observed_imports or {}
    rows = []

    for name in sorted(ENTRYPOINTS):
        importers = sorted(observed.get(name, []))
        rows.append({
            "name": name,
            "layer": "backend",
            "enabled": enabled[name],
            "core": name in CORE,
            "entrypoints": list(ENTRYPOINTS[name]),
            "depends_on": list(DEPENDS[name]),
            "wired": bool(importers),
            "observed_by": importers,
            # An endpoint is a call site, but it is not the runtime request path. Kept separate
            # so a router that reads a module is not reported as the pipeline executing it.
            "call_site": call_site_kinds(importers),
            "runtime_reachable": runtime_reachable(name, observed),
            "state": "wired" if importers else "logic only, no call site",
        })

    plugins = []
    plugin_root = plugin_dir(roots[0]) if roots else None
    if plugin_root and plugin_root.exists():
        for entry in sorted(plugin_root.glob("*.json")):
            manifest = load_manifest(entry, [plugin_root])
            if manifest.get("accepted"):
                plugins.append(manifest)

    return {
        "registry": REGISTRY_NAME,
        "modules": rows,
        "plugins": plugins,
        "plugin_dir": str(plugin_root) if plugin_root else None,
        "refused_overrides": resolved["refused_overrides"],
        "dependency_refusals": dependency_refusals(enabled),
        "counts": {
            "total": len(rows),
            "enabled": sum(1 for r in rows if r["enabled"]),
            "wired": sum(1 for r in rows if r["wired"]),
            "plugins": len(plugins),
        },
    }


__all__ = [
    "REGISTRY_NAME",
    "CORE",
    "LAYERS",
    "ENTRYPOINTS",
    "DEPENDS",
    "PLUGIN_DIR_NAME",
    "normalize",
    "plugin_dir",
    "resolve_enabled",
    "dependency_refusals",
    "load_manifest",
    "observed_from_tree",
    "call_site_kinds",
    "runtime_reachable",
    "status",
]
