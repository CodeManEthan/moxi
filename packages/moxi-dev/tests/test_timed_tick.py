"""The scheduled tick (`moxi_dev.tick`): the stamp writer, the verified copy and the tick.

Everything runs on throwaway git repositories and trees under tmp, with a stand-in `uv`
named in each run directory's `schedule.json`; no unit is written and no real run
measures anything.
"""

from __future__ import annotations

import ast
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from moxi_dev import tick
from test_check import git

REPO_ROOT = Path(__file__).resolve().parents[3]


def commit_all(root, message="c"):
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD").strip()


@pytest.fixture
def source(tmp_path):
    """A clone holding a plain file, an executable, a symlink and a nested file."""
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / ".gitignore").write_text("/.moxi-dev/\n/.moxi-dev-stamp.json\n")
    (root / "a.txt").write_text("alpha\n")
    (root / "bin").mkdir()
    (root / "bin" / "run.sh").write_text("#!/bin/sh\necho run\n")
    (root / "bin" / "run.sh").chmod(0o755)
    (root / "sub" / "deep").mkdir(parents=True)
    (root / "sub" / "deep" / "d.txt").write_text("deep\n")
    (root / "link").symlink_to("a.txt")
    commit_all(root, "start")
    return root


def schedule_json(run_dir, source, **kw):
    run_dir.mkdir(parents=True, exist_ok=True)
    data = {"format": tick.SCHEDULE_FORMAT, "machine": "host-a", "source": str(source),
            "has_git": True, "record_dir": None, "slack": 1.0, "idle_line": None,
            "takes": {}, "stamp": False, "uv": "/nonexistent/uv", "git": "git",
            "python": "/nonexistent/python3.12", "python_version": "3.12.0", **kw}
    (run_dir / tick.SCHEDULE_JSON).write_text(json.dumps(data))
    return run_dir


def stamp_of(root):
    return json.loads((root / tick.STAMP_NAME).read_text())


def ls_tree(root, commit):
    out = git(root, "ls-tree", "-r", "-z", "--full-tree", commit)
    rows = []
    for item in filter(None, out.split("\0")):
        head, path = item.split("\t", 1)
        mode, kind, digest = head.split(" ")
        rows.append({"mode": mode, "type": kind, "hash": digest, "path": path})
    return rows


# --- the stamp -------------------------------------------------------------------------

def test_stamp_only_writes_mains_commit_and_its_tree_part_file_then_rename(
        source, tmp_path, monkeypatch, capsys):
    run_dir = schedule_json(tmp_path / "run", source, stamp=True)
    git(source, "checkout", "-q", "-b", "side")
    (source / "a.txt").write_text("staged, not main's\n")
    git(source, "add", "a.txt")
    renamed = []
    real_replace = os.replace
    monkeypatch.setattr(tick.os, "replace",
                        lambda src, dst: (renamed.append((Path(src), Path(dst))),
                                          real_replace(src, dst))[1])
    assert tick.main(["--stamp-only"], run_dir=run_dir) == 0
    main = git(source, "rev-parse", "main").strip()
    stamp = stamp_of(source)
    assert stamp["format"] == tick.STAMP_FORMAT == 1
    assert stamp["commit"] == main and stamp["hash"] == "sha1" and stamp["written"]
    assert stamp["manifest"] == ls_tree(source, main)
    assert {row["path"] for row in stamp["manifest"]} == {
        ".gitignore", "a.txt", "bin/run.sh", "sub/deep/d.txt", "link"}
    assert renamed == [(source / ".moxi-dev" / f"stamp.{os.getpid()}.part",
                        source / tick.STAMP_NAME)]
    assert not list((source / ".moxi-dev").glob("*.part"))
    assert f"stamp written for {main[:12]}" in capsys.readouterr().out


@pytest.mark.parametrize("held", [None, "{not json", '["a list"]'])
def test_a_missing_or_unparsable_stamp_is_written(source, held):
    if held is not None:
        (source / tick.STAMP_NAME).write_text(held)
    commit, wrote = tick.write_stamp("git", source)
    assert wrote and stamp_of(source)["commit"] == commit


def test_a_stamp_naming_mains_commit_is_left_and_one_naming_another_is_rewritten(source):
    first, _ = tick.write_stamp("git", source)
    before = (source / tick.STAMP_NAME).stat()
    assert tick.write_stamp("git", source) == (first, False)
    after = (source / tick.STAMP_NAME).stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    (source / "a.txt").write_text("beta\n")
    second = commit_all(source)
    assert tick.write_stamp("git", source) == (second, True)
    assert stamp_of(source)["commit"] == second


class FakeClock:
    """`tick._clock` and `tick._sleep`: sleeping moves the clock, and `on_sleep` runs at
    each sleep."""

    def __init__(self, on_sleep=None):
        self.now, self.sleeps, self.on_sleep = 0.0, 0, on_sleep

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.sleeps += 1
        if self.on_sleep:
            self.on_sleep(self.sleeps)


def hold_stamp_lock(source):
    (source / ".moxi-dev").mkdir(exist_ok=True)
    fd = os.open(source / tick.STAMP_LOCK, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def test_with_the_stamp_lock_held_the_writer_waits_then_writes(source, monkeypatch):
    fd = hold_stamp_lock(source)
    fake = FakeClock(lambda n: n == 5 and os.close(fd))
    monkeypatch.setattr(tick, "_clock", fake.clock)
    monkeypatch.setattr(tick, "_sleep", fake.sleep)
    commit, wrote = tick.write_stamp("git", source)
    assert wrote and fake.sleeps == 5 and stamp_of(source)["commit"] == commit


def test_with_the_stamp_lock_held_60_s_the_stamp_fails_with_its_line(
        source, tmp_path, monkeypatch, capsys):
    fd = hold_stamp_lock(source)
    fake = FakeClock()
    monkeypatch.setattr(tick, "_clock", fake.clock)
    monkeypatch.setattr(tick, "_sleep", fake.sleep)
    run_dir = schedule_json(tmp_path / "run", source, stamp=True)
    try:
        assert tick.main(["--stamp-only"], run_dir=run_dir) == 2
    finally:
        os.close(fd)
    assert 60 <= fake.now < 60 + 2 * tick.STAMP_POLL_S
    assert ("moxi-dev timed tick: host-a: stamp failed: another stamp writer held its "
            "lock for 60 s") in capsys.readouterr().out
    assert not (source / tick.STAMP_NAME).exists()


def test_a_writer_started_at_c1_that_gets_the_lock_after_c2_was_stamped_leaves_c2(
        source, monkeypatch):
    fd = hold_stamp_lock(source)
    stamped = []

    def other_writer(n):
        if n == 1:
            (source / "a.txt").write_text("c2\n")
            stamped.append(commit_all(source, "c2"))
            stamp = {"format": 1, "commit": stamped[0], "written": "other",
                     "hash": "sha1", "manifest": ls_tree(source, stamped[0])}
            (source / tick.STAMP_NAME).write_text(json.dumps(stamp))
            os.close(fd)

    fake = FakeClock(other_writer)
    monkeypatch.setattr(tick, "_clock", fake.clock)
    monkeypatch.setattr(tick, "_sleep", fake.sleep)
    assert tick.write_stamp("git", source) == (stamped[0], False)
    assert stamp_of(source)["written"] == "other"


def test_the_repos_own_gitignore_ignores_the_stamp(source):
    (source / ".gitignore").write_text((REPO_ROOT / ".gitignore").read_text())
    tick.write_stamp("git", source)
    git(source, "check-ignore", "-q", tick.STAMP_NAME)
    assert tick.STAMP_NAME not in git(source, "status", "--porcelain")


# --- the verified copy -----------------------------------------------------------------

def sync(source, synced):
    """What a finished sync leaves: the source's files without `.git` or `.moxi-dev`."""
    if synced.exists():
        shutil.rmtree(synced)
    shutil.copytree(source, synced, symlinks=True,
                    ignore=shutil.ignore_patterns(".git", ".moxi-dev"))
    return synced


def snapshot(root, *, kept=True):
    """Every path under `root` with what it holds; with `kept=False`, the kept names'
    directories left out."""
    out = {}
    for where in sorted(root.rglob("*")):
        rel = where.relative_to(root)
        if not kept and any(part in tick.KEPT_NAMES for part in rel.parts):
            continue
        if where.is_symlink():
            out[str(rel)] = ("link", os.readlink(where))
        elif where.is_dir():
            out[str(rel)] = ("dir",)
        else:
            out[str(rel)] = ("file", where.read_bytes(), bool(where.stat().st_mode & 0o111))
    return out


def tracked(root):
    return {path: held for path, held in snapshot(root, kept=False).items()
            if held != ("dir",)}


def committed(source, commit):
    """What a run tree at `commit` holds outside the kept names."""
    out = {}
    for row in ls_tree(source, commit):
        body = git(source, "cat-file", "blob", row["hash"]).encode()
        out[row["path"]] = (("link", body.decode()) if row["mode"] == tick.SYMLINK_MODE
                            else ("file", body, row["mode"] == tick.EXEC_MODE))
    return out


def commit_file(tree):
    return json.loads((tree / tick.TREE_COMMIT).read_text())


@pytest.fixture
def synced(source, tmp_path):
    tick.write_stamp("git", source)
    return sync(source, tmp_path / "synced")


def test_a_tree_equal_to_its_stamp_copies_and_writes_its_commit(source, synced, tmp_path):
    tree = tmp_path / "run" / "tree"
    commit = tick.copy_tree(synced, tree)
    assert commit == git(source, "rev-parse", "main").strip()
    assert tracked(tree) == committed(source, commit)
    assert os.readlink(tree / "link") == "a.txt"
    assert commit_file(tree) == {"format": 1, "commit": commit}


def test_the_run_tree_commit_file_is_the_one_the_verb_reads():
    from moxi_dev import timed
    assert tick.TREE_COMMIT == timed.TREE_TIMED_DIR / timed.COMMIT_JSON


@pytest.mark.parametrize("change, cause", [
    (lambda s: (s / "sub" / "deep" / "d.txt").write_text("half synced\n"),
     "differs sub/deep/d.txt"),
    (lambda s: (s / "bin" / "run.sh").unlink(), "missing bin/run.sh"),
    (lambda s: (s / "link").unlink() or (s / "link").write_text("a.txt"), "differs link"),
    (lambda s: (s / tick.STAMP_NAME).unlink(), "stamp missing"),
    (lambda s: (s / tick.STAMP_NAME).write_text('{"format": 1, "comm'), "stamp unreadable"),
])
def test_a_tree_not_matching_its_stamp_is_not_settled_and_writes_nothing(
        source, synced, tmp_path, change, cause):
    tree = tmp_path / "run" / "tree"
    tick.copy_tree(synced, tree)
    (tree / "stray.txt").write_text("left by hand\n")
    before = snapshot(tree)
    change(synced)
    with pytest.raises(tick.NotSettled) as caught:
        tick.copy_tree(synced, tree)
    assert str(caught.value) == cause
    assert not (tree / tick.TREE_COMMIT).exists()
    before.pop(str(tick.TREE_COMMIT))
    assert snapshot(tree) == before


def test_a_synced_file_replaced_after_the_copy_read_it_leaves_the_bytes_it_checked(
        source, synced, tmp_path, monkeypatch):
    real_verify = tick._verify

    def verify_then_sync_lands(*args):
        held = real_verify(*args)
        (synced / "a.txt").write_text("a newer commit's bytes\n")
        return held

    monkeypatch.setattr(tick, "_verify", verify_then_sync_lands)
    tree = tmp_path / "tree"
    tick.copy_tree(synced, tree)
    assert (tree / "a.txt").read_text() == "alpha\n"


def test_a_path_only_the_synced_tree_holds_is_never_copied(synced, tmp_path):
    (synced / "untracked.txt").write_text("not in the commit\n")
    (synced / "sub" / "new.py").write_text("a newer commit's file\n")
    tree = tmp_path / "tree"
    tick.copy_tree(synced, tree)
    assert not (tree / "untracked.txt").exists() and not (tree / "sub" / "new.py").exists()
    assert not (tree / tick.STAMP_NAME).exists()


def test_the_tree_comes_from_the_stamp_alone_across_c1_c2_c3(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    git(source, "init", "-q", "-b", "main")
    (source / ".gitignore").write_text("/.moxi-dev/\n/.moxi-dev-stamp.json\n")
    (source / "old").write_text("old\n")
    (source / "keep").write_text("keep\n")
    c1 = commit_all(source, "c1")
    tick.write_stamp("git", source)
    synced, tree = sync(source, tmp_path / "synced"), tmp_path / "tree"
    assert tick.copy_tree(synced, tree) == c1
    assert set(tracked(tree)) == {".gitignore", "old", "keep"}

    (source / "old").unlink()
    (source / "new").write_text("new\n")
    (source / "late").write_text("late\n")
    commit_all(source, "c2")
    tick.write_stamp("git", source)
    for name in (tick.STAMP_NAME, "new"):           # the sync has landed all but `late`
        shutil.copy2(source / name, synced / name)
    before = snapshot(tree)
    with pytest.raises(tick.NotSettled, match="^missing late$"):
        tick.copy_tree(synced, tree)
    before.pop(str(tick.TREE_COMMIT))
    assert snapshot(tree) == before

    (source / "new").unlink()
    c3 = commit_all(source, "c3")
    tick.write_stamp("git", source)
    sync(source, synced)
    assert tick.copy_tree(synced, tree) == c3
    assert tracked(tree) == committed(source, c3)
    assert set(tracked(tree)) == {".gitignore", "keep", "late"}


def test_a_file_put_in_the_run_tree_is_removed_and_the_kept_names_are_left(synced, tmp_path):
    tree = tmp_path / "tree"
    tick.copy_tree(synced, tree)
    strays = ["stray.txt", "sub/stray.txt", "sub/deep/more/stray.txt", "empty/"]
    kept = [".venv/lib/site.py", ".moxi-dev/timed/.run-lock", "sub/__pycache__/m.pyc",
            "sub/deep/.pytest_cache/v", ".ruff_cache/x", "web/node_modules/p/index.js"]
    for rel in strays + kept:
        where = tree / rel
        if rel.endswith("/"):
            where.mkdir(parents=True)
        else:
            where.parent.mkdir(parents=True, exist_ok=True)
            where.write_text("x\n")
    (tree / "dangling").symlink_to("nowhere")
    tick.copy_tree(synced, tree)
    for rel in [*strays, "sub/deep/more", "dangling"]:
        assert not (tree / rel).exists() and not (tree / rel).is_symlink(), rel
    for rel in kept:
        assert (tree / rel).read_text() == "x\n", rel


def test_a_dir_where_the_manifest_has_a_file_is_replaced_by_the_file(
        synced, tmp_path):
    tree = tmp_path / "tree"
    (tree / "a.txt" / ".venv").mkdir(parents=True)
    (tree / "a.txt" / "inner").write_text("x\n")
    (tree / "link").mkdir()
    tick.copy_tree(synced, tree)
    assert (tree / "a.txt").read_text() == "alpha\n"
    assert os.readlink(tree / "link") == "a.txt"


def edit_stamp(synced, change):
    stamp = json.loads((synced / tick.STAMP_NAME).read_text())
    change(stamp)
    (synced / tick.STAMP_NAME).write_text(json.dumps(stamp))


@pytest.mark.parametrize("change, line", [
    (lambda stamp: stamp.update(format=2),
     f"stamp format 2 unknown to tick {tick.TICK_VERSION}; run schedule again"),
    (lambda stamp: stamp["manifest"].append(
        {"mode": "100644", "type": "blob", "hash": "0" * 40, "path": "pkg/.venv/x"}),
     "tracked path under .venv: pkg/.venv/x"),
    (lambda stamp: stamp["manifest"].append(
        {"mode": "160000", "type": "commit", "hash": "0" * 40, "path": "vendor/lib"}),
     "submodule entry: vendor/lib"),
])
def test_a_stamp_the_tick_cant_copy_fails_with_its_line_and_writes_nothing(
        synced, tmp_path, change, line):
    tree = tmp_path / "tree"
    tick.copy_tree(synced, tree)
    (tree / "stray.txt").write_text("x\n")
    before = snapshot(tree)
    edit_stamp(synced, change)
    with pytest.raises(tick.CopyError) as caught:
        tick.copy_tree(synced, tree)
    assert str(caught.value) == line
    before.pop(str(tick.TREE_COMMIT))
    assert snapshot(tree) == before


def test_the_mode_comes_from_the_manifest_not_the_sync(synced, tmp_path):
    tree = tmp_path / "tree"
    tick.copy_tree(synced, tree)
    (tree / "a.txt").chmod(0o755)
    (synced / "a.txt").chmod(0o755)
    (synced / "bin" / "run.sh").chmod(0o644)
    tick.copy_tree(synced, tree)
    assert (tree / "a.txt").stat().st_mode & 0o777 == 0o644
    assert (tree / "bin" / "run.sh").stat().st_mode & 0o777 == 0o755


class FailAt:
    """`tick._remove` and `tick._rename`, counted; the nth raises as a full disk would."""

    def __init__(self, monkeypatch, n=None):
        self.n, self.count = n, 0
        for name in ("_remove", "_rename"):
            monkeypatch.setattr(tick, name, self.wrap(getattr(tick, name)))

    def wrap(self, real):
        def act(*args):
            self.count += 1
            if self.count == self.n:
                raise OSError(28, "No space left on device")
            return real(*args)
        return act


def test_a_copy_failing_at_any_removal_or_write_leaves_nothing_the_next_copy_trusts(
        source, synced, tmp_path, monkeypatch):
    template = tmp_path / "template"
    tick.copy_tree(synced, template)
    for rel in ("stray.txt", "sub/stray.txt"):
        (template / rel).write_text("x\n")
    (source / "a.txt").write_text("a2\n")
    (source / "bin" / "run.sh").unlink()
    (source / "sub" / "deep" / "e.txt").write_text("e\n")
    c2 = commit_all(source, "c2")
    tick.write_stamp("git", source)
    sync(source, synced)
    with monkeypatch.context() as patch:
        counted = FailAt(patch)
        shutil.copytree(template, tmp_path / "dry", symlinks=True)
        tick.copy_tree(synced, tmp_path / "dry")
    assert counted.count >= 6
    for n in range(1, counted.count + 1):
        tree = tmp_path / f"tree-{n}"
        shutil.copytree(template, tree, symlinks=True)
        with monkeypatch.context() as patch:
            FailAt(patch, n)
            with pytest.raises(tick.CopyError, match="No space left"):
                tick.copy_tree(synced, tree)
        assert not (tree / tick.TREE_COMMIT).exists(), n
        assert tick.copy_tree(synced, tree) == c2
        assert tracked(tree) == committed(source, c2), n
        assert not [p for p in tree.rglob("*") if p.name.endswith(".part")], n
        assert commit_file(tree)["commit"] == c2


# --- the tick --------------------------------------------------------------------------

STAND_UV = r'''#!{python}
import fcntl, json, os, signal, sys, time
from pathlib import Path
here = Path(__file__).resolve().parent
plan = json.loads((here / "plan.json").read_text())
kind = "take" if "take" in sys.argv else "run"
call = {{"kind": kind, "argv": sys.argv[1:], "cwd": os.getcwd(),
        "fd": os.environ.get("MOXI_TEST_TIMED_LOCK_FD"),
        "slack": os.environ.get("MOXI_TEST_TIMED_SLACK"),
        "commit_json": Path(".moxi-dev/timed/commit.json").exists()}}
if call["fd"]:
    fd, lock = int(call["fd"]), Path(plan["lock"])
    handed, ours = os.fstat(fd), lock.stat()
    call["fd_on_lock"] = (handed.st_dev, handed.st_ino) == (ours.st_dev, ours.st_ino)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    other = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        call["held_by_tick"] = False
    except BlockingIOError:
        call["held_by_tick"] = True
with open(here / "calls.jsonl", "a") as fh:
    fh.write(json.dumps(call) + "\n")
step = plan.get(kind, {{}})
if step.get("ignore_int"):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
time.sleep(step.get("sleep_s", 0))
code = step.get("exit", 0)
if kind == "run":
    print("measuring")
    print(f"moxi-dev timed: commit 0123456789ab on host-a | passed 1, failed 0, "
          f"skipped 0, not measured 0 | total 1.0 s | exit {{code}}")
sys.exit(code)
'''


class Uv:
    """The stand-in `uv`: `plan` says how each kind of call ends, `calls()` what came."""

    def __init__(self, bin_dir, lock):
        bin_dir.mkdir(parents=True, exist_ok=True)
        self.dir, self.path = bin_dir, bin_dir / "uv"
        self.path.write_text(STAND_UV.format(python=sys.executable))
        self.path.chmod(0o755)
        self.plan(lock=str(lock))

    def plan(self, **kw):
        where = self.dir / "plan.json"
        data = json.loads(where.read_text()) if where.exists() else {}
        where.write_text(json.dumps({**data, **kw}))

    def calls(self, kind=None):
        where = self.dir / "calls.jsonl"
        rows = [json.loads(line) for line in where.read_text().splitlines()] \
            if where.exists() else []
        return [row for row in rows if kind in (None, row["kind"])]


class Run:
    def __init__(self, run_dir, uv, lock, record_dir, source):
        self.dir, self.uv, self.lock, self.record_dir = run_dir, uv, lock, record_dir
        self.source, self.tree = source, run_dir / "tree"

    def settings(self, **kw):
        data = json.loads((self.dir / tick.SCHEDULE_JSON).read_text())
        (self.dir / tick.SCHEDULE_JSON).write_text(json.dumps({**data, **kw}))

    def tick(self, *argv):
        return tick.main(list(argv), run_dir=self.dir)

    def tick_files(self):
        ticks = self.record_dir / "host-a" / "ticks"
        return sorted(ticks.iterdir()) if ticks.exists() else []

    def only_tick(self):
        files = self.tick_files()
        assert len(files) == 1, files
        return files[0], json.loads(files[0].read_text())

    def hold_lock(self):
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd


@pytest.fixture
def clone_run(source, tmp_path):
    run_dir = tmp_path / "run"
    git(source, "worktree", "add", "-q", "--detach", str(run_dir / "tree"), "main")
    lock = source / ".git" / "moxi-dev" / "timed" / ".run-lock"
    uv = Uv(tmp_path / "bin", lock)
    schedule_json(run_dir, source, uv=str(uv.path))
    return Run(run_dir, uv, lock, source / ".git" / "moxi-dev" / "timed", source)


@pytest.fixture
def synced_run(source, synced, tmp_path):
    run_dir = tmp_path / "run"
    lock = run_dir / "tree" / tick.TREE_RUN_LOCK
    uv = Uv(tmp_path / "bin", lock)
    results = tmp_path / "results"
    schedule_json(run_dir, synced, has_git=False, git=None, record_dir=str(results),
                  uv=str(uv.path))
    return Run(run_dir, uv, lock, results, synced)


TICK_NAME = re.compile(r"[0-9]{8}T[0-9]{6}Z\.json")


def test_a_clone_tick_checks_out_main_and_runs_with_the_factor_and_the_descriptor(
        clone_run, monkeypatch, capsys):
    (clone_run.source / "a.txt").write_text("moved on\n")
    main = commit_all(clone_run.source, "moved")
    clone_run.settings(slack=3.1, idle_line=1.5)
    monkeypatch.setenv("INVOCATION_ID", "abc123")
    monkeypatch.setenv(tick.TIMED_SLACK_ENV, "9")
    assert clone_run.tick() == 0
    assert git(clone_run.tree, "rev-parse", "HEAD").strip() == main
    [call] = clone_run.uv.calls()
    assert call["argv"] == ["run", "--locked", "moxi-dev", "timed", "--scheduled",
                            "--machine", "host-a", "--idle-line", "1.5"]
    assert Path(call["cwd"]) == clone_run.tree
    assert call["slack"] == "3.1" and call["fd_on_lock"] and call["held_by_tick"]
    path, data = clone_run.only_tick()
    assert TICK_NAME.fullmatch(path.name)
    assert data == {
        "format": tick.TICK_FORMAT, "machine": "host-a", "started": data["started"],
        "tick_version": tick.TICK_VERSION, "commit": main,
        "steps": {"lock": "ok", "stamp": "skipped", "tree": "ok", "run": "ok",
                  "take": "skipped"},
        "failure": None, "not_settled": None,
        "summary": ("moxi-dev timed: commit 0123456789ab on host-a | passed 1, failed 0, "
                    "skipped 0, not measured 0 | total 1.0 s | exit 0"),
        "exit": 0, "invocation": "abc123"}
    assert path.name == data["started"].replace("-", "").replace(":", "") + ".json"
    out = capsys.readouterr().out
    assert "measuring\n" in out and out.endswith("moxi-dev timed tick: host-a: exit 0\n")


def test_the_env_names_the_tick_writes_are_the_kits():
    from moxi_dev import testing, timed
    assert tick.TIMED_LOCK_FD_ENV == testing.TIMED_LOCK_FD_ENV
    assert tick.TIMED_SLACK_ENV == testing.TIMED_SLACK_ENV
    assert tick.TREE_RUN_LOCK == timed.TREE_TIMED_DIR / timed.RUN_LOCK
    assert tick.COMMON_RUN_LOCK == Path("moxi-dev") / "timed" / timed.RUN_LOCK


@pytest.mark.parametrize("code", [0, 3, 1, 2])
def test_the_ticks_exit_is_the_runs_when_every_other_step_held(clone_run, code):
    clone_run.uv.plan(run={"exit": code})
    assert clone_run.tick() == code
    _, data = clone_run.only_tick()
    assert data["exit"] == code
    if code in (0, 3):
        assert data["steps"]["run"] == "ok" and data["failure"] is None
    else:
        assert data["steps"]["run"] == "failed"
        assert data["failure"] == f"moxi-dev timed tick: host-a: run failed: exit {code}"
        assert data["summary"].endswith(f"exit {code}")


def test_a_held_lock_exits_2_with_its_line_and_leaves_a_clones_tree(clone_run, capsys):
    before = git(clone_run.tree, "rev-parse", "HEAD").strip()
    (clone_run.source / "a.txt").write_text("moved on\n")
    commit_all(clone_run.source, "moved")
    fd = clone_run.hold_lock()
    try:
        assert clone_run.tick() == 2
    finally:
        os.close(fd)
    line = ("moxi-dev timed tick: host-a: lock failed: another timed run is measuring in "
            "this clone")
    assert line in capsys.readouterr().out
    assert git(clone_run.tree, "rev-parse", "HEAD").strip() == before
    assert clone_run.uv.calls() == []
    _, data = clone_run.only_tick()
    assert data["exit"] == 2 and data["failure"] == line and data["commit"] is None
    assert data["steps"] == {"lock": "failed", "stamp": "skipped", "tree": "skipped",
                             "run": "skipped", "take": "skipped"}


def test_a_held_lock_leaves_a_synced_run_tree_as_it_was(synced_run):
    assert synced_run.tick() == 0
    (synced_run.tree / "stray.txt").write_text("x\n")
    (synced_run.source / "a.txt").write_text("half synced\n")
    before = snapshot(synced_run.tree)
    fd = synced_run.hold_lock()
    try:
        assert synced_run.tick() == 2
    finally:
        os.close(fd)
    assert snapshot(synced_run.tree) == before
    assert (synced_run.tree / tick.TREE_COMMIT).exists()


def test_a_run_past_its_bound_is_cut_and_the_tick_exits_1(clone_run, monkeypatch):
    monkeypatch.setattr(tick, "RUN_BOUND_S", 0.5)
    monkeypatch.setattr(tick, "KILL_GRACE_S", 0.5)
    for plan in ({"sleep_s": 60}, {"sleep_s": 60, "ignore_int": True}):
        clone_run.uv.plan(run=plan)
        began = time.monotonic()
        assert clone_run.tick() == 1
        assert time.monotonic() - began < 15
    for path in clone_run.tick_files():
        data = json.loads(path.read_text())
        assert data["exit"] == 1 and data["steps"]["run"] == "failed"
        assert data["failure"].startswith("moxi-dev timed tick: host-a: run failed: cut at ")


def test_a_take_runs_after_the_run_whatever_it_did_and_one_that_fails_exits_2(
        clone_run, tmp_path):
    results = tmp_path / "moxi-timed-host-b"
    clone_run.settings(takes={"host-b": str(results)})
    clone_run.uv.plan(run={"exit": 1})
    assert clone_run.tick() == 1
    assert [call["kind"] for call in clone_run.uv.calls()] == ["run", "take"]
    take = clone_run.uv.calls("take")[0]
    assert take["argv"] == ["run", "--locked", "moxi-dev", "timed", "take",
                            str(results / "host-b"), "--machine", "host-b"]
    assert Path(take["cwd"]) == clone_run.tree and take["fd"] is None
    clone_run.uv.plan(run={"exit": 0}, take={"exit": 2})
    time.sleep(1.1)                         # the next tick file's name is the next second
    assert clone_run.tick() == 2
    data = json.loads(clone_run.tick_files()[-1].read_text())
    assert data["exit"] == 2 and data["steps"]["take"] == "failed"
    assert data["failure"] == "moxi-dev timed tick: host-a: take failed: host-b: exit 2"


def test_a_tick_that_writes_the_stamp_writes_it_first(clone_run):
    clone_run.settings(stamp=True)
    assert clone_run.tick() == 0
    assert stamp_of(clone_run.source)["commit"] == git(clone_run.source, "rev-parse",
                                                       "main").strip()
    assert clone_run.only_tick()[1]["steps"]["stamp"] == "ok"


def test_a_hand_tick_runs_unscheduled_at_its_factor_and_writes_no_tick_file(
        clone_run, tmp_path):
    clone_run.settings(stamp=True, slack=3.1, takes={"host-b": str(tmp_path / "r")})
    assert clone_run.tick("--hand", "--slack", "2") == 0
    [call] = clone_run.uv.calls()
    assert call["argv"] == ["run", "--locked", "moxi-dev", "timed", "--machine", "host-a"]
    assert call["slack"] == "2" and call["fd_on_lock"]
    assert clone_run.tick_files() == []
    assert not (clone_run.source / tick.STAMP_NAME).exists()


def test_a_synced_tick_runs_on_the_verified_copy_and_files_its_tick_in_the_results(
        synced_run, source):
    assert synced_run.tick() == 0
    [call] = synced_run.uv.calls()
    assert call["argv"][-2:] == ["--record-dir", str(synced_run.record_dir)]
    assert call["commit_json"] and call["fd_on_lock"] and call["held_by_tick"]
    main = git(source, "rev-parse", "main").strip()
    _, data = synced_run.only_tick()
    assert data["commit"] == main and data["exit"] == 0


def test_a_synced_tick_whose_tree_hasnt_settled_exits_3_with_its_cause(synced_run, capsys):
    (synced_run.source / "a.txt").write_text("half synced\n")
    assert synced_run.tick() == 3
    assert ("moxi-dev timed tick: host-a: not measured: tree not settled: differs a.txt"
            in capsys.readouterr().out)
    assert synced_run.uv.calls() == []
    _, data = synced_run.only_tick()
    assert data["not_settled"] == "differs a.txt" and data["failure"] is None
    assert data["steps"]["tree"] == "not settled" and data["exit"] == 3
    assert data["commit"] is None


def test_a_synced_tick_whose_copy_fails_exits_2_with_its_line(synced_run):
    edit_stamp(synced_run.source, lambda stamp: stamp.update(format=2))
    assert synced_run.tick() == 2
    _, data = synced_run.only_tick()
    assert data["failure"] == (f"moxi-dev timed tick: host-a: copy failed: stamp format 2 "
                               f"unknown to tick {tick.TICK_VERSION}; run schedule again")
    assert data["steps"]["tree"] == "failed" and synced_run.uv.calls() == []


def test_the_tick_runs_as_a_copy_in_its_run_dir(synced_run):
    shutil.copy2(Path(tick.__file__), synced_run.dir / "tick.py")
    proc = subprocess.run([sys.executable, "-I", str(synced_run.dir / "tick.py")],
                          capture_output=True, text=True, check=False, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.endswith("moxi-dev timed tick: host-a: exit 0\n")
    assert synced_run.only_tick()[1]["exit"] == 0


def test_tick_imports_nothing_outside_the_standard_library():
    tree = ast.parse(Path(tick.__file__).read_text())
    names = {alias.name.split(".")[0] for node in ast.walk(tree)
             if isinstance(node, ast.Import) for alias in node.names}
    names |= {node.module.split(".")[0] for node in ast.walk(tree)
              if isinstance(node, ast.ImportFrom) and node.level == 0}
    assert names and names <= set(sys.stdlib_module_names) | {"__future__"}
    assert not [node for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.level]


#: `tick.py`'s digest at this `TICK_VERSION`. A change to `tick.py` raises `TICK_VERSION`
#: and pins its new digest here, since every machine's copy is as old as its schedule.
TICK_DIGEST = (1, "6f0bf574759ae3d242dde706a9aaff4a986a8588853968191bde02715ffb5a3d")


def test_tick_py_is_pinned_beside_its_version():
    body = Path(tick.__file__).read_bytes().replace(b"\r\n", b"\n")
    digest = hashlib.sha256(body).hexdigest()
    assert (tick.TICK_VERSION, digest) == TICK_DIGEST, (
        "tick.py changed: raise TICK_VERSION and pin the new digest in TICK_DIGEST")
