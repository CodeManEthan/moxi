"""The verbs behind `moxi repo git-hooks` and `moxi repo specs`, for the CLI and for other
modules.

`add`, `remove`, `enable`, `disable` and `refresh` change a repo's git hooks and return a
`Report` of what they did; `status` and `available` read. A verb that can't act raises a
`Failure` whose `code` is the exit code (`Usage` 2, `Refused` 3, else 1) and whose text
says what to do.

A repo is named by a path inside it; repo keeps an index of the repos it has written to
(the `repos` store), which `refresh` and listings walk. The git config is the truth: a repo
missing from the index still runs its pieces, and `add PATH` with no piece indexes it
again.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

from moxi_repo import _records, gitconf, pieces, shim
from moxi_repo.gitconf import Repo, State
from moxi_repo.run import (EVENTS, enabled, fail_policy, last, order_of, settings_of)

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


def indexed() -> dict[str, dict]:
    """The index: repo key -> {"root", "common_dir"}."""
    try:
        data = json.loads(index_path().read_bytes())
    except (OSError, ValueError):
        return {}
    repos = data.get("repos") if isinstance(data, dict) else None
    return repos if isinstance(repos, dict) else {}


def _index_change(fn: Callable[[dict], None]) -> None:
    from moxi import files

    def edit(old: bytes | None) -> bytes:
        try:
            data = json.loads(old) if old else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict) or not isinstance(data.get("repos"), dict):
            data = {"repos": {}}
        fn(data["repos"])
        return (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)
                + "\n").encode()

    files.update(index_path(), edit)


def indexed_at(path: str | os.PathLike) -> list[str]:
    """The keys of indexed repos whose root, or key, is `path`."""
    want = os.path.normcase(os.path.abspath(path))
    return [key for key, entry in indexed().items()
            if os.path.normcase(os.path.abspath(entry.get("root", ""))) == want
            or key == want]


def _index_put(repo: Repo) -> None:
    def put(repos: dict) -> None:
        entry = repos.setdefault(repo.key, {})
        entry.setdefault("root", str(repo.root))
        entry["common_dir"] = str(repo.common_dir)

    _index_change(put)


def _index_drop(key: str) -> None:
    def drop(repos: dict) -> None:
        repos.pop(key, None)

    _index_change(drop)


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


def add(path: str | os.PathLike, piece_ids: list[str] | tuple = (), *,
        mode: str | None = None, sets: list[str] | tuple = ()) -> Report:
    """Register pieces in the repo at `path`; with none, index the repo and write nothing
    else. `mode` pins the mode (auto unpins); `sets` are `KEY=VALUE` settings for the
    named pieces."""
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


def remove(path: str | os.PathLike, piece_ids: list[str] | tuple = ()) -> Report:
    """Remove pieces from the repo at `path`; with none, everything repo wrote there. On a
    path that no longer exists, drop its index entry and its records."""
    report = Report()
    where = Path(path)
    if not where.exists():
        gone = indexed_at(where)
        if not gone:
            raise Usage(f"{path} doesn't exist and no indexed repo was there")
        if piece_ids:
            raise Usage(f"{path} doesn't exist; remove the whole repo with: "
                        f"moxi repo git-hooks remove {path}")
        for key in gone:
            _records.close_repo(key)
            _index_drop(key)
            report.lines.append(f"dropped {path} from the index; it no longer exists")
        return report
    repo = _resolve(path)
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
        for key, entry in sorted(indexed().items()):
            root = entry.get("root", "")
            try:
                repos.append(gitconf.resolve(root))
            except gitconf.NotARepo:
                report.problems.append(f"{root} is no longer a git work tree; run: "
                                       f"moxi repo git-hooks remove {root}")
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
                        f"moxi repo git-hooks remove {repo.root} {pid}")
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


# -- committer specs ----------------------------------------------------------------------

def specs(path: str | os.PathLike | None = None) -> list[dict]:
    """Each indexed repo, or the one at `path`, with its saved specs and the specs that ran
    there without being saved, each with its last result (`moxi repo specs --json`). Runs
    the record sweep for each repo first."""
    from moxi_repo import committer

    if path is not None:
        repos = [_resolve(path)]
    else:
        repos = []
        for key, entry in sorted(indexed().items()):
            try:
                repos.append(gitconf.resolve(entry.get("root", "")))
            except gitconf.NotARepo:
                continue
    out = []
    for repo in repos:
        _sweep(repo)
        try:
            listed = committer.listing(repo.root)
        except committer.SpecError as exc:
            raise Failure(str(exc)) from None
        if path is None and not listed:
            continue
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
        raise Usage(f"no spec named {name} in {repo.root}; see: moxi repo specs {repo.root}")
    report = Report()
    report.lines += lines
    _sweep(repo)
    return report


# -- reading ------------------------------------------------------------------------------

def status(path: str | os.PathLike | None = None) -> list[dict]:
    """Each indexed repo, or the one at `path`: its git hooks as `git-hooks list --json`
    prints them. Each repo's `pieces` lists every registered piece with its id, event,
    enabled, fail policy, order, settings and whether a doctor record of it is open."""
    version = _version(need=False)
    index = indexed()
    if path is not None:
        targets = [(None, _resolve(path))]
    else:
        targets = []
        for key, entry in sorted(index.items()):
            try:
                targets.append((key, gitconf.resolve(entry.get("root", ""))))
            except gitconf.NotARepo:
                targets.append((key, None))
    good, _ = pieces.declared()
    out = []
    for key, repo in targets:
        if repo is None:
            root = index[key].get("root", "")
            out.append({"root": root, "common_dir": index[key].get("common_dir"),
                        "indexed": True, "pieces": [], "events": {},
                        "problems": [Problem(f"{root} is no longer a git work tree",
                                             f"moxi repo git-hooks remove {root}",
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
                                    f"moxi repo git-hooks remove {repo.root} {pid}",
                                    state="fail" if closing else "warn",
                                    how="remove it with"))
        if last(section, "event") not in EVENTS:
            problems.append(Problem(f"{pid} names {last(section, 'event')!r}, not a git "
                                    "event", state="warn"))
    listed.sort(key=lambda p: (p["event"] or "", p["order"], p["id"]))
    mode = state.mode
    if mode == "config" and version is not None and version < gitconf.CONFIG_FROM:
        problems.append(Problem(f"git {gitconf.version_text(version)} runs no config "
                                "entries, so no piece runs here",
                                f"moxi repo git-hooks refresh {repo.root}"))
    events = {}
    ours = shim.our_shims(repo.hooks_dir)
    python, runner = gitconf.interpreter(), gitconf.runner_file()
    for event in sorted(state.events()):
        info: dict = {"pieces": [p["id"] for p in listed if p["event"] == event],
                      "via": None, "existing": None, "chain_line": None}
        hook = repo.hooks_dir / event
        if mode == "config":
            info["via"] = "config entry"
            command = state.entries.get(event)
            if command is None:
                problems.append(Problem(f"{event} has pieces but no config entry",
                                        f"moxi repo git-hooks refresh {repo.root}"))
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
            else:
                problems.append(Problem(f"{event}: repo's shim is missing or another tool "
                                        "replaced it",
                                        f"moxi repo git-hooks refresh {repo.root}"))
            if _runnable(local):
                info["existing"] = f"{local}, run by the shim after the pieces"
        else:
            info["via"] = "launcher"
            _, line, present = gitconf.chain_state(repo, event)
            info["chain_line"] = "present" if present else "needed"
            info["existing"] = str(hook) if hook.exists() else None
            if not present:
                info["line"] = line
            if not repo.launcher.exists():
                # With the chain line in place the pieces then don't run at all.
                problems.append(Problem(f"{event}: the launcher {repo.launcher} is missing, "
                                        "so the chain line skips quietly",
                                        f"moxi repo git-hooks refresh {repo.root}",
                                        state="fail" if present else "warn"))
            else:
                problems += _guard_problems(event, repo.launcher.read_text(
                    encoding="utf-8", errors="replace"), python, runner, repo.root)
        events[event] = info
    return {"root": str(repo.root), "common_dir": str(repo.common_dir),
            "hooks_dir": str(repo.hooks_dir), "indexed": in_index,
            "git_version": gitconf.version_text(version), "mode": mode,
            "mode_set": state.mode_set, "pieces": listed, "events": events,
            "problems": problems}


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
    fix = f"moxi repo git-hooks refresh {root}"
    if written_py and not Path(written_py).exists():
        out.append(Problem(f"{event}: the interpreter {written_py} is missing", fix))
    elif written_run and not Path(written_run).exists():
        out.append(Problem(f"{event}: the runner {written_run} is missing", fix))
    elif written_py != python or written_run != runner:
        # Both still exist, so the pieces run, from another environment.
        out.append(Problem(f"{event}: written for another interpreter or runner", fix,
                           state="warn"))
    return out


__all__ = ["Failure", "Usage", "Refused", "Busy", "Report", "Problem", "SEEDS", "SETTING_CHECKS",
           "available", "add", "remove", "enable", "disable", "refresh", "status",
           "indexed", "default_mode", "everywhere", "specs", "forget_spec"]
