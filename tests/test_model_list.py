"""Catalog-row listing checks for Open Web Vibes.

Run with plain python (no pytest on this machine): python3 tests/test_model_list.py

The helpers are read straight out of utils/models.py and exec'd, so the test does not need the
fastapi/sqlalchemy import chain. It validates the behaviour the UI depends on: a row must have a
label, and a row must have a connection index before a completion can be routed to it.
"""

import re
import types

SRC = 'backend/open_webui/utils/models.py'

text = open(SRC, encoding='utf-8', newline='').read()
start = text.index('def _catalog_display_name')
end = text.index('async def get_all_models')
ns = {}
exec(text[start:end], ns)

display_name = ns['_catalog_display_name']
url_index = ns['_catalog_url_index']


def row(name, model_id='row-1'):
    r = types.SimpleNamespace()
    r.name = name
    r.id = model_id
    return r


def app(urls):
    state = types.SimpleNamespace(OPENAI_API_BASE_URLS=urls)
    return types.SimpleNamespace(app=types.SimpleNamespace(state=state))


def check(label, got, want):
    status = 'ok' if got == want else 'FAIL'
    print(f'{status}: {label} -> {got!r} (expected {want!r})')
    return got == want


results = []

# lp10 case: profiles carry no name field, so the row label falls back to the launcher path
results.append(check('blank name falls back to handshake model stem',
                     display_name(row(''), {'model': '/home/amd/models/Ternary-Bonsai-2-7B-PQ2_0.gguf'}),
                     'Ternary-Bonsai-2-7B-PQ2_0'))
results.append(check('name wins when the row has one',
                     display_name(row('bonsai-27b'), {'model': '/x/y.gguf'}), 'bonsai-27b'))
results.append(check('base model name is preferred over the path',
                     display_name(row('', 'row-2'), {'model': '/x/y.gguf'}, {'name': 'qwen3.6'}), 'qwen3.6'))
results.append(check('nothing to fall back on keeps the row id',
                     display_name(row('', 'row-3'), {}), 'row-3'))

# routing: urlIdx is what a completion needs, and it only exists when a connection can be named
results.append(check('base model urlIdx wins over the backend match',
                     url_index(app(['http://a/v1', 'http://b/v1']), 'strata', {'urlIdx': 1}), 1))
results.append(check('backend named in the connection url resolves',
                     url_index(app(['http://strata-host/v1', 'http://llamacpp-host/v1']), 'llamacpp'), 1))
results.append(check('single connection is unambiguous (nvidia strata, lp10 llama)',
                     url_index(app(['http://127.0.0.1:8004/v1']), 'strata'), 0))
results.append(check('localhost url with no engine name cannot be matched',
                     url_index(app(['http://127.0.0.1:8004/v1', 'http://127.0.0.1:8001/v1']), 'strata'), None))
results.append(check('no connection configured cannot route',
                     url_index(app([]), 'llamacpp'), None))

print()
print('all passed' if all(results) else 'FAILURES present')
raise SystemExit(0 if all(results) else 1)
