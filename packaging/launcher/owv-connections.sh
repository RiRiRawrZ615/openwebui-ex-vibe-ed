#!/usr/bin/env bash
# Open Web Vibes: wire strata into the fork's connection list, in the fork's own database.
#
# Why this exists
# ---------------
# "Only tokenharbor connects" is not a network failure, it is persistent config beating the
# environment. Open WebUI seeds its config table from OPENAI_API_BASE_URLS once and then reads
# the DATABASE on every request (models/config.py, routers/openai.py get_openai_runtime_config).
# A launcher exporting OPENAI_API_BASE_URL=http://127.0.0.1:8004/v1 therefore changes nothing
# while a stored row still points at an engine that is not running. The remote provider is the
# only one that survives because every local entry in the stored list is dead. That is the exact
# symptom, and it is why strata "never starts with the app": the app never asks 8004 anything.
#
# Two data dirs are two instances. The fork reads ${XDG_DATA_HOME}/openwebvibes; the live instance
# reads ~/.open-webui-llamacpp-safe-v4. A connection fixed in one is invisible to the other, so
# this script names the database it writes and never guesses. It never writes two files at once.
#
# Modes
#   show    print the stored list per index (default)
#   apply   repair in place, write nothing if nothing differs
#
# What apply does, and only this
#   - puts the strata url at index 0, keeping every other entry in its existing order
#   - re-aligns api_configs AND api_keys to the new order, not just to the length
#   - marks the strata entry provider llama.cpp (it speaks OpenAI) and gives it a placeholder key
#   - ensures openai.enable is true, and direct.enable is true
#
# direct.enable matters more than it looks. The personal Connections tab renders nothing when
# enable_direct_connections is false, and the add-connection modal probes /api/v1/models/list, a
# direct-connections endpoint. So with direct off, adding a connection returns "openai connection
# problem" even though the engine is perfectly reachable by curl. That was the reported symptom.
#   - never invents a provider that is not already stored or declared
#
# Usage
#   owv-connections.sh show
#   owv-connections.sh apply
#
# Environment
#   OWV_DATA_DIR     fork data dir (default ${XDG_DATA_HOME:-$HOME/.local/share}/openwebvibes)
#   OWV_API_PORT     strata port (default 8004)
#   OWV_STRATA_URL   strata base url (default http://127.0.0.1:$OWV_API_PORT/v1)
#   OWV_CONNECTIONS  optional semicolon-separated list, strata first; overrides the stored order

set -euo pipefail

OWV_HOME="${OWV_HOME:-$HOME}"
DATA_DIR="${OWV_DATA_DIR:-${XDG_DATA_HOME:-$OWV_HOME/.local/share}/openwebvibes}"
API_PORT="${OWV_API_PORT:-8004}"
STRATA_URL="${OWV_STRATA_URL:-http://127.0.0.1:${API_PORT}/v1}"
PYTHON_BIN="${OWV_PYTHON:-python3}"

MODE="${1:-show}"
case "$MODE" in
    show|apply) ;;
    -h|--help) sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "owv-connections.sh: unknown mode: $MODE (show|apply)" >&2; exit 2 ;;
esac

DB="$DATA_DIR/webui.db"
[[ -f "$DB" ]] || { echo "[owv-connections] no database at $DB" >&2; exit 1; }

"$PYTHON_BIN" - "$DB" "$MODE" "$STRATA_URL" "${OWV_CONNECTIONS:-}" <<'PYCON'
import json, sqlite3, sys

db_path, mode, strata_url, declared = sys.argv[1:5]

con = sqlite3.connect(db_path)
con.execute("pragma busy_timeout=5000")

def get(key):
    row = con.execute("select value from config where key=?", (key,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return row[0]

urls = get("openai.api_base_urls")
urls = list(urls) if isinstance(urls, list) else []
configs = get("openai.api_configs")
configs = dict(configs) if isinstance(configs, dict) else {}
keys = get("openai.api_keys")
keys = list(keys) if isinstance(keys, list) else []
enable = bool(get("openai.enable"))

if declared:
    urls = [u.strip() for u in declared.split(";") if u.strip()]

changed = []

if not urls:
    # Nothing stored yet. Seed exactly the one connection the launcher is serving, and nothing
    # else, so a clean fork database reaches the engine instead of falling back to api.openai.com.
    urls = [strata_url]
    changed.append("empty connection list; seeded strata: %s" % strata_url)

old_urls = list(urls)
old_cfg = {str(i): configs.get(str(i), {}) for i in range(len(old_urls))}
old_keys = list(keys)
if len(old_keys) > len(old_urls):
    old_keys = old_keys[:len(old_urls)]
elif len(old_keys) < len(old_urls):
    old_keys = old_keys + [""] * (len(old_urls) - len(old_keys))

if strata_url not in urls:
    urls.insert(0, strata_url)
    changed.append("added strata at index 0: %s" % strata_url)
elif urls[0] != strata_url:
    urls.remove(strata_url)
    urls.insert(0, strata_url)
    changed.append("moved strata from index %d to 0" % old_urls.index(strata_url))

# rebuild per-index blocks for the new order, carrying each entry's own config and key with it
new_cfg, new_keys = {}, []
for i, u in enumerate(urls):
    j = old_urls.index(u) if u in old_urls else -1
    entry = dict(old_cfg.get(str(j), {})) if j >= 0 else {}
    if not entry:
        entry = {"enable": True, "tags": [], "prefix_id": "", "model_ids": [],
                 "connection_type": "external", "auth_type": "bearer"}
        changed.append("created config block for index %d" % i)
    if u == strata_url:
        if entry.get("enable") is False:
            entry["enable"] = True
            changed.append("index %d was disabled, re-enabled" % i)
        if not entry.get("provider"):
            entry["provider"] = "llama.cpp"
            changed.append("index %d marked provider llama.cpp" % i)
    new_cfg[str(i)] = entry
    k = old_keys[j] if j >= 0 else ""
    if u == strata_url and not k:
        # a local engine takes a placeholder key, not an empty one, because the list is read
        # positionally and an empty slot reads as a missing credential
        k = "none"
        changed.append("placeholder key 'none' for strata at index %d" % i)
    new_keys.append(k)

configs, keys = new_cfg, new_keys
if not enable:
    enable = True
    changed.append("openai.enable was false, set true")

# direct.enable gates the connections UI itself, not just outbound calls
if not get("direct.enable"):
    changed.append("direct.enable was false, set true (connections UI and add-connection probe)")
    direct_true = True
else:
    direct_true = None

def show():
    print("[owv-connections] db:", db_path)
    for i, u in enumerate(urls):
        c = configs.get(str(i), {})
        flag = "on " if c.get("enable", True) else "off"
        models = ",".join(c.get("model_ids") or []) or "all"
        prov = (" provider=" + c["provider"]) if c.get("provider") else ""
        k = keys[i]
        print("  %d %s %-32s key=%s models=%s%s" % (
            i, flag, u, (k[:14] + "...") if len(k) > 14 else (k or "-"), models, prov))
    print("  openai.enable =", enable)

if mode == "show":
    show()
    print("[owv-connections] " + ("nothing to change" if not changed else "would change:"))
    for c in changed:
        print("  -", c)
    con.close(); sys.exit(0)

if not changed:
    show()
    print("[owv-connections] already correct, wrote nothing")
    con.close(); sys.exit(0)

cur = con.cursor()
rows = (("openai.api_base_urls", urls), ("openai.api_keys", keys),
        ("openai.api_configs", configs), ("openai.enable", enable))
if direct_true:
    rows += (("direct.enable", True),)
for key, val in rows:
    cur.execute("insert into config (key, value) values (?, ?) "
                "on conflict(key) do update set value=excluded.value",
                (key, json.dumps(val)))
con.commit()

print("[owv-connections] applied:")
for c in changed:
    print("  -", c)
show()
print("[owv-connections] the UI reads the database, so it picks this up on a UI-only restart: kill-owv-dirty")
con.close()
PYCON
