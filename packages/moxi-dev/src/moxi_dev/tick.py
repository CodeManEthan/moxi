"""The scheduled tick, and the numbers its copies on each machine share with the code that
reads what they write.

A tick runs from a copy of this file that `moxi-dev timed schedule` puts in the machine's
run directory, beside the machine's settings, `schedule.json`, and the run tree,
`tree/`. It imports the standard library only, so a commit that breaks moxi-dev can't
stop the next tick from bringing in its fix. `TICK_VERSION` is raised with any change to
this file; each tick file records it, and `moxi-dev timed show` prints a machine's
newest tick's version beside this one, marked `run schedule again` when older.
`TICK_FORMAT` is the tick files' format and `STAMP_FORMAT` the stamp's, each written in
its file's `format` field.

`schedule.json` holds `format` (`SCHEDULE_FORMAT`), `machine`, `source` (the tree
`schedule` ran from), `has_git` (whether the source is a git clone), `record_dir` (or
null: the clone's own), `slack`, `idle_line` (or null: the verb's default), `takes` (a
host's name to its results folder), `stamp` (whether this machine writes the stamp), and
the absolute paths `uv`, `git` (null with no git) and `python`, with `python_version`.

`tick.py --stamp-only` writes the stamp, `.moxi-dev-stamp.json` at the source's root:
its `format`, main's commit, the time it was written, the object hash git names (`sha1`
or `sha256`), and the manifest, every entry of the commit's tree from `git ls-tree -r -z
--full-tree` with its mode, type, hash and path. The writer holds its own lock,
`.moxi-dev/stamp.lock` in the source (`fcntl.flock`), waiting at most 60 s for it; inside
it, it reads main again and writes only when the stamp is missing, doesn't parse, or
names another commit, to `.moxi-dev/stamp.<pid>.part`, then renamed into place. So a
writer that started before another stamped a newer main finds that stamp and leaves it.

On a source with no git, the run tree is the verified copy (`copy_tree`): its tracked
part is derived from the stamp alone, whatever an earlier copy did or left. Every path is
verified before any is written, and what is removed is found by comparing the run tree
with the stamp's manifest, so a copy that ends early changes nothing a run could
measure, and the next copy that verifies leaves exactly its manifest's paths.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import json
import math
import os
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    import fcntl
except ImportError:                     # Windows: `show` reads the numbers below there
    fcntl = None

TICK_VERSION = 1
TICK_FORMAT = 1
STAMP_FORMAT = 1
SCHEDULE_FORMAT = 1
#: The stamp, at the source tree's root: the commit a synced tree holds, and every
#: tracked path's mode and blob hash.
STAMP_NAME = ".moxi-dev-stamp.json"
SCHEDULE_JSON = "schedule.json"
STAMP_LOCK = Path(".moxi-dev") / "stamp.lock"
STAMP_WAIT_S = 60.0
STAMP_POLL_S = 0.2
HASHES = ("sha1", "sha256")

_clock = time.monotonic
_sleep = time.sleep


class TickError(Exception):
    """A step failed for a cause a person must clear; the message says why."""


class StampError(TickError):
    """The stamp couldn't be written; the message says why."""


class CopyError(TickError):
    """The verified copy failed for a cause a person must clear (exit 2)."""


class NotSettled(Exception):
    """The synced tree doesn't match its stamp yet; the message is the cause."""


class Settings:
    """`schedule.json`, read (module doc)."""

    def __init__(self, run_dir: Path, data: dict) -> None:
        if data.get("format") != SCHEDULE_FORMAT:
            raise ValueError(f"format {data.get('format')!r} is not {SCHEDULE_FORMAT}")
        self.run_dir = run_dir
        self.tree = run_dir / "tree"
        self.machine = str(data["machine"])
        self.source = Path(data["source"])
        self.has_git = bool(data["has_git"])
        self.record_dir = Path(data["record_dir"]) if data.get("record_dir") else None
        self.slack = float(data.get("slack") or 1)
        self.idle_line = data.get("idle_line")
        self.takes = {str(name): Path(where)
                      for name, where in (data.get("takes") or {}).items()}
        self.stamp = bool(data.get("stamp"))
        self.uv = str(data["uv"])
        self.git = str(data["git"]) if data.get("git") else None


def read_settings(run_dir: Path) -> Settings:
    """The run directory's `schedule.json`; ValueError naming why when it can't be used."""
    where = run_dir / SCHEDULE_JSON
    try:
        data = json.loads(where.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("not an object")
        return Settings(run_dir, data)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"{where}: {exc}") from exc


def now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _iso(at: datetime.datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(git: str, cwd: Path, *args: str) -> str:
    """git's stdout; TickError with its stderr when it fails."""
    proc = subprocess.run([git, *args], cwd=cwd, capture_output=True, check=False)
    if proc.returncode != 0:
        why = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise TickError(f"git {' '.join(args)}: {why[-1] if why else proc.returncode}")
    return proc.stdout.decode("utf-8", "surrogateescape")


# --- the stamp -------------------------------------------------------------------------

def manifest_of(git: str, source: Path, commit: str) -> list[dict[str, str]]:
    """Every entry of `commit`'s tree, as `git ls-tree -r -z --full-tree` lists it."""
    out = _git(git, source, "ls-tree", "-r", "-z", "--full-tree", commit)
    entries = []
    for item in out.split("\0"):
        if not item:
            continue
        head, path = item.split("\t", 1)
        mode, kind, digest = head.split(" ")
        entries.append({"mode": mode, "type": kind, "hash": digest, "path": path})
    return entries


def read_stamp(source: Path) -> dict | None:
    """The source's stamp, or None when it is missing or doesn't parse as an object."""
    try:
        data = json.loads((source / STAMP_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _hold_stamp_lock(source: Path, wait_s: float) -> int:
    lock = source / STAMP_LOCK
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    if fcntl is None:
        return fd
    began = _clock()
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if _clock() - began >= wait_s:
                os.close(fd)
                raise StampError(f"another stamp writer held its lock for {wait_s:g} s") \
                    from None
            _sleep(STAMP_POLL_S)


def write_stamp(git: str, source: Path, *, wait_s: float = STAMP_WAIT_S) -> tuple[str, bool]:
    """Bring the source's stamp to main's commit under the stamp's lock (module doc); the
    commit and whether a stamp was written. TickError when it can't."""
    fd = _hold_stamp_lock(source, wait_s)
    try:
        commit = _git(git, source, "rev-parse", "--verify", "refs/heads/main^{commit}").strip()
        held = read_stamp(source)
        if held is not None and held.get("format") == STAMP_FORMAT \
                and held.get("commit") == commit:
            return commit, False
        algo = _git(git, source, "rev-parse", "--show-object-format").strip() or "sha1"
        stamp = {"format": STAMP_FORMAT, "commit": commit, "written": _iso(now_utc()),
                 "hash": algo, "manifest": manifest_of(git, source, commit)}
        part = source / STAMP_LOCK.parent / f"stamp.{os.getpid()}.part"
        try:
            part.write_text(json.dumps(stamp, separators=(",", ":")), encoding="utf-8")
            os.replace(part, source / STAMP_NAME)
        except OSError as exc:
            part.unlink(missing_ok=True)
            raise StampError(f"writing {STAMP_NAME}: {exc}") from exc
        return commit, True
    finally:
        os.close(fd)


# --- the verified copy -----------------------------------------------------------------

#: Directories the copy never enters or removes, at any depth: what the run builds.
KEPT_NAMES = frozenset({".venv", ".moxi-dev", "__pycache__", ".pytest_cache", ".ruff_cache",
                        "node_modules"})
#: The run tree's commit file, which `moxi-dev timed` reads in a tree with no git.
TREE_COMMIT = Path(".moxi-dev") / "timed" / "commit.json"
SYMLINK_MODE = "120000"
EXEC_MODE = "100755"
FILE_MODES = ("100644", EXEC_MODE)


def blob_hash(data: bytes, algo: str) -> str:
    """The object id git gives `data` as a blob."""
    digest = hashlib.new(algo)
    digest.update(b"blob %d\0" % len(data))
    digest.update(data)
    return digest.hexdigest()


def _load_stamp(source: Path) -> tuple[str, str, list[dict[str, str]]]:
    """The stamp's commit, hash and manifest (the copy's step 2)."""
    try:
        text = (source / STAMP_NAME).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise NotSettled("stamp missing") from None
    except OSError as exc:
        raise CopyError(f"reading {STAMP_NAME}: {exc}") from exc
    try:
        stamp = json.loads(text)
    except ValueError:
        raise NotSettled("stamp unreadable") from None
    if not isinstance(stamp, dict):
        raise NotSettled("stamp unreadable")
    if stamp.get("format") != STAMP_FORMAT:
        raise CopyError(f"stamp format {stamp.get('format')} unknown to tick {TICK_VERSION}; "
                        f"run schedule again")
    commit, algo, manifest = stamp.get("commit"), stamp.get("hash", "sha1"), stamp.get("manifest")
    if not (isinstance(commit, str) and commit and algo in HASHES and isinstance(manifest, list)
            and all(isinstance(row, dict) and all(isinstance(row.get(key), str) for key in (
                "mode", "type", "hash", "path")) for row in manifest)):
        raise NotSettled("stamp unreadable")
    for row in manifest:
        path, parts = row["path"], row["path"].split("/")
        if row["type"] == "commit" or row["mode"] == "160000":
            raise CopyError(f"submodule entry: {path}")
        if row["mode"] not in (*FILE_MODES, SYMLINK_MODE):
            raise CopyError(f"mode {row['mode']} unknown: {path}")
        if any(part in ("", ".", "..") for part in parts):
            raise CopyError(f"path outside the tree: {path}")
        kept = next((part for part in parts if part in KEPT_NAMES), None)
        if kept is not None:
            raise CopyError(f"tracked path under {kept}: {path}")
    return commit, algo, manifest


def _verify(source: Path, manifest: list[dict[str, str]], algo: str) -> dict[str, bytes]:
    """Each manifest path's bytes, read once from the synced tree and checked against the
    manifest (the copy's step 3); NotSettled at the first that is missing or differs."""
    held = {}
    for row in manifest:
        path = row["path"]
        where = source / path
        try:
            mode = os.lstat(where).st_mode
            if row["mode"] == SYMLINK_MODE:
                if not stat.S_ISLNK(mode):
                    raise NotSettled(f"differs {path}")
                data = os.fsencode(os.readlink(where))
            else:
                if not stat.S_ISREG(mode):
                    raise NotSettled(f"differs {path}")
                data = where.read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            raise NotSettled(f"missing {path}") from None
        except OSError as exc:
            raise CopyError(f"reading {path}: {exc}") from exc
        if blob_hash(data, algo) != row["hash"]:
            raise NotSettled(f"differs {path}")
        held[path] = data
    return held


def _remove(path: Path) -> None:
    """Remove one file, symlink or directory of the run tree."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _rename(part: Path, target: Path) -> None:
    os.replace(part, target)


def _put(target: Path, make) -> None:
    """Make `target` by `make(part)` on a part file beside it, then rename; the part file
    is gone whether or not it lands."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(f"{target.name}.{os.getpid()}.part")
    try:
        make(part)
        _rename(part, target)
    except BaseException:
        if part.is_symlink() or part.exists():
            part.unlink()
        raise


def _remove_extra(tree: Path, files: set[str], dirs: set[str]) -> None:
    """Remove what the manifest lacks (the copy's step 4)."""
    def visit(at: Path, prefix: str) -> None:
        with os.scandir(at) as listing:
            entries = sorted(listing, key=lambda entry: entry.name)
        for entry in entries:
            rel, where = prefix + entry.name, Path(entry.path)
            if not entry.is_dir(follow_symlinks=False):
                if rel not in files:
                    _remove(where)
            elif entry.name in KEPT_NAMES:
                continue
            elif rel in files:
                _remove(where)
            else:
                visit(where, rel + "/")
                if rel not in dirs and not any(where.iterdir()):
                    _remove(where)
    visit(tree, "")


def _write_tree(tree: Path, manifest: list[dict[str, str]], held: dict[str, bytes]) -> None:
    """Write each path whose bytes or mode differ from the kept bytes (the copy's step 5)."""
    for row in manifest:
        target, data = tree / row["path"], held[row["path"]]
        if row["mode"] == SYMLINK_MODE:
            if target.is_symlink() and os.fsencode(os.readlink(target)) == data:
                continue
            _put(target, lambda part, data=data: os.symlink(os.fsdecode(data), part))
            continue
        mode = 0o755 if row["mode"] == EXEC_MODE else 0o644
        if not target.is_symlink() and target.is_file() \
                and bool(target.stat().st_mode & 0o111) == bool(mode & 0o111) \
                and target.read_bytes() == data:
            continue

        def make(part: Path, data: bytes = data, mode: int = mode) -> None:
            part.write_bytes(data)
            os.chmod(part, mode)
        _put(target, make)


def copy_tree(source: Path, tree: Path) -> str:
    """Bring the run tree to the commit the synced source's stamp names, from the stamp
    alone; the commit. NotSettled when the source doesn't match its stamp yet, CopyError
    for a cause a person must clear.

    1. Remove the run tree's `commit.json`, so nothing measures until step 5 writes it.
    2. Read the stamp: missing or not parsing is not settled; an unknown `format`, a
       submodule entry, or a path under a kept name (`KEPT_NAMES`) is a CopyError.
    3. Verify: read each manifest path once, hash it as git hashes a blob, compare, and
       keep the bytes; a symlink's bytes are its target. The first path missing or not
       matching ends the copy before anything is written.
    4. Remove every file and symlink of the run tree the manifest lacks, every directory
       standing where the manifest has a file, and every directory left empty that no
       manifest path lies under; the kept names are skipped whole, at any depth.
    5. Write each path whose bytes or mode differ, from the kept bytes with the
       manifest's mode (part file, then rename), then `commit.json` the same way.
    A failure in step 4 or 5 is a CopyError, and leaves no `commit.json`.
    """
    try:
        tree.mkdir(parents=True, exist_ok=True)
        (tree / TREE_COMMIT).unlink(missing_ok=True)
    except OSError as exc:
        raise CopyError(str(exc)) from exc
    commit, algo, manifest = _load_stamp(source)
    held = _verify(source, manifest, algo)
    files = {row["path"] for row in manifest}
    dirs = {"/".join(path.split("/")[:end]) for path in files
            for end in range(1, path.count("/") + 1)}
    try:
        _remove_extra(tree, files, dirs)
        _write_tree(tree, manifest, held)
        body = json.dumps({"format": 1, "commit": commit}).encode()
        _put(tree / TREE_COMMIT, lambda part: part.write_bytes(body))
    except OSError as exc:
        raise CopyError(str(exc)) from exc
    return commit


# --- the tick --------------------------------------------------------------------------

#: The verb's environment names, written here once more since a tick imports nothing of
#: moxi-dev; a test ties each to `moxi_dev.testing`'s.
TIMED_LOCK_FD_ENV = "MOXI_TEST_TIMED_LOCK_FD"  # noqa: MX2 -- the tick runs from a copy that imports only the standard library
TIMED_SLACK_ENV = "MOXI_TEST_TIMED_SLACK"  # noqa: MX2 -- the tick runs from a copy that imports only the standard library
#: The run lock under a clone's git common dir, and under a run tree with no git.
COMMON_RUN_LOCK = Path("moxi-dev") / "timed" / ".run-lock"
TREE_RUN_LOCK = Path(".moxi-dev") / "timed" / ".run-lock"
RUN_BOUND_S = 35 * 60.0
KILL_GRACE_S = 30.0
SUMMARY_PREFIX = "moxi-dev timed: commit "
STEPS = ("lock", "stamp", "tree", "run", "take")


class Tick:
    """One tick, scheduled or by hand (`main`'s doc)."""

    def __init__(self, settings: Settings, *, hand: bool, slack: float) -> None:
        self.settings, self.hand, self.slack = settings, hand, slack
        self.machine = settings.machine
        self.started = now_utc()
        self.steps = dict.fromkeys(STEPS, "skipped")
        self.failure: str | None = None
        self.not_settled: str | None = None
        self.summary: str | None = None
        self.commit: str | None = None
        self.needs_person = False
        self.cut = False
        self.run_exit: int | None = None
        self.fd: int | None = None
        self.common_dir: Path | None = None

    def fail(self, step: str, reason: str, *, needs_person: bool = True) -> None:
        line = f"moxi-dev timed tick: {self.machine}: {step} failed: {reason}"
        print(line, flush=True)
        self.steps["tree" if step in ("checkout", "copy") else step] = "failed"
        self.failure = self.failure or line
        self.needs_person = self.needs_person or needs_person

    def go(self) -> int:
        try:
            if self.lock() and self.stamp() and self.tree():
                self.run()
                if not self.hand:
                    self.take()
            return self.finish()
        finally:
            if self.fd is not None:
                os.close(self.fd)

    # step 1
    def lock(self) -> bool:
        settings = self.settings
        try:
            if settings.has_git:
                if not settings.git:
                    raise TickError("schedule.json names no git for a clone")
                self.common_dir = Path(_git(settings.git, settings.source, "rev-parse",
                                            "--path-format=absolute",
                                            "--git-common-dir").strip())
                path = self.common_dir / COMMON_RUN_LOCK
            else:
                path = settings.tree / TREE_RUN_LOCK
            if fcntl is None:
                raise TickError("this system has no flock")
            path.parent.mkdir(parents=True, exist_ok=True)
            self.fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise TickError("another timed run is measuring in this clone") from None
        except (TickError, OSError) as exc:
            self.fail("lock", str(exc))
            return False
        self.steps["lock"] = "ok"
        return True

    # step 2
    def stamp(self) -> bool:
        if self.hand or not self.settings.stamp:
            return True
        try:
            if not self.settings.git:
                raise TickError("the source has no git")
            write_stamp(self.settings.git, self.settings.source)
        except (TickError, OSError) as exc:
            self.fail("stamp", str(exc))
            return False
        self.steps["stamp"] = "ok"
        return True

    # step 3
    def tree(self) -> bool:
        settings = self.settings
        if settings.has_git:
            try:
                _git(settings.git, settings.tree, "checkout", "--quiet", "--force",
                     "--detach", "main")
                self.commit = _git(settings.git, settings.tree, "rev-parse", "HEAD").strip()
            except (TickError, OSError) as exc:
                self.fail("checkout", str(exc))
                return False
        else:
            try:
                self.commit = copy_tree(settings.source, settings.tree)
            except NotSettled as exc:
                self.not_settled = str(exc)
                self.steps["tree"] = "not settled"
                _say(self.machine, f"not measured: tree not settled: {exc}")
                return False
            except (TickError, OSError) as exc:
                self.fail("copy", str(exc))
                return False
        self.steps["tree"] = "ok"
        return True

    # step 4
    def run(self) -> None:
        settings = self.settings
        cmd = [settings.uv, "run", "--locked", "moxi-dev", "timed", "--machine",
               self.machine]
        if not self.hand:
            cmd.insert(5, "--scheduled")
        if settings.record_dir is not None:
            cmd += ["--record-dir", str(settings.record_dir)]
        if settings.idle_line is not None:
            cmd += ["--idle-line", f"{float(settings.idle_line):g}"]
        env = {**os.environ, TIMED_LOCK_FD_ENV: str(self.fd),
               TIMED_SLACK_ENV: f"{self.slack:g}"}
        try:
            proc = subprocess.Popen(cmd, cwd=settings.tree, env=env, pass_fds=(self.fd,),
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    process_group=0)
        except OSError as exc:
            self.fail("run", f"could not start: {exc}", needs_person=False)
            self.run_exit = 2
            return
        reader = threading.Thread(target=self._pump, args=(proc,), daemon=True)
        reader.start()
        try:
            code = proc.wait(timeout=RUN_BOUND_S)
        except subprocess.TimeoutExpired:
            self._stop(proc)
            self.cut = True
        except KeyboardInterrupt:
            self._stop(proc)
            raise
        reader.join(timeout=5)
        if self.cut:
            self.fail("run", f"cut at {RUN_BOUND_S / 60:g} minutes", needs_person=False)
            self.run_exit = 1
        elif code in (0, 3):
            self.steps["run"] = "ok"
            self.run_exit = code
        else:
            self.fail("run", f"exit {code}", needs_person=False)
            self.run_exit = code if code in (1, 2) else 2

    def _pump(self, proc: subprocess.Popen) -> None:
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            print(line, flush=True)
            if line.startswith(SUMMARY_PREFIX):
                self.summary = line

    @staticmethod
    def _stop(proc: subprocess.Popen) -> None:
        """SIGINT to the run's process group, then SIGKILL `KILL_GRACE_S` later."""
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGINT)
        try:
            proc.wait(timeout=KILL_GRACE_S)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()

    # step 5
    def take(self) -> None:
        settings = self.settings
        if not settings.takes:
            return
        env = {key: value for key, value in os.environ.items() if key != TIMED_LOCK_FD_ENV}
        failed = False
        for name, results_dir in settings.takes.items():
            cmd = [settings.uv, "run", "--locked", "moxi-dev", "timed", "take",
                   str(results_dir / name), "--machine", name]
            if settings.record_dir is not None:
                cmd += ["--record-dir", str(settings.record_dir)]
            try:
                code = subprocess.run(cmd, cwd=settings.tree, env=env, check=False).returncode
            except OSError as exc:
                code, why = None, f"{name}: {exc}"
            else:
                why = f"{name}: exit {code}"
            if code != 0:
                self.fail("take", why)
                failed = True
        if not failed:
            self.steps["take"] = "ok"

    # step 6
    def record_dir(self) -> Path | None:
        if self.settings.record_dir is not None:
            return self.settings.record_dir
        return self.common_dir / "moxi-dev" / "timed" if self.common_dir else None

    def finish(self) -> int:
        if self.needs_person:
            code = 2
        elif self.not_settled is not None:
            code = 3
        elif self.cut:
            code = 1
        else:
            code = 2 if self.run_exit is None else self.run_exit
        if not self.hand:
            try:
                self.write_tick_file(code)
            except (TickError, OSError) as exc:
                _say(self.machine, f"tick file failed: {exc}")
                code = 2
        _say(self.machine, f"exit {code}")
        return code

    def write_tick_file(self, code: int) -> Path:
        record_dir = self.record_dir()
        if record_dir is None:
            raise TickError("no record directory: schedule.json names none and the "
                            "clone's git directory wasn't read")
        ticks = record_dir / self.machine / "ticks"
        ticks.mkdir(parents=True, exist_ok=True)
        where = ticks / f"{self.started:%Y%m%dT%H%M%SZ}.json"
        data = {"format": TICK_FORMAT, "machine": self.machine,
                "started": _iso(self.started), "tick_version": TICK_VERSION,
                "commit": self.commit, "steps": self.steps, "failure": self.failure,
                "not_settled": self.not_settled, "summary": self.summary, "exit": code,
                "invocation": os.environ.get("INVOCATION_ID")}
        part = ticks / f"{where.name}.{os.getpid()}.part"
        try:
            part.write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(part, where)
        finally:
            part.unlink(missing_ok=True)
        return where


# --- the command line ------------------------------------------------------------------

def _say(machine: str, text: str) -> None:
    print(f"moxi-dev timed tick: {machine}: {text}", flush=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tick.py",
        description="The timed tests' scheduled tick, run from a machine's run directory.")
    parser.add_argument("--stamp-only", action="store_true",
                        help="write the source's stamp when it doesn't name main's commit, "
                             "and nothing else")
    parser.add_argument("--hand", action="store_true",
                        help="a hand tick: lock, tree and a run that isn't --scheduled; "
                             "no tick file")
    parser.add_argument("--slack", type=float, metavar="F",
                        help="the hand tick's slack factor (default: the machine's)")
    return parser


def main(argv: list[str] | None = None, *, run_dir: Path | None = None) -> int:
    """A tick, from the run directory (this file's own, or `run_dir`).

    In order; each step that fails prints `moxi-dev timed tick: <machine>: <step> failed:
    <reason>` and the tick goes to step 6:

    1. Lock: the run lock (a clone's `<git common dir>/moxi-dev/timed/.run-lock`, or the
       run tree's own `.moxi-dev/timed/.run-lock`), taken without waiting on a descriptor
       the run inherits.
    2. Stamp, when `schedule.json` says this machine writes it: the stamp writer.
    3. Tree: on a clone, `git checkout --quiet --force --detach main` in the run tree; on
       a synced source, the verified copy. A copy that doesn't verify ends the tick `not
       measured: tree not settled: <cause>`, which is no failure.
    4. Run: `<uv> run --locked moxi-dev timed --scheduled --machine <name>`, with
       `--record-dir` and `--idle-line` as set, and `MOXI_TEST_TIMED_LOCK_FD` and
       `MOXI_TEST_TIMED_SLACK` set for it alone; its output goes to this tick's. Bound
       at 35 minutes: SIGINT to its process group, SIGKILL 30 s later. A run that exits
       1 or 2 (or outside 0-3, read as 2) fails.
    5. Take, whatever the run did: `<uv> run --locked moxi-dev timed take DIR/NAME
       --machine NAME` for each take.
    6. The tick file, `<record dir>/<machine>/ticks/YYYYMMDDTHHMMSSZ.json` (part file,
       then rename): `format`, `machine`, `started`, `tick_version`, `commit` (or null),
       `steps` (each of lock, stamp, tree, run, take: ok, failed, skipped, or `not
       settled` for the tree), `failure` (the first failure line), `not_settled` (the
       cause), `summary` (the run's summary line), `exit`, `invocation` (systemd's
       `INVOCATION_ID`). Then the exit: 2 when the lock, the stamp, the checkout, the
       copy or a take failed; 3 for a tree not settled; 1 for a run cut at its bound;
       else the run's.

    `--hand` runs steps 1, 3 and 4 with a run that isn't `--scheduled`, at `--slack` (the
    machine's factor by default), and writes no tick file. `--stamp-only` writes the
    stamp and nothing else.
    """
    parser = _parser()
    args = parser.parse_args(argv)
    if args.slack is not None and not args.hand:
        parser.error("--slack goes with --hand")
    if args.stamp_only and args.hand:
        parser.error("--stamp-only and --hand are two ticks")
    run_dir = run_dir or Path(__file__).resolve().parent
    try:
        settings = read_settings(run_dir)
    except ValueError as exc:
        print(f"moxi-dev timed tick: {exc}; run schedule again", flush=True)
        return 2
    if args.stamp_only:
        if not settings.git:
            _say(settings.machine, "stamp failed: the source has no git")
            return 2
        try:
            commit, wrote = write_stamp(settings.git, settings.source)
        except (TickError, OSError) as exc:
            _say(settings.machine, f"stamp failed: {exc}")
            return 2
        _say(settings.machine, f"stamp {'written' if wrote else 'kept'} for {commit[:12]}")
        return 0
    slack = settings.slack if args.slack is None else args.slack
    if not (math.isfinite(slack) and slack >= 1):
        _say(settings.machine, f"--slack is a finite number of at least 1; it is {slack!r}")
        return 2
    return Tick(settings, hand=args.hand, slack=slack).go()


if __name__ == "__main__":
    sys.exit(main())
