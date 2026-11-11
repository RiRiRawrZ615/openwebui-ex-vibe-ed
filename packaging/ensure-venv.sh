#!/usr/bin/env bash
# Ensure the fork venv exists and can import the fork, for a packaged install.
#
# A noarch package ships code, never an environment. That is the gap a release has to close: a
# fresh machine installs fine, then the launcher either refuses with "fork venv is missing" or the
# webui dies on the first import. On this fleet every machine already had a venv from previous work,
# so packaged installs looked healthy while a clean install could not actually run. Anyone who
# downloads the RPM gets that broken first-run unless %post fills the environment.
#
# The venv goes at the package root, because that is what the packaged launcher resolves:
# owv-packaged.sh sets OWV_FORK_DIR to its own app root, and the launcher then uses
# $FORK_DIR/.venv, which on a packaged install is /usr/lib/openwebvibes/.venv. Filling a
# different directory is the mistake that made past installs look installed while the launcher could
# not import the fork.
#
# Detection is an import against the interpreter that will run it, not a file lookup, because a venv
# does not see the user site-packages, and fastapi alone is not enough: chromadb was the module that
# was missing on past machines while fastapi and uvicorn were present.
#
# One attempt, bounded, non-fatal. An offline machine must still install cleanly. Skip entirely with
# OWV_RPM_BOOTSTRAP_VENV=0.

set -Euo pipefail

state_dir="${OWV_STATE_DIR:-/var/lib/openwebvibes}"
autoinstall="${OWV_RPM_BOOTSTRAP_VENV:-1}"
log_file="${OWV_VENV_INSTALL_LOG:-$state_dir/venv-install.log}"
LIMIT=""
[ "${OWV_VENV_EXACT:-0}" = "1" ] && LIMIT=1

mkdir -p "$state_dir" 2>/dev/null || true

if [ "$autoinstall" != "1" ]; then
    printf '{"status":"skipped","route":"OWV_RPM_BOOTSTRAP_VENV=0"}\n'
    exit 0
fi

pkg_root="/usr/lib/openwebvibes"
venv_dir="$pkg_root/.venv"
req="$pkg_root/backend/requirements.txt"

# Probe modules the launcher actually needs, against the interpreter that will run the fork.
probe_modules="fastapi uvicorn chromadb bs4 markdown sqlalchemy"

probe() {
    local py="$1" miss=""
    for mod in $probe_modules; do
        "$py" -c "import $mod" >/dev/null 2>&1 || miss="${miss:+$miss }$mod"
    done
    printf '%s' "$miss"
}

route="none"

if [ -x "$venv_dir/bin/python" ]; then
    miss="$(probe "$venv_dir/bin/python")"
    if [ -z "$miss" ]; then
        printf '{"status":"present","route":"venv import ok","python":"%s"}\n' "$venv_dir/bin/python"
        exit 0
    fi
    route="venv exists, missing: $miss"
fi

uv="$(command -v uv || true)"
[ -z "$uv" ] && [ -x "${HOME:-/root}/.local/bin/uv" ] && uv="${HOME:-/root}/.local/bin/uv"
# %post runs as root, so a user-installed uv is often invisible. python3 -m venv is the fallback that
# needs nothing beyond the interpreter, and a venv made this way carries its own pip.
plain_pip="$(command -v pip3 || true)"

if [ ! -f "$req" ]; then
    printf '{"status":"failed","route":"requirements.txt not in package tree","venv":"%s"}\n' "$venv_dir"
    exit 0
fi

if [ -n "$uv" ]; then
    if [ ! -x "$venv_dir/bin/python" ]; then
        # A venv cannot be created as an unprivileged user into /usr/lib. This path only runs from
        # %post (root) or an explicit sudo invocation, which is what a fresh install needs.
        if [ "$(id -u)" -ne 0 ]; then
            printf '{"status":"deferred","route":"venv create needs root; launcher completes it at first start","venv":"%s"}\n' "$venv_dir"
            exit 0
        fi
        "$uv" venv "$venv_dir" >> "$log_file" 2>&1 || true
    fi
    "$uv" pip install --python "$venv_dir/bin/python" ${LIMIT:+--exact} -r "$req" >> "$log_file" 2>&1 \
        && route="uv install" \
        || route="uv install failed (see $log_file)"
elif [ -x "$venv_dir/bin/python" ] && "$venv_dir/bin/python" -m pip --version >/dev/null 2>&1; then
    "$venv_dir/bin/python" -m pip install --prefer-binary -r "$req" >> "$log_file" 2>&1 \
        && route="pip install into venv" \
        || route="pip install failed (see $log_file)"
else
    if [ -n "$plain_pip" ] && [ -x "$(command -v python3 || true)" ]; then
        if [ ! -x "$venv_dir/bin/python" ]; then
            python3 -m venv "$venv_dir" >> "$log_file" 2>&1 || true
        fi
        if [ -x "$venv_dir/bin/python" ]; then
            # --exact is a uv flag, not a pip flag. Only pass it where uv is the installer.
            "$venv_dir/bin/python" -m pip install --prefer-binary -r "$req" >> "$log_file" 2>&1 \
                && route="python3 -m venv + venv pip" \
                || route="venv pip install failed (see $log_file)"
        else
            printf '{"status":"failed","route":"venv create failed","venv":"%s"}\n' "$venv_dir"
            exit 0
        fi
    else
        printf '{"status":"failed","route":"neither uv nor pip available","venv":"%s"}\n' "$venv_dir"
        exit 0
    fi
fi

miss="$(probe "$venv_dir/bin/python")"
printf '{"status":"%s","route":"%s","python":"%s","venv":"%s","still_missing":"%s"}\n' \
    "$([ -z "$miss" ] && echo ok || echo partial)" "$route" "$venv_dir/bin/python" "$venv_dir" "$miss"
exit 0
