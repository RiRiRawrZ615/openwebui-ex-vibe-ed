"""Self-check for the reconstruction handoff's character budget.

Run with the backend on PYTHONPATH:

    python backend/ex_reconstruct_selftest.py

The handoff used four hardcoded 1400-character slices. A reasoning loop measured 7233 characters
on a working machine, so on any window large enough to hold it the block still threw away 81% of
the one field the model needed to pick the conversation back up, silently and with no ellipsis.
The slices are now one shared budget with a per-field floor. These checks are the difference.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from open_webui.utils.ex_reconstruct import (
    DEFAULT_MIN_PER_FIELD,
    DEFAULT_TOTAL_CAP,
    INLINE_CAP,
    _slices,
    reconstruct_block,
)

# A realistic set: long reasoning, short everything else.
PARTS = {
    "prior_context": "P" * 200,
    "last_reasoning": "R" * 7233,
    "last_response": "S" * 900,
    "last_user": "U" * 60,
}


def main() -> int:
    failures = []

    # 1. the long field keeps its text instead of being cut to a fifth
    cut = _slices(PARTS, DEFAULT_TOTAL_CAP, DEFAULT_MIN_PER_FIELD)
    if len(cut["last_reasoning"]) <= INLINE_CAP:
        failures.append(
            f"reasoning still capped at {len(cut['last_reasoning'])} (old ceiling {INLINE_CAP})"
        )
    if cut["last_reasoning"] != PARTS["last_reasoning"]:
        failures.append("reasoning text was truncated when the budget had room for it")

    # 2. the whole block stays inside the budget
    total = sum(len(v) for v in cut.values())
    if total > DEFAULT_TOTAL_CAP:
        failures.append(f"block {total} exceeds budget {DEFAULT_TOTAL_CAP}")

    # 3. a pathologically long field cannot starve the user turn
    huge = dict(PARTS, last_reasoning="R" * 500000)
    cut2 = _slices(huge, DEFAULT_TOTAL_CAP, DEFAULT_MIN_PER_FIELD)
    if len(cut2["last_user"]) != len(PARTS["last_user"]):
        failures.append(f"user turn clipped to {len(cut2['last_user'])} by an oversized reasoning field")
    if sum(len(v) for v in cut2.values()) > DEFAULT_TOTAL_CAP:
        failures.append("oversized case exceeded the budget")

    # 4. empty parts must not raise
    try:
        empty = _slices({}, DEFAULT_TOTAL_CAP, DEFAULT_MIN_PER_FIELD)
        if any(len(v) for v in empty.values()):
            failures.append("empty input produced text")
    except Exception as exc:  # noqa: BLE001 - a raise here is the failure
        failures.append(f"empty input raised {exc!r}")

    # 5. the rendered block keeps the marker, the journal hint and the file pointer
    block = reconstruct_block("/tmp", "cid", PARTS, [1, 2, 3])
    for needle in ("[context slide]", "entries: 1, 2, 3", "Reconstruction file:"):
        if needle not in block:
            failures.append(f"rendered block lost {needle!r}")

    # 6. the old three-argument call must still work for any caller that ignores the budget
    try:
        reconstruct_block("/tmp", "cid", PARTS, [1])
    except Exception as exc:  # noqa: BLE001
        failures.append(f"positional call raised {exc!r}")

    for line in failures:
        print("FAIL", line)
    print("ok" if not failures else f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
