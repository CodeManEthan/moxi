"""The verbs behind `moxi repo git-hooks`, `moxi repo rules` and `moxi repo specs`, for the
CLI and for other modules.

`add`, `remove`, `enable`, `disable` and `refresh` change a repo's git hooks and return a
`Report` of what they did; `status`, `available` and `rules` read. A verb that can't act raises a
`Failure` whose `code` is the exit code (`Usage` 2, `Refused` 3, else 1) and whose text
says what to do.

This module is the one other modules import from repo, so it also hands out the
committer's and the git hook pieces' names: the package's exports (`git`, `Spec`,
`commit_saved`, `HookContext`, `Outcome` and the rest), `SpecError` and `PATHSPEC_VARS`.
Each is the same object as in its home module and is loaded only when first asked for, so
importing this module loads nothing it didn't load before.

A repo is named by a path inside any of its work trees; repo keeps an index of the repos it
has written to (the `repos` store), which `refresh` and listings walk. An indexed repo is
keyed by its common git dir, and its `root` is its main work tree; it is gone only when its
common git dir is gone, so removing a linked worktree, or the work tree it was added
through, leaves it indexed. The git config is the truth: a repo missing from the index
still runs its pieces, and `add PATH` with no piece indexes it again.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

from moxi_repo import EXPORTS as _PACKAGE_EXPORTS
from moxi_repo import _records, gitconf, pieces, shim
from moxi_repo.gitconf import Repo, State
from moxi_repo.run import (EVENTS, enabled, fail_policy, last, order_of, settings_of)
from moxi_repo.run import quote_arg as _arg

MODULE = "repo"
#: The index's file name under the module's state directory.
INDEX = "repos.json"


class Failure(Exception):
    """A verb that could not act. `code` is its exit code."""

    code = 1

    def __init__(self, text: str, *, lines: list[str] | None = None):
        super().__init__(text)
        self.text = text
        self.lines = list(lines or [])


class Usage(Failure):
    code = 2


class Refused(Failure):
    code = 3


class Busy(Failure):
    """Another process holds a lock the verb needs; try again later."""

    code = 75


class Problem(str):
    """One problem a listing names, as the text it prints, carrying the parts the doctor's
    row takes: `detail`, `fix` (the command, or None) and `state` (`fail`, `warn` or `ok`:
    `ok` for something worth saying that needs no action)."""

    def __new__(cls, detail: str, fix: str | None = None, *, state: str = "fail",
                how: str = "run"):
        self = super().__new__(cls, detail if fix is None else f"{detail}; {how}: {fix}")
        self.detail, self.fix, self.state = detail, fix, state
        return self


class Report:
    """What a verb did: lines to print, problems found, and chain lines the user adds."""

    __slots__ = ("lines", "problems", "chain")

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.problems: list[str] = []
        #: (repo root, event, hook file, chain line, present)
        self.chain: list[tuple[str, str, str, str, bool]] = []

    def take(self, root: Path, done: gitconf.Sync) -> None:
        self.lines += done.lines
        self.problems += [f"{root}: {p}" for p in done.problems]
        for event, (hook, line, present) in sorted(done.chain.items()):
            self.chain.append((str(root), event, str(hook), line, present))

    @property
    def ok(self) -> bool:
        return not self.problems


#: The commit-rules piece, which `commit_rules.everywhere` registers.
COMMIT_RULES = "repo/commit-rules"


def _seed_commit_rules() -> dict[str, list[str]]:
    return {"tag-pattern": [str(p) for p in _config()["commit_rules.tag_patterns"]]}


def _check_commit_rules(name: str, values: list[str]) -> str | None:
    from moxi_repo.commit_rules import pattern_problem

    if name != "tag-pattern":
        return None
    for value in values:
        why = pattern_problem(value)
        if why is not None:
            return f"--set tag-pattern: {why}"
    return None


#: Settings a piece starts with in a repo where it is first registered: piece id ->
#: fn() -> {setting: [values]}. A key the same call gives with `--set` (the clearing form
#: included) is not seeded.
SEEDS: dict[str, Callable[[], dict[str, list[str]]]] = {COMMIT_RULES: _seed_commit_rules}

#: Checks of a piece's setting values before anything is written: piece id ->
#: fn(setting, values) -> why they are refused, or None.
SETTING_CHECKS: dict[str, Callable[[str, list[str]], str | None]] = {
    COMMIT_RULES: _check_commit_rules}


def _commit_rules_lines(repo: Repo, settings: dict[str, list[str]]) -> list[str]:
    from moxi_repo.commit_rules import rule_lines

    return rule_lines(settings)


#: The pieces that give a repo commit rules, in the order `rules` prints them: piece id ->
#: fn(repo, the piece's settings there) -> its lines. A repo where none of them is
#: registered has no commit rules.
RULE_LINES: dict[str, Callable[[Repo, dict[str, list[str]]], list[str]]] = {
    COMMIT_RULES: _commit_rules_lines}


# -- the module's config ------------------------------------------------------------------

def _config():
    """repo's settings as they stand."""
    from moxi import config

    from moxi_repo import settings

    try:
        return settings.load()
    except config.ConfigError as exc:
        raise Failure(f"repo's config can't be read: {exc}") from None


def default_mode() -> str:
    """`git_hooks.mode` from repo's config: auto, config or shim."""
    return str(_config()["git_hooks.mode"])


def everywhere() -> bool:
    """`commit_rules.everywhere` from repo's config."""
    return bool(_config()["commit_rules.everywhere"])


# -- the index ----------------------------------------------------------------------------

def index_path() -> Path:
    from moxi import paths

    return paths.Layout(MODULE).state_dir() / INDEX


class IndexUnreadable(Failure):
    """The index file is there and can't be read as an index. Nothing overwrites it, since
    the repos it names can't be found again from anywhere else. `detail` is what is wrong,
    without the fix."""

    def __init__(self, path: Path, why: str, *, writing: bool = False):
        self.path, self.why = path, why
        self.detail = f"repo's index {path} can't be read ({why})"
        super().__init__(self.detail + ("; the index was not changed" if writing else "")
                         + "; fix or remove it, then index each repo again with: "
                         "moxi repo git-hooks add PATH")


def _unreadable(path: Path, why: str) -> IndexUnreadable:
    return IndexUnreadable(path, why)


def _parse_index(path: Path, raw: bytes | None) -> dict:
    """The index file's data, `{"repos": {key: entry}}`; raises `IndexUnreadable` when it
    is there and isn't that. No file, or an empty one, is an empty index."""
    if not raw or not raw.strip():
        return {"repos": {}}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise _unreadable(path, f"not JSON: {exc}") from None
    repos = data.get("repos") if isinstance(data, dict) else None
    if not isinstance(repos, dict) or not all(isinstance(v, dict) for v in repos.values()):
        raise _unreadable(path, 'not {"repos": {key: entry}}')
    return data


def indexed() -> dict[str, dict]:
    """The index: repo key -> {"root", "common_dir"}. Raises `IndexUnreadable` when the
    file is there and can't be read."""
    path = index_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise _unreadable(path, exc.strerror or str(exc)) from None
    return _parse_index(path, raw)["repos"]


def _index_change(fn: Callable[[dict], None]) -> None:
    from moxi import files

    path = index_path()

    def edit(old: bytes | None) -> bytes:
        try:
            data = _parse_index(path, old)
        except IndexUnreadable as exc:
            raise IndexUnreadable(path, exc.why, writing=True) from None
        fn(data["repos"])
        return (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)
                + "\n").encode()

    files.update(path, edit)


def indexed_at(path: str | os.PathLike) -> list[str]:
    """The keys of indexed repos whose root, or key, is `path`. A path that no longer
    exists also names a repo when git still lists it as a work tree of that repo's common
    git dir: a linked worktree whose directory was deleted."""
    want = gitconf.key_of(path)
    index = indexed()
    found = [key for key, entry in index.items()
             if gitconf.key_of(entry.get("root", "")) == want or key == want]
    if found or os.path.lexists(path):
        return found
    for key, entry in index.items():
        common = entry.get("common_dir") or key
        if os.path.isdir(common) and any(gitconf.key_of(top) == want
                                         for top, _ in gitconf.worktrees(common)):
            found.append(key)
    return found


def _index_put(repo: Repo) -> None:
    def put(repos: dict) -> None:
        entry = repos.setdefault(repo.key, {})
        entry["root"] = str(repo.root)
        entry["common_dir"] = str(repo.common_dir)

    _index_change(put)


def _index_drop(key: str) -> None:
    def drop(repos: dict) -> None:
        repos.pop(key, None)

    _index_change(drop)


def gone_text(root: str) -> str:
    return f"{root} is no longer a git work tree"


def open_indexed(key: str, entry: dict) -> tuple[Repo | None, str | None]:
    """An index entry's repo, or (None, why it can't be opened).

    The repo is found through its common git dir: from its stored root, else from any
    work tree git lists for that dir, so a removed linked worktree doesn't lose it. A
    path that now resolves to another repo is not this one.
    """
    common = entry.get("common_dir") or key
    root = entry.get("root") or common
    if not os.path.isdir(common):
        return None, gone_text(root)
    tried = [root] + [str(top) for top, bare in gitconf.worktrees(common) if not bare]
    for where in dict.fromkeys(tried):
        if not os.path.isdir(where):
            continue
        try:
            repo = gitconf.resolve(where)
        except gitconf.NotARepo:
            continue
        if repo.key == key:
            return repo, None
    return None, (f"{gone_text(root)}, and git lists no other work tree of its git dir "
                  f"{common}")


def indexed_repos(index: dict | None = None) -> list[tuple[str, dict, Repo | None,
                                                           str | None]]:
    """Each index entry, sorted by key: (key, entry, its repo or None, why it is None)."""
    index = indexed() if index is None else index
    return [(key, entry, *open_indexed(key, entry))
            for key, entry in sorted(index.items())]


# -- helpers ------------------------------------------------------------------------------

def _resolve(path: str | os.PathLike) -> Repo:
    try:
        return gitconf.resolve(path)
    except gitconf.NotARepo as exc:
        raise Usage(f"no git work tree at {exc}") from None


def _version(*, need: bool = True) -> tuple[int, int, int] | None:
    version = gitconf.git_version()
    if not need:
        return version
    if version is None:
        raise Refused("git is not on PATH, or `git version` gave no version; install git "
                      f"{gitconf.version_text(gitconf.FLOOR)} or later")
    if version < gitconf.FLOOR:
        raise Refused(f"git {gitconf.version_text(version)} is older than "
                      f"{gitconf.version_text(gitconf.FLOOR)}, the oldest repo's git hooks "
                      "support; install a newer git")
    return version


def _named(ids: list[str], good: dict, bad: dict) -> list[pieces.Piece]:
    out = []
    for pid in ids:
        if pid in bad:
            raise Refused(f"{pid}'s declaration is refused: {bad[pid]}")
        if pid not in good:
            raise Usage(f"no installed module declares the piece {pid}; "
                        "see: moxi repo git-hooks available")
        if good[pid] not in out:
            out.append(good[pid])
    return out


def _state(repo: Repo) -> State:
    try:
        return gitconf.read(repo)
    except gitconf.GitError as exc:
        raise Failure(str(exc)) from None


def _sweep(repo: Repo) -> None:
    _records.sweep(repo.common_dir)


# -- verbs --------------------------------------------------------------------------------

def available() -> dict:
    """Declared pieces of installed modules: {"pieces": [...], "refused": {id: why}}."""
    good, bad = pieces.declared()
    return {"pieces": [good[pid].as_dict() for pid in sorted(good)],
            "refused": dict(sorted(bad.items()))}


def _ids(given: tuple, piece: str | None = None) -> list[str]:
    """Piece ids given one per argument, as lists, or both: `add(path, "a/b", "c/d")` and
    `add(path, ["a/b", "c/d"])` name the same pieces."""
    out: list[str] = []
    for item in (*given, *(() if piece is None else (piece,))):
        for pid in ((item,) if isinstance(item, str) else item):
            if not isinstance(pid, str):
                raise Usage(f"a piece id is a string like owner/name, not {pid!r}")
            if pid not in out:
                out.append(pid)
    return out


def add(path: str | os.PathLike, *piece_ids: str | list[str] | tuple[str, ...],
        mode: str | None = None, sets: list[str] | tuple = ()) -> Report:
    """Register pieces in the repo at `path`; with none, index the repo and write nothing
    else. Each piece id is its own argument, or they come as a list. `mode` pins the mode
    (auto unpins); `sets` are `KEY=VALUE` settings for the named pieces."""
    piece_ids = tuple(_ids(piece_ids))
    if mode is not None and mode not in gitconf.MODE_SETS:
        raise Usage(f"--mode {mode}: it is one of {', '.join(gitconf.MODE_SETS)}")
    if sets and not piece_ids:
        raise Usage("--set sets a setting of a piece named in the same call; name the piece")
    version = _version()
    repo = _resolve(path)
    report = Report()
    state = _state(repo)
    good, bad = pieces.declared() if piece_ids or everywhere() else ({}, {})
    named = _named(list(piece_ids), good, bad)
    implied = _implied(state, named, good)
    if not piece_ids and mode is None and not implied:
        _index_put(repo)
        report.lines.append(f"indexed {repo.root}")
        _sweep(repo)
        return report
    try:
        planned = pieces.plan_sets(named, list(sets))
    except pieces.SetError as exc:
        raise Usage(str(exc)) from None
    named += implied
    changes_of = {piece.id: _changes(piece, state, planned) for piece in named}
    mode_set = mode if mode is not None else state.mode_set
    effective = gitconf.pick_mode(mode_set, default_mode(), version)
    if effective == "shim" and repo.hooks_inside:
        events = set(state.events()) | {p.event for p in named}
        problems = []
        for event in sorted(events):
            try:
                shim.check_place(repo.hooks_dir, event, event in state.moved)
            except shim.ShimRefused as exc:
                problems.append(f"{event}: {exc}")
        if problems:
            raise Refused(f"repo can't place its shims in {repo.hooks_dir}; nothing was "
                          "written", lines=problems)
    _index_put(repo)
    try:
        gitconf.set_mode(repo, state, mode=effective,
                         mode_set=mode_set if (mode is not None or state.mode is None)
                         else None)
        for piece in named:
            new = gitconf.register(repo, piece, state)
            if changes_of[piece.id]:
                gitconf.apply_settings(repo, piece.id, changes_of[piece.id], state)
            report.lines.append(f"{'added' if new else 'rewrote'} {piece.id} "
                                f"({piece.event}, {piece.fail}) in {repo.root}"
                                + (" (commit_rules.everywhere)" if piece in implied
                                   else ""))
        report.take(repo.root, gitconf.sync(repo, state))
    except gitconf.GitError as exc:
        raise Failure(str(exc)) from None
    _sweep(repo)
    return report


def _implied(state: State, named: list[pieces.Piece], good: dict) -> list[pieces.Piece]:
    """The commit-rules piece, when `commit_rules.everywhere` is on and the repo lacks it.
    A disabled registration is a registration, so it is left alone."""
    if (COMMIT_RULES in state.pieces or COMMIT_RULES not in good
            or any(p.id == COMMIT_RULES for p in named) or not everywhere()):
        return []
    return [good[COMMIT_RULES]]


def _changes(piece: pieces.Piece, state: State,
             planned: dict) -> dict[str, list[str] | None]:
    """The settings a call writes for `piece`: its `--set`s, then, on its first
    registration, the seed for every key no `--set` named. Raises `Usage` when a check
    refuses a value, before anything is written."""
    changes = dict(planned.get(piece.id, {}))
    if piece.id not in state.pieces and piece.id in SEEDS:
        for name, values in SEEDS[piece.id]().items():
            if name not in changes and name in piece.settings and values:
                changes[name] = list(values)
    check = SETTING_CHECKS.get(piece.id)
    if check is not None:
        for name, values in changes.items():
            why = check(name, values) if values else None
            if why is not None:
                raise Usage(f"{piece.id}: {why}; nothing was written")
    return changes


def remove(path: str | os.PathLike, *piece_ids: str | list[str] | tuple[str, ...],
           piece: str | None = None) -> Report:
    """Remove pieces from the repo at `path`, given as `add` takes them or as `piece=`;
    with none, everything repo wrote there. On a path that no longer exists, drop its
    index entry and its records."""
    piece_ids = tuple(_ids(piece_ids, piece))
    report = Report()
    where = Path(path)
    if not where.exists():
        gone = indexed_at(where)
        if not gone:
            raise Usage(f"{path} doesn't exist and no indexed repo was there")
        if piece_ids:
            raise Usage(f"{path} doesn't exist; remove the whole repo with: "
                        f"moxi repo git-hooks remove {_arg(path)}")
        _drop_stale(path, gone, report, why="it no longer exists")
        return report
    repo = _resolve(path)
    stale = [key for key in indexed_at(where) if key != repo.key]
    if stale:
        # An indexed repo was here and is gone; the path is now inside another repo,
        # which this call leaves alone.
        if piece_ids:
            raise Usage(f"{path} is indexed as a repo that is no longer there, and is now "
                        f"inside {repo.root}; drop it first with: "
                        f"moxi repo git-hooks remove {_arg(path)}")
        _drop_stale(path, stale, report,
                    why=f"that repo is gone and {repo.root}, which was left as it is, "
                    "holds the path now")
        return report
    state = _state(repo)
    try:
        if piece_ids:
            for pid in piece_ids:
                if pid not in state.pieces:
                    raise Usage(f"{pid} is not registered in {repo.root}")
            for pid in piece_ids:
                gitconf.remove_piece(repo, pid, state)
                _records.close_piece(repo.common_dir, pid)
                report.lines.append(f"removed {pid} from {repo.root}")
            report.take(repo.root, gitconf.sync(repo, state))
            _sweep(repo)
            return report
        report.lines += gitconf.remove_all(repo, state)
    except gitconf.GitError as exc:
        raise Failure(str(exc)) from None
    _records.close_repo(repo.common_dir)
    _index_drop(repo.key)
    report.lines.append(f"removed repo's git hooks from {repo.root}")
    return report


def _drop_stale(path, keys: list[str], report: Report, *, why: str) -> None:
    """Drop index entries whose repo is gone, with their records; an entry whose repo is
    still there elsewhere gets its root corrected and a problem naming it."""
    index = indexed()
    for key in keys:
        repo, _ = open_indexed(key, index.get(key, {}))
        if repo is not None:
            _index_put(repo)
            report.problems.append(
                f"{path} is not where its repo is: the repo indexed there is at "
                f"{repo.root}; to remove repo's git hooks from it run: "
                f"moxi repo git-hooks remove {_arg(repo.root)}")
            continue
        _records.close_repo(index.get(key, {}).get("common_dir") or key)
        _index_drop(key)
        report.lines.append(f"dropped {path} from the index; {why}")


def enable(path: str | os.PathLike, piece_id: str) -> Report:
    return _flip(path, piece_id, True)


def disable(path: str | os.PathLike, piece_id: str) -> Report:
    return _flip(path, piece_id, False)


def _flip(path: str | os.PathLike, piece_id: str, value: bool) -> Report:
    repo = _resolve(path)
    state = _state(repo)
    if piece_id not in state.pieces:
        raise Usage(f"{piece_id} is not registered in {repo.root}")
    report = Report()
    try:
        gitconf.set_enabled(repo, piece_id, value, state)
        if not value:
            # The record, its ledger key, then the flag.
            _records.close_piece(repo.common_dir, piece_id)
            gitconf.clear_recorded(repo, piece_id, state)
        report.take(repo.root, gitconf.sync(repo, state))
    except gitconf.GitError as exc:
        raise Failure(str(exc)) from None
    report.lines.append(f"{'enabled' if value else 'disabled'} {piece_id} in {repo.root}")
    _sweep(repo)
    return report


def refresh(path: str | os.PathLike | None = None) -> Report:
    """Re-read declarations, git's version and the interpreter, and rewrite every entry,
    shim and launcher of one repo or every indexed repo by the rewrite rule; then sweep."""
    version = _version()
    report = Report()
    if path is not None:
        repos = [_resolve(path)]
    else:
        repos = []
        for key, entry, repo, why in indexed_repos():
            if repo is None:
                report.problems.append(f"{why}; run: moxi repo git-hooks remove "
                                       f"{_arg(entry.get('root') or key)}")
            else:
                repos.append(repo)
    good, bad = pieces.declared()
    default = default_mode()
    for repo in repos:
        state = _state(repo)
        try:
            for piece in _implied(state, [], good):
                changes = _changes(piece, state, {})
                gitconf.register(repo, piece, state)
                if changes:
                    gitconf.apply_settings(repo, piece.id, changes, state)
                report.lines.append(f"added {piece.id} ({piece.event}, {piece.fail}) in "
                                    f"{repo.root} (commit_rules.everywhere)")
            for pid in sorted(state.pieces):
                if pid in good:
                    gitconf.register(repo, good[pid], state)
                elif pid in bad:
                    report.problems.append(f"{repo.root}: {pid}'s declaration is refused "
                                           f"({bad[pid]}); its registration is left as it is")
                else:
                    report.problems.append(
                        f"{repo.root}: no installed module declares {pid}; remove it with: "
                        f"moxi repo git-hooks remove {_arg(repo.root)} {pid}")
            effective = gitconf.pick_mode(state.mode_set, default, version)
            if effective != state.mode:
                if state.mode:
                    report.lines.append(f"{repo.root}: {state.mode} mode to {effective} "
                                        f"(git {gitconf.version_text(version)})")
                gitconf.set_mode(repo, state, mode=effective)
            report.take(repo.root, gitconf.sync(repo, state))
        except gitconf.GitError as exc:
            report.problems.append(f"{repo.root}: {exc}")
            continue
        except Usage as exc:
            report.problems.append(f"{repo.root}: {exc.text}")
            continue
        _index_put(repo)
        _sweep(repo)
        report.lines.append(f"refreshed {repo.root}")
    if path is None:
        _records.sweep()
    return report


# -- other modules' installs and uninstalls, and repo's own -------------------------------

def module_of(name: str) -> str:
    """The module a piece id or a spec name belongs to: the part before its `/`."""
    return name.split("/", 1)[0]


def _declares_otherwise(section: dict[str, list], piece: pieces.Piece) -> bool:
    """True when the registration's declared keys differ from the declaration's."""
    return any(section.get(var) != [value] for var, value in piece.registration().items())


def _unreadable_repo(key: str, entry: dict) -> str | None:
    """Why an indexed repo that `open_indexed` couldn't open is still there: its root
    exists, so it isn't gone, but git can't read it (a broken config, say). None when the
    repo is gone: its root no longer exists, or is now another repo's or no repo's."""
    root = entry.get("root") or key
    if not os.path.isdir(root) or not os.path.isdir(entry.get("common_dir") or key):
        return None
    try:
        gitconf.resolve(root)
    except gitconf.NotARepo as exc:
        return str(exc)
    return None


def install_module(module: str) -> Report:
    """What `on_install` does after module `module`'s install or upgrade: in every indexed
    repo, rewrite each registered piece of that module that its declaration now gives
    otherwise, by the rewrite rule, and remove each one it no longer declares as `remove`
    does, with a line naming the piece and the repo. A piece whose declaration is refused
    is left as it is, with a problem. Raises `IndexUnreadable` when the index can't be
    read; one repo's problem is a problem of the report."""
    report = Report()
    index = indexed()
    if not index:
        return report
    good, bad = pieces.declared()
    for key, entry, repo, why in indexed_repos(index):
        if repo is None:
            # A repo that is gone is the doctor's row and `refresh`'s problem.
            problem = _unreadable_repo(key, entry)
            if problem:
                report.problems.append(f"{problem}, so it is left as it is")
            continue
        try:
            state = gitconf.read(repo)
            mine = sorted(pid for pid in state.pieces if module_of(pid) == module)
            changed = False
            for pid in mine:
                if pid in good:
                    if _declares_otherwise(state.pieces[pid], good[pid]):
                        gitconf.register(repo, good[pid], state)
                        report.lines.append(f"rewrote {pid} ({good[pid].event}, "
                                            f"{good[pid].fail}) in {repo.root}")
                        changed = True
                elif pid in bad:
                    report.problems.append(f"{repo.root}: {pid}'s declaration is refused "
                                           f"({bad[pid]}); its registration is left as it is")
                else:
                    gitconf.remove_piece(repo, pid, state)
                    _records.close_piece(repo.common_dir, pid)
                    report.lines.append(f"removed {pid} from {repo.root}: {module} no "
                                        "longer declares it")
                    changed = True
            if changed:
                report.take(repo.root, gitconf.sync(repo, state))
        except (gitconf.GitError, OSError) as exc:
            report.problems.append(f"{repo.root}: {exc}")
            continue
        if mine:
            _sweep(repo)
    return report


def uninstall_module(module: str) -> Report:
    """What `on_uninstall` does when module `module` is uninstalled: in every indexed repo,
    remove each of its registered pieces as `remove` does, and forget each of its specs,
    saved or only in the repo's state file, as `remove_spec` does. Raises
    `IndexUnreadable` when the index can't be read; one repo's problem is a problem of the
    report."""
    from moxi import files

    from moxi_repo import committer

    report = Report()
    index = indexed()
    for key, entry, repo, why in indexed_repos(index):
        if repo is None:
            # The sweep clears a gone repo's records once its git dir is gone.
            problem = _unreadable_repo(key, entry)
            if problem:
                report.problems.append(f"{problem}, so {module}'s pieces and specs are "
                                       "left there")
            continue
        try:
            state = gitconf.read(repo)
            mine = sorted(pid for pid in state.pieces if module_of(pid) == module)
            for pid in mine:
                gitconf.remove_piece(repo, pid, state)
                _records.close_piece(repo.common_dir, pid)
                report.lines.append(f"removed {pid} from {repo.root}: {module} is "
                                    "uninstalled")
            if mine:
                report.take(repo.root, gitconf.sync(repo, state))
            names = (set(committer.saved_names(repo.common_dir))
                     | set(committer.read_state(repo.common_dir)["specs"]))
            for name in sorted(n for n in names if module_of(n) == module):
                report.lines += committer.remove_spec(repo.root, name)
        except files.LockTimeout as exc:
            report.problems.append(f"{repo.root}: a committer pass holds {exc.path}, so "
                                   f"{module}'s specs are left; forget each with: "
                                   f"moxi repo specs remove {_arg(repo.root)} NAME")
            continue
        except (gitconf.GitError, committer.SpecError, OSError) as exc:
            report.problems.append(f"{repo.root}: {exc}")
            continue
        _sweep(repo)
    return report


def chain_holders(repo: Repo) -> list[tuple[str, Path]]:
    """Each (event, git hook file) in a hooks dir outside the repo's git dir that holds
    repo's chain line."""
    if repo.hooks_inside:
        return []
    return [(event, hook) for event in sorted(EVENTS)
            for hook in gitconf.chain_hooks(repo, event)
            if gitconf.holds_chain_line(hook, event)]


def _shims_to_remove(repo: Repo) -> bool:
    """True when `remove_all` has shims of repo's to take out of the git dir: in the hooks
    dir when it is inside it, or in `<common git dir>/hooks` when git reads another."""
    return bool((repo.hooks_inside and shim.our_shims(repo.hooks_dir))
                or (repo.stray_dir is not None and shim.our_shims(repo.stray_dir)))


def _wrote_in(repo: Repo, state: gitconf.State) -> bool:
    """True when `remove_all` has something of repo's to remove there: a config key, a
    shim, a git hook it moved aside, or `<common git dir>/moxi-repo/`."""
    return bool(state.sections or state.entries or state.moved or repo.own_dir.exists()
                or _shims_to_remove(repo) or shim.our_shims(repo.hooks_dir))


def _removing(repo: Repo, state: gitconf.State) -> str:
    """What `remove_all` is about to remove from the repo's git dir, in words, for its
    line."""
    parts = []
    if state.sections or state.entries:
        parts.append("config keys")
    if state.entries or _shims_to_remove(repo):
        parts.append("git hooks")
    if repo.launcher.exists():
        parts.append("launcher")
    elif not parts and repo.own_dir.exists():
        parts.append(f"{repo.own_dir.name}/ dir")
    if len(parts) > 1:
        return ", ".join(parts[:-1]) + " and " + parts[-1]
    return parts[0] if parts else "nothing"


def _left_outside(repo: Repo, moved: set[str]) -> list[str]:
    """A line for each git hook repo moved aside (an event in `moved`) that is in a hooks
    dir outside the git dir, which `remove_all` doesn't move back: it takes only its own
    shims out of such a dir."""
    if repo.hooks_inside:
        return []
    out = []
    for event in sorted(moved):
        local, hook = repo.hooks_dir / (event + shim.LOCAL), repo.hooks_dir / event
        if local.exists() or local.is_symlink():
            out.append(f"{local} may be the git hook repo moved aside for {event}, left as "
                       f"it is outside {repo.common_dir}; repo no longer records it, so "
                       f"move it back to {hook} or delete it")
    return out


class Teardown(Report):
    """`teardown`'s report, which also names each repo it cleaned."""

    __slots__ = ("cleaned",)

    def __init__(self) -> None:
        super().__init__()
        #: The root of each repo whose git hooks it removed and that it dropped from the
        #: index.
        self.cleaned: list[str] = []


def teardown() -> Teardown:
    """What repo's own uninstall does: for every indexed repo, what `remove PATH` does (its
    sections, entries, shims with each `.local` moved back, the launcher and
    `<common git dir>/moxi-repo/`), naming each git hook file that still holds a chain line
    for the user to delete; an indexed path that is no longer a repo is dropped from the
    index with one line. When that repo's common git dir is still there with no work tree,
    what repo wrote in it goes too, through `git --git-dir`, and the line says what went;
    where git reads that git dir's hooks from a dir outside it, only repo's shims there
    are removed, and the report says so and names each chain line and moved git hook left
    there. Then every doctor record repo has open is cleared.

    It first opens and reads every indexed repo, and when one can't be read (a config git
    can't read, say) it removes nothing and the report names that repo. A step that still
    fails once removing began stops it there: that repo and the rest stay indexed with
    their records, and the report's `cleaned` names the repos already cleaned. Raises
    `IndexUnreadable` when the index can't be read."""
    report = Teardown()
    plan: list[tuple[str, dict, Repo | None, str | None, gitconf.State | None,
                     list[tuple[str, Path]], Repo | None]] = []
    for key, entry, repo, why in indexed_repos():
        left = None
        if repo is None:
            problem = _unreadable_repo(key, entry)
            if problem:
                report.problems.append(f"{problem}, so repo can't clean it")
                continue
            # A git dir still there with no work tree keeps repo's keys, which a work
            # tree added later would run.
            left = gitconf.git_dir_only(entry.get("common_dir") or key)
        target = repo or left
        try:
            state = gitconf.read(target) if target is not None else None
            found = chain_holders(target) if target is not None else []
        except (gitconf.GitError, OSError) as exc:
            report.problems.append(f"{repo.root if repo is not None else left.common_dir}: "
                                   f"{exc}, so repo can't clean it")
            continue
        if left is not None and not _wrote_in(left, state):
            target = None
        plan.append((key, entry, target, why, state, found, left))
    if not report.ok:
        return report
    held: list[tuple[str, Path]] = []
    # Read once every removal is done, so a moved git hook in a hooks dir another indexed
    # repo shares, which that repo's cleaning has just moved back, isn't named.
    outside: list[tuple[Repo, set[str]]] = []
    for key, entry, repo, why, state, found, left in plan:
        gone = why is not None
        root = (entry.get("root") or key) if gone else str(repo.root)
        try:
            if gone:
                line = f"dropped {root} from the index: {why}"
                moved = set(state.moved) if state is not None else set()
                if repo is not None:
                    what = _removing(repo, state)
                    shims = not repo.hooks_inside and shim.our_shims(repo.hooks_dir)
                    report.lines += gitconf.remove_all(repo, state, final=True)
                    line += f"; removed repo's {what} from {repo.common_dir} too"
                    if not repo.hooks_inside:
                        line += (f"; its hooks dir {repo.hooks_dir} is outside that git dir, "
                                 "so repo removed "
                                 + ("only its shims there" if shims else "nothing there"))
                _records.close_repo(entry.get("common_dir") or key)
                _index_drop(key)
                report.lines.append(line)
                held += found
                if left is not None:
                    outside.append((left, moved))
                continue
            report.lines += gitconf.remove_all(repo, state, final=True)
            _records.close_repo(repo.common_dir)
            _index_drop(key)
        except (gitconf.GitError, OSError, IndexUnreadable) as exc:
            report.problems.append(f"{root}: {exc}; repo stopped there, so it and the "
                                   "repos after it are left as they are")
            break
        report.lines.append(f"removed repo's git hooks from {root}")
        report.cleaned.append(root)
        held += found
    for event, hook in held:
        report.lines.append(f"{hook} still holds repo's chain line for {event}, which does "
                            "nothing now; delete that line")
    for left, moved in outside:
        report.lines += _left_outside(left, moved)
    if report.ok:
        _records.close_all()
    return report


# -- committer specs ----------------------------------------------------------------------

def specs(path: str | os.PathLike | None = None, *, sweep: bool = True) -> list[dict]:
    """Each indexed repo, or the one at `path`, with its saved specs and the specs that ran
    there without being saved, each with its last result (`moxi repo specs --json`). Runs
    the record sweep for each repo first, unless `sweep` is false: then nothing is
    written. A saved spec's `last_commit` is its author's newest commit, `{"sha",
    "time"}`, or None; a spec not saved names no author, so its `last_commit` is None."""
    from moxi_repo import committer

    if path is not None:
        repos = [_resolve(path)]
    else:
        repos = [repo for _, _, repo, _ in indexed_repos() if repo is not None]
    out = []
    for repo in repos:
        if sweep:
            _sweep(repo)
        try:
            listed = committer.listing(repo.root)
        except committer.SpecError as exc:
            raise Failure(str(exc)) from None
        if path is None and not listed:
            continue
        for item in listed:
            author = (item.get("spec") or {}).get("author")
            found = committer.last_commit(repo.root, tuple(author)) if author else None
            item["last_commit"] = (None if found is None
                                   else {"sha": found[0], "time": found[1]})
        out.append({"root": str(repo.root), "common_dir": str(repo.common_dir),
                    "specs": listed})
    return out


def forget_spec(path: str | os.PathLike, name: str) -> Report:
    """Forget a spec in the repo at `path`, saved or not: its saved section, its state-file
    entry and its doctor records (`moxi repo specs remove`)."""
    from moxi import files

    from moxi_repo import committer

    repo = _resolve(path)
    try:
        lines = committer.remove_spec(repo.root, name)
    except files.LockTimeout as exc:
        raise Busy(f"a committer pass holds {exc.path}; try again") from None
    except committer.SpecError as exc:
        raise Failure(str(exc)) from None
    if not lines:
        raise Usage(f"no spec named {name} in {repo.root}; see: "
                    f"moxi repo specs {_arg(repo.root)}")
    report = Report()
    report.lines += lines
    _sweep(repo)
    return report


# -- reading ------------------------------------------------------------------------------

def rules(path: str | os.PathLike) -> list[str]:
    """What a committer in the repo at `path` must know, as the lines `moxi repo rules`
    prints: a heading naming the repo, then the lines of each registered piece in
    `RULE_LINES`. With none of those pieces registered, one line saying the repo has no
    commit rules. A disabled piece is still registered."""
    repo = _resolve(path)
    state = _state(repo)
    found = [pid for pid in RULE_LINES if pid in state.pieces]
    if not found:
        return [f"no commit rules for {repo.root}"]
    lines = [f"commit rules for {repo.root}"]
    for pid in found:
        lines += RULE_LINES[pid](repo, settings_of(state.pieces[pid]))
    return lines


def status(path: str | os.PathLike | None = None) -> list[dict]:
    """Each indexed repo, or the one at `path`: its git hooks as `git-hooks list --json`
    prints them. Each repo's `pieces` lists every registered piece with its id, event,
    enabled, fail policy, order, settings and whether a doctor record of it is open, and
    its `records` each doctor record repo has open for it (`_records.open_of`)."""
    version = _version(need=False)
    index = indexed()
    if path is not None:
        targets = [(None, _resolve(path), None)]
    else:
        targets = [(key, repo, why) for key, _, repo, why in indexed_repos(index)]
    good, _ = pieces.declared()
    out = []
    for key, repo, why in targets:
        if repo is None:
            root = index[key].get("root") or key
            out.append({"root": root, "common_dir": index[key].get("common_dir"),
                        "indexed": True, "pieces": [], "events": {},
                        "records": _records.open_of(index[key].get("common_dir") or key),
                        "problems": [Problem(why,
                                             f"moxi repo git-hooks remove {_arg(root)}",
                                             state="warn")]})
            continue
        out.append(_status_of(repo, repo.key in index, version, good))
    return out


def _status_of(repo: Repo, in_index: bool, version, good: dict) -> dict:
    state = _state(repo)
    problems: list[Problem] = []
    listed = []
    for pid, section in state.pieces.items():
        listed.append({"id": pid, "event": last(section, "event"),
                       "enabled": enabled(section), "fail": fail_policy(section),
                       "order": order_of(section), "settings": settings_of(section),
                       "recorded": "recorded" in section, "declared": pid in good})
        if pid not in good:
            # Enabled and closed, it refuses every event it runs on.
            closing = enabled(section) and fail_policy(section) == "closed"
            problems.append(Problem(f"{pid} is registered but no installed module "
                                    "declares it",
                                    f"moxi repo git-hooks remove {_arg(repo.root)} {pid}",
                                    state="fail" if closing else "warn",
                                    how="remove it with"))
        if last(section, "event") not in EVENTS:
            problems.append(Problem(f"{pid} names {last(section, 'event')!r}, not a git "
                                    "event", state="warn"))
    listed.sort(key=lambda p: (p["event"] or "", p["order"], p["id"]))
    mode = state.mode
    if mode == "config" and version is not None and version < gitconf.CONFIG_FROM:
        # refresh keeps config mode when the repo or the module's default pins it, so
        # then the fix is the pin to shim mode.
        try:
            default = default_mode()
        except Failure:
            default = "auto"  # refresh itself then names the config that can't be read
        if gitconf.pick_mode(state.mode_set, default, version) == "config":
            fix = f"moxi repo git-hooks add {_arg(repo.root)} --mode shim"
        else:
            fix = f"moxi repo git-hooks refresh {_arg(repo.root)}"
        problems.append(Problem(f"git {gitconf.version_text(version)} runs no config "
                                "entries, so no piece runs here", fix))
    events = {}
    ours = shim.our_shims(repo.hooks_dir)
    python, runner = gitconf.interpreter(), gitconf.runner_file()
    running = state.events()
    for event in sorted({p["event"] for p in listed if p["event"] in EVENTS}):
        info: dict = {"pieces": [p["id"] for p in listed if p["event"] == event],
                      "via": None, "existing": None, "chain_line": None}
        hook = repo.hooks_dir / event
        if event not in running:
            # Every piece of it is disabled, so nothing of repo's is there to check.
            info["existing"] = str(hook) if _runnable(hook) else None
        elif mode == "config":
            info["via"] = "config entry"
            command = state.entries.get(event)
            if command is None:
                problems.append(Problem(f"{event} has pieces but no config entry",
                                        f"moxi repo git-hooks refresh {_arg(repo.root)}"))
            else:
                problems += _guard_problems(event, command, python, runner, repo.root)
            if _runnable(hook) and shim.shim_event(hook) is None:
                info["existing"] = f"{hook}, run by git after the pieces"
        elif repo.hooks_inside:
            info["via"] = "shim"
            mine = ours.get(event, [])
            local = repo.hooks_dir / (event + shim.LOCAL)
            if hook in mine:
                text = hook.read_text(encoding="utf-8", errors="replace")
                problems += _guard_problems(event, text, python, runner, repo.root)
            elif mine:
                # A manager that moves a hook aside as `<event>.legacy` runs it from its
                # own hook; one that renames it anything else (`.old`) doesn't.
                runs = mine[0].name == event + shim.LEGACY
                problems.append(Problem(
                    f"{event}: another tool's git hook took {hook}, and moved repo's shim "
                    f"to {mine[0].name}; the pieces run only if that tool runs it",
                    state="ok" if runs else "fail"))
                if runs:
                    # It still runs, so its guard's two paths matter as the shim's do.
                    problems += _guard_problems(event, mine[0].read_text(
                        encoding="utf-8", errors="replace"), python, runner, repo.root)
            else:
                problems.append(Problem(f"{event}: repo's shim is missing or another tool "
                                        "replaced it",
                                        f"moxi repo git-hooks refresh {_arg(repo.root)}"))
            if _runnable(local):
                info["existing"] = f"{local}, run by the shim after the pieces"
        else:
            info["via"] = "launcher"
            chain_hook, line, present = gitconf.chain_state(repo, event)
            info["chain_line"] = "present" if present else "needed"
            info["hook"] = str(chain_hook)
            info["existing"] = str(hook) if hook.exists() else None
            if not present:
                info["line"] = line
            if not repo.launcher.exists():
                # With the chain line in place the pieces then don't run at all.
                problems.append(Problem(f"{event}: the launcher {repo.launcher} is missing, "
                                        "so the chain line skips quietly",
                                        f"moxi repo git-hooks refresh {_arg(repo.root)}",
                                        state="fail" if present else "warn"))
            else:
                problems += _guard_problems(event, repo.launcher.read_text(
                    encoding="utf-8", errors="replace"), python, runner, repo.root)
        events[event] = info
    for event, path, fixable in gitconf.leftovers(repo, state):
        if shim.shim_event(path) is not None:
            problems.append(Problem(f"{event}: repo's shim {path} is left in {path.parent}, "
                                    "which git no longer reads as the hooks dir",
                                    f"moxi repo git-hooks refresh {_arg(repo.root)}",
                                    state="warn"))
        elif fixable:
            problems.append(Problem(f"{event}: the git hook repo moved aside is still "
                                    f"{path}, which nothing runs",
                                    f"moxi repo git-hooks refresh {_arg(repo.root)}",
                                    state="warn"))
        else:
            problems.append(Problem(f"{event}: the git hook repo moved aside is still "
                                    f"{path}, which nothing runs, and another git hook took "
                                    f"{path.with_name(event)}; merge them or remove one",
                                    state="warn"))
    return {"root": str(repo.root), "common_dir": str(repo.common_dir),
            "hooks_dir": str(repo.hooks_dir), "indexed": in_index,
            "git_version": gitconf.version_text(version), "mode": mode,
            "mode_set": state.mode_set, "pieces": listed, "events": events,
            "records": _records.open_of(repo.common_dir), "problems": problems}


def _runnable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _guard_problems(event: str, text: str, python: str, runner: str,
                    root) -> list[Problem]:
    """What is wrong with the guard's two paths in an entry, shim or launcher's text."""
    import re

    out = []
    py = re.search(r"\[ -x '((?:[^']|'\\'')*)' \]|PY='((?:[^']|'\\'')*)'", text)
    run = re.search(r"\[ -f '((?:[^']|'\\'')*)' \]|RUN='((?:[^']|'\\'')*)'", text)
    written_py = (py.group(1) or py.group(2)).replace("'\\''", "'") if py else None
    written_run = (run.group(1) or run.group(2)).replace("'\\''", "'") if run else None
    fix = f"moxi repo git-hooks refresh {_arg(root)}"
    if written_py and not Path(written_py).exists():
        out.append(Problem(f"{event}: the interpreter {written_py} is missing", fix))
    elif written_run and not Path(written_run).exists():
        out.append(Problem(f"{event}: the runner {written_run} is missing", fix))
    elif written_py != python or written_run != runner:
        # Both still exist, so the pieces run, from another environment.
        out.append(Problem(f"{event}: written for another interpreter or runner", fix,
                           state="warn"))
    return out


#: Each name this module hands out for another module -> the module it comes from. Four
#: are not package exports: `SpecError`, which `save_spec` raises; `saved_spec`, the spec a
#: name last saved; `last_commit_time`, when an author last committed; and `PATHSPEC_VARS`,
#: the environment variables that change how git reads a pathspec.
REEXPORTS = {**_PACKAGE_EXPORTS, "SpecError": "moxi_repo.committer",
             "saved_spec": "moxi_repo.committer", "last_commit_time": "moxi_repo.committer",
             "PATHSPEC_VARS": "moxi_repo.gitrun"}


def __getattr__(name):
    module = REEXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module 'moxi_repo.api' has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module), name)


def __dir__():
    return sorted({*globals(), *REEXPORTS})


__all__ = ["Failure", "Usage", "Refused", "Busy", "IndexUnreadable", "Report", "Problem", "SEEDS", "SETTING_CHECKS",
           "RULE_LINES", "available", "add", "remove", "enable", "disable", "refresh",
           "rules", "status",
           "indexed", "indexed_repos", "open_indexed", "default_mode", "everywhere", "specs",
           "forget_spec", "module_of", "install_module", "uninstall_module",
           "chain_holders", "teardown", "Teardown", *sorted(REEXPORTS)]
