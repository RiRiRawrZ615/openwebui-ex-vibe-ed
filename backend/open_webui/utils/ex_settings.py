"""EX settings resolver.

Pure logic. Settings are stored per-key (stock behavior), so the EX namespace
needs no migration. This resolver reads the `ex` block from user settings in the
shapes it actually appears in: dict, pydantic model, or absent.

Defaults are intentionally inside the good band for a small quant on llama.cpp
(temperature band 0.2-0.4 per prior analysis), but the resolver does not force
params; it only gates EX behavior. Params themselves belong to the launcher
handshake, because the launcher is what starts the server and wins.
"""

from __future__ import annotations

from typing import Any

DEFAULTS = {
    "enabled": True,
    "journalWindow": 1,          # turns of reasoning kept in the prompt head
    "toolOutputWindow": 3,       # tool outputs kept in full, counted from the end
    "toolOutputMaxChars": 1500,  # only oversized bodies get journalled
    "loopGuard": True,           # detection + steering note at iteration boundary
    "loopThreshold": 3,
    "liveStats": True,           # tok/s during generation
    "showInfoIcon": True,        # enriched i icon at end of message
    "launcherStatus": True,      # show launcher-override state in the EX tab
    "verbatimSummary": False,    # plain-text digest instead of model paraphrase
    # Per-chat retrieval memory. Keeps the original text outside the prompt in a local
    # vector index, and puts the few most relevant passages back on the next turn. This is
    # what survives a compaction: the digest shrinks the transcript, this keeps a copy.
    "ragMemory": True,           # index this chat and retrieve relevant passages
    "ragTopK": 6,                # passages returned per turn
    "ragMaxChars": 4000,         # character budget for the retrieved block
    "ragMinScore": 0.25,         # cosine floor; below this a hit is noise
    # Context window policy. The engine serves 262144 tokens; the stock compaction threshold
    # is 80000, so the default here is the real ceiling rather than the cautious one. The
    # reply reserve is held back so the answer itself never truncates.
    #
    # This is the only context policy. The separate summarise-and-drop compaction feature was
    # removed because it derived its own threshold and rewrote the transcript behind these
    # settings, which is the conflict that made the window figure unreliable.
    "contextWindow": 262144,     # the engine ceiling; lower it only if the engine is lowered
    "replyReserve": 16384,       # tokens held back from the window for the reply
    "trimToolOutput": True,      # trim stale tool results before touching conversation text
    "keepToolTurns": 3,          # tool results kept intact, counted from the end
    "tailInject": True,          # inject context blocks at the tail so the KV cache survives
    # Feature E: the UI records the backend choice. It is a preference, not a promise;
    # the status endpoint reports what is actually serving the model.
    "backend": "auto",           # auto | llamacpp | strata
    "modelPath": "",             # optional override for the model check
    "allowInstall": False,       # plan is dry-run unless the user turns this on
    # Agentic continuation. Two counters, kept separate because they mean different things:
    # transport retries can be a repeat of the same request, shape retries cannot.
    "autoRetry": True,
    "maxRetries": 3,
    "shapeRetries": 1,
    "backoffSeconds": 2.0,
    "resumeOnCrash": True,
    "exportFallback": True,
    # Previous context as a verbatim export. A model paraphrase is what loses the facts, so the
    # block is built from the stored messages themselves, newest wins, nothing rewritten.
    "previousContext": True,
    "previousContextChars": 12000,
    "previousContextAsSystem": True,
    # Mid generation steering. The stock queue waits for generation to finish, so it is
    # send_after. The redirect form is injection at the next iteration boundary.
    "steeringQueue": True,
    "drainSteeringAtBoundary": True,
    "steeringMode": "next_iteration",
    # Modularity. A slot falls back to the builtin component when its plugin is off, so a plugin
    # cannot break the host.
    "uiPlugins": True,
    # Themes. Navy as RiRi chose it; cobalt read as too bright.
    "themeName": "navy",
    "themeAccent": "#24406b",
    "themeSurface": "#12203a",
    "themeText": "#e6ecf5",
    # Canonical to the CSS and the TS mirror. These three files are compared by
    # test_ex_settings and test_ex_theme_mirror, and they had drifted: Python still held
    # the old bright two-stop ('#24406b', '#3a6ba5') while the stylesheet and the mirror
    # had already moved to the dimmed three-stop below. Since this engine applies the
    # gradient at runtime, the Python value is the one the user actually sees, so it is
    # the one that had to move.
    "themeGradient": ["#0a1421", "#101d2e", "#17293d"],
    "themeAdjust": 0,
    "themeWallpaper": "",
    "themeIcon": "",
    # Compute layer. A worker record is what the worker advertised, so this is an empty list
    # by default; nothing here assumes a device exists or claims a capacity.
    "workers": [],
    # Module registry. Empty means every non-core module is on; a dict here is a per-module
    # override, and a core override is refused by the registry rather than applied.
    "modules": {},
    # Feature trim. A gate, per user, so nothing here deletes files from the tree.
    "trimEnabled": True,
    "trim": {},

}


def resolve_ex_settings(user: Any, form_data: dict | None = None) -> dict:
    """Return the effective EX settings for a request.

    Read order: form_data settings (chat controls can override), then user
    settings, then defaults. Accepts user.settings as dict or pydantic model.
    """
    raw: dict = {}

    fd_settings = (form_data or {}).get("settings") or {}
    if isinstance(fd_settings, dict):
        raw = fd_settings.get("ex") or {}

    if not raw and user is not None:
        settings = getattr(user, "settings", None)
        if hasattr(settings, "model_dump"):
            settings = settings.model_dump()
        if isinstance(settings, dict):
            # The settings modal saves under the ui block, so an EX tab lands at
            # settings.ui.ex. Accept both shapes; the block must be a dict.
            ui = settings.get("ui")
            if isinstance(ui, dict) and isinstance(ui.get("ex"), dict):
                raw = ui["ex"]
            elif isinstance(settings.get("ex"), dict):
                raw = settings["ex"]

    merged = dict(DEFAULTS)
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in merged:
                merged[key] = value
    return merged
