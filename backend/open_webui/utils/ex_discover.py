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

import os
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


def probe_executable(component: str, home: str | Path | None = None) -> dict:
    """Look for one component's binary. Returns a report; never raises.

    Checks PATH first, then the known candidate directories. PATH first because if the
    user has deliberately put a build on PATH, that is the one they mean, and a stale
    directory should not silently win over an explicit choice.
    """
    home_path = Path(home).expanduser() if home is not None else Path.home()
    names = EXECUTABLES.get(component, ())
    tried: list[str] = []
    found: Path | None = None
    source = "missing"

    for name in names:
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
    }
    report["hint"] = describe(report)
    return report


def describe(report: dict) -> str:
    """One line Fit for a settings row. Plain language, no jargon."""
    component = report.get("component", "component")
    if report.get("found"):
        where = "on PATH" if report.get("source") == "path" else "in a known location"
        return f"{component}: found {where} at {report.get('path')}"
    return f"{component}: not found. Checked PATH and {len(report.get('candidates_tried', []))} known locations."


def probe_all(home: str | Path | None = None, components: tuple[str, ...] = DISCOVERABLE) -> dict:
    """Probe every discoverable component.

    Returns {components: {...}, ready: bool, missing: [...]}. `ready` is True when every
    requested component was found, which is what the setup workflow gates on: a fresh
    machine is simply the case where `missing` is non-empty.
    """
    reports = {component: probe_executable(component, home=home) for component in components}
    missing = [c for c, r in reports.items() if not r["found"]]
    return {
        "components": reports,
        "missing": missing,
        "ready": not missing,
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
    "probe_executable",
    "probe_all",
    "describe",
    "manual_select",
]
