#!/bin/bash
# What is actually running right now, in one screen.
#
# Written because a healthy-looking 8080 hid a dead terminal twice. Checking a port is open
# proves nothing; each line here checks the thing that has to be true for chat and uploads
# to work, and says plainly when it is not.

# Same trap as the backup script: a configured default is a guess. On this machine the
# documented default pointed at a stale sibling database, so status reported counts from a file
# the running server was not using. Resolve from the live process instead.
_PORT="${OWV_WEBUI_PORT:-8080}"
_LIVE_PID="$(ss -ltnp 2>/dev/null | awk -v p=":$_PORT" '$4 ~ p {print $NF}' \
    | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u | head -1 || true)"
_LIVE_DD=""
[[ -n "$_LIVE_PID" ]] && _LIVE_DD="$(tr '\0' '\n' < "/proc/$_LIVE_PID/environ" 2>/dev/null | sed -n 's/^DATA_DIR=//p' | head -1)"
if [[ -n "${OWV_DATA_DIR:-}" ]]; then
    DB="$OWV_DATA_DIR/webui.db"
elif [[ -n "$_LIVE_DD" && -f "$_LIVE_DD/webui.db" ]]; then
    DB="$_LIVE_DD/webui.db"
else
    DB="/var/lib/openwebvibes/webui.db"
    [[ -f "$DB" ]] || DB="$HOME/.local/share/openwebvibes/webui.db"
fi
WEBUI_PORT="${OWV_WEBUI_PORT:-8080}"
API_PORT="${OWV_API_PORT:-8004}"
TERM_PORT="${OWV_TERMINAL_PORT:-8003}"
ok()   { printf '  [ ok ] %s\n' "$*"; }
bad()  { printf '  [FAIL] %s\n' "$*"; }
warn() { printf '  [warn] %s\n' "$*"; }

echo "OpenWebVibes status"
echo

echo "webui on $WEBUI_PORT"
if PID=$(ss -ltnp 2>/dev/null | awk -v p=":$WEBUI_PORT" '$4 ~ p {print $NF}' \
        | grep -o 'pid=[0-9]*' | cut -d= -f2 | head -1) && [[ -n "${PID:-}" ]]; then
    CMD=$(tr '\0' ' ' < "/proc/$PID/cmdline" 2>/dev/null)
    ok "listening, pid $PID"
    if [[ "$CMD" == *"uv/cache"* || "$CMD" == *"uv/archive"* ]]; then
        warn "this is the STOCK uv-cached build, not the fork"
    elif [[ "$CMD" == *"vibe-ed"* || "$CMD" == *"open_webui.main"* ]]; then
        ok "the fork is serving"
    fi
    NAME=$(curl -s -m 5 "http://127.0.0.1:$WEBUI_PORT/api/config" \
        | python3 -c "import json,sys;print(json.load(sys.stdin).get('name','?'))" 2>/dev/null)
    [[ -n "$NAME" ]] && ok "api answers: $NAME" || bad "port open but /api/config does not answer"
else
    bad "nothing listening on $WEBUI_PORT"
fi
echo

echo "model engine on $API_PORT"
if curl -s -m 5 "http://127.0.0.1:$API_PORT/v1/models" >/dev/null 2>&1; then
    N=$(curl -s -m 5 "http://127.0.0.1:$API_PORT/v1/models" \
        | python3 -c "import json,sys;print(len(json.load(sys.stdin).get('data',[])))" 2>/dev/null)
    ok "models served: $N"
else
    bad "no model API. Chat will fail."
fi
echo

echo "terminal on $TERM_PORT (chat tools and file upload need this)"
if curl -s -m 5 "http://127.0.0.1:$TERM_PORT/health" >/dev/null 2>&1; then
    ok "health endpoint answers"
    # Health alone proves nothing. Ask the terminal to do the exact thing the webui asks it to
    # do, using the exact key the database holds. Comparing key FILES was wrong: a separate
    # launcher legitimately keeps its key somewhere else while still accepting the same value.
    DBKEY=$(sqlite3 "$DB" "SELECT value FROM config WHERE key='terminal_server.connections';" 2>/dev/null \
        | grep -o '"key":"[^"]*"' | head -1 | cut -d'"' -f4)
    if [[ -z "$DBKEY" ]]; then
        warn "the database has no terminal key, so I cannot test auth"
    else
        CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 5 -H "Authorization: Bearer $DBKEY" \
            "http://127.0.0.1:$TERM_PORT/files/list?path=${OWV_TERMINAL_CWD:-$HOME/Documents/AI_Output}" 2>/dev/null)
        case "$CODE" in
            200) ok "terminal accepts the database key and can list files" ;;
            401|403) bad "terminal REJECTS the database key (http $CODE)."
                     warn "chat tools and uploads will fail while health still looks fine."
                     warn "start via owv.sh, which adopts the database key." ;;
            *)   warn "terminal auth probe returned http ${CODE:-none}" ;;
        esac
    fi
else
    bad "terminal is down. Uploads will say 'Could not determine the upload folder'."
fi
echo

echo "database"
if [[ -f "$DB" ]]; then
    ok "$DB"
    printf '         chats %s, messages %s\n' \
        "$(sqlite3 "$DB" 'SELECT COUNT(*) FROM chat;' 2>/dev/null)" \
        "$(sqlite3 "$DB" 'SELECT COUNT(*) FROM chat_message;' 2>/dev/null)"
else
    bad "no database at $DB"
fi
echo

echo "launcher"
# Scan /proc directly. pgrep -f matches the tool's own command line, which both misses the
# launcher when it was started as ./owv.sh and can report a false positive on this script.
LPID=""
for d in /proc/[0-9]*; do
    p=${d#/proc/}
    [[ "$p" == "$$" ]] && continue
    c=$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null)
    case "$c" in
        *owv-status.sh*) continue ;;
        *owv.sh*) LPID=$p ;;
    esac
done
if [[ -n "$LPID" ]]; then
    ok "owv.sh supervising, pid $LPID"
else
    warn "owv.sh is not running, so nothing restarts the services above"
fi