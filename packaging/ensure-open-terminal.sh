#!/usr/bin/env bash
# Ensure Open Terminal exists for the interpreter the launcher will actually use.
#
# The package is noarch, so it never builds an environment. That means a fresh machine gets the
# fork code but no open_terminal module, and the launcher's terminal stage has nothing to start:
# the cache is empty, uvx --offline resolves to nothing, the health window expires, and the script
# gives up. The webui still boots, so the failure looks like a port problem rather than a missing
# package. This closes that gap at install time.
#
# Detection is an import against the target python, not a file lookup, because a venv does not see
# the user site-packages. Installing into ~/.local and then checking a different interpreter is
# exactly the mistake that made the two fleet boxes look installed while the launcher could not
# import the module.
#
# Order: already importable, then uv (the venvs here are uv-created and carry no pip), then pip
# with the PEP 668 flag for a system interpreter. One attempt each, no retries, and a failure is
# reported rather than fatal, so an offline install still completes.

set -Euo pipefail

state_dir="${OWV_STATE_DIR:-/var/lib/openwebvibes}"
mkdir -p "$state_dir" 2>/dev/null || true
log_file="$state_dir/open-terminal-install.log"

# Running from %post means root, and a --user install there lands in /root/.local, which the fork
# will never import. So the two entry points get different targets: a user run uses the interpreter
# the launcher will run, a root run uses the system interpreter and leaves the per-user environment
# to the launcher, which completes the install at first start with the right permissions.
if [ "$(id -u)" -eq 0 ]; then
    runtime_python="$(command -v python3 || true)"
    as_root=1
else
    runtime_python="${OWV_PYTHON:-/usr/lib/openwebvibes/.venv/bin/python}"
    if [ ! -x "$runtime_python" ]; then
        runtime_python="$(command -v python3 || true)"
    fi
    as_root=0
fi

[ -n "$OWV_TERMINAL_AUTOINSTALL" ] && autoinstall="$OWV_TERMINAL_AUTOINSTALL" || autoinstall=1

status="unknown"
route="none"

if [ -n "$runtime_python" ] && "$runtime_python" -c 'import open_terminal' >/dev/null 2>&1; then
    status="present"
    route="import"
elif [ "$autoinstall" != "1" ]; then
    status="missing"
    route="skipped (autoinstall off)"
else
    uv="$(command -v uv || true)"
    [ -z "$uv" ] && [ -x "$HOME/.local/bin/uv" ] && uv="$HOME/.local/bin/uv"

    if [ -n "$uv" ] && [ -n "$runtime_python" ]; then
        route="uv pip install --python $runtime_python"
        if "$uv" pip install --python "$runtime_python" open-terminal >>"$log_file" 2>&1; then
            status="installed"
        else
            status="failed"
        fi
    elif [ -n "$runtime_python" ]; then
        if [ "$as_root" -eq 1 ]; then
            route="pip install --break-system-packages (system)"
        else
            route="pip install --user --break-system-packages"
        fi
        pip_flags=()
        [ "$as_root" -eq 0 ] && pip_flags=(--user)
        if "$runtime_python" -m pip install "${pip_flags[@]}" --break-system-packages open-terminal >>"$log_file" 2>&1; then
            status="installed"
        else
            status="failed"
        fi
    else
        status="missing"
        route="no interpreter found"
    fi

    # Re-check with the interpreter the launcher uses. An install that lands somewhere the launcher
    # cannot import is not an install.
    if [ "$status" = "installed" ] && [ -n "$runtime_python" ]; then
        if "$runtime_python" -c 'import open_terminal' >/dev/null 2>&1; then
            status="present"
        else
            status="installed-but-not-importable"
        fi
    fi
fi

printf '%s\n' "$status" > "$state_dir/open-terminal-state" 2>/dev/null || true

cat <<JSON
{
  "open_terminal": {
    "status": $(printf '%s' "$status" | sed 's/.*/"&"/'),
    "route": $(printf '%s' "$route" | sed 's/.*/"&"/'),
    "runtime_python": $(printf '%s' "$runtime_python" | sed 's/.*/"&"/'),
    "checked_at": $(date +%s)
  }
}
JSON

# Non-fatal on purpose: %post runs this and must not fail an install because a package could not
# be fetched offline.
exit 0
