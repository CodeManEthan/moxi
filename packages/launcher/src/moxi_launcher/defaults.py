"""The launch defaults, `[defaults]` of the user's `presets.toml`, as a table `moxi config`
shows and changes: `moxi config launcher show` lists its rows as `defaults.<row>`, and
`moxi config set launcher defaults.<row> VALUE` (or `unset`) writes one key. The
announcement's `CONFIG_TABLES` names `TABLE`, the hook `moxi.config` describes. The
launcher alone reads, checks and writes the table; the Settings page edits it through
`moxi config`.

The rows `keys` lists:

- **The rows a launch here takes.** Every settable row of the launcher's catalog and of
  each harness's that is offered on this machine (`catalog.Row.offered`: its modules
  present, its condition holding), apart from `takes_policy_directors`, which `[defaults]`
  refuses (`presets.PER_SEAT`). A name both harnesses have is one row, as `[defaults]`
  holds one key for both, and its line gives each harness's doc. An `enum` row offers the
  values whose `when` needs no missing module and no failed condition; a value that needs
  only the launch's target is offered. Each row sits under its catalog group, or `start`
  for one with none.
- **The keys that need a fix.** Every other key `[defaults]` holds that launches refuse or
  leave out (an owned row, `takes_policy_directors`, an unknown key, a retired row) is
  listed first, under `needs a fix`, with `clear_only`: it can only be removed.
- **Not listed:** a key the file holds for a row this machine doesn't offer. Every write
  changes one key, so it is kept as it is; the page can't clear it, and a hand edit can.

Each row carries the launcher's words for what its value meets: `refused` for what
refuses every launch (the key's own check, the place rule between two keys, a value whose
`when` this machine can't meet), `warning` for what some launches refuse or leave out
(`presets.defaults_problems`' warnings). Where a launch holds something else for some
launches, the row's line says so.

A change. `set` reads the text as the row's type and checks it by the row's own check
alone: `presets.defaults_problems` of that one key, `catalog.Row.check` of every row the
name names (so a retired name or value is refused whatever `[defaults]` makes of it), and
the values offered here. Nothing else in the file is read to refuse it, so the order of a
Save's changes never matters; what the value meets is returned as warning lines. `unset`
removes the key and returns the lines every row the table still holds carries. A key
`keys` doesn't list is refused with the row's own words, so a clear of a key `keys` lists
is refused only by a file that can't be read or a held lock.

The write is one `moxi.files.edit_toml` block on `presets.path(env)`, under
`presets.toml.lock`, which reads the file again inside the lock and keeps every other byte:
a changed value keeps its place and trailing comment, a new key goes straight after the
table's last key (or straight under its header when it holds none, so it lands above a
comment block that follows the table), a cleared key goes with its line, the header stays
when its last key goes, a file with no `[defaults]` gets one at its end, an inline
`defaults = {...}` is edited in place, and a missing file is made (0600 on POSIX).
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from moxi import files
from moxi.config import ConfigError

from . import catalog, presets

TITLE = "Launch defaults"
LINE = ("Every launch starts from these. A seat, a project preset or a flag typed at "
        "launch wins over them. A relaunch or resume keeps what the session launched "
        "with.")
APPLIES = "Applies to sessions launched after this. Running sessions keep their own."
#: The heading of the keys launches refuse or leave out, and of the rows with no group.
NEEDS_FIX = "needs a fix"
START = "start"
#: The headings, in the order their rows are listed.
GROUPS = (NEEDS_FIX, START, *catalog.GROUPS)
#: What the line of a name both harnesses have starts with.
BOTH = "Claude and Codex launches both read it."
#: The text a bool row takes, as `moxi config` reads it.
_TRUE = ("true", "yes", "on", "1")
_FALSE = ("false", "no", "off", "0")
#: The table `[defaults]` is, in the file.
_TABLE = "defaults"


class _Unchanged(Exception):
    """Raised inside the edit block when a change would change nothing."""


@dataclass(frozen=True)
class Table:
    """The hook for `[defaults]` (see the module docstring). `available` stands in for the
    launcher's own answer to which modules are installed."""

    title: str = TITLE
    line: str = LINE
    applies: str = APPLIES
    available: Callable[[str], bool] | None = None

    def file(self, env: Mapping[str, str]) -> Path:
        """The file that holds the table."""
        return presets.path(env)

    def keys(self, env: Mapping[str, str]) -> list[dict]:
        """One dict per row listed, in `moxi config <module> show --json`'s shape, named
        by the row alone. Raises `ConfigError` when `presets.toml` can't be read."""
        avail = presets._avail(self.available)
        held = dict(presets.read(env=env).defaults)
        return _items(held, avail)

    def change(self, row: str, op: str, value: object = None, *, by: str,
               env: Mapping[str, str]) -> tuple[bool, list[str]]:
        """Set (`value` as the command line spells it) or unset `row`; whether the file
        changed, and the launcher's lines: after a `set`, what the new value meets; after
        an `unset`, what every row the table still holds meets, since a clear can change
        another row's lines (clearing `target = slot` brings back a relaunch's). Raises
        `ConfigError` for a refusal, keyed by `row`; a held lock and a file that is not
        TOML pass through from `edit_toml`."""
        avail = presets._avail(self.available)
        path = presets.path(env)
        new: object = None
        try:
            with files.edit_toml(path) as doc:
                held = dict(presets.read(path, env=env).defaults)
                if op == "set":
                    new = _checked(row, value, avail, path)
                    if row in held and _same(held[row], new):
                        raise _Unchanged
                else:
                    _clearable(row, held, avail, path)
                    if row not in held:
                        raise _Unchanged
                _write(doc, row, op, new)
        except _Unchanged:
            return False, []
        if op != "set":
            del held[row]
            return True, _table_lines(held, avail)
        held[row] = new
        refused, warning = _lines(held, avail).get(row, ([], []))
        return True, [*refused, *warning]


TABLE = Table()


# -- the rows -------------------------------------------------------------------------

def offered_rows(available: Callable[[str], bool] | None = None
                 ) -> dict[str, tuple[catalog.Row, ...]]:
    """The rows a launch on this machine takes from `[defaults]`, by name, each with every
    catalog row of that name (two for a name both harnesses have), in the order `keys`
    lists them: by heading (`GROUPS`), then in catalog order."""
    avail = presets._avail(available)
    found: dict[str, list[catalog.Row]] = {}
    for rows in (catalog.launcher(), *(catalog.load(h) for h in catalog.HARNESSES)):
        for name, row in rows.offered(avail).items():
            if name not in presets.PER_SEAT:
                found.setdefault(name, []).append(row)
    ordered = sorted(found.items(), key=lambda item: GROUPS.index(_group(item[1][0])))
    return {name: tuple(rows) for name, rows in ordered}


def offered_values(row: catalog.Row, available: Callable[[str], bool] | None = None
                   ) -> list[str] | None:
    """The values an `enum` row offers here: those whose `when` this machine can meet;
    None for a row of another type."""
    if row.type != "enum":
        return None
    avail = presets._avail(available)
    return [value for value in row.values if _unmet(row, value, avail) is None]


def _group(row: catalog.Row) -> str:
    return row.group if row.group in GROUPS else START


def _unmet(row: catalog.Row, value: object, avail) -> tuple[str, str] | None:
    """The `when` words of an `enum` value whose needs this machine can't meet (a module
    absent, a condition failing), whatever the launch's target; None when it can."""
    need = row.when.get(value) if isinstance(value, str) else None
    if need is None:
        return None
    if all(avail(module) for module in need.requires) and (
            need.condition is None
            or catalog.CONDITIONS[need.condition](row.harness) is None):
        return None
    return need.refused, need.hint


def _items(held: Mapping[str, object], avail) -> list[dict]:
    offered = offered_rows(avail)
    problems = presets.defaults_problems(held, available=avail)
    lines = _lines(held, avail, offered, problems)
    unlisted = {problem.key for problem in problems if _is_unoffered(problem)}
    out = []
    for key, value in held.items():
        if key in offered or key in unlisted:
            continue
        refused, warning = lines.get(key, ([], []))
        rows = _rows_named(key)
        doc = rows[0].doc if rows else ""
        out.append({"name": key, "type": _toml_type(value), "default": None, "doc": doc,
                    "line": doc, "label": (rows[0].label if rows else None) or key,
                    "group": NEEDS_FIX, "value": value, "source": "file",
                    "clear_only": True, "refused": _said(refused),
                    "warning": _said(warning)})
    for name, rows in offered.items():
        row = rows[0]
        refused, warning = lines.get(name, ([], []))
        doc = _doc(rows)
        out.append({"name": name, "type": "string" if row.optional_value else row.type,
                    "default": row.default, "doc": doc,
                    "line": " ".join([doc, *_held_elsewhere(name, held)]),
                    "label": row.label or name, "group": _group(row),
                    "values": offered_values(row, avail),
                    "items": "str" if row.type == "list" else None,
                    "value": held[name] if name in held else row.default,
                    "source": "file" if name in held else "default",
                    "refused": _said(refused), "warning": _said(warning)})
    return out


def _rows_named(name: str) -> list[catalog.Row]:
    """Every catalog row named `name`: the launcher's, else each harness's."""
    if name in catalog.launcher():
        return [catalog.launcher()[name]]
    return [catalog.load(h)[name] for h in catalog.HARNESSES if name in catalog.load(h)]


def _doc(rows: tuple[catalog.Row, ...]) -> str:
    """A row's line: its doc; for a name both harnesses have, each harness's, named, or
    the one when they match."""
    if len(rows) == 1:
        return rows[0].doc
    docs = {row.doc for row in rows}
    if len(docs) == 1:
        return f"{BOTH} {rows[0].doc}"
    return " ".join([BOTH, *(f"{str(row.harness).capitalize()}: {_sentence(row.doc)}"
                             for row in rows)])


def _held_elsewhere(name: str, held: Mapping[str, object]) -> list[str]:
    """The launcher's words for a `[defaults]` value some launches hold something else in
    place of: a relaunch a scratch launch holds off, and a `limit_at` a relaunch launch
    leaves out (while the catalog has one)."""
    relaunch = held.get(presets.RECYCLE_AT_LIMIT) == "relaunch"
    if name == presets.RECYCLE_AT_LIMIT and relaunch:
        return [_sentence(presets.SCRATCH_RECYCLE_OFF)]
    left_out = getattr(presets, "LIMIT_AT_LEFT_OUT", None)
    if (name == getattr(presets, "LIMIT_AT", None) and left_out and relaunch
            and catalog.is_set(held.get(name))):
        return [_sentence(left_out)]
    return []


def _lines(held: Mapping[str, object], avail,
           offered: Mapping[str, tuple[catalog.Row, ...]] | None = None,
           problems: list[presets.Problem] | None = None
           ) -> dict[str, tuple[list[str], list[str]]]:
    """Each key's `refused` and `warning` lines for what `held` meets: each
    `presets.defaults_problems` problem on the key it names (an error refused, a warning a
    warning; the place rule on every place key held), and an offered row's value whose
    `when` this machine can't meet refused, in place of what a launch with no preset
    meets from it."""
    offered = offered_rows(avail) if offered is None else offered
    if problems is None:
        problems = presets.defaults_problems(held, available=avail)
    out: dict[str, tuple[list[str], list[str]]] = {}
    unmet = {name: _unmet(rows[0], held[name], avail) for name, rows in offered.items()
             if name in held}
    unmet = {name: words for name, words in unmet.items() if words is not None}
    places = [key for key in presets.PLACE if catalog.is_set(held.get(key))]
    for problem in problems:
        if problem.key is None or _is_unoffered(problem):
            continue
        for key in places if problem.code == "two-places" else [problem.key]:
            if problem.level == presets.ERROR:
                out.setdefault(key, ([], []))[0].append(f"{problem.problem}; {problem.hint}")
            elif problem.code == "retired":
                out.setdefault(key, ([], []))[1].append(_left_out_words(problem))
            elif key not in unmet:
                out.setdefault(key, ([], []))[1].append(f"{problem.problem}; {problem.hint}")
    for name, (problem, hint) in unmet.items():
        out.setdefault(name, ([], []))[0].append(f"{problem}; {hint}")
    return out


def _table_lines(held: Mapping[str, object], avail) -> list[str]:
    """Every listed key's `refused` and `warning` lines for what `held` meets, in the order
    `keys` lists them, each once."""
    offered = offered_rows(avail)
    lines = _lines(held, avail, offered)
    out: list[str] = []
    for name in [*(key for key in held if key not in offered), *offered]:
        refused, warning = lines.get(name, ([], []))
        out.extend(line for line in (*refused, *warning) if line not in out)
    return out


def _is_unoffered(problem: presets.Problem) -> bool:
    """A key's own check found its row not offered here: the key is not listed."""
    return problem.level == presets.ERROR and problem.code == "needs-module"


def _unlisted(held: Mapping[str, object], avail) -> set[str]:
    """The keys `held` holds for a row this machine doesn't offer."""
    return {problem.key for problem in presets.defaults_problems(held, available=avail)
            if _is_unoffered(problem)}


def _left_out_words(problem: presets.Problem) -> str:
    """A retired row or value's warning: `<what> is left out: <what to do instead>`."""
    return f"{problem.problem.removesuffix(' is retired')} is left out: {problem.hint}"


def _said(lines: list[str]) -> str | None:
    """Lines as one, each a sentence; None for none."""
    return " ".join(_sentence(line) for line in lines) if lines else None


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def _toml_type(value: object) -> str:
    """The type a held value reads as, for a key no row types."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "table"
    return "string"


# -- a change -------------------------------------------------------------------------

def _checked(name: str, value: object, avail, path: Path) -> object:
    """`value` for row `name`, read as its type and checked by the row's own check, or
    `ConfigError`."""
    offered = offered_rows(avail)
    if name not in offered:
        raise ConfigError(path, name, *_own_words(name, value, avail))
    rows = offered[name]
    new = _typed(rows[0], name, value, path)
    for problem in presets.defaults_problems({name: new}, available=avail):
        if problem.key == name and (problem.level == presets.ERROR
                                    or problem.code == "retired"):
            raise ConfigError(path, name, problem.problem, problem.hint)
    for row in rows:
        try:
            row.check(new)
        except catalog.Refused as exc:
            raise ConfigError(path, name, exc.problem, exc.hint) from None
    unmet = _unmet(rows[0], new, avail)
    if unmet is not None:
        raise ConfigError(path, name, *unmet)
    return new


def _clearable(name: str, held: Mapping[str, object], avail, path: Path) -> None:
    """Refuse a clear of a key `keys` doesn't list, with the row's own words."""
    if name in offered_rows(avail) or (name in held and name not in _unlisted(held, avail)):
        return
    raise ConfigError(path, name, *_own_words(name, held.get(name, ""), avail))


def _own_words(name: str, value: object, avail) -> tuple[str, str]:
    """Why `[defaults]` takes no key `name` here, in the launcher's words: the key's own
    check of `value`, else that no row has the name."""
    for problem in presets.defaults_problems({name: value}, available=avail):
        if problem.key == name and (problem.level == presets.ERROR
                                    or problem.code == "retired"):
            return problem.problem, problem.hint
    return f"{name} is not a launcher row", "moxi launcher catalog lists the rows"


def _typed(row: catalog.Row, name: str, value: object, path: Path) -> object:
    """`value` read as `row`'s type when it is text, as `moxi config` reads the command
    line; any other value as it is, for the row's check to judge. A row whose flag takes
    an optional value reads `true` and `false` as the bare flag on and off."""
    if not isinstance(value, str):
        return value
    if row.optional_value and value in ("true", "false"):
        return value == "true"
    if row.type == "bool":
        word = value.strip().lower()
        if word in _TRUE + _FALSE:
            return word in _TRUE
        raise ConfigError(path, name, f"cannot be {value!r}, which is not a bool",
                          "give true or false (yes/no, on/off and 1/0 also do)")
    if row.type in ("int", "float"):
        try:
            return int(value) if row.type == "int" else float(value)
        except ValueError:
            raise ConfigError(path, name, f"cannot be {value!r}, which is not "
                              f"{'an int' if row.type == 'int' else 'a float'}",
                              f"give {'an int' if row.type == 'int' else 'a float'}"
                              ) from None
    if row.type == "list":
        try:
            return tomllib.loads(f"v = {value}")["v"]
        except tomllib.TOMLDecodeError:
            raise ConfigError(path, name, f"cannot be {value!r}, which is not one TOML "
                              "list", "give the whole list as one TOML value, such as "
                              "'[\"a\", \"b\"]'") from None
    return value


def _same(old: object, new: object) -> bool:
    return type(old) is type(new) and old == new


def _write(doc, name: str, op: str, value: object) -> None:
    """Change key `name` of the document's `[defaults]`, keeping every other byte."""
    if _TABLE not in doc:
        doc[_TABLE] = {}
    table = doc[_TABLE]
    if op != "set":
        del table[name]
    elif (name in table or table.as_string().lstrip().startswith("{")
          or not table.value.body):
        table[name] = value
    else:
        body = table.value
        keys = [key for key, _ in body.body if key is not None]
        if keys:
            body._insert_after(keys[-1], name, value)
        else:
            body._insert_at(0, name, value)


__all__ = ["APPLIES", "BOTH", "GROUPS", "LINE", "NEEDS_FIX", "START", "TABLE", "TITLE",
           "Table", "offered_rows", "offered_values"]
