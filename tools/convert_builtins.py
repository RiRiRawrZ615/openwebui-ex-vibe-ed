#!/usr/bin/env python3
"""Convert a WebUI tool export into an Open Web Vibes native builtin module.

Approach: keep the class, add delegating wrappers
-------------------------------------------------
The first attempt at this rewrote each method into a module-level function, resolving
`self.valves.X` to a constant and `self.helper(...)` to a plain call. That failed on real
code, and the failures are instructive:

  - `asyncio.to_thread(self._fetch_sync, url)` passes the bound method as a *value*, not as
    a call, so attribute rewriting does not reach it.
  - `self._ssh_base(self.valves, host)` passes the whole Valves object to a helper, so
    flattening valves into constants is wrong in principle, not just mechanically.
  - `social_reddit_x_1` mutates `self._token` and `self._last_request` across calls. A plain
    function has nowhere to put that state, and hoisting it to module globals would silently
    change the threading of a rate limiter and a token cache.

Rather than perform surgery on nine files of unfamiliar code, this keeps each `Tools` class
exactly as written and appends:

  1. a module-level singleton, `_builtin_tools = Tools()`
  2. one module-level async wrapper per public method, with the original signature copied
     verbatim and the arguments forwarded

The registry introspects plain async functions, so the wrappers are what it sees. The tool's
own logic, its valves, its helpers, and its instance state all continue to behave exactly as
they did as a DB tool, which means a conversion cannot introduce a behavioural difference.
The cost is one extra stack frame per call.

Which methods get a wrapper
---------------------------
Public async methods with a return annotation. That matches what the registry requires, and
it mirrors how the original exports were introspected. Underscore-prefixed methods are
helpers and stay unexposed.

Defensive detail: the class body may reference names that only exist in the original module
namespace. Those imports are preserved verbatim, because the class is now in a new file.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

HEADER = '''"""Open Web Vibes builtin: {name}

Generated from the WebUI tool export `{tool_id}` by tools/convert_builtins.py.
Do not edit by hand, because the next regeneration would overwrite it. User edits belong in
the overlay, which takes precedence over this file.

Original tool name: {orig_name}
Exported functions: {funcs}

Structure note: the original `Tools` class is kept intact and exposed through module-level
wrappers, so the tool's valves, helpers and instance state behave exactly as they did as a
DB tool. See the converter's docstring for why this is preferable to rewriting the bodies.
"""
'''

# Methods that must not become tools, and the rule for what does.
#
# The exports come in two shapes. Some tools expose `async def` methods (pcalc, the YouTube
# tools, the social reader); others expose ordinary `def` (leanrea, fleet_ops, fleet_ssh),
# which are built around blocking subprocess and SSH calls. Both are legitimate: the original
# WebUI tool runtime did not care, because it invoked whatever it found.
#
# The native registry does care, but only about the spec, which needs a return annotation.
# So the wrapper kind is chosen to match the original exactly: an async method gets an async
# wrapper that awaits, a sync method gets a sync wrapper that returns directly. Wrapping a
# sync tool in an async shell would run its blocking SSH work on the event loop, which is
# precisely the bug this preserves against.
def is_public_tool_method(node: ast.stmt) -> bool:
    if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
        return False
    if node.name.startswith('_'):
        return False
    # The registry builds a spec from the signature, which needs the return annotation.
    return node.returns is not None


def build_wrapper(method: ast.AsyncFunctionDef | ast.FunctionDef, singleton: str) -> ast.stmt:
    """Emit a module-level function of the same kind that forwards to the singleton."""
    args = ast.arguments(
        posonlyargs=list(method.args.posonlyargs),
        args=list(method.args.args),
        vararg=method.args.vararg,
        kwonlyargs=list(method.args.kwonlyargs),
        kw_defaults=list(method.args.kw_defaults),
        kwarg=method.args.kwarg,
        defaults=list(method.args.defaults),
    )

    # Forward every declared parameter by name, so defaults and keyword-only arguments work.
    names = [a.arg for a in args.posonlyargs + args.args if a.arg != 'self']
    names += [a.arg for a in args.kwonlyargs]
    if args.vararg:
        names.append('*' + args.vararg.arg)
    if args.kwarg:
        names.append('**' + args.kwarg.arg)

    call_args: list[ast.expr] = []
    for raw in names:
        if raw.startswith('*'):
            call_args.append(
                ast.Starred(value=ast.Name(id=raw.lstrip('*'), ctx=ast.Load()), ctx=ast.Load())
            )
        elif raw.startswith('**'):
            call_args.append(ast.keyword(arg=None, value=ast.Name(id=raw.lstrip('*'), ctx=ast.Load())))
        else:
            call_args.append(ast.keyword(arg=raw, value=ast.Name(id=raw, ctx=ast.Load())))

    call = ast.Call(
        func=ast.Attribute(value=ast.Name(id=singleton, ctx=ast.Load()), attr=method.name, ctx=ast.Load()),
        args=[],
        keywords=call_args,
    )
    is_async = isinstance(method, ast.AsyncFunctionDef)
    value = ast.Await(value=call) if is_async else call
    body: list[ast.stmt] = [ast.Return(value=value)]

    # Keep the original docstring: the registry turns it into the tool description, and the
    # :param lines become the parameter descriptions.
    doc = ast.get_docstring(method, clean=False)
    if doc:
        body.insert(0, ast.Expr(value=ast.Constant(value=doc)))

    cls = ast.AsyncFunctionDef if is_async else ast.FunctionDef
    wrapper = cls(
        name=method.name,
        args=args,
        body=body,
        decorator_list=[],
        returns=method.returns,
        type_comment=None,
        type_params=[],
    )
    return ast.fix_missing_locations(wrapper)


def convert(source: str, tool_id: str, name: str, orig_name: str, funcs: list[str]) -> str:
    module = ast.parse(source)

    tools_cls = next(
        (n for n in module.body if isinstance(n, ast.ClassDef) and n.name == 'Tools'), None
    )
    if tools_cls is None:
        raise SystemExit(f'{tool_id}: no `class Tools` found, cannot convert')

    # Everything except the class: imports, logger setup, module constants. The class body
    # references these, so dropping them would produce NameError at import time.
    preamble = [n for n in module.body if n is not tools_cls]

    # `from __future__` imports must be the first statement after the module docstring, and
    # fleet_ssh has one. ast.unparse emits the docstring first, so any future import must be
    # hoisted above the rest of the preamble or the module will not compile.
    future = [n for n in preamble if isinstance(n, ast.ImportFrom) and n.module == '__future__']
    rest = [n for n in preamble if n not in future]

    # The module docstring, if the original had one, must stay first.
    # The original leading string is dropped. Some exports start with a plain module
    # docstring, but fleet_ssh starts with a YAML frontmatter block (`---\ntitle: ...`). Both
    # are just a leading string constant, and neither is wanted in the generated file, because
    # HEADER supplies the docstring and the frontmatter is WebUI tool metadata rather than
    # Python. Dropping it here avoids having to guess where a docstring ends.
    if rest and isinstance(rest[0], ast.Expr) and isinstance(rest[0].value, ast.Constant):
        rest.pop(0)

    singleton = '_builtin_tools'
    assign = ast.Assign(
        targets=[ast.Name(id=singleton, ctx=ast.Store())],
        value=ast.Call(func=ast.Name(id='Tools', ctx=ast.Load()), args=[], keywords=[]),
    )

    wrappers = [
        build_wrapper(item, singleton) for item in tools_cls.body if is_public_tool_method(item)
    ]

    # Ordering is expressed as an AST rather than by splicing unparsed text. That matters:
    # a module docstring must be first, `from __future__` must be second, and everything else
    # follows. Getting that wrong is a compile error, and fleet_ssh genuinely has a future
    # import, so this is a real case rather than a hypothetical one.
    header_doc = ast.Expr(value=ast.Constant(value=HEADER.format(
        name=name, tool_id=tool_id, orig_name=orig_name, funcs=', '.join(sorted(funcs))
    )))

    new_module = ast.Module(
        body=[header_doc]
        + future
        + rest
        + [tools_cls, ast.fix_missing_locations(assign)]
        + wrappers,
        type_ignores=[],
    )
    ast.fix_missing_locations(new_module)
    ast.parse(ast.unparse(new_module))
    return ast.unparse(new_module) + '\n'


def load_exports(snapshot: Path) -> list[dict]:
    tools = []
    for path in sorted(snapshot.glob('tool-*.json')):
        raw = json.loads(path.read_text())
        entry = raw[0] if isinstance(raw, list) else raw
        tools.append(
            {
                'id': entry.get('id'),
                'name': entry.get('name'),
                'content': entry.get('content') or '',
                'specs': [s.get('name') for s in (entry.get('specs') or [])],
            }
        )
    return tools


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--snapshot', required=True, type=Path)
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--manifest', type=Path, default=None)
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict] = {}
    failures: list[tuple[str, str]] = []

    for tool in load_exports(args.snapshot):
        if not tool['content'].strip():
            failures.append((tool['id'], 'empty content'))
            continue
        try:
            converted = convert(
                tool['content'], tool['id'], tool['name'], tool['name'], tool['specs']
            )
            # An unparseable or unimportable module would break the app at startup, so both
            # checks are mandatory rather than nice to have.
            ast.parse(converted)
            compile(converted, f'{tool["id"]}.py', 'exec')
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            failures.append((tool['id'], f'{type(exc).__name__}: {exc}'))
            continue

        slug = tool['id'].replace('custom_', '')
        dest = args.out / f'{slug}.py'
        dest.write_text(converted)

        # Record which wrappers were emitted, so a mismatch against the export's own spec
        # list is visible instead of silent.
        tree = ast.parse(converted)
        emitted = sorted(
            n.name
            for n in tree.body
            if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and not n.name.startswith('_')
        )
        declared = sorted(set(tool['specs']))
        manifest[slug] = {
            'tool_id': tool['id'],
            'name': tool['name'],
            'source': dest.name,
            'specs': declared,
            'exposed': emitted,
            'missing': [s for s in declared if s not in emitted],
        }
        flag = 'OK ' if not manifest[slug]['missing'] else 'WARN'
        print(f'  {flag} {slug:34} {len(converted):7}c  {len(emitted):2} wrapped')
        if manifest[slug]['missing']:
            print(f'        declared but not exposed: {manifest[slug]["missing"]}')

    if args.manifest:
        args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')

    print()
    if failures:
        print(f'FAILED ({len(failures)}):')
        for tool_id, why in failures:
            print(f'  {tool_id}: {why}')
        return 1

    warn = sum(1 for m in manifest.values() if m['missing'])
    print(f'converted {len(manifest)} tools, {warn} with a spec/wrapper mismatch')
    return 0


if __name__ == '__main__':
    sys.exit(main())
