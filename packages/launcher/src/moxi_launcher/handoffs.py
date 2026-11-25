"""Handoffs: the key a handoff is filed under, the folder it goes in, its file name, and
which handoffs are still waiting for their seat.

A handoff is a short file a session writes for the next session of its seat. The launcher
points a seat to its waiting handoff at the seat's next start or resume (`listen_handoff`),
and gives a handoff to a launch as its first turn.

**The key.** For a launch, from its row: the comms identity without its `@scope`; else the
seat's name; else the project key. For a session the launcher did not start: the comms name
in its environment, without any `@scope`. No key, no pointer. The project a session's
folder belongs to is not a key: working in a project's folder doesn't make a session that
project's seat.

**The folder.** With notes installed, notes' `handoffs/` folder for the session's folder,
the launch's `notes_dir` passed as notes' override; where notes gives that folder no notes
directory (or can't be read), the session has no handoff folder (`NoNotesDir`). Only
without notes is it the launcher's own `handoffs` folder in its data directory. A key named
from outside its sessions (`handoffs --key`) is read in the folder of its newest launch,
else in the caller's directory's folder.

**The file name.** `<YYYY-MM-DD>-<key>.md`, then `-2`, `-3` before `.md` when the day's name
is taken. Files sort by their date, then that count. Read without a key (`others`), a name's
trailing `-<n>` (n of 2 or more) is the count only when the same date holds the name
without it; otherwise it is part of the key.

The rule is ambiguous for a key that itself ends in `-<n>`: `2026-10-29-lead-2.md` is both
seat `lead`'s second handoff of that day and seat `lead-2`'s first. A seat reads names
for its own key (`parse`), so seats `lead` and `lead-2` each take that file as their own
waiting handoff, and `lead`'s other-seat lines leave it out even on a date with no
`2026-10-29-lead.md`. Only a read without a key uses the same-date test above.

**Other seats' handoffs.** A seat's start also lists the handoffs other keys have waiting
in its folder (`others`), newest first, at most `OTHERS_SHOWN` lines, then how many more.
Listing one picks nothing: it waits for its own seat.

**A handoff waits until it is picked.** Showing it picks nothing: the pointer comes back at
every start of the seat. It counts as picked when the launcher gave it to a launch (unless
that launch ended `failed`), or when it was taken (`take`, `moxi launcher handoff take`). A
`shown` pick an older launcher recorded at a seat's start counts too. A handoff waits while
it is newer than the newest handoff of its key a pick names.

**Taking a handoff.** `take` records a `taken` pick for a handoff and its key. The key is
the one passed, else the caller's (`current`); with neither it refuses (`TakeRefused`). With
no file it takes the key's waiting handoff, and refuses when none waits. A file named
without a key must be in the caller's handoff folder and be named for the caller's key; a
file named with a key must be named for that key, wherever it is. A refusal writes nothing.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from moxi import exit as exits
from moxi import files, modules
from moxi.paths import Layout

if TYPE_CHECKING:
    from .store import LaunchRow

log = logging.getLogger("moxi.launcher.handoffs")

MODULE = "launcher"
#: The launcher's own handoff folder, under its data directory (the `handoffs` store).
HANDOFFS_DIR = "handoffs"
def comms_name_env() -> str | None:
    """Comms' identity stamp, when the optional comms module is available."""
    if not modules.available("comms"):
        return None
    found = modules.load("comms", interface=2)
    return found.api.NAME_ENV if found.ok and found.api is not None else None
#: What a key may be: it is part of a file name.
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_DATE = r"(\d{4}-\d{2}-\d{2})"
#: How many other seats' handoffs a start lists before it counts the rest.
OTHERS_SHOWN = 5


class NoKey(Exception):
    """The session has no key, so it has no handoff folder of its own."""

    exit_code = exits.REFUSED


class TakeRefused(Exception):
    """`take` can't pick the handoff asked for; nothing was written."""

    exit_code = exits.REFUSED

    def __init__(self, code: str, problem: str, hint: str | None = None):
        self.code = code
        self.problem = problem
        self.hint = hint
        super().__init__(problem if hint is None else f"{problem}; {hint}")


class NoNotesDir(Exception):
    """Notes is installed and gives the session's folder no notes directory, so the
    session has no handoff folder: the launcher's own is used only without notes."""

    code = "no-notes-dir"
    exit_code = exits.REFUSED

    def __init__(self, cwd: str | Path, reason: str | None = None):
        self.cwd = str(cwd)
        self.problem = (f"this launch has no notes directory (no notes_dir and none for "
                        f"{cwd}), so it has no handoffs/ to read from")
        if reason:
            self.problem += f": {reason}"
        self.hint = "launch it with notes_dir set, or in a folder notes keeps notes for"
        super().__init__(f"{self.problem}; {self.hint}")


@dataclass(frozen=True)
class Handoff:
    """One handoff file of a key."""

    path: Path
    date: str
    count: int

    @property
    def order(self) -> tuple[str, int]:
        return (self.date, self.count)


# -- the key -----------------------------------------------------------------------------


def check_key(key: str | None) -> str | None:
    """`key` when it can be part of a file name, else None (and one log line)."""
    if not key:
        return None
    if _KEY.fullmatch(key):
        return key
    log.warning("handoff key %r can't be part of a file name; no handoff is kept for it",
                key)
    return None


def key_of_row(row) -> str | None:
    """A launch's key: its identity without the `@scope`, else its seat's name, else its
    project key."""
    if row.identity:
        return check_key(row.identity.split("@", 1)[0])
    if row.preset_kind == "seat" and row.preset_name:
        return check_key(row.preset_name)
    return check_key(row.project_key)


def key_of_env(env: Mapping[str, str]) -> str | None:
    """The key of a session the launcher did not start: its comms name without any
    `@scope`."""
    name = env.get(comms_name_env() or "")
    return check_key(name.split("@", 1)[0]) if name else None


# -- the folder --------------------------------------------------------------------------


def own_handoffs_dir(env: Mapping[str, str] | None = None, *, create: bool = True) -> Path:
    """The launcher's own handoff folder, used when notes is not installed."""
    handoffs_dir = Layout(MODULE).data_dir(env) / HANDOFFS_DIR
    if create:
        files.private_dir(handoffs_dir)
    return handoffs_dir


def handoff_dir(cwd: str | Path, *, notes_dir: str | None = None,
           env: Mapping[str, str] | None = None, create: bool = True,
           create_own: bool | None = None) -> Path:
    """The handoff folder for a session working in `cwd`.

    With notes installed, notes' `handoffs/` folder for `cwd`, `notes_dir` (a launch's
    `notes_dir` value) passed as its override; raises `NoNotesDir` when notes gives none
    or can't be read. Without notes, the launcher's own. `create` is passed to notes, and
    makes the launcher's own folder unless `create_own` says otherwise.
    """
    if modules.available("notes"):
        return _notes_handoff_dir(cwd, notes_dir, create=create)
    return own_handoffs_dir(env, create=create if create_own is None else create_own)


def handoff_dir_of_row(row, *, env: Mapping[str, str] | None = None,
                  create: bool = True) -> Path:
    """A launch's handoff folder, for its cwd and its `notes_dir` value. Raises
    `NoNotesDir` as `handoff_dir` does."""
    notes_dir = row.values.get("notes_dir")
    return handoff_dir(row.cwd, notes_dir=notes_dir or None, env=env, create=create)


@dataclass(frozen=True)
class KeyFolder:
    """The handoff folder `handoff_dir_of_key` read for a key, and the launch it came from:
    None when the key has no launch and the folder is the caller's directory's."""

    handoff_dir: Path
    launch: LaunchRow | None


def handoff_dir_of_key(key: str, *, cwd: str | Path | None = None,
                  env: Mapping[str, str] | None = None, create: bool = False) -> KeyFolder:
    """`key`'s own handoff folder: the one of its newest launch (`handoff_dir_of_row`), so a
    seat that works in another project is read where it files. A key with no launch, or
    no launch log at all, gets the folder of `cwd` (this directory by default). Raises
    `NoNotesDir` as `handoff_dir` does."""
    launch = newest_launch(key, env=env)
    if launch is not None:
        return KeyFolder(handoff_dir_of_row(launch, env=env, create=create), launch)
    where = cwd if cwd is not None else Path.cwd()
    return KeyFolder(handoff_dir(where, env=env, create=create), None)


def newest_launch(key: str, *, env: Mapping[str, str] | None = None) -> LaunchRow | None:
    """The newest launch whose key (`key_of_row`) is `key`, or None, also when the launch
    log was never made. Raises `moxi.store.StoreTooNew` when a newer launcher wrote it."""
    from moxi import store as core_store

    from . import store

    try:
        conn = store.connect(env=env, create=False)
    except core_store.StoreTooNew:
        raise
    except core_store.StoreError:
        return None
    try:
        # Every row that can have the key, newest first; `key_of_row` decides, since a
        # row's identity outranks its seat's name and its project key.
        cursor = conn.execute(
            "SELECT " + ", ".join(store.COLUMNS) + " FROM launches WHERE identity = ? OR "
            "substr(identity, 1, ?) = ? OR preset_name = ? OR project_key = ? "
            "ORDER BY created_at DESC, rowid DESC",
            (key, len(key) + 1, key + "@", key, key))
        names = [column[0] for column in cursor.description]
        for values in cursor:
            row = store.LaunchRow(**dict(zip(names, values)))
            if key_of_row(row) == key:
                return row
        return None
    finally:
        conn.close()


def notes_home(cwd: str | Path) -> Path | None:
    """The notes directory notes gives a session in `cwd`, or None: notes not installed,
    not readable, or giving that folder none. Makes nothing."""
    if not modules.available("notes"):
        return None
    try:
        return _notes_handoff_dir(cwd, None, create=False).parent
    except NoNotesDir:
        return None


def _notes_handoff_dir(cwd, notes_dir, *, create: bool) -> Path:
    found = modules.load("notes", interface=1)
    handoffs_dir = getattr(found.api, "handoffs_dir", None) if found.api else None
    if found.state != modules.PRESENT or handoffs_dir is None:
        reason = found.reason or "notes has no handoffs_dir"
        log.warning("notes is installed but its handoff folder can't be read (%s)", reason)
        raise NoNotesDir(cwd, f"notes can't be read ({reason})")
    try:
        got = handoffs_dir(str(cwd), create=create, override=notes_dir)
    except Exception as exc:  # noqa: BLE001 - a broken link costs the folder, not the caller
        log.warning("notes' handoff folder for %s failed: %s: %s", cwd,
                    type(exc).__name__, exc)
        raise NoNotesDir(cwd, f"{type(exc).__name__}: {exc}") from None
    if got is None:
        raise NoNotesDir(cwd)
    return Path(got)


# -- the files ---------------------------------------------------------------------------


def file_name(key: str, date: str, count: int = 1) -> str:
    """The file name of the `count`th handoff of `key` on `date` (`YYYY-MM-DD`)."""
    return f"{date}-{key}.md" if count <= 1 else f"{date}-{key}-{count}.md"


def parse(path: str | Path, key: str) -> Handoff | None:
    """The handoff `path` names when its file name is one of `key`'s, else None. A
    trailing `-<n>` is read as `key`'s count whether or not the date holds the name
    without it, so `<date>-<key>-<n>.md` is `key`'s even when a seat named `<key>-<n>`
    wrote it (the module's note on the file name)."""
    match = re.fullmatch(_DATE + "-" + re.escape(key) + r"(?:-([2-9]|[1-9]\d+))?\.md",
                         Path(path).name)
    if match is None:
        return None
    return Handoff(Path(path), match.group(1), int(match.group(2) or 1))


def handoffs(where: str | Path, key: str) -> list[Handoff]:
    """The handoff files of `key` in folder `where`, newest first."""
    try:
        entries = list(Path(where).iterdir())
    except OSError:
        return []
    found = [h for p in entries if p.is_file() and (h := parse(p, key)) is not None]
    return sorted(found, key=lambda h: h.order, reverse=True)


def unpicked(where: str | Path, key: str, picked: Iterable[str]) -> list[Handoff]:
    """The handoffs of `key` in `where` newer than the newest one a pick names, newest
    first. `picked` is the paths `store.picked(conn, key)` returns."""
    named = [h.order for p in picked if (h := parse(p, key)) is not None]
    newest = max(named) if named else None
    return [h for h in handoffs(where, key) if newest is None or h.order > newest]


def waiting(conn, where: str | Path, key: str) -> Handoff | None:
    """The newest handoff of `key` still waiting for its seat, or None. `conn` is the
    launch log, or None when it was never made (nothing was picked)."""
    from . import store

    left = unpicked(where, key, store.picked(conn, key) if conn is not None else [])
    return left[0] if left else None


def pointer(handoff: Handoff) -> str:
    """The line a seat's start shows for its waiting handoff."""
    return (f"Handoff waiting: {handoff.path} ({handoff.date}). Read it before other work, "
            f"then run: {take_command(handoff.path)}")


def take_command(path: str | Path) -> str:
    """The command that takes the handoff at `path`."""
    return f"moxi launcher handoff take {path}"


def keyed(where: str | Path) -> list[tuple[str, Handoff]]:
    """Every handoff file in folder `where` with the key its name gives: the date, then
    the key, then an optional `-<n>` count that is a count only when the same date also
    holds the name without it. Names that give no usable key are left out."""
    try:
        paths = [p for p in Path(where).iterdir() if p.is_file()]
    except OSError:
        return []
    names = {p.name for p in paths}
    out: list[tuple[str, Handoff]] = []
    for path in paths:
        match = re.fullmatch(_DATE + r"-(.+)\.md", path.name)
        if match is None:
            continue
        date, rest = match.group(1), match.group(2)
        key, count = rest, 1
        counted = re.fullmatch(r"(.+)-([2-9]|[1-9]\d+)", rest)
        if counted is not None and f"{date}-{counted.group(1)}.md" in names:
            key, count = counted.group(1), int(counted.group(2))
        if _KEY.fullmatch(key):
            out.append((key, Handoff(path, date, count)))
    return out


def others(conn, where: str | Path, key: str | None) -> list[tuple[str, Handoff]]:
    """Each other key's newest handoff still waiting in folder `where`, as `(key,
    handoff)`, newest first (by date, then when the file was last written). `key` is the
    asking seat's, left out with every file its own names read as; None leaves out
    nothing. `conn` is the launch log, or None when it was never made."""
    from . import store

    grouped: dict[str, list[Handoff]] = {}
    for other, found in keyed(where):
        if other == key or (key is not None and parse(found.path, key) is not None):
            continue
        grouped.setdefault(other, []).append(found)
    out: list[tuple[str, Handoff]] = []
    for other, found in grouped.items():
        picked = store.picked(conn, other) if conn is not None else []
        named = [h.order for p in picked if (h := parse(p, other)) is not None]
        newest = max(named) if named else None
        left = [h for h in found if newest is None or h.order > newest]
        if left:
            out.append((other, max(left, key=lambda h: h.order)))
    return sorted(out, key=lambda item: (item[1].date, _mtime(item[1].path), item[0]),
                  reverse=True)


def other_lines(found: list[tuple[str, Handoff]], shown: int | None = None) -> list[str]:
    """The lines a seat's start shows for other keys' waiting handoffs: at most `shown`
    (`OTHERS_SHOWN`), then one line counting the rest."""
    shown = OTHERS_SHOWN if shown is None else shown
    lines = [f"Another seat's handoff: {h.path} ({other}, {h.date})"
             for other, h in found[:shown]]
    if len(found) > shown:
        lines.append(f"\u2026 and {len(found) - shown} more")
    return lines


def _mtime(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


# -- the current session -----------------------------------------------------------------


@dataclass(frozen=True)
class Current:
    """The current session's key and handoff folder."""

    key: str
    handoff_dir: Path
    request_id: str | None


def current(env: Mapping[str, str] | None = None, *, cwd: str | Path | None = None,
            create: bool = True) -> Current:
    """The key and handoff folder of the session whose environment is `env` (this
    process's by default), for `moxi launcher handoff dir`.

    A session the launcher started is read through its launch stamp; any other through
    its comms name, in `cwd` (this directory by default). Raises `NoKey` when the session
    has no key, and `NoNotesDir` as `handoff_dir` does.
    """
    from . import listeners, store

    env = os.environ if env is None else env
    request_id = env.get(listeners.REQUEST_ENV)
    if request_id:
        conn = listeners.open_store()
        row = None
        if conn is not None:
            try:
                row = store.get(conn, request_id)
            finally:
                conn.close()
        if row is not None:
            key = key_of_row(row)
            if key is None:
                raise NoKey(f"launch {request_id} has no identity, seat or project key, "
                            "so it has no handoff folder")
            return Current(key, handoff_dir_of_row(row, create=create), request_id)
    key = key_of_env(env)
    if key is None:
        raise NoKey("this session has no comms name and no launch of the launcher's, so "
                    "it has no handoff folder")
    return Current(key, handoff_dir(cwd if cwd is not None else Path.cwd(), create=create), None)


# -- taking a handoff --------------------------------------------------------------------


@dataclass(frozen=True)
class Taken:
    """The handoff `take` picked, the key it was taken under and the folder read."""

    key: str
    path: Path
    handoff_dir: Path
    request_id: str | None


def take(file: str | Path | None = None, *, key: str | None = None,
         env: Mapping[str, str] | None = None, cwd: str | Path | None = None) -> Taken:
    """Record a `taken` pick for the handoff `file`, or with no file for the waiting
    handoff of the key, and return what was taken.

    `key` names the key; without it the key and folder are the caller's (`current`, with
    `env` this process's by default and `cwd` this directory). `env` is read for the
    session's launch stamp and comms name only; the launch log and the folders are this
    process's. Raises `TakeRefused`,
    writing nothing, when there is no key, when `file` is not a file, is named for
    another key, or (with no `key` passed) lies outside the caller's handoff folder, and
    when no file is named and nothing waits. Raises `NoNotesDir` as `handoff_dir` does.
    """
    from . import listeners, store

    env = os.environ if env is None else env
    request_id = env.get(listeners.REQUEST_ENV) or None
    if key is not None:
        checked = check_key(key)
        if checked is None:
            raise TakeRefused("bad-key", f"{key!r} can't be a handoff key",
                              "a key is letters, digits, '.', '_' and '-'")
        key = checked
        where = handoff_dir_of_key(key, cwd=cwd).handoff_dir
        named = True
    else:
        try:
            here = current(env, cwd=cwd, create=False)
        except NoKey:
            raise TakeRefused("no-seat", "this session has no seat",
                              "pass --key") from None
        key, where, named = here.key, here.handoff_dir, False
    if file is not None:
        path = Path(file).expanduser().absolute()
        if not path.is_file():
            raise TakeRefused("no-handoff", f"{path} is not a file")
        if parse(path, key) is None:
            raise TakeRefused("another-key", f"{path.name} is not a handoff of {key}",
                              None if named else "pass --key with the key it names")
        if not named and not _same_dir(path.parent, where):
            raise TakeRefused("another-folder",
                              f"{path} is not in {key}'s handoff folder {where}",
                              "pass --key with the key it names")
    conn = store.connect()
    try:
        if file is None:
            found = waiting(conn, where, key)
            if found is None:
                raise TakeRefused("nothing-waiting",
                                  f"no handoff of {key} is waiting in {where}",
                                  f"moxi launcher handoffs --key {key} lists them")
            path = found.path
        store.add_pick(conn, str(path), key, "taken", request_id=request_id)
    finally:
        conn.close()
    return Taken(key, path, where, request_id)


def _same_dir(one: Path, other: Path) -> bool:
    try:
        return one.resolve() == Path(other).resolve()
    except OSError:
        return False


__all__ = ["comms_name_env", "HANDOFFS_DIR", "OTHERS_SHOWN", "Current", "Handoff",
           "KeyFolder", "NoKey", "NoNotesDir", "TakeRefused", "Taken", "check_key",
           "current", "file_name",
           "handoff_dir", "handoff_dir_of_key", "handoff_dir_of_row", "handoffs",
           "key_of_env", "key_of_row", "keyed", "newest_launch", "notes_home",
           "other_lines", "others", "own_handoffs_dir", "parse", "pointer", "take",
           "take_command", "unpicked", "waiting"]
