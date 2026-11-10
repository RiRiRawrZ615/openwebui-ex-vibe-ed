"""Binary and component discovery for the setup workflow (pure).

Why this exists
---------------
The Setup tab has to answer one question honestly: *what does this machine already
have?* A tab that asks the user to type a path when a working binary is sitting two
directories away is worse than useless, and a tab that claims something is installed
when it is not is actively harmful.

So this module probes, and reports what it found with evidence. It never executes a
candidate to check a version, and it never downloads anything. Running a found binary
to interrogate it is exactly the kind of thing that turns a settings tab into a
remote-code surface, and version probing is not worth that.

Design rules, matching ex_backend.py
------------------------------------
- Pure: no subprocess, no network, no imports from open_webui.
- Nothing is written here. The caller decides what to persist.
- A candidate is reported with the reason it was accepted or rejected, so the UI can
  show a real state instead of a guess.

Where the candidate list comes from
-----------------------------------
The search order mirrors RiRi's own launchers, which encode real working layouts:
`~/Documents/openwebui_llamacpp_safe_moe_v4.sh` (AMD / NVIDIA / generic llama.cpp
builds) and `~/Documents/strata_openwebui_launcher.sh`. Those scripts check the same
directories in the same order, and the two must not drift, so the list lives here as
the single source and the shell side is compared against it by a test.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

# Components the setup workflow can discover, in the order the UI should present them.
# 'webui' is absent on purpose: the frontend is what is running this code, so it is
# never "found" or "missing" in a useful sense.
DISCOVERABLE = ("llamacpp", "strata", "bonsai")

# Executable names per component. A component is satisfied by the first of these that
# resolves on PATH.
EXECUTABLES = {
    "llamacpp": ("llama-server", "llama-cli"),
    "strata": ("strata", "strata-server", "strata_cli"),
    "bonsai": ("bonsai", "bonsai-server", "bonsai_cli"),
}

# Relative candidate directories per component, most-specific first. Expanded against
# the user's home. Order matters: a backend-specific build is preferred over a generic
# one, because on a machine with both, the backend-specific build is the one that has
# the right kernels compiled in.
#
# Kept as relative paths so this stays portable. RiRi's machines resolve to the same
# places as everyone else's; they simply already have the directories present.
CANDIDATE_DIRS = {
    "llamacpp": (
        # The ternary build is its own tree, not a variant inside llama.cpp, and the profile for a
        # ternary model names that path. It was missing here, so the setup tab reported "not found"
        # for a binary that exists and is the one the launcher actually starts from. That is the drift
        # the module docstring warns about: the shell list had it and this one did not.
        ("llama.cpp-ternary", "build-ternary", "bin"),
        ("llama.cpp-rdna2-fa", "build", "bin"),      # AMD, matches the v4 launcher
        ("llama.cpp-nvidia", "build", "bin"),        # NVIDIA, matches openwebui_nvidia.sh
        ("llama.cpp", "build", "bin"),               # generic
        ("llama.cpp", "build", "bin", "Release"),    # MSVC layout, harmless here
        ("llama.cpp-rdna2-fa", "build", "bin", "Release"),
        (".local", "bin"),
    ),
    "strata": (
        ("strata", "bin"),
        ("strata", "target", "release"),
        ("strata", "target", "debug"),
        (".local", "bin"),
        ("Documents", "strata"),
    ),
    "bonsai": (
        ("bonsai", "bin"),
        ("bonsai", "target", "release"),
        (".local", "bin"),
        ("Documents", "bonsai"),
    ),
}

# Report shape keys, so the UI and the shell agree on names:
#   component, found, path, source, executable, candidates_tried, hint
# `source` is 'path' | 'candidate' | 'missing', which is enough for the UI to say
# "found on PATH" versus "found at your known location" versus "not found".


def _is_executable_file(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


# Order a binary is resolved in, and it has to be the same order the launcher uses. The setup tab
# reports what will run, so a probe that prefers something the launcher would not pick is a report
# that is true about the filesystem and false about the process.
#   manual    a choice persisted in the setup tab, which is an explicit user decision
#   path      an explicit PATH entry
#   profile   the binary named by the profile for the model being started
#   candidate a known build directory
PRECEDENCE = ("manual", "path", "profile", "candidate")


def overrides_file(data_dir: str | Path) -> Path:
    """Where persisted manual choices live. The launcher reads the same file."""
    return Path(data_dir) / "ex_binaries.json"


def load_overrides(data_dir: str | Path) -> dict:
    """Read persisted manual choices. A malformed or absent file is an empty map, not an error."""
    try:
        raw = json.loads(Path(data_dir).joinpath("ex_binaries.json").read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: str(v) for k, v in raw.items() if k in DISCOVERABLE and v}


def save_override(data_dir: str | Path, component: str, executable: str | Path) -> dict:
    """Persist one manual choice so a later probe and the launcher see the same thing.

    The manual endpoint used to validate a path and return, which is why setting a binary in the
    setup tab changed nothing: the report was correct at that moment and nothing stored it, so the
    next probe re-ran discovery and the launcher kept using whatever it found on its own. Validation
    without persistence is a check that answers a question nobody asked.
    """
    path = overrides_file(data_dir)
    current = load_overrides(data_dir)
    current[component] = str(Path(executable).expanduser())
    try:
        path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as e:
        return {"persisted": False, "reason": f"could not write {path}: {e}"}
    return {"persisted": True, "file": str(path), "reason": "stored"}


def profile_binary(profile_path: str | Path) -> str:
    """The binary a profile names, or an empty string."""
    try:
        raw = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return ""
    return str(raw.get("llama_bin") or "")


def profile_path_for_model(profiles_dir: str | Path, model_name: str) -> Path | None:
    """Map a model id to its profile file, the same way the launcher does.

    Profile files on disk turn every dot into an underscore, so the literal stem and the underscore
    form are two different names. Both are tried, in the same order the shell tries them, because a
    model whose name contains a dot missed its profile when only the literal form was checked.
    """
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", str(model_name).split("/")[-1].rsplit(".gguf", 1)[0])
    alt = stem.replace(".", "_")
    base = Path(profiles_dir)
    for cand in (f"local_{stem}_gguf.json", f"local_{alt}_gguf.json", f"{stem}.json", f"{alt}.json"):
        p = base / cand
        if p.is_file():
            return p
    return None


def probe_executable(
    component: str,
    home: str | Path | None = None,
    overrides: dict | None = None,
    profiles_dir: str | Path | None = None,
    model_name: str | None = None,
) -> dict:
    """Look for one component's binary. Returns a report; never raises.

    Follows PRECEDENCE. PATH is checked before the candidate directories because a deliberate PATH
    entry is the build the user means, and a stale directory should not silently win over it.
    """
    home_path = Path(home).expanduser() if home is not None else Path.home()
    names = EXECUTABLES.get(component, ())
    tried: list[str] = []
    found: Path | None = None
    source = "missing"
    note = ""

    # A persisted manual choice is checked before PATH. It is an explicit decision, and the launcher
    # reads the same file, so the probe has to agree with it or the tab reports something the process
    # is not doing.
    override = (overrides or {}).get(component)
    if override:
        stored = Path(override).expanduser()
        tried.append(f"stored:{stored}")
        if _is_executable_file(stored):
            found, source = stored, "manual"
        else:
            # A stored path that no longer resolves is named in the report instead of falling through
            # to the next source silently. That is how a settings tab ends up lying.
            note = f"stored path does not resolve: {stored}"

    if found is None and component == "llamacpp" and profiles_dir and model_name:
        pfile = profile_path_for_model(profiles_dir, model_name)
        if pfile is not None:
            exe = profile_binary(pfile)
            tried.append(f"profile:{pfile}")
            if exe and _is_executable_file(Path(exe)):
                found, source = Path(exe), "profile"
            elif exe:
                note = f"profile names {exe}, which is not an executable file"

    for name in names:
        if found is not None:
            break
        resolved = shutil.which(name)
        resolved = shutil.which(name)
        if resolved:
            found = Path(resolved)
            source = "path"
            break
        tried.append(f"PATH:{name}")

    if found is None:
        for rel in CANDIDATE_DIRS.get(component, ()):
            for name in names:
                candidate = home_path.joinpath(*rel) / name
                tried.append(str(candidate))
                if _is_executable_file(candidate):
                    found = candidate
                    source = "candidate"
                    break
            if found is not None:
                break

    report = {
        "component": component,
        "found": found is not None,
        "path": str(found) if found else "",
        "source": source,
        "executable": found.name if found else "",
        "candidates_tried": tried,
        "note": note,
    }
    report["hint"] = describe(report)
    return report


def describe(report: dict) -> str:
    """One line Fit for a settings row. Plain language, no jargon."""
    component = report.get("component", "component")
    if report.get("found"):
        where = {
            "manual": "to your stored choice",
            "path": "on PATH",
            "profile": "through the profile for this model",
            "candidate": "in a known location",
        }.get(report.get("source"), "in a known location")
        return f"{component}: found {where} at {report.get('path')}"
    if report.get("note"):
        return f"{component}: {report['note']}"
    return f"{component}: not found. Checked PATH and {len(report.get('candidates_tried', []))} known locations."


# Which component a backend needs. A machine running llamacpp is not broken because a binary it does
# not use is absent, but a gate over the whole catalog reports exactly that: the laptop serving this
# session has its llamacpp binary resolving through a profile and was still reported as not ready.
# Readiness and the missing list therefore come from the backend in use, and the full catalog result
# is kept separately for a caller that wants to know what is installed.
BACKEND_COMPONENTS = {
    "llamacpp": ("llamacpp",),
    "strata": ("strata",),
    "bonsai": ("bonsai",),
}


def needed_components(backend: str | None) -> tuple[str, ...]:
    """Components the given backend requires. With no backend named, fall back to the whole catalog."""
    return BACKEND_COMPONENTS.get(str(backend or ""), tuple(DISCOVERABLE))


def probe_all(
    home: str | Path | None = None,
    components: tuple[str, ...] = DISCOVERABLE,
    overrides: dict | None = None,
    profiles_dir: str | Path | None = None,
    model_name: str | None = None,
    backend: str | None = None,
) -> dict:
    """Probe every discoverable component.

    Returns {components: {...}, ready: bool, missing: [...]}. `ready` is True when every
    requested component was found, which is what the setup workflow gates on: a fresh
    machine is simply the case where `missing` is non-empty.
    """
    reports = {
        component: probe_executable(
            component, home=home, overrides=overrides, profiles_dir=profiles_dir, model_name=model_name
        )
        for component in components
    }
    missing_all = [c for c, r in reports.items() if not r["found"]]
    needed = needed_components(backend)
    missing = [c for c in needed if not reports.get(c, {}).get("found")]
    return {
        "components": reports,
        "needed": list(needed),
        "backend": str(backend or ""),
        "missing": missing,
        "missing_all": missing_all,
        "ready": not missing,
        "hints": {c: describe(r) for c, r in reports.items()},
    }


def manual_select(component: str, path: str | Path) -> dict:
    """Validate a user-supplied path for a component.

    The UI offers a manual override because discovery cannot cover every layout. This
    only checks the file exists and is executable. It does not run it, and it does not
    accept a path for a component that is not discoverable, so the field cannot be used
    to point the setup at an arbitrary binary.
    """
    if component not in DISCOVERABLE:
        return {
            "component": component,
            "accepted": False,
            "path": str(path),
            "reason": f"unknown component: {component}",
        }

    candidate = Path(path).expanduser()
    if not candidate.exists():
        return {"component": component, "accepted": False, "path": str(candidate), "reason": "path does not exist"}
    if not candidate.is_file():
        return {
            "component": component,
            "accepted": False,
            "path": str(candidate),
            "reason": "path is a directory; point at the executable itself",
        }
    if not os.access(candidate, os.X_OK):
        return {
            "component": component,
            "accepted": False,
            "path": str(candidate),
            "reason": "path is not executable",
        }

    return {
        "component": component,
        "accepted": True,
        "path": str(candidate),
        "source": "manual",
        "executable": candidate.name,
        "reason": "accepted",
    }


__all__ = [
    "DISCOVERABLE",
    "EXECUTABLES",
    "CANDIDATE_DIRS",
    "PRECEDENCE",
    "probe_executable",
    "probe_all",
    "describe",
    "manual_select",
    "needed_components",
    "load_overrides",
    "save_override",
    "overrides_file",
    "profile_binary",
    "profile_path_for_model",
]
