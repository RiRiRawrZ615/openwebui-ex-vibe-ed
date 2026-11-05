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
    # Feature E: the UI records the backend choice. It is a preference, not a promise;
    # the status endpoint reports what is actually serving the model.
    "backend": "auto",           # auto | llamacpp | strata
    "modelPath": "",             # optional override for the model check
    "allowInstall": False,       # plan is dry-run unless the user turns this on
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
