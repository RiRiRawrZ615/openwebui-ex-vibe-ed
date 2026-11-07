"""Builtin tool registry: shipped tools, per-tool toggles, and the user edit overlay.

What this is
------------
The nine tools that used to live only in a WebUI export now ship inside the product. Each is
individually switchable, and each can be overridden by a user edit that survives releases.

Resolution order for one tool's source
--------------------------------------
    1. <DATA_DIR>/builtins/overlay/<slug>.py    a user edit, whole file
    2. <package>/tools/builtins/<slug>.py       the shipped builtin

The overlay is a whole-file replacement rather than a patch. A line-level patch would have to
be re-anchored against every future version of the builtin and would fail silently when it
could not be. A whole file either parses and works or visibly does not, which is the property
worth having when the thing being overridden is code that runs.

The overlay also records the hash of the builtin it was written against, so the UI can say
"this edit is based on an older version" instead of quietly shadowing an upstream fix.

Why an explicit allowlist
-------------------------
Converting a tool into a module puts its imports in the module namespace. `youtube_visual_1`
ends up with `parse_qs`, `urlopen` and `urlparse` as module-level functions, and `pcalc` ends
up with pydantic's `Field`. Iterating the namespace and offering everything callable would
expose those to the model as tools, which is both useless and confusing. So the registry
exposes exactly the function names the original export declared, taken from the manifest.

Pure with respect to import: nothing here imports a builtin at module import time. Baking in
nine modules, one of which is 87 KB, would slow every application start even for users who
have every tool switched off. Modules load on first use and are then cached.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import logging
import os
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Where the shipped builtins live, relative to this file's package.
_SHIPPED_DIR = Path(__file__).resolve().parent / 'builtins'
_MANIFEST_PATH = _SHIPPED_DIR / 'manifest.json'

# Loaded modules, keyed by (slug, source_hash). The hash is part of the key so that editing an
# overlay replaces the module rather than serving a stale import.
_MODULE_CACHE: dict[tuple[str, str], object] = {}


def _data_dir() -> Path:
    """Resolve the data directory the same way the rest of the app does."""
    from open_webui.env import DATA_DIR

    return Path(DATA_DIR)


def overlay_dir() -> Path:
    path = _data_dir() / 'builtins' / 'overlay'
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path() -> Path:
    return _data_dir() / 'builtins' / 'registry.json'


def load_manifest() -> dict:
    """The shipped inventory. Fails soft: a missing manifest means no builtins, not a crash."""
    try:
        return json.loads(_MANIFEST_PATH.read_text())
    except FileNotFoundError:
        log.warning('builtin manifest missing at %s', _MANIFEST_PATH)
        return {}
    except Exception as exc:  # noqa: BLE001
        log.error('builtin manifest unreadable: %s', exc)
        return {}


# --- enabled state ------------------------------------------------------------------------


def load_state() -> dict:
    try:
        return json.loads(state_path().read_text())
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001
        log.warning('builtin registry state unreadable, treating as empty: %s', exc)
        return {}


def save_state(state: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + '\n')
    os.replace(tmp, path)


def slug_for_tool_id(tool_id: str) -> str | None:
    """Reverse lookup from the stored tool id to the registry slug.

    Needed because the tools admin page works on ids while the registry works on slugs, and the
    request path has to decide which authority applies to a given row.
"""
    for slug, entry in load_manifest().items():
        if entry.get('tool_id') == tool_id:
            return slug
    return None


def is_enabled(slug: str, default: bool = True) -> bool:
    """Per-tool switch.

    Defaults to enabled. RiRi's call for the release: these are the tools the product is built
    around, with search replacing the bundled web search outright, so a fresh install should
    arrive working rather than with everything dark. Admins switch off what they do not want.
    """
    entry = load_state().get(slug) or {}
    return bool(entry.get('enabled', default))


def set_enabled(slug: str, enabled: bool) -> dict:
    if slug not in load_manifest():
        raise KeyError(slug)
    state = load_state()
    entry = state.setdefault(slug, {})
    entry['enabled'] = bool(enabled)
    entry['updated_at'] = time.time()
    save_state(state)
    return entry


# --- source resolution and hashing --------------------------------------------------------


def overlay_path(slug: str) -> Path:
    return overlay_dir() / f'{slug}.py'


def shipped_path(slug: str) -> Path | None:
    meta = load_manifest().get(slug)
    if not meta:
        return None
    candidate = _SHIPPED_DIR / meta['source']
    return candidate if candidate.exists() else None


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def resolve_source(slug: str) -> tuple[str, str, str]:
    """Return (source_text, origin, hash) where origin is 'overlay' or 'builtin'."""
    overlay = overlay_path(slug)
    if overlay.exists():
        text = overlay.read_text()
        return text, 'overlay', _hash(text)

    shipped = shipped_path(slug)
    if shipped is None:
        raise KeyError(slug)
    text = shipped.read_text()
    return text, 'builtin', _hash(text)


def builtin_hash(slug: str) -> str:
    """Hash of the shipped file, used to detect an overlay based on an older version."""
    shipped = shipped_path(slug)
    return _hash(shipped.read_text()) if shipped else ''


# --- loading ------------------------------------------------------------------------------


def load_module(slug: str):
    """Import a builtin's module, preferring the overlay. Cached by source hash."""
    text, origin, source_hash = resolve_source(slug)
    key = (slug, source_hash)
    if key in _MODULE_CACHE:
        return _MODULE_CACHE[key]

    # A stable, namespace-safe module name. The slug comes from an export id, so it is already
    # limited to word characters, but it is sanitised anyway because it becomes a module name.
    safe = ''.join(ch if ch.isalnum() or ch == '_' else '_' for ch in slug)
    module_name = f'owv_builtin_{safe}'

    spec = importlib.util.spec_from_loader(module_name, loader=None)
    module = importlib.util.module_from_spec(spec)
    # Registered before exec so a tool that imports itself by name resolves.
    sys.modules[module_name] = module
    try:
        exec(compile(text, f'<owv_builtin:{slug}:{origin}>', 'exec'), module.__dict__)
    except Exception:
        # Never leave a half-initialised module in sys.modules; the next attempt would get it.
        sys.modules.pop(module_name, None)
        raise

    _MODULE_CACHE[key] = module
    return module


def load_instance(slug: str):
    """The tool object a builtin exposes.

    Every fork export is shaped as `class Tools:` with methods, which is the shape Open WebUI
    registers. A module-level getattr found nothing for those, so a builtin marked enabled in the
    registry still produced no callable and never reached the model. That is why the toolset looked
    unwired even though the manifest, the files, and the enabled state were all correct. Module-level
    definitions are still honored for anything written that way.
    """
    module = load_module(slug)
    tool_class = getattr(module, 'Tools', None)
    if tool_class is None:
        return module
    instance = tool_class()
    # Stock tools read `self.valves` and `self.user`, so the instance is the thing that must carry them.
    instance.__owv_slug__ = slug
    return instance


def get_functions(slug: str) -> list:
    """The callables this tool exposes, restricted to what the manifest declares.

    The allowlist is the whole point: the module namespace contains the tool's imports, and
    offering `urlparse` to a model as a tool is wrong.

    Each returned function is tagged with `__owv_slug__`. The caller uses that to namespace the
    function when registering it, because the shipped tools genuinely collide: `check`, `fleet`,
    `run`, `rustdesk` and `search` each appear in two of the nine exports. Tagging happens here
    rather than at the registration site so that the association cannot be lost.
    """
    meta = load_manifest().get(slug)
    if not meta:
        return []

    target = load_instance(slug)
    functions = []
    for name in meta.get('specs', []):
        fn = getattr(target, name, None)
        if callable(fn):
            # setattr on a module-level function is safe and visible to every caller, since
            # there is exactly one function object per name per module.
            # A method resolved through an instance is a bound method, and an attribute cannot be set
            # on the bound wrapper. Tagging the underlying function is what makes the lookup work from
            # either side, and the namespacing at the registration site depends on that lookup.
            tag_target = getattr(fn, '__func__', fn)
            try:
                tag_target.__owv_slug__ = slug
            except (AttributeError, TypeError):
                log.warning('could not tag %s from %s for namespacing', name, slug)
            functions.append(fn)
        else:
            log.error('builtin %s declares %s but the module does not define it', slug, name)
    return functions


def enabled_functions() -> list[tuple[str, object]]:
    """Every function from every enabled builtin, as (slug, function).

    The slug travels alongside the function so a failure can be attributed to the tool that
    caused it rather than to "a builtin".
    """
    out: list[tuple[str, object]] = []
    for slug in sorted(load_manifest()):
        if not is_enabled(slug):
            continue
        try:
            for fn in get_functions(slug):
                out.append((slug, fn))
        except Exception as exc:  # noqa: BLE001
            # One broken tool must not take down the whole toolset.
            log.error('builtin %s failed to load and was skipped: %s', slug, exc)
    return out


# --- overlay management -------------------------------------------------------------------


def save_overlay(slug: str, text: str) -> dict:
    """Write a user edit, after checking it is at least syntactically valid Python.

    A syntax check is the minimum bar. It cannot prove the edit is *correct*, but it does stop
    the common case of a save that would break the tool on next load with a traceback.
    """
    if slug not in load_manifest():
        raise KeyError(slug)

    try:
        compile(text, f'<overlay:{slug}>', 'exec')
    except SyntaxError as exc:
        raise ValueError(f'overlay has a syntax error on line {exc.lineno}: {exc.msg}') from exc

    path = overlay_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)

    state = load_state()
    entry = state.setdefault(slug, {})
    entry['source'] = 'overlay'
    entry['overlay_hash'] = _hash(text)
    # What the edit was written against. When the shipped file changes, this no longer matches
    # builtin_hash() and the UI can say so.
    entry['based_on'] = builtin_hash(slug)
    entry['updated_at'] = time.time()
    save_state(state)
    return entry


def revert_overlay(slug: str) -> dict:
    """Drop a user edit and fall back to the shipped builtin."""
    if slug not in load_manifest():
        raise KeyError(slug)

    path = overlay_path(slug)
    existed = path.exists()
    if existed:
        path.unlink()

    state = load_state()
    entry = state.setdefault(slug, {})
    entry['source'] = 'builtin'
    entry.pop('overlay_hash', None)
    entry.pop('based_on', None)
    entry['updated_at'] = time.time()
    save_state(state)
    return {'reverted': existed}


def describe(slug: str) -> dict:
    """Everything the settings UI needs about one builtin, in one call."""
    meta = load_manifest().get(slug) or {}
    state = load_state().get(slug) or {}
    origin = 'overlay' if overlay_path(slug).exists() else 'builtin'

    info = {
        'slug': slug,
        'name': meta.get('name', slug),
        'tool_id': meta.get('tool_id', ''),
        'specs': meta.get('specs', []),
        'enabled': is_enabled(slug),
        'source': origin,
        'has_overlay': origin == 'overlay',
        'updated_at': state.get('updated_at'),
        'load_error': None,
    }

    # Report a failure now rather than at first model call, so a broken overlay is visible in
    # the settings tab where it can be fixed.
    try:
        load_module(slug)
    except Exception as exc:  # noqa: BLE001
        info['load_error'] = f'{type(exc).__name__}: {exc}'

    # Stale overlay: the edit was written against a shipped version that has since changed.
    if info['has_overlay']:
        based_on = state.get('based_on')
        info['based_on'] = based_on
        info['builtin_hash'] = builtin_hash(slug)
        info['overlay_stale'] = bool(based_on) and based_on != info['builtin_hash']
    else:
        info['overlay_stale'] = False

    return info


def describe_all() -> list[dict]:
    return [describe(slug) for slug in sorted(load_manifest())]


__all__ = [
    'load_manifest',
    'load_state',
    'save_state',
    'is_enabled',
    'set_enabled',
    'load_module',
    'get_functions',
    'enabled_functions',
    'resolve_source',
    'builtin_hash',
    'overlay_path',
    'shipped_path',
    'save_overlay',
    'revert_overlay',
    'describe',
    'describe_all',
]
