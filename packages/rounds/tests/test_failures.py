"""A red run's failures: the tests a failed job's log names, the target's own among a
candidate's, the halves of a split, and `QueueCi.failures` reading them with gh."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from moxi_rounds import ci, failures, queue_ci

SCOPED_ONE = {"job": "scoped", "test": "tests/test_a.py::test_one"}


def test_a_log_names_each_jobs_failed_tests_once_without_colour():
    text = "\n".join([
        "linux\tRun tests\t2026-11-22T10:00:00Z FAILED tests/test_a.py::test_one - boom",
        "linux\tRun tests\t2026-11-22T10:00:01Z [gw0] \x1b[31mFAILED\x1b[0m "
        "tests/test_a.py::test_one",
        "linux\tRun tests\t2026-11-22T10:00:02Z FAILED tests/test_b.py::test_two[x]",
        "firefox\tRun\t2026-11-22T10:00:03Z Error: the browser closed",
        "a line with no tabs FAILED tests/test_c.py::test_three",
    ])
    assert failures.tests_of_log(text) == {
        "linux": ["tests/test_a.py::test_one", "tests/test_b.py::test_two[x]"]}


def test_a_runs_failures_are_its_tests_or_the_job_alone():
    jobs = [{"name": "linux", "status": "completed", "conclusion": "failure"},
            {"name": "firefox", "status": "completed", "conclusion": "timed_out"},
            {"name": "macos", "status": "completed", "conclusion": "cancelled"},
            {"name": "lint", "status": "completed", "conclusion": "success"}]
    failed = failures.failed_jobs(jobs)
    assert failed == ["linux", "firefox"]
    assert failures.failed_jobs(jobs, ["firefox"]) == ["firefox"]
    assert failures.of_run(failed, {"linux": ["t::a", "t::b"]}) == [
        {"job": "linux", "test": "t::a"}, {"job": "linux", "test": "t::b"},
        {"job": "firefox", "test": None}]


def test_the_targets_own_are_compared_by_test_across_jobs_and_by_job_alone():
    target = [{"job": "linux", "test": "tests/test_a.py::test_one"},
              {"job": "firefox", "test": None}]
    candidate = [SCOPED_ONE, {"job": "scoped", "test": "tests/test_a.py::test_new"},
                 {"job": "firefox", "test": None},
                 {"job": "firefox", "test": "tests/test_page.py::test_x"},
                 {"job": "local", "test": None}]
    assert failures.new(candidate, target) == [
        {"job": "scoped", "test": "tests/test_a.py::test_new"},
        {"job": "local", "test": None}]


def test_halves_split_in_order_the_first_the_larger():
    assert failures.halves([1, 2, 3]) == ([1, 2], [3])
    assert failures.halves([1, 2]) == ([1], [2])
    assert failures.said([SCOPED_ONE, {"job": "local", "test": None}]) == (
        "scoped: tests/test_a.py::test_one, local")


def test_queue_ci_reads_failures_from_the_jobs_and_the_failed_log(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    jobs = [{"name": "scoped", "status": "completed", "conclusion": "failure"},
            {"name": "lint", "status": "completed", "conclusion": "success"}]
    log = "scoped\tTest\t2026-11-22T10:00:00Z FAILED tests/test_a.py::test_one - boom\n"
    gh = bin_dir / "gh"
    gh.write_text(f"#!{sys.executable}\nimport sys\n"
                  f"if '--log-failed' in sys.argv: print({log!r})\n"
                  f"else: print({json.dumps(json.dumps(jobs))})\n", encoding="utf-8")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{Path(sys.executable).parent}")
    found = ci.Declared(workflows=(ci.Workflow("ci.yml"),), local=None,
                        local_timeout_s=None, remote="origin", status=ci.STATUS,
                        ids=ci.IDS, status_timeout_s=10)
    read = queue_ci.Read(ci.RED, ("7",), ({"path": "ci.yml", "reading": ci.RED},))
    got = queue_ci.QueueCi(found, local_log=tmp_path / "local.log").failures(
        tmp_path, "a" * 40, read)
    assert got == [SCOPED_ONE]
