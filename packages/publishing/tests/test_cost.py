"""Cost, the slow tier: the pieces' budgets and the whole-tree scan's, each held at three
times its value so a slower runner does not flake.

| what | budget |
|---|---|
| import of a piece's module | 10 ms, with secrets loaded at first use, not at import |
| message piece | 50 ms for a 4 KB message |
| content piece | 0.3 s for a 1,000-line diff |
| push piece | 2 s for 50 commits |
| `scan` of a 4.2 MB tree | 3 s, with secrets on RE2 |

Each is timed in a fresh interpreter (`cost_driver.py`), as a git hook runs, so a piece's
time takes in its first-use loads. The trees are generated here from a seeded generator:
code-shaped lines that hold nothing a scan refuses. Run with `pytest -m slow
tests/test_cost.py`; `-s` shows the numbers. The generated tree is removed when the
module's tests end.
"""

from __future__ import annotations

import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from moxi_dev.testing import timed_slack
from conftest import git

pytestmark = pytest.mark.slow

DRIVER = Path(__file__).with_name("cost_driver.py")
SLACK = 3.0
IMPORT_BUDGET_S = 0.010
MESSAGE_BUDGET_S = 0.050
CONTENT_BUDGET_S = 0.3
PUSH_BUDGET_S = 2.0
SCAN_BUDGET_S = 3.0
SCAN_TREE_BYTES = 4_200_000

_WORDS = ("the", "path", "return", "self", "data", "import", "def", "for", "value",
          "branch", "record", "with", "read", "write", "when", "rule", "piece", "module",
          "Error", "found", "print", "range", "open", "options", "None", "True", "line",
          "text", "name", "items", "result", "config", "else", "if", "in", "not", "and")
_SHAPES = ("{i}{a} = {b}({c}, {d})", "{i}def {a}_{b}({c}, {d}):", "{i}return {a}.{b}({c})",
           "{i}# {a} {b} {c} {d} {e} {a}", "{i}if {a} is not {b}:", "{i}{a}.{b} = [{c}, {d}]",
           "", "{a} {b} {c} {d} {e}, {a} {c} {d}.", "{i}for {a} in {b}:")


def code_lines(rng: random.Random):
    """Endless code- and prose-shaped lines."""
    while True:
        w = [rng.choice(_WORDS) for _ in range(5)]
        yield rng.choice(_SHAPES).format(i=" " * rng.choice((0, 4, 8)), a=w[0], b=w[1],
                                         c=w[2], d=w[3], e=w[4])


def text_of(rng: random.Random, size: int) -> str:
    out, total, lines = [], 0, code_lines(rng)
    while total < size:
        line = next(lines)
        out.append(line)
        total += len(line) + 1
    return "\n".join(out) + "\n"


def re2_installed() -> bool:
    """Whether google-re2 is installed here: secrets runs every pattern on it when it is."""
    return importlib.util.find_spec("re2") is not None


def drive(cwd: Path, *args: str) -> dict:
    done = subprocess.run([sys.executable, "-I", str(DRIVER), *args], cwd=cwd,
                          capture_output=True, text=True, timeout=120,
                          env={**os.environ, "PYTHONSAFEPATH": "1"})
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def best(cwd: Path, *args: str, runs: int = 3) -> dict:
    """The fastest of `runs` fresh runs: a budget is the piece's cost, not the machine's
    worst moment."""
    results = [drive(cwd, *args) for _ in range(runs)]
    return min(results, key=lambda result: result["s"])


def within(timed_reading, name: str, took: float, budget: float) -> None:
    """Report a piece's time, which its child measured, and hold it under the budget at
    `SLACK` times the run's `timed_slack()`."""
    print(f"{name}: {took * 1000:.1f} ms (budget {budget * 1000:.0f} ms, held at "
          f"{budget * SLACK * timed_slack() * 1000:.0f} ms)")
    timed_reading(took, budget * SLACK, "s", clock="child")
    assert took < budget * SLACK * timed_slack(), (name, took, budget)


@pytest.fixture
def flagged(repo, names):
    """`repo` marked public, with no piece registered, so commits made to set a test up
    run no hook."""
    git(repo, "config", "moxi-publishing.public", "true")
    return repo


@pytest.mark.timed
@pytest.mark.parametrize("module", ["moxi_publishing.message", "moxi_publishing.content",
                                    "moxi_publishing.push"])
def test_a_piece_module_imports_within_budget_without_secrets(repo, module, timed_reading):
    result = best(repo, "import", module)
    assert not result["secrets_loaded"], module
    within(timed_reading, f"import {module}", result["s"], IMPORT_BUDGET_S)


@pytest.mark.timed
def test_the_message_piece_judges_4_kb_within_budget(flagged, timed_reading):
    rng = random.Random(11)
    body = text_of(rng, 4000).replace("#", "-")
    message = flagged / ".git" / "COMMIT_EDITMSG"
    message.write_text("Change the reader\n\n" + body,
                       encoding="utf-8")
    assert 4000 <= message.stat().st_size < 4400
    result = best(flagged, "message", str(message))
    assert result["kind"] == "ok", result
    within(timed_reading, "message piece, 4 KB", result["s"], MESSAGE_BUDGET_S)


@pytest.mark.timed
def test_the_content_piece_judges_1000_lines_within_budget(flagged, timed_reading):
    rng = random.Random(12)
    lines = code_lines(rng)
    for n in range(10):
        path = flagged / "src" / f"part_{n}.py"
        path.parent.mkdir(exist_ok=True)
        path.write_text("\n".join(next(lines) for _ in range(100)) + "\n", encoding="utf-8")
    git(flagged, "add", "src")
    added = git(flagged, "diff", "--cached", "--numstat").stdout.split("\n")
    assert sum(int(row.split("\t")[0]) for row in added if row) == 1000
    result = best(flagged, "content")
    assert result["kind"] == "ok", result
    within(timed_reading, "content piece, 1,000 lines", result["s"], CONTENT_BUDGET_S)


@pytest.mark.timed
def test_the_push_piece_judges_50_commits_within_budget(flagged, remote, timed_reading):
    rng = random.Random(13)
    lines = code_lines(rng)
    base = git(flagged, "rev-parse", "HEAD").stdout.strip()
    for n in range(50):
        path = flagged / "src" / f"step_{n % 7}.py"
        path.parent.mkdir(exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(next(lines) for _ in range(20)) + "\n")
        git(flagged, "add", "src")
        git(flagged, "commit", "-q", "--no-verify", "-m", f"Step {n}: change the reader")
    tip = git(flagged, "rev-parse", "HEAD").stdout.strip()
    assert git(flagged, "rev-list", "--count", f"{base}..{tip}").stdout.strip() == "50"
    # A branch other than main: main's move also waits on GitHub's runs, which isn't the
    # piece's own cost.
    line = f"refs/heads/topic {tip} refs/heads/topic {'0' * 40}"
    result = best(flagged, "push", "origin", str(remote), line)
    assert result["kind"] == "ok", result
    within(timed_reading, "push piece, 50 commits", result["s"], PUSH_BUDGET_S)


@pytest.fixture(scope="module")
def generated_tree(tmp_path_factory, template):
    """A repo whose one commit holds a generated 4.2 MB tree: about 1,300 files in nested
    folders, like a source repo's. The tree is removed when the module's tests end."""
    tree_dir = tmp_path_factory.mktemp("tree")
    where = tree_dir / "proj-b"
    shutil.copytree(template, where, symlinks=True)
    rng = random.Random(14)
    total = 0
    n = 0
    while total < SCAN_TREE_BYTES:
        size = rng.choice((800, 1600, 3200, 6400))
        path = where / f"pkg_{n % 9}" / f"mod_{n % 37}" / f"file_{n}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = text_of(rng, size)
        path.write_text(data, encoding="utf-8")
        total += len(data.encode("utf-8"))
        n += 1
    env = {"GIT_CONFIG_GLOBAL": str(template.parent / "gitconfig"),
           "GIT_CONFIG_NOSYSTEM": "1"}
    git(where, "add", "-A", env=env)
    git(where, "commit", "-q", "--no-verify", "-m", "Generated tree", env=env)
    yield where
    shutil.rmtree(tree_dir)


@pytest.mark.timed
@pytest.mark.parametrize("ci", [False, True], ids=["local", "ci"])
def test_scan_of_a_4_mb_tree_within_budget(generated_tree, names, gitenv, ci,
                                           timed_reading):
    if not re2_installed():
        pytest.skip("the scan's budget is set on RE2, which isn't installed here")
    tracked = git(generated_tree, "ls-files", "-z").stdout.split("\0")
    size = sum((generated_tree / path).stat().st_size for path in tracked if path)
    assert SCAN_TREE_BYTES <= size < SCAN_TREE_BYTES * 1.01
    result = best(generated_tree, "scan", *(("--ci",) if ci else ()), runs=2)
    assert result["hits"] == 0, result
    within(timed_reading, f"scan{' --ci' if ci else ''} of {size / 1e6:.1f} MB",
           result["s"], SCAN_BUDGET_S)
