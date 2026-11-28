"""Recover from a tool call the engine refused because the name was never declared.

The failure this fixes, seen 28 times on one machine:

    strata: 1 <tool_call> block(s) in the answer did not read as a call of a declared tool
    and went back as text ...; the names the model wrote: cache_read

The engine is right not to execute a call it cannot resolve. What was wrong is what happened
next: the markup went into the answer as ordinary assistant text, the agentic loop saw no tool
calls, and the turn ended. The user sees raw <tool_call> markup printed as prose and the run is
dead.

The real names, measured on this deployment, cluster into three shapes:

  1. a near miss of a declared name     cache_read      for read_cache
  2. a namespaced form                  fleet_ops__push for push
  3. a tool that is not loaded at all   ex_journal, WebFetch, run_file, search_deviantart

(1) and (2) are recoverable: the intent is unambiguous, so the name can be corrected mechanically
and the call re-issued once. (3) is not, because there is no declared name that matches intent, so
the honest outcome is a short correction telling the model what it actually has.

Nothing here guesses at semantics. Every recovery is anchored to the exact set of names the
request declared.
"""
from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

# Names are matched case-insensitively and ignoring separators, so read_cache, readCache and
# ReadCache all collapse to the same key. Underscores and double underscores are stripped for the
# comparison only; the original spelling is never sent.
_SEP = re.compile(r"[^a-z0-9]+")


def _key(name: str) -> str:
    return _SEP.sub("", str(name or "").lower())


def declared_names(tools: Any) -> set[str]:
    """The exact function names the request declared."""
    names: set[str] = set()
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if "function" in tool else tool
        if isinstance(fn, dict):
            name = fn.get("name")
            if name:
                names.add(str(name))
    return names


def suggest(name: str, declared: set[str]) -> str:
    """The declared name this one most likely meant, or '' when there is no honest match."""
    if not declared:
        return ""
    raw = str(name or "")
    if raw in declared:
        return raw
    k = _key(raw)
    if not k:
        return ""
    by_key = {_key(d): d for d in declared}

    # Exact match ignoring case and separators. This is the cache_read -> read_cache class, and
    # also the fleet_ops__push -> push class once the prefix is removed below.
    if k in by_key:
        return by_key[k]

    # A namespaced form: fleet_ops__push, ex_journal__needle. Try the part after the last
    # separator run, then each trailing segment, longest first, because the prefix is the tool
    # group rather than the function.
    parts = [p for p in _SEP.split(raw.lower()) if p]
    for i in range(1, len(parts)):
        suffix = "".join(parts[i:])
        if suffix in by_key:
            return by_key[suffix]

    # A declared name that ends with what the model wrote: the model added a prefix.
    for d in declared:
        if _key(d).endswith(k) or k.endswith(_key(d)):
            return d

    # Word-order swaps and single missing/extra segments: cache_read vs read_cache is the same two
    # words in the other order, so compare sorted segments.
    if len(parts) > 1:
        wanted = "".join(sorted(parts))
        for d in declared:
            dparts = [p for p in _SEP.split(d.lower()) if p]
            if len(dparts) > 1 and "".join(sorted(dparts)) == wanted:
                return d

    return ""


def plan(rejected: Any, declared: set[str]) -> dict:
    """Split rejected names into ones that can be corrected and ones that cannot.

    Returns {'fixable': {written: declared}, 'unknown': [written, ...]}.
    """
    fixable: dict[str, str] = {}
    unknown: list[str] = []
    seen = set()
    for item in rejected or []:
        name = item.get("name") if isinstance(item, dict) else item
        name = str(name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        target = suggest(name, declared)
        if target and target != name:
            fixable[name] = target
        elif not target:
            unknown.append(name)
    return {"fixable": fixable, "unknown": unknown}


def correction_note(result: dict, declared: set[str]) -> str:
    """The system note that tells the model what it actually has. Empty when nothing to say."""
    fixable = result.get("fixable") or {}
    unknown = result.get("unknown") or []
    if not fixable and not unknown:
        return ""
    lines = [
        "[tool name correction]",
        "The last answer contained a tool call that could not be run because the name was not",
        "declared in this request. Nothing was executed. Do not repeat the markup.",
    ]
    if fixable:
        lines.append("These names resolve to a declared tool; use the declared spelling:")
        for written, real in sorted(fixable.items()):
            lines.append(f"  {written}  ->  {real}")
    if unknown:
        lines.append(
            "These are not available in this chat and were not run: " + ", ".join(sorted(unknown))
        )
        lines.append(
            "Use one of the declared tools listed above, or answer in plain text if none of them"
            " fits. Do not invent a tool name."
        )
    return "\n".join(lines)


def available_summary(declared: set[str], limit: int = 60) -> str:
    """A compact list of what IS declared, for the correction note."""
    if not declared:
        return ""
    names = sorted(declared)
    if len(names) > limit:
        names = names[:limit]
        return ", ".join(names) + f", ... ({len(declared)} total)"
    return ", ".join(names)


__all__ = [
    "available_summary",
    "correction_note",
    "declared_names",
    "plan",
    "suggest",
]
