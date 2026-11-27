"""Exact token counting for the EX gate.

The gate has always estimated with a character ratio (chars / 3.4). Measured against a live
engine on 2026-11-27 that ratio was wrong by roughly 1.5x on tool JSON and code, which is how a
request reported as 103372 tokens reached the engine as 157034 and was refused. A character
ratio cannot be tuned into a tokenizer, so this counts with the model's own vocabulary, which
the launcher already has on disk beside the pack.

Failure is never fatal. When no vocabulary or no tokenizers library is available, count()
returns None and the caller keeps its existing estimate. Nothing here raises into a request path.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

_LOADED: dict = {}


def _candidate_dirs():
    env = os.environ.get("EX_TOKENIZER_DIR")
    if env:
        yield Path(env)
    roots = os.environ.get("EX_MODEL_ROOTS") or ""
    for root in roots.split(os.pathsep):
        if not root:
            continue
        base = Path(root)
        for d in (base / "tokenizer", base):
            try:
                if (d / "vocab.json").exists() and (d / "merges.txt").exists():
                    yield d
            except OSError:
                continue
    for packs in (Path("/home/amd/Strata-data/packs"), Path.home() / "Strata-data" / "packs"):
        try:
            if packs.exists():
                for p in sorted(packs.glob("*/tokenizer")):
                    if (p / "vocab.json").exists() and (p / "merges.txt").exists():
                        yield p
        except OSError:
            continue


def load(directory: Any = None):
    key = str(directory or "auto")
    if key in _LOADED:
        return _LOADED[key]
    tok = None
    dirs = [Path(directory)] if directory else list(_candidate_dirs())
    try:
        from tokenizers import Tokenizer, decoders, models, pre_tokenizers
    except Exception as exc:
        log.warning("EX tokenizer unavailable, keeping the estimate: %s", exc)
        _LOADED[key] = None
        return None
    for d in dirs:
        try:
            vocab = json.loads((d / "vocab.json").read_text(encoding="utf-8"))
            merges = []
            for line in (d / "merges.txt").read_text(encoding="utf-8").splitlines():
                if not line or line.startswith("#"):
                    continue
                parts = line.split(" ")
                if len(parts) == 2:
                    merges.append((parts[0], parts[1]))
            if not vocab or not merges:
                continue
            built = Tokenizer(models.BPE(vocab=vocab, merges=merges))
            built.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
            built.decoder = decoders.ByteLevel()
            tok = built
            log.info("EX tokenizer loaded from %s (%d vocab, %d merges)", d, len(vocab), len(merges))
            break
        except Exception as exc:
            log.warning("EX tokenizer at %s unusable: %s", d, exc)
            tok = None
    _LOADED[key] = tok
    return tok


def available(directory: Any = None) -> bool:
    return load(directory) is not None


def count(text: Any, directory: Any = None) -> Optional[int]:
    """Exact tokens, or None when no vocabulary is loaded."""
    if not text:
        return 0
    if not isinstance(text, str):
        text = str(text)
    tok = load(directory)
    if tok is None:
        return None
    try:
        return len(tok.encode(text).ids)
    except Exception:
        return None
