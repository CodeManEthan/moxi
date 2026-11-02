"""Page jobs and the harness homes: an act from the page runs as the same verb in a
terminal, or refuses before writing; its record always ends."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import loadout_kit as kit
import pytest

from moxi_loadout import cli, harness, homes, install, ledger, pagejob


@pytest.fixture
def lo(folder):
    kit.configure(folder)
    kit.skill(folder / "skills", "review")
    kit.manifest(folder, "[user]\n")
    return folder


@pytest.fixture
def page(monkeypatch):
    """This process as a page job would run: loadout's marker set (and taken out again
    after the test)."""
    monkeypatch.setenv(homes.MARKER, "test")


def recorded_homes(monkeypatch) -> dict:
    """Run the user's own install once, which records the homes; return them."""
    monkeypatch.delenv(homes.MARKER, raising=False)
    assert install.install().code == 0
    return ledger.read()[homes.KEY]


def end(job: str) -> dict:
    return json.loads(pagejob.end_path(job).read_text())


def manifest_bytes(folder: Path) -> bytes:
    return (folder / "loadout.toml").read_bytes()


# -- the runner -----------------------------------------------------------------------------

GLOBS = ("packages/loadout/src/moxi_loadout/homes.py",
         "packages/loadout/src/moxi_loadout/pagejob.py",
         "packages/loadout/tests/test_pagejob.py")


def hide_git(monkeypatch):
    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda n, *a, **k: None if n == "git"
                        else real(n, *a, **k))


@pytest.mark.parametrize("variable, harness_name", [
    ("CLAUDE_CONFIG_DIR", "Claude Code"), ("CODEX_HOME", "Codex"),
    ("XDG_CONFIG_HOME", "OpenCode"), ("HOME", "the home folder")])
def test_a_job_under_another_home_is_refused_with_nothing_written(
        lo, monkeypatch, tmp_path, page, variable, harness_name):
    recorded_homes(monkeypatch)
    before_ledger = ledger.read()
    before = manifest_bytes(lo)
    monkeypatch.setenv(variable, str(tmp_path / "elsewhere"))
    assert pagejob.run("j1", ["add", "skill", "review"]) == 3
    record = end("j1")
    assert record["exit"] == 3 and record["outcome"] is None
    [line] = record["lines"]
    assert harness_name in line and variable in line and str(tmp_path / "elsewhere") in line
    assert "moxi ui stop" in line and "terminal" in line
    assert manifest_bytes(lo) == before
    assert ledger.read() == before_ledger
    assert not (kit.claude() / "review").exists()


def test_a_job_whose_path_has_no_git_is_refused_when_the_terminals_had_it(
        lo, monkeypatch, page):
    recorded_homes(monkeypatch)
    assert ledger.read()[homes.GIT] is True
    before_ledger = ledger.read()
    before = manifest_bytes(lo)
    hide_git(monkeypatch)
    assert pagejob.run("j1", ["add", "skill", "review"]) == 3
    [line] = end("j1")["lines"]
    assert "git isn't on PATH here, but the last run in a terminal found it" in line
    assert "Set PATH, with git's folder on it," in line and "moxi ui stop" in line
    assert manifest_bytes(lo) == before
    assert ledger.read() == before_ledger
    assert not (kit.claude() / "review").exists()


def test_a_job_with_git_runs_when_the_terminal_had_none(lo, monkeypatch, page):
    real = shutil.which
    hide_git(monkeypatch)
    recorded_homes(monkeypatch)
    monkeypatch.setattr(shutil, "which", real)
    monkeypatch.setenv(homes.MARKER, "test")
    assert pagejob.run("j1", ["add", "skill", "review"]) == 0


@pytest.mark.slow
@pytest.mark.touches(*GLOBS)
def test_a_job_with_no_git_places_nothing_in_a_project_work_tree(
        lo, monkeypatch, tmp_path, page):
    root = tmp_path / "app"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)
    kit.manifest(lo, f'[user]\n[project."{root}"]\nskills = []\n')
    recorded_homes(monkeypatch)
    before = manifest_bytes(lo)
    before_ledger = ledger.read()
    # The job's PATH holds the fake CLIs and nothing else: no git.
    fakes = os.environ["PATH"].split(os.pathsep)[0]
    assert not (Path(fakes) / "git").exists()
    monkeypatch.setenv("PATH", fakes)
    assert pagejob.run("j1", ["add", "skill", "review", f"--project={root}"]) == 3
    assert "git isn't on PATH here" in end("j1")["lines"][0]
    assert manifest_bytes(lo) == before
    assert ledger.read() == before_ledger
    assert not (root / ".claude").exists() and not (root / ".agents").exists()
    assert sorted(p.name for p in root.iterdir()) == [".git"]


def test_a_job_with_no_record_of_the_homes_is_refused(lo, page):
    before = manifest_bytes(lo)
    assert pagejob.run("j1", ["add", "skill", "review"]) == 3
    [line] = end("j1")["lines"]
    assert "moxi loadout install once in a terminal" in line
    assert manifest_bytes(lo) == before
    assert not (kit.claude() / "review").exists()


def test_a_job_runs_the_verb_and_writes_its_outcome(lo, monkeypatch, page):
    first = recorded_homes(monkeypatch)
    monkeypatch.setenv(homes.MARKER, "test")
    assert pagejob.run("j1", ["add", "skill", "review"]) == 0
    record = end("j1")
    assert record["exit"] == 0 and record["outcome"]["exit"] == 0
    assert "listed skill review in user" in record["outcome"]["lines"]
    assert os.readlink(kit.claude() / "review") == str(lo / "skills" / "review")
    assert os.environ[homes.MARKER] == pagejob.CALLER
    assert ledger.read()[homes.KEY] == first
    assert pagejob.read("j1")["state"] == "done"


def test_a_job_that_fails_writes_exit_1(lo, monkeypatch, page):
    recorded_homes(monkeypatch)
    monkeypatch.setenv(homes.MARKER, "test")
    kit.manifest(lo, '[user]\nskills = ["nowhere"]\n')
    assert pagejob.run("j1", ["install"]) == 1
    record = end("j1")
    assert record["exit"] == 1 and record["outcome"]["exit"] == 1


def test_an_error_in_the_check_is_written_as_exit_1(lo, page):
    from moxi_loadout import stores

    stores.state_dir(create=True)
    stores.ledger_path().write_text("not json")
    assert pagejob.run("j1", ["install"]) == 1
    [line] = end("j1")["lines"]
    assert "ledger.json" in line and "can't be read" in line


def test_an_error_outside_the_cli_is_written_as_exit_1(lo, monkeypatch, page):
    recorded_homes(monkeypatch)
    monkeypatch.setenv(homes.MARKER, "test")

    def broken(argv=None, **kw):
        raise RuntimeError("sample failure")

    monkeypatch.setattr(cli, "main", broken)
    assert pagejob.run("j1", ["install"]) == 1
    assert end("j1") == {"exit": 1, "outcome": None,
                         "lines": ["loadout: the page job failed: sample failure"]}


def test_a_verb_that_isnt_an_act_is_refused_as_usage(lo, page):
    assert pagejob.run("j1", ["status"]) == 2
    assert end("j1")["exit"] == 2


HOLD = """
import sys
from moxi import files, paths
with files.lock(paths.core_lock()):
    print("held", flush=True)
    sys.stdin.read()
"""


def test_a_job_that_waits_past_the_core_lock_exits_75_with_the_waiting_line(
        lo, monkeypatch, page):
    recorded_homes(monkeypatch)
    monkeypatch.setenv(homes.MARKER, "test")
    monkeypatch.setattr(install, "CORE_WAIT", 0.3)
    holder = subprocess.Popen([sys.executable, "-c", HOLD], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        assert pagejob.run("j1", ["install"]) == 75
    finally:
        holder.stdin.close()
        holder.wait(timeout=30)
    record = end("j1")
    assert record["exit"] == 75
    assert any("waiting for an install or uninstall" in line
               for line in record["outcome"]["lines"]), record


def test_a_job_with_no_claude_on_path_writes_the_rest(lo, monkeypatch, page):
    import shutil

    recorded_homes(monkeypatch)
    monkeypatch.setenv(homes.MARKER, "test")
    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda n: None if n == "claude" else real(n))
    kit.manifest(lo, '[user]\nskills = ["review"]\nmcp = ["docs"]\n'
                     '[mcp.docs]\ncommand = "uvx"\n')
    pagejob.run("j1", ["install"])
    rows = {(r["kind"], r["name"], r["harness"]): r for r in end("j1")["outcome"]["rows"]}
    standing = rows[("mcp", "docs", "claude")]
    assert standing["state"] == "blocked"
    assert "no claude on PATH" in standing["reason"] and "moxi ui stop" in standing["reason"]
    assert os.readlink(kit.claude() / "review") == str(lo / "skills" / "review")
    assert not harness.claude_json().exists()


def test_a_failure_before_the_verb_is_written_as_exit_1(lo, monkeypatch, page):
    from moxi import log as moxi_log

    def broken(module, level=None):
        raise RuntimeError("sample log failure")

    monkeypatch.setattr(moxi_log, "setup", broken)
    assert pagejob.run("j1", ["install"]) == 1
    assert end("j1") == {"exit": 1, "outcome": None,
                         "lines": ["loadout: the page job failed: sample log failure"]}


def test_an_end_moxi_cant_write_is_written_beside_the_record(lo, monkeypatch, page):
    folder = pagejob.jobs_dir(create=True)
    (folder / "j1.json").write_text(json.dumps({"job": "j1", "argv": ["install"],
                                                "created": time.time()}))
    monkeypatch.chdir(folder.parent)               # where `start` starts the runner

    def broken(*, create=False):
        raise ImportError("sample import failure")

    monkeypatch.setattr(pagejob, "jobs_dir", broken)
    assert pagejob.run("j1", ["install"]) == 1
    record = json.loads((folder / "j1.end.json").read_text())
    assert record == {"exit": 1, "outcome": None,
                      "lines": ["loadout: the page job failed: sample import failure"]}
    assert (folder / "j1.end.json").stat().st_mode & 0o777 == 0o600
    assert sorted(p.name for p in folder.iterdir()) == ["j1.end.json", "j1.json"]


def test_an_end_with_no_record_beside_it_is_not_written_elsewhere(lo, monkeypatch, page,
                                                                  tmp_path):
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / pagejob.JOBS).mkdir(parents=True)
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(pagejob, "jobs_dir", lambda *, create=False: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        pagejob.run("j1", ["install"])
    assert list((elsewhere / pagejob.JOBS).iterdir()) == []


def test_the_runner_prunes_records_older_than_a_day(lo, page):
    folder = pagejob.jobs_dir(create=True)
    old, new = folder / "old.json", folder / "new.json"
    for path in (old, new, folder / "old.end.json", folder / "old.log"):
        path.write_text("{}")
    then = time.time() - pagejob.KEEP_SECONDS - 60
    for path in (old, folder / "old.end.json", folder / "old.log"):
        os.utime(path, (then, then))
    pagejob.run("j1", ["status"])
    assert sorted(p.name for p in folder.iterdir()) == ["j1.end.json", "new.json"]


# -- spawning and reading -------------------------------------------------------------------

def fake_spawn(monkeypatch, pid: int | None = None):
    calls = []

    def spawn(argv, *, cwd, output):
        calls.append((argv, cwd, output))
        return os.getpid() if pid is None else pid

    monkeypatch.setattr(pagejob, "_spawn", spawn)
    return calls


def test_start_creates_the_record_then_writes_the_pid(lo, monkeypatch):
    calls = fake_spawn(monkeypatch)
    started = pagejob.start(["add", "skill", "review"])
    assert started.new and pagejob.valid(started.job)
    [(argv, cwd, _)] = calls
    assert argv[-5:] == ["moxi_loadout.pagejob", started.job, "add", "skill", "review"]
    from moxi_loadout import stores

    assert cwd == os.fspath(stores.state_dir())
    record = json.loads(pagejob.record_path(started.job).read_text())
    assert record["pid"] == os.getpid() and record["started"]
    assert pagejob.read(started.job)["state"] == "running"


def test_a_failed_spawn_leaves_no_record(lo, monkeypatch):
    def spawn(argv, *, cwd, output):
        raise OSError("sample spawn failure")

    monkeypatch.setattr(pagejob, "_spawn", spawn)
    with pytest.raises(OSError):
        pagejob.start(["install"], job="k1")
    assert not pagejob.record_path("k1").exists()
    assert pagejob.read("k1") is None


def test_a_pid_that_cant_be_recorded_after_the_spawn_still_starts_the_job(
        lo, monkeypatch, caplog):
    from moxi import proc

    calls = fake_spawn(monkeypatch)

    def broken(pid):
        raise OSError("sample identity failure")

    monkeypatch.setattr(proc, "identity", broken)
    started = pagejob.start(["install"], job="k1")
    assert started == pagejob.Started("k1", True) and len(calls) == 1
    record = json.loads(pagejob.record_path("k1").read_text())
    assert "pid" not in record and record["argv"] == ["install"]
    assert "its pid wasn't recorded" in caplog.text
    assert pagejob.read("k1")["state"] == "running"    # within the start grace


def test_a_confirmed_acts_repeat_finds_its_record(lo, monkeypatch):
    calls = fake_spawn(monkeypatch)
    key = "A" * 43
    assert pagejob.start(["global", "off"], job=key) == pagejob.Started(key, True)
    # A restarted page server holds no memory of the key; the record is enough.
    assert pagejob.start(["global", "off"], job=key) == pagejob.Started(key, False)
    assert len(calls) == 1
    with pytest.raises(pagejob.Clash):
        pagejob.start(["install"], job=key)


def test_a_fresh_record_with_no_pid_reads_running_and_an_old_one_interrupted(lo):
    folder = pagejob.jobs_dir(create=True)
    (folder / "fresh.json").write_text(json.dumps({"job": "fresh", "argv": ["install"],
                                                   "created": time.time()}))
    (folder / "old.json").write_text(json.dumps({"job": "old", "argv": ["install"],
                                                 "created": time.time() - 60}))
    assert pagejob.read("fresh")["state"] == "running"
    old = pagejob.read("old")
    assert old["state"] == "interrupted" and old["lines"] == [pagejob.INTERRUPTED]
    assert "run it again" in pagejob.INTERRUPTED


def test_a_record_whose_process_is_gone_reads_interrupted(lo, monkeypatch):
    from moxi import proc

    child = subprocess.Popen([sys.executable, "-c", "pass"])
    ident = proc.identity(child.pid)
    child.wait(timeout=30)
    fake_spawn(monkeypatch, pid=child.pid)
    monkeypatch.setattr(proc, "identity", lambda pid: ident)
    started = pagejob.start(["install"])
    assert pagejob.read(started.job)["state"] == "interrupted"


def test_a_record_whose_pid_was_reused_reads_interrupted(lo):
    from moxi import proc

    ident = proc.identity(os.getpid())
    folder = pagejob.jobs_dir(create=True)
    # This process is alive under the pid, but started at another time than the job.
    (folder / "j1.json").write_text(json.dumps({
        "job": "j1", "argv": ["install"], "created": time.time() - 60,
        "pid": os.getpid(), "started": ident.started - 1000}))
    found = pagejob.read("j1")
    assert found["state"] == "interrupted" and found["lines"] == [pagejob.INTERRUPTED]


def test_a_record_with_its_end_reads_done(lo):
    folder = pagejob.jobs_dir(create=True)
    (folder / "j1.json").write_text(json.dumps({"job": "j1", "argv": ["install"],
                                                "created": time.time() - 60}))
    (folder / "j1.end.json").write_text(json.dumps({"exit": 3, "outcome": None,
                                                    "lines": ["refused"]}))
    assert pagejob.read("j1") == {"job": "j1", "argv": ["install"], "state": "done",
                                  "exit": 3, "outcome": None, "lines": ["refused"]}
    assert pagejob.read("no-such-job") is None
    assert pagejob.read("../j1") is None


def test_a_spawned_job_runs_to_its_end(lo, monkeypatch):
    recorded_homes(monkeypatch)
    started = pagejob.start(["add", "skill", "review"])
    deadline = time.monotonic() + 60
    while (found := pagejob.read(started.job))["state"] == "running":
        assert time.monotonic() < deadline, found
        time.sleep(0.1)
    assert found["state"] == "done" and found["exit"] == 0, found
    assert os.readlink(kit.claude() / "review") == str(lo / "skills" / "review")
