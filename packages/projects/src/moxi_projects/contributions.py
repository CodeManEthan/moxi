"""What other modules contribute to projects: kinds, field defaults and remove refusals.

A module that depends on projects declares one entry in the entry-point group
`moxi.projects`, named by the module and pointing at a small module of its own:

    [project.entry-points."moxi.projects"]
    notes = "moxi_notes.projects_link"

That module declares `INTERFACE`, the version of this contract it implements; this code
reads versions 1 and 2. Interface 1 offers either or both of:

- `KINDS`: a mapping of kind name to `(doc, check)`. `check(fields)` gets the proposed row
  as a dict (`key`, `root`, `kind`, `lead`, `notes_root`) and returns a sentence refusing
  it, or None.
- `refuse_remove(project)`: gets the `moxi_projects.api.Project` about to be removed and
  returns one sentence naming what still holds it and the command that frees it, or None.

Interface 2 is interface 1 plus, each optional:

- `prepare(fields)`: gets the fields `add` was given by the user (always `key` and `kind`,
  and whichever of `root`, `lead` and `notes_root` the user passed; never another
  module's defaults) and returns a dict of defaults, or None. It may default only `root`
  and `notes_root`; each value is a string, an absolute path, or None for no default.
- a `KINDS` value of three, `(doc, check, mint)`, where `mint` says how `add --create`
  makes a root of that kind: `"git"` (a directory, then `git init`), `"dir"` (a directory
  only) or `"refuse"`. A value of two means `"git"`. Under interface 1 a value of three is
  the wrong length.

Every contribution function does local reads only: no subprocess, no network, no writes.

The group is read afresh by every `load()`, so a long-lived process sees a module
installed after it started. The read API's `list_projects`, `get` and `for_path` never
read it.

A broken contribution blocks only the acts it bears on, and every block names the module
and the fix (repair it or uninstall it):

- An entry that won't load (its import fails, its `INTERFACE` is not 1 or 2, its `KINDS`
  is not a mapping, or a function it offers is not callable) blocks `remove`, since it
  can't say whether it holds the project; `add`, since it can't say whether its
  `prepare` would have filled a field or its kind is the one asked for; and `set` of a
  kind no loaded entry declares.
- A kind declared twice, by two modules or by one module over a built-in kind, or
  declared with a value of the wrong shape, blocks `add` and `set` of that kind alone. A
  blocked kind is not among the kinds found; it is named among the broken ones.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable, Mapping

import moxi.modules

from . import api
from .api import DEFAULT_KIND, Kind, Kinds, Project
from .errors import Broken, Refused, Usage
from .rules import check_agent, check_key, check_kind_name, check_root

#: The entry-point group other modules contribute through.
GROUP = "moxi.projects"
#: The versions of the contract this code reads.
INTERFACES = frozenset({1, 2})
#: How `add --create` may make a root.
MINTS = ("git", "dir", "refuse")
#: The fields a `prepare` may default.
DEFAULTABLE = ("root", "notes_root")
#: The kinds projects itself declares: labels, with no rule on their roots.
BUILT_IN = {
    "project": "a code project: a directory, usually a git repository",
    "agent": "an agent's home directory",
}
#: The module name built-in kinds carry.
OWN = "projects"


def _fix(module: str) -> str:
    return f"repair {module} or uninstall it (`moxi uninstall {module}`)"


def _describe(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()
    return f"{type(exc).__name__}: {text[0]}" if text else type(exc).__name__


class Declared:
    """One kind as declared: the public `Kind`, and its `check` (None for a built-in)."""

    __slots__ = ("kind", "check")

    def __init__(self, kind: Kind, check: Callable[[dict], object] | None):
        self.kind = kind
        self.check = check

    @property
    def name(self) -> str:
        return self.kind.name

    @property
    def module(self) -> str:
        return self.kind.module

    def check_row(self, row: Mapping[str, object],
                  supplied: Mapping[str, tuple[str, str]] | None = None) -> None:
        """Run the kind's check on `row`, the row about to be written. A refusal raises
        `Refused` (exit 3), saying which module supplied any defaulted field in
        `supplied` (`{field: (value, module)}`); a check that raises, or answers with
        something other than a sentence or None, raises `Broken` (exit 1)."""
        if self.check is None:
            return
        try:
            said = self.check(dict(row))
        except Exception as exc:  # noqa: BLE001 - a raising check is the module's fault
            raise Broken(self.module, f"{self.module}'s check of kind {self.name!r} "
                                      f"raised {_describe(exc)}", _fix(self.module)) from exc
        if said is None:
            return
        if not isinstance(said, str) or not said.strip():
            raise Broken(self.module, f"{self.module}'s check of kind {self.name!r} answered "
                                      f"{said!r}, not a sentence or None", _fix(self.module))
        by = "".join(f" ({field} supplied by {module})"
                     for field, (_value, module) in sorted((supplied or {}).items()))
        raise Refused(f"kind {self.name!r} ({self.module}) refuses the row: "
                      f"{said.strip()}{by}",
                      "change the fields it names" + (", or pass them yourself" if by else ""))


class Contribution:
    """One entry of the group that loaded: its module, interface and offerings."""

    __slots__ = ("module", "name", "interface", "kinds", "bad_kinds", "refuse_remove",
                 "prepare")

    def __init__(self, module: str, name: str, interface: int,
                 kinds: dict[str, Declared], bad_kinds: dict[str, str],
                 refuse_remove: Callable | None, prepare: Callable | None):
        self.module = module
        self.name = name
        self.interface = interface
        self.kinds = kinds
        self.bad_kinds = bad_kinds
        self.refuse_remove = refuse_remove
        self.prepare = prepare


def _kind_value(module: str, name: str, value: object, interface: int
                ) -> tuple[Declared | None, str | None]:
    """A `KINDS` value read as a `Declared`, or the reason it has the wrong shape."""
    try:
        check_kind_name(name)
    except Refused as exc:
        return None, exc.reason
    lengths = (2,) if interface == 1 else (2, 3)
    if not isinstance(value, tuple | list) or len(value) not in lengths:
        want = "(doc, check)" if interface == 1 else "(doc, check) or (doc, check, mint)"
        return None, (f"kind {name!r}: its value is {value!r}; interface {interface} takes "
                      f"{want}")
    doc, check, *rest = value
    mint = rest[0] if rest else "git"
    if not isinstance(doc, str):
        return None, f"kind {name!r}: its doc is {doc!r}, not a string"
    if not callable(check):
        return None, f"kind {name!r}: its check is {check!r}, not callable"
    if mint not in MINTS:
        return None, (f"kind {name!r}: its mint is {mint!r}, not one of "
                      f"{', '.join(MINTS)}")
    return Declared(Kind(name=name, doc=doc, module=module, mint=mint), check), None


def _read(ext: moxi.modules.Extension) -> Contribution:
    """Load one entry, or raise `ValueError` with the reason it won't load."""
    try:
        target = ext.load()
    except moxi.modules.ExtensionError as exc:
        raise ValueError(exc.reason) from exc
    interface = getattr(target, "INTERFACE", None)
    if isinstance(interface, bool) or interface not in INTERFACES:
        raise ValueError(f"its INTERFACE is {interface!r}; this version of projects reads "
                         f"{' and '.join(str(i) for i in sorted(INTERFACES))}")
    declared = getattr(target, "KINDS", None)
    if declared is None:
        declared = {}
    if not isinstance(declared, Mapping):
        raise ValueError(f"its KINDS is {type(declared).__name__}, not a mapping")
    refuse_remove = getattr(target, "refuse_remove", None)
    if refuse_remove is not None and not callable(refuse_remove):
        raise ValueError("its refuse_remove is not callable")
    prepare = getattr(target, "prepare", None) if interface >= 2 else None
    if prepare is not None and not callable(prepare):
        raise ValueError("its prepare is not callable")
    kinds: dict[str, Declared] = {}
    bad: dict[str, str] = {}
    for name, value in declared.items():
        if not isinstance(name, str):
            raise ValueError(f"its KINDS holds the name {name!r}, not a string")
        good, reason = _kind_value(ext.module, name, value, interface)
        if good is not None:
            kinds[name] = good
        else:
            bad[name] = reason
    return Contribution(ext.module, ext.name, interface, kinds, bad, refuse_remove, prepare)


class Contributions:
    """What the group held at one reading: the entries that loaded, and those that won't
    as `(module, reason)` pairs."""

    def __init__(self, loaded: list[Contribution], unloadable: list[tuple[str, str]]):
        self.loaded = tuple(loaded)
        self.unloadable = tuple(unloadable)
        self._table = self._build()

    def _build(self) -> dict[str, tuple[Declared | None, str | None, tuple[str, ...]]]:
        """Each kind name: `(declared, None, modules)` when it may be used, or
        `(None, reason, modules)` when it is blocked."""
        claims: dict[str, list[tuple[str, Declared | None, str | None]]] = {}
        for name, doc in BUILT_IN.items():
            claims[name] = [(OWN, Declared(Kind(name=name, doc=doc, module=OWN,
                                                 mint="git"), None), None)]
        for c in self.loaded:
            for name, declared in c.kinds.items():
                claims.setdefault(name, []).append((c.module, declared, None))
            for name, reason in c.bad_kinds.items():
                claims.setdefault(name, []).append((c.module, None, reason))
        table = {}
        for name, claimants in claims.items():
            modules = tuple(module for module, _d, _r in claimants)
            if len(claimants) > 1:
                if name in BUILT_IN:
                    others = ", ".join(m for m in modules if m != OWN)
                    reason = (f"kind {name!r} is built in to projects and {others} declares "
                              "it again")
                else:
                    reason = f"kind {name!r} is declared by {', '.join(modules)}"
                table[name] = (None, reason, modules)
                continue
            _module, declared, reason = claimants[0]
            table[name] = (declared, reason, modules)
        return table

    def kinds(self) -> Kinds:
        """The kinds that may be used, by name, and every unloadable entry and blocked
        kind as `(module, reason)` pairs."""
        found = tuple(declared.kind for name, (declared, _r, _m) in sorted(self._table.items())
                      if declared is not None)
        broken = list(self.unloadable)
        for name, (declared, reason, modules) in sorted(self._table.items()):
            if declared is None:
                broken.extend((module, reason) for module in modules if module != OWN)
        return Kinds(found=found, broken=tuple(broken))

    def require_loaded(self, act: str) -> None:
        """`Broken` when any entry won't load: `add` and `remove` stop past it, since it
        could bear on either."""
        if not self.unloadable:
            return
        modules = tuple(dict.fromkeys(module for module, _r in self.unloadable))
        why = "; ".join(f"{module}: {reason}" for module, reason in self.unloadable)
        raise Broken(modules, f"{act} can't go ahead while a contribution to projects "
                              f"won't load ({why})",
                     "; ".join(_fix(m) for m in modules))

    def kind(self, name: str, act: str) -> Declared:
        """The kind `name` for `act` (`add` or `set`), or why it can't be used.

        A blocked kind raises `Broken` (exit 1). An unknown kind raises `Refused` (exit 3)
        when every entry loaded, and `Broken` naming the unloadable modules when some
        entry won't load, since that entry may be its home."""
        entry = self._table.get(name)
        if entry is not None:
            declared, reason, modules = entry
            if declared is not None:
                return declared
            culprits = tuple(m for m in modules if m != OWN)
            fix = ("; ".join(_fix(m) for m in culprits) if len(culprits) == 1
                   else f"uninstall all but one of {', '.join(culprits)}")
            raise Broken(culprits, f"{act} of kind {name!r} is blocked: {reason}", fix)
        if self.unloadable:
            modules = tuple(dict.fromkeys(module for module, _r in self.unloadable))
            raise Broken(modules, f"kind {name!r} is unknown, and {', '.join(modules)} "
                                  "won't load, so it may be the kind's home",
                         "; ".join(_fix(m) for m in modules))
        raise Refused(f"kind {name!r} is unknown",
                      "`moxi projects kinds` lists the kinds; a module may contribute more")

    def defaults(self, fields: Mapping[str, object], *,
                 notes: bool | None = None) -> dict[str, tuple[str, str]]:
        """The defaults the contributions supply for an `add` given `fields`, as
        `{field: (value, module)}`.

        Every loaded interface 2 entry's `prepare` is called with a copy of the user's
        `fields`. A default fills only a field the user left unset: a default for a field
        in `fields` is neither merged nor compared. A value of None is no default. A
        default may name only `root` and `notes_root`, each an absolute path, given as a
        string; it is returned normalized. A `notes_root` default is dropped while notes
        is not installed (`notes` overrides that test). Any other name, a value that isn't
        a string, a relative path, or a `prepare` that raises or answers other than a
        dict or None is `Broken` naming the module. Two modules defaulting one field to
        different values is `Broken` naming both; the user settles it by passing the
        field.

        Refuses first, as `add` does, while any entry won't load.
        """
        self.require_loaded("add")
        proposals: dict[str, list[tuple[str, str]]] = {}
        for c in self.loaded:
            if c.prepare is None:
                continue
            try:
                said = c.prepare(dict(fields))
            except Exception as exc:  # noqa: BLE001 - a raising prepare is the module's
                raise Broken(c.module, f"{c.module}'s prepare raised {_describe(exc)}",
                             _fix(c.module)) from exc
            if said is None:
                continue
            if not isinstance(said, Mapping):
                raise Broken(c.module, f"{c.module}'s prepare answered "
                                       f"{type(said).__name__}, not a dict or None",
                             _fix(c.module))
            for name, value in said.items():
                if name not in DEFAULTABLE:
                    raise Broken(c.module, f"{c.module}'s prepare defaults {name!r}; a "
                                           "default may name only root and notes_root",
                                 _fix(c.module))
                if value is None:
                    continue
                if not isinstance(value, str):
                    raise Broken(c.module, f"{c.module}'s prepare defaults {name} to "
                                           f"{value!r}, not a string", _fix(c.module))
                if fields.get(name) is not None:
                    continue
                if not os.path.isabs(value) or "\0" in value:
                    raise Broken(c.module, f"{c.module}'s prepare defaults {name} to "
                                           f"{value!r}, not an absolute path",
                                 f"{_fix(c.module)}, or pass --{name.replace('_', '-')} "
                                 "yourself")
                proposals.setdefault(name, []).append((os.path.normpath(value), c.module))
        if notes is None and "notes_root" in proposals:
            notes = notes_present()
        if not notes:
            proposals.pop("notes_root", None)
        out: dict[str, tuple[str, str]] = {}
        for name, said in proposals.items():
            values = {os.path.normcase(value) for value, _m in said}
            if len(values) > 1:
                modules = tuple(dict.fromkeys(module for _v, module in said))
                spelled = "; ".join(f"{module} says {value}" for value, module in said)
                raise Broken(modules, f"the modules disagree on the default {name}: "
                                      f"{spelled}",
                             f"pass --{name.replace('_', '-')} yourself to settle it")
            out[name] = said[0]
        return out

    def remove_refusals(self, project: Project) -> list[tuple[str, str]]:
        """Every contribution's refusal to remove `project`, as `(module, sentence)`
        pairs; empty when none holds it.

        Refuses first, as `remove` does, while any entry won't load. A `refuse_remove`
        that raises, or answers other than a sentence or None, is `Broken` naming the
        module. Ask this outside any write transaction, so a slow contribution never holds
        the store's lock."""
        self.require_loaded("remove")
        out = []
        for c in self.loaded:
            if c.refuse_remove is None:
                continue
            try:
                said = c.refuse_remove(project)
            except Exception as exc:  # noqa: BLE001 - a raising refusal is the module's
                raise Broken(c.module, f"{c.module}'s refuse_remove raised "
                                       f"{_describe(exc)}", _fix(c.module)) from exc
            if said is None:
                continue
            if not isinstance(said, str) or not said.strip():
                raise Broken(c.module, f"{c.module}'s refuse_remove answered {said!r}, not "
                                       "a sentence or None", _fix(c.module))
            out.append((c.module, said.strip()))
        return out


def load() -> Contributions:
    """Read the group afresh and load every entry. Never raises: an entry that won't load
    is listed in `unloadable` with the reason."""
    importlib.invalidate_caches()
    loaded: list[Contribution] = []
    unloadable: list[tuple[str, str]] = []
    for ext in moxi.modules.extensions(GROUP):
        try:
            loaded.append(_read(ext))
        except ValueError as exc:
            unloadable.append((ext.module, f"the entry {ext.name} won't load: {exc}"))
        except Exception as exc:  # noqa: BLE001 - one entry never stops the reading
            unloadable.append((ext.module, f"the entry {ext.name} won't load: "
                                           f"{_describe(exc)}"))
    return Contributions(loaded, unloadable)


def notes_present() -> bool:
    """Whether notes is installed: `notes_root` is written only while it is."""
    return moxi.modules.present("notes")


def _notes_gate(notes: bool | None) -> None:
    if not (notes_present() if notes is None else notes):
        raise Refused("a notes root is stored only while notes is installed",
                      "run `moxi install notes`, or leave the notes root out")


class Planned:
    """An `add` checked and ready to write: `row` (the fields `store.insert` takes),
    `supplied` (`{field: (value, module)}` for each defaulted field) and `kind`, the
    `Declared` kind, whose `kind.mint` says how `--create` makes the root."""

    __slots__ = ("row", "supplied", "kind")

    def __init__(self, row: dict, supplied: dict[str, tuple[str, str]], kind: Declared):
        self.row = row
        self.supplied = supplied
        self.kind = kind

    def insert(self, conn) -> Project:
        """Write the row on `conn`, inside the write sequence (`store.writing`). A
        refusal the store makes (a taken root, say) names the module that supplied any
        defaulted field."""
        from . import store

        try:
            return store.insert(conn, **self.row)
        except Refused as exc:
            if not self.supplied:
                raise
            by = "".join(f" ({field} supplied by {module})"
                         for field, (_value, module) in sorted(self.supplied.items()))
            raise Refused(exc.reason + by, exc.fix) from exc


def plan_add(key: str, *, kind: str = DEFAULT_KIND, root: str | None = None,
             lead: str | None = None, notes_root: str | None = None,
             contributions: Contributions | None = None,
             notes: bool | None = None) -> Planned:
    """Check an `add` against every rule before anything is made or written.

    In order: the key's rules; a typed `notes_root` only while notes is installed (`notes`
    overrides that test); every entry loaded (else `Broken`); the defaults the
    contributions supply for fields the user left unset, merged; a root, typed or
    defaulted (else `Usage`, exit 2); the root, notes root and lead rules; the kind,
    usable (`Contributions.kind`); then the kind's check on the merged row. Typed paths
    must already be absolute (`rules.absolutize` makes a command line's so).

    Whether the key or root is taken is checked by the write itself (`Planned.insert`).
    """
    key = check_key(key)
    kind = check_kind_name(kind)
    if notes_root is not None:
        _notes_gate(notes)
    contributions = contributions if contributions is not None else load()
    contributions.require_loaded("add")
    fields: dict[str, object] = {"key": key, "kind": kind}
    if root is not None:
        fields["root"] = check_root(root)
    if lead is not None:
        fields["lead"] = lead
    if notes_root is not None:
        fields["notes_root"] = check_root(notes_root, "notes_root")
    supplied = contributions.defaults(fields, notes=notes)
    row = {"key": key, "root": fields.get("root"), "kind": kind,
           "lead": None if lead is None else check_agent(lead, "lead"),
           "notes_root": fields.get("notes_root")}
    for name, (value, _module) in supplied.items():
        row[name] = value
    if row["root"] is None:
        raise Usage(f"project {key!r} needs a root, and no installed module supplies one "
                    f"for kind {kind!r}", "pass --root DIR")
    declared = contributions.kind(kind, "add")
    declared.check_row(row, supplied)
    return Planned(row, supplied, declared)


def plan_set(key: str, *, contributions: Contributions | None = None,
             notes: bool | None = None, **changes) -> Declared | None:
    """Check a `set` of `changes` on project `key` before it is written; return the kind
    whose check passed, or None when none ran.

    `changes` holds the fields named (`root`, `kind`, `lead`, `notes_root`; None clears
    `lead` and `notes_root`). A `set` never takes defaults. A typed `notes_root` needs
    notes installed. A named kind must be usable (`Contributions.kind`): an unknown kind
    is `Refused` when every entry loaded and `Broken` when one won't; a set of a known kind
    goes ahead past an entry that won't load. The row's kind, named or kept, has its check
    run on the row as it would stand; a kept kind that no loaded module declares any more
    stays as a label, with no check. Raises `NotFound` for an unknown key.
    """
    unknown = set(changes) - {"root", "kind", "lead", "notes_root"}
    if unknown:
        raise Usage(f"set can't change {', '.join(sorted(unknown))}",
                    "it changes root, kind, lead and notes_root")
    if changes.get("notes_root") is not None:
        _notes_gate(notes)
    current = api.get(key)
    contributions = contributions if contributions is not None else load()
    row = {"key": current.key, "root": current.root, "kind": current.kind,
           "lead": current.lead, "notes_root": current.notes_root}
    for name, value in changes.items():
        if value is None:
            row[name] = None
        elif name in ("root", "notes_root"):
            row[name] = check_root(value, name)
        elif name == "lead":
            row[name] = check_agent(value, "lead")
        else:
            row[name] = check_kind_name(value)
    if "kind" in changes:
        declared = contributions.kind(row["kind"], "set")
    else:
        try:
            declared = contributions.kind(row["kind"], "set")
        except (Refused, Broken):
            declared = None
    if declared is not None:
        declared.check_row(row)
    return declared


__all__ = ["GROUP", "INTERFACES", "MINTS", "DEFAULTABLE", "BUILT_IN", "OWN", "Declared",
           "Contribution", "Contributions", "Planned", "load", "notes_present", "plan_add",
           "plan_set"]
