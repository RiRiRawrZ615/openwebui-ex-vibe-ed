#!/usr/bin/env bash
# Packaged entry point. Resolves its own location so the launcher works from
# /usr/lib/openwebvibes as well as from a git checkout.
#
# Config search order is /etc/openwebvibes/owv.conf then ~/.config/openwebvibes/owv.conf, with the
# user file winning when it exists. The launcher reads config as data, never evals it, so nothing
# here executes the contents of a config file.

set -Eeuo pipefail

SELF="$(readlink -f "$0")"
LAUNCHER_DIR="$(dirname "$SELF")"
APP_ROOT="$(dirname "$LAUNCHER_DIR")"

export OWV_FORK_DIR="${OWV_FORK_DIR:-$APP_ROOT}"
export OWV_PACKAGED=1

user_conf="${XDG_CONFIG_HOME:-$HOME/.config}/openwebvibes/owv.conf"
if [ -z "${OWV_CONFIG_FILE:-}" ]; then
    if [ -f "$user_conf" ]; then
        export OWV_CONFIG_FILE="$user_conf"
    elif [ -f /etc/openwebvibes/owv.conf ]; then
        export OWV_CONFIG_FILE=/etc/openwebvibes/owv.conf
    fi
fi

target="$LAUNCHER_DIR/owv.sh"
if [ ! -f "$target" ]; then
    echo "[ERROR] packaged launcher not found at $target" >&2
    exit 1
fi

exec bash "$target" "$@"
