"""`moxi-dev timed`, its record, `take` and `show`.

The verb runs on tiny workspaces in git repositories under tmp with the step runner
replaced: a stand-in answers `uv` and writes each pytest session's `durations.json`, so
no environment is synced and no test is timed.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

from moxi_dev import tick, timed, timed_record, timed_show
from moxi_dev.testing import (DURATIONS_ENV, IDLE_ENV, TIERS_ENV, TIMED_ENV,
                              TIMED_LOCK_FD_ENV, TIMED_SLACK_ENV)
from test_check import LAYOUT, git, write_workspace

SUMMARY = (r"moxi-dev timed: commit [0-9a-f]{12}(\+dirty)? on [A-Za-z0-9._-]+ \| "
           r"(passed \d+, failed \d+, skipped \d+, not measured \d+( \(.*\))?|already measured)"
           r" \| total \d+\.\d s \| exit \d")


def entry(outcome="passed", *, other=0.1, reading=1.0, budget=2.0, failure=None):
    return {"outcome": outcome, "clock": "wall", "gauge": "stat", "other": other,
            "line": 2.0, "reading": reading, "budget": budget, "unit": "ms",
            "failure": failure, "waited_s": 0.0}


class Stand:
    """The step runner's stand-in: answers `uv`, and for each pytest session writes the
    planned tests' `durations.json` and answers with the planned code."""

    def __init__(self, plan=None, *, lock=0, sync=0, gate=None):
        self.plan = plan or {}
        self.lock, self.sync = lock, sync
        self.gate = gate or {"gauge": "stat", "line": 2.0, "deadline": 0,
                             "first_sample": 0.2, "waited_s": 0.0}
        self.calls = []

    def __call__(self, cmd, cwd, env):
        self.calls.append((list(cmd), Path(cwd), dict(env)))
        if cmd[:2] == ["uv", "lock"]:
            return self.lock
        if cmd[:2] == ["uv", "sync"]:
            return self.sync
        code, tests = self.plan.get(Path(cwd).name, (5, {}))
        body = {"format": 1, "gate": self.gate,
                "tests": {nodeid: {"call": 0.1, "outcome": one["outcome"], "timed": one}
                          for nodeid, one in tests.items()}}
        Path(env[DURATIONS_ENV]).write_text(json.dumps(body))
        return code

    def sessions(self):
        return [(cwd.name, cmd, env) for cmd, cwd, env in self.calls if "pytest" in cmd]


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write_workspace(root, LAYOUT)
    (root / ".gitignore").write_text(".moxi-dev/\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "start")
    return root


def head(root):
    return git(root, "rev-parse", "HEAD").strip()


def record_dir(root):
    return root / ".git" / "moxi-dev" / "timed"


def opts(**kw):
    return timed.Options(**{"machine": "box", **kw})


def go(root, stand, env=None, **kw):
    return timed.run_timed(root, opts(**kw), env={"PATH": os.environ["PATH"], **(env or {})},
                           run=stand, probe=lambda *_: None)


def kept(root, machine="box", commit=None):
    path = record_dir(root) / machine / f"{commit or head(root)}.json"
    return json.loads(path.read_text())


PLAN = {"moxi": (0, {"tests/test_a.py::test_a": entry()}),
        "comms": (0, {"tests/test_b.py::test_b": entry(),
                      "tests/test_c.py::test_c": entry("skipped")})}


# --- the verb --------------------------------------------------------------------------

def test_every_test_passing_or_skipping_exits_0_and_keeps_the_record(repo):
    result = go(repo, Stand(PLAN))
    assert result.code == 0
    assert re.fullmatch(SUMMARY, result.line()), result.line()
    assert f"commit {head(repo)[:12]} on box | passed 2, failed 0, skipped 1, not measured 0" \
        in result.line()
    data = kept(repo)
    assert data["machine"] == "box" and data["commit"] == head(repo)
    assert data["collected"] == sorted(["tests/test_a.py::test_a", "tests/test_b.py::test_b",
                                        "tests/test_c.py::test_c"])
    run = data["runs"][0]
    assert {key: run[key] for key in ("slack", "exit", "kind", "complete", "gauge", "line",
                                      "first_sample", "measured")} == {
        "slack": 1.0, "exit": 0, "kind": "hand", "complete": True, "gauge": "stat",
        "line": 2.0, "first_sample": 0.2, "measured": 2}
    assert run["system"] and run["cpu_model"] and run["cpus"] and run["python"]
    assert data["tests"]["tests/test_b.py::test_b"]["1"]["package"] == "comms"


def test_a_failure_exits_1_and_a_not_measured_test_exits_3(repo):
    failing = {"moxi": (1, {"t::a": entry("failed", failure="assert 3 < 2")})}
    assert go(repo, Stand(failing)).code == 1
    busy = {"moxi": (0, {"t::a": entry("not measured", other=3.0), "t::b": entry()})}
    stand = Stand(busy, gate={"gauge": "stat", "line": 2.0, "deadline": 0,
                              "first_sample": 3.5, "waited_s": 12.0})
    result = go(repo, stand)
    assert result.code == 3
    assert ("not measured 1 (machine busy, other work 3.00 CPUs over 2, waited 12 s)"
            in result.line())


def test_a_session_that_broke_exits_1(repo):
    assert go(repo, Stand({"moxi": (2, {})})).code == 1


@pytest.mark.parametrize("stand", [Stand(PLAN, lock=1), Stand(PLAN, sync=1)])
def test_a_stale_lock_or_a_failed_sync_exits_2_before_any_session(repo, stand):
    assert go(repo, stand).code == 2
    assert stand.sessions() == []


def test_the_verb_syncs_its_own_env_and_runs_each_package_alone(repo):
    stand = Stand(PLAN)
    go(repo, stand, wait_s=60, line=1.5)
    sync = [(cmd, env) for cmd, _, env in stand.calls if cmd[:2] == ["uv", "sync"]]
    assert sync[0][1]["UV_PROJECT_ENVIRONMENT"] == str(repo / ".moxi-dev/timed-env-3.12")
    sessions = stand.sessions()
    assert [name for name, _, _ in sessions] == list(LAYOUT)
    for name, cmd, env in sessions:
        assert cmd[-2:] == ["-n", "0"]
        assert env[TIMED_ENV] == "only" and env[TIERS_ENV] == "all"
        assert Path(env[DURATIONS_ENV]).parent == repo / ".moxi-dev/timed-run"
        line, deadline = env[IDLE_ENV].split("@")
        assert line == "1.5" and abs(float(deadline) - time.time() - 60) < 30
        assert ".moxi-dev/env-3.12" not in env["VIRTUAL_ENV"]
    assert not (repo / ".moxi-dev/run").exists()


def test_a_dirty_tree_keeps_no_record(repo):
    (repo / "README.md").write_text("changed\n")
    result = go(repo, Stand(PLAN))
    assert result.code == 0 and "+dirty" in result.line()
    assert not (record_dir(repo) / "box").exists()


def test_no_record_keeps_none(repo):
    go(repo, Stand(PLAN), keep_record=False)
    assert not (record_dir(repo) / "box").exists()


def test_the_run_lock_comes_first_and_a_second_run_exits_2_without_syncing(repo, capsys):
    lock = record_dir(repo) / ".run-lock"
    lock.parent.mkdir(parents=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        stand = Stand(PLAN)
        assert go(repo, stand).code == 2
        assert stand.calls == []
        assert "another timed run is measuring in this clone" in capsys.readouterr().err
    finally:
        os.close(fd)


def test_a_descriptor_handed_on_the_lock_file_runs_and_one_on_another_file_exits_2(repo,
                                                                                   tmp_path):
    lock = record_dir(repo) / ".run-lock"
    lock.parent.mkdir(parents=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    other = os.open(tmp_path / "other", os.O_RDWR | os.O_CREAT)
    try:
        assert go(repo, Stand(PLAN), env={TIMED_LOCK_FD_ENV: str(fd)}).code == 0
        stand = Stand(PLAN)
        assert go(repo, stand, env={TIMED_LOCK_FD_ENV: str(other)}).code == 2
        assert stand.calls == []
        assert go(repo, Stand(PLAN), env={TIMED_LOCK_FD_ENV: "9999"}).code == 2
    finally:
        os.close(fd)
        os.close(other)


@pytest.fixture
def synced(tmp_path):
    """A run tree with no git: the workspace and a `commit.json` a tick wrote."""
    root = tmp_path / "tree"
    root.mkdir()
    write_workspace(root, LAYOUT)
    where = root / ".moxi-dev" / "timed"
    where.mkdir(parents=True)
    (where / "commit.json").write_text(json.dumps({"format": 1, "commit": "a" * 40}))
    return root


def test_outside_git_the_commit_file_and_record_dir_place_the_record(synced, tmp_path):
    results = tmp_path / "moxi-timed"
    assert go(synced, Stand(PLAN), record_dir=results).code == 0
    assert json.loads((results / "box" / f"{'a' * 40}.json").read_text())["commit"] == "a" * 40
    fd = os.open(synced / ".moxi-dev/timed/.run-lock", os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert go(synced, Stand(PLAN), record_dir=results).code == 2
    finally:
        os.close(fd)


def test_outside_git_no_commit_file_or_no_record_dir_exits_2_before_measuring(synced,
                                                                             tmp_path):
    stand = Stand(PLAN)
    assert go(synced, stand).code == 2
    (synced / ".moxi-dev/timed/commit.json").unlink()
    assert go(synced, stand, record_dir=tmp_path / "x").code == 2
    assert stand.calls == []


def test_machine_files_the_record_defaults_to_the_short_host_name_and_refuses_odd_names(
        repo, monkeypatch):
    go(repo, Stand(PLAN), machine="host-b")
    assert (record_dir(repo) / "host-b" / f"{head(repo)}.json").is_file()
    stand = Stand(PLAN)
    assert go(repo, stand, machine="a/b").code == 2 and stand.calls == []
    monkeypatch.setattr(timed_record.platform, "node", lambda: "alpha.lan")
    assert timed_record.default_machine() == "alpha"
    seen = []
    monkeypatch.setattr(timed, "run_timed", lambda start, o: seen.append(o) or timed.Result())
    timed.main([])
    assert seen[0].machine == "alpha"


def test_a_slack_the_kit_would_refuse_exits_2_and_a_set_one_is_recorded(repo):
    assert go(repo, Stand(PLAN), env={TIMED_SLACK_ENV: "0.5"}).code == 2
    assert go(repo, Stand(PLAN), env={TIMED_SLACK_ENV: "3.1"}).code == 0
    data = kept(repo)
    assert data["runs"][0]["slack"] == 3.1
    assert "3.1" in data["tests"]["tests/test_a.py::test_a"]


def test_the_merge_rule_within_a_factor_and_factors_kept_apart():
    def merged(*outcomes):
        kept_one = None
        for outcome in outcomes:
            kept_one = timed_record.merge_result(kept_one, {"outcome": outcome})
        return kept_one["outcome"]
    assert merged("passed", "failed") == "failed"
    assert merged("failed", "passed") == "passed"
    assert merged("passed", "skipped") == "passed"
    assert merged("not measured", "skipped") == "skipped"
    assert merged("skipped", "skipped") == "skipped"
    assert merged("passed", "not measured") == "passed"
    assert merged("skipped", "not measured") == "skipped"
    assert merged("not measured", "not measured") == "not measured"
    data = timed_record.empty("c" * 40, "box")
    for started, slack, outcome in [("1", 1.0, "passed"), ("2", 2.0, "failed"),
                                    ("3", 1.0, "not measured")]:
        data = timed_record.add_run(data, {"started": started, "slack": slack},
                                    {"t": {"outcome": outcome}}, None)
    assert data["tests"]["t"]["1"]["outcome"] == "passed"
    assert data["tests"]["t"]["2"]["outcome"] == "failed"


def test_collected_comes_only_from_a_complete_run(repo):
    go(repo, Stand(PLAN), packages=["moxi"])
    assert "collected" not in kept(repo)
    assert not kept(repo)["runs"][0]["complete"]
    go(repo, Stand(PLAN))
    whole = kept(repo)["collected"]
    go(repo, Stand({"moxi": (0, {"t::new": entry()})}), packages=["moxi"])
    go(repo, Stand({"moxi": (0, {"t::found": entry()})}), findings=["L1"])
    data = kept(repo)
    assert data["collected"] == whole
    assert "t::new" in data["tests"] and "t::found" in data["tests"]


def test_scheduled_refuses_a_package_or_finding(repo):
    for kw in ({"packages": ["moxi"]}, {"findings": ["L1"]}):
        stand = Stand(PLAN)
        assert go(repo, stand, scheduled=True, **kw).code == 2 and stand.calls == []


def test_scheduled_skips_a_settled_tip_and_measures_an_unsettled_one(repo):
    stand = Stand(PLAN)
    assert go(repo, stand, scheduled=True).code == 0
    assert stand.sessions()
    again = Stand(PLAN)
    result = go(repo, again, scheduled=True)
    assert result.code == 0 and "| already measured |" in result.line()
    assert again.calls == []
    # a pass taken only over this run's factor doesn't settle it at 1
    path = record_dir(repo) / "box" / f"{head(repo)}.json"
    data = json.loads(path.read_text())
    data["tests"]["tests/test_a.py::test_a"] = {"2": {"outcome": "passed", "run": "x"}}
    path.write_text(json.dumps(data))
    stand = Stand(PLAN)
    go(repo, stand, scheduled=True)
    assert stand.sessions()
    # a failure taken over this run's factor does
    data["tests"]["tests/test_a.py::test_a"] = {"2": {"outcome": "failed", "run": "x"}}
    path.write_text(json.dumps(data))
    stand = Stand(PLAN)
    go(repo, stand, scheduled=True)
    assert stand.calls == []


def test_scheduled_measures_a_tip_whose_record_has_no_collected_list(repo):
    go(repo, Stand(PLAN), packages=["moxi", "comms"])
    stand = Stand(PLAN)
    go(repo, stand, scheduled=True)
    assert stand.sessions()
    assert kept(repo)["runs"][-1]["kind"] == "scheduled"


# --- take ------------------------------------------------------------------------------

SHA1, SHA2, SHA3 = "1" * 40, "2" * 40, "3" * 40


def commit_file(machine, sha, runs, tests, collected=None, collected_run=None):
    data = {"format": 1, "commit": sha, "machine": machine, "runs": runs, "tests": tests}
    if collected is not None:
        data.update(collected=collected, collected_run=collected_run)
    return data


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if not isinstance(data, str) else data)


def tick_file(machine="host-b", **kw):
    return {"format": 1, "machine": machine, "started": "2026-11-18T14:00:00Z",
            "tick_version": 1, "commit": None, "steps": {}, "failure": None,
            "not_settled": None, "summary": None, "exit": 0, "invocation": None, **kw}


@pytest.fixture
def host(tmp_path):
    return tmp_path / "moxi-timed-host-b" / "host-b"


@pytest.fixture
def rec(tmp_path):
    return tmp_path / "record"


def run(started):
    return {"started": started, "slack": 3.1, "kind": "scheduled"}


def test_take_adds_merges_and_keeps(host, rec):
    put(host / f"{SHA1}.json", commit_file("host-b", SHA1, [run("a")],
                                           {"t": {"3.1": {"outcome": "passed", "run": "a"}}},
                                           ["t"], "a"))
    put(host / f"{SHA2}.json", commit_file("host-b", SHA2, [run("a"), run("b")],
                                           {"t": {"3.1": {"outcome": "failed", "run": "b"}}},
                                           ["t"], "b"))
    put(rec / "host-b" / f"{SHA2}.json", commit_file(
        "host-b", SHA2, [run("a")], {"t": {"3.1": {"outcome": "passed", "run": "a"}}},
        ["t"], "a"))
    put(rec / "host-b" / f"{SHA3}.json", commit_file("host-b", SHA3, [run("z")], {}))
    put(host / f"{SHA1}.json.part", "half")
    put(host / f".syncthing.{SHA1}.json.tmp", "half")
    code, _ = timed_record.take(host, rec, "host-b", own="alpha")
    assert code == 0
    assert json.loads((rec / "host-b" / f"{SHA1}.json").read_text())["runs"] == [run("a")]
    two = json.loads((rec / "host-b" / f"{SHA2}.json").read_text())
    assert [one["started"] for one in two["runs"]] == ["a", "b"]
    assert two["tests"]["t"]["3.1"]["outcome"] == "failed"
    assert (rec / "host-b" / f"{SHA3}.json").is_file()
    assert not list((rec / "host-b").glob("*.part")) and \
        not list((rec / "host-b").glob(".syncthing*"))


def test_an_older_host_file_with_fewer_runs_loses_none_of_the_record(host, rec):
    put(rec / "host-b" / f"{SHA1}.json", commit_file(
        "host-b", SHA1, [run("a"), run("b")],
        {"t": {"3.1": {"outcome": "failed", "run": "b"}}}, ["t", "u"], "b"))
    put(host / f"{SHA1}.json", commit_file(
        "host-b", SHA1, [run("a")], {"t": {"3.1": {"outcome": "passed", "run": "a"}}},
        ["t"], "a"))
    assert timed_record.take(host, rec, "host-b", own="alpha")[0] == 0
    data = json.loads((rec / "host-b" / f"{SHA1}.json").read_text())
    assert [one["started"] for one in data["runs"]] == ["a", "b"]
    assert data["tests"]["t"]["3.1"]["outcome"] == "failed"
    assert data["collected"] == ["t", "u"]


def test_take_adds_a_tick_file_once_and_reads_only_the_name_form(host, rec):
    put(host / "ticks" / "20261118T140000Z.json", tick_file(exit=2))
    put(host / "ticks" / "2026-11-18T14:00:00Z.json", tick_file())
    put(rec / "host-b" / "ticks" / "20261118T150000Z.json", tick_file(exit=0))
    put(host / "ticks" / "20261118T150000Z.json", tick_file(exit=1))
    assert timed_record.take(host, rec, "host-b", own="alpha")[0] == 0
    ticks = sorted(path.name for path in (rec / "host-b" / "ticks").iterdir())
    assert ticks == ["20261118T140000Z.json", "20261118T150000Z.json"]
    assert json.loads((rec / "host-b/ticks/20261118T150000Z.json").read_text())["exit"] == 0


def test_take_refuses_another_machine_an_unknown_format_and_its_own_name(host, rec):
    put(host / f"{SHA1}.json", commit_file("host-c", SHA1, [], {}))
    put(host / f"{SHA2}.json", {**commit_file("host-b", SHA2, [], {}), "format": 9})
    put(host / f"{SHA3}.json", commit_file("host-b", SHA3, [run("a")], {}))
    put(host / "ticks" / "20261118T140000Z.json", tick_file(format=9))
    code, said = timed_record.take(host, rec, "host-b", own="alpha")
    assert code == 2
    assert sum("not taken" in line for line in said) == 3
    assert (rec / "host-b" / f"{SHA3}.json").is_file()
    assert timed_record.take(host, rec, "alpha", own="alpha")[0] == 2


def test_take_with_nothing_to_take_exits_0_and_with_its_results_dir_gone_exits_2(host, rec):
    assert timed_record.take(host, rec, "host-b", own="alpha") == (2, [
        f"the results folder {host.parent} is gone"])
    host.parent.mkdir()
    code, said = timed_record.take(host, rec, "host-b", own="alpha")
    assert code == 0 and said[0].startswith("nothing to take")
    host.mkdir()
    assert timed_record.take(host, rec, "host-b", own="alpha")[0] == 0


def test_collected_comes_from_the_file_whose_complete_run_is_later():
    mine = commit_file("m", SHA1, [run("c")], {}, ["old"], "a")
    theirs = commit_file("m", SHA1, [run("b")], {}, ["new"], "b")
    assert timed_record.merge_files(mine, theirs)["collected"] == ["new"]
    assert timed_record.merge_files(theirs, mine)["collected"] == ["new"]


def test_take_merges_while_its_parent_holds_the_run_lock(repo, host):
    put(host / f"{SHA1}.json", commit_file("host-b", SHA1, [run("a")], {}))
    lock = record_dir(repo) / ".run-lock"
    lock.parent.mkdir(parents=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        code, _ = timed_record.take(host, record_dir(repo), "host-b", own="alpha")
    finally:
        os.close(fd)
    assert code == 0 and (record_dir(repo) / "host-b" / f"{SHA1}.json").is_file()


def test_the_record_and_the_tick_agree_on_the_tick_files_format():
    assert timed_record.TICK_FORMAT == tick.TICK_FORMAT


# --- show ------------------------------------------------------------------------------

def commits(root, n, *, days_ago=0):
    out = []
    when = int(time.time()) - days_ago * 86400
    for index in range(n):
        env = {**os.environ, "GIT_COMMITTER_DATE": f"{when + index} +0000",
               "GIT_AUTHOR_DATE": f"{when + index} +0000"}
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                        "commit", "-q", "--allow-empty", "-m", f"c{index}"],
                       cwd=root, env=env, check=True)
        out.append(head(root))
    return out[::-1]


def result(outcome, started="r1", **kw):
    return {"outcome": outcome, "run": started, "reading": 1.0, "budget": 2.0,
            "unit": "ms", **kw}


def machine_file(rec, machine, sha, tests, *, runs=None, collected=None):
    runs = runs or [{"started": "r1", "slack": 1.0, "kind": "hand"}]
    put(rec / machine / f"{sha}.json",
        commit_file(machine, sha, runs, tests, collected, runs[-1]["started"]))


def shown(repo, rec, **kw):
    code, reports, _ = timed_show.show(repo, rec, "main", own="alpha", **kw)
    return code, {report["machine"]: report for report in reports}, reports


def test_each_machine_on_its_own_its_own_first_and_machine_picks_one(repo, rec):
    tip = commits(repo, 1)[0]
    machine_file(rec, "alpha", tip, {"t": {"1": result("passed")}}, collected=["t"])
    machine_file(rec, "host-b", tip, {"t": {"1": result("failed")}}, collected=["t"])
    code, by, reports = shown(repo, rec)
    assert [report["machine"] for report in reports] == ["alpha", "host-b"]
    assert by["alpha"]["verdict"] == 0 and by["host-b"]["verdict"] == 1 and code == 1
    assert shown(repo, rec, machine="alpha")[0] == 0


def test_no_complete_run_stays_out_of_the_exit_unless_a_counted_failure(repo, rec):
    tip = commits(repo, 1)[0]
    machine_file(rec, "alpha", tip, {"t": {"1": result("passed")}}, collected=["t"])
    machine_file(rec, "host-b", tip, {"t": {"1": result("not measured")}})
    code, by, _ = shown(repo, rec)
    assert by["host-b"]["verdict"] is None and code == 0
    lines = timed_show.render(list(by.values()), "main", tip, 7, "alpha")
    assert any("no complete run" in line for line in lines)
    machine_file(rec, "alpha", tip, {"t": {"1": result("failed")}})
    assert shown(repo, rec)[0] == 1
    machine_file(rec, "alpha", tip, {"t": {"1": result("passed")}})
    assert shown(repo, rec)[0] == 3


SCHED = [{"started": "r0", "slack": 3.1, "kind": "scheduled"}]


@pytest.mark.parametrize("factor, outcome, code", [
    ("3.1", "passed", 0), ("1", "passed", 0), ("4", "passed", 3),
    ("3.1", "failed", 1), ("4", "failed", 1), ("1", "failed", 3)])
def test_a_result_counts_against_the_machines_set_factor(repo, rec, factor, outcome, code):
    tip = commits(repo, 1)[0]
    machine_file(rec, "host-b", tip, {"t": {factor: result(outcome)}},
                 runs=[*SCHED, {"started": "r1", "slack": float(factor), "kind": "hand"}],
                 collected=["t"])
    got, by, _ = shown(repo, rec)
    assert by["host-b"]["set_factor"] == 3.1
    assert got == code


def test_with_no_scheduled_run_a_pass_at_2_is_marked_and_an_older_pass_at_1_counts(repo,
                                                                                   rec):
    old, tip = commits(repo, 2)[::-1]
    machine_file(rec, "alpha", old, {"t": {"1": result("passed", "r0")}}, collected=["t"])
    machine_file(rec, "alpha", tip, {"t": {"2": result("passed")}}, collected=["t"])
    code, by, _ = shown(repo, rec)
    row = by["alpha"]["tests"][0]
    assert by["alpha"]["set_factor"] == 1.0 and code == 0
    assert row["commit"] == old
    machine_file(rec, "alpha", old, {}, collected=["t"])
    code, by, _ = shown(repo, rec)
    assert code == 3 and by["alpha"]["tests"][0]["kept"] == [
        {"outcome": "passed", "slack": 2.0}]
    lines = timed_show.render(list(by.values()), "main", tip, 7, "alpha")
    assert any("passed at slack 2" in line for line in lines)


def test_the_walk_its_window_the_test_set_and_the_failure_range(repo, rec):
    gone = commits(repo, 1, days_ago=10)[0]
    first, passed, failed1, failed2 = commits(repo, 4)[::-1]
    machine_file(rec, "alpha", gone, {"t": {"1": result("failed")}}, collected=["t"])
    machine_file(rec, "alpha", passed, {"t": {"1": result("passed")},
                                         "old": {"1": result("passed")}},
                 collected=["t", "old"])
    machine_file(rec, "alpha", failed1, {"t": {"1": result("failed")}})
    machine_file(rec, "alpha", failed2, {"t": {"1": result("failed")}}, collected=["t"])
    assert gone not in timed_show.walk(repo, "main", 7)
    code, by, _ = shown(repo, rec)
    report = by["alpha"]
    assert code == 1 and [row["test"] for row in report["tests"]] == ["t"]
    row = report["tests"][0]
    assert (row["first_failed"], row["last_passed"]) == (failed1, passed)
    assert report["newest_commit"] == failed2 and report["behind"] == 0
    assert first


def test_each_machines_newest_tick_and_an_older_tick_version(repo, rec, monkeypatch):
    tip = commits(repo, 1)[0]
    machine_file(rec, "host-b", tip, {"t": {"1": result("passed")}}, collected=["t"])
    put(rec / "host-b" / "ticks" / "20261118T140000Z.json",
        tick_file(exit=2, failure="moxi-dev timed tick: host-b: lock failed: held"))
    put(rec / "host-b" / "ticks" / "20261118T150000Z.json",
        tick_file(exit=3, not_settled="differs README.md"))
    monkeypatch.setattr(tick, "TICK_VERSION", 2)
    _, by, _ = shown(repo, rec)
    lines = timed_show.render(list(by.values()), "main", tip, 7, "alpha")
    text = "\n".join(lines)
    assert "exit 3; not measured: tree not settled: differs README.md" in text
    assert "tick 1, this moxi-dev's 2; run schedule again on host-b" in text


def test_the_strict_line_states_coverage(repo, rec):
    tip = commits(repo, 1)[0]
    machine_file(rec, "host-b", tip, {name: {"3.1": result("passed")} for name in "abc"},
                 runs=SCHED, collected=["a", "b", "c"])
    _, _, reports = shown(repo, rec)
    assert timed_show.strict_line("alpha", reports, tip).startswith(
        "no strict reading: alpha measured no timed test")
    machine_file(rec, "alpha", tip, {"a": {"1": result("passed")},
                                      "b": {"1": result("not measured")}},
                 collected=["a", "b", "c"])
    _, _, reports = shown(repo, rec)
    assert timed_show.strict_line("alpha", reports, tip) == (
        f"alpha's strict reading covers 1 of 3 tests on the walk to {tip[:12]}; "
        f"the other 2 are read only at the hosts' factors")


def test_the_strict_line_is_left_out_when_machine_names_another(repo, rec, monkeypatch,
                                                                capsys):
    tip = commits(repo, 1)[0]
    for name in ("alpha", "host-b"):
        machine_file(rec, name, tip, {"a": {"1": result("passed")}}, collected=["a"])
    monkeypatch.setattr(timed_record.platform, "node", lambda: "alpha")
    monkeypatch.chdir(repo)
    strict = f"alpha's strict reading covers 1 of 1 tests on the walk to {tip[:12]}"

    def read(*argv):
        assert timed_show.main(["--record-dir", str(rec), *argv]) == 0
        return capsys.readouterr().out

    assert strict not in read("--machine", "host-b")
    assert "no strict reading" not in read("--machine", "host-b")
    assert json.loads(read("--machine", "host-b", "--json"))["strict"] is None
    assert strict in read("--machine", "alpha")
    assert strict in read()
    assert json.loads(read("--json"))["strict"].startswith(strict)


def test_no_record_on_the_walk_exits_3(repo, rec):
    commits(repo, 1)
    assert shown(repo, rec)[0] == 3


# --- show --package --------------------------------------------------------------------

def two_packages(rec, tip):
    """alpha with a complete run, host-b with none; each holds a comms test that passed
    and a sessions test that failed."""
    machine_file(rec, "alpha", tip, {"c1": {"1": result("passed", package="comms")},
                                      "s1": {"1": result("failed", package="sessions")}},
                 collected=["c1", "s1"])
    machine_file(rec, "host-b", tip, {"c2": {"1": result("passed", package="comms")},
                                       "s2": {"1": result("failed", package="sessions")}})


def test_package_reads_only_its_own_tests_on_every_machine(repo, rec):
    tip = commits(repo, 1)[0]
    two_packages(rec, tip)
    assert shown(repo, rec)[0] == 1
    code, by, _ = shown(repo, rec, package="comms")
    assert [row["test"] for row in by["alpha"]["tests"]] == ["c1"]
    assert [row["test"] for row in by["host-b"]["tests"]] == ["c2"]
    # Another package's counted failure, on the machine with a complete run and on the
    # one without, leaves this package's exit 0.
    assert by["alpha"]["verdict"] == 0 and by["host-b"]["verdict"] is None and code == 0
    code, by, _ = shown(repo, rec, package="sessions")
    assert [row["test"] for row in by["alpha"]["tests"]] == ["s1"]
    assert by["alpha"]["verdict"] == 1 and by["host-b"]["verdict"] == 1 and code == 1


def test_package_reads_the_same_by_either_of_its_names(repo, rec):
    tip = commits(repo, 1)[0]
    two_packages(rec, tip)
    assert shown(repo, rec, package="moxi-comms") == shown(repo, rec, package="comms")


def test_a_package_name_naming_no_workspace_package_exits_2(repo, rec, monkeypatch, capsys):
    tip = commits(repo, 1)[0]
    two_packages(rec, tip)
    with pytest.raises(timed_show.ShowError, match="names no workspace package: nope"):
        shown(repo, rec, package="nope")
    monkeypatch.chdir(repo)
    assert timed_show.main(["--record-dir", str(rec), "--package", "nope"]) == 2
    assert capsys.readouterr().err == (
        "moxi-dev timed show: --package names no workspace package: nope\n")


def test_the_strict_line_counts_the_package_s_tests_alone(repo, rec):
    tip = commits(repo, 1)[0]
    machine_file(rec, "alpha", tip, {"c1": {"1": result("passed", package="comms")},
                                      "c2": {"1": result("not measured", package="comms")},
                                      "s1": {"1": result("passed", package="sessions")},
                                      "s2": {"1": result("passed", package="sessions")}},
                 collected=["c1", "c2", "s1", "s2"])
    _, _, reports = shown(repo, rec, package="comms")
    assert timed_show.strict_line("alpha", reports, tip, "comms") == (
        f"alpha's strict reading covers 1 of 2 tests on the walk to {tip[:12]}; "
        f"the other 1 are read only at the hosts' factors")


def test_a_package_with_no_collected_test_has_nothing_to_read(repo, rec):
    tip = commits(repo, 1)[0]
    two_packages(rec, tip)
    nothing = (f"turns has no timed test collected by a complete run on the walk to "
               f"{tip[:12]}; nothing to read")
    code, by, reports = shown(repo, rec, package="turns")
    assert code == 0 and all(not report["tests"] for report in reports)
    lines = timed_show.render(reports, "main", tip, 7, "alpha", package="turns")
    assert lines[1] == nothing
    assert not any(line.startswith("  passed") or line.startswith("  failed")
                   for line in lines)
    # With no complete run on any machine the record can't tell a package with no timed
    # test from one never measured.
    machine_file(rec, "alpha", tip, {"c1": {"1": result("passed", package="comms")}})
    code, _, reports = shown(repo, rec, package="turns")
    assert code == 3
    assert timed_show.strict_line("alpha", reports, tip, "turns") == nothing


def test_a_machine_s_newest_measured_commit_is_its_package_s_newest(repo, rec):
    old, tip = commits(repo, 2)[::-1]
    machine_file(rec, "alpha", old, {"c1": {"1": result("passed", package="comms")},
                                      "s1": {"1": result("passed", package="sessions")}},
                 collected=["c1", "s1"])
    # A hand run of another package alone at the tip.
    machine_file(rec, "alpha", tip, {"s1": {"1": result("passed", package="sessions")}})
    machine_file(rec, "host-b", tip, {"s2": {"1": result("passed", package="sessions")}},
                 collected=["s2"])
    _, by, _ = shown(repo, rec)
    assert (by["alpha"]["newest_commit"], by["alpha"]["behind"]) == (tip, 0)
    _, by, reports = shown(repo, rec, package="comms")
    assert (by["alpha"]["newest_commit"], by["alpha"]["behind"]) == (old, 1)
    assert (by["host-b"]["newest_commit"], by["host-b"]["behind"]) == (None, None)
    lines = timed_show.render(reports, "main", tip, 7, "alpha", package="comms")
    assert (f"alpha: set factor 1 | newest measured commit {old[:12]}, 1 commits behind"
            in lines)
    assert "host-b: set factor 1 | no result of comms on the walk" in lines
    # A package no machine has a result of, read from a machine with no record: the strict
    # line still picks a set among machines with no newest commit.
    _, _, reports = shown(repo, rec, package="turns")
    assert all(report["newest_commit"] is None for report in reports)
    assert timed_show.strict_line("gamma", reports, tip, "turns").startswith(
        "turns has no timed test collected by a complete run")

def test_json_carries_the_package(repo, rec, monkeypatch, capsys):
    tip = commits(repo, 1)[0]
    two_packages(rec, tip)
    monkeypatch.setattr(timed_record.platform, "node", lambda: "alpha")
    monkeypatch.chdir(repo)
    assert timed_show.main(["--record-dir", str(rec), "--package", "comms", "--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["package"] == "comms"
    assert [row["test"] for row in body["machines"][0]["tests"]] == ["c1"]
    assert timed_show.main(["--record-dir", str(rec), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["package"] is None


def test_a_timed_run_keeps_its_temp_on_disk(repo, monkeypatch, capsys):
    """Its record is what checks, which keep their temp on disk, are held to."""
    from moxi_dev import temp

    linux = temp.Places(platform="linux", mountinfo=str(repo / "no-mountinfo"))
    monkeypatch.setattr(temp, "_PLACES", linux)
    stand = Stand(PLAN)
    assert go(repo, stand, env={"MOXI_TEST_TEMP": "ram"}).code == 0
    assert stand.sessions() and all(env["MOXI_TEST_TEMP"] == "disk"
                                    for _, _, env in stand.sessions())
    assert "moxi-dev timed: temp disk (ram asked; a check keeps its temp on disk)" \
        in capsys.readouterr().out
    bad = go(repo, Stand(PLAN), env={"MOXI_TEST_TEMP": "RAM"})
    assert bad.code == 2
    assert "MOXI_TEST_TEMP must be disk or ram" in capsys.readouterr().err
