"""Gauges over the request check, the route table and the header set: each fails the
day one of these files grows a thing it must not have.

These three files import only the standard library and each other, read no environment
and hold no clock, so the check's answer depends only on its arguments.
"""

import ast
import pathlib
import sys

import moxi_loopback

SRC = pathlib.Path(moxi_loopback.__file__).parent
FILES = [SRC / "check.py", SRC / "routes.py", SRC / "headers.py"]
OWN = {"moxi_loopback.check", "moxi_loopback.routes", "moxi_loopback.headers"}


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ""


def _code_strings(tree):
    """Every string literal the code executes; docstrings are left out."""
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings]


def test_the_package_init_imports_nothing():
    # A process started without `python -P` refuses before it imports anything else, and
    # importing the package must not import anything first.
    assert list(_imports(_tree(SRC / "__init__.py"))) == []


def test_these_files_import_only_the_standard_library_and_each_other():
    for path in FILES:
        for name in _imports(_tree(path)):
            top = name.split(".")[0]
            assert top in sys.stdlib_module_names or name in OWN or top == "__future__", (
                f"{path.name} imports {name}")


def test_these_files_read_no_environment_and_hold_no_clock():
    for path in FILES:
        tree = _tree(path)
        for name in _imports(tree):
            assert name.split(".")[0] not in ("time", "datetime", "os"), f"{path.name}: {name}"
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in ("environ", "getenv", "monotonic", "time"), (
                    f"{path.name} reads {node.attr}")


def test_every_anchored_pattern_anchors_absolutely():
    # Python's `$` matches before a trailing newline, so every pattern uses \A and \Z.
    anchored = 0
    for path in FILES:
        for literal in _code_strings(_tree(path)):
            if "[" not in literal:
                continue
            assert not literal.startswith("^"), f"{path.name}: {literal!r} uses ^"
            assert not literal.endswith("$"), f"{path.name}: {literal!r} uses $"
            if literal.startswith("\\A") or literal.endswith("\\Z"):
                anchored += 1
                assert literal.startswith("\\A") and literal.endswith("\\Z"), (
                    f"{path.name}: {literal!r} is half-anchored")
    assert anchored >= 15


def test_the_token_compare_is_compare_digest_and_never_equals():
    tree = _tree(SRC / "check.py")
    func = next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == "token_matches")
    calls = [node.func.attr for node in ast.walk(func)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
    assert "compare_digest" in calls
    # The only == or != is on lengths and counts, never on two strings.
    for node in ast.walk(func):
        if isinstance(node, ast.Compare) and any(
                isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
            assert isinstance(node.left, ast.Call) and node.left.func.id == "len", (
                "token_matches compares with == or != on something other than a length")


def test_no_port_number_is_a_constant():
    for path in FILES:
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assert "PORT" not in target.id or not isinstance(node.value, ast.Constant)
