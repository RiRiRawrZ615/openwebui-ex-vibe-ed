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
    # Two ceilings the window was missing: one for a single read, one for the total bytes the
    # tool window may hold. Both are reported when reached instead of applied silently.
    "readMaxChars": 8000,
    "toolOutputWindowMaxChars": 12000,
    "loopGuard": True,           # detection + steering note at iteration boundary
    "loopThreshold": 3,
    "liveStats": True,           # tok/s during generation
    "showInfoIcon": False,       # the i icon repeats the stats row, so it is off by default
    # Two token counters exist: the engine's own count read from the log, and the front-end's live
    # arrival count. They are different measurements, so the row shows one. "both" is available when
    # someone wants the two figures side by side and can tell them apart.
    "statsTokenSource": "engine",
    "toolsDefaultActive": True,       # active tools apply without a per-chat pick
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
    # Two figures the shaping pass never counted. The tool definitions are part of every request with
    # tools active, and the tool bodies only enter the prompt when the request is assembled, which is
    # after the old measurement ran. Both are now measured; this is the allowance for the first.
    "toolSchemaTokens": 4096,    # allowance for the tool definition block
    "trimTargetRatio": 0.85,     # headroom target after trimming, so the prefix stays stable
    "fitMaxTokens": True,        # shorten the requested output cap to the room the prompt leaves
    # Context sliding. Past this share of the real window the oldest turns leave the prompt and
    # are journalled by entry id, keeping the last few responses plus the last reasoning turn. The
    # fraction scales, so a 262144 window slides at 183500 and a 128k machine slides at 89600
    # with no per-machine tuning.
    "contextSlide": True,        # the gate calls ex_slide per turn
    "slideFraction": 0.70,       # share of the window that fires the slide
    "slideKeepTail": 4,          # last agent/user responses kept intact
    # Safety net for the case where nothing can fit. A generation cannot be interrupted mid-turn, so
    # the only control is at the iteration boundary. When a prompt is over the window the gate builds
    # the smallest prompt that still contains a real request from the reconstruction file instead of
    # stopping at a refusal.
    "safetyNet": True,
    # The reconstruction block is injected every turn as a system instruction, not only when a slide
    # fires. It carries prior context, the last full reasoning loop, the last response, the last user
    # message, and the path of the text file holding all four.
    "reconstructBlock": True,
    "enforceCeiling": True,      # refuse locally before the engine does; the engine never truncates
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
    # Rolling context. The writer keeps a verbatim copy of the chat on disk in the export shape and
    # the reader hands it to the next chat, so a chat can be continued without anything being
    # paraphrased on the way in. The engine is not asked to be clever about this; the app owns it.
    "rollover": True,
    "rolloverThreshold": 0.7,      # fraction of the window that fires the rollover
    "rolloverMaxChars": 60000,     # size of the block fed back; the file on disk keeps everything
    # Crossing the rollover point has to shrink the prompt, not only record where the next chat should
    # look. Without this the trigger fired forever and nothing ever got smaller, which is how a chat
    # reached 262306 tokens against a 262144 window and was refused.
    "rolloverShed": True,         # move the oldest sections out of the prompt at the trigger
    "rolloverShedKeepRatio": 0.7,  # kept tail after a shed; the chat grows back to the trigger
    # What comes back into the prompt after an eviction: the address plus the last turn's reasoning.
    # Everything else is pulled by id or by similarity, so the prompt does not carry a copy of the
    # chat twice.
    "shedFeedBackReasoning": True,  # the most recent reasoning block returns verbatim
    "shedReasoningChars": 2000,      # cap on that one block, not on the chat
    "reasoningFiles": True,        # one file per reasoning block, addressed by id
    "reasoningOnDemand": True,     # ids are attached to sections so a block can be pulled back
    "reasoningExcerptChars": 160,  # length of one index line
    "rolloverCarryOnce": True,     # a carry pointer is consumed the first time a new chat reads it
    # Engine status, read from the log the launcher already writes. The figure is the engine's own and
    # is labelled as such: the live row's rate is a windowed arrival estimate, and the two are not the
    # same measurement on a turn with tool calls.
    "engineStatus": True,
    "engineStatusLines": 400,      # tail window, so a growing log is not re-read whole
    "engineStatusEta": True,       # (budget - tokens) / speed, seconds only
    "engineStatusMaxAge": 30,      # seconds; past this the log is reported as stale, not as live
    # Empty resolves to the launcher's engine log. Kept as its own line because the mirror test
    # compares parsed values, and an inline comment on a string default reads as part of the value.
    "engineLogPath": "",
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
