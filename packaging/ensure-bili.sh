#!/usr/bin/env bash
# Ensure billion-context (bili) exists, because it is now the context system rather than an extra.
#
# Why this file exists: the launcher starts bili before the fork and points the fork's provider at
# it. On a machine without bili the launcher degrades safely (it warns and talks to the engine
# directly), but that means a fresh install silently loses context compression, which is the one
# feature this fork is for. Detect once at install time and fill the gap.
#
# Detection is the binary answering --version, not a file lookup, because npm's global prefix is
# per-user: a root install lands somewhere the amd user's PATH never sees, which is exactly how the
# two fleet boxes looked installed while the launcher could not find bili.
#
# Order: already present, then npm --prefix into the user's ~/.local (the prefix the launcher
# searches), then give up and report. One attempt each, no retries. A failure is reported rather
# than fatal, so an offline install still completes.
#
# Never touches the running stack.

set -Euo pipefail

state_dir="${OWV_STATE_DIR:-/var/lib/openwebvibes}"
autoinstall="${OWV_BILI_AUTOINSTALL:-1}"
log_file="${OWV_BILI_INSTALL_LOG:-$state_dir/bili-install.log}"
version_pin="${OWV_BILI_VERSION:-billion-context@0.1.213}"

mkdir -p "$state_dir" 2>/dev/null || true

# Running from %post means root. A root `npm install -g` lands in /usr/lib/node_modules, which is
# on the system PATH but not necessarily the one the launcher's shell sees, and it also means the
# package is unmanaged by rpm. So a root run installs nothing and only reports what is missing; the
# launcher's own first start (as the user) completes it with the right prefix. Same discipline as
# ensure-open-terminal.sh, for the same reason.
if [ "$(id -u)" -eq 0 ]; then
    as_root=1
else
    as_root=0
fi

report() {
    printf '{"component":"bili","status":"%s","detail":"%s","as_root":%s}\n' "$1" "$2" "$as_root"
    printf '%s %s\n' "$1" "$2" >> "$log_file" 2>/dev/null || true
}

bin="${OWV_BILI_BIN:-$HOME/.local/bin/bili}"

# 1. present and answering?
if [ -x "$bin" ] && "$bin" --version >/dev/null 2>&1; then
    report "present" "$("$bin" --version 2>/dev/null | head -1)"
    exit 0
fi
if command -v bili >/dev/null 2>&1 && bili --version >/dev/null 2>&1; then
    report "present" "$(bili --version 2>/dev/null | head -1) (on PATH)"
    exit 0
fi

# 2. node available? bili is an npm package and has no other install route.
if ! command -v npm >/dev/null 2>&1; then
    report "missing" "bili and npm both absent; install nodejs then re-run this script"
    exit 0
fi

if [ "$as_root" -eq 1 ]; then
    report "missing" "not installing as root; the launcher completes this at first start with the user prefix"
    exit 0
fi

if [ "$autoinstall" != "1" ]; then
    report "missing" "autoinstall disabled (OWV_BILI_AUTOINSTALL=0)"
    exit 0
fi

# 3. install into the prefix the launcher searches.
mkdir -p "$HOME/.local" 2>/dev/null || true
if npm install -g --prefix="$HOME/.local" "$version_pin" >>"$log_file" 2>&1; then
    if [ -x "$bin" ] && "$bin" --version >/dev/null 2>&1; then
        report "installed" "$("$bin" --version 2>/dev/null | head -1) into $HOME/.local"
        exit 0
    fi
    report "installed-but-unusable" "npm reported success but $bin does not answer --version"
    exit 0
fi

report "install-failed" "npm install $version_pin failed; see $log_file"
exit 0
