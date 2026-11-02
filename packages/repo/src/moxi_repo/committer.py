"""The one committer: stage named paths, let a prepare step drop some, commit them alone,
push, and leave a doctor record whoever called.

`commit(spec)` runs one pass and never raises; every outcome is a `CommitResult`. A pass
has three parts under one lock, the lock file `<common git dir>/moxi-repo/lock`, shared by
every committer on that repo:

- the commit part: cadence, what changed, what to leave out, stage, prepare, commit;
- the push part, which runs on every pass that holds the lock, whatever the commit part
  returned, so a failed push is retried by the first pass after `push_backoff`;
- the record part: the repo's state file, then the doctor records, then the sweep.

Only a `busy` pass, which never took the lock, skips the push and record parts. A failure
in the commit part ends it `error` (unstaging what it staged), and a failure in the push
part is a `push_error`; either way the record part runs.

Every path the committer hands git is literal by its own form (`:(literal)<path>`). A list
of changed paths goes on stdin (`--pathspec-from-file=- --pathspec-file-nul`); the only
paths in argv are the spec's own, at most 64 paths and 8 KiB. Every call goes through
`git()` with a clean environment: the committer sets no pathspec variable and passes no
global pathspec option, and removes the ones a caller set, so the git hooks its commit runs
read pathspecs as git does by default.

The git calls of a pass:

| step | call |
|---|---|
| before the lock | `rev-parse --path-format=absolute --git-common-dir --show-toplevel --git-dir` |
| cadence | `log --all -1 --basic-regexp --author=<anchored> --format=%ct` |
| dirty | `status --porcelain -z --untracked-files=all --no-renames -- <spec paths>` |
| staged before | `rev-parse -q --verify HEAD^{commit}`; `diff-index --cached --raw -z --no-renames <HEAD or the empty tree> -- <spec paths>` (on an unborn branch, `hash-object -t tree --stdin` first) |
| stage | `add --all --pathspec-from-file=- --pathspec-file-nul` |
| ours | the same `diff-index` with `-M` in place of `--no-renames`; a path staged and not read back as ours is unstaged as below |
| drop, unstage | `reset -q --pathspec-from-file=- --pathspec-file-nul`, then the `diff-index` again to verify |
| commit | `rev-parse -q --verify HEAD^{commit}`; `commit -q -F <file> [--trailer k=v]... --pathspec-from-file=- --pathspec-file-nul`; `rev-list -1 --parents HEAD` (when `HEAD`'s parent isn't the `HEAD` read before, `rev-list --reverse --parents --basic-regexp --author=<anchored> HEAD ^<old HEAD>`); `diff-tree -r -z --no-renames --raw --root --no-commit-id <new commit>` |
| push | `symbolic-ref -q HEAD`; `remote`; per branch `rev-parse -q --verify refs/heads/<b>`, `rev-list --count refs/remotes/<r>/<b>..refs/heads/<b>`, `push -q <r> refs/heads/<b>:refs/heads/<b>` (before it, `config --get core.sshCommand`) |

The push part keeps, per branch, the sha its last successful push sent. A branch whose
remote-tracking ref is missing (a push-only remote) reads as not ahead while
`refs/heads/<b>` is still that sha, so it is pushed once per new commit, not every pass.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from moxi_repo import _records
from moxi_repo.run import COMMITTER_ENV, quote_arg
from moxi_repo.gitrun import (DEFAULT_TIMEOUT, Change, GitFailed, changes, empty_tree, git,
                              head_commit, literal_pathspec, parse_raw, paths_problem)

#: Where the committer keeps its lock, state file and message file, in the common git dir.
OWN_DIR = "moxi-repo"
LOCK = "lock"
STATE = "state.json"
MESSAGE = "message"
#: The git config section prefix of a saved spec: `[moxi-repo "spec.<name>"]`.
SPEC_PREFIX = "moxi-repo.spec."

STATUSES = ("committed", "clean", "throttled", "busy", "index-busy", "refused", "error")
#: A pass that saw something it can judge: its record part writes or clears the commit
#: record. `throttled`, `busy` and `index-busy` passes leave it as it was, except that
#: one whose push printed lines writes them into it, and git's index lock held for
#: `STALE_INDEX_PASSES` passes in a row is written there too.
CONCLUSIVE = ("committed", "clean", "refused", "error")
#: This many `index-busy` passes in a row, and git's `index.lock` is reported as stale.
STALE_INDEX_PASSES = 5
COMMIT_TIMEOUT = 300.0
PUSH_TIMEOUT = 120.0
#: How many left-out or unjudged paths a result's line names before "and N more".
NAMED = 5

_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9._-]*")
_TARGET = re.compile(r"[A-Za-z_][\w.]*:[A-Za-z_][\w.]*")
_TRAILER_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")
_REMOTE = re.compile(r"[^\s]+")

#: The clock; tests replace it.
_now = time.time


# -- the spec -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Spec:
    """What one committer pass commits, and how.

    `name` is `<module>/<name>`. `paths` are relative to `root`, the work tree's top, and
    read literally: `"."` is the whole tree, a directory takes what is under it; at most
    64 paths and 8 KiB in all. `author` (name, email) is set as author and committer.
    `min_interval` is the seconds since this author's last commit before another commit.
    `message` is the summary line, or fn(changes) -> str; None gives `<name>: N paths`.
    `trailers` is fn(changes, since) -> [(key, value)]. `prepare` is fn(PrepareContext) ->
    Prepared. Either callable may be given as a `"pkg.mod:fn"` string. `push` names the
    remote to push to (None: no push); `push_backoff` is the seconds after a failed push
    before the next attempt; `max_bytes` caps each file's size (None: no cap).
    """

    name: str
    root: Path
    paths: tuple[str, ...]
    author: tuple[str, str]
    min_interval: float = 0
    message: str | Callable | None = None
    trailers: Callable | str | None = None
    prepare: Callable | str | None = None
    push: str | None = None
    push_backoff: float = 300
    max_bytes: int | None = 10 * 1024 * 1024


@dataclass(frozen=True)
class Prepared:
    """What a prepare step returns: paths to drop, each with its reason, or a refusal."""

    drop: tuple[tuple[str, str], ...] = ()
    refuse: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "drop", tuple((str(p), str(r)) for p, r in self.drop))


@dataclass(frozen=True)
class PrepareContext:
    """What a prepare step judges: the work tree's top, and our staged changes, each with
    its staged blob id (`Change.blob`). `git(*args)` runs git there with a clean
    environment; a path a prepare step passes it is its own to write literally."""

    root: Path
    changes: tuple[Change, ...]
    spec: str

    def git(self, *args: str, input: bytes | str | None = None,
            timeout: float = DEFAULT_TIMEOUT, binary: bool = False):
        return git(self.root, *args, input=input, timeout=timeout, binary=binary)


@dataclass(frozen=True)
class CommitResult:
    """One pass's outcome. `status` is the commit part's (one of `STATUSES`); `left` holds
    each path left out, with why; `unjudged` each committed path prepare didn't judge;
    `pushed` the branches pushed; `push_error` what failed in the push part; `hook_lines`
    every line git and the git hooks printed."""

    status: str
    detail: str = ""
    sha: str | None = None
    files: tuple[str, ...] = ()
    left: tuple[tuple[str, str], ...] = ()
    unjudged: tuple[str, ...] = ()
    pushed: tuple[str, ...] = ()
    push_error: str | None = None
    hook_lines: tuple[str, ...] = ()
    spec: str = ""
    detached: bool = False

    def line(self) -> str:
        """The one line a person or an agent who asked for the commit sees."""
        n = len(self.files)
        head = {
            "committed": f"committed {(self.sha or '')[:7]} ({n} path{'s' if n != 1 else ''})",
            "clean": "nothing to commit",
        }.get(self.status, self.status)
        text = f"repo: {self.spec} {head}"
        if self.detail and self.status not in ("committed",):
            text += f": {self.detail}"
        if self.left:
            text += f"; left out {len(self.left)}: " + _named(
                f"{p} ({why})" for p, why in self.left)
        if self.unjudged:
            text += f"; committed unjudged {len(self.unjudged)}: " + _named(self.unjudged)
        if self.pushed:
            text += "; pushed " + ", ".join(self.pushed)
        if self.push_error:
            text += f"; push failed: {self.push_error}"
        if self.detached:
            text += "; HEAD is detached"
        return text

    @property
    def exit_code(self) -> int:
        """0 for a pass that did what it could; 3 refused; 1 an error or a failed push."""
        if self.status == "error" or self.push_error:
            return 1
        if self.status == "refused":
            return 3
        return 0

    def as_dict(self) -> dict:
        out = asdict(self)
        out["left"] = [list(x) for x in self.left]
        for name in ("files", "unjudged", "pushed", "hook_lines"):
            out[name] = list(out[name])
        out["line"] = self.line()
        return out


def _named(items) -> str:
    items = list(items)
    shown = ", ".join(items[:NAMED])
    return shown + (f" and {len(items) - NAMED} more" if len(items) > NAMED else "")


class SpecError(ValueError):
    """A spec the committer can't run or save; the message says what is wrong."""


def spec_problem(spec: Spec) -> str | None:
    """What is wrong with a spec, or None."""
    if not isinstance(spec, Spec):
        return f"a spec is a moxi_repo.Spec, not {type(spec).__name__}"
    if not isinstance(spec.name, str) or not _NAME.fullmatch(spec.name):
        return (f"spec name {spec.name!r}: it is <module>/<name>, lowercase letters, digits, "
                "'-' and '_' before the slash")
    if isinstance(spec.paths, str) or not isinstance(spec.paths, (tuple, list)):
        return "paths is a tuple of paths relative to root"
    if not spec.paths:
        return "paths is empty; name at least one path ('.' is the whole tree)"
    for p in spec.paths:
        if not isinstance(p, str) or not p or "\0" in p or os.path.isabs(p):
            return f"path {p!r}: each path is relative to root, not empty, with no NUL"
    problem = paths_problem(spec.paths)
    if problem:
        return f"paths: {problem}"
    if (not isinstance(spec.author, (tuple, list)) or len(spec.author) != 2
            or not all(isinstance(x, str) and x.strip() for x in spec.author)
            or any(c in "".join(spec.author) for c in "<>\n\0")):
        return "author is (name, email), each non-empty, with no '<', '>' or newline"
    for label, value in (("min_interval", spec.min_interval),
                         ("push_backoff", spec.push_backoff)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            return f"{label} is a number of seconds, 0 or more"
    if spec.max_bytes is not None and (isinstance(spec.max_bytes, bool)
                                       or not isinstance(spec.max_bytes, int)
                                       or spec.max_bytes <= 0):
        return "max_bytes is a positive number of bytes, or None for no cap"
    if spec.message is not None and not callable(spec.message) and (
            not isinstance(spec.message, str) or not spec.message.strip()):
        return "message is a summary line, a callable, or None"
    for label, value in (("trailers", spec.trailers), ("prepare", spec.prepare)):
        if value is not None and not callable(value) and not (
                isinstance(value, str) and _TARGET.fullmatch(value)):
            return f"{label} is a callable, a 'pkg.mod:fn' string, or None"
    if spec.push is not None and (not isinstance(spec.push, str)
                                  or not _REMOTE.fullmatch(spec.push)):
        return "push names a remote, or is None"
    return None


def _load(target, label: str) -> Callable:
    if callable(target):
        return target
    from importlib import import_module

    module, _, attr = target.partition(":")
    try:
        obj = import_module(module)
        for part in attr.split("."):
            obj = getattr(obj, part)
    except Exception as exc:  # noqa: BLE001 - any failure to load is the spec's error
        raise SpecError(f"{label} {target} can't be loaded: {_describe(exc)}") from None
    if not callable(obj):
        raise SpecError(f"{label} {target} is not callable")
    return obj


def _describe(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()
    return type(exc).__name__ + (f": {text[0]}" if text else "")


# -- where, and the lock ------------------------------------------------------------------

class _Where:
    __slots__ = ("top", "cgd", "git_dir")

    def __init__(self, top: Path, cgd: Path, git_dir: Path):
        self.top, self.cgd, self.git_dir = top, cgd, git_dir

    @property
    def own(self) -> Path:
        return self.cgd / OWN_DIR


def _where(root, *, exact: bool = True) -> _Where:
    """The work tree's top, its common git dir and its git dir; raises SpecError. With
    `exact` (a spec's root), `root` must be the work tree's top; without it, any path
    inside the work tree will do."""
    root = Path(root)
    if not root.is_dir():
        raise SpecError(f"{root} is not a directory")
    done = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir",
               "--show-toplevel", "--git-dir")
    lines = done.stdout.splitlines()
    if done.returncode != 0 or len(lines) != 3:
        raise SpecError(f"{root} is not in a git work tree")
    cgd, found, git_dir = (Path(x) for x in lines)
    if exact and os.path.realpath(found) != os.path.realpath(root):
        raise SpecError(f"root {root} is not the top of its work tree ({found})")
    return _Where(found, cgd, git_dir)


# This thread's passes: the common git dirs whose committer lock it holds.
_held = threading.local()


def _held_here() -> set[str]:
    if not hasattr(_held, "dirs"):
        _held.dirs = set()
    return _held.dirs


# One gate per lock file, so this process's threads queue for the committer's lock (the
# lock file itself refuses a second thread of the same process at once).
_gates: dict[str, threading.Lock] = {}
_gates_guard = threading.Lock()


@contextlib.contextmanager
def _take(where: _Where, wait: float | None):
    """Hold the committer's lock for the block, waiting up to `wait` seconds (None: as
    long as it takes; 0: try once) behind other processes and this process's other
    threads alike. Raises `moxi.files.LockTimeout` when the wait runs out, and
    `RuntimeError` when this thread already holds it."""
    from moxi import files

    path = where.own / LOCK
    key = _records.key_of(where.cgd)
    if key in _held_here():
        raise RuntimeError(f"{path} is already held by this thread")
    with _gates_guard:
        gate = _gates.setdefault(os.path.abspath(path), threading.Lock())
    deadline = None if wait is None else time.monotonic() + wait
    if wait is None:
        got = gate.acquire()
    elif wait > 0:
        got = gate.acquire(timeout=wait)
    else:
        got = gate.acquire(blocking=False)
    if not got:
        raise files.LockTimeout(path, os.getpid())
    try:
        left = None if deadline is None else max(0.0, deadline - time.monotonic())
        with files.lock(path, timeout=left):
            _held_here().add(key)
            try:
                yield
            finally:
                _held_here().discard(key)
    finally:
        gate.release()


@contextlib.contextmanager
def _locked(where: _Where, wait: float | None):
    """Hold the committer's lock for the block; yields False, holding nothing, when another
    pass holds it past `wait`."""
    from moxi import files

    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(_take(where, wait))
        except (files.LockTimeout, RuntimeError):
            yield False
            return
        yield True


# -- the state file -----------------------------------------------------------------------

def _state_path(cgd) -> Path:
    return Path(cgd) / OWN_DIR / STATE


def read_state(cgd) -> dict:
    """The repo's state file: {"specs": {name: entry}}."""
    try:
        data = json.loads(_state_path(cgd).read_bytes())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or not isinstance(data.get("specs"), dict):
        data = {"specs": {}}
    return data


def _change_state(cgd, fn: Callable[[dict], None]) -> None:
    from moxi import files

    def edit(old: bytes | None) -> bytes:
        try:
            data = json.loads(old) if old else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict) or not isinstance(data.get("specs"), dict):
            data = {"specs": {}}
        fn(data["specs"])
        return (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)
                + "\n").encode("utf-8", "surrogateescape")

    files.update(_state_path(cgd), edit)


def _stamp(t: float | None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)) if t else "unknown"


# -- saved specs --------------------------------------------------------------------------

def _config_specs(cgd) -> dict[str, dict[str, list[str]]]:
    """Every saved spec in the repo's git config: name -> {variable: [values]}."""
    done = git(cgd, f"--git-dir={cgd}", "config", "--local", "-z", "--get-regexp",
               r"^moxi-repo\.spec\.", binary=True)
    out: dict[str, dict[str, list[str]]] = {}
    if done.returncode != 0:
        return out
    for item in done.stdout.split(b"\0"):
        if not item:
            continue
        key, _, value = item.partition(b"\n")
        key_text = os.fsdecode(key)
        if not key_text.startswith(SPEC_PREFIX):
            continue
        name, _, var = key_text[len(SPEC_PREFIX):].rpartition(".")
        out.setdefault(name, {}).setdefault(var, []).append(os.fsdecode(value))
    return out


def _one(section: dict[str, list[str]], var: str) -> str | None:
    values = section.get(var)
    return values[-1] if values else None


def _number(section, var: str, default, kind=float):
    value = _one(section, var)
    if value is None:
        return default
    if kind is int and value == "none":
        return None
    try:
        return kind(value)
    except ValueError:
        raise SpecError(f"saved {var} = {value!r} is not a number") from None


def _from_section(top: Path, name: str, section: dict[str, list[str]]) -> Spec:
    author = _one(section, "author") or ""
    found = re.fullmatch(r"(.+?) <([^<>]+)>", author)
    if not found:
        raise SpecError(f"saved author = {author!r} is not 'name <email>'")
    spec = Spec(name=name, root=top, paths=tuple(section.get("paths", ())),
                author=(found.group(1), found.group(2)),
                min_interval=_number(section, "min-interval", 0),
                message=_one(section, "message"), trailers=_one(section, "trailers"),
                prepare=_one(section, "prepare"), push=_one(section, "push") or None,
                push_backoff=_number(section, "push-backoff", 300),
                max_bytes=_number(section, "max-bytes", 10 * 1024 * 1024, int))
    problem = spec_problem(spec)
    if problem:
        raise SpecError(f"the saved spec: {problem}")
    return spec


def saved_spec(root, name: str) -> Spec | None:
    """The spec saved as `name` in the repo at `root`, or None when there is none; raises
    SpecError when its section can't be read as a spec."""
    where = _where(root)
    section = _config_specs(where.cgd).get(name)
    return None if section is None else _from_section(where.top, name, section)


def saved_names(cgd) -> list[str]:
    return sorted(_config_specs(cgd))


def _plain(x: float) -> str:
    return str(int(x)) if float(x) == int(x) else repr(float(x))


def _fields(spec: Spec) -> list[tuple[str, str]]:
    out = [("paths", p) for p in spec.paths]
    out.append(("author", f"{spec.author[0]} <{spec.author[1]}>"))
    out.append(("min-interval", _plain(spec.min_interval)))
    for var, value in (("message", spec.message), ("trailers", spec.trailers),
                       ("prepare", spec.prepare), ("push", spec.push)):
        if value is not None:
            out.append((var, value))
    out.append(("push-backoff", _plain(spec.push_backoff)))
    out.append(("max-bytes", "none" if spec.max_bytes is None else str(spec.max_bytes)))
    return out


def save_spec(spec: Spec, *, wait: float = 30) -> None:
    """Write `spec` into its repo's git config as `[moxi-repo "spec.<name>"]`, replacing
    what was saved under that name, so `commit_saved` and `moxi repo commit` run it.

    Its callables must be `"pkg.mod:fn"` strings; a `message` is saved as the summary line
    it is. Raises SpecError for a spec that can't be saved, and `moxi.files.LockTimeout`
    when a pass holds the committer's lock past `wait` seconds.
    """
    problem = spec_problem(spec)
    if problem:
        raise SpecError(problem)
    for label, value in (("message", spec.message), ("trailers", spec.trailers),
                         ("prepare", spec.prepare)):
        if callable(value):
            raise SpecError(f"{label} is a callable; a saved spec takes a 'pkg.mod:fn' "
                            "string" + (" (or a summary line)" if label == "message" else ""))
    where = _where(spec.root)
    with _take(where, wait):
        section = f"{SPEC_PREFIX}{spec.name}"
        if spec.name in _config_specs(where.cgd):
            done = _config_write(where.top, "--remove-section", section)
            if done.returncode != 0:
                raise SpecError(f"git config could not replace the saved spec: "
                                f"{done.stderr.strip()}")
        for var, value in _fields(spec):
            done = _config_write(where.top, "--add", f"{section}.{var}", value)
            if done.returncode != 0:
                raise SpecError(f"git config could not save {var}: {done.stderr.strip()}")
    _index(where)


def _config_write(top: Path, *args: str):
    """One `git config --local` write; while another git holds the config lock, it is
    tried twice more, 20 ms and 50 ms later."""
    for delay in (0, 0.02, 0.05):
        if delay:
            time.sleep(delay)
        done = git(top, "config", "--local", *args)
        if done.returncode == 0:
            break
    return done


def remove_spec(root, name: str, *, wait: float = 30) -> list[str]:
    """Forget a spec, saved or not: its saved section, its state-file entry and its doctor
    records. Returns a line for each thing removed (none when there was nothing)."""
    where = _where(root)
    lines: list[str] = []
    with _take(where, wait):
        if name in _config_specs(where.cgd):
            done = _config_write(where.top, "--remove-section", f"{SPEC_PREFIX}{name}")
            if done.returncode != 0:
                raise SpecError(f"git config could not remove the saved spec {name}: "
                                f"{done.stderr.strip()}")
            lines.append(f"removed the saved spec {name} from {where.top}")
        if name in read_state(where.cgd)["specs"]:
            _change_state(where.cgd, lambda specs: specs.pop(name, None))
            lines.append(f"removed {name}'s state from {where.top}")
        for key in _spec_keys(where.cgd, name):
            _records.close_record(key)
            lines.append(f"cleared the doctor record {key}")
    return lines


def _spec_keys(cgd, name: str) -> list[str]:
    want = _records.key_of(cgd)
    return sorted(k for k, v in _records.ledger().items()
                  if v.get("cgd") == want and v.get("kind") in ("commit", "push")
                  and (v.get("subject") or {}).get("spec") == name)


# -- one pass -----------------------------------------------------------------------------

class _Pass:
    """What one pass found and did, gathered as it goes."""

    def __init__(self, name: str, where: _Where | None):
        self.name, self.where = name, where
        self.status: str | None = None
        self.detail = ""
        self.sha: str | None = None
        self.files: list[str] = []
        self.left: list[tuple[str, str]] = []
        self.dropped = False
        self.unjudged: list[str] = []
        self.pushed: list[str] = []
        self.push_errors: list[str] = []
        self.hook_lines: list[str] = []
        self.detached = False
        #: branch -> (text, fix) to write, or None to clear
        self.push_records: dict[str, tuple[str, str] | None] = {}
        #: branch -> its state entry, or None to drop it
        self.push_state: dict[str, dict | None] = {}
        #: paths this pass staged and could not unstage: path -> staged blob
        self.stranded: dict[str, str | None] = {}
        #: the lines of the push part's successful pushes (also in `hook_lines`)
        self.push_lines: list[str] = []
        #: branch -> the sha a push of it sent, or None to forget it
        self.pushed_sha: dict[str, str | None] = {}
        #: True once git commit has made the commit
        self.landed = False

    def end(self, status: str, detail: str = "") -> None:
        self.status, self.detail = status, detail

    def result(self) -> CommitResult:
        return CommitResult(
            status=self.status or "error", detail=self.detail, sha=self.sha,
            files=tuple(self.files), left=tuple(self.left), unjudged=tuple(self.unjudged),
            pushed=tuple(self.pushed),
            push_error="; ".join(self.push_errors) or None,
            hook_lines=tuple(self.hook_lines), spec=self.name, detached=self.detached)


def commit(spec: Spec, *, wait: float = 0) -> CommitResult:
    """Run one pass of `spec`. Never raises.

    `wait` is how long to wait for another pass's lock (0: try once, and a held lock
    returns `busy` at once)."""
    name = getattr(spec, "name", "")
    try:
        if not isinstance(spec, Spec):
            return CommitResult("error", spec_problem(spec) or "not a spec", spec=str(name))
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            return CommitResult("error", spec_problem(spec) or "bad name", spec=str(name))
        try:
            where = _where(spec.root)
        except SpecError as exc:
            return CommitResult("error", str(exc), spec=name)
        return _run(name, where, wait, lambda: spec)
    except Exception as exc:  # noqa: BLE001 - a pass never raises at its caller
        return CommitResult("error", f"the pass failed: {_describe(exc)}", spec=str(name))


def commit_saved(root, name: str, *, wait: float = 0) -> CommitResult:
    """Run one pass of the spec saved as `name` in the repo at `root`. Never raises."""
    try:
        try:
            where = _where(root)
        except SpecError as exc:
            return CommitResult("error", str(exc), spec=name)

        def load() -> Spec | None:
            section = _config_specs(where.cgd).get(name)
            if section is None:
                return None
            return _from_section(where.top, name, section)

        return _run(name, where, wait, load)
    except Exception as exc:  # noqa: BLE001
        return CommitResult("error", f"the pass failed: {_describe(exc)}", spec=name)


def _run(name: str, where: _Where, wait: float, load: Callable[[], Spec | None]
         ) -> CommitResult:
    p = _Pass(name, where)
    with _locked(where, wait) as held:
        if not held:
            p.end("busy", "another pass holds the committer's lock"
                  + (f" (waited {_plain(wait)} s)" if wait else ""))
            return p.result()
        state = read_state(where.cgd)["specs"].get(name) or {}
        try:
            spec = load()
        except SpecError as exc:
            p.end("error", str(exc))
            _record_part(p, None, state)
            return p.result()
        if spec is None:
            p.end("error", f"no spec named {name} is saved in {where.top}")
            return p.result()
        problem = spec_problem(spec)
        if problem:
            p.end("error", problem)
        else:
            try:
                _commit_part(spec, p, state)
            except Exception as exc:  # noqa: BLE001 - the push and record parts still run
                p.end("error", str(exc) if isinstance(exc, SpecError) else _describe(exc))
        if _pushable(spec):
            try:
                _push_part(spec, p, state)
            except Exception as exc:  # noqa: BLE001 - the record part still runs
                p.push_errors.append(f"the push part failed: {_describe(exc)}")
        _record_part(p, spec, state)
    return p.result()


# -- the commit part ----------------------------------------------------------------------

def _g(p: _Pass, *args: str, **kw):
    return git(p.where.top, *args, **kw)


def _stdin(paths) -> bytes:
    return b"".join(os.fsencode(literal_pathspec(x)) + b"\0" for x in paths)


def _index_locked(p: _Pass) -> bool:
    return (p.where.git_dir / "index.lock").exists()


def _bre(text: str) -> str:
    return re.sub(r"([\\.\[\]*^$])", r"\\\1", text)


def _last_commit(p: _Pass, author: tuple[str, str]) -> float | None:
    return last_commit_time(p.where.top, author)


def last_commit_time(top, author: tuple[str, str]) -> float | None:
    """The commit time of `author`'s newest commit on any ref of the repo at `top`, or
    None when there is none."""
    found = last_commit(top, author)
    return None if found is None else found[1]


def last_commit(top, author: tuple[str, str]) -> tuple[str, float] | None:
    """The hash and commit time of `author`'s newest commit on any ref of the repo at
    `top`, or None when there is none."""
    who = f"^{_bre(author[0])} <{_bre(author[1])}>$"
    done = git(top, "log", "--all", "-1", "--basic-regexp", f"--author={who}",
               "--format=%H %ct")
    sha, _, when = done.stdout.strip().partition(" ")
    try:
        return (sha, float(when)) if done.returncode == 0 and sha else None
    except ValueError:
        return None


def _dirty(p: _Pass, paths) -> list[tuple[str, str]]:
    done = _g(p, "status", "--porcelain", "-z", "--untracked-files=all", "--no-renames",
              "--", *(literal_pathspec(x) for x in paths), binary=True)
    if done.returncode != 0:
        raise GitFailed(("status",), done.returncode, done.stderr)
    out = []
    for item in done.stdout.split(b"\0"):
        if len(item) > 3:
            out.append((item[:2].decode("ascii", "replace"), os.fsdecode(item[3:])))
    return out


def _staged(p: _Pass, paths, *, renames: bool) -> list[Change]:
    return changes(p.where.top, staged=True, paths=paths, renames=renames)


def _blobs(entries: list[Change]) -> dict[str, str | None]:
    """Path -> staged blob (None for a deletion), with a rename as its two sides."""
    out: dict[str, str | None] = {}
    for c in entries:
        if c.old_path is not None:
            out[c.old_path] = None
        out[c.path] = c.blob
    return out


def _unstage(p: _Pass, spec: Spec, paths: list[str], judged: dict) -> bool:
    """Unstage `paths` and check they are no longer staged. While another git holds the
    index lock, try again twice; what still can't be unstaged is kept as stranded, so the
    next pass knows it as its own."""
    if not paths:
        return True
    data = _stdin(paths)
    for delay in (0, 0.05, 0.2):
        if delay:
            time.sleep(delay)
        _g(p, "reset", "-q", "--pathspec-from-file=-", "--pathspec-file-nul", input=data)
        still = _blobs(_staged(p, spec.paths, renames=False))
        left = [x for x in paths if x in still]
        if not left:
            return True
        if not _index_locked(p):
            break
    for x in left:
        p.stranded[x] = judged.get(x, still.get(x))
    return False


def _commit_part(spec: Spec, p: _Pass, state: dict) -> None:
    now = _now()
    since = _last_commit(p, spec.author)
    if spec.min_interval and since is not None and now - since < spec.min_interval:
        p.end("throttled", f"the last commit by {spec.author[0]} was {int(now - since)} s "
                           f"ago; the interval is {_plain(spec.min_interval)} s")
        return
    stranded = state.get("stranded") or {}
    for attempt in (1, 2):
        candidates = _dirty(p, spec.paths)
        if not candidates:
            p.end("clean")
            return
        before = {path: blob for path, blob in
                  _blobs(_staged(p, spec.paths, renames=False)).items()
                  if not (path in stranded and stranded[path] == blob)}
        p.left = []
        stage = []
        for _, path in candidates:
            if path in before:
                p.left.append((path, "staged by someone else"))
            elif path.endswith("/"):
                p.left.append((path, "a nested repository"))
            elif spec.max_bytes is not None and _size(p.where.top / path) > spec.max_bytes:
                p.left.append((path, f"over {spec.max_bytes} bytes"))
            else:
                stage.append(path)
        if not stage:
            p.end("clean")
            return
        done = _g(p, "add", "--all", "--pathspec-from-file=-", "--pathspec-file-nul",
                  input=_stdin(stage))
        if done.returncode == 0:
            break
        if _index_locked(p):
            p.end("index-busy", "another git holds the index lock")
            return
        if attempt == 2:
            p.end("error", "git add failed twice: " + _last_line(done.stderr))
            return
    try:
        _after_staging(spec, p, before, stage, since)
    except Exception as exc:  # noqa: BLE001 - a failure after staging unstages our paths
        p.end("error", str(exc) if isinstance(exc, SpecError) else _describe(exc))
        if not p.landed:
            _undo_staging(p, spec, stage)


def _ours(staged_now: list[Change], before: dict) -> list[Change]:
    """Our staged changes: what is staged now minus what was staged before the pass. When
    git pairs one of our paths with someone else's as a rename, our side stays ours, as
    the plain add or deletion it is."""
    out = []
    for c in staged_now:
        if c.old_path is None:
            if c.path not in before:
                out.append(c)
            continue
        new_ours, old_ours = c.path not in before, c.old_path not in before
        if new_ours and old_ours:
            out.append(c)
        elif new_ours:
            out.append(Change("A", c.path, None, c.blob))
        elif old_ours:
            out.append(Change("D", c.old_path, None, None))
    return out


def _sides(ours: list[Change]) -> list[str]:
    return [x for c in ours for x in ((c.path, c.old_path) if c.old_path else (c.path,))]


def _undo_staging(p: _Pass, spec: Spec, stage: list[str]) -> None:
    """After a failure, unstage what step 5 staged; what can't be is kept as stranded."""
    try:
        _unstage(p, spec, stage, {})
    except Exception as exc:  # noqa: BLE001
        p.detail += f"; its paths could not be unstaged: {_describe(exc)}"


def _after_staging(spec: Spec, p: _Pass, before: dict, stage: list[str],
                   since: float | None) -> None:
    """Steps 6-10: ours, prepare, commit. Any path step 5 staged that isn't ours is
    unstaged first, so nothing the pass staged is left in the index."""
    ours = _ours(_staged(p, spec.paths, renames=True), before)
    judged = _blobs(ours)
    every = _sides(ours)
    stray = sorted(set(stage) - set(every))
    if stray:
        _unstage(p, spec, stray, {})
    if not ours:
        p.end("clean")
        return

    # Prepare: what to drop, then unstage it and verify.
    if spec.prepare is not None:
        try:
            fn = _load(spec.prepare, "prepare")
            got = fn(PrepareContext(root=p.where.top, changes=tuple(ours), spec=spec.name))
        except Exception as exc:  # noqa: BLE001 - a prepare step that raises is an error
            _back_out(p, spec, every, judged, "error", f"prepare raised {_describe(exc)}")
            return
        if not isinstance(got, Prepared):
            _back_out(p, spec, every, judged, "error",
                      f"prepare returned {type(got).__name__}, not a Prepared")
            return
        if got.refuse is not None:
            _back_out(p, spec, every, judged, "refused", f"prepare refused: {got.refuse}")
            return
        ours = _drop(p, spec, ours, dict(got.drop), judged)
        if ours is None:
            return
        judged = _blobs(ours)
        every = _sides(ours)
        if not ours:
            p.end("clean")
            return

    # Commit, our paths on stdin.
    try:
        message = _message(spec, ours)
        trailers = _trailers(spec, ours, since)
    except SpecError as exc:
        _back_out(p, spec, every, judged, "error", str(exc))
        return
    from moxi import files

    message_file = p.where.own / MESSAGE
    files.atomic_write(message_file, message.encode("utf-8", "surrogateescape"))
    args = ["commit", "-q", "-F", str(message_file)]
    for key, value in trailers:
        args += ["--trailer", f"{key}={value}"]
    args += ["--pathspec-from-file=-", "--pathspec-file-nul"]
    old_head = head_commit(p.where.top)
    try:
        done = _g(p, *args, input=_stdin(every), author=tuple(spec.author),
                  extra_env={COMMITTER_ENV: spec.name}, timeout=COMMIT_TIMEOUT)
        code, out = done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired as exc:
        code = None
        out = _text(exc.stdout) + _text(exc.stderr)
    p.hook_lines = [line.rstrip() for line in out.splitlines() if line.strip()]
    if code != 0:
        busy = _index_locked(p)
        if not _unstage(p, spec, every, judged) and not busy:
            p.end("error", "the commit failed and its paths could not be unstaged")
            return
        if busy or _index_locked(p):
            p.end("index-busy", "another git holds the index lock")
        elif code is None:
            p.end("refused", f"git commit ran past {int(COMMIT_TIMEOUT)} s and was stopped")
        else:
            p.end("refused", p.hook_lines[0] if p.hook_lines else f"git commit exited {code}")
        return
    p.landed = True
    sha = _our_commit(p, spec, old_head)
    if sha is None:
        p.end("committed", "HEAD moved before the commit could be read back")
        return
    tree = _g(p, "diff-tree", "-r", "-z", "--no-renames", "--raw", "--root", "--no-commit-id",
              sha, binary=True)
    committed = _blobs(parse_raw(tree.stdout)) if tree.returncode == 0 else {}
    p.sha = sha
    p.files = sorted(committed)
    p.unjudged = sorted(x for x, blob in committed.items()
                        if x not in judged or judged[x] != blob)
    p.end("committed")


def _our_commit(p: _Pass, spec: Spec, old_head: str | None) -> str | None:
    """The commit this pass made: `HEAD` when its parent is `old_head`, the `HEAD` just
    before the commit. When another git committed right after, the commit by the spec's
    author whose parent is `old_head`; None when there is none to be sure of."""
    want = [old_head] if old_head else []
    done = _g(p, "rev-list", "-1", "--parents", "HEAD")
    fields = done.stdout.split()
    if done.returncode == 0 and fields and fields[1:] == want:
        return fields[0]
    who = f"^{_bre(spec.author[0])} <{_bre(spec.author[1])}>$"
    done = _g(p, "rev-list", "--reverse", "--parents", "--basic-regexp", f"--author={who}",
              "HEAD", *([f"^{old_head}"] if old_head else []))
    for line in done.stdout.splitlines() if done.returncode == 0 else ():
        fields = line.split()
        if fields[1:] == want:
            return fields[0]
    return None


def _size(path: Path) -> int:
    try:
        return path.lstat().st_size
    except OSError:
        return 0


def _last_line(text: str) -> str:
    lines = [x for x in (text or "").splitlines() if x.strip()]
    return lines[-1].strip() if lines else "no message"


def _text(data) -> str:
    if data is None:
        return ""
    return data.decode("utf-8", "surrogateescape") if isinstance(data, bytes) else data


def _back_out(p: _Pass, spec: Spec, every: list[str], judged: dict, status: str,
              detail: str) -> None:
    """Unstage everything of ours, then end the commit part."""
    if not _unstage(p, spec, every, judged) and _index_locked(p):
        p.end("index-busy", "another git holds the index lock")
        return
    p.end(status, detail)


def _drop(p: _Pass, spec: Spec, ours: list[Change], drops: dict[str, str],
          judged: dict) -> list[Change] | None:
    """Unstage what prepare dropped (a rename whole; every deletion too when an added file
    is dropped), verify, and return what is left; None when the pass ended."""
    gone: list[str] = []
    kept: list[Change] = []
    added = False
    for c in ours:
        why = drops.get(c.path)
        if why is None and c.old_path is not None:
            why = drops.get(c.old_path)
        if why is None:
            kept.append(c)
            continue
        p.dropped = True
        p.left.append((c.path, f"dropped: {why}"))
        gone.append(c.path)
        if c.old_path is not None:
            p.left.append((c.old_path, f"dropped with its rename to {c.path}: {why}"))
            gone.append(c.old_path)
        if c.status in ("A", "R", "C"):
            added = True
    if not gone:
        return ours
    if added:
        rest = []
        for c in kept:
            if c.status == "D":
                p.left.append((c.path, "held back: an added file was dropped"))
                gone.append(c.path)
            else:
                rest.append(c)
        kept = rest
    if not _unstage(p, spec, gone, judged):
        if _index_locked(p):
            p.end("index-busy", "another git holds the index lock")
        else:
            still = _blobs(_staged(p, spec.paths, renames=False))
            stuck = [x for x in gone if x in still]
            p.end("error", "a dropped path is still staged: " + _named(stuck))
        _unstage(p, spec, _sides(kept), judged)
        return None
    return kept


def _message(spec: Spec, ours: list[Change]) -> str:
    if spec.message is None:
        n = len(ours)
        return f"{spec.name}: {n} path{'s' if n != 1 else ''}\n"
    if isinstance(spec.message, str):
        text = spec.message
    else:
        try:
            text = spec.message(tuple(ours))
        except Exception as exc:  # noqa: BLE001
            raise SpecError(f"message raised {_describe(exc)}") from None
        if not isinstance(text, str) or not text.strip():
            raise SpecError("message returned no text")
    return text.rstrip("\n") + "\n"


def _trailers(spec: Spec, ours: list[Change], since: float | None) -> list[tuple[str, str]]:
    if spec.trailers is None:
        return []
    fn = _load(spec.trailers, "trailers")
    try:
        got = list(fn(tuple(ours), since) or ())
    except Exception as exc:  # noqa: BLE001
        raise SpecError(f"trailers raised {_describe(exc)}") from None
    out = []
    for item in got:
        try:
            key, value = item
        except (TypeError, ValueError):
            raise SpecError(f"trailers gave {item!r}, not a (key, value) pair") from None
        key, value = str(key), str(value)
        if not _TRAILER_KEY.fullmatch(key) or "\n" in value or not value.strip():
            raise SpecError(f"trailer {key}: {value!r}: a key is letters, digits and '-', "
                            "a value one non-empty line")
        out.append((key, value))
    return out


# -- the push part ------------------------------------------------------------------------

def _push_part(spec: Spec, p: _Pass, state: dict) -> None:
    """Push each branch of the push set that is ahead: the branch HEAD is on and every
    branch with an open push record for this spec."""
    open_branches = sorted((state.get("push") or {}).keys())
    head = _g(p, "symbolic-ref", "-q", "HEAD")
    current = None
    if head.returncode == 0 and head.stdout.strip().startswith("refs/heads/"):
        current = head.stdout.strip()[len("refs/heads/"):]
    else:
        p.detached = True
    branches = ([current] if current else []) + [b for b in open_branches if b != current]
    remotes: list[str] | None = None
    pushed_sha = state.get("pushed_sha") or {}
    for b in branches:
        if spec.push is None:
            _push_clear(p, b, state)
            if b in pushed_sha:
                p.pushed_sha[b] = None
            continue
        tip = _g(p, "rev-parse", "-q", "--verify", f"refs/heads/{b}")
        if tip.returncode != 0:
            _push_clear(p, b, state)
            if b in pushed_sha:
                p.pushed_sha[b] = None
            continue
        tip_sha = tip.stdout.strip()
        if remotes is None:
            remotes = _g(p, "remote").stdout.split()
        r = spec.push
        if r not in remotes:
            error = f"remote {r} is not configured"
            p.push_errors.append(f"{b}: {error}")
            p.push_state[b] = {"error": error}
            p.push_records[b] = (
                f"repo: {spec.name} can't push {b}: {error}",
                f"add the remote {r} to {p.where.top}, or save the spec without push")
            continue
        count = _g(p, "rev-list", "--count", f"refs/remotes/{r}/{b}..refs/heads/{b}")
        if count.returncode == 0 and count.stdout.strip() == "0":
            _push_clear(p, b, state)
            continue
        if count.returncode != 0 and tip_sha and pushed_sha.get(b) == tip_sha:
            # No tracking ref (a push-only remote): the last push sent this very commit.
            _push_clear(p, b, state)
            continue
        failed_at = ((state.get("push") or {}).get(b) or {}).get("failed_at")
        if failed_at is not None and _now() - failed_at < spec.push_backoff:
            continue
        _push_one(spec, p, r, b, tip_sha)


def _pushable(spec: Spec) -> bool:
    """Whether the spec's push fields can be read, so the push part can run even when
    another field is wrong."""
    backoff = spec.push_backoff
    return ((spec.push is None or (isinstance(spec.push, str)
                                   and bool(_REMOTE.fullmatch(spec.push))))
            and isinstance(backoff, (int, float)) and not isinstance(backoff, bool))


def _push_clear(p: _Pass, branch: str, state: dict) -> None:
    if branch in (state.get("push") or {}):
        p.push_state[branch] = None
    p.push_records[branch] = None


def _push_one(spec: Spec, p: _Pass, remote: str, branch: str, tip: str) -> None:
    try:
        done = _g(p, "push", "-q", remote, f"refs/heads/{branch}:refs/heads/{branch}",
                  timeout=PUSH_TIMEOUT)
        code, out = done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired as exc:
        code, out = None, _text(exc.stdout) + _text(exc.stderr)
    lines = [line.rstrip() for line in out.splitlines() if line.strip()]
    if code == 0:
        p.pushed.append(branch)
        p.push_state[branch] = None
        p.push_records[branch] = None
        if tip:
            p.pushed_sha[branch] = tip
        said = [x for x in lines if not x.startswith("remote:")]
        p.hook_lines += said
        p.push_lines += said
        return
    error = (f"{remote} did not answer within {int(PUSH_TIMEOUT)} s" if code is None
             else _push_reason(lines, code))
    p.push_errors.append(f"{branch}: {error}" if branch else error)
    p.push_state[branch] = {"failed_at": _now(), "error": error, "lines": lines[-20:]}
    p.push_records[branch] = (
        f"repo: {spec.name} push of {branch} to {remote} failed: {error}"
        + "".join(f"\n  {x}" for x in lines[-20:]),
        f"fix the remote or what refused the push; the first pass after "
        f"{_plain(spec.push_backoff)} s tries again (moxi repo commit {quote_arg(p.where.top)})")


def _push_reason(lines: list[str], code: int) -> str:
    """The failed push in one line: a git hook's refusal, else git's rejection, else its
    error, with the remote's first message."""
    picks = [x for x in lines if x.startswith("repo:")][:1]
    if not picks:
        picks = [x.strip() for x in lines if x.lstrip().startswith("!")][:1]
    if not picks:
        picks = [x for x in lines if x.startswith(("fatal:", "error:"))][:1]
    remote = [x for x in lines if x.startswith("remote:") and x[7:].strip()][:1]
    return "; ".join(picks + remote) or (lines[-1] if lines else f"git push exited {code}")


# -- the record part ----------------------------------------------------------------------

def _record_part(p: _Pass, spec: Spec | None, old: dict) -> None:
    """The state file first, then the doctor records, then the sweep of this repo."""
    now = _now()
    status = p.status or "error"
    conclusive = status in CONCLUSIVE
    entry = json.loads(json.dumps(old)) if old else {}
    entry["last"] = {"status": status, "detail": p.detail, "time": now, "sha": p.sha,
                     "line": p.result().line()}
    if status == "refused":
        entry.setdefault("refused_since", now)
        entry["refused_passes"] = int(entry.get("refused_passes") or 0) + 1
    elif conclusive:
        entry.pop("refused_since", None)
        entry.pop("refused_passes", None)
    if status == "index-busy":
        entry.setdefault("index_busy_since", now)
        entry["index_busy_passes"] = int(entry.get("index_busy_passes") or 0) + 1
    elif conclusive:
        entry.pop("index_busy_since", None)
        entry.pop("index_busy_passes", None)
    if conclusive:
        before = entry.get("left") or {}
        entry["left"] = {path: {"since": (before.get(path) or {}).get("since", now),
                                "why": why} for path, why in p.left}
    push = dict(entry.get("push") or {})
    for branch, value in p.push_state.items():
        if value is None:
            push.pop(branch, None)
        else:
            if "failed_at" not in value and branch in push:
                value = {**push[branch], **value}
            push[branch] = value
    entry["push"] = push
    pushed_sha = dict(entry.get("pushed_sha") or {})
    for branch, sha in p.pushed_sha.items():
        if sha is None:
            pushed_sha.pop(branch, None)
        else:
            pushed_sha[branch] = sha
    if pushed_sha:
        entry["pushed_sha"] = pushed_sha
    else:
        entry.pop("pushed_sha", None)
    stranded = {} if conclusive else dict(entry.get("stranded") or {})
    stranded.update(p.stranded)
    if stranded:
        entry["stranded"] = stranded
    else:
        entry.pop("stranded", None)
    _change_state(p.where.cgd, lambda specs: specs.__setitem__(p.name, entry))

    ledger = _records.ledger()
    key = _records.commit_key(p.where.cgd, p.name)
    stale = (status == "index-busy"
             and int(entry.get("index_busy_passes") or 0) >= STALE_INDEX_PASSES)
    # A pass that saw nothing conclusive still writes the lines its pushes printed.
    if conclusive or stale or p.push_lines:
        text = _commit_text(p, entry, stale)
        if text:
            _records.open_record(key, cgd=p.where.cgd, kind="commit",
                                 subject={"spec": p.name}, text=text,
                                 fix=_commit_fix(p, stale))
        elif key in ledger:
            _records.close_record(key)
    for branch, value in sorted(p.push_records.items()):
        pkey = _records.push_key(p.where.cgd, p.name, branch)
        if value is None:
            if pkey in ledger:
                _records.close_record(pkey)
        else:
            _records.open_record(pkey, cgd=p.where.cgd, kind="push",
                                 subject={"spec": p.name, "branch": branch},
                                 text=value[0], fix=value[1])
    _index(p.where)
    _records.sweep(p.where.cgd)


def _commit_text(p: _Pass, entry: dict, stale: bool) -> str | None:
    """The commit record's text, or None when the pass has none of its conditions."""
    if stale:
        return (f"repo: {p.name}: git's index lock {p.where.git_dir / 'index.lock'} has "
                f"been held for {entry['index_busy_passes']} passes in a row, since "
                f"{_stamp(entry.get('index_busy_since'))}")
    if not (p.status in ("refused", "error") or p.dropped or p.unjudged or p.hook_lines):
        return None
    text = p.result().line()
    if p.status == "refused":
        text += (f" (refused since {_stamp(entry.get('refused_since'))}, "
                 f"{entry.get('refused_passes', 1)} passes)")
    lines = p.hook_lines[:40]
    text += "".join(f"\n  {x}" for x in lines)
    if len(p.hook_lines) > len(lines):
        text += f"\n  and {len(p.hook_lines) - len(lines)} more lines"
    return text


def _commit_fix(p: _Pass, stale: bool) -> str:
    if stale:
        return (f"if no git is running in {p.where.top}, remove "
                f"{p.where.git_dir / 'index.lock'}")
    return (f"the spec's next pass that commits cleanly clears this; to forget the spec: "
            f"moxi repo specs remove {quote_arg(p.where.top)} {p.name}")


def _index(where: _Where) -> None:
    """Put the repo in repo's index, when it isn't there yet."""
    from moxi_repo import api, gitconf

    if _records.key_of(where.cgd) in api.indexed():
        return
    try:
        api._index_put(gitconf.resolve(where.top))
    except gitconf.NotARepo:
        pass


# -- what the sweep asks ------------------------------------------------------------------

def spec_gone(cgd, kind: str, subject: dict) -> str | None:
    """Why a spec or push key's subject is gone, or None while it stands: the spec is
    neither saved nor in the repo's state file; a saved spec's `push` is unset; a push
    record's branch no longer exists."""
    name = subject.get("spec") or ""
    saved = _config_specs(cgd).get(name)
    if saved is None and name not in read_state(cgd)["specs"]:
        return "its spec is neither saved nor in the repo's state file"
    if kind == "push":
        if saved is not None and not _one(saved, "push"):
            return "its saved spec no longer pushes"
        branch = subject.get("branch") or ""
        done = git(cgd, f"--git-dir={cgd}", "rev-parse", "-q", "--verify",
                   f"refs/heads/{branch}")
        if done.returncode != 0:
            return "its branch is gone"
    return None


def clear_gone(cgd) -> list:
    """Clear each spec or push key of the repo whose subject is gone, under the committer's
    lock: taken try-once, and when another pass holds it the keys are left for later.
    Returns the sweep's findings for what was cleared."""
    cgd = Path(cgd)
    if _records.key_of(cgd) in _held_here():
        return _clear_gone(cgd)
    where = _Where(cgd, cgd, cgd)
    with _locked(where, 0) as held:
        if not held:
            return []
        return _clear_gone(cgd)


def _clear_gone(cgd: Path) -> list:
    want = _records.key_of(cgd)
    found = []
    for key, entry in sorted(_records.ledger().items()):
        if entry.get("cgd") != want or entry.get("kind") not in ("commit", "push"):
            continue
        subject = entry.get("subject") or {}
        why = spec_gone(cgd, entry["kind"], subject)
        if why:
            _records.close_record(key)
            found.append(_records.Finding(key, "clear", why, want, subject))
    return found


# -- reading ------------------------------------------------------------------------------

def listing(root) -> list[dict]:
    """The repo's saved specs and the specs that ran without being saved, each with its
    last result, as `moxi repo specs --json` prints them. `push` holds each branch whose
    last push failed, and `pushed` each branch's last pushed commit."""
    where = _where(root)
    saved = _config_specs(where.cgd)
    state = read_state(where.cgd)["specs"]
    keys = _records.ledger()
    want = _records.key_of(where.cgd)
    out = []
    for name in sorted(set(saved) | set(state)):
        entry = state.get(name) or {}
        item: dict = {"name": name, "saved": name in saved, "spec": None, "problem": None,
                      "last": entry.get("last"), "refused_since": entry.get("refused_since"),
                      "refused_passes": entry.get("refused_passes"),
                      "push": entry.get("push") or {},
                      "pushed": entry.get("pushed_sha") or {}, "left": entry.get("left") or {},
                      "records": sorted(k for k, v in keys.items() if v.get("cgd") == want
                                        and (v.get("subject") or {}).get("spec") == name
                                        and v.get("kind") in ("commit", "push"))}
        if name in saved:
            try:
                spec = _from_section(where.top, name, saved[name])
                item["spec"] = {"paths": list(spec.paths), "author": list(spec.author),
                                "min_interval": spec.min_interval, "message": spec.message,
                                "trailers": spec.trailers, "prepare": spec.prepare,
                                "push": spec.push, "push_backoff": spec.push_backoff,
                                "max_bytes": spec.max_bytes}
            except SpecError as exc:
                item["problem"] = str(exc)
        out.append(item)
    return out


__all__ = ["Spec", "CommitResult", "PrepareContext", "Prepared", "SpecError", "commit",
           "commit_saved", "save_spec", "remove_spec", "saved_spec", "saved_names",
           "spec_problem", "spec_gone", "clear_gone", "listing", "read_state", "STATUSES",
           "CONCLUSIVE", "COMMITTER_ENV"]
