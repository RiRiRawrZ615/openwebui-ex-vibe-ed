"""No pytest, no network. Seeds its own data dir and asserts the properties that matter."""
import os, sys, tempfile, shutil
import os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from open_webui.utils import ex_perturn as P
from open_webui.utils.ex_context_window import message_tokens

def msgs_fixture(turns=12, tool_body=900):
    m = [{'role': 'system', 'id': 'sys', 'content': 'You are an agent. Follow the rules.'}]
    for i in range(turns):
        m.append({'role': 'user', 'id': f'u{i}', 'content': f'request number {i}: please do the thing'})
        m.append({'role': 'assistant', 'id': f'a{i}', 'content': f'working on {i}'})
        m.append({'role': 'assistant', 'id': f'ac{i}', 'content': '',
                  'tool_calls': [{'name': 'read_file', 'arguments': {'path': f'/tmp/{i}.py'}}]})
        m.append({'role': 'tool', 'id': f't{i}', 'tool_call_id': f'c{i}',
                  'content': 'X' * tool_body})
        m.append({'role': 'assistant', 'id': f'r{i}', 'content': 'done', 'type': 'reasoning'})
    return m

fail = []
def check(name, cond, detail=''):
    print(('  PASS  ' if cond else '  FAIL  ') + name + ((' | ' + str(detail)) if detail else ''))
    if not cond: fail.append(name)

d = tempfile.mkdtemp(prefix='expt-')
try:
    S = {'shedFeedBackReasoning': True, 'shedReasoningChars': 2000,
         'evictExportChars': 20000, 'evictMessageChars': 2000}
    original = msgs_fixture()
    before = sum(message_tokens(m) for m in original)

    # turn 1
    out, rep = P.per_turn_evict(original, S, d, 'chat-1')
    after1 = sum(message_tokens(m) for m in out)
    check('evicts on the first turn', rep['evicted'] > 0, f"evicted={rep['evicted']}")
    check('prompt shrinks', after1 < before, f'{before} -> {after1}')
    check('system head survives', any(m.get('role') == 'system' and 'agent' in str(m.get('content')) for m in out))
    check('live user turn survives', any(m.get('role') == 'user' and 'request number 11' in str(m.get('content')) for m in out))
    check('handoff is one block', sum(1 for m in out if str(m.get('content','')).startswith(P.MARKER)) == 1)
    check('transcript file written', P.transcript_path(d, 'chat-1').exists())
    t1 = P.transcript_path(d, 'chat-1').read_text()
    check('transcript holds the evicted text', 'request number 0' in t1, f'{len(t1)} chars')

    # turn 2: same chat, one more exchange. must not duplicate.
    grow = list(original) + [
        {'role': 'user', 'id': 'u12', 'content': 'request number 12: continue'},
        {'role': 'assistant', 'id': 'a12', 'content': 'continuing'},
    ]
    out2, rep2 = P.per_turn_evict(grow, S, d, 'chat-1')
    t2 = P.transcript_path(d, 'chat-1').read_text()
    check('no duplicate lines on second pass', t2.count('request number 0 ') == t1.count('request number 0 '),
          f"count {t1.count('request number 0 ')} -> {t2.count('request number 0 ')}")
    check('new turn appended', 'request number 11' in t2)
    check('replaces the handoff, does not stack',
          sum(1 for m in out2 if str(m.get('content','')).startswith(P.MARKER)) == 1)
    after2 = sum(message_tokens(m) for m in out2)
    check('prompt stays bounded across turns', after2 < before * 0.5, f'turn1={after1} turn2={after2} full={before}')

    # reasoning fed back
    check('last reasoning fed back', 'your own last reasoning' in ' '.join(str(m.get('content','')) for m in out2).lower())

    # no live user turn: must be a no-op
    only_assistant = [{'role': 'system', 'content': 'sys'}, {'role': 'assistant', 'content': 'hi'}]
    out3, rep3 = P.per_turn_evict(only_assistant, S, d, 'chat-2')
    check('no live turn means no eviction', rep3['evicted'] == 0 and out3 == only_assistant, rep3.get('reason'))

    # write failure must not evict
    bad = '/proc/definitely/not/writable'
    out4, rep4 = P.per_turn_evict(msgs_fixture(3), S, bad, 'chat-3')
    check('failed write means nothing evicted', rep4['evicted'] == 0, rep4.get('reason'))

    # export cap respected
    S2 = dict(S, evictExportChars=1500)
    out5, rep5 = P.per_turn_evict(msgs_fixture(40), S2, d, 'chat-4')
    blk = [m for m in out5 if str(m.get('content','')).startswith(P.MARKER)][0]
    check('export block obeys its cap', len(str(blk['content'])) < 4000, f"{len(str(blk['content']))} chars")

    # growth test: 150 turns must not blow the window
    big = msgs_fixture(150)
    out6, rep6 = P.per_turn_evict(big, S, d, 'chat-5')
    after6 = sum(message_tokens(m) for m in out6)
    check('150 turns stays small', after6 < 9000, f'{sum(message_tokens(m) for m in big)} -> {after6}')
finally:
    shutil.rmtree(d, ignore_errors=True)

print()
print('RESULT:', 'all pass' if not fail else f'{len(fail)} FAILED: {fail}')
sys.exit(1 if fail else 0)
