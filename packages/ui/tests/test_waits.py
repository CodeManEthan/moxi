"""Every `wait_for_function` in the repository's Python waits on a function.

The shell's pages allow no eval (`script-src 'self'`). Playwright evaluates the predicate's
text with `eval` in the page. A text that evaluates to a function (`"() => ..."`) is
evaluated once, inside Playwright's own call, which the policy lets through, and the
function is called on each poll after. A bare expression (`"window.done"`) is evaluated on
every poll, and every poll after the first runs outside that call, so the policy refuses
it with an EvalError. Such a wait passes only while its condition already holds at the
first poll, which a loaded machine makes false now and then.

The check reads each package's `src/` and `tests/` and refuses a predicate that is not a
function, or that it cannot read: a string or f-string written in the call, or a name
assigned one at the top of the same file.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

#: The repository's packages, beside this one.
PACKAGES = Path(__file__).resolve().parents[2]

#: Text that evaluates to a function: an arrow function or a function expression.
FUNCTION = re.compile(r"\s*(async\s+)?(function\b|\([^()]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)")


def _text(node: ast.expr, constants: dict[str, ast.expr]) -> str | None:
    """The start of the predicate's text, or None when the check cannot read it."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        first = node.values[0] if node.values else None
        return first.value if isinstance(first, ast.Constant) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _text(node.left, constants)
    if isinstance(node, ast.Name) and node.id in constants:
        return _text(constants[node.id], constants)
    return None


def _constants(tree: ast.Module) -> dict[str, ast.expr]:
    found = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                and isinstance(stmt.targets[0], ast.Name):
            found[stmt.targets[0].id] = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) \
                and stmt.value is not None:
            found[stmt.target.id] = stmt.value
    return found


def eval_waits(source: str, path: str = "<source>") -> list[str]:
    """Each `wait_for_function` call in `source` whose predicate is not a function."""
    tree = ast.parse(source, path)
    constants = _constants(tree)
    refused = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "wait_for_function"):
            continue
        given = node.args[0] if node.args else next(
            (k.value for k in node.keywords if k.arg == "expression"), None)
        text = None if given is None else _text(given, constants)
        if text is None:
            refused.append(f"{path}:{node.lineno}: a predicate this check cannot read")
        elif not FUNCTION.match(text):
            refused.append(f"{path}:{node.lineno}: not a function: {text.strip()[:60]!r}")
    return refused


def test_a_bare_expression_is_refused_and_a_function_is_not():
    source = '''
DONE = "() => window.done"
LATER = """(mark) => {
  return window.marks[mark];
}"""
page.wait_for_function("window.done")
page.wait_for_function("() => window.done")
page.wait_for_function("async () => await window.ready()")
page.wait_for_function("mark => window.marks[mark]", arg="m")
page.wait_for_function("function () { return window.done; }")
page.wait_for_function(DONE)
page.wait_for_function(LATER, arg="m", timeout=5)
page.wait_for_function(f"() => document.querySelector('{tag}') != null")
page.wait_for_function(expression="window.done !== undefined")
page.wait_for_function("(window.done)")
page.wait_for_function(helpers.PREDICATE)
'''
    assert eval_waits(source) == [
        "<source>:6: not a function: 'window.done'",
        "<source>:14: not a function: 'window.done !== undefined'",
        "<source>:15: not a function: '(window.done)'",
        "<source>:16: a predicate this check cannot read",
    ]


def test_every_wait_for_function_in_the_repository_waits_on_a_function():
    files = [path for package in PACKAGES.iterdir() for part in ("src", "tests")
             for path in sorted((package / part).rglob("*.py"))]
    assert Path(__file__).resolve() in files, PACKAGES
    refused = [line for path in files
               for line in eval_waits(path.read_text(encoding="utf-8"),
                                      str(path.relative_to(PACKAGES)))]
    assert not refused, ("a `wait_for_function` predicate must be a function "
                         "(\"() => ...\"); a bare expression is evaluated on every poll, "
                         "which the pages' policy refuses:\n" + "\n".join(refused))
