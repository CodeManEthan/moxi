"""`moxi-dev new <module>`: what it writes, how it names the package in the workspace,
and what it refuses. Every test renders into a tiny workspace in its own tmp, with a
stand-in `uv` on PATH that records how it was called."""

from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import moxi.modules as modules
from moxi_dev import new
from moxi_dev.contract import check

ROOT_PYPROJECT = """\
# The workspace root: a comment that must survive.
[tool.uv.workspace]
members = [
    "packages/moxi",  # the core
    "packages/other",
]

[tool.uv.sources]
moxi = { workspace = true }   # resolved here
moxi-other = { workspace = true }

# The dev tools.
[dependency-groups]
dev = ["moxi-dev"]
"""

FILES = [".gitignore", "LICENSE", "README.md", "conftest.py", "pyproject.toml",
         "src/moxi_sample/__init__.py", "src/moxi_sample/module.py",
         "tests/test_contract.py"]

#: The repository's own LICENSE, which every package carries byte for byte.
LICENSE = Path(__file__).resolve().parents[3] / "LICENSE"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    (root / "packages" / "moxi").mkdir(parents=True)
    (root / "packages" / "other").mkdir()
    (root / "pyproject.toml").write_text(ROOT_PYPROJECT)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "uv.calls"
    uv = bin_dir / "uv"
    uv.write_text(f'#!/bin/sh\necho "$PWD $*" >> {calls}\nexit "${{UV_EXIT:-0}}"\n')
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.chdir(root)
    return root


def calls(root) -> list[str]:
    """Each `uv` call in the workspace at `root`: its cwd and arguments."""
    log = root.parent / "uv.calls"
    return log.read_text().splitlines() if log.exists() else []


def run(*argv: str) -> int:
    return new.main(list(argv))


def rendered(root, module="sample") -> list[str]:
    package = root / "packages" / module
    return sorted(str(p.relative_to(package)) for p in package.rglob("*") if p.is_file())


# --- what it writes ---------------------------------------------------------------------

def test_it_writes_the_package_and_nothing_else(workspace, capsys):
    assert run("sample") == 0
    assert rendered(workspace) == FILES
    assert not (workspace / "packages" / "sample" / ".public-paths").exists()
    package = workspace / "packages" / "sample"
    assert (package / "LICENSE").read_bytes() == LICENSE.read_bytes()
    assert (package / "README.md").read_text().startswith("# moxi-sample\n")
    out = capsys.readouterr().out.splitlines()
    assert sorted(out[:-1]) == [f"packages/sample/{f}" for f in FILES]
    assert out[-1] == ("pyproject.toml: packages/sample added to the workspace members "
                       "and sources")


def test_the_pyproject_declares_the_module(workspace):
    run("sample", "--summary", "Moxi's sample: a test.")
    data = tomllib.loads((workspace / "packages/sample/pyproject.toml").read_text())
    project = data["project"]
    assert project["name"] == "moxi-sample"
    assert project["description"] == "Moxi's sample: a test."
    assert project["requires-python"] == ">=3.12"
    assert project["dependencies"] == ["moxi>=0.1,<0.2"]
    assert "scripts" not in project  # only the core declares the moxi script
    assert project["entry-points"] == {"moxi.modules": {"sample": "moxi_sample.module"}}
    assert project["urls"] == {"Homepage": "https://github.com/moxihq/moxi",
                               "Repository": "https://github.com/moxihq/moxi",
                               "Issues": "https://github.com/moxihq/moxi/issues"}
    assert data["dependency-groups"] == {"dev": ["pytest", "moxi-dev"]}
    assert "tool" not in data or "uv" not in data["tool"]       # the root resolves
    options = data["tool"]["pytest"]["ini_options"]
    assert options["testpaths"] == ["tests"]
    assert options["addopts"] == "-m 'not browser and not slow and not build and not live'"


def test_the_package_loads_the_kit_and_imports_only_names_that_stay(workspace):
    run("sample")
    package = workspace / "packages" / "sample"
    conftest = ast.parse((package / "conftest.py").read_text())
    [assign] = [n for n in conftest.body if isinstance(n, ast.Assign)]
    assert ast.literal_eval(assign.value) == ["moxi_dev.testing"]
    allowed = {"moxi_dev.contract", "moxi_dev.testing", "moxi_sample"}
    for path in package.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                        else [node.module])
                for mod in mods:
                    top = mod.split(".")[0]
                    assert top in sys.stdlib_module_names or mod in allowed \
                        or top == "moxi_sample", (path.name, mod)
    test = (package / "tests" / "test_contract.py").read_text()
    assert "from moxi_dev.contract import check" in test


def test_the_package_keeps_the_module_contract(workspace):
    run("sample")
    package = workspace / "packages" / "sample"
    assert check(package) == []
    git = ["git", "-c", "init.defaultBranch=main"]
    subprocess.run([*git, "init", "-q", str(workspace)], check=True)
    subprocess.run(["git", "-C", str(workspace), "add", "."], check=True)
    assert check(package, staged=True) == []


def test_the_announcement_loads_and_offers_nothing(workspace, tmp_path):
    run("sample")
    package = workspace / "packages" / "sample"
    site = tmp_path / "site"
    for path in (package / "src").rglob("*.py"):
        target = site / path.relative_to(package / "src")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(path.read_text())
    info = site / "moxi_sample-0.1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: moxi-sample\n"
                                   "Version: 0.1.0\n")
    (info / "entry_points.txt").write_text("[moxi.modules]\nsample = moxi_sample.module\n")
    sys.path.insert(0, str(site))
    importlib.invalidate_caches()
    modules.reset()
    try:
        found = modules.load("sample")
        assert found.state == modules.PRESENT, found.reason
        assert found.interfaces == frozenset() and found.api is None and found.cli is None
        assert found.optional == {} and found.summary == "Moxi's sample module."
    finally:
        sys.path.remove(str(site))
        modules.reset()
        for name in [m for m in sys.modules if m.split(".")[0] == "moxi_sample"]:
            del sys.modules[name]


# --- the workspace ------------------------------------------------------------------------

def test_it_names_the_package_in_the_root_and_locks(workspace):
    assert run("sample") == 0
    text = (workspace / "pyproject.toml").read_text()
    data = tomllib.loads(text)
    assert data["tool"]["uv"]["workspace"]["members"] == [
        "packages/moxi", "packages/other", "packages/sample"]
    assert data["tool"]["uv"]["sources"]["moxi-sample"] == {"workspace": True}
    assert data["dependency-groups"] == {"dev": ["moxi-dev"]}
    kept = [line for line in text.splitlines()
            if line not in ('    "packages/sample",', "moxi-sample = { workspace = true }")]
    assert kept == ROOT_PYPROJECT.splitlines()          # every other line, in order
    assert calls(workspace) == [f"{workspace} lock"]


def test_it_finds_the_root_from_below(workspace, monkeypatch):
    monkeypatch.chdir(workspace / "packages" / "other")
    assert run("sample") == 0
    assert rendered(workspace) == FILES
    assert calls(workspace) == [f"{workspace} lock"]


@pytest.mark.parametrize("before, after", [
    ('members = ["packages/moxi"]\n', 'members = ["packages/moxi", "packages/sample"]\n'),
    ('members = ["packages/moxi",]\n', 'members = ["packages/moxi", "packages/sample"]\n'),
    ("members = []\n", 'members = ["packages/sample"]\n'),
    ('members = [\n  "packages/moxi"\n]\n',
     'members = [\n  "packages/moxi",\n  "packages/sample",\n]\n'),
    ('members = [\n  "packages/moxi"  # core\n]\n',
     'members = [\n  "packages/moxi",  # core\n  "packages/sample",\n]\n'),
], ids=["one-line", "trailing-comma", "empty", "no-comma", "comment"])
def test_add_member_keeps_the_array_s_shape(before, after):
    text = "[tool.uv.workspace]\n" + before + "\n[tool.uv.sources]\nmoxi = { workspace = true }\n"
    out = new.add_member(text, "sample")
    assert out == ("[tool.uv.workspace]\n" + after + "\n[tool.uv.sources]\n"
                   "moxi = { workspace = true }\nmoxi-sample = { workspace = true }\n")


def test_add_member_adds_a_sources_table_when_there_is_none():
    out = new.add_member('[tool.uv.workspace]\nmembers = ["packages/moxi"]\n', "sample")
    assert out.endswith('\n\n[tool.uv.sources]\nmoxi-sample = { workspace = true }\n')
    assert tomllib.loads(out)["tool"]["uv"]["sources"] == {"moxi-sample": {"workspace": True}}


def test_a_failed_lock_says_so(workspace, monkeypatch, capsys):
    monkeypatch.setenv("UV_EXIT", "3")
    assert run("sample") == 1
    assert "uv lock failed (exit 3)" in capsys.readouterr().err
    assert rendered(workspace) == FILES


# --- refusals -----------------------------------------------------------------------------

def unchanged(root) -> bool:
    return ((root / "pyproject.toml").read_text() == ROOT_PYPROJECT
            and not (root / "packages" / "sample").exists() and not calls(root))


@pytest.mark.parametrize("module, says", [
    ("check", "one of the core's own verbs"),
    ("installs", "a name the core keeps for itself"),
    ("Bad-Name", "not a Moxi module name"),
    ("other", "the workspace already has moxi-other"),
], ids=["verb", "reserved", "bad-name", "member"])
def test_it_refuses_a_name(workspace, capsys, module, says):
    assert run(module) == 2
    assert says in capsys.readouterr().err
    assert unchanged(workspace)


def test_it_overwrites_nothing(workspace, capsys):
    (workspace / "packages" / "sample").mkdir()
    (workspace / "packages" / "sample" / "conftest.py").write_text("# mine\n")
    assert run("sample") == 2
    assert "overwrites nothing" in capsys.readouterr().err
    assert (workspace / "packages" / "sample" / "conftest.py").read_text() == "# mine\n"
    assert rendered(workspace) == ["conftest.py"]
    assert (workspace / "pyproject.toml").read_text() == ROOT_PYPROJECT


def test_it_refuses_outside_a_workspace(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert run("sample") == 2
    assert "no workspace root" in capsys.readouterr().err


def test_the_summary_is_one_line(workspace, capsys):
    assert run("sample", "--summary", "two\nlines") == 2
    assert "one non-empty line" in capsys.readouterr().err
    assert unchanged(workspace)
