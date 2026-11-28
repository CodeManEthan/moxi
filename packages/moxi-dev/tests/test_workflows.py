"""The repository's two check workflows: ci.yml, the full check, and queue.yml, the
scoped check of a merge queue's candidate on a `ci/queue-` branch.

The files are read as text, a job by its two-space key under `jobs:`, so no YAML
library is needed. The tests need the repository; an sdist has no `.github`.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO / ".github" / "workflows"

pytestmark = pytest.mark.skipif(not (WORKFLOWS / "queue.yml").is_file(),
                                reason="not run from the repository (an sdist has no "
                                       ".github)")

#: A candidate's branch: main's head's first 12 hex digits, then the batch's number.
CANDIDATE = "ci/queue-0123456789ab-1"


def text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def jobs(name: str) -> dict[str, str]:
    """Each job of the workflow `name` by its key, with the lines of its block."""
    found: dict[str, list[str]] = {}
    current = None
    inside = False
    for line in text(name).splitlines():
        if line == "jobs:":
            inside = True
            continue
        if not inside:
            continue
        key = re.fullmatch(r"  ([A-Za-z0-9_-]+):", line)
        if key:
            current = key.group(1)
            found[current] = []
        elif current is not None:
            found[current].append(line)
    return {key: "\n".join(lines) for key, lines in found.items()}


def on_push(name: str) -> str:
    """The workflow's `on:` block."""
    return re.search(r"^on:\n((?:  .*\n)+)", text(name), re.MULTILINE).group(1)


def browser_packages_of_ci() -> list[str]:
    """The packages ci.yml's browser jobs run, in its order."""
    line = re.search(r"^        package: \[([^\]]*)\]$", jobs("ci.yml")["browser"],
                     re.MULTILINE).group(1)
    return [one.strip() for one in line.split(",")]


def browser_packages_of_queue() -> list[str]:
    line = re.search(r"^          BROWSER: (.*)$", jobs("queue.yml")["pick"],
                     re.MULTILINE).group(1)
    return line.split()


def test_ci_runs_on_every_branch_but_a_candidate_s():
    assert on_push("ci.yml") == '  push:\n    branches-ignore: ["ci/queue-*"]\n'


def test_queue_runs_only_on_a_candidate_s_branch():
    assert on_push("queue.yml") == '  push:\n    branches: ["ci/queue-*"]\n'
    assert re.search(r"^name: queue$", text("queue.yml"), re.MULTILINE)


def test_no_job_of_queue_is_named_linux():
    found = jobs("queue.yml")
    assert set(found) == {"pick", "scoped", "web", "browser"}
    for key, block in found.items():
        assert not key.startswith("linux")
        named = re.search(r"^    name: (.*)$", block, re.MULTILINE)
        assert named is None or "linux" not in named.group(1)


def test_the_pick_job_reads_the_base_from_the_branch_and_prints_the_pick():
    pick = jobs("queue.yml")["pick"]
    assert '"${GITHUB_REF_NAME#ci/queue-}"' in pick and "[0-9a-f]{12}" in pick
    assert "moxi-dev check --pick --with-dependents --base \"$BASE\"" in pick
    assert 'echo "picked: $picked"' in pick
    # What the pick hands the browser jobs, under their name pattern, so a reader of the
    # run tells a browser job the pick left out from one that never started.
    assert 'echo "picked for browser (*): $browser"' in pick
    scoped = jobs("queue.yml")["scoped"]
    assert "needs: pick" in scoped
    assert 'moxi-dev check --with-dependents --base "$BASE"' in scoped
    assert "BASE: ${{ needs.pick.outputs.base }}" in scoped


def test_a_pushed_branch_checks_with_the_dependents_and_the_full_check_takes_no_flag():
    linux = jobs("ci.yml")["linux"]
    assert "run: uv run --locked moxi-dev check --with-dependents --base origin/main" in linux
    full = [line for line in text("ci.yml").splitlines() if "moxi-dev check --full" in line]
    assert len(full) == 2
    assert not any("--with-dependents" in line for line in full)


def test_the_browser_jobs_run_the_picked_packages_ci_runs_and_skip_when_none():
    assert browser_packages_of_queue() == browser_packages_of_ci()
    browser = jobs("queue.yml")["browser"]
    assert "package: ${{ fromJSON(needs.pick.outputs.browser) }}" in browser
    assert "needs.pick.outputs.browser != '[]'" in browser
    assert "engine: [chromium, firefox]" in browser


def test_the_browser_and_web_steps_are_ci_s():
    ci, queue = jobs("ci.yml"), jobs("queue.yml")

    def steps(block: str) -> list[str]:
        lines = block.split("\n")
        start = lines.index("    steps:")
        return [line for line in lines[start:]
                if line.strip() and not line.strip().startswith("#")]

    assert steps(queue["browser"]) == steps(ci["browser"])
    assert steps(queue["web"]) == steps(ci["web"])
    # ci.yml's web job runs on Linux alone on a ci/ branch; so does the candidate's.
    assert "os: [ubuntu-latest]" in queue["web"]


def _main_ci(monkeypatch, job_names: list[str]) -> list[str]:
    """publishing's gate on moving main, asked of a commit whose one run is a push to
    CANDIDATE with the jobs `job_names`, each concluded success. The fake answers every
    workflow's runs with that run, so the gate's reading of the jobs alone decides."""
    from moxi_publishing import push, tags

    table = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    own = table["tool"][tags.TABLE]
    commit = "c" * 40

    class Fake(tags.Web):
        def fetch(self, url, headers):
            if "/actions/workflows/" in url:
                body = {"workflow_runs": [{"id": 7, "head_sha": commit, "event": "push",
                                           "head_branch": CANDIDATE}]}
            elif "/actions/runs/7/jobs" in url:
                body = {"jobs": [{"name": name, "conclusion": "success",
                                  "status": "completed"} for name in job_names]}
            else:
                return 404, b""
            return 200, json.dumps(body).encode()

    def release_table(git, at):
        return {tags.CHECK_WORKFLOW: own[tags.CHECK_WORKFLOW],
                tags.CHECK_JOB: own[tags.CHECK_JOB]}

    monkeypatch.setattr(tags, "release_table", release_table)
    return push.main_ci(None, commit, main="main", patterns=["ci/*"], github="owner/repo",
                        web=Fake(github_api="http://github.example.test"))


def test_a_candidate_s_run_does_not_let_main_move(monkeypatch):
    names = ["pick", "scoped", "web (ubuntu-latest)",
             *(f"browser ({package}, {engine})" for package in browser_packages_of_queue()
               for engine in ("chromium", "firefox"))]
    [why] = _main_ci(monkeypatch, names)
    assert "has linux concluded success" in why
    # The same run with ci.yml's linux job lets it move: the gate reads the job alone.
    assert _main_ci(monkeypatch, [*names, "linux"]) == []
