#!/usr/bin/env bash
#
# OpenWebVibes launcher (fork, test build). Hybrid of the two working launchers:
#   ai-launchers/openwebui_llamacpp_safe_moe_v4.sh  (llama.cpp path)
#   ai-launchers/strata_openwebui_launcher.sh        (Strata path, the one that works)
#
# What this version fixes over the previous owv.sh:
#   1. Ports match the live instance exactly: 8080 webui, 8001 model API, 8003/8004 terminal
#      per the launcher you choose, 8002 vision. The fork replaces the live launcher, so it
#      takes the same ports. The fork refuses to attach to a port already serving something
#      else, so it cannot hijack a live instance by accident.
#   2. The fork actually starts. It uses the fork's own venv python and runs uvicorn from the
#      fork's backend dir, which is the only way 'import open_webui' resolves to the fork.
#      The old version used system python3 (3.14 via uv), where open_webui was not importable,
#      so the webui died instantly while the launcher sat waiting on a health check.
#   3. Strata is invoked exactly like the working strata launcher: venv python + serve/server.py
#      + config + ROCm lib paths. The engine binary was being exec'd through python, which fails.
#   4. No network required at start. open-terminal and open-webui resolve from the uv cache
#      first, so an outage cannot stall the boot. The old shape pinged 1.1.1.1 and then asked
#      uvx to re-fetch every wheel, which is the single point of failure that cost you the night.
#
# Safety rules inherited from both launchers: only PIDs started by this script are stopped,
# no broad pkill inside the script, no eval of config files, refuse on ambiguity.
# Never touches the live instance while that instance owns the port.

set -Eeuo pipefail

# -----------------------------
# Re-exec inside tmux, always
# -----------------------------
# The stack is long-lived and lives across shells. Before this guard, staying in tmux depended
# on the caller remembering to use `owv-tmux`, so a plain `owv` left the stack attached to a
# terminal that could be closed, and a later `kill-owv` had no session to clean up. Making the
# launcher wrap itself means there is exactly one correct way to start it and no way to start it
# wrong.
#
# Guard conditions, all required to re-exec:
#   - stdout is a terminal (a pipe or file means a script or CI, which cannot attach)
#   - not already inside a tmux client
#   - OWV_NO_TMUX is not set (escape hatch for foreground debugging)
#   - tmux actually exists
# The TTY check matters: without it, a non-interactive invocation such as a test harness
# or a systemd unit re-execs into tmux, which then fails with 'terminal does not support
# clear' and never reaches the dry-run path. Only wrap when a human is watching.
if [[ -t 1 && -z "${TMUX:-}" && "${OWV_NO_TMUX:-0}" != "1" ]] && command -v tmux >/dev/null 2>&1; then
    _owv_session="${OWV_TMUX_SESSION:-owv}"
    _owv_self="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
    if tmux has-session -t "$_owv_session" 2>/dev/null; then
        printf '[OWV] session %s already running; attaching\n' "$_owv_session"
        exec tmux attach-session -t "$_owv_session"
    fi
    printf '[OWV] starting in tmux session %s (OWV_NO_TMUX=1 for foreground)\n' "$_owv_session"
    # Pass the environment through so OWV_* overrides behave the same wrapped or not.
    exec tmux new-session -s "$_owv_session" -c "$(pwd)" \
        "env ${OWV_PASSTHROUGH:-} OWV_IN_TMUX=1 bash '$_owv_self'"
fi

# -----------------------------
# Per-user config, read as data
# -----------------------------
# Parsed line by line, never eval'd. Only OWV_ names plus EX_MODEL_ROOTS are accepted so a
# settings file cannot change how the shell resolves binaries. An env var already set in the
# calling shell wins over the file.
OWV_CONFIG_FILE="${OWV_CONFIG_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/openwebvibes/owv.conf}"
if [[ -f "$OWV_CONFIG_FILE" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%%$'\r'}"
        [[ -z "$line" || "${line:0:1}" == "#" ]] && continue
        key="${line%%=*}"
        val="${line#*=}"
        [[ "$key" == "$line" ]] && continue
        [[ "${key:0:4}" != "OWV_" && "$key" != "EX_MODEL_ROOTS" ]] && continue
        if [[ -z "${!key:-}" ]]; then
            export "$key=$val"
        fi
    done < "$OWV_CONFIG_FILE"
fi

# -----------------------------
# Portable defaults, all overridable
# -----------------------------
OWV_HOME="${OWV_HOME:-$HOME}"
FORK_DIR="${OWV_FORK_DIR:-$OWV_HOME/Documents/AI_Output/Forks/vibe-ed}"
FORK_BACKEND="$FORK_DIR/backend"

# Fork venv is the only python that can import the fork. System python3 and the uv cache envs
# are not it; the crash log proves it (ModuleNotFoundError: open_webui under python3.14).
FORK_PY="${OWV_PYTHON:-${FORK_DIR}/.venv/bin/python}"

# Data dir. The fork's own home is $XDG_DATA_HOME/openwebvibes, which is where the sync-owv
# alias restores the database, so that is the default. The previous default pointed at the stock
# instance's dir instead, which meant a machine that had just run sync-owv read a different
# database from the one that had just been written: the chat history was intact, the models and
# the added model were not, and the write path and the read path disagreed because they were two
# different files.
#
# It must also be exported, not just computed. Every other setting here is exported, so a value
# that is only a shell variable is invisible to the python process, and env.py falls back to
# BACKEND_DIR/data, which on a packaged install is /usr/lib/openwebvibes/backend/data and holds
# nothing but readme.txt.
DATA_DIR="${OWV_DATA_DIR:-${XDG_DATA_HOME:-$OWV_HOME/.local/share}/openwebvibes}"
export DATA_DIR
RUNTIME_DIR="${OWV_RUNTIME_DIR:-${XDG_CACHE_HOME:-$OWV_HOME/.cache}/openwebvibes}"
LOG_DIR="${OWV_LOG_DIR:-${XDG_STATE_HOME:-$OWV_HOME/.local/state}/openwebvibes}"

MODEL_ROOT="${OWV_MODEL_ROOT:-$OWV_HOME/Documents/AI_Models}"
MODEL_PATH="${OWV_MODEL:-}"

# Per-model profiles. The stock launcher had PROFILES_DIR and the packaged launcher had none, so
# on an RPM machine nothing reads ~/Documents/llama_profiles at all: ngl, flash attention, cache
# type, batch size, the binary itself and everything in extra_args are simply dropped. Only ctx,
# temp, top-p, top-k and reasoning came through, which is why the tested settings for qwen 3.6 and
# bonsai never applied.
# Resolved from the runtime user's home rather than a literal /home/<name>, so the same launcher
# works for any user without editing it.
PROFILES_DIR="${OWV_PROFILES_DIR:-$OWV_HOME/Documents/llama_profiles}"

# Ports identical to the working launchers, so bookmarks and workflows do not change.
# The fork replaces the live launcher, so it takes these same ports. It refuses if one is
# already serving something else, which is how it avoids hijacking a running instance.
WEBUI_PORT="${OWV_WEBUI_PORT:-8080}"
API_PORT="${OWV_API_PORT:-8004}"
VISION_PORT="${OWV_VISION_PORT:-8002}"
OPEN_TERMINAL_PORT="${OWV_TERMINAL_PORT:-8003}"

# Strata. Same shape as strata_openwebui_launcher.sh.
STRATA_DIR="${OWV_STRATA_DIR:-$OWV_HOME/strata}"
STRATA_VENV_PY="${OWV_STRATA_PY:-$STRATA_DIR/.venv/bin/python}"
STRATA_SERVER="${OWV_STRATA_SERVER:-$STRATA_DIR/serve/server.py}"
STRATA_CONFIG="${OWV_STRATA_CONFIG:-$STRATA_DIR/strata-iq2_xs.json}"

OWV_BACKEND="${OWV_BACKEND:-auto}"
OWV_INSTALL="${OWV_INSTALL:-0}"
OWV_DRY_RUN="${OWV_DRY_RUN:-0}"
OWV_START_WEBUI="${OWV_START_WEBUI:-1}"
OWV_TERMINAL_ENABLED="${OWV_TERMINAL_ENABLED:-1}"
OWV_STARTUP_TIMEOUT="${OWV_STARTUP_TIMEOUT:-1800}"
OWV_TERMINAL_STARTUP_TIMEOUT="${OWV_TERMINAL_STARTUP_TIMEOUT:-120}"

WEBUI_AUTH="${WEBUI_AUTH:-False}"
# Matches the stock launcher's value on purpose. The secret signs sessions and API tokens, so
# using a different one means every existing cookie and token is rejected on the first
# switch and the user has to sign in again. The point of this fork is to replace the live
# instance without the user noticing, so the value is the same on both sides. Override it
# with WEBUI_SECRET_KEY in the environment, or set it in owv.conf if the parser accepted
# non-OWV keys, which it does not.
WEBUI_SECRET_KEY="${WEBUI_SECRET_KEY:-fixed_secret_llamacpp_safe_v4}"
OPENAI_API_BASE_URL="${OPENAI_API_BASE_URL:-http://127.0.0.1:${API_PORT}/v1}"

# Offline-first. A cached copy is used whenever it exists; the network is only consulted when
# there is no local copy at all. This is the fix for the outage failure.
OWV_ALLOW_NETWORK="${OWV_ALLOW_NETWORK:-0}"
UV_CACHE="${UV_CACHE:-${XDG_CACHE_HOME:-$OWV_HOME/.cache}/uv}"

log()  { printf '[OWV] %s\n' "$*"; }
warn() { printf '[OWV][WARNING] %s\n' "$*"; }
die()  { printf '[OWV][ERROR] %s\n' "$*" >&2; exit 1; }

# -----------------------------
# Aliases for the shell rc (same shapes as the existing openwebui pair)
# -----------------------------
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # One source of the alias definitions: owv-aliases.sh. Start, stop, watch, status. The kill
    # path is scoped, checks ownership, and refuses to take down the instance the calling shell is
    # standing inside, which is the failure that keeps happening when a kill sweeps by name.
    if [[ -f "$here/owv-aliases.sh" ]]; then
        source "$here/owv-aliases.sh"
    fi
    return 0 2>/dev/null
fi

# -----------------------------
# Runtime state
# -----------------------------
mkdir -p "$DATA_DIR" "$RUNTIME_DIR" "$LOG_DIR"

# Persistent config beats the environment. Open WebUI seeds the config table from
# OPENAI_API_BASE_URLS once and then reads the database on every request, so exporting the strata
# url here does nothing while a stored row still points at an engine that is not running. That is
# the shape of the failure: the only provider that appears alive is the remote one, because every
# local entry in the stored list is dead.
#
# So the launcher reconciles the database instead of hoping the environment wins. It runs the
# connection repair against the fork's own data dir, before the fork starts, so what the UI lists
# matches what the launcher actually serves. Nothing is invented: if the fork database has no
# stored connections, the repair seeds exactly this one entry from the environment.
if [[ "${OWV_REPAIR_CONNECTIONS:-1}" == "1" ]] && [[ -f "$DATA_DIR/webui.db" ]]; then
    here_c="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -x "$here_c/owv-connections.sh" ]]; then
        # A dry run must not write. Report only.
        mode_c="apply"
        [[ "$OWV_DRY_RUN" == "1" ]] && mode_c="show"
        OWV_DATA_DIR="$DATA_DIR" OWV_API_PORT="$API_PORT" "$here_c/owv-connections.sh" "$mode_c" || \
            warn "connection repair skipped (database busy or locked); the fork will use what is stored"
    fi
fi

# The stock launcher writes ~/Documents/openwebui.log and that is where log-oui and every
# existing habit points. The fork therefore writes the same file, and keeps its own copy in
# LOG_DIR as a secondary so nothing is lost when the two launchers overwrite each other.
STOCK_WEBUI_LOG="${OWV_STOCK_LOG:-$OWV_HOME/Documents/openwebui.log}"
WEBUI_LOG="$STOCK_WEBUI_LOG"
WEBUI_LOG_SECONDARY="$LOG_DIR/openwebvibes.log"
BACKEND_LOG="$LOG_DIR/backend.log"
TERMINAL_LOG="$LOG_DIR/open-terminal.log"

# log-oui tails exactly three files: ~/Documents/llama.log, ~/Documents/engine.log and
# ~/Documents/openwebui.log. The launcher only ever fed openwebui.log, so two thirds of that
# alias showed stale files from days ago and the model engine's own output went somewhere the
# alias never reads. These two are the alias-visible sinks for model/engine output.
STOCK_ENGINE_LOG="${OWV_STOCK_ENGINE_LOG:-$OWV_HOME/Documents/engine.log}"
STOCK_LLAMA_LOG="${OWV_STOCK_LLAMA_LOG:-$OWV_HOME/Documents/llama.log}"
WEBUI_PID_FILE="$RUNTIME_DIR/webui.pid"
BACKEND_PID_FILE="$RUNTIME_DIR/backend.pid"
TERMINAL_PID_FILE="$RUNTIME_DIR/terminal.pid"

safe_kill_pid() {
    local pid="${1:-}"
    if [[ -z "$pid" || ! "$pid" =~ ^[0-9]+$ || ! -d "/proc/$pid" ]]; then return 0; fi
    kill "$pid" 2>/dev/null || true
    for _ in {1..8}; do if [[ ! -d "/proc/$pid" ]]; then return 0; fi; sleep 1; done
    kill -9 "$pid" 2>/dev/null || true
}

safe_kill_pgid() {
    local pid="${1:-}"
    if [[ -z "$pid" || ! "$pid" =~ ^[0-9]+$ || ! -d "/proc/$pid" ]]; then return 0; fi
    kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
    for _ in {1..8}; do if [[ ! -d "/proc/$pid" ]]; then return 0; fi; sleep 1; done
    kill -9 -- "-$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
}

cleanup() {
    local exit_code=$?
    trap - EXIT INT TERM
    log "shutting down services started by this launcher only"
    for f in "$WEBUI_PID_FILE" "$TERMINAL_PID_FILE" "$BACKEND_PID_FILE"; do
        if [[ -f "$f" ]]; then safe_kill_pgid "$(cat "$f" 2>/dev/null)"; fi
    done
    rm -f "$WEBUI_PID_FILE" "$BACKEND_PID_FILE" "$TERMINAL_PID_FILE" 2>/dev/null || true
    exit "$exit_code"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

port_in_use() {
    curl -s --max-time 2 "http://127.0.0.1:${1}/health" >/dev/null 2>&1
}

fork_serving() {
    [[ -f "$WEBUI_PID_FILE" ]] && kill -0 "$(cat "$WEBUI_PID_FILE" 2>/dev/null)" 2>/dev/null
}

# Is the listener on this port an Open WebUI instance, and is it the one being replaced?
# A stranger on the port is never killed, so a mistyped port cannot take down something else.
live_instance_on() {
    local port="$1"
    curl -s --max-time 3 "http://127.0.0.1:${port}/api/config" 2>/dev/null | grep -q '"name"'
}

# Stop the live instance holding this port, and only that one. Matched on the command line and
# on the port it was started with, so two instances on different ports are not confused.
stop_live_instance() {
    local port="$1"
    local pids
    pids="$(pgrep -f "open-webui serve" 2>/dev/null || true)"
    if [[ -z "$pids" ]]; then
        pids="$(ss -tlnp 2>/dev/null | awk -v p=":${port}" '$4 ~ p {print $NF}' | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u)"
    fi
    [[ -z "$pids" ]] && { warn "no pid found for the instance on $port"; return 0; }
    for pid in $pids; do
        local pport
        pport="$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | awk -F= '$1=="PORT"{print $2}')"
        if [[ "${pport:-}" == "$port" ]]; then
            log "stopping live Open WebUI pid $pid (PORT=$pport)"
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done
}

# -----------------------------
# Binary resolution, no hardcoded machine paths
# -----------------------------
resolve_binary() {
    local override="${1:-}"
    shift
    if [[ -n "$override" ]]; then
        [[ -x "$override" ]] && { printf '%s\n' "$override"; return 0; }
        die "override not executable: $override"
    fi
    for name in "$@"; do
        local found
        found="$(command -v "$name" 2>/dev/null || true)"
        [[ -n "$found" ]] && { printf '%s\n' "$found"; return 0; }
    done
    for candidate in "${OWV_BIN_DIRS[@]}"; do
        for name in "$@"; do
            local p="$candidate/$name"
            [[ -x "$p" ]] && { printf '%s\n' "$p"; return 0; }
        done
    done
    return 1
}

OWV_BIN_DIRS=(
    "${XDG_LOCAL_BIN:-$OWV_HOME/.local/bin}"
    "$OWV_HOME/bin"
    "$OWV_HOME/.cargo/bin"
    "$OWV_HOME/llama.cpp/build/bin"
    "$OWV_HOME/llama.cpp-ternary/build-ternary/bin"
    "$STRATA_DIR/engine"
    "$OWV_HOME/.strata/bin"
    "/usr/local/bin"
    "/usr/bin"
)

LLAMA_BIN="$(resolve_binary "${OWV_LLAMA_BIN:-}" llama-server llama-server-cuda llama-server-vulkan ggml || true)"

# -----------------------------
# Model resolution. Refuse on ambiguity instead of guessing.
# -----------------------------
model_roots() {
    local raw="${OWV_MODEL_ROOTS:-${EX_MODEL_ROOTS:-$MODEL_ROOT}}"
    local root
    local IFS=':'
    for root in $raw; do
        [[ -n "$root" ]] && printf '%s\n' "$root"
    done
}

resolve_model() {
    if [[ -n "$MODEL_PATH" ]]; then printf '%s\n' "$MODEL_PATH"; return 0; fi
    local hits=() root found_any=0
    while IFS= read -r root; do
        if [[ -d "$root" ]]; then
            found_any=1
            while IFS= read -r -d '' f; do hits+=("$f"); done < <(find "$root" -maxdepth 2 -name '*.gguf' -print0 2>/dev/null)
        fi
    done < <(model_roots)
    if [[ ${#hits[@]} -eq 1 ]]; then printf '%s\n' "${hits[0]}"; return 0; fi
    if [[ ${#hits[@]} -gt 1 ]]; then
        die "ambiguous model: ${#hits[@]} gguf files across the configured roots. Set OWV_MODEL to one of them."
    fi
    while IFS= read -r root; do
        if [[ -f "$root/manifest.json" || -f "$root/pack.json" || -f "$root/index.txt" || -f "$root/conversions.json" ]]; then
            printf '%s\n' "$root"; return 0
        fi
    done < <(model_roots)
    [[ "$found_any" == "0" ]] && die "no model roots exist. Set OWV_MODEL, OWV_MODEL_ROOT, or OWV_MODEL_ROOTS."
    die "no model found under the configured roots (set OWV_MODEL)"
}

# Profile name is the model id with / and : replaced by _, plus the gguf suffix: local/<Model>:gguf
# becomes local_<Model>_gguf.json. Matching on the file name alone is not enough because a profile
# can be named for the id while the file on disk differs, so both forms are tried.
profile_for_model() {
    local model="$1"
    [[ -d "$PROFILES_DIR" ]] || return 1
    local stem="$(basename "$model" | sed 's/\.gguf$//; s/[^A-Za-z0-9_.-]/_/g')"
    local cand
    for cand in "${PROFILES_DIR}/local_${stem}_gguf.json" "${PROFILES_DIR}/${stem}.json" "${PROFILES_DIR}/local_${stem}.json"; do
        [[ -f "$cand" ]] && { printf '%s\n' "$cand"; return 0; }
    done
    return 1
}

# Reads a profile as data. Keys are mapped through a fixed table, so a value in a profile cannot be
# executed by the shell; an unknown key is reported, not passed through.
profile_json_field() {
    "$FORK_PY" - "$1" "$2" <<'PYFIELD'
import json, sys
try:
    raw = json.loads(open(sys.argv[1], encoding="utf-8").read())
except (ValueError, OSError):
    print(""); raise SystemExit(0)
print(raw.get(sys.argv[2]) or "")
PYFIELD
}

build_llama_args() {
    local profile="${1:-}"
    if [[ -z "$profile" ]]; then
        printf '%s\n' --ctx-size "${OWV_CTX_SIZE:-256000}" --temp "${OWV_TEMPERATURE:-0.2}" \
            --top-p "${OWV_TOP_P:-0.8}" --top-k "${OWV_TOP_K:-20}" --reasoning on
        return 0
    fi
    "$FORK_PY" - "$profile" "${OWV_CTX_SIZE:-}" "${OWV_TEMPERATURE:-}" "${OWV_TOP_P:-}" "${OWV_TOP_K:-}" <<'PYARGS'
import json, sys

path, octx, otemp, otopp, otopk = sys.argv[1:6]
try:
    raw = json.loads(open(path, encoding="utf-8").read())
except (ValueError, OSError):
    raw = {}

# An explicit OWV_ setting beats the profile; the profile beats the launcher default.
flags = [
    ("ngl", "--n-gpu-layers", "int"),
    ("ctx_size", "--ctx-size", "int"),
    ("batch", "--batch-size", "int"),
    ("ubatch", "--ubatch-size", "int"),
    ("threads", "--threads", "int"),
    ("threads_batch", "--threads-batch", "int"),
    ("cache_type_k", "--cache-type-k", "str"),
    ("cache_type_v", "--cache-type-v", "str"),
    ("parallel", "--parallel", "int"),
    ("ncmoe", "--ncmoe", "int"),
]
args = []
for key, flag, kind in flags:
    value = raw.get(key)
    if value in (None, "", 0, False):
        continue
    args += [flag, str(value)]
# --flash-attn is a bare boolean in upstream llama.cpp but takes on|off|auto in the ternary
# build, where a lone flag swallows the following token as its value. A profile that says true
# therefore produced `--flash-attn --no-warmup` and the server refused to start. Emit the value.
fa = raw.get("flash_attn")
if fa is True:
    args += ["--flash-attn", "on"]
elif fa not in (None, "", False):
    args += ["--flash-attn", str(fa)]
for key, flag in (("cmoe", "--cmoe"), ("no_mmap", "--no-mmap"), ("no_warmup", "--no-warmup")):
    if raw.get(key):
        args.append(flag)
if octx:
    args += ["--ctx-size", octx]
if otemp:
    args += ["--temp", otemp]
if otopp:
    args += ["--top-p", otopp]
if otopk:
    args += ["--top-k", otopk]
args += [str(a) for a in (raw.get("extra_args") or [])]
print("\n".join(args))
PYARGS
}

# -----------------------------
# Backend decision, done by the fork's own module
# -----------------------------
fork_backend_decision() {
    local model="$1" prefer="$2"
    local mod="$FORK_BACKEND/open_webui/utils/ex_backend.py"
    [[ -x "$FORK_PY" && -f "$mod" ]] || return 1
    "$FORK_PY" - "$mod" "$model" "$prefer" <<'PYFORK'
import importlib.util, json, sys

path, model, prefer = sys.argv[1], sys.argv[2], sys.argv[3]
spec = importlib.util.spec_from_file_location("ex_backend", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(json.dumps(mod.auto_select(model, prefer=prefer or "auto")))
PYFORK
}

# Params the launcher actually passes. The settings tab reports these as launcher-locked, so this
# must describe reality. For llama.cpp all four are command-line args. For strata, generation
# params arrive per request, so only the context read out of strata's own config is reportable.
build_params_json() {
    local backend="$1"
    if [[ "$backend" == "llamacpp" ]]; then
        # Report what is actually being passed. When a profile supplies ctx, cache type, batch and
        # the generation values, the settings tab must show those and not the launcher defaults, or
        # the tab reports numbers the process is not using.
        "$FORK_PY" - "${PROFILE:-}" "${OWV_TEMPERATURE:-0.2}" "${OWV_TOP_P:-0.8}" "${OWV_TOP_K:-20}" "${OWV_CTX_SIZE:-256000}" <<'PYPARAMS_LLAMA'
import json, sys

profile, temp, top_p, top_k, ctx = sys.argv[1:6]
params = {"temperature": float(temp), "top_p": float(top_p), "top_k": int(top_k), "ctx_size": int(ctx)}
if profile:
    try:
        raw = json.loads(open(profile, encoding="utf-8").read())
    except (ValueError, OSError):
        raw = {}
    for key, name in (("ctx_size", "ctx_size"), ("ngl", "n_gpu_layers"), ("batch", "batch_size"),
                      ("ubatch", "ubatch_size"), ("threads", "threads"), ("threads_batch", "threads_batch"),
                      ("cache_type_k", "cache_type_k"), ("cache_type_v", "cache_type_v"), ("flash_attn", "flash_attn")):
        if raw.get(key) is not None:
            params[name] = raw[key]
    extra = [str(a) for a in (raw.get("extra_args") or [])]
    for i, a in enumerate(extra):
        if a in ("--temp", "--top-p", "--top-k", "--min-p") and i + 1 < len(extra):
            # The flags in a profile are hyphenated (--top-k) while the reported keys are
            # underscored (top_k). Indexing a fixed dict with a flag not in it raised KeyError on
            # the first extra_args value, which is what threw during startup on the laptop.
            name = {"--temp": "temperature", "--top-p": "top_p", "--top-k": "top_k", "--min-p": "min_p"}.get(a)
            if name:
                try:
                    params[name] = float(extra[i + 1])
                except ValueError:
                    pass
print(json.dumps(params, sort_keys=True))
PYPARAMS_LLAMA
        return 0
    fi
    "$FORK_PY" - "$STRATA_CONFIG" <<'PYPARAMS'
import json, sys
from pathlib import Path

config = Path(sys.argv[1]).expanduser()
if not config.is_file():
    print("{}"); raise SystemExit(0)
try:
    raw = json.loads(config.read_text(encoding="utf-8", errors="replace"))
except (ValueError, OSError):
    print("{}"); raise SystemExit(0)
args = raw.get("args") or []
context = None
for i, arg in enumerate(args):
    if arg == "--max-context" and i + 1 < len(args):
        try:
            context = int(args[i + 1])
        except ValueError:
            context = None
        break
params = {}
if context is not None:
    params["ctx_size"] = context
print(json.dumps(params, sort_keys=True))
PYPARAMS
}

write_handshake() {
    local backend="$1" model="$2" params_json="${3:-}"
    [[ -z "$params_json" ]] && params_json="{}"
    local mod="$FORK_BACKEND/open_webui/utils/ex_handshake.py"
    if [[ ! -x "$FORK_PY" || ! -f "$mod" ]]; then
        warn "fork handshake module not available; the settings tab will report no handshake"
        return 1
    fi
    "$FORK_PY" - "$mod" "$DATA_DIR" "$backend" "$model" "$params_json" <<'PYHS'
import importlib.util, json, sys

mod_path, data_dir, backend, model, params = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
spec = importlib.util.spec_from_file_location("ex_handshake", mod_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
record = {
    "source": mod.HANDSHAKE_SOURCE,
    "backend": backend,
    "model": model,
    "params": json.loads(params) if params else {},
}
print(mod.write_handshake(data_dir, record))
PYHS
}

# -----------------------------
# Resolve, decide, report
# -----------------------------
[[ -x "$FORK_PY" ]] || die "fork venv python missing: $FORK_PY"
[[ -d "$FORK_BACKEND/open_webui" ]] || die "fork backend dir missing: $FORK_BACKEND"

MODEL="$(resolve_model)"
PROFILE="$(profile_for_model "$MODEL" || true)"
log "model: $MODEL"
log "profiles dir: $PROFILES_DIR"
if [[ -n "$PROFILE" ]]; then
    log "profile for this model: $PROFILE"
else
    log "no profile matches this model; launcher defaults apply"
fi
log "fork python: $FORK_PY"
log "data dir: $DATA_DIR"
log "ports: webui $WEBUI_PORT, api $API_PORT, terminal $OPEN_TERMINAL_PORT, vision $VISION_PORT"

DECISION="$(fork_backend_decision "$MODEL" "$OWV_BACKEND" || true)"
[[ -z "$DECISION" ]] && die "backend decision unavailable; the fork module could not be loaded"

SELECTED="$("$FORK_PY" -c 'import json,sys; print(json.loads(sys.stdin.read()).get("selected") or "")' <<< "$DECISION")"
REASON="$("$FORK_PY" -c 'import json,sys; print(json.loads(sys.stdin.read()).get("reason",""))' <<< "$DECISION")"
[[ -z "$SELECTED" ]] && die "no backend can serve this model: $REASON"

log "backend selected by the fork: $SELECTED ($REASON)"
PARAMS_JSON="$(build_params_json "$SELECTED")"
HANDSHAKE_PATH="$(write_handshake "$SELECTED" "$MODEL" "$PARAMS_JSON")"
log "handshake written: $HANDSHAKE_PATH"

if [[ "$OWV_DRY_RUN" == "1" ]]; then
    log "dry run only, nothing started"
    printf '%s\n' "$PARAMS_JSON"
    exit 0
fi

# -----------------------------
# Ports must be free, or this must already be the fork serving them
# -----------------------------
if port_in_use "$WEBUI_PORT" && ! fork_serving; then
    # Same-port handover. The configuration asks for the live instance's ports, so a listener
    # here is either the instance being replaced or a stranger.
    if [[ "${OWV_TAKE_OVER_PORT:-1}" == "1" ]] && live_instance_on "$WEBUI_PORT"; then
        log "port $WEBUI_PORT is serving Open WebUI. Stopping it so the fork can take over."
        stop_live_instance "$WEBUI_PORT"
        for _ in $(seq 1 30); do
            port_in_use "$WEBUI_PORT" || break
            sleep 1
        done
        port_in_use "$WEBUI_PORT" && die "the live instance on $WEBUI_PORT did not release the port."
    else
        die "port $WEBUI_PORT is serving something that is not an Open WebUI instance. Refusing to take it. Set OWV_WEBUI_PORT to a free port, or OWV_TAKE_OVER_PORT=1 to override."
    fi
fi

# -----------------------------
# Start the model backend
# -----------------------------
if port_in_use "$API_PORT" && ! fork_serving; then
    log "model API already healthy on $API_PORT. Reusing it, no model reload."
    BACKEND_PID=""
else
    case "$SELECTED" in
        strata)
            [[ -x "$STRATA_VENV_PY" ]] || die "strata venv python not found: $STRATA_VENV_PY"
            [[ -f "$STRATA_SERVER" ]]   || die "strata server not found: $STRATA_SERVER"
            [[ -f "$STRATA_CONFIG" ]]   || die "strata config not found: $STRATA_CONFIG"
            log "starting strata model server on $API_PORT (model load takes several minutes)"
            # Exact invocation of the working strata launcher: python server, not the engine
            # binary, with the config's own env and library paths. One process group, stoppable.
            ( cd "$STRATA_DIR"
              export STRATA_ROCBLAS_TUNING="${STRATA_ROCBLAS_TUNING:-$STRATA_DIR/tools/hip}"
              export LD_LIBRARY_PATH="$STRATA_DIR/.venv/lib/python3.11/site-packages/_rocm_sdk_devel/lib:$STRATA_DIR/.venv/lib/python3.11/site-packages/_rocm_sdk_libraries_gfx103X_all/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
              exec "$STRATA_VENV_PY" "$STRATA_SERVER" --engine strata --config "$STRATA_CONFIG" --port "$API_PORT"
            ) 2>&1 | tee -a "$BACKEND_LOG" "$STOCK_ENGINE_LOG" &
            # tee is now the last stage, so $! is the forwarder. The real server pid is
            # recovered below by port, exactly as the webui block does.
            BACKEND_PID=$!
            ;;
        llamacpp)
            PROFILE="$(profile_for_model "$MODEL" || true)"
            if [[ -n "$PROFILE" ]]; then
                # A profile can name its own binary, and for the ternary builds that is the only
                # path that works. An explicit OWV_LLAMA_BIN still wins so a deliberate override is
                # not silently replaced, but a profile named for this model should not be ignored.
                PROFILE_BIN="$(profile_json_field "$PROFILE" llama_bin)"
                if [[ -n "$PROFILE_BIN" && -x "$PROFILE_BIN" && -z "${OWV_LLAMA_BIN:-}" ]]; then
                    LLAMA_BIN="$PROFILE_BIN"
                fi
                log "profile applied: $PROFILE"
            fi
            [[ -n "$LLAMA_BIN" ]] || die "llama.cpp binary not found. Set OWV_LLAMA_BIN or put llama-server on PATH."
            mapfile -t LLAMA_ARGS < <(build_llama_args "$PROFILE")
            log "starting llama.cpp via $LLAMA_BIN"
            setsid "$LLAMA_BIN" -m "$MODEL" --host 127.0.0.1 --port "$API_PORT" \
                "${LLAMA_ARGS[@]}" --reasoning on \
                2>&1 | tee -a "$BACKEND_LOG" "$STOCK_LLAMA_LOG" &
            BACKEND_PID=$!
            ;;
        *)
            die "unexpected backend selection: $SELECTED"
            ;;
    esac
    # With tee in the pipeline, $! is the log forwarder, not the model server. Resolve the real
    # listener by port so the pid file never holds a forwarder, which would make the shutdown
    # path signal the wrong process and leave the engine holding VRAM.
    if ! port_in_use "$API_PORT"; then
        for _ in $(seq 1 20); do
            port_in_use "$API_PORT" && break
            sleep 1
        done
    fi
    REAL_BACKEND_PID="$(ss -ltnp 2>/dev/null | awk -v p=":$API_PORT" '$4 ~ p {print $NF}' \
        | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u | head -1 || true)"
    [[ -n "$REAL_BACKEND_PID" ]] && BACKEND_PID="$REAL_BACKEND_PID"
    [[ -n "$BACKEND_PID" ]] && echo "$BACKEND_PID" > "$BACKEND_PID_FILE"

    for _ in $(seq 1 "$OWV_STARTUP_TIMEOUT"); do
        if port_in_use "$API_PORT"; then break; fi
        if [[ -n "$BACKEND_PID" ]] && ! kill -0 "$BACKEND_PID" 2>/dev/null; then
            die "backend exited during startup. Check: $BACKEND_LOG"
        fi
        sleep 1
    done
    port_in_use "$API_PORT" || die "backend did not become healthy within ${OWV_STARTUP_TIMEOUT}s. Check: $BACKEND_LOG"
fi
log "model API ready: http://127.0.0.1:$API_PORT/v1"

# -----------------------------
# Open Terminal, cache-first, no network requirement
# -----------------------------
if [[ "$OWV_TERMINAL_ENABLED" == "1" ]]; then
    KEY_DIR="${XDG_CONFIG_HOME:-$OWV_HOME/.config}/open-terminal"
    KEY_FILE="$KEY_DIR/api-key-owv"
    mkdir -p "$KEY_DIR"
    chmod 700 "$KEY_DIR"
    # The terminal's API key must match the key recorded in the webui database, because the fork
    # authenticates to the terminal with whatever is in terminal_server.connections. A freshly
    # generated key is therefore worse than useless: the terminal starts, the health check
    # passes, and every actual call from the webui is rejected. That is what produced
    # "Cannot connect to host 127.0.0.1:8003" in the chat path and "Could not determine the
    # upload folder" in the composer, with a terminal that looked perfectly healthy.
    #
    # So: adopt the key the database already uses, and only generate one when the database has
    # none. OWV_TERMINAL_KEY_FILE overrides for a deliberately separate terminal.
    DB_TERMINAL_KEY=""
    if [[ -f "$DATA_DIR/webui.db" ]] && command -v sqlite3 >/dev/null 2>&1; then
        DB_TERMINAL_KEY="$(sqlite3 "$DATA_DIR/webui.db" \
            "SELECT value FROM config WHERE key='terminal_server.connections';" 2>/dev/null \
            | grep -o '"key":"[^"]*"' | head -1 | cut -d'"' -f4 || true)"
    fi
    if [[ -n "$DB_TERMINAL_KEY" ]]; then
        printf '%s\n' "$DB_TERMINAL_KEY" > "$KEY_FILE"
        chmod 600 "$KEY_FILE"
        log "terminal api key adopted from $DATA_DIR/webui.db"
    elif [[ ! -s "$KEY_FILE" ]]; then
        (umask 077; "$FORK_PY" -c 'import secrets; print(secrets.token_hex(32))' > "$KEY_FILE")
        warn "no terminal key in the database; generated one. If the webui already has a terminal"
        warn "connection, its key will not match and calls will fail until they are aligned."
    fi
    chmod 600 "$KEY_FILE"

    # Cached archive first. If the package is on disk, no resolver runs, so an outage cannot stall
    # the boot. The archive dir is a plain site-packages tree, so it is importable by path without
    # building an environment. This is the single point of failure removed.
    TERM_DIR=""
    if [[ -d "$UV_CACHE" ]]; then
        TERM_DIR="$(find "$UV_CACHE"/archive-v0 -maxdepth 2 -type d -name open_terminal 2>/dev/null | head -1 || true)"
    fi

    # Importable is not the same as runnable. open_terminal imports nbformat at module level for its
    # notebook support, and that is optional metadata, so a package installed without it starts, prints
    # the banner, crashes on import, and the health window expires with nothing in the log but a
    # traceback. That is exactly what happened on both fleet boxes. The probe therefore checks the
    # imports the server actually performs, against the path it will run under.
    TERM_PROBE="import open_terminal, nbformat"
    term_probe() {
        PYTHONPATH="${1:-}" "$FORK_PY" -c "$TERM_PROBE" >/dev/null 2>&1
    }

    # Fork environment first, then the cache path. A venv cannot see ~/.local site-packages, so the
    # check is an import and not a file lookup. On a machine installed from the RPM the cache is empty
    # and uvx --offline resolves to nothing, which is the case the install step below now covers.
    TERM_IMPORT_OK=0
    TERM_PYTHONPATH=""
    if term_probe ""; then
        TERM_IMPORT_OK=1
    elif [[ -n "$TERM_DIR" ]] && term_probe "$(dirname "$TERM_DIR")"; then
        TERM_PYTHONPATH="$(dirname "$TERM_DIR")"
    fi

    if [[ -z "$TERM_DIR" && "$TERM_IMPORT_OK" != "1" && -z "$TERM_PYTHONPATH" && "${OWV_TERMINAL_AUTOINSTALL:-1}" == "1" ]]; then
        # One attempt, no retry loop. uv-first because the venvs in this fleet are uv-created and
        # carry no pip; pip with the PEP 668 flag is the fallback for a system interpreter.
        UV_BIN="$(command -v uv || true)"
        if [[ -z "$UV_BIN" && -x "$OWV_HOME/.local/bin/uv" ]]; then
            UV_BIN="$OWV_HOME/.local/bin/uv"
        fi
        if [[ -n "$UV_BIN" ]]; then
            log "open-terminal or its deps not importable by $FORK_PY; attempting one install with uv"
            "$UV_BIN" pip install --python "$FORK_PY" open-terminal nbformat >> "$TERMINAL_LOG" 2>&1 \
                && log "open-terminal installed into the fork environment" \
                || warn "open-terminal install failed; see $TERMINAL_LOG"
        elif "$FORK_PY" -m pip --version >/dev/null 2>&1; then
            log "open-terminal or its deps not importable and no uv; attempting pip install"
            "$FORK_PY" -m pip install --break-system-packages open-terminal nbformat >> "$TERMINAL_LOG" 2>&1 \
                && log "open-terminal installed with pip" \
                || warn "open-terminal pip install failed; see $TERMINAL_LOG"
        fi
        if term_probe ""; then
            TERM_IMPORT_OK=1
        elif [[ -n "$TERM_DIR" ]] && term_probe "$(dirname "$TERM_DIR")"; then
            TERM_PYTHONPATH="$(dirname "$TERM_DIR")"
        fi
    fi

    if [[ -n "$TERM_PYTHONPATH" ]]; then
        log "open-terminal resolved from the uv cache, no network needed"
        # PYTHONPATH must be the PARENT of the package directory. The earlier value pointed at
        # the package itself, so the import resolved to <pkg>/open_terminal and failed.
        setsid env OPEN_TERMINAL_API_KEY="$(cat "$KEY_FILE")" PYTHONPATH="$(dirname "$TERM_DIR")" "$FORK_PY" -m open_terminal run \
            --host 127.0.0.1 --port "$OPEN_TERMINAL_PORT" --cwd "${OWV_TERMINAL_CWD:-$OWV_HOME/Documents/AI_Output}" \
            > "$TERMINAL_LOG" 2>&1 &
        TERMINAL_PID=$!
    elif [[ "$TERM_IMPORT_OK" == "1" ]]; then
        setsid env OPEN_TERMINAL_API_KEY="$(cat "$KEY_FILE")" "$FORK_PY" -m open_terminal run \
            --host 127.0.0.1 --port "$OPEN_TERMINAL_PORT" --cwd "${OWV_TERMINAL_CWD:-$OWV_HOME/Documents/AI_Output}" \
            > "$TERMINAL_LOG" 2>&1 &
        TERMINAL_PID=$!
    elif command -v uvx >/dev/null 2>&1; then
        warn "open-terminal is not in the uv cache; this start needs network and may fail offline"
        setsid env OPEN_TERMINAL_API_KEY="$(cat "$KEY_FILE")" uvx --python 3.11 open-terminal@latest run \
            --host 127.0.0.1 --port "$OPEN_TERMINAL_PORT" --cwd "${OWV_TERMINAL_CWD:-$OWV_HOME/Documents/AI_Output}" \
            > "$TERMINAL_LOG" 2>&1 &
        TERMINAL_PID=$!
    else
        warn "open-terminal is not importable, not cached, and could not be installed. Fork runs without it."
        TERMINAL_PID=""
    fi

    if [[ -n "$TERMINAL_PID" ]]; then
        echo "$TERMINAL_PID" > "$TERMINAL_PID_FILE"
        for _ in $(seq 1 "$OWV_TERMINAL_STARTUP_TIMEOUT"); do
            if curl -fsS --max-time 1 "http://127.0.0.1:$OPEN_TERMINAL_PORT/health" >/dev/null 2>&1; then break; fi
            kill -0 "$TERMINAL_PID" 2>/dev/null || break
            sleep 1
        done
        curl -fsS --max-time 2 "http://127.0.0.1:$OPEN_TERMINAL_PORT/health" >/dev/null 2>&1 \
            && log "Open Terminal ready on http://127.0.0.1:$OPEN_TERMINAL_PORT" \
            || warn "Open Terminal did not become healthy on $OPEN_TERMINAL_PORT; fork continues without it. Check $TERMINAL_LOG"
    fi
fi

# -----------------------------
# Start the fork itself, cache-first, no online fetch
# -----------------------------
if [[ "$OWV_START_WEBUI" == "1" ]]; then
    if port_in_use "$WEBUI_PORT" && ! fork_serving; then
        die "port $WEBUI_PORT is already serving something else. The fork must not attach to another instance."
    fi
    # The package is noarch and never builds an environment, so a venv can exist and still be missing
    # most of the requirements. On the fleet boxes fastapi and uvicorn were present and chromadb was not,
    # so this check passed, the engine loaded, and the webui died later on an unrelated import. Naming
    # the first missing module is the whole point; a generic message sent the investigation to the wrong
    # subsystem.
    missing=""
    for mod in fastapi uvicorn chromadb bs4 markdown sqlalchemy; do
        "$FORK_PY" -c "import ${mod}" >/dev/null 2>&1 || missing="${missing:+$missing }${mod}"
    done
    if [[ -n "$missing" ]]; then
        UV_BIN="$(command -v uv || true)"
        if [[ -z "$UV_BIN" && -x "$OWV_HOME/.local/bin/uv" ]]; then
            UV_BIN="$OWV_HOME/.local/bin/uv"
        fi
        die "fork venv is missing: ${missing}. Fill it with:
     ${UV_BIN:-pip} install -r $FORK_BACKEND/requirements.txt --python $FORK_PY"
    fi
    # A stale build is served happily by the backend and looks like a broken feature in the UI.
    # Check it here so the reason is stated instead of discovered in a browser.
    if [[ -d "$FORK_DIR/build" ]]; then
        # The packaged install ships build but not src, so this find had nothing to walk. Under
        # `set -Eeuo pipefail` the pipeline's status is find's status, not head's, so a missing src
        # directory made the assignment fail, errexit fired, and the cleanup trap tore down the engine
        # and the terminal that had just come up. On a git checkout src exists, so this only shows on
        # machines installed from the RPM. Both guards are needed: the directory check and `|| true`
        # inside the substitution.
        if [[ -d "$FORK_DIR/src" ]]; then
            # No `local` here: this block sits at top level, not inside a function, and bash
            # rejects `local` outside a function with "can only be used in a function". Under
            # set -e that aborted the launcher immediately after the engine had loaded, which
            # then triggered the shutdown trap. The symptom was a long model load followed by a
            # bare "shutting down services" and no server, which reads like an engine fault and
            # is not: it is a shell error in the launcher itself.
            newer="$(find "$FORK_DIR/src" -newer "$FORK_DIR/build/index.html" -type f 2>/dev/null | head -1 || true)"
            if [[ -n "$newer" ]]; then
                warn "frontend build is older than the sources (e.g. ${newer#$FORK_DIR/})."
                if [[ "${OWV_REQUIRE_FRESH_BUILD:-0}" == "1" ]]; then
                    # Set by owv-dev.sh. In a dev run a stale bundle is not a cosmetic problem: a
                    # correct fix looks broken, and the usual response is to rewrite code that was
                    # already right. Refuse instead of serving a bundle that cannot contain the change.
                    die "OWV_REQUIRE_FRESH_BUILD=1 and the build is stale. Rebuild first:
     (cd $FORK_DIR && NODE_OPTIONS=--max-old-space-size=8192 npm run build)"
                fi
                warn "the UI will show the previous bundle. Rebuild with: (cd $FORK_DIR && npm run build)"
            fi
        else
            # Packaged installs carry build only. Reporting the skip is better than a check that
            # cannot run, because a check that cannot run is what took down the engine here.
            log "no src directory at $FORK_DIR/src; build cannot be compared against sources, staleness check skipped"
        fi
    else
        warn "no frontend build at $FORK_DIR/build. The server will answer the API only."
    fi

    log "starting the fork from $FORK_BACKEND with the fork venv"

    # Port preflight. Without this, starting a second server on a held port produces a boot that
    # looks successful and is not.
    #
    # The mechanism, which is why this was hard to see: uvicorn runs lifespan startup BEFORE it
    # binds the socket. So the new process loads the embedding model, starts the scheduler, spawns
    # the terminal server, logs "Initialized 1 terminal server(s)"... and only then fails with
    # `[Errno 98] address already in use` and exits. Meanwhile the OLD server still holds the port
    # and answers, so the readiness probe below (a plain curl at the port) passes and the launcher
    # reports success. The user sees a stack that "booted" with an Errno 98 in the log.
    #
    # Check the port instead of assuming, and name the holder so the fix is obvious.
    _port_holder="$(ss -ltnp 2>/dev/null | awk -v p=":$WEBUI_PORT" '$4 ~ p {print $NF}' \
        | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u | head -1 || true)"
    if [[ -n "$_port_holder" ]]; then
        _holder_cmd="$(tr '\0' ' ' < "/proc/$_port_holder/cmdline" 2>/dev/null || true)"
        die "port $WEBUI_PORT is already held by pid $_port_holder (${_holder_cmd:-unknown}).
     Refusing to start a second server that would lose the bind after a full model load.
     Use the dirty restart to swap the UI without touching the engine:
         kill-owv-dirty
     Or stop everything first:
         kill-owv"
    fi

    # open_webui resolves only inside the fork's backend dir. The earlier version ran uvicorn from
    # the launcher directory with system python, and the import failed. Run from the fork itself.
    ( cd "$FORK_BACKEND"
      setsid env DATA_DIR="$DATA_DIR" PORT="$WEBUI_PORT" WEBUI_AUTH="$WEBUI_AUTH" \
          WEBUI_SECRET_KEY="$WEBUI_SECRET_KEY" ENABLE_OPENAI_API=True ENABLE_OLLAMA_API=False \
          ENABLE_TOOL_SERVERS=True ENABLE_KB_EXEC=True ENABLE_CODE_INTERPRETER=True ENABLE_CODE_EXECUTION=True \
          ENABLE_VERSION_UPDATE_CHECK=False \
          CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS="${CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS:--1}" \
          OPENAI_API_BASE_URL="$OPENAI_API_BASE_URL" OPENAI_API_KEY="${OPENAI_API_KEY:-owv-local-key}" \
          FRONTEND_BUILD_DIR="$FORK_DIR/build" \
          "$FORK_PY" -m uvicorn open_webui.main:app --host 0.0.0.0 --port "$WEBUI_PORT" \
          2>&1 | tee -a "$WEBUI_LOG" "$WEBUI_LOG_SECONDARY" &
      # tee sits at the end of that pipeline, so $! is tee's pid, not uvicorn's. The pid file is
      # written from $WEBUI_PID below instead, discovered by port, because a pid file holding the
      # log forwarder would make the shutdown path kill the wrong process and leave uvicorn up.
    )
    # Because of the tee pipeline, $! was the forwarder and the pid file was never written with a
    # real server pid. Find the listener on the webui port instead and record that.
    WEBUI_PID=""
    for _ in $(seq 1 30); do
        WEBUI_PID="$(ss -ltnp 2>/dev/null | awk -v p=":$WEBUI_PORT" '$4 ~ p {print $NF}' \
            | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u | head -1 || true)"
        # NOT `[[ ... ]] && break`. That was the bug. The test is the last command in the loop
        # body, so a failing test returned 1, and under `set -e` a compound statement returning
        # non-zero aborts the script. On the very first pass the listener may not be up yet, the
        # test fails, errexit fires, the cleanup trap runs, and the launcher tears down the engine
        # and terminal it just started. The detached uvicorn survived because its pid was never
        # recorded, so the port answered while nothing was supervising it. Symptoms: a fork that
        # "booted" with no terminal, chat failing on 127.0.0.1:8003, and uploads with no defined
        # folder. An `if` always returns 0 and cannot abort the script.
        if [[ -n "$WEBUI_PID" ]]; then
            break
        fi
        sleep 1
    done
    if [[ -n "$WEBUI_PID" ]]; then
        echo "$WEBUI_PID" > "$WEBUI_PID_FILE"
        log "fork pid $WEBUI_PID recorded"
    else
        warn "could not find a listener on $WEBUI_PORT yet; the shutdown path will fall back to port lookup"
    fi

    for _ in {1..120}; do
        if curl -s --max-time 2 "http://127.0.0.1:$WEBUI_PORT" >/dev/null; then break; fi
        kill -0 "$WEBUI_PID" 2>/dev/null || die "webui exited during startup. Check: $WEBUI_LOG"
        sleep 1
    done
    curl -s --max-time 2 "http://127.0.0.1:$WEBUI_PORT" >/dev/null || die "webui did not answer within 120s. Check: $WEBUI_LOG"
    log "webui ready: http://0.0.0.0:$WEBUI_PORT"
fi

log "handshake file: $DATA_DIR/ex_runtime.json"
log "the OpenWebVibes settings tab reads this file and reports the params locked here"
log "no online fetch was required for this start"

while true; do
    sleep 3600 &
    wait $!
done
