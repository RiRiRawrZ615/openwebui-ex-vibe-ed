# Interactive shells only. Aliases do not expand in a non-interactive shell, so sourcing this from
# a script would define them and then fail to use them.
if [[ -n "$BASH_VERSION" && $- == *i* ]]; then
    OWV_LAUNCHER_DIR="${OWV_LAUNCHER_DIR:-/usr/lib/openwebvibes/launcher}"
    if [[ -f "$OWV_LAUNCHER_DIR/owv-aliases.sh" ]]; then
        source "$OWV_LAUNCHER_DIR/owv-aliases.sh"
    fi
fi
