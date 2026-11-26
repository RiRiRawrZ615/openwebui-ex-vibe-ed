"""Self-check for the history-eviction step of the context gate.

The step used to anchor on the FIRST user message and drop only the text in front of it. In a
real chat the system prompt is first, so that slice held nothing but the system message and the
step could never fire: a 45k-token list stayed over a 25k limit with every default on. It now
evicts the oldest turn at a time behind a trailing window. These checks are the difference.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from open_webui.utils.ex_fit import fit_request, scaled_limits
from open_webui.utils.ex_settings import DEFAULTS

CHAT = "11111111-2222-3333-4444-555555555555"


def conv(turns):
    msgs = [{"role": "system", "content": "SYSMARKER"}]
    for i in range(turns):
        msgs.append({"role": "user", "content": "u%dx" % i})
        msgs.append({"role": "assistant", "content": "y" * 300})
    msgs.append({"role": "user", "content": "LIVETURN"})
    return msgs


def count(m):
    c = m.get("content") if isinstance(m, dict) else m
    return len(str(c or "")) // 4


def run(msgs):
    limits = scaled_limits(32768, {})
    out, report = fit_request(msgs, limits, dict(DEFAULTS), chat_id=CHAT,
                              data_dir="/tmp", token_counter=count)
    return out, report, limits


def main():
    bad = []
    out, rep, lim = run(conv(600))
    if sum(count(m) for m in out) > lim["send_limit"]:
        bad.append("still over the send limit")
    if not rep.get("history_dropped"):
        bad.append("nothing evicted from an over-limit chat")
    text = " ".join(str(m.get("content")) for m in out)
    if "SYSMARKER" not in text:
        bad.append("the system prompt was evicted")
    if "LIVETURN" not in text:
        bad.append("the live user turn was evicted")
    small, rep2, _ = run(conv(10))
    if rep2.get("history_dropped"):
        bad.append("evicted from a chat that already fit")
    _, rep3, _ = run(conv(400))
    if (rep3.get("history_dropped") or 0) > 200:
        bad.append("eviction overshot: far more messages dropped than the overshoot needed")
    for line in bad:
        print("FAIL", line)
    print("ok" if not bad else "%d failure(s)" % len(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
