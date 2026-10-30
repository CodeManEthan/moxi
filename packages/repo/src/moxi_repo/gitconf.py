"""Registrations in a repo's own git config, the mode, and what reaches the runner.

Everything repo keeps about a repo's git hooks is in that repo's local config, which git
never tracks:

    [moxi-repo]
        mode = config                  ; config or shim: what repo wrote
        mode-set = auto                ; auto, config or shim: what the user pinned
        moved = pre-push               ; events whose existing git hook repo moved to .local
    [moxi-repo "piece.notes/secret-gate"]
        event = pre-commit
        target = moxi_notes.gates:pre_commit
        fail = closed
        order = 20
        enabled = true
        refused-name = ...             ; the piece's own settings, if it declares any
        recorded = true                ; while a doctor record of the piece is open
    [hook "moxi-repo-pre-commit"]      ; config mode: one entry per event
        command = <the guard>; '<python>' -I -m moxi_repo.run pre-commit
        event = pre-commit

The rewrite rule: a rewrite of a piece's section sets only the keys its declaration gives
(`event`, `target`, `fail`, `order`) and leaves every other key as it was. `enabled` is
written once when the piece is first registered and then only by `enable` and `disable`;
the settings only by `--set`; `recorded` only by the runner and the sweep.

`sync` makes an event's config entry, shim or launcher match its registered pieces, and
runs after every change to them, since the guard's exit status depends on them.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from moxi_repo import shim
from moxi_repo.gitrun import git
from moxi_repo.run import (EVENTS, PIECE_PREFIX, SECTION, STDIN_EVENTS, can_refuse, enabled,
                           fail_policy, last, parse_config, pieces_of)

#: The oldest git repo supports.
FLOOR = (2, 39, 0)
#: The first git that runs config-based hooks.
CONFIG_FROM = (2, 54, 0)
MODES = ("config", "shim")
MODE_SETS = ("auto", "config", "shim")
#: The prefix of repo's config hook names: `hook.moxi-repo-<event>`.
HOOK_PREFIX = "moxi-repo-"


class NotARepo(Exception):
    """The path names no git work tree."""


class GitError(Exception):
    """A git call repo needed failed; the message holds git's own line."""


class Repo:
    """A repo as git sees it from a path: its work tree's top and its git dirs."""

    __slots__ = ("root", "git_dir", "common_dir", "hooks_dir")

    def __init__(self, root: Path, git_dir: Path, common_dir: Path, hooks_dir: Path):
        self.root, self.git_dir = root, git_dir
        self.common_dir, self.hooks_dir = common_dir, hooks_dir

    @property
    def key(self) -> str:
        """The repo's key: its common git dir, `normcase`d, so case-only differences on
        Windows and macOS are one repo."""
        return key_of(self.common_dir)

    @property
    def hooks_inside(self) -> bool:
        """True when the hooks dir is inside the repo's git dir, where repo may write."""
        try:
            return self.hooks_dir.resolve().is_relative_to(self.common_dir.resolve())
        except OSError:
            return False

    @property
    def launcher(self) -> Path:
        return self.common_dir.joinpath(*shim.LAUNCHER)

    @property
    def own_dir(self) -> Path:
        """`<common git dir>/moxi-repo/`: the launcher, the committer's lock and state."""
        return self.common_dir / SECTION

    def __repr__(self) -> str:
        return f"Repo({str(self.root)!r})"


def key_of(common_dir: str | os.PathLike) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(common_dir)))


def _rev_parse(path: str | os.PathLike) -> Repo:
    where = Path(path)
    if not where.is_dir():
        raise NotARepo(f"{path} is not a directory")
    done = git(where, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir",
               "--git-path", "hooks", "--show-toplevel")
    lines = done.stdout.splitlines()
    if done.returncode != 0 or len(lines) < 4:
        why = (done.stderr.strip().splitlines() or ["not a git work tree"])[-1]
        if len(lines) == 3:
            why = "a bare repository has no work tree for git hooks to run in"
        raise NotARepo(f"{path}: {why}")
    git_dir, common, hooks, top = (Path(p) for p in lines[:4])
    return Repo(top, git_dir, common, hooks)


def resolve(path: str | os.PathLike) -> Repo:
    """The repo at or above `path`; raises `NotARepo`.

    Its `root` is the repo's main work tree, from whichever of its work trees `path` is
    in, so a repo has one root however it is named; a linked worktree's own top is the
    root only when the main work tree is bare or gone.
    """
    repo = _rev_parse(path)
    if key_of(repo.git_dir) == repo.key:
        return repo
    for top, bare in worktrees(repo.common_dir)[:1]:
        if bare or not top.is_dir() or key_of(top) == key_of(repo.root):
            break
        try:
            main = _rev_parse(top)
        except NotARepo:
            break
        if main.key == repo.key and key_of(main.git_dir) == main.key:
            return main
    return repo


def worktrees(common_dir: str | os.PathLike) -> list[tuple[Path, bool]]:
    """Each work tree git lists for a common git dir, main first: (its top, bare)."""
    cgd = os.fspath(common_dir)
    done = git(cgd, f"--git-dir={cgd}", "worktree", "list", "--porcelain", "-z")
    if done.returncode != 0:
        return []
    out: list[tuple[Path, bool]] = []
    for field in done.stdout.split("\0"):
        if field.startswith("worktree "):
            out.append((Path(field[len("worktree "):]), False))
        elif field == "bare" and out:
            out[-1] = (out[-1][0], True)
    return out


# -- git's version ------------------------------------------------------------------------

def parse_version(text: str) -> tuple[int, int, int] | None:
    """The first three numbers of `git version`'s line, ignoring any suffix."""
    found = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not found:
        return None
    return int(found.group(1)), int(found.group(2)), int(found.group(3) or 0)


def git_version() -> tuple[int, int, int] | None:
    """The version of the git on PATH, or None when there is none or it can't say."""
    try:
        done = git(".", "version")
    except OSError:
        return None
    return parse_version(done.stdout) if done.returncode == 0 else None


def version_text(version: tuple[int, int, int] | None) -> str:
    return ".".join(map(str, version)) if version else "unknown"


def pick_mode(mode_set: str, default: str, version: tuple[int, int, int] | None) -> str:
    """The mode a repo gets: its pin, else the module's default, else by the git found."""
    if mode_set in MODES:
        return mode_set
    if default in MODES:
        return default
    return "config" if version is not None and version >= CONFIG_FROM else "shim"


# -- the interpreter and the runner -------------------------------------------------------

def interpreter() -> str:
    """The Python entries, shims and the launcher run: the environment's own, never a
    resolved `sys.executable`, so it stays put across an upgrade of the environment."""
    from moxi import paths

    return shim.sh_path(paths.python())


def runner_file() -> str:
    """`moxi_repo/run.py` in this environment, the file the guard tests."""
    from importlib.util import find_spec

    spec = find_spec("moxi_repo.run")
    origin = spec.origin if spec is not None and spec.origin else str(
        Path(__file__).with_name("run.py"))
    return shim.sh_path(origin)


# -- reading ------------------------------------------------------------------------------

class State:
    """What repo's keys in one repo's local git config say."""

    __slots__ = ("mode", "mode_set", "moved", "pieces", "entries", "sections")

    def __init__(self) -> None:
        self.mode: str | None = None
        self.mode_set = "auto"
        self.moved: set[str] = set()
        self.pieces: dict[str, dict[str, list]] = {}
        #: Event -> its config entry's command.
        self.entries: dict[str, str] = {}
        #: Every `moxi-repo` section and subsection, as `--remove-section` names them.
        self.sections: set[str] = set()

    def events(self) -> dict[str, int]:
        """Each event with a registered piece -> the guard's exit status for it."""
        out: dict[str, int] = {}
        for section in self.pieces.values():
            event = last(section, "event")
            if event not in EVENTS:
                continue
            closed = (enabled(section) and fail_policy(section) == "closed"
                      and can_refuse(event))
            out[event] = max(out.get(event, 0), 1 if closed else 0)
        return out


def config(repo: Repo, *args: str):
    """`git config --local <args>` in the repo."""
    return git(repo.root, "config", "--local", *args)


def _get_regexp(repo: Repo, pattern: str) -> list[tuple[str, str | None]]:
    done = git(repo.root, "config", "--local", "-z", "--get-regexp", pattern)
    if done.returncode == 1 and not done.stdout:
        return []
    if done.returncode != 0:
        raise GitError(f"git config could not read {repo.root}: "
                       f"{done.stderr.strip() or f'exit {done.returncode}'}")
    return parse_config(done.stdout.encode("utf-8", "surrogateescape"))


def read(repo: Repo) -> State:
    state = State()
    pairs = _get_regexp(repo, r"^moxi-repo\.")
    for key, value in pairs:
        head, _, var = key.rpartition(".")
        state.sections.add(head)
        if head == SECTION:
            if var == "mode" and value:
                state.mode = value
            elif var == "mode-set" and value:
                state.mode_set = value
            elif var == "moved" and value:
                state.moved.add(value)
    state.pieces = pieces_of(pairs)
    for key, value in _get_regexp(repo, r"^hook\.moxi-repo-"):
        head, _, var = key.rpartition(".")
        event = head[len("hook." + HOOK_PREFIX):]
        if var == "command":
            state.entries[event] = value or ""
        else:
            state.entries.setdefault(event, "")
    return state


# -- writing ------------------------------------------------------------------------------

def _check(done, what: str) -> None:
    if done.returncode != 0:
        raise GitError(f"git config could not {what}: "
                       f"{done.stderr.strip() or f'exit {done.returncode}'}")


def _set(repo: Repo, key: str, value: str) -> None:
    _check(config(repo, "--replace-all", key, value), f"set {key}")


def _unset(repo: Repo, key: str) -> None:
    done = config(repo, "--unset-all", key)
    if done.returncode not in (0, 5):  # 5: the key was already gone
        _check(done, f"unset {key}")


def _remove_section(repo: Repo, name: str) -> None:
    done = config(repo, "--remove-section", name)
    if done.returncode not in (0, 128) or (done.returncode == 128
                                            and "no such section" not in done.stderr):
        _check(done, f"remove section {name}")


def piece_key(piece_id: str, var: str) -> str:
    return f"{PIECE_PREFIX}{piece_id}.{var}"


def register(repo: Repo, piece, state: State) -> bool:
    """Write `piece`'s registration by the rewrite rule; return True when it is new.

    A new registration also gets `enabled = true`. An existing one gets only the keys the
    declaration gives, so `enabled`, its settings and `recorded` stay as they were.
    """
    new = piece.id not in state.pieces
    for var, value in piece.registration().items():
        _set(repo, piece_key(piece.id, var), value)
    if new:
        _set(repo, piece_key(piece.id, "enabled"), "true")
        state.pieces[piece.id] = {}
    section = state.pieces[piece.id]
    for var, value in piece.registration().items():
        section[var] = [value]
    if new:
        section["enabled"] = ["true"]
    return new


def set_enabled(repo: Repo, piece_id: str, value: bool, state: State) -> None:
    _set(repo, piece_key(piece_id, "enabled"), "true" if value else "false")
    state.pieces.setdefault(piece_id, {})["enabled"] = ["true" if value else "false"]


def clear_recorded(repo: Repo, piece_id: str, state: State) -> None:
    """Unset the piece's `recorded` flag, if it is set."""
    section = state.pieces.get(piece_id, {})
    if "recorded" in section:
        _unset(repo, piece_key(piece_id, "recorded"))
        section.pop("recorded", None)


def apply_settings(repo: Repo, piece_id: str, changes: dict[str, list[str] | None],
                   state: State) -> None:
    """Each named setting takes its whole new list, or goes when None; others stay."""
    section = state.pieces.setdefault(piece_id, {})
    for name, values in changes.items():
        key = piece_key(piece_id, name)
        _unset(repo, key)
        section.pop(name, None)
        for value in values or ():
            _check(config(repo, "--add", key, value), f"add {key}")
        if values:
            section[name] = list(values)


def remove_piece(repo: Repo, piece_id: str, state: State) -> None:
    _remove_section(repo, f"{SECTION}.piece.{piece_id}")
    state.pieces.pop(piece_id, None)
    state.sections.discard(f"{SECTION}.piece.{piece_id}")


def set_mode(repo: Repo, state: State, *, mode: str | None = None,
             mode_set: str | None = None) -> None:
    if mode_set is not None:
        _set(repo, f"{SECTION}.mode-set", mode_set)
        state.mode_set = mode_set
    if mode is not None and mode != state.mode:
        _set(repo, f"{SECTION}.mode", mode)
        state.mode = mode
    state.sections.add(SECTION)


def _write_moved(repo: Repo, state: State, moved: set[str]) -> None:
    if moved == state.moved:
        return
    _unset(repo, f"{SECTION}.moved")
    for event in sorted(moved):
        _check(config(repo, "--add", f"{SECTION}.moved", event), "record a moved git hook")
    state.moved = set(moved)


# -- what reaches the runner --------------------------------------------------------------

class Sync:
    """What a `sync` did and found."""

    __slots__ = ("lines", "problems", "chain")

    def __init__(self) -> None:
        #: Things done worth a line (a git hook moved aside, or back).
        self.lines: list[str] = []
        #: Events repo could not set up, with why.
        self.problems: list[str] = []
        #: Event -> (hook file, chain line, present): shim mode outside the git dir.
        self.chain: dict[str, tuple[Path, str, bool]] = {}


def chain_state(repo: Repo, event: str) -> tuple[Path, str, bool]:
    """(the manager's git hook for `event`, its chain line, whether the line is there)."""
    hook = repo.hooks_dir / event
    try:
        text = hook.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    return hook, shim.chain_line(event, event in STDIN_EVENTS), shim.has_chain_line(text, event)


def check_shims(repo: Repo, state: State, events) -> list[str]:
    """Why a shim for each of `events` can't be placed, if any can't; writes nothing."""
    if state.mode != "shim" or not repo.hooks_inside:
        return []
    out = []
    for event in sorted(events):
        try:
            shim.check_place(repo.hooks_dir, event, event in state.moved)
        except shim.ShimRefused as exc:
            out.append(f"{event}: {exc}")
    return out


def sync(repo: Repo, state: State) -> Sync:
    """Make the entries, shims and launcher match `state`'s pieces and mode."""
    from moxi import files

    out = Sync()
    python, runner, root = interpreter(), runner_file(), shim.sh_path(repo.root)
    events = state.events()
    moved = set(state.moved)
    if state.mode == "shim":
        for event in list(state.entries):
            _remove_section(repo, f"hook.{HOOK_PREFIX}{event}")
            state.entries.pop(event, None)
    else:
        for event, _ in sorted(events.items()):
            command = shim.entry_command(event, python, runner, root, events[event])
            _set(repo, f"hook.{HOOK_PREFIX}{event}.command", command)
            _set(repo, f"hook.{HOOK_PREFIX}{event}.event", event)
            state.entries[event] = command
        for event in sorted(set(state.entries) - set(events)):
            _remove_section(repo, f"hook.{HOOK_PREFIX}{event}")
            state.entries.pop(event, None)
    inside = state.mode == "shim" and repo.hooks_inside
    for event in sorted(shim.our_shims(repo.hooks_dir)):
        if not inside or event not in events:
            out.lines += shim.take_away(repo.hooks_dir, event, event in moved)
            moved.discard(event)
    if inside:
        for event, code in sorted(events.items()):
            text = shim.shim_text(event, python, runner, root, code, event in STDIN_EVENTS)
            try:
                done = shim.place(repo.hooks_dir, event, text, event in moved)
            except shim.ShimRefused as exc:
                out.problems.append(f"{event}: {exc}")
                continue
            if done:
                moved.add(event)
                out.lines += done
    outside = state.mode == "shim" and not repo.hooks_inside and events
    if outside:
        closed = [event for event, code in events.items() if code]
        files.atomic_write(repo.launcher, shim.launcher_text(python, runner, root, closed),
                           mode=0o755)
        for event in sorted(events):
            out.chain[event] = chain_state(repo, event)
    elif repo.launcher.exists():
        repo.launcher.unlink()
    _write_moved(repo, state, moved)
    return out


def remove_all(repo: Repo, state: State | None = None) -> list[str]:
    """Remove everything repo wrote to the repo: sections, entries, shims (moving each
    `.local` back), the launcher and `<common git dir>/moxi-repo/`."""
    state = state or read(repo)
    lines: list[str] = []
    for event in sorted(state.entries):
        _remove_section(repo, f"hook.{HOOK_PREFIX}{event}")
    for event in sorted(shim.our_shims(repo.hooks_dir)):
        lines += shim.take_away(repo.hooks_dir, event, event in state.moved)
    for name in sorted(state.sections, key=len, reverse=True):
        _remove_section(repo, name)
    if repo.own_dir.exists():
        shutil.rmtree(repo.own_dir)
    return lines


__all__ = ["Repo", "State", "Sync", "NotARepo", "GitError", "FLOOR", "CONFIG_FROM",
           "resolve", "read", "register", "set_enabled", "clear_recorded", "apply_settings", "remove_piece",
           "set_mode", "sync", "remove_all", "pick_mode", "git_version", "parse_version",
           "interpreter", "runner_file", "chain_state", "check_shims", "key_of", "worktrees"]
