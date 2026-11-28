import os
"""Exercise the recovery path directly, in-process, without needing a live model."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('WEBUI_SECRET_KEY', 'x')
from open_webui.utils import ex_rejected as R

# exactly what the engine reports for an undeclared name
declared = {'read_cache', 'grep_cache', 'cache_index', 'search', 'fetch_url',
            'run', 'push', 'pull', 'check', 'fleet'}

print('=== engine payload -> plan ===')
for payload in (
    [{'name': 'cache_read', 'reason': 'undeclared'}],
    [{'name': 'fleet_ops__push', 'reason': 'undeclared'}],
    [{'name': 'ex_journal', 'reason': 'undeclared'}],
    [{'name': 'cache_read', 'reason': 'undeclared'}, {'name': 'WebFetch', 'reason': 'undeclared'}],
):
    plan = R.plan(payload, declared)
    print('  in :', [p['name'] for p in payload])
    print('  out: fixable=%s unknown=%s' % (plan['fixable'], plan['unknown']))
    note = R.correction_note(plan, declared)
    print('  note:', note.replace(chr(10), ' | ')[:150])
    print()

print('=== must NOT fire when nothing was rejected ===')
print('  empty ->', R.plan([], declared), '| note:', repr(R.correction_note(R.plan([], declared), declared)))

print()
print('=== must NOT "fix" a valid name into something else ===')
for name in sorted(declared):
    got = R.suggest(name, declared)
    assert got == name, f'{name} mapped to {got}'
print('  all', len(declared), 'valid names map to themselves')

print()
print('=== the note always names real tools, never a guess ===')
plan = R.plan([{'name': 'WebFetch'}, {'name': 'run_file'}], declared)
note = R.correction_note(plan, declared)
leaked = [n for n in ('WebFetch', 'run_file') if ('->' in note and n in note.split('->')[1])]
print('  invented names presented as fixes:', leaked if leaked else 'none')
print('  RESULT: PASS')
