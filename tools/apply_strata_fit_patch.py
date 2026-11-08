#!/usr/bin/env python3
"""Strata side of the context fix: fit the output cap instead of answering 400.

The engine's rule is correct and was never the problem: it does not truncate a prompt, so a request
that leaves no room for an answer is refused. The request was the thing that was wrong. The app now
fits max_tokens itself in ex_wiring.prompt_gate, so this patch is belt and braces: it makes the
engine clamp the cap for any client that does not do the arithmetic.

Default is dry run. The profile file is shared between the stock instance and the dev instance, so
applying this changes both. Run with --apply only if that is what you want.

Usage:
  python3 apply_strata_fit_patch.py                      # dry run on the IQ2_XS profile
  python3 apply_strata_fit_patch.py --apply              # write the key
  python3 apply_strata_fit_patch.py --profile <path>     # a dev-only copy of the profile
"""
import argparse, json, sys
from pathlib import Path

KEY = "fit_max_tokens"
DEFAULT_PROFILE = "/home/amd/strata/profiles/qwen-iq2-xs-iq2-xs-qwen3-8-flash-next-gsq-rco-iq2-xs/strata-iq2_xs.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    ap.add_argument("--apply", action="store_true", help="write the key; default is dry run")
    args = ap.parse_args()

    path = Path(args.profile)
    if not path.exists():
        print(f"[ERROR] profile not found: {path}")
        return 1

    record = json.loads(path.read_text(encoding="utf-8"))
    current = record.get(KEY)
    if current is True:
        print(f"[ok] {KEY} already true in {path}")
        return 0

    print(f"[dry run] {path}")
    print(f"  {KEY}: {current!r} -> true")
    print("  engine policy unchanged: prompts are never truncated, only the output cap is clamped.")
    if not args.apply:
        print("  nothing written. The dev instance and the stock instance read this same file.")
        return 0

    record[KEY] = True
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"[written] {KEY}: true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
