"""Feature trim gate for the fork.

RiRi's request: strip the options for frontier models and other things the local agent does not
use, but connections must still work for OpenRouter or similar. So this is a gate, not a deletion.

Two reasons the distinction matters:

1. The license. The About page notice says the Open WebUI identifier must not be altered or
   removed, so stripping means features, not branding. A gate records what is off; it does not
   touch the identifier or the license text.
2. Reversibility. Deleting a feature from the tree is permanent and breaks a multi-user deploy for
   everyone. A gate is per user, so the local agent's preference does not remove a feature another
   user needs. Nothing here deletes files.

Names come from the code that actually decides features, not from preference. The request path
reads a client-supplied `features` block, so the gate applies to those keys. Settings-only entries
are reported separately, because a gate on a name that no code reads is decoration.

Load bearing items are refused here rather than trusted to a checkbox. A gate that could turn off
connections, the tool path, storage, auth, or the DB is a worse bug than the bloat it removes.
"""

from __future__ import annotations

# Feature keys read by the request path at the features block in middleware. These are the keys the
# gate can actually act on, because the gate runs where that block is read.
FEATURE_KEYS = (
    "voice",
    "memory",
    "web_search",
    "image_generation",
    "code_interpreter",
)

# Named entries that decide settings or records rather than request features. Reported as candidates
# so the settings tab can show them, but the gate cannot act on them at the request path.
RECORD_KEYS = (
    "arena",            # evaluation.arena.enable
    "evaluations",      # read in main.py
    "leaderboard",      # models/feedbacks.py, routers/analytics.py
    "comments",         # comments.enable
    "rating",           # ENABLE_MESSAGE_RATING
)

# Never gateable. Load bearing for a local agent fork.
NEVER_GATEABLE = (
    "connections",
    "remote_models",
    "openrouter",
    "tools",
    "function_calling",
    "storage",
    "auth",
    "database",
    "license_notice",
    "open_webui_identifier",
)


def resolve_gate(settings: dict | None) -> dict:
    """Effective gate. A candidate can be off; a load bearing item cannot."""
    settings = settings or {}
    raw = settings.get("trim") or {}
    if not isinstance(raw, dict):
        raw = {}

    gate = {name: not bool(raw.get(name, False)) for name in (*FEATURE_KEYS, *RECORD_KEYS)}

    refused = []
    for name in NEVER_GATEABLE:
        if raw.get(name):
            refused.append({"name": name, "reason": "load bearing, cannot be gated"})

    return {"gate": gate, "refused_overrides": refused}


def gate_features(features: dict, settings: dict | None) -> dict:
    """Apply the gate to a request's feature block.

    Returns the block with gated keys removed, plus what was dropped, so the caller can report it.
    A key that is not in FEATURE_KEYS is passed through untouched; the gate has no claim on it.
    """
    settings = settings or {}
    if not settings.get("trimEnabled", True):
        # A gate that is off must not change the request, so the stock feature block survives.
        return {"features": dict(features), "dropped": [], "refused": []}

    resolved = resolve_gate(settings)
    gate = resolved["gate"]

    dropped = [k for k in features if k in FEATURE_KEYS and not gate.get(k, True)]
    kept = {k: v for k, v in features.items() if k not in dropped}

    return {
        "features": kept,
        "dropped": dropped,
        "refused": resolved["refused_overrides"],
    }


def status(settings: dict | None) -> dict:
    settings = settings or {}
    resolved = resolve_gate(settings)
    gate = resolved["gate"]
    return {
        "trim": bool(settings.get("trimEnabled", True)),
        "gated_off": sorted(k for k, v in gate.items() if not v),
        "feature_keys": list(FEATURE_KEYS),
        "record_keys": list(RECORD_KEYS),
        "never_gateable": list(NEVER_GATEABLE),
        "refused_overrides": resolved["refused_overrides"],
        "counts": {"candidates": len(gate), "off": sum(1 for v in gate.values() if not v)},
        "note": "a gate is per user; nothing here deletes files from the tree",
        "license_note": "stripping means features, not branding. The identifier and license notice stay.",
        "connections_note": "connections stay reachable so OpenRouter and other remote models keep working",
        "wired": "feature keys are enforced at the request path; record keys are reported only",
    }


__all__ = [
    "FEATURE_KEYS",
    "RECORD_KEYS",
    "NEVER_GATEABLE",
    "resolve_gate",
    "gate_features",
    "status",
]
