"""Sending a check to a lane (`sending.send`), with no ssh: the lane's `ssh` words are a
stand-in that drops ssh's options and host and relays the session to a real `moxi
rounds serve` with a state and cache folder of its own, apart from this process's, so
the far side has its own check lock as another machine would. The stand-in notes the
words it was given and how many bytes it passed to the far side."""

from __future__ import annotations

import _thread
import hashlib
import json
import os
import sys
import textwrap
import threading
from pathlib import Path

import pytest
from moxi_dev.testing import PROC_WAIT_S, wait_for

from moxi_rounds import check_line, checking, frames, paths, sending, serving
from moxi_rounds.errors import RoundsError

POSIX = pytest.mark.skipif(sys.platform == "win32",
                           reason="POSIX signals: Windows ends the run's job at once")
PY = sys.executable

#: `serve`, with `checking.GRACE_S` set first when the first argument is not empty.
SERVE = textwrap.dedent("""
    import sys
    from moxi_rounds import checking, cli
    if sys.argv[1]:
        checking.GRACE_S = float(sys.argv[1])
    sys.exit(cli.main(["serve"]))
    """)

#: The stand-in for ssh: its own arguments are the far home, the record file and the
#: grace; the rest are the session's words. It relays stdin to a `serve` whose rounds
#: folders are all under the far home, then notes the words and the bytes passed.
RELAY = textwrap.dedent("""
    import json, os, subprocess, sys
    far_home, record, grace = sys.argv[1:4]
    if sys.platform == "win32":
        import msvcrt
        msvcrt.setmode(0, os.O_BINARY)
    env = {key: value for key, value in os.environ.items() if key not in LOCATORS}
    env[HOME] = far_home
    child = subprocess.Popen([sys.executable, "-c", SERVE, grace], stdin=subprocess.PIPE,
                             env=env)
    count = 0
    while True:
        chunk = os.read(0, 65536)
        if not chunk:
            break
        count += len(chunk)
        try:
            child.stdin.write(chunk)
            child.stdin.flush()
        except OSError:
            pass
    try:
        child.stdin.close()
    except OSError:
        pass
    code = child.wait()
    with open(record, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"words": sys.argv[4:], "bytes": count}) + "\\n")
    sys.exit(code)
    """).replace("LOCATORS", repr(tuple(paths.LOCATOR_ENVS))).replace(
    "HOME]", f"{paths.HOME_ENV!r}]").replace("SERVE,", f"{SERVE!r},")

#: A check's command: prints, as its last line, what it finds in its tree.
FACTS = textwrap.dedent("""
    import json, os, subprocess, sys
    def git(*args):
        return subprocess.run(["git", *args], capture_output=True, text=True).stdout.strip()
    brought = os.path.join("conf", "secret.env")
    print("the check ran")
    print(json.dumps({
        "root": sys.argv[1], "head": git("rev-parse", "HEAD"),
        "main": git("rev-parse", "main"), "base": git("merge-base", "HEAD", "main"),
        "brought": open(brought).read() if os.path.exists(brought) else None,
        "mode": os.stat(brought).st_mode & 0o777 if os.path.exists(brought) else None,
        "untracked": os.path.exists("loose.txt")}))
    """)


class Lane:
    """A lane through the stand-in, its far side's folders under `home`."""

    def __init__(self, tmp_path: Path, grace_s: float | None = None):
        self.home = tmp_path / "far"
        self.record = tmp_path / "relay.jsonl"
        env = {key: value for key, value in os.environ.items()
               if key not in paths.LOCATOR_ENVS}
        env[paths.HOME_ENV] = str(self.home)
        self.env = env
        self.lock = checking.lock_file(env)
        self.trees = serving.trees_dir(env)
        self.table = {"host": "far-test", "moxi": "moxi", "connect_timeout_s": 10,
                      "shared_lock": None,
                      "ssh": [PY, "-c", RELAY, str(self.home), str(self.record),
                              "" if grace_s is None else str(grace_s)]}

    def relayed(self) -> list[dict]:
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text().splitlines()]

    def tree_dirs(self) -> list[Path]:
        return [one for one in self.trees.iterdir() if one.is_dir()] \
            if self.trees.is_dir() else []


@pytest.fixture
def lane(fenced, tmp_path):
    return Lane(tmp_path)


@pytest.fixture
def work(git_repo):
    """The test's repo with a branch `work` one commit past `main`, a file not tracked
    in its worktree, and a brought private file."""
    git_repo.switch("work", new_from="main")
    git_repo.commit("src.txt", "the build's work")
    (git_repo.root / "loose.txt").write_text("not committed\n", encoding="utf-8")
    (git_repo.root / "conf").mkdir()
    (git_repo.root / "conf" / "secret.env").write_text("TOKEN=1\n", encoding="utf-8")
    return git_repo


BROUGHT = [{"path": "conf/secret.env", "from": "~/elsewhere/secret.env",
            "sha256": "recorded at commission"}]


def request(repo, argv, **over) -> dict:
    return {**sending.request_of(
        name="proj-a/lane", stage="B1", step="check", repo="proj-a", branch="work",
        tip=repo.rev("work"), into="main", into_head=repo.rev("main"), argv=argv,
        timeout_s=60, memory_mib=None, preflight=[], wait=True, shared_lock=None),
        **over}


def run(lane, repo, argv, tmp_path, name: str = "check-1.log", say=None, **over):
    return sending.send("far", lane.table, request(repo, argv, **over),
                        worktree=repo.root, output=tmp_path / name, brought=BROUGHT,
                        say=say)


# -- the session's words -----------------------------------------------------------------

def test_the_session_opens_its_own_connection_and_keeps_each_ssh_word_whole():
    words = sending.session_argv({"host": "far", "moxi": "C:\\Moxi\\moxi.exe",
                                  "connect_timeout_s": 2.5,
                                  "ssh": ["C:\\Windows\\System32\\OpenSSH\\ssh.exe"]})
    assert words[0] == "C:\\Windows\\System32\\OpenSSH\\ssh.exe"
    assert words[1] == "-T"
    options = [words[i + 1] for i, one in enumerate(words) if one == "-o"]
    assert options == ["BatchMode=yes", "ConnectTimeout=3", "ServerAliveInterval=15",
                       "ServerAliveCountMax=4", "ControlMaster=no", "ControlPath=none"]
    assert words[-2:] == ["far", "C:\\Moxi\\moxi.exe rounds serve"]


def test_a_brought_file_the_worktree_no_longer_holds_is_not_sent(work):
    (work.root / "conf" / "secret.env").unlink()
    assert sending.brought_files(work.root, BROUGHT) == []
    (work.root / "conf" / "secret.env").write_text("X\n", encoding="utf-8")
    [(meta, data)] = sending.brought_files(work.root, BROUGHT)
    assert meta == {"path": "conf/secret.env", "sha256": hashlib.sha256(b"X\n").hexdigest()}
    assert data == b"X\n"


# -- a run -------------------------------------------------------------------------------

def test_a_run_sends_the_commit_and_brought_file_and_writes_its_output(
        lane, work, tmp_path):
    done = run(lane, work, [PY, "-c", FACTS, "{root}"], tmp_path)

    assert (done.exit_code, done.host, done.check_lock) == (0, serving.host_name(), 1)
    text = done.output.read_text()
    assert "the check ran" in text
    found = json.loads(text.strip().splitlines()[-1])
    assert Path(found["root"]).parent == lane.trees
    assert found["head"] == work.rev("work") and found["main"] == work.rev("main")
    assert found["base"] == work.rev("main")
    assert found["brought"] == "TOKEN=1\n" and not found["untracked"]
    if sys.platform != "win32":
        assert found["mode"] == 0o600
    [relayed] = lane.relayed()
    assert "ControlMaster=no" in relayed["words"] and "ControlPath=none" in relayed["words"]
    assert relayed["words"][-2:] == ["far-test", "moxi rounds serve"]
    assert lane.tree_dirs() == [] and check_line.holder(lane.lock) is None
    bare = serving.repo_dir(serving.host_name(), "proj-a", lane.env)
    assert not list(bare.rglob("secret.env"))


def test_a_second_send_carries_only_the_new_commits(lane, work, tmp_path):
    run(lane, work, [PY, "-c", FACTS, "{root}"], tmp_path)
    work.commit("more.txt", "more work")
    done = run(lane, work, [PY, "-c", FACTS, "{root}"], tmp_path, name="check-2.log")

    assert done.exit_code == 0
    first, second = lane.relayed()
    assert second["bytes"] < first["bytes"]
    found = json.loads(done.output.read_text().strip().splitlines()[-1])
    assert found["head"] == work.rev("work")


def test_a_failed_command_and_one_past_its_limit_give_their_results(lane, work, tmp_path):
    failed = run(lane, work, [PY, "-c", "raise SystemExit(3)"], tmp_path)
    assert failed.exit_code == 3

    late = run(lane, work, [PY, "-c", "import time; time.sleep(60)"], tmp_path,
               name="check-2.log", timeout_s=1)
    assert late.exit_code is None and late.duration_s < 40


def test_output_before_a_quiet_stretch_reaches_the_file_while_the_run_is_quiet(
        lane, work, tmp_path):
    output = tmp_path / "check-1.log"
    release = tmp_path / "release"
    command = textwrap.dedent(f"""
        import os, time
        print("before the quiet stretch", flush=True)
        end = time.monotonic() + 60
        while not os.path.exists({str(release)!r}) and time.monotonic() < end:
            time.sleep(0.05)
        print("after it", flush=True)
        """)
    seen: list[str] = []

    def watch() -> None:
        def written() -> bool:
            text = output.read_text() if output.exists() else ""
            return "before the quiet stretch" in text and "after it" not in text
        try:
            wait_for(written, timeout=sending.LOG_EVERY_S + 8)
            seen.append("written while quiet")
        except AssertionError:
            pass
        release.write_text("go", encoding="utf-8")

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    done = run(lane, work, [PY, "-c", command], tmp_path)
    watcher.join(timeout=PROC_WAIT_S)

    assert seen == ["written while quiet"]
    assert done.exit_code == 0 and "after it" in output.read_text()


# -- the far lock's answer ---------------------------------------------------------------

def test_no_wait_with_the_far_lock_held_is_busy_and_sends_no_bundle(
        lane, work, tmp_path, holder):
    lane.lock.parent.mkdir(parents=True, exist_ok=True)
    holder(lane.lock)

    with pytest.raises(sending.Busy) as caught:
        run(lane, work, [PY, "-c", "pass"], tmp_path, wait=False)

    assert "lane far (far-test) is busy" in caught.value.reason
    [relayed] = lane.relayed()
    asked = len(frames.REQUEST) + 1 + len(json.dumps(request(work, [PY, "-c", "pass"],
                                                             wait=False)))
    assert relayed["bytes"] < asked + 16
    assert lane.tree_dirs() == []


def test_a_waiting_run_relays_the_far_lines_and_runs_once_placed(
        lane, work, tmp_path, holder):
    lane.lock.parent.mkdir(parents=True, exist_ok=True)
    held = holder(lane.lock)
    lines: list[str] = []

    def say(line: str) -> None:
        lines.append(line)
        if held.stdin and not held.stdin.closed:
            held.stdin.close()

    done = run(lane, work, [PY, "-c", "print('ran')"], tmp_path, say=say)

    assert done.exit_code == 0 and "ran" in done.output.read_text()
    assert lines and all(line.startswith("lane far: ") for line in lines)


def test_a_held_shared_lock_makes_a_no_wait_run_busy(lane, work, tmp_path, holder):
    shared = tmp_path / "scheduled.lock"
    holder(shared)

    with pytest.raises(sending.Busy) as caught:
        run(lane, work, [PY, "-c", "pass"], tmp_path, wait=False,
            shared_lock=str(shared))

    assert "shared lock" in caught.value.reason
    assert check_line.holder(lane.lock) is None


# -- a lane that can't be reached --------------------------------------------------------

@pytest.mark.parametrize("code, err, words", [
    (255, "ssh: connect to host far-test port 22: Connection refused",
     "ssh could not reach far-test"),
    (127, "bash: line 1: moxi: command not found", "moxi rounds serve is missing"),
    (1, "ModuleNotFoundError: No module named 'moxi_rounds'", "failed to start")])
def test_an_unreachable_lane_says_what_failed(lane, work, tmp_path, code, err, words):
    lane.table["ssh"] = [PY, "-c", f"import sys; sys.stderr.write({err!r} + '\\n'); "
                         f"sys.exit({code})"]

    with pytest.raises(sending.Unreachable) as caught:
        run(lane, work, [PY, "-c", "pass"], tmp_path)

    assert words in caught.value.reason and err in caught.value.reason
    if code != 255:
        assert caught.value.fix == serving.fix_line("far-test")


def test_no_ssh_client_here_is_unreachable(lane, work, tmp_path):
    lane.table["ssh"] = [str(tmp_path / "no-such-ssh")]

    with pytest.raises(sending.Unreachable) as caught:
        run(lane, work, [PY, "-c", "pass"], tmp_path)

    assert "could not start here" in caught.value.reason


def test_a_far_side_of_another_version_is_unreachable_with_the_fix(lane, work, tmp_path):
    with pytest.raises(sending.Unreachable) as caught:
        run(lane, work, [PY, "-c", "pass"], tmp_path, version=frames.VERSION + 1)

    assert f"speaks version {frames.VERSION}" in caught.value.reason
    assert caught.value.fix == serving.fix_line(serving.host_name())


# -- not ready and lost ------------------------------------------------------------------

def test_a_failed_preflight_is_not_ready_and_frees_the_far_place(lane, work, tmp_path):
    with pytest.raises(sending.NotReady) as caught:
        run(lane, work, [PY, "-c", "pass"], tmp_path,
            preflight=[f"{Path(PY).as_posix()} -c \"print('no node'); raise SystemExit(2)\""])

    assert "exited 2" in caught.value.reason and "no node" in caught.value.reason
    assert check_line.holder(lane.lock) is None and lane.tree_dirs() == []


#: A far side that takes a place, then ends with no result.
GONE = textwrap.dedent("""
    import json, sys
    out = sys.stdout.buffer
    line = sys.stdin.buffer.readline()
    sys.stdin.buffer.read(int(line.split()[1]))
    body = json.dumps({"check_lock": 1}).encode()
    out.write(b"placed %d\\n" % len(body) + body)
    out.flush()
    """)


def test_a_session_that_ends_with_no_result_after_its_place_is_lost(lane, work, tmp_path):
    lane.table["ssh"] = [PY, "-c", GONE]

    with pytest.raises(sending.Lost) as caught:
        run(lane, work, [PY, "-c", "pass"], tmp_path)

    assert "was lost mid-run" in caught.value.reason


# -- an interrupt ------------------------------------------------------------------------

@POSIX
def test_an_interrupt_of_the_near_side_ends_the_far_run_and_frees_its_place(
        fenced, work, tmp_path):
    lane = Lane(tmp_path, grace_s=8)
    caught_file = tmp_path / "caught"
    command = textwrap.dedent(f"""
        import signal, sys, time
        def stop(signum, frame):
            open({str(caught_file)!r}, "w").write("SIGINT")
            sys.exit(3)
        signal.signal(signal.SIGINT, stop)
        print("started", flush=True)
        open({str(tmp_path / "ready")!r}, "w").write("ready")
        time.sleep(60)
        """)

    def interrupt() -> None:
        wait_for((tmp_path / "ready").exists, timeout=PROC_WAIT_S)
        _thread.interrupt_main()

    threading.Thread(target=interrupt, daemon=True).start()
    with pytest.raises(KeyboardInterrupt):
        run(lane, work, [PY, "-c", command], tmp_path)

    wait_for(lambda: lane.relayed(), timeout=PROC_WAIT_S)
    assert caught_file.read_text() == "SIGINT"
    assert check_line.holder(lane.lock) is None and lane.tree_dirs() == []
    assert "started" in (tmp_path / "check-1.log").read_text()


def test_the_bundle_of_a_build_with_nothing_missing_is_empty(work):
    held = [work.rev("work"), work.rev("main")]
    assert sending.bundle_of(work.root, "work", "main", held) == b""
    assert sending.bundle_of(work.root, "work", "main", []).startswith(b"# v")
    with pytest.raises(RoundsError):
        sending.bundle_of(work.root, "no-such-branch", None, [])
