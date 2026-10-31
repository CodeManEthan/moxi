"""projects' doctor checks: `rows()`, which the announcement's `DOCTOR` names.

The core's own checks cover the declared store's existence, mode and size. These look
inside it:

- store: the file opens, `PRAGMA quick_check` passes, and its schema version is one this
  code reads. `fail` otherwise, naming the fix. A missing store is `ok`: the first write
  makes it.
- store version: `warn` for a version older than this code's that it still reads; the
  next write, or `moxi install projects`, migrates it.
- roots: `warn` for a root that isn't absolute, doesn't exist or isn't a directory (a
  project may be added before it is cloned).
- root spelling: a root's resolved spelling, which path lookup compares, that no longer
  matches where the root resolves now. While the store is at this code's version they
  are refreshed in one write, through the store's whole write sequence, and the row is
  `ok` with the count; otherwise `warn`. This is the doctor's one write, and it never
  migrates: the store is already at this code's version.
- same directory: `warn` for two roots naming one directory (through a symlink, or in
  another case); path lookup picks the lower key. Every root is statted, so this runs
  only on demand.
- kinds: `warn` for rows whose kind no installed module declares. Such a row stays
  readable, its kind a label.
- agents: `fail` for a lead also in the associated set, or a name breaking the name
  rule; only a hand edit gets here.
- contributions: `fail` for each entry of the `moxi.projects` group that won't load
  (it blocks remove, add, and set of a kind no loaded module declares), and for each
  kind declared twice or with a wrong shape (it blocks add and set of that kind alone).
- notes root: `ok`, with the count, for notes roots kept while notes is not installed.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator

import moxi.store
from moxi.doctor import Row

from . import api, store
from . import contributions as _contributions
from .errors import Refused
from .rules import check_agent

MODULE = "projects"
_INSTALL = "moxi install projects"


def rows() -> Iterator[Row]:
    """Every check's row."""
    where = store.path()
    state = _store(where)
    yield state.row
    if state.version is None:
        yield from _contribution_rows(_contributions.load())
        return
    if state.version in api._READS and state.version < api._VERSION:
        yield Row(MODULE, "store version", "warn",
                  f"the store is at schema version {state.version}, older than this "
                  f"code's {api._VERSION}; it is still read, and migrates on the next "
                  "write", f"`{_INSTALL}` migrates it now")
    try:
        data = api._rows(where)
    except api.ProjectsError as exc:
        yield Row(MODULE, "store", "fail", exc.reason, exc.fix)
        return
    found = _contributions.load()
    yield _roots(data)
    yield _spelling(data, where, state.version)
    yield _same_directory(data)
    yield _kinds(data, found)
    yield _agents(data)
    yield from _contribution_rows(found)
    notes = _notes(data)
    if notes is not None:
        yield notes


class _State:
    __slots__ = ("row", "version")

    def __init__(self, row: Row, version: int | None):
        self.row = row
        self.version = version


def _store(where: str) -> _State:
    """The store row, and the schema version when the rows can be read."""
    try:
        size = os.path.getsize(where)
    except FileNotFoundError:
        return _State(Row(MODULE, "store", "ok",
                          f"no store yet at {where}: the first add makes it"), None)
    except OSError as exc:
        return _State(Row(MODULE, "store", "fail", f"{where} can't be read: {exc}",
                          "give your user read and write access to it"), None)
    if size == 0:
        return _State(Row(MODULE, "store", "ok", f"{where} is being created"), None)
    try:
        conn = moxi.store.connect(where, create=False)
    except (moxi.store.StoreError, sqlite3.Error, OSError) as exc:
        failed = api._unreadable(where, exc)
        return _State(Row(MODULE, "store", "fail", failed.reason, failed.fix), None)
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        checked = [line for (line,) in conn.execute("PRAGMA quick_check")]
    except sqlite3.Error as exc:
        failed = api._unreadable(where, exc)
        return _State(Row(MODULE, "store", "fail", failed.reason, failed.fix), None)
    finally:
        conn.close()
    if checked != ["ok"]:
        return _State(Row(MODULE, "store", "fail",
                          f"{where} fails SQLite's quick check: {'; '.join(checked[:3])}",
                          "move the file aside and add the projects again, or restore it "
                          "from a backup"), None)
    if version == 0:
        return _State(Row(MODULE, "store", "ok", f"{where} is being set up"), None)
    if version > api._VERSION:
        return _State(Row(MODULE, "store", "fail",
                          f"{where} is at schema version {version}, newer than the "
                          f"{api._VERSION} this version of moxi-projects knows",
                          f"upgrade it with `{_INSTALL}`"), None)
    if version not in api._READS:
        return _State(Row(MODULE, "store", "fail",
                          f"{where} is at schema version {version}, older than this "
                          "version of moxi-projects reads",
                          f"`{_INSTALL}` migrates it"), None)
    return _State(Row(MODULE, "store", "ok",
                      f"{where} passes its quick check at schema version {version}"),
                  version)


def _keys(keys: list[str]) -> str:
    return ", ".join(keys[:10]) + (f" and {len(keys) - 10} more" if len(keys) > 10 else "")


def _roots(data: list[tuple]) -> Row:
    bad = []
    for row in data:
        key, root = row[0], row[1]
        if not os.path.isabs(root):
            bad.append(f"{key} ({root} is not absolute)")
        elif not os.path.isdir(root):
            bad.append(f"{key} ({root} " + ("is not a directory)" if os.path.lexists(root)
                                            else "doesn't exist)"))
    if not bad:
        return Row(MODULE, "roots", "ok", f"all {len(data)} roots are directories")
    return Row(MODULE, "roots", "warn",
               f"{len(bad)} root{'s are' if len(bad) != 1 else ' is'} missing or not a "
               f"directory: {_keys(bad)}",
               "clone or make them, or point them elsewhere with `moxi projects set KEY "
               "--root DIR`")


def _stale(data: list[tuple]) -> list[tuple[str, str]]:
    return [(row[0], os.path.realpath(row[1])) for row in data
            if os.path.realpath(row[1]) != row[7]]


def _spelling(data: list[tuple], where: str, version: int) -> Row:
    stale = _stale(data)
    if not stale:
        return Row(MODULE, "root spelling", "ok", "every root resolves as when it was saved")
    keys = [key for key, _real in stale]
    if version != api._VERSION:
        return Row(MODULE, "root spelling", "warn",
                   f"{len(stale)} root{'s resolve' if len(stale) != 1 else ' resolves'} "
                   f"elsewhere now ({_keys(keys)}); the store is not at this code's "
                   "version, so the doctor leaves them",
                   f"`{_INSTALL}`, then `moxi doctor projects` refreshes them")

    def refresh(conn) -> int:
        count = 0
        for key, root, real in conn.execute("SELECT key, root, root_real FROM projects"
                                            ).fetchall():
            now = os.path.realpath(root)
            if now != real:
                conn.execute("UPDATE projects SET root_real = ? WHERE key = ?", (now, key))
                count += 1
        return count

    try:
        count = store.write(refresh, where)
    except api.ProjectsError as exc:
        return Row(MODULE, "root spelling", "warn",
                   f"{len(stale)} root{'s resolve' if len(stale) != 1 else ' resolves'} "
                   f"elsewhere now ({_keys(keys)}), and the refresh failed: {exc.reason}",
                   exc.fix)
    return Row(MODULE, "root spelling", "ok",
               f"refreshed {count} root{'s' if count != 1 else ''} that resolve elsewhere "
               f"now ({_keys(keys)})")


def _same_directory(data: list[tuple]) -> Row:
    seen: dict[tuple[int, int], list[str]] = {}
    for row in data:
        try:
            st = os.stat(row[1])
        except (OSError, ValueError):
            continue
        if st.st_ino:
            seen.setdefault((st.st_dev, st.st_ino), []).append(row[0])
    shared = [sorted(keys, key=lambda k: (k.casefold(), k)) for keys in seen.values()
              if len(keys) > 1]
    if not shared:
        return Row(MODULE, "same directory", "ok", "no two roots name one directory")
    groups = "; ".join(f"{' and '.join(keys)} (find picks {keys[0]})" for keys in shared)
    return Row(MODULE, "same directory", "warn",
               f"roots naming one directory: {groups}",
               "point all but one elsewhere with `moxi projects set KEY --root DIR`, or "
               "remove them")


def _kinds(data: list[tuple], found: _contributions.Contributions) -> Row:
    known = found.names()
    orphans: dict[str, list[str]] = {}
    for row in data:
        if row[2] not in known:
            orphans.setdefault(row[2], []).append(row[0])
    if not orphans:
        return Row(MODULE, "kinds", "ok", "every project's kind is declared")
    said = "; ".join(f"kind {kind!r} ({_keys(keys)})" for kind, keys in sorted(orphans.items()))
    unloadable = tuple(dict.fromkeys(module for module, _r in found.unloadable))
    maybe = (f"; {', '.join(unloadable)} won't load and may declare "
             f"{'them' if len(orphans) > 1 else 'it'}" if unloadable else "")
    return Row(MODULE, "kinds", "warn",
               f"no installed module declares {said}{maybe}; the rows stay readable, "
               "the kind a label",
               "install the module that declares it, or change the kind with `moxi "
               "projects set KEY --kind KIND`")


def _agents(data: list[tuple]) -> Row:
    bad = []
    for row in data:
        key, lead, associated = row[0], row[3], row[4]
        if lead is not None and lead in associated:
            bad.append(f"{key}: the lead {lead} is also associated")
        for name in ((lead,) if lead is not None else ()) + associated:
            try:
                check_agent(name)
            except Refused:
                bad.append(f"{key}: the name {name!r} breaks the name rule")
    if not bad:
        return Row(MODULE, "agents", "ok", "every agent name keeps the rules")
    return Row(MODULE, "agents", "fail", "; ".join(bad[:10]),
               "fix each with `moxi projects agents remove KEY NAME` or `moxi projects "
               "set KEY --lead NAME | --no-lead`")


def _contribution_rows(found: _contributions.Contributions) -> Iterator[Row]:
    if not found.unloadable and not found.blocked():
        count = len(found.loaded)
        yield Row(MODULE, "contributions", "ok",
                  f"{count} contribution{'s' if count != 1 else ''} to projects loaded")
        return
    for module, reason in found.unloadable:
        yield Row(MODULE, "contributions", "fail",
                  f"{module}: {reason}. It blocks remove, add, and set of a kind no loaded "
                  "module declares",
                  f"repair {module} or uninstall it (`moxi uninstall {module}`)")
    for name, reason, modules in found.blocked():
        fix = (f"repair {modules[0]} or uninstall it (`moxi uninstall {modules[0]}`)"
               if len(modules) == 1 else f"uninstall all but one of {', '.join(modules)}")
        yield Row(MODULE, "contributions", "fail",
                  f"{reason}. It blocks add and set of kind {name!r} alone", fix)


def _notes(data: list[tuple]) -> Row | None:
    kept = [row[0] for row in data if row[5] is not None]
    if not kept or _contributions.notes_present():
        return None
    return Row(MODULE, "notes root", "ok",
               f"{len(kept)} project{'s keep' if len(kept) != 1 else ' keeps'} a notes root "
               f"that nothing uses while notes is not installed ({_keys(kept)})")


__all__ = ["MODULE", "rows"]
