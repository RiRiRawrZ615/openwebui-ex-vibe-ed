"""UI slot registry (pure).

The modularity goal as RiRi stated it: after the move, components can be swapped or updated
freely without rewiring the request path. For backend modules the registry already exists in
ex_modules. This is the frontend half, and it needs its own rules because a frontend component
is not called by the request path, it is rendered by a host that resolves a slot.

A slot is a named place the shipped UI agrees to fill from a manifest. A plugin declares which
slot it fills and which file fills it. The host resolves slot name to component. Nothing in the
shipped UI imports a plugin file by name, so a plugin cannot be reached except through a slot.

Rules and why:
  - reserved slots cannot be claimed by a plugin if they are marked core. A plugin that could
    impersonate the chat pane or the settings modal is the same risk as a plugin claiming a core
    entrypoint.
  - a slot resolution must resolve inside the plugin root. Absolute paths and traversal out of
    the root are refused, the same posture ex_modules uses for manifests.
  - a plugin can be disabled without breaking the host, because an empty slot falls back to the
    built-in component. That is the difference between a plugin system and a fork edit: a fork
    edit has no fallback.
  - nothing here renders anything or executes code. It reports what a slot resolves to, what the
    fallback would be, and what is refused.

The honest limit, stated so a later context does not over-claim: a slot registry is only useful
if a host actually reads it. A registry with no host is a data structure. The host must be
mounted in the frontend before any slot resolution means anything.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PLUGIN_ROOT_NAME = "ex_ui"
SAFE_NAME = re.compile(r"^[a-z0-9_]{1,40}$")

# Where the frontend host can actually render a plugin. The host reads a statically analyzable
# glob, so only files inside this directory can be loaded without a rebuild. A manifest in the data
# plugin root is validated there, but its component is renderable only when the file also exists
# here. Stating this is the difference between a plugin system and a promise the bundler cannot keep.
# Anchored to the fork root from the module's own location, not to cwd, because the working
# directory changes with how the app is started and a relative default would resolve differently
# for the same plugin.
FRONTEND_PLUGIN_DIR = Path(__file__).resolve().parents[3] / "src/lib/components/ex/plugins"

# Slots the shipped UI fills. core means a plugin may not replace it.
SLOTS = {
    "settings_tab": {"core": False, "builtin": "OpenWebVibes.svelte"},
    "response_footer": {"core": False, "builtin": "StatsRow"},
    "message_actions": {"core": False, "builtin": "MessageActions"},
    "chat_pane": {"core": True, "builtin": "ChatPane"},
    "settings_modal": {"core": True, "builtin": "SettingsModal"},
    "sidebar": {"core": True, "builtin": "Sidebar"},
}

# A manifest may request these. Anything else is refused rather than passed to a renderer.
ALLOWED_KINDS = ("component", "style", "hook")


def normalize(name: str) -> str:
    candidate = str(name or "").strip().lower().replace("-", "_")
    return candidate if SAFE_NAME.match(candidate) else ""


def plugin_root(data_dir: Path | str) -> Path:
    return Path(data_dir) / PLUGIN_ROOT_NAME


def resolve_manifest(path: Path | str, roots: list[Path] | tuple[Path, ...]) -> dict:
    """Read one UI manifest. Refuses traversal, reuse of a core slot, and unknown kinds."""
    path = Path(path)
    roots = [Path(r) for r in roots]

    if not roots:
        return {"accepted": False, "refused": True, "reason": "no plugin root supplied"}

    resolved = path.resolve()
    if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
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
    slot = normalize(raw.get("slot"))
    kind = str(raw.get("kind") or "component")

    if not name:
        return {"accepted": False, "refused": True, "reason": "manifest name is not a safe plugin name", "path": str(resolved)}
    if slot not in SLOTS:
        return {"accepted": False, "refused": True, "reason": f"slot is not a known slot: {slot}", "path": str(resolved)}
    if SLOTS[slot]["core"]:
        return {"accepted": False, "refused": True, "reason": f"slot {slot} is core and cannot be replaced", "path": str(resolved), "name": name}
    if kind not in ALLOWED_KINDS:
        return {"accepted": False, "refused": True, "reason": f"kind must be one of {ALLOWED_KINDS}", "path": str(resolved)}

    component = str(raw.get("component") or "")
    if kind == "component":
        if not component:
            return {"accepted": False, "refused": True, "reason": "component manifests need a component path", "path": str(resolved)}
        # A relative component path is relative to the manifest, not to the process cwd. Resolving
        # it against cwd refused a perfectly valid plugin component, which is the difference
        # between a working plugin and one that can never be accepted.
        target = (resolved.parent / component).resolve()
        if not target.is_relative_to(resolved.parent):
            return {"accepted": False, "refused": True, "reason": "component path escapes the plugin root", "path": str(resolved)}
        # A single filename, not a subdirectory. The frontend host matches plugins by a fixed glob
        # over one directory, so a component in a subfolder would be validated here and then fail
        # to load there. Refusing it keeps both sides describing the same path.
        if "/" in component:
            return {"accepted": False, "refused": True, "reason": "component must be a single filename inside the plugin root", "path": str(resolved)}
    else:
        target = resolved

    return {
        "accepted": True,
        "refused": False,
        "name": name,
        "slot": slot,
        "kind": kind,
        "component": component,
        "fallback": SLOTS[slot]["builtin"],
        "path": str(resolved),
    }


def resolve_slots(root: Path | str, enabled: dict | None = None, frontend_dir: Path | str | None = None) -> dict:
    """Slot to component, with the fallback kept visible.

    A disabled plugin does not leave a hole. The slot resolves to the builtin, which is the whole
    point of a slot registry instead of editing the shipped component.
    """
    root = Path(root)
    enabled = enabled or {}
    rows = []
    refused = []

    if root.exists():
        for entry in sorted(root.glob("*.json")):
            manifest = resolve_manifest(entry, [root])
            if not manifest.get("accepted"):
                refused.append(manifest)
                continue
            slot = manifest["slot"]
            # Both the plugin and the slot it claims must be on. An or here would let a
            # disabled plugin resolve as active whenever its own name was enabled, which is the
            # opposite of what disabling means.
            plugin_on = bool(enabled.get(slot, True)) and bool(enabled.get(manifest["name"], True))
            # A component in the data plugin root is not reachable by the bundler at runtime. The
            # host renders it only when the file also sits in the frontend plugin directory, so the
            # registry reports the two cases separately instead of claiming a plugin is live.
            component = manifest["component"]
            host_dir = Path(frontend_dir) if frontend_dir else Path(FRONTEND_PLUGIN_DIR)
            renderable = bool(
                plugin_on
                and manifest["kind"] == "component"
                and component
                and (host_dir / component).exists()
            )
            rows.append({
                "slot": slot,
                "plugin": manifest["name"] if plugin_on else "",
                "component": component if plugin_on else "",
                "fallback": manifest["fallback"],
                "resolved": (manifest["component"] if renderable else manifest["fallback"]) if plugin_on else manifest["fallback"],
                "state": "plugin" if renderable else ("resolved, not renderable" if plugin_on else "builtin (plugin disabled)"),
                "renderable": renderable,
            })

    for slot, info in SLOTS.items():
        if not any(r["slot"] == slot for r in rows):
            rows.append({"slot": slot, "plugin": "", "component": "", "fallback": info["builtin"],
                         "resolved": info["builtin"], "state": "builtin (no plugin)"})

    rows.sort(key=lambda r: r["slot"])
    return {
        "slots": rows,
        "refused": refused,
        "plugin_root": str(root),
        "core_slots": [s for s, i in SLOTS.items() if i["core"]],
        "counts": {"slots": len(rows), "filled_by_plugin": sum(1 for r in rows if r["plugin"]), "refused": len(refused)},
        "host_note": "a slot only means something when a frontend host reads this registry; a registry with no host is data",
        "renderable_note": f"plugin components render from {FRONTEND_PLUGIN_DIR}; a manifest elsewhere is validated but cannot be loaded without a rebuild",
    }


def status(settings: dict | None, root: Path | str = "") -> dict:
    settings = settings or {}
    return {
        "uiPlugins": bool(settings.get("uiPlugins", True)),
        "plugin_root": str(plugin_root(root) if root else ""),
        "allowed_kinds": list(ALLOWED_KINDS),
        "slots": sorted(SLOTS),
        "core_slots": [s for s, i in SLOTS.items() if i["core"]],
        "note": "disabling a plugin falls back to the builtin component, so a slot never breaks the host",
    }


__all__ = [
    "PLUGIN_ROOT_NAME",
    "SLOTS",
    "ALLOWED_KINDS",
    "normalize",
    "plugin_root",
    "resolve_manifest",
    "resolve_slots",
    "status",
]
