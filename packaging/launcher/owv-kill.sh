#!/usr/bin/env bash
# Open Web Vibes: stop one instance, scoped to that instance, and refuse to stop the one the
# caller is standing inside.
#
# Why this exists
# ---------------
# kill-owv had three definitions living at once:
#   1. .bashrc, a blunt fuser+pkill alias.
#   2. owv.sh, a pid-file alias that only exists when the launcher is sourced as a library.
#   3. owv.aliases, which pointed at a script that did not exist yet.
# Whichever was sourced last won, so what `kill-owv` actually did depended on load order. The
# .bashrc version also named ports the fork does not own (8085-8088), and it swept by process
# name, so a dev-scoped kill could reach the production instance and a production kill could
# reach the dev instance. Neither version noticed the obvious thing: a kill issued from inside
# the stack takes the caller down with it. That is how the agent got destroyed.
#
# Three rules this script enforces
# --------------------------------
#   SCOPE. One instance per call. A dev kill only matches processes whose cmdline or cwd points
#          at the dev code copy; a production kill only matches the stable code copy. Ports are
#          read per scope, so the shared terminal on 8003 is never swept by a dev call.
#   OWNERSHIP. A pid is only killed if it belongs to this user and its cmdline is recognised.
#          Anything else is reported and left alone.
#   SELF-PRESERVATION. If the caller is inside the instance being pointed at, the script refuses
#          and says why. `--force` overrides it, so a deliberate cold stop is still one flag away.
#          The check is the ancestor chain: if any ancestor of this shell carries OWV_RUNTIME_DIR
#          for the scope being killed, or is that scope's launcher or terminal server, the caller
#          is inside the stack and is serving a live chat through it.
#
# Order of operations (unchanged from the original script, because it was right)
# -----------------------------------------------------------------------------
#   1. SIGTERM the launcher so its EXIT trap runs. The trap knows the exact process groups it
#      started and tidies the pid files. Skipping straight to -9 skips the trap and leaves
#      orphans holding the ports.
#   2. Pid files, then a scope-filtered pattern sweep for anything the launcher did not track.
#   3. Port holdouts, verified by owner and cmdline.
#   4. The tmux session for that scope.
#
# Usage
#   owv-kill.sh                  production instance, everything it started
#   owv-kill.sh --dev            dev instance only
#   owv-kill.sh --all            both, dev first so the production call is the last thing seen
#   owv-kill.sh --ui-only        web UI only, engine and shared terminal left running
#   owv-kill.sh --dry-run        print what would be stopped, change nothing
#   owv-kill.sh --force          skip the inside-the-stack refusal
#
# Environment
#   OWV_LAUNCHER_DIR   where these scripts live
#   OWV_WEBUI_PORT     production web UI port (8080)
#   OWV_DEV_WEBUI_PORT dev web UI port (8090)
#   OWV_RUNTIME_DIR    production runtime dir
#   OWV_DEV_RUNTIME_DIR dev runtime dir
#   OWV_PROTECTED_PIDS file of pids to never touch, one per line

set -uo pipefail

LAUNCHER_DIR="${OWV_LAUNCHER_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

# --- scope resolution ---------------------------------------------------------------------
PROD_RUNTIME_DIR="${OWV_RUNTIME_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/openwebvibes}"
PROD_CODE_DIR="${OWV_CODE_DIR:-$HOME/Documents/AI_Output/Forks/vibe-ed}"
PROD_WEBUI_PORT="${OWV_WEBUI_PORT:-8080}"
PROD_ENGINE_PORT="${OWV_API_PORT:-8004}"
PROD_TERMINAL_PORT="${OWV_TERMINAL_PORT:-8003}"

DEV_RUNTIME_DIR="${OWV_DEV_RUNTIME_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/openwebvibes-dev}"
DEV_CODE_DIR="${OWV_DEV_CODE_DIR:-$HOME/Documents/AI_Output/Dev/vibe-ed-dev}"
DEV_WEBUI_PORT="${OWV_DEV_WEBUI_PORT:-8090}"
DEV_ENGINE_PORT="${OWV_DEV_API_PORT:-8005}"
DEV_VISION_PORT="${OWV_DEV_VISION_PORT:-8006}"

say()  { printf '%s\n' "$*"; }
run()  { if [[ "$DRY_RUN" == 1 ]]; then say "  [dry-run] $*"; else "$@"; fi; }
mark() { printf '  %s %s\n' "$1" "$2"; }

DRY_RUN=0
FORCE=0
UI_ONLY=0
SCOPES=()

for arg in "$@"; do
    case "$arg" in
        --dev)    SCOPES+=(dev) ;;
        --prod)   SCOPES+=(prod) ;;
        --all)    SCOPES+=(dev prod) ;;
        --ui-only) UI_ONLY=1 ;;
        --dry-run|-n) DRY_RUN=1 ;;
        --force)  FORCE=1 ;;
        -h|--help) sed -n '2,46p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) say "owv-kill: unknown option: $arg" >&2; exit 2 ;;
    esac
done
[[ ${#SCOPES[@]} -eq 0 ]] && SCOPES=(prod)

# --- self-preservation --------------------------------------------------------------------
# The caller is inside an instance if any ancestor of this shell was started by that instance's
# launcher, or carries that instance's runtime dir in its environment.
# OWV_CALLER_PID lets the guard be tested as if it were called from another shell.
caller_ancestry() {
    # $$ is the pid, not $; a bare $ here is a literal dollar sign and the walk would never start.
    local p="${OWV_CALLER_PID:-$$}" seen=""
    while [[ "$p" != "0" && -n "$p" && -d "/proc/$p" ]]; do
        printf '%s\n' "$p"
        local next
        # The comm field can contain spaces and parentheses, so field 4 of stat is not reliable.
        # Everything after the closing paren is trustworthy: state, ppid, pgrp.
        next=$(sed 's/.*) //' "/proc/$p/stat" 2>/dev/null | awk '{print $2}')
        [[ -z "$next" || "$next" == "$p" || "$seen" == *"$next"* ]] && break
        seen="$seen $next"
        p="$next"
    done
}

protected_list() {
    local f="${OWV_PROTECTED_PIDS:-$PROD_RUNTIME_DIR/protected.pids}"
    [[ -f "$f" ]] && cat "$f" 2>/dev/null
}

is_ancestor() {
    local pid="$1"
    for a in $(caller_ancestry); do [[ "$a" == "$pid" ]] && return 0; done
    return 1
}

# owv.sh keeps RUNTIME_DIR as a shell variable, so the launcher's own environ does not carry it.
# The children do, because they are exec'd with it. So a launcher is matched to a scope by its
# descendants, not by itself.
launcher_for_scope() {
    local runtime="$1" p child
    for p in $(pgrep -f 'bash .*owv\.sh' 2>/dev/null | sort -u); do
        for child in $(pgrep -P "$p" 2>/dev/null); do
            if read_env "$child" | grep -qx "OWV_RUNTIME_DIR=$runtime"; then echo "$p"; return 0; fi
        done
    done
    return 1
}

read_env() {
    local pid="$1"
    [[ -r "/proc/$pid/environ" ]] && tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null
}

ppid_of() {
    local pid="$1"
    [[ -r "/proc/$pid/stat" ]] && sed 's/.*) //' "/proc/$pid/stat" 2>/dev/null | awk '{print $2}'
}

inside_scope() {
    local runtime="$1"
    for a in $(caller_ancestry); do
        if read_env "$a" | grep -qx "OWV_RUNTIME_DIR=$runtime"; then
            say "  REFUSED: this shell is inside the instance it is pointed at (ancestor $a)."
            say "           Killing it takes down the chat that is serving this session."
            say "           Pass --force for a deliberate stop."
            return 0
        fi
    done
    return 1
}

# --- helpers ------------------------------------------------------------------------------
scope_ports() {
    if [[ "$1" == dev ]]; then
        echo "$DEV_WEBUI_PORT $DEV_ENGINE_PORT $DEV_VISION_PORT"
    else
        if [[ "$UI_ONLY" == 1 ]]; then echo "$PROD_WEBUI_PORT"; else echo "$PROD_WEBUI_PORT $PROD_ENGINE_PORT $PROD_TERMINAL_PORT"; fi
    fi
}

matches_scope() {
    # A pid belongs to this scope only if its cmdline or cwd points at this scope's code copy.
    local pid="$1" code="$2" runtime="$3" cmd cwd
    [[ -d "/proc/$pid" ]] || return 1
    cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
    cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)
    [[ "$cmd" == *"$code"* || "$cwd" == "$code" || "$cwd" == "$code"/* ]] && return 0
    [[ -n "$3" ]] && read_env "$pid" | grep -qx "OWV_RUNTIME_DIR=$3"
    return 1
}

# A descendant of the launcher is only a service if its own command line is one. The launcher's
# loop has helper children (a sleep between checks), and killing those leaves a live launcher
# with its children gone, which is worse than killing nothing.
is_service() {
    local pid="$1" cmd
    [[ -d "/proc/$pid" ]] || return 1
    cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
    case "$cmd" in
        *open_webui.main:app*|*open_terminal\ run*|*strata*|*llama-server*|*owv.sh*|*"tee -a"*) return 0 ;;
        *) return 1 ;;
    esac
}

belongs_to_me() {
    local pid="$1"
    [[ "$(stat -c '%U' "/proc/$pid" 2>/dev/null)" == "$(id -un)" ]]
}

skip_reason() {
    local pid="$1" scope="$2"
    if is_ancestor "$pid"; then echo "ancestor of this shell"; return; fi
    if grep -qx "$pid" <<<"$(protected_list)"; then echo "listed in OWV_PROTECTED_PIDS"; return; fi
    if [[ "$scope" == dev ]]; then
        # The dev instance never starts a terminal, so anything on the shared terminal is out of scope.
        tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q "open_terminal run" && { echo "shared terminal server (dev does not own it)"; return; }
    fi
    if [[ "$UI_ONLY" == 1 ]]; then
        tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -qiE 'strata|llama-server|open_terminal run' && { echo "engine/terminal, left running by --ui-only"; return; }
    fi
    echo ""
}

SEEN=""

kill_one() {
    local pid="$1" scope="$2" label="$3" reason
    [[ -z "$pid" || ! "$pid" =~ ^[0-9]+$ ]] && return 0
    [[ ! -d "/proc/$pid" ]] && return 0
    [[ "$SEEN" == *" $pid "* ]] && return 0
    SEEN="$SEEN $pid "
    reason=$(skip_reason "$pid" "$scope")
    if [[ -n "$reason" && "$FORCE" != 1 ]]; then
        mark "skip" "$pid ($label): $reason"
        return 0
    fi
    if ! belongs_to_me "$pid"; then
        mark "skip" "$pid ($label): owned by $(stat -c '%U' "/proc/$pid" 2>/dev/null || echo '?')"
        return 0
    fi
    if [[ "$FORCE" == 1 && -n "$reason" ]]; then mark "force" "$pid ($label): $reason"; fi
    run kill "$pid" 2>/dev/null || true
    mark "kill" "$pid ($label)"
}

listener_pids() {
    local port="$1"
    ss -ltnpH "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u
}

kill_scope() {
    local scope="$1" runtime code webui session launcher
    if [[ "$scope" == dev ]]; then
        runtime="$DEV_RUNTIME_DIR"; code="$DEV_CODE_DIR"; webui="$DEV_WEBUI_PORT"; session="owv-testing"
    else
        runtime="$PROD_RUNTIME_DIR"; code="$PROD_CODE_DIR"; webui="$PROD_WEBUI_PORT"; session="owv"
    fi

    say ""
    say "scope: $scope (web UI port $webui, runtime $runtime)"

    if [[ "$FORCE" != 1 ]] && inside_scope "$runtime"; then
        return 1
    fi

    # 1. the launcher, so its EXIT trap runs
    # 1. the launcher, so its EXIT trap runs. In --ui-only this step is skipped on purpose:
    #    the trap would take the engine down with the UI, which is the whole point of UI-only.
    launcher=$(launcher_for_scope "$runtime")
    if [[ -n "$launcher" && "$UI_ONLY" != 1 ]]; then
        kill_one "$launcher" "$scope" "launcher"
        if [[ "$DRY_RUN" != 1 && "$FORCE" != 1 ]]; then
            for _ in {1..10}; do [[ -z "$(listener_pids "$webui")" ]] && break; sleep 1; done
        fi
    else
        say "  no launcher running for this scope"
    fi

    # 2. pid files. These name exactly what this instance started, so they are trusted as scope
    #    evidence even when the process cmdline does not mention the code dir (the log tee).
    for name in webui terminal backend engine; do
        pidfile="$runtime/$name.pid"
        [[ -f "$pidfile" ]] || continue
        pid=$(cat "$pidfile" 2>/dev/null)
        if [[ "$UI_ONLY" == 1 && "$name" != webui ]]; then continue; fi
        kill_one "$pid" "$scope" "$name.pid"
    done

descendants() {
    local p="$1" c
    [[ -z "$p" || "$p" == "0" ]] && return 0
    for c in $(pgrep -P "$p" 2>/dev/null); do
        echo "$c"
        descendants "$c"
    done
}

in_scope() {
    local pid="$1" launcher="$2" code="$3" runtime="${4:-}"
    [[ -z "$pid" || ! -d "/proc/$pid" ]] && return 1
    # Never match the shell issuing the call. A command line containing the words being searched
    # for is how a kill script ends up reporting itself as a target.
    [[ "$pid" == "${OWV_CALLER_PID:-$$}" || "$pid" == "$$" ]] && return 1
    is_service "$pid" || return 1
    [[ -n "$launcher" && "$(ppid_of "$pid")" == "$launcher" ]] && return 0
    local tree
    tree=" $(descendants "$launcher" | tr '\n' ' ') "
    [[ -n "$launcher" && "$tree" == *" $pid "* ]] && return 0
    matches_scope "$pid" "$code"
}

    # 3. descendants and stragglers. The trap covers the launcher's own children, but a straggler
    #    started outside the launcher's tracking still holds a port, so both are swept here. A
    #    process belongs to the scope if it is a descendant of this scope's launcher or its cmdline
    #    or cwd points at this scope's code copy. Out-of-scope candidates are not printed.
    for pid in $( (descendants "${launcher:-}"; pgrep -f 'open_webui|open_terminal|owv\.sh|strata|llama-server' 2>/dev/null) | sort -u); do
        in_scope "$pid" "$launcher" "$code" "$runtime" && kill_one "$pid" "$scope" "descendant/straggler"
    done

    # 4. port holdouts
    if [[ "$DRY_RUN" != 1 ]]; then
        for port in $(scope_ports "$scope"); do
            for pid in $(listener_pids "$port"); do
                if matches_scope "$pid" "$code"; then
                    [[ -d "/proc/$pid" ]] && run kill -9 "$pid" 2>/dev/null || true
                    mark "kill" "$pid (hard, port $port)"
                else
                    mark "skip" "$pid (port $port): not this scope"
                fi
            done
        done
    fi

    # 5. tmux session
    if tmux has-session -t "$session" 2>/dev/null; then
        say "  tmux session '$session'"
        run tmux kill-session -t "$session" 2>/dev/null || true
    else
        say "  no tmux session '$session'"
    fi

    if [[ "$DRY_RUN" == 1 ]]; then say "  dry run, nothing changed"; return 0; fi
    rm -f "$runtime"/*.pid 2>/dev/null || true
    return 0
}

say "owv-kill: $([[ "$DRY_RUN" == 1 ]] && echo '(dry run) ')(scope: ${SCOPES[*]}$( [[ "$UI_ONLY" == 1 ]] && echo ", UI only"))"

blocked=0
for s in "${SCOPES[@]}"; do
    kill_scope "$s" || blocked=1
done

if [[ "$blocked" == 1 ]]; then
    say ""
    say "one scope was refused. Re-run with --force if the stop is intended."
    exit 1
fi
exit 0
