"""`moxi rounds serve`, the far side of a lane, through a pipe and no ssh: a real
`serve` process run by `moxi rounds serve` with a state and cache folder of its own,
apart from this process's, so it has its own check lock as another machine would. The
test plays the near side, frame by frame."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from moxi import proc
from moxi_dev.testing import PROC_WAIT_S, wait_for

from moxi_rounds import check_line, checking, frames, paths, serving

POSIX = pytest.mark.skipif(sys.platform == "win32",
                           reason="POSIX signals: Windows ends the run's job at once")
PY = sys.executable

#: Runs `moxi rounds serve` with the arguments after the first; the first, when not
#: empty, sets `checking.GRACE_S` before.
STANDIN = textwrap.dedent("""
    import sys
    from moxi_rounds import checking, cli
    if sys.argv[1]:
        checking.GRACE_S = float(sys.argv[1])
    sys.exit(cli.main(["serve", *sys.argv[2:]]))
    """)

#: A check's command: prints, as its last line, what it finds in its tree.
FACTS = textwrap.dedent("""
    import json, os, subprocess, sys
    def git(*args):
        return subprocess.run(["git", *args], capture_output=True, text=True).stdout.strip()
    brought = os.path.join("conf", "secret.env")
    print("the check ran")
    print(json.dumps({
        "cwd": os.getcwd(), "root": sys.argv[1], "head": git("rev-parse", "HEAD"),
        "branch": git("branch", "--show-current"), "main": git("rev-parse", "main"),
        "base": git("merge-base", "HEAD", "main"),
        "brought": open(brought).read() if os.path.exists(brought) else None,
        "mode": os.stat(brought).st_mode & 0o777 if os.path.exists(brought) else None,
        "untracked": os.path.exists("loose.txt"),
        "holder": json.load(open(sys.argv[2]))}))
    """)


def far_env(far_home: Path) -> dict[str, str]:
    """This process's environment with rounds' folders all under `far_home`."""
    env = {key: value for key, value in os.environ.items()
           if key not in paths.LOCATOR_ENVS}
    env[paths.HOME_ENV] = str(far_home)
    return env


class Far:
    """A `serve` process and the near side's end of its pipes."""

    def __init__(self, env: dict, err: Path, *args: str, grace_s: float | None = None):
        self.err = err
        self.proc = subprocess.Popen(
            [PY, "-c", STANDIN, "" if grace_s is None else str(grace_s), *args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err.open("wb"), env=env)
        self.writer = frames.Writer(self._write)
        self.reader = frames.Reader(self.proc.stdout.read)
        self.output = b""

    def _write(self, data: bytes) -> None:
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def send(self, kind: str, value=None) -> None:
        if isinstance(value, bytes) or value is None:
            self.writer.send(kind, value or b"")
        else:
            self.writer.send_json(kind, value)

    def frame(self):
        """The next frame other than output, its payload read as JSON (`(None, None)`
        at the end); output frames are gathered in `output`."""
        while True:
            found = self.reader.frame()
            if found is None:
                return None, None
            kind, payload = found
            if kind == frames.OUT:
                self.output += payload
                continue
            return kind, frames.as_json(payload)

    def until(self, kind: str) -> tuple[dict, list[str]]:
        """The frames up to the first of `kind`: its message and the kinds before it."""
        seen = []
        while True:
            found, value = self.frame()
            assert found is not None, f"the session ended before {kind}: {seen} {self.said()}"
            if found == kind:
                return value, seen
            seen.append(found)

    def said(self) -> str:
        return self.err.read_text(encoding="utf-8", errors="replace")

    def end(self) -> int:
        if not self.proc.stdin.closed:
            self.proc.stdin.close()
        return self.proc.wait(timeout=PROC_WAIT_S)


@pytest.fixture
def far(fenced, tmp_path):
    """`far(*args, grace_s=None)`: a `serve` with its own state and cache folder,
    `far.env`; killed when the test ends if still running."""
    home = tmp_path / "far"
    env = far_env(home)
    started: list[Far] = []

    def start(*args: str, grace_s: float | None = None) -> Far:
        one = Far(env, tmp_path / f"serve-{len(started)}.err", *args, grace_s=grace_s)
        started.append(one)
        return one

    start.env = env
    start.lock = checking.lock_file(env)
    start.trees = serving.trees_dir(env)
    yield start
    for one in started:
        if one.proc.poll() is None:
            one.proc.kill()
        one.proc.wait(timeout=PROC_WAIT_S)
        one.proc.stdout.close()
        if not one.proc.stdin.closed:
            try:
                one.proc.stdin.close()
            except OSError:
                pass


@pytest.fixture
def work(git_repo):
    """The test's repo with a branch `work` one commit past `main`, and a file not
    tracked in its worktree."""
    git_repo.switch("work", new_from="main")
    git_repo.commit("src.txt", "the build's work")
    (git_repo.root / "loose.txt").write_text("not committed\n", encoding="utf-8")
    return git_repo


def bundle(repo, held: list[str], path: Path) -> bytes:
    """A bundle of `work` and `main` less the commits `held`; empty when nothing is
    missing."""
    done = subprocess.run(["git", "-C", str(repo.root), "bundle", "create", str(path),
                           "work", "main", *[f"^{one}" for one in held]],
                          env=repo.env(), capture_output=True, text=True, check=False)
    if done.returncode != 0:
        assert "empty" in done.stderr, done.stderr
        return b""
    return path.read_bytes()


def request(repo, argv, **over) -> dict:
    return {"version": frames.VERSION, "round": "proj-a/lane", "stage": "B1",
            "step": "check", "repo": "proj-a", "branch": "work", "tip": repo.rev("work"),
            "into": "main", "into_head": repo.rev("main"), "argv": argv, "timeout_s": 60,
            "memory_mib": None, "preflight": [], "wait": True, "from_host": "near-test",
            **over}


def secret(text: str = "TOKEN=1\n") -> tuple[dict, bytes]:
    data = text.encode()
    return {"path": "conf/secret.env", "sha256": hashlib.sha256(data).hexdigest()}, data


def send_all(one: Far, repo, tmp_path: Path, brought=(), name: str = "b") -> dict:
    """Read the refs, send what they lack and the brought files; the refs message."""
    refs, _seen = one.until(frames.REFS)
    one.send(frames.BUNDLE, bundle(repo, refs["refs"], tmp_path / f"{name}.bundle"))
    for meta, data in brought:
        one.send(frames.FILE, frames.file_payload(meta, data))
    one.send(frames.FILES_END)
    return refs


def facts(one: Far) -> dict:
    return json.loads(one.output.decode().strip().splitlines()[-1])


def trees(far) -> list[Path]:
    return [entry for entry in far.trees.iterdir() if entry.is_dir()] \
        if far.trees.is_dir() else []


def holder_record(far) -> dict | None:
    return check_line.holder(far.lock)


# -- frames ------------------------------------------------------------------------------

def test_a_frame_of_any_bytes_is_read_whole_and_nothing_past_its_end():
    data = bytes(range(256)) * 3
    sent = bytearray()
    writer = frames.Writer(sent.extend)
    writer.send(frames.OUT, data)
    writer.send_json(frames.RESULT, {"exit_code": 0})
    pos = 0

    def read(n):
        nonlocal pos
        found = bytes(sent[pos:pos + n])
        pos += len(found)
        return found

    reader = frames.Reader(read)
    assert reader.frame() == (frames.OUT, data)
    kind, payload = reader.frame()
    assert (kind, frames.as_json(payload)) == (frames.RESULT, {"exit_code": 0})
    assert reader.frame() is None


def _reader(data: bytes) -> frames.Reader:
    box = [data]

    def read(n: int) -> bytes:
        found, box[0] = box[0][:n], box[0][n:]
        return found

    return frames.Reader(read)


def test_a_header_that_is_no_frame_and_a_short_payload_are_refused():
    with pytest.raises(frames.FrameError):
        _reader(b"xy\n").header()
    with pytest.raises(frames.FrameError):
        _reader(b"nonsense 3\nabc").header()
    with pytest.raises(frames.FrameError):
        _reader(b"out 10\nshort").frame()
    assert _reader(b"").frame() is None


def test_a_writer_that_drops_keeps_going_after_its_pipe_breaks():
    def broken(_data):
        raise BrokenPipeError()

    writer = frames.Writer(broken, drop=True)
    writer.send(frames.OUT, b"x")
    writer.send(frames.OUT, b"y")
    assert writer.broken
    with pytest.raises(OSError):
        frames.Writer(broken).send(frames.OUT, b"x")


# -- the request -------------------------------------------------------------------------

def test_a_request_of_another_version_is_refused_naming_both_and_the_fix(far):
    one = far()
    one.send(frames.REQUEST, {"version": frames.VERSION + 1})
    found, seen = one.until(frames.REFUSED)
    assert seen == []
    assert f"version {frames.VERSION + 1}" in found["reason"]
    assert f"speaks version {frames.VERSION}" in found["reason"]
    assert found["fix"] == serving.fix_line(serving.host_name())
    assert one.end() == 1
    assert not far.lock.exists() or holder_record(far) is None


def test_a_request_missing_a_field_is_refused_before_any_place_is_taken(far, work):
    one = far()
    sent = request(work, [PY, "-c", "pass"])
    del sent["tip"]
    one.send(frames.REQUEST, sent)
    found, seen = one.until(frames.REFUSED)
    assert seen == [] and "tip" in found["reason"]
    assert one.end() == 1


# -- a run -------------------------------------------------------------------------------

def test_a_run_takes_the_far_place_then_the_commit_and_runs_in_a_tree_of_its_own(
        far, work, tmp_path):
    holder = check_line.holder_file(far.lock)
    one = far()
    one.send(frames.REQUEST, request(work, [PY, "-c", FACTS, "{root}", str(holder)]))
    placed, seen = one.until(frames.PLACED)
    assert seen == [] and placed == {"check_lock": 1}
    refs = send_all(one, work, tmp_path, brought=[secret()])
    assert refs == {"refs": []}
    one.until(frames.STARTED)
    result, _seen = one.until(frames.RESULT)
    assert one.end() == 0
    assert result["exit_code"] == 0 and result["host"] == serving.host_name()
    assert result["check_lock"] == 1
    found = facts(one)
    assert b"the check ran" in one.output
    assert os.path.realpath(found["cwd"]) == os.path.realpath(found["root"])
    assert Path(found["root"]).parent == far.trees
    assert found["head"] == work.rev("work") and found["branch"] == "work"
    assert found["main"] == work.rev("main") and found["base"] == work.rev("main")
    assert found["brought"] == "TOKEN=1\n" and not found["untracked"]
    if sys.platform != "win32":
        assert found["mode"] == 0o600
    record = found["holder"]
    assert (record["round"], record["stage"], record["step"], record["repo"]) == (
        "proj-a/lane", "B1", "check", "proj-a")
    assert record["from_host"] == "near-test" and record["tree_dir"] == found["root"]
    assert trees(far) == [] and holder_record(far) is None
    bare = serving.repo_dir("near-test", "proj-a", far.env)
    assert not list(bare.rglob("secret.env"))


def test_a_second_send_carries_only_what_the_far_side_lacks(far, work, tmp_path):
    first = far()
    first.send(frames.REQUEST, request(work, [PY, "-c", "pass"]))
    send_all(first, work, tmp_path, name="first")
    first.until(frames.RESULT)
    assert first.end() == 0
    old = work.rev("work")
    work.commit("more.txt", "more work")
    second = far()
    holder = check_line.holder_file(far.lock)
    second.send(frames.REQUEST, request(work, [PY, "-c", FACTS, "{root}", str(holder)]))
    refs = send_all(second, work, tmp_path, name="second")
    assert set(refs["refs"]) == {old, work.rev("main")}
    size = (tmp_path / "second.bundle").stat().st_size
    assert size < (tmp_path / "first.bundle").stat().st_size
    second.until(frames.RESULT)
    assert second.end() == 0
    found = facts(second)
    assert found["head"] == work.rev("work") and found["base"] == work.rev("main")


def test_no_wait_with_the_far_place_held_answers_busy_and_reads_no_bundle(
        far, work, holder):
    held = holder(far.lock)
    check_line.write_holder(far.lock, {"pid": held.pid,
                                       "created": proc.identity(held.pid).started,
                                       "round": "other/round", "stage": "B2"})
    one = far()
    one.send(frames.REQUEST, request(work, [PY, "-c", "pass"], wait=False))
    found, seen = one.until(frames.BUSY)
    assert seen == [] and "other/round B2" in found["reason"]
    assert one.frame() == (None, None)
    assert one.end() == 1


def test_a_waiting_run_says_so_and_is_placed_once_the_holder_goes(far, work, holder,
                                                                  tmp_path):
    held = holder(far.lock)
    one = far()
    one.send(frames.REQUEST, request(work, [PY, "-c", "pass"]))
    kind, wait_line = one.frame()
    assert kind == frames.WAIT and "waiting" in wait_line["line"]
    held.stdin.close()
    one.until(frames.PLACED)
    send_all(one, work, tmp_path)
    result, _seen = one.until(frames.RESULT)
    assert result["exit_code"] == 0 and one.end() == 0


def test_a_failed_preflight_is_not_ready_frees_the_place_and_removes_the_tree(
        far, work, tmp_path):
    one = far()
    fail = f"{PY} -c \"import sys; print('no node here'); sys.exit(4)\""
    one.send(frames.REQUEST, request(work, [PY, "-c", "pass"],
                                     preflight=[f"{PY} --version", fail]))
    send_all(one, work, tmp_path)
    found, seen = one.until(frames.NOT_READY)
    assert frames.STARTED not in seen
    assert found["exit_code"] == 4 and found["line"] == fail
    assert "no node here" in found["tail"]
    assert one.end() == 1
    assert trees(far) == [] and holder_record(far) is None


def test_a_brought_file_whose_bytes_differ_is_not_ready_naming_it(far, work, tmp_path):
    meta, _data = secret()
    one = far()
    one.send(frames.REQUEST, request(work, [PY, "-c", "pass"]))
    send_all(one, work, tmp_path, brought=[(meta, b"TOKEN=2\n")])
    found, _seen = one.until(frames.NOT_READY)
    assert "conf/secret.env" in found["reason"] and "sha256" in found["reason"]
    assert one.end() == 1
    assert trees(far) == []


def test_the_shared_lock_held_makes_no_wait_busy_and_a_wait_wait(far, work, holder,
                                                                 tmp_path):
    shared = tmp_path / "scheduled.lock"
    held = holder(shared)
    busy = far()
    busy.send(frames.REQUEST, request(work, [PY, "-c", "pass"], wait=False,
                                      shared_lock=str(shared)))
    found, _seen = busy.until(frames.BUSY)
    assert str(shared) in found["reason"]
    assert busy.end() == 1
    waiting = far()
    probe = textwrap.dedent(f"""
        from moxi import files
        try:
            with files.lock({str(shared)!r}, timeout_s=0):
                print("free")
        except files.LockTimeout:
            print("held")
        """)
    waiting.send(frames.REQUEST, request(work, [PY, "-c", probe], shared_lock=str(shared)))
    waiting.until(frames.PLACED)
    kind, line = waiting.frame()
    assert kind == frames.WAIT and str(shared) in line["line"]
    held.stdin.close()
    send_all(waiting, work, tmp_path)
    waiting.until(frames.RESULT)
    assert waiting.end() == 0
    # The run itself ran while serve held the shared lock.
    assert waiting.output.decode().split() == ["held"]


def test_far_words_with_root_run_with_the_far_trees_path(far, work, tmp_path):
    one = far()
    one.send(frames.REQUEST, request(work, [PY, "-c", "import sys; print(sys.argv[1])",
                                            "{root}/x"]))
    send_all(one, work, tmp_path)
    started, _seen = one.until(frames.STARTED)
    one.until(frames.RESULT)
    assert one.end() == 0
    printed = one.output.decode().strip()
    assert printed == started["argv"][-1] and Path(printed).parent.parent == far.trees


# -- the sweep and the refs --------------------------------------------------------------

def test_the_sweep_removes_a_gone_runs_tree_and_leaves_one_whose_run_lives(
        fenced, tmp_path):
    env = far_env(tmp_path / "far")
    lock_path = checking.lock_file(env)
    lock_path.parent.mkdir(parents=True)
    root = serving.trees_dir(env)
    gone, living = root / "aaaa", root / "bbbb"
    for tree in (gone, living):
        (tree / "sub").mkdir(parents=True)
        serving.tree_log(tree).write_text("output", encoding="utf-8")
    tag = check_line.new_tag()
    run = subprocess.Popen([PY, "-c", "import time; time.sleep(60)"],
                           env={**os.environ, proc.RUN_TAG_ENV: tag})
    try:
        first = wait_for(lambda: proc.identity(run.pid), what="the run's start")
        dead = subprocess.run([PY, "-c", "import os; print(os.getpid())"],
                              capture_output=True, text=True, check=True)
        check_line.write_holder(lock_path, {
            "pid": int(dead.stdout), "created": 1.0, "round": "proj-a/lane",
            "stage": "B1", "tree_dir": str(living), "run_tag": tag,
            "run_pid": first.pid, "run_started": first.started})
        assert serving.sweep(root, lock_path, at_once=1) == [gone]
        assert not gone.exists() and not serving.tree_log(gone).exists()
        assert living.is_dir()
    finally:
        run.kill()
        run.wait(timeout=PROC_WAIT_S)
    assert serving.sweep(root, lock_path, at_once=1) == [living]


def test_the_sweep_leaves_a_tree_whose_serve_still_holds_its_lock(fenced, tmp_path,
                                                                   holder):
    env = far_env(tmp_path / "far")
    lock_path = checking.lock_file(env)
    tree = serving.trees_dir(env) / "cccc"
    tree.mkdir(parents=True)
    held = holder(serving.tree_lock(tree))
    assert serving.sweep(serving.trees_dir(env), lock_path, at_once=1) == []
    assert tree.is_dir()
    held.stdin.close()
    held.wait(timeout=PROC_WAIT_S)
    assert serving.sweep(serving.trees_dir(env), lock_path, at_once=1) == [tree]


def test_refs_no_send_named_for_fourteen_days_are_dropped(fenced, work, tmp_path):
    bare = tmp_path / "bare.git"
    serving.ensure_bare(bare)
    sent = tmp_path / "all.bundle"
    bundle(work, [], sent)
    day = 86400.0
    old = request(work, [PY], branch="work")
    serving.take_bundle(bare, sent, serving.read_request(old), now_s=day)
    work.switch("other", new_from="main")
    newer = work.commit("other.txt")
    other = serving.read_request(request(work, [PY], branch="other", tip=newer))
    subprocess.run(["git", "-C", str(work.root), "bundle", "create",
                    str(tmp_path / "other.bundle"), "other"], env=work.env(),
                   check=True, capture_output=True)
    serving.take_bundle(bare, tmp_path / "other.bundle", other,
                        now_s=day + serving.REF_IDLE_DAYS * day)
    said = subprocess.run(["git", "--git-dir", str(bare), "for-each-ref",
                           "--format=%(refname)"], capture_output=True, text=True,
                          check=True).stdout.split()
    assert "refs/heads/other" in said and "refs/heads/main" in said
    assert "refs/heads/work" not in said


# -- each way serve ends -----------------------------------------------------------------

#: A far command that ignores SIGINT and names itself in `argv[1]` once it runs.
STUBBORN = textwrap.dedent("""
    import os, signal, sys, time
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    with open(sys.argv[1] + ".tmp", "w") as fh:
        fh.write(str(os.getpid()))
    os.replace(sys.argv[1] + ".tmp", sys.argv[1])
    time.sleep(120)
    """)


def _stubborn_run(far, work, tmp_path) -> tuple[Far, int, Path]:
    marker = tmp_path / "stubborn.pid"
    one = far(grace_s=8)
    one.send(frames.REQUEST, request(work, [PY, "-c", STUBBORN, str(marker)]))
    send_all(one, work, tmp_path)
    one.until(frames.STARTED)
    wait_for(marker.exists, timeout=PROC_WAIT_S, what="the far command's start")
    return one, int(marker.read_text()), marker


@POSIX
@pytest.mark.parametrize("stdout_first", [False, True], ids=["stdin", "stdout-then-stdin"])
def test_serve_outlives_its_run_when_the_near_side_goes(far, work, tmp_path, stdout_first):
    one, pid, _marker = _stubborn_run(far, work, tmp_path)
    command = proc.identity(pid)
    started = time.monotonic()
    if stdout_first:
        one.proc.stdout.close()
    one.proc.stdin.close()
    time.sleep(proc.EXIT_GRACE + 0.5)
    assert one.proc.poll() is None, one.said()
    assert proc.alive(command)
    assert holder_record(far) is not None
    code = one.proc.wait(timeout=PROC_WAIT_S)
    assert time.monotonic() - started >= 8 - 0.5
    assert code == serving.INTERRUPTED
    assert not proc.alive(command)
    assert holder_record(far) is None and trees(far) == []
    assert "Traceback" not in one.said()


def test_a_normal_end_whose_near_side_closes_stdin_at_once_cleans_up_and_exits_0(
        far, work, tmp_path):
    one = far()
    one.send(frames.REQUEST, request(work, [PY, "-c", "print('done')"]))
    send_all(one, work, tmp_path)
    one.until(frames.RESULT)
    one.proc.stdin.close()
    assert one.proc.wait(timeout=PROC_WAIT_S) == 0
    assert "KeyboardInterrupt" not in one.said() and "Traceback" not in one.said()
    assert holder_record(far) is None and trees(far) == []


def test_a_near_side_gone_while_waiting_for_the_far_place_leaves_no_ticket(
        far, work, holder):
    holder(far.lock)
    one = far()
    one.send(frames.REQUEST, request(work, [PY, "-c", "pass"]))
    kind, _line = one.frame()
    assert kind == frames.WAIT
    assert len(check_line.tickets(far.lock)) == 1
    assert one.end() == serving.INTERRUPTED
    assert check_line.tickets(far.lock) == []


# -- the probe ---------------------------------------------------------------------------

def test_the_probe_answers_the_payload_as_read_its_end_and_the_preflight(far):
    one = far("--probe")
    payload = bytes(range(256)) * 4
    one.send(frames.REQUEST, {"version": frames.VERSION, "preflight": {
        "proj-a": [f"{PY} --version"],
        "proj-b": [f"{PY} -c \"import sys; sys.exit(2)\""]}})
    one.send(frames.PROBE, payload)
    one.proc.stdin.close()
    found, _seen = one.until(frames.PROBED)
    assert one.proc.wait(timeout=PROC_WAIT_S) == 0
    assert found["length"] == len(payload)
    assert found["sha256"] == hashlib.sha256(payload).hexdigest()
    assert found["eof"] is True and found["version"] == frames.VERSION
    assert found["host"] == serving.host_name() and found["cpus"] >= 1
    assert found["git"].startswith("git version")
    assert found["check_lock"] == {"at_once": 1, "holders": [], "waiting": 0}
    assert found["preflight"]["proj-a"] is None
    assert found["preflight"]["proj-b"]["exit_code"] == 2


def test_the_probe_refuses_another_version(far):
    one = far("--probe")
    one.send(frames.REQUEST, {"version": 0})
    one.proc.stdin.close()
    found, _seen = one.until(frames.REFUSED)
    assert "version 0" in found["reason"]
    assert one.proc.wait(timeout=PROC_WAIT_S) == 1


def test_serve_is_a_verb_of_rounds(capsys):
    from moxi_rounds import cli

    assert cli.main(["serve", "--help"]) == 0
    assert "--probe" in capsys.readouterr().out
