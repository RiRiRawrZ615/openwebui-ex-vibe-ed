#!/usr/bin/env bash
# Install-time detection: existing stack versus fresh machine. One workflow, two entry conditions,
# so this reports what is present rather than deciding what to install. Written as JSON to
# /var/lib/openwebvibes/install-report.json, and the Setup tab reads the same facts.

set -Euo pipefail

state_dir="${OWV_STATE_DIR:-/var/lib/openwebvibes}"
mkdir -p "$state_dir" 2>/dev/null || true

find_bin() {
    # First the explicit override, then PATH, then the usual local bins. No guessing, no network.
    name="$1"
    override="${2:-}"
    if [ -n "$override" ] && [ -x "$override" ]; then
        printf '%s' "$override"
        return 0
    fi
    if command -v "$name" >/dev/null 2>&1; then
        command -v "$name"
        return 0
    fi
    for dir in "$HOME/.local/bin" /usr/local/bin /usr/bin; do
        if [ -x "$dir/$name" ]; then
            printf '%s' "$dir/$name"
            return 0
        fi
    done
    printf 'not found'
}

llama="$(find_bin llama-server "${OWV_LLAMA_BIN:-}")"
strata="$(find_bin strata "${OWV_STRATA_BIN:-}")"
uv="$(find_bin uv "${OWV_UV_BIN:-}")"

model_root="${OWV_MODEL_ROOT:-$HOME/Documents/AI_Models}"
models=0
if [ -d "$model_root" ]; then
    models="$(find "$model_root" -maxdepth 2 -name '*.gguf' 2>/dev/null | wc -l | tr -d ' ')"
fi

fresh=1
if [ "$llama" != "not found" ] || [ "$strata" != "not found" ]; then
    fresh=0
fi

cat <<JSON
{
  "machine_state": $( [ "$fresh" -eq 1 ] && printf 'true' || printf 'false' ),
  "detected_at": $(date +%s),
  "llama": $(printf '%s' "$llama" | sed 's/.*/"&"/'),
  "strata": $(printf '%s' "$strata" | sed 's/.*/"&"/'),
  "uv": $(printf '%s' "$uv" | sed 's/.*/"&"/'),
  "model_root": $(printf '%s' "$model_root" | sed 's/.*/"&"/'),
  "models": ${models},
  "setup_workflow_should_run": $( [ "$fresh" -eq 1 ] && printf 'true' || printf 'false' )
}
JSON
