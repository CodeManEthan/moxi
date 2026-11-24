"""A red run's failures: the tests a failed job's log names, the target's own among a
candidate's, the halves of a split, and `QueueCi.failures` reading them with gh."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from moxi_dev.testing import console_script
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
    # A job of the target's that failed with no test named excuses no test of the
    # candidate's, in that job or any other.
    assert failures.new(candidate, target) == [
        {"job": "scoped", "test": "tests/test_a.py::test_new"},
        {"job": "firefox", "test": "tests/test_page.py::test_x"},
        {"job": "local", "test": None}]


#: A whole-tree failure in the candidate's scoped job, which stands for main's linux.
SCOPED_WHOLE = {"job": "scoped", "test": None, "stands_for": "linux"}


def test_a_runs_failures_hold_the_job_their_job_stands_for():
    assert failures.of_run(["scoped", "web"], {"scoped": ["t::a"]},
                           {"scoped": "linux"}) == [
        {"job": "scoped", "test": "t::a", "stands_for": "linux"},
        {"job": "web", "test": None}]


def test_a_whole_tree_failure_is_mains_own_only_where_the_stood_for_job_names_no_test():
    assert failures.new([SCOPED_WHOLE], [{"job": "linux", "test": None}]) == []
    # main's linux red on a test, or green, or red under the candidate's own job name:
    # the candidate's whole-tree failure is its own.
    for target in ([{"job": "linux", "test": "t::a"}], [],
                   [{"job": "scoped", "test": None}]):
        assert failures.new([SCOPED_WHOLE], target) == [SCOPED_WHOLE]


def test_a_failed_test_is_the_targets_own_only_where_the_target_fails_that_test():
    failed = {"job": "scoped", "test": "t::x", "stands_for": "linux"}
    assert failures.new([failed], [{"job": "linux", "test": "t::x"}]) == []
    assert failures.new([failed], [{"job": "web", "test": "t::x"}]) == []
    assert failures.new([failed], [{"job": "linux", "test": "t::y"},
                                   {"job": "scoped", "test": None}]) == [failed]


def test_main_red_on_a_whole_tree_step_in_linux_blames_a_new_test_in_scoped():
    failed = {"job": "scoped", "test": "t::new", "stands_for": "linux"}
    assert failures.new([failed, SCOPED_WHOLE], [{"job": "linux", "test": None}]) == [
        failed]


def test_with_no_stands_for_a_job_is_compared_by_its_own_name():
    scoped = {"job": "scoped", "test": None}
    assert failures.new([scoped], [{"job": "linux", "test": None}]) == [scoped]
    assert failures.new([scoped], [{"job": "scoped", "test": None}]) == []


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
    console_script(bin_dir, "gh", "import sys\n"
                   f"if '--log-failed' in sys.argv: print({log!r})\n"
                   f"else: print({json.dumps(json.dumps(jobs))})\n")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{Path(sys.executable).parent}")
    found = ci.Declared(workflows=(ci.Workflow("ci.yml"),), local=None,
                        local_timeout_s=None, remote="origin", status=ci.STATUS,
                        ids=ci.IDS, status_timeout_s=10)
    read = queue_ci.Read(ci.RED, ("7",), ({"path": "ci.yml", "reading": ci.RED},))
    got = queue_ci.QueueCi(found, local_log=tmp_path / "local.log").failures(
        tmp_path, "a" * 40, read)
    assert got == [SCOPED_ONE]


def test_queue_ci_gives_the_rerun_and_the_failed_log_the_settings_repo(tmp_path,
                                                                        monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.jsonl"
    jobs = [{"name": "linux", "status": "completed", "conclusion": "failure"}]
    console_script(bin_dir, "gh", "import json, sys\n"
                   f"open({str(calls)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
                   f"if '--log-failed' in sys.argv: print('')\n"
                   f"elif 'rerun' not in sys.argv: print({json.dumps(json.dumps(jobs))})\n")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{Path(sys.executable).parent}")
    found = ci.Declared(workflows=(ci.Workflow("ci.yml"),), local=None,
                        local_timeout_s=None, remote="origin",
                        status=(*ci.STATUS, "--repo", "owner/name"), ids=ci.IDS,
                        status_timeout_s=10)
    queue = queue_ci.QueueCi(found, local_log=tmp_path / "local.log")
    assert queue.rerun(tmp_path, ["7"], failed=True).code == 0
    queue.failures(tmp_path, "a" * 40,
                   queue_ci.Read(ci.RED, ("7",), ({"path": "ci.yml", "reading": ci.RED},)))
    ran = [json.loads(line) for line in calls.read_text().splitlines()]
    rerun = next(one for one in ran if "rerun" in one)
    failed_log = next(one for one in ran if "--log-failed" in one)
    for argv in (rerun, failed_log):
        assert argv[-2:] == ["--repo", "owner/name"], argv
    assert queue_ci.gh_repo(found) == ("--repo", "owner/name")


def test_queue_ci_gives_each_failure_the_job_it_stands_for(tmp_path, monkeypatch):
    jobs = [{"name": "scoped", "status": "completed", "conclusion": "failure"},
            {"name": "web (ubuntu-latest)", "status": "completed", "conclusion": "failure"}]
    log = "scoped\tTest\t2026-11-22T10:00:00Z FAILED tests/test_a.py::test_one - boom\n"
    monkeypatch.setattr(ci, "run_jobs", lambda *args, **kwargs: ((), jobs))
    monkeypatch.setattr(queue_ci, "_said", lambda *args, **kwargs: queue_ci.Said(0, log))
    found = ci.Declared(workflows=(ci.Workflow("queue.yml"),), local=None,
                        local_timeout_s=None, remote="origin", status=ci.STATUS,
                        ids=ci.IDS, status_timeout_s=10)
    read = queue_ci.Read(ci.RED, ("7",), ({"path": "queue.yml", "reading": ci.RED,
                                           "stands_for": {"scoped": "linux"}},))
    got = queue_ci.QueueCi(found, local_log=tmp_path / "local.log").failures(
        tmp_path, "a" * 40, read)
    assert got == [{**SCOPED_ONE, "stands_for": "linux"},
                   {"job": "web (ubuntu-latest)", "test": None}]
