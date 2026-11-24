"""The lander: one per repo by its lock, the loop and its exit, the read again after the
release that keeps a member from waiting with no lander, who starts one and how, and
what `moxi rounds queue` and the doctor say."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest
from moxi import proc
from moxi_dev.testing import PROC_WAIT_S
from test_queue_join import ROUND, configure, ready, repo, round_log  # noqa: F401 -- fixtures

from moxi_rounds import doctor, joining, lander, queue_file
from moxi_rounds.cli import main

REPO = "proj-a"


def join(name: str = ROUND, stage: str = "B1", tip: str = "a" * 40, **more) -> None:
    queue_file.append(REPO, {"event": queue_file.JOIN, "round": name, "stage": stage,
                             "tip": tip, "main": "b" * 40, "urgent": False, "by": "coder",
                             **more})


def end(name: str = ROUND, stage: str = "B1", kind: str = queue_file.LANDED) -> None:
    queue_file.append(REPO, {"event": kind, "round": name, "stage": stage,
                             "reason": "test"})


@pytest.fixture
def started(monkeypatch):
    """Every lander `start` records the repo instead of starting a process."""
    calls: list[str] = []

    def start(repo, *, env=None, spawn=None):
        calls.append(repo)
        return lander.Started("session", lander.log_file(repo))

    monkeypatch.setattr(lander, "start", start)
    return calls


# -- the queue file ----------------------------------------------------------------------

def test_a_batch_that_ends_without_its_member_puts_it_back_in_its_place():
    join(tip="a" * 40)
    join(stage="B2", tip="c" * 40)
    queue_file.append(REPO, {"event": queue_file.BATCH, "state": queue_file.BUILT,
                             "id": "bbb-1", "n": 1, "main": "b" * 40, "branch": "q",
                             "members": [{"round": ROUND, "stage": "B1", "tip": "a" * 40}]})
    queue = queue_file.read(REPO)
    assert queue.member(ROUND, "B1").state == queue_file.IN_BATCH
    assert [one.stage for one in queue.waiting()] == ["B2"]
    assert queue.position(ROUND, "B1") == 1 and queue.running().id == "bbb-1"
    queue_file.append(REPO, {"event": queue_file.BATCH, "state": queue_file.ENDED,
                             "id": "bbb-1", "why": "main moved"})
    queue = queue_file.read(REPO)
    assert queue.running() is None
    assert [one.stage for one in queue.waiting()] == ["B1", "B2"]


def test_a_pause_holds_until_it_is_resumed():
    queue_file.append(REPO, {"event": queue_file.PAUSED, "reason": "a scan refusal"})
    assert queue_file.read(REPO).paused == "a scan refusal"
    queue_file.append(REPO, {"event": queue_file.RESUMED, "by": "lead"})
    assert queue_file.read(REPO).paused is None


def test_an_event_of_no_known_kind_is_refused():
    with pytest.raises(ValueError):
        queue_file.append(REPO, {"event": "held"})


# -- one lander per repo -----------------------------------------------------------------

def test_a_second_lander_exits_on_the_lock(holder):
    lander.lock_file(REPO).parent.mkdir(parents=True, exist_ok=True)
    holder(lander.lock_file(REPO))
    ran = []
    assert lander.held(REPO)
    assert lander.run(REPO, step=ran.append, say=lambda _line: None) == "held"
    assert ran == []


def test_a_batch_whose_members_all_left_keeps_the_lander_until_it_ends():
    join()
    queue_file.append(REPO, {"event": queue_file.BATCH, "state": queue_file.BUILT,
                             "id": "bbb-1", "n": 1, "main": "b" * 40, "branch": "q",
                             "members": [{"round": ROUND, "stage": "B1", "tip": "a" * 40}]})
    end(kind=queue_file.LEFT)
    assert not queue_file.read(REPO).queued()
    assert not lander.settled(REPO, queue_file.read(REPO))
    queue_file.append(REPO, {"event": queue_file.BATCH, "state": queue_file.ENDED,
                             "id": "bbb-1", "why": "rebuilt without it"})
    assert lander.settled(REPO, queue_file.read(REPO))


def test_the_lander_works_until_nothing_waits():
    join()
    steps, slept, said = [], [], []

    def step(repo):
        steps.append(repo)
        if len(steps) == 2:
            end()

    assert lander.run(REPO, step=step, sleep=slept.append, poll_s=5,
                      say=said.append) == "settled"
    assert steps == [REPO, REPO] and slept == [5]
    assert not lander.held(REPO)


def test_a_member_that_joins_while_the_lander_exits_is_taken(monkeypatch):
    join()
    reads = []
    read = queue_file.read

    def read_and_join(repo, env=None):
        if steps == [REPO]:
            reads.append(repo)
            if len(reads) == 2:
                # The first read after the first step is the loop's own; the second is
                # the one after the lock's release.
                join(name="proj-a/b-round")
        return read(repo, env)

    steps, said = [], []

    def step(repo):
        monkeypatch.setattr(queue_file, "read", read)
        for member in read(repo).queued():
            end(member.name, member.stage)
        steps.append(repo)
        monkeypatch.setattr(queue_file, "read", read_and_join)

    assert lander.run(REPO, step=step, sleep=lambda _s: None, say=said.append) == (
        "settled")
    assert len(steps) == 2
    assert any("joined as it exited" in line for line in said), said
    assert not read(REPO).queued()


# -- who starts it -----------------------------------------------------------------------

def test_ensure_starts_a_lander_only_when_a_member_waits_and_none_runs(holder):
    spawned = []

    def spawn(argv, env):
        spawned.append(argv)

    assert lander.ensure(REPO, spawn=spawn) is None
    join()
    lander.lock_file(REPO).parent.mkdir(parents=True, exist_ok=True)
    one = holder(lander.lock_file(REPO))
    assert lander.ensure(REPO, spawn=spawn) is None
    one.stdin.close()
    one.wait(timeout=30)


def test_ensure_starts_one_for_a_waiting_member_with_no_lander(repo):
    join()
    spawned = []
    found = lander.ensure(REPO, spawn=lambda argv, env: spawned.append(argv))
    assert found is not None
    assert spawned == [lander.argv(REPO)]
    assert spawned[0][-4:] == ["rounds", "land", "--repo", REPO]


def test_finish_starts_a_lander_every_time_a_build_joins(ready, started, capsys):
    ready()
    assert main(["finish", ROUND, "B1"]) == 0
    assert main(["finish", ROUND, "B1"]) == 0
    assert started == [REPO, REPO]
    assert "lander: started (session)" in capsys.readouterr().out


def test_next_starts_a_lander_for_a_waiting_member_and_says_so(ready, started, capsys,
                                                               monkeypatch):
    ready()
    joining.join(ROUND, "B1", by="coder")
    monkeypatch.setattr(lander, "held", lambda repo, env=None: False)
    assert main(["next", ROUND]) == 0
    assert started == [REPO]
    assert f"lander: started for {REPO}" in capsys.readouterr().out


def test_queue_starts_a_lander_for_a_waiting_member_and_shows_the_queue(
        ready, started, capsys):
    tree = ready()
    joining.join(ROUND, "B1", by="coder", urgent=True)
    assert main(["queue", "--repo", REPO]) == 0
    out = capsys.readouterr().out
    assert started == [REPO]
    assert "lander: started (session), since a member waits and none ran" in out
    assert f"1. {ROUND} B1 at {tree.rev()[:12]}, urgent (by coder)" in out


def test_queue_resume_lifts_a_pause(repo, configure, capsys, started):
    queue_file.append(REPO, {"event": queue_file.PAUSED, "reason": "a run cancelled twice"})
    assert main(["queue", "--repo", REPO]) == 0
    assert "paused: a run cancelled twice" in capsys.readouterr().out
    assert main(["queue", "--repo", REPO, "--resume"]) == 0
    assert "resumed proj-a's queue, paused for: a run cancelled twice" in (
        capsys.readouterr().out)
    assert queue_file.read(REPO).paused is None


def test_queue_without_a_repo_shows_every_queue_project(repo, configure, capsys):
    assert main(["queue"]) == 0
    assert "queue of proj-a" in capsys.readouterr().out


# -- how it is started -------------------------------------------------------------------

def test_the_lander_gets_the_tools_credentials_and_moxi_settings_of_its_starter():
    env = {"PATH": "/bin", "HOME": "/home/u", "SSH_AUTH_SOCK": "/run/agent",
           "GH_TOKEN": "t", "MOXI_ROUNDS_STATE_HOME": "/s", "OTHER": "x"}
    assert lander.lander_env(env) == {key: env[key] for key in env if key != "OTHER"}


def test_without_systemd_run_the_lander_starts_detached_with_no_shell(repo, monkeypatch):
    spawned = []

    def spawn(argv, **kwargs):
        spawned.append((argv, kwargs))
        return 4321

    monkeypatch.setattr(lander.shutil, "which", lambda _name: None)
    monkeypatch.setattr(proc, "spawn", spawn)
    found = lander.start(REPO, env={"PATH": "/bin", "MOXI_X": "1", "OTHER": "x"})
    assert found.how == "session" and found.log == lander.log_file(REPO)
    [(argv, kwargs)] = spawned
    assert argv == lander.argv(REPO)
    assert kwargs["detached"] is True and kwargs["output"] == found.log
    assert kwargs["env"] == {"PATH": "/bin", "MOXI_X": "1"}
    assert kwargs["cwd"] == repo.root.resolve() or str(kwargs["cwd"]) == str(repo.root)


def test_a_lander_started_with_no_shell_appends_its_output_to_its_log(repo, monkeypatch):
    log = lander.log_file(REPO)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("before\n", encoding="utf-8")
    monkeypatch.setattr(lander.shutil, "which", lambda _name: None)
    monkeypatch.setattr(lander, "argv", lambda _repo: [
        sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"])
    lander.start(REPO)
    deadline = time.monotonic() + PROC_WAIT_S
    while time.monotonic() < deadline:
        lines = log.read_text(encoding="utf-8").split()
        if "out" in lines and "err" in lines:
            break
        time.sleep(0.05)
    assert sorted(log.read_text(encoding="utf-8").split()) == ["before", "err", "out"]


def test_with_systemd_run_the_lander_starts_in_a_transient_user_unit(repo, monkeypatch):
    ran = []

    def run(argv, **kwargs):
        ran.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(lander.shutil, "which", lambda _name: "/usr/bin/systemd-run")
    monkeypatch.setattr(subprocess, "run", run)
    found = lander.start(REPO, env={"PATH": "/bin", "MOXI_X": "1"})
    assert found.how == "unit" and found.unit.startswith("moxi-rounds-lander-proj-a-")
    [argv] = ran
    assert argv[:4] == ["/usr/bin/systemd-run", "--user", "--collect", "--quiet"]
    assert "--setenv=PATH=/bin" in argv and "--setenv=MOXI_X=1" in argv
    assert argv[-len(lander.argv(REPO)):] == lander.argv(REPO)


# -- the doctor --------------------------------------------------------------------------

def test_the_doctor_warns_of_a_waiting_member_with_no_lander(repo, configure):
    join()
    [row] = [one for one in doctor.rows() if one.name == f"queue {REPO}"]
    assert row.state == "warn"
    assert row.fix == f"moxi rounds queue --repo {REPO}, which starts one"


def test_the_doctor_warns_of_a_pause(repo, configure):
    queue_file.append(REPO, {"event": queue_file.PAUSED, "reason": "scan refused"})
    [row] = [one for one in doctor.rows() if one.name == f"queue {REPO}"]
    assert row.state == "warn" and "paused: scan refused" in str(row)
