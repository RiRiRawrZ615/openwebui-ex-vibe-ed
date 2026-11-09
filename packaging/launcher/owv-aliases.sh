#!/usr/bin/env bash
# Shell aliases for a packaged Open Web Vibes install: start, stop, watch, status.
#
# This file is the single source of the alias definitions. The launcher sources it when it is used
# as a library, and /etc/profile.d/openwebvibes.sh sources it for interactive login shells, so a
# fresh install has the same set without anyone editing their shell rc by hand.
#
# Deliberately left out: any alias that could reach a second instance. A packaged install is one
# instance, so there is no dev scope here, and a kill that sweeps by process name or by shared
# port is how a running instance gets taken down by accident. Everything that stops something
# goes through owv-kill.sh, which checks ownership, refuses targets outside its scope, and refuses
# to kill the instance the calling shell is standing inside. --force is the escape hatch, and it
# is the only way to get past that guard.

OWV_LAUNCHER_DIR="${OWV_LAUNCHER_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

shopt -s expand_aliases

# start
alias launch-owv="bash $OWV_LAUNCHER_DIR/owv.sh"
alias owv="bash $OWV_LAUNCHER_DIR/owv.sh"
alias owv-tmux="tmux new-session -d -s owv \"bash $OWV_LAUNCHER_DIR/owv.sh\""

# stop
alias kill-owv="bash $OWV_LAUNCHER_DIR/owv-kill.sh"
alias kill-owv-dirty="bash $OWV_LAUNCHER_DIR/owv-kill.sh --force"
alias kill-owv-ui="bash $OWV_LAUNCHER_DIR/owv-kill.sh --ui-only"
alias restart-owv="bash $OWV_LAUNCHER_DIR/owv-kill.sh && bash $OWV_LAUNCHER_DIR/owv.sh"

# look
alias owv-status="bash $OWV_LAUNCHER_DIR/owv-status.sh"

# watch. log-oui renamed to log-owv, and the tails now point at the sinks this launcher actually
# writes. The stock alias named three files and this launcher only ever fed one of them, so two
# thirds of it showed stale content from days ago.
alias log-owv='state="${XDG_STATE_HOME:-$HOME/.local/state}/openwebvibes"; tail -F "$HOME/Documents/openwebui.log" "$HOME/Documents/engine.log" "$HOME/Documents/llama.log" "$state/openwebvibes.log" "$state/backend.log" 2>/dev/null'
alias tail-eng='state="${XDG_STATE_HOME:-$HOME/.local/state}/openwebvibes"; tail -F "$HOME/Documents/engine.log" "$HOME/Documents/llama.log" "$state/backend.log" "$state/open-terminal.log" 2>/dev/null'
alias tail-owv='state="${XDG_STATE_HOME:-$HOME/.local/state}/openwebvibes"; tail -F "$state/openwebvibes.log" "$state/backend.log" "$state/open-terminal.log" 2>/dev/null'
