"""The skill placer: a module's skills and agent files, placed where a harness reads them.

One placer serves `moxi install` and anything that places on a user's behalf (a project
manager placing a project's own skills, say). The caller names what it wants placed, as
`Item`s, and the placer plans, applies, reads back and removes those placements, keeping
a ledger of what each owner placed.

    plan(owner, items, form=None)        what placing `items` would do (`Step` per item)
    apply(steps, replace=False)          carry a plan out (`Placed` per placement left)
    status(owner)                        each placement of `owner` read against the disk
    remove(owner)                        remove every placement of `owner`
    adopt(from_owner, to_owner, placed, seeded=False)
                                         hand placements from one owner to another
    harnesses()                          the harness table, and a line per row skipped
    chosen()                             `skills.harnesses`, and a line per name skipped
    from_package(found, source)          whether a source lies in a module's own package

`plan` and `status` only read and take no lock. `apply`, `remove` and `adopt` take the
core lock with `nested=True`: under an install (its own process, or a settle or teardown
child that adopted the install's claim) they yield at once, and any other caller takes the
lock and waits for it. To plan and apply as one step outside an install, hold
`moxi.files.lock(moxi.paths.core_lock(), nested=True)` around both.

Where an item goes
------------------

An item names its `source` (a skill's folder, or one file), a `harness` and a `name`. With
no `dest` it goes to `<the harness row's folder for its kind>/<name>`: a `skill` to the
row's skills folder, an `agent` to its agents folder. With `dest`, an absolute path, it
goes exactly there, and `harness` only labels the placement. The harness table is the core
setting `harnesses` (`moxi config moxi`): one row per harness, `{name, skills, agents}`. An
empty folder is the built-in one, which only `claude` (`$CLAUDE_CONFIG_DIR/skills` and
`/agents`, else `~/.claude/...`) and `codex` (`~/.agents/skills`, and `$CODEX_HOME/agents`,
else `~/.codex/agents`) have; another row with an empty folder has no folder of that
kind. `~` is expanded. A row whose name is not a lower-case word (letters, digits, `-`,
`_`), that repeats an earlier row's name, or whose folder is not a string or not absolute
once `~` is expanded, is skipped with one line (`harnesses()`), and the first row of a
name wins.

A placement is known by its owner and its target, the path it sits at: made absolute, `~`
expanded, and its parent folder resolved (the target itself is not followed, since it may
be the link). So one owner may place one name into two project roots as two placements,
and two items of one owner naming one target are one placement: the second is refused.

The form
--------

The core setting `skills.form` (or `plan`'s `form=`) is `auto`, `link` or `copy`. `auto`
links on Linux and macOS. On Windows a folder links as a directory junction under `link`;
under `auto` it is copied, until Claude Code and Codex are seen reading a skill through a
junction (`_AUTO_JUNCTION`). A single file on Windows links when a test symlink in the
target's folder succeeds, and is otherwise copied with one note. A link is made directly
at the target, so an existing path makes it fail. A copy is made beside the target as
`.<name>.moxi-tmp` and renamed into place.

Ownership is by mark. A link or junction is the owner's when the ledger names the owner
for it and it points at the source (or at the source the ledger recorded, a source that
moved with an upgrade). A copied folder holds `.moxi-placed`, `{"owner", "source",
"source_hash", "hash"}`, `hash` taken over the copy without the mark. A copied file has no
room for a mark, so its ledger entry is its mark. Hashes are SHA-256 over sorted relative
paths and file bytes. The ledger is `STATE/moxi/placed.json`:
`{owner: {"placed": {target: placement}, "handed": {target: handed}}}`.

What `plan` decides (`Step.action`, with `cause` for a `refuse`):

    add      nothing at the target
    keep     the owner's placement, current, in the form resolved now; or a held
             item's target that is its source ("held in place")
    update   the owner's placement with a changed source and an unmodified copy, a
             link to an older source, or in another form than the one resolved now
    remove   an entry of the owner's ledger that no item's target names
    skip     a target the owner handed to another (`adopt`), while any other owner's
             ledger places it (the taker, or one it handed the target on to); once no
             owner does, the item plans as any other, and `apply` drops the lapsed
             hand-over when it places the target
    refuse   source (missing), io (an OSError reading this item), held, duplicate,
             harness (no such row, or no folder of the item's kind), name (not a
             single path part), occupant (a path in the way: a link the placer did
             not make, a folder it did not mark or marked for another owner, a file
             it did not place, or a placed copy since modified), tracked (such an
             occupant that git tracks, or a marked folder this ledger lacks that git
             tracks), owner (another owner's placement)

`tracked` differs from `occupant` only in its name, so a caller can tell that the path in
the way is in git without reading the reason; its reason reads as an occupant's, with
", and git tracks it" added. Whatever reads cause `occupant` reads `tracked` alike.

`plan` never raises for a cause that belongs to one item: each item yields exactly one
step. Only a wrong call raises: an element of `items` that is not an `Item` (`TypeError`),
an unknown kind, form or a relative `dest` (`ValueError`). An item whose source is missing
leaves its placement exactly as it is, even a link that now dangles, and its target still
counts as named, so nothing removes it. `apply(..., replace=True)` moves a refused
occupant (cause `occupant` or `tracked`) to
`DATA/moxi/displaced/<UTC stamp>/<harness>/<name>` and places the item; it never takes
another owner's placement.

Held items
----------

`held=True` says the source sits inside a project tree and is the project's own copy;
`root` names the project's root (an absolute folder). A held item with no `root`, or whose
source does not lie inside it, is refused. A target that resolves to the source itself is
`keep`, "held in place": never read as an occupant, never removed (dropping the item
drops only its ledger entry). Every other target of a held item must lie inside the same
root. It takes the form resolved as for any item, so `copy` wins over held: such a copy
carries `"held": true` in its mark. A link of a held item is relative, so it still
resolves when the project tree moves; on Windows that is a relative symlink when the test
symlink succeeds, else a copy marked held. A held source is never touched.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

_WINDOWS = sys.platform == "win32"

#: How a placement is made.
FORMS = ("link", "junction", "copy")
#: What `skills.form` and `plan`'s `form=` may say.
SETTINGS = ("auto", "link", "copy")
#: What an item is: a skill (a folder) or an agent (one file).
KINDS = ("skill", "agent")
#: What `plan` may decide for one item, or for one ledger entry no item names.
ACTIONS = ("add", "keep", "update", "remove", "refuse", "skip")
#: The causes a `refuse` carries.
CAUSES = ("source", "io", "held", "duplicate", "harness", "name", "occupant", "tracked",
          "owner")
#: The causes of a `refuse` for a path in the way, which `apply(..., replace=True)` moves
#: aside.
_OCCUPANTS = ("occupant", "tracked")
#: What `status` may read for one placement.
STATES = ("ok", "modified", "stale", "missing")

#: The mark a copied folder holds.
MARK = ".moxi-placed"
#: The ledger's file name, in the core's state directory.
LEDGER = "placed.json"
#: The form a placement held in place records: the target is the source itself.
IN_PLACE = "in place"
#: The reason of a held item's `keep` whose target resolves to its source.
HELD_IN_PLACE = "held in place"
#: The one note for a file copied on Windows because this account can't make symlinks.
FILE_NOTE = ("agent files are copied, since this account can't make symlinks; "
             "turn on Developer Mode to link them")
#: The same note for a held item's folder, which can't be a junction (a junction is
#: always absolute, so it would break when the project tree moves).
FOLDER_NOTE = ("a project's own skills are copied, since this account can't make "
               "symlinks; turn on Developer Mode to link them")

#: Whether `auto` links a folder as a junction on Windows. Off until Claude Code and Codex
#: are seen reading a skill through a junction; till then `auto` copies folders there.
_AUTO_JUNCTION = False

#: The built-in folders of the two harnesses that have them: (skills, agents).
_BUILT_IN = ("claude", "codex")
_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-_")
_GIT_SECONDS = 10.0
_BLOCK = 64 * 1024


# -- records ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Item:
    """One thing an owner wants placed.

    `source` is a skill's folder or one file. `harness` names a row of the harness table;
    `name` is the placement's name in that harness's folder. `kind` is `skill` or `agent`.
    `dest`, an absolute path, places the item exactly there instead, and `harness` then
    only labels the placement. `held=True` says the source is a project's own copy inside
    `root`, the project's root (see the module's docs).
    """

    source: Path
    harness: str
    name: str
    kind: str = "skill"
    dest: Path | None = None
    held: bool = False
    root: Path | None = None


@dataclass(frozen=True)
class Step:
    """What `plan` decided for one item, or for one ledger entry no item names.

    `target` is the placement's path (None only when the item has no folder to go to).
    `form` is how the placement is or will be made (`FORMS`). `action` is one of
    `ACTIONS`, and `cause` says why for a `refuse` (`CAUSES`), else None. `reason` is one
    line for a person. `occupant` is the path in the way of a `refuse` with cause
    `occupant` or `tracked`, which `apply(..., replace=True)` moves aside.

    `owner` is the plan's owner, which `apply` records the placement under. `note` is a
    line `apply` says once for the whole plan (the Windows copy note), or None.
    """

    item: Item
    target: Path | None
    form: str
    action: str
    cause: str | None
    reason: str
    occupant: Path | None = None
    owner: str = ""
    note: str | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class Placed:
    """One placement, as the ledger and the disk say: `path` is its target, `form` how it
    was made (`FORMS`, or `"in place"` for a held item's own source), `state` one of
    `STATES`."""

    owner: str
    kind: str
    harness: str
    name: str
    path: Path
    form: str
    state: str


@dataclass(frozen=True)
class Harness:
    """One row of the harness table: its folders, absolute, or None for none."""

    name: str
    skills: Path | None
    agents: Path | None

    def folder(self, kind: str) -> Path | None:
        return self.skills if kind == "skill" else self.agents


# -- the harness table --------------------------------------------------------------------

def _core_settings():
    from . import config

    return config.load("moxi", config._core_schema())


def harnesses(settings=None) -> tuple[dict[str, Harness], list[str]]:
    """The harness table: each valid row by name, and one line per row skipped."""
    if settings is None:
        settings = _core_settings()
    rows: dict[str, Harness] = {}
    lines: list[str] = []
    for number, row in enumerate(settings["harnesses"], 1):
        problem = _row_problem(row, rows)
        if problem:
            lines.append(f"harnesses row {number} is skipped: {problem}")
            continue
        name = row["name"]
        rows[name] = Harness(name, _folder(name, "skills", row.get("skills", "")),
                             _folder(name, "agents", row.get("agents", "")))
    return rows, lines


def chosen(settings=None) -> tuple[list[str], list[str]]:
    """The names in `skills.harnesses` that have a row, in order, and one line per name
    that has none (or names a row that is skipped)."""
    if settings is None:
        settings = _core_settings()
    rows, _ = harnesses(settings)
    names: list[str] = []
    lines: list[str] = []
    for name in settings["skills.harnesses"]:
        if name in rows:
            if name not in names:
                names.append(name)
        else:
            lines.append(f"skills.harnesses names {name}, which has no row in harnesses")
    return names, lines


def _row_problem(row, seen: dict) -> str | None:
    if not isinstance(row, dict):
        return "it is not a table"
    name = row.get("name")
    if not isinstance(name, str) or not name or not set(name) <= _NAME_CHARS:
        return f"its name {name!r} is not a lower-case word"
    if name in seen:
        return f"{name} repeats an earlier row's name"
    for kind in ("skills", "agents"):
        value = row.get(kind, "")
        if not isinstance(value, str):
            return f"{name}'s {kind} folder is not a string"
        if value and not os.path.isabs(os.path.expanduser(value)):
            return f"{name}'s {kind} folder {value!r} is not an absolute path"
    return None


def _folder(name: str, kind: str, value: str) -> Path | None:
    if value:
        return Path(os.path.abspath(os.path.expanduser(value)))
    if name == "claude":
        base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join("~", ".claude")
        return Path(os.path.abspath(os.path.expanduser(base))) / kind
    if name == "codex":
        if kind == "skills":
            base = os.path.join("~", ".agents")
        else:
            base = os.environ.get("CODEX_HOME") or os.path.join("~", ".codex")
        return Path(os.path.abspath(os.path.expanduser(base))) / kind
    return None


# -- plan ---------------------------------------------------------------------------------

def plan(owner: str, items: list[Item], *, form: str | None = None) -> list[Step]:
    """What placing `items` for `owner` would do: one `Step` per item, in order, then one
    `remove` per ledger entry of `owner` that no item's target names.

    `items` is the owner's whole wanted set. `form` overrides `skills.form` for this
    plan. Takes no lock and changes nothing (on Windows it may make and remove a test
    symlink, `.moxi-probe-<hex>`). Raises only for a wrong call (see the module's docs).
    """
    if not isinstance(owner, str) or not owner:
        raise TypeError(f"owner must be a non-empty string, not {owner!r}")
    items = list(items)
    for item in items:
        _check_item(item)
    if form is not None and form not in SETTINGS:
        raise ValueError(f"form must be one of {', '.join(SETTINGS)}, not {form!r}")
    settings = _core_settings()
    setting = form if form is not None else settings["skills.form"]
    rows, skipped = harnesses(settings)
    planner = _Planner(owner, setting, rows, skipped, _read_ledger())
    steps = [planner.step(item) for item in items]
    steps.extend(planner.removals())
    return steps


def _check_item(item) -> None:
    if not isinstance(item, Item):
        raise TypeError(f"plan takes Item objects, not {type(item).__name__}")
    if item.kind not in KINDS:
        raise ValueError(f"an item's kind is skill or agent, not {item.kind!r}")
    if item.dest is not None and not os.path.isabs(os.path.expanduser(str(item.dest))):
        raise ValueError(f"an item's dest must be an absolute path, not {item.dest}")


def target_key(path: str | os.PathLike) -> str:
    """The ledger's key for a target: absolute, `~` expanded, its parent folder resolved
    with `os.path.realpath` and the last part left as it is (it may be the link)."""
    path = os.path.abspath(os.path.expanduser(os.fspath(path)))
    parent, base = os.path.split(path)
    return os.path.join(os.path.realpath(parent), base)


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _good_name(name: object) -> bool:
    return (isinstance(name, str) and name not in ("", ".", "..")
            and "/" not in name and os.sep not in name
            and not (os.altsep and os.altsep in name))


class _Planner:
    def __init__(self, owner: str, setting: str, rows: dict, skipped: list[str],
                 ledger: dict):
        self.owner = owner
        self.setting = setting
        self.rows = rows
        self.skipped = skipped
        self.ledger = ledger
        mine = ledger.get(owner, {})
        self.placed: dict = mine.get("placed", {})
        self.handed: dict = mine.get("handed", {})
        self.named: set[str] = set()
        # (harness, kind, name) of items refused for want of a harness folder: their
        # old placements stay, since a broken row is no reason to remove them.
        self.unrouted: set[tuple] = set()
        self.git: dict[str, frozenset | None] = {}
        self.source_hash: str | None = None

    def _step(self, item, target, action, cause, reason, *, form="link", occupant=None,
              note=None) -> Step:
        return Step(item, Path(target) if target is not None else None, form, action, cause,
                    reason, occupant=Path(occupant) if occupant is not None else None,
                    owner=self.owner, note=note)

    def target(self, item: Item) -> tuple[str | None, str | None, str]:
        """The item's target key, or None with the cause and the line that say why it
        has none."""
        if item.dest is not None:
            return target_key(item.dest), None, ""
        if not _good_name(item.name):
            return None, "name", f"{item.name!r} is not a name a folder can hold"
        row = self.rows.get(item.harness)
        if row is None:
            why = f"harness {item.harness} has no row in harnesses"
            if self.skipped:
                why += " (some rows are skipped: moxi config moxi shows the table)"
            return None, "harness", why
        folder = row.folder(item.kind)
        if folder is None:
            return None, "harness", f"harness {item.harness} has no {item.kind}s folder"
        return target_key(folder / item.name), None, ""

    def step(self, item: Item) -> Step:
        key, cause, why = self.target(item)
        if key is None:
            self.unrouted.add((item.harness, item.kind, item.name))
            return self._step(item, None, "refuse", cause, why)
        duplicate = key in self.named
        self.named.add(key)
        if duplicate:
            return self._step(item, key, "refuse", "duplicate", f"two items place {key}")
        handed = self.handed.get(key)
        if handed is not None and _held_by_another(self.ledger, self.owner, key):
            to = handed.get("to", "another owner")
            return self._step(item, key, "skip", None,
                              f"{item.name} was handed to {to}; skipped")
        other = self._other_owner(key)
        if other is not None:
            return self._step(item, key, "refuse", "owner", f"{key} is {other}'s placement")
        try:
            return self._decide(item, key)
        except OSError as exc:
            return self._step(item, key, "refuse", "io", f"{key}: {exc}")

    def _decide(self, item: Item, key: str) -> Step:
        source = os.path.abspath(os.fspath(item.source))
        if not os.path.exists(source):
            return self._step(item, key, "refuse", "source", f"source missing: {source}")
        # Read the whole source now, so an unreadable file is this item's refusal
        # (cause io) whatever the form, and a copy is compared against this hash.
        self.source_hash = _hash(source)
        real_source = os.path.realpath(source)
        entry = self.placed.get(key)
        if item.held:
            root = None if item.root is None else os.fspath(item.root)
            if root is None or not os.path.isabs(root) \
                    or not _inside(real_source, os.path.realpath(root)):
                return self._step(item, key, "refuse", "held", "held source is in no project")
            if self._in_place(key, real_source, entry):
                return self._step(item, key, "keep", None, HELD_IN_PLACE,
                                  form=self._form(item, source, key)[0])
            if not _inside(key, os.path.realpath(root)):
                return self._step(item, key, "refuse", "held",
                                  f"{key} is outside the project of a held source")
        form, note = self._form(item, source, key)
        if not os.path.lexists(key):
            return self._step(item, key, "add", None, f"place {item.name} at {key}",
                              form=form, note=note)
        if _is_link(key):
            return self._link(item, key, source, entry, form, note)
        if os.path.isdir(key):
            return self._folder_copy(item, key, source, entry, form, note)
        return self._file_copy(item, key, source, entry, form, note)

    @staticmethod
    def _in_place(key: str, real_source: str, entry: dict | None) -> bool:
        """Whether a held item's target resolves to its source and is not a link this
        owner placed (a link the ledger names is managed as one: kept, updated or
        removed). The source itself, or a link a clone or a moved tree brought along,
        is held in place."""
        if not os.path.lexists(key) or os.path.realpath(key) != real_source:
            return False
        placed_link = entry is not None and entry.get("form") in ("link", "junction")
        return not (placed_link and _is_link(key))

    def _form(self, item: Item, source: str, key: str) -> tuple[str, str | None]:
        """The form the item takes now, and the note to say for it, if any."""
        if self.setting == "copy":
            return "copy", None
        if not _WINDOWS:
            return "link", None
        folder = os.path.isdir(source)
        if item.held:
            if _symlink_allowed(os.path.dirname(key)):
                return "link", None
            return "copy", (FOLDER_NOTE if folder else FILE_NOTE)
        if folder:
            if self.setting == "link" or _AUTO_JUNCTION:
                return "junction", None
            return "copy", None
        if _symlink_allowed(os.path.dirname(key)):
            return "link", None
        return "copy", FILE_NOTE

    def _other_owner(self, key: str) -> str | None:
        for name, record in self.ledger.items():
            if name != self.owner and key in record.get("placed", {}):
                return name
        return None

    def _occupant(self, item, key, form, what: str) -> Step:
        cause = "occupant"
        if self._tracked(key):
            cause, what = "tracked", f"{what}, and git tracks it"
        return self._step(item, key, "refuse", cause, f"{key} is {what}", form=form,
                          occupant=key)

    def _link(self, item, key, source, entry, form, note) -> Step:
        pointed = _link_points(key)
        recorded = entry.get("source") if entry else None
        ours = entry is not None and (_same(pointed, source)
                                      or (recorded is not None and _same(pointed, recorded)))
        if not ours:
            return self._occupant(item, key, form, "a link the placer did not make")
        made = "junction" if _is_junction(key) else "link"
        if made != form:
            return self._step(item, key, "update", None, f"{item.name}: now a {form}",
                              form=form, note=note)
        if os.readlink(key) != _link_text(item, source, key) and made == "link":
            return self._step(item, key, "update", None, f"{item.name}: the source moved",
                              form=form, note=note)
        if not _same(pointed, source):
            return self._step(item, key, "update", None, f"{item.name}: the source moved",
                              form=form, note=note)
        return self._step(item, key, "keep", None, f"{item.name} is current", form=form)

    def _folder_copy(self, item, key, source, entry, form, note) -> Step:
        mark = _read_mark(key)
        if mark is None or mark.get("owner") != self.owner:
            return self._occupant(item, key, form, "a folder the placer did not mark")
        if entry is None and self._tracked(key):
            # A mark with no ledger entry here came from elsewhere (a commit): not ours.
            return self._occupant(item, key, form, "a marked folder this ledger lacks")
        if _hash(key, skip_mark=True) != mark.get("hash"):
            return self._occupant(item, key, form, "a placed copy that has been modified")
        return self._current_copy(item, key, source, mark, form, note)

    def _file_copy(self, item, key, source, entry, form, note) -> Step:
        if entry is None or entry.get("form") != "copy":
            return self._occupant(item, key, form, "a file the placer did not place")
        if _hash(key) != entry.get("hash"):
            return self._occupant(item, key, form, "a placed copy that has been modified")
        return self._current_copy(item, key, source, entry, form, note)

    def _current_copy(self, item, key, source, mark, form, note) -> Step:
        if form != "copy":
            return self._step(item, key, "update", None, f"{item.name}: now a {form}",
                              form=form, note=note)
        if bool(mark.get("held")) != bool(item.held):
            return self._step(item, key, "update", None, f"{item.name}: held changed",
                              form=form, note=note)
        if mark.get("source") != source or self.source_hash != mark.get("source_hash"):
            return self._step(item, key, "update", None, f"{item.name}: the source changed",
                              form=form, note=note)
        return self._step(item, key, "keep", None, f"{item.name} is current", form=form)

    def _tracked(self, key: str) -> bool:
        """Whether git tracks `key` or anything under it: one `git ls-files` per folder."""
        folder, base = os.path.split(key)
        if folder not in self.git:
            self.git[folder] = _git_files(folder)
        files = self.git[folder]
        if not files:
            return False
        return base in files or any(path.startswith(base + "/") for path in files)

    def removals(self) -> list[Step]:
        steps = []
        for key, entry in self.placed.items():
            if key in self.named:
                continue
            label = (entry.get("harness"), entry.get("kind"), entry.get("name"))
            if label in self.unrouted:
                continue
            item = _entry_item(key, entry)
            steps.append(self._step(item, key, "remove", None,
                                    f"{item.name} is no longer wanted at {key}",
                                    form=entry.get("form", "link")))
        return steps


def _held_by_another(ledger: dict, owner: str, key: str) -> bool:
    """Whether an owner other than `owner` places `key` now: while one does, a hand-over
    of `key` by `owner` stands, even when it was handed on again since."""
    return any(name != owner and isinstance(record, dict)
               and key in record.get("placed", {}) for name, record in ledger.items())


def _entry_item(key: str, entry: dict) -> Item:
    return Item(Path(entry.get("source", key)), entry.get("harness", ""),
                entry.get("name", os.path.basename(key)), entry.get("kind", "skill"),
                dest=Path(key), held=bool(entry.get("held")))


def _git_files(folder: str) -> frozenset | None:
    """The paths git tracks under `folder`, relative to it, or None outside a work tree
    or without git."""
    import shutil
    import subprocess

    from . import proc

    if not os.path.isdir(folder) or shutil.which("git") is None:
        return None
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    try:
        inside = proc.run(["git", "-C", folder, "rev-parse", "--is-inside-work-tree"],
                          timeout=_GIT_SECONDS, env=env)
        if inside.returncode != 0 or inside.stdout.strip() != b"true":
            return None
        listed = proc.run(["git", "-C", folder, "ls-files", "-z"], timeout=_GIT_SECONDS,
                          env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listed.returncode != 0:
        return None
    return frozenset(p.decode("utf-8", "surrogateescape")
                     for p in listed.stdout.split(b"\0") if p)


# -- the disk -----------------------------------------------------------------------------

def _is_junction(path: str) -> bool:
    return os.path.isjunction(path)


def _is_link(path: str) -> bool:
    return os.path.islink(path) or _is_junction(path)


def _link_points(path: str) -> str:
    """Where the link or junction at `path` points, as an absolute path."""
    text = os.readlink(path)
    if text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normpath(os.path.join(os.path.dirname(path), text))


def _same(a: str, b: str) -> bool:
    return os.path.normpath(a) == os.path.normpath(b) \
        or os.path.realpath(a) == os.path.realpath(b)


def _link_text(item: Item, source: str, key: str) -> str:
    """What a link of `item` at `key` says: relative for a held item, so it survives the
    project tree moving; else the absolute source."""
    if item.held:
        return os.path.relpath(os.path.realpath(source), os.path.dirname(key))
    return source


def _symlink_allowed(folder: str) -> bool:
    """Whether this account can make a symlink in `folder` (or its nearest existing
    parent): makes `.moxi-probe-<hex>` and removes it."""
    import secrets

    while folder and not os.path.isdir(folder):
        parent = os.path.dirname(folder)
        if parent == folder:
            return False
        folder = parent
    probe = os.path.join(folder, f".moxi-probe-{secrets.token_hex(4)}")
    try:
        os.symlink(probe + "-target", probe)
    except OSError:
        return False
    try:
        os.unlink(probe)
    except OSError:
        pass
    return True


def _read_mark(folder: str) -> dict | None:
    try:
        with open(os.path.join(folder, MARK), "rb") as handle:
            mark = json.loads(handle.read())
    except FileNotFoundError:
        return None
    except (ValueError, IsADirectoryError, NotADirectoryError):
        return None
    return mark if isinstance(mark, dict) else None


def _hash(path: str, *, skip_mark: bool = False) -> str:
    """SHA-256 over a file's bytes, or over a folder's sorted relative paths and file
    bytes (a symlink inside counts by its text). `skip_mark` leaves out a copy's mark."""
    import hashlib

    digest = hashlib.sha256()
    if not os.path.isdir(path) or os.path.islink(path):
        digest.update(b"F\0")
        _feed(digest, path)
        return digest.hexdigest()
    for rel, kind, full in sorted(_entries(path)):
        if skip_mark and rel == MARK:
            continue
        digest.update(kind.encode() + b"\0" + rel.encode("utf-8", "surrogateescape") + b"\0")
        if kind == "F":
            _feed(digest, full)
        elif kind == "L":
            digest.update(os.readlink(full).encode("utf-8", "surrogateescape") + b"\0")
    return digest.hexdigest()


def _feed(digest, path: str) -> None:
    with open(path, "rb") as handle:
        size = 0
        while chunk := handle.read(_BLOCK):
            digest.update(chunk)
            size += len(chunk)
    digest.update(b"\0" + str(size).encode() + b"\0")


def _entries(root: str) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []

    def walk(folder: str, prefix: str) -> None:
        with os.scandir(folder) as found:
            for entry in found:
                rel = prefix + entry.name
                if entry.is_symlink() or _is_junction(entry.path):
                    out.append((rel, "L", entry.path))
                elif entry.is_dir():
                    out.append((rel, "D", entry.path))
                    walk(entry.path, rel + "/")
                else:
                    out.append((rel, "F", entry.path))

    walk(root, "")
    return out


# -- the ledger ---------------------------------------------------------------------------

def _ledger_path() -> Path:
    from . import paths

    return paths.core().state_dir() / LEDGER


def _read_ledger() -> dict:
    path = _ledger_path()
    try:
        data = json.loads(path.read_bytes())
    except FileNotFoundError:
        return {}
    except ValueError as exc:
        raise ValueError(f"{path} does not read as JSON ({exc}); move it aside, and the "
                         "placer starts a new ledger") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object; move it aside, and the placer "
                         "starts a new ledger")
    for record in data.values():
        if isinstance(record, dict):
            record.setdefault("placed", {})
            record.setdefault("handed", {})
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def _write_ledger(ledger: dict) -> None:
    from . import files

    kept = {owner: record for owner, record in sorted(ledger.items())
            if record.get("placed") or record.get("handed")}
    files.atomic_write(_ledger_path(), json.dumps(kept, indent=1, sort_keys=True) + "\n")


def _record(ledger: dict, owner: str) -> dict:
    return ledger.setdefault(owner, {"placed": {}, "handed": {}})


#: The folders an installer puts packages in, which an older install of a package lay in.
_PACKAGE_HOMES = ("site-packages", "dist-packages")


def from_package(found, source: str | os.PathLike) -> bool:
    """Whether `source`, a placement's recorded source, lies in module `found`'s own
    installed package: the placements `moxi install` makes for its `SKILLS`.

    It does when the source is under the folder the module's top-level package is
    installed in now (the link's own path, or that path resolved), or under a folder of
    the same name directly inside a `site-packages` or `dist-packages` folder, where an
    older install of the package lay before an upgrade moved it. A project folder that
    happens to carry the package's name is neither, so a project's own skills and
    placements handed over from another module never count."""
    package = getattr(found, "package", None) if found is not None else None
    if not package or not source:
        return False
    top = package.split(".")[0]
    source = os.path.abspath(os.fspath(source))
    here = _package_folder(top)
    if here is not None:
        for path in {source, os.path.realpath(source)}:
            if _inside(path, here) or _inside(path, os.path.realpath(here)):
                return True
    parts = Path(source).parts
    return any(parts[i] == top and parts[i - 1] in _PACKAGE_HOMES and i + 1 < len(parts)
               for i in range(1, len(parts)))


def _package_folder(top: str) -> str | None:
    """The folder top-level package `top` is installed in, or None (not importable, or not
    a folder on disk)."""
    import importlib.resources

    try:
        folder = importlib.resources.files(top)
    except (ImportError, TypeError, ValueError):
        return None
    return os.path.abspath(folder) if isinstance(folder, Path) else None


def _core_lock():
    from . import files, paths

    return files.lock(paths.core_lock(), nested=True)


# -- apply --------------------------------------------------------------------------------

def apply(steps: Sequence[Step], *, replace: bool = False,
          say: Callable[[str], object] = print) -> list[Placed]:
    """Carry a plan out, in order, under the core lock. Returns the placements the plan
    leaves in place (each `add`, `update` and `keep`, and each refusal `replace` placed
    over), read back after.

    A `remove` step removes the placement as `remove` does and drops its ledger entry. A
    step that places a target the owner handed over is skipped while any other owner
    places it, and otherwise drops that lapsed hand-over. A `refuse` and a
    `skip` say their line and change nothing, except that `replace=True` moves the
    occupant of a `refuse` with cause `occupant` or `tracked` to
    `DATA/moxi/displaced/<UTC stamp>/<harness>/<name>` and places the item. An `OSError`
    on one step says one line and leaves that step's placement as it was; the rest go on.
    """
    import time

    steps = list(steps)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    done: list[Placed] = []
    notes: list[str] = []
    with _core_lock():
        ledger = _read_ledger()
        written = json.dumps(ledger, sort_keys=True)
        for step in steps:
            places = step.action in ("add", "update") or (
                step.action == "refuse" and replace and step.cause in _OCCUPANTS)
            if places and step.note and step.note not in notes:
                notes.append(step.note)
                say(step.note)
            try:
                placed = _apply_one(step, ledger, replace, stamp, say)
            except OSError as exc:
                say(f"{step.item.name} for {step.item.harness}: could not "
                    f"{step.action} {step.target}: {exc}")
                continue
            finally:
                now = json.dumps(ledger, sort_keys=True)
                if now != written:
                    _write_ledger(ledger)
                    written = now
            if placed is not None:
                done.append(placed)
    return done


def _apply_one(step: Step, ledger: dict, replace: bool, stamp: str, say) -> Placed | None:
    """One step. A step that places a target this owner handed over first checks the
    hand-over: while any other owner places the target the step is skipped, and once
    the target is placed the lapsed `handed` entry is dropped."""
    if step.action == "skip":
        say(step.reason)
        return None
    key = str(step.target)
    handed = ledger.get(step.owner, {}).get("handed", {})
    if step.target is not None and key in handed and step.action != "remove":
        if _held_by_another(ledger, step.owner, key):
            to = handed[key].get("to", "another owner")
            say(f"{step.item.name} was handed to {to}; skipped")
            return None
    placed = _apply_step(step, ledger, replace, stamp, say)
    if placed is not None:
        ledger.get(step.owner, {}).get("handed", {}).pop(key, None)
    return placed


def _apply_step(step: Step, ledger: dict, replace: bool, stamp: str, say) -> Placed | None:
    item, key, owner = step.item, step.target, step.owner
    if step.action == "refuse":
        if not (replace and step.cause in _OCCUPANTS and step.occupant is not None):
            say(f"{item.name} for {item.harness} not placed: {step.reason}")
            return None
        moved = _displace(str(step.occupant), stamp, item.harness, item.name)
        say(f"moved {step.occupant} to {moved}")
        _record(ledger, owner)["placed"].pop(str(key), None)
        return _place(step, ledger)
    key = str(key)
    if step.action == "remove":
        entry = _record(ledger, owner)["placed"].get(key)
        if entry is not None:
            _unplace(owner, key, entry, stamp, say)
            _record(ledger, owner)["placed"].pop(key, None)
        return None
    if step.action == "keep":
        mine = _record(ledger, owner)["placed"]
        if key not in mine:
            mine[key] = _entry(step, in_place=step.reason == HELD_IN_PLACE)
        return _placed(owner, key, mine[key])
    if step.action == "update":
        # Only a placement plan found to be this owner's, and unmodified, is updated.
        _remove_path(key)
        _record(ledger, owner)["placed"].pop(key, None)
    return _place(step, ledger)


def _entry(step: Step, *, in_place: bool = False) -> dict:
    item = step.item
    entry = {"kind": item.kind, "harness": item.harness, "name": item.name,
             "form": IN_PLACE if in_place else step.form,
             "source": os.path.abspath(os.fspath(item.source))}
    if item.dest is not None:
        entry["dest"] = True
    if item.held:
        entry["held"] = True
    key = str(step.target)
    if not in_place and step.form == "copy" and os.path.lexists(key) and not os.path.isdir(key):
        entry["source_hash"] = _hash(entry["source"])
        entry["hash"] = _hash(key)
    return entry


def _place(step: Step, ledger: dict) -> Placed:
    """Make the placement of an `add` (or the second half of an `update`) and record it."""
    item, key = step.item, str(step.target)
    source = os.path.abspath(os.fspath(item.source))
    parent = os.path.dirname(key)
    os.makedirs(parent, exist_ok=True)
    if os.path.lexists(key):
        raise FileExistsError(f"{key} appeared after the plan")
    folder = os.path.isdir(source)
    if step.form == "link":
        os.symlink(_link_text(item, source, key), key, target_is_directory=folder)
        entry = _entry(step)
    elif step.form == "junction":
        import _winapi

        _winapi.CreateJunction(source, key)
        entry = _entry(step)
    else:
        _copy(item, source, key, step.owner)
        entry = _entry(step)
    _record(ledger, step.owner)["placed"][key] = entry
    return _placed(step.owner, key, entry)


def _copy(item: Item, source: str, key: str, owner: str) -> None:
    import shutil

    tmp = os.path.join(os.path.dirname(key), f".{os.path.basename(key)}.moxi-tmp")
    if os.path.lexists(tmp):
        _remove_path(tmp)
    try:
        if os.path.isdir(source):
            source_hash = _hash(source)
            shutil.copytree(source, tmp, symlinks=True)
            mark = {"owner": owner, "source": source, "source_hash": source_hash,
                    "hash": _hash(tmp, skip_mark=True)}
            if item.held:
                mark["held"] = True
            with open(os.path.join(tmp, MARK), "x", encoding="utf-8") as handle:
                handle.write(json.dumps(mark, indent=1, sort_keys=True) + "\n")
        else:
            shutil.copy2(source, tmp)
        if os.path.lexists(key):
            raise FileExistsError(f"{key} appeared while it was copied")
        os.rename(tmp, key)
    except BaseException:
        if os.path.lexists(tmp):
            _remove_path(tmp)
        raise


def _remove_path(path: str) -> None:
    """Remove a link, junction, folder or file. A link or junction is removed itself and
    never followed: `shutil.rmtree` is only called on a real folder."""
    import shutil

    if _is_junction(path):
        os.rmdir(path)
    elif os.path.islink(path):
        try:
            os.unlink(path)
        except OSError:
            if not _WINDOWS:
                raise
            os.rmdir(path)   # a directory symlink on Windows
    elif os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.lexists(path):
        os.unlink(path)


def _displace(path: str, stamp: str, harness: str, name: str) -> Path:
    """Move `path` to `DATA/moxi/displaced/<stamp>/<harness>/<name>` (a free name)."""
    import shutil

    from . import paths

    base = paths.core().data_dir() / "displaced" / stamp / (harness or "dest") / name
    dest, n = base, 1
    while os.path.lexists(dest):
        n += 1
        dest = base.with_name(f"{name}-{n}")
    os.makedirs(dest.parent, exist_ok=True)
    shutil.move(path, dest)
    return dest


def _unplace(owner: str, key: str, entry: dict, stamp: str, say) -> str:
    """Take one placement off the disk as `remove` does; return its state before."""
    state = _state(owner, key, entry)
    if entry.get("form") == IN_PLACE or state == "missing":
        return state
    if not _ours(owner, key, entry):
        say(f"left {key}: it is no longer what the placer put there")
        return state
    if state == "modified" and not _is_link(key):
        moved = _displace(key, stamp, entry.get("harness", ""),
                          entry.get("name", os.path.basename(key)))
        say(f"{key} was modified; moved it to {moved}")
        return state
    _remove_path(key)
    return state


def _ours(owner: str, key: str, entry: dict) -> bool:
    """Whether the path at `key` is still the placement `entry` records for `owner`."""
    if _is_link(key):
        return entry.get("form") in ("link", "junction") \
            and _same(_link_points(key), entry.get("source", ""))
    if os.path.isdir(key):
        mark = _read_mark(key)
        return mark is not None and mark.get("owner") == owner
    return entry.get("form") == "copy" and "hash" in entry


# -- status, remove, adopt ----------------------------------------------------------------

def _state(owner: str, key: str, entry: dict) -> str:
    source = entry.get("source", "")
    try:
        if entry.get("form") == IN_PLACE:
            if not os.path.lexists(key):
                return "missing"
            return "ok" if os.path.realpath(key) == os.path.realpath(source) else "stale"
        if not os.path.lexists(key):
            return "missing"
        if _is_link(key):
            pointed = _link_points(key)
            if not _same(pointed, source):
                return "modified"
            return "ok" if os.path.exists(pointed) else "stale"
        if os.path.isdir(key):
            mark = _read_mark(key)
            if mark is None or mark.get("owner") != owner \
                    or _hash(key, skip_mark=True) != mark.get("hash"):
                return "modified"
            known = mark.get("source_hash")
        else:
            if entry.get("form") != "copy" or _hash(key) != entry.get("hash"):
                return "modified"
            known = entry.get("source_hash")
        if not os.path.exists(source) or _hash(source) != known:
            return "stale"
        return "ok"
    except OSError:
        return "stale"


def _placed(owner: str, key: str, entry: dict, state: str | None = None) -> Placed:
    return Placed(owner, entry.get("kind", "skill"), entry.get("harness", ""),
                  entry.get("name", os.path.basename(key)), Path(key),
                  entry.get("form", "link"),
                  _state(owner, key, entry) if state is None else state)


def status(owner: str) -> list[Placed]:
    """Each placement of `owner` read against the disk, by target: `missing` (gone),
    `stale` (a link's target gone, or the source missing or changed), `modified` (a
    copy changed, or a link pointed elsewhere), else `ok`. Takes no lock."""
    placed = _read_ledger().get(owner, {}).get("placed", {})
    return [_placed(owner, key, placed[key]) for key in sorted(placed)]


def remove(owner: str, *, say: Callable[[str], object] = print) -> list[Placed]:
    """Remove every placement of `owner`, under the core lock, and drop its entries.

    Links and junctions are unlinked and unmodified copies deleted; a modified copy is
    moved to `DATA/moxi/displaced/` with a line, so a user's edits are never lost. A
    target held in place, or a path that is no longer what the placer put there, is
    left with its entry dropped. What the owner handed over (`adopt`) is left. Returns
    the placements removed, each with the state it had.
    """
    import time

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    gone: list[Placed] = []
    with _core_lock():
        ledger = _read_ledger()
        placed = _record(ledger, owner)["placed"]
        try:
            for key in sorted(placed):
                entry = placed[key]
                try:
                    state = _unplace(owner, key, entry, stamp, say)
                except OSError as exc:
                    say(f"could not remove {key}: {exc}")
                    continue
                del placed[key]
                gone.append(_placed(owner, key, entry, state))
        finally:
            _write_ledger(ledger)
    return gone


def adopt(from_owner: str, to_owner: str, placements: Iterable[Placed], *,
          seeded: bool = False, say: Callable[[str], object] = print) -> list[Placed]:
    """Hand placements from `from_owner` to `to_owner`, by target, under the core lock.

    Each placement's ledger entry moves to `to_owner`, a copied folder's mark is
    rewritten to name `to_owner`, and `from_owner`'s `handed` map gains `{target: {kind,
    harness, name, "to": to_owner}}`, so its later plans skip the target while another
    owner's ledger places it (`to_owner`, or an owner it handed the target on to), and
    its `remove` leaves it. Once no owner places the target (it was removed, or handed
    back and then removed), the entry has lapsed: `from_owner`'s plan treats the item as
    any other, and `apply` drops the entry when it places the target. `to_owner`'s
    `handed` map drops the target, so handing an item back gives it back whole. Nothing
    on disk moves. A placement that is not `from_owner`'s is refused with a line and the
    rest go on. Returns the placements now `to_owner`'s.

    `seeded=True` marks each new `handed` entry `"seeded": true`: the placement was made
    as `from_owner` only so that `to_owner` could take it, not by `from_owner`'s own
    install. A seeded entry skips and lapses as any other, but `moxi install` does not
    count it as the module's own placement when it decides whether to ask its skills
    question. An existing `handed` entry for the target is replaced, seeded or not as
    this call says. A seeded hand-over is never handed back: a target `to_owner` handed
    over seeded is refused with a line, "<name> was seeded; <its taker> removes it", and
    the rest go on: its taker removes it instead, so a placement made that way never
    becomes the module's own.
    """
    from . import files

    if from_owner == to_owner:
        raise ValueError("adopt hands placements between two owners, not to the same one")
    moved: list[Placed] = []
    with _core_lock():
        ledger = _read_ledger()
        giver, taker = _record(ledger, from_owner), _record(ledger, to_owner)
        try:
            for placement in placements:
                key = target_key(placement.path)
                entry = giver["placed"].get(key)
                if entry is None:
                    say(f"{placement.name} at {key} is not {from_owner}'s; not handed to "
                        f"{to_owner}")
                    continue
                back = taker["handed"].get(key)
                if isinstance(back, dict) and back.get("seeded"):
                    say(f"{placement.name} was seeded; {back.get('to', from_owner)} "
                        f"removes it")
                    continue
                if entry.get("form") != IN_PLACE and os.path.isdir(key) \
                        and not _is_link(key):
                    mark = _read_mark(key)
                    if mark is not None and mark.get("owner") == from_owner:
                        mark["owner"] = to_owner
                        files.atomic_write(os.path.join(key, MARK),
                                           json.dumps(mark, indent=1, sort_keys=True) + "\n",
                                           mode=0o644)
                del giver["placed"][key]
                taker["placed"][key] = entry
                taker["handed"].pop(key, None)
                handed = {"kind": entry.get("kind", "skill"),
                          "harness": entry.get("harness", ""),
                          "name": entry.get("name", os.path.basename(key)),
                          "to": to_owner}
                if seeded:
                    handed["seeded"] = True
                giver["handed"][key] = handed
                moved.append(_placed(to_owner, key, entry))
        finally:
            _write_ledger(ledger)
    return moved


__all__ = ["FORMS", "SETTINGS", "KINDS", "ACTIONS", "CAUSES", "STATES", "MARK", "LEDGER",
           "IN_PLACE", "HELD_IN_PLACE", "FILE_NOTE", "FOLDER_NOTE", "Item", "Step", "Placed", "Harness",
           "harnesses", "chosen", "target_key", "from_package", "plan", "apply", "status",
           "remove", "adopt"]
