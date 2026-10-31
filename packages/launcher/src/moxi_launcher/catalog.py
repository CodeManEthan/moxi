"""The flag catalogs: what a launch may set, and how each harness command spells it.

One TOML file per harness ships in the package, `catalog/claude.toml` and
`catalog/codex.toml`, and a third, `catalog/launcher.toml`, holds the launcher's own rows,
which no harness spells (`harness`, `cwd`, `brief`, `target` and the rest). Each row is a
table keyed by the launcher's name for it. Its fields are `moxi.config.Key`'s shape plus
the catalog's own:

    type, default, doc, values, env, retired
             as a config key. A default of "" (or [] or false) means the row is not set.
             On a `string` row `values` lists suggestions, and any string is taken; on an
             `enum` it is the set of values the row takes. `env` names a variable spelled
             with the launcher's prefix. A retired row says what to do instead, and
             setting it is refused.
    flag     the harness spelling (`--model`, `-m`); a launcher row has none
    aliases  other spellings of the same flag that the harness's `--help` lists
             (`--allowed-tools` beside `--allowedTools`)
    repeatable, variadic
             on a `list` row: the flag may appear once per item; the flag takes several
             values. Either way each item is composed as `<flag> <item>`.
    optional_value
             the flag takes an optional value: the row is false (absent), true (the bare
             flag) or a value, which is composed as `<flag>=<value>`, so no bare token
             ever follows the flag
    modes    the commands the row composes into, with the spelling each one takes. A
             list of mode names means each takes `flag`. A table maps a mode to its
             spelling: a flag, or a table with `flag`, an optional `value` template and
             an optional `join`. A template holds `{value}` (the value as text), `{toml}`
             (the value as a TOML literal, a list whole) or `{cwd_toml}` (the launch's
             directory as a TOML string); a `join` puts a list's items in one token.
    items    on a `list` row: "path" when each item is a path
    owned    the launcher sets the row itself; it is never offered, and a pass-through
             flag that spells it is refused
    posture  the row is part of the permission posture
    since    the harness version that added the flag; an older harness is not expected
             to list it
    hidden   the harness's `--help` does not list the flag
    requires a Moxi module that must be installed for the row to be offered

The modes are Claude's `interactive` (`claude`) and `print` (`claude -p`), and Codex's
`interactive` (`codex`), `exec` (`codex exec`) and `host` (turns' Codex host, and comms'
Codex wrapper, which takes the host's flags). A harness file also holds one `[catalog]`
table: the harness, the version its lists were checked against, and per `--help` command
(`[catalog.help.<name>]`) the argv that prints it, the modes it covers and the flags it
lists that no row spells (`others`).

What this module gives the rest of the launcher:

- `load(harness)` and `launcher()`: the parsed catalogs, checked when read; `offered(...)`
  the rows a user may set on this install.
- `Row.check(value)` and `Row.tokens(mode, value, cwd=...)`: a value checked against its
  row, and the tokens one mode spells it as.
- `fit(harness, mode, values, chosen)`: the modes rule. A composed command carries only
  flags its own command accepts: a row whose `modes` lacks the launch's mode is left out
  when its value is a default, and the launch is refused naming the row and the mode when
  its value was chosen for this launch.
- `build_parser` and `parse`: the parser of a launch's flags, generated from the launcher's
  rows and the chosen harness's, read with `parse_known_args`. An unknown flag passes on
  with one warning and goes last; a pass-through flag that spells an owned or a posture
  row is refused naming the row.
- `diff(harness)`: each `--help` the harness's modes compose for, compared with the
  catalog. It is the one place the launcher reads human-readable output, and it is the
  harness's, not a Moxi module's.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tomllib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from types import MappingProxyType

from moxi import exit as codes
from moxi import names
from moxi.config import Key

MODULE = "launcher"
#: The harnesses a catalog ships for, and the modes each one's commands compose in.
MODES = {"claude": ("interactive", "print"), "codex": ("interactive", "exec", "host")}
HARNESSES = tuple(MODES)
#: How a refusal names a mode's command.
COMMANDS = {("claude", "interactive"): "claude", ("claude", "print"): "claude -p",
            ("codex", "interactive"): "codex", ("codex", "exec"): "codex exec",
            ("codex", "host"): "turns' Codex host"}
#: The name of the launcher's own rows, in place of a harness.
LAUNCHER = "launcher"
#: The launcher row that holds the flags passed on to the harness, last on its argv.
#: The parser fills it from unknown flags and the words after `--`.
EXTRA = "extra_flags"
#: Flags with no row of their own that set what a posture row sets, per harness: the
#: posture row each one stands in for. Passed on, each would change the posture without
#: the posture check, so each is refused like that row, in every mode. Claude's two skip
#: or offer to skip every permission check; Codex's bypass drops the sandbox and the
#: approvals, its approve-for-me hands approvals to an automatic reviewer, and its host
#: takes a permissions profile in place of a sandbox mode.
POSTURE_FLAGS = {
    "claude": {"--dangerously-skip-permissions": "permission_mode",
               "--allow-dangerously-skip-permissions": "permission_mode"},
    "codex": {"--permissions": "sandbox",
              "--dangerously-bypass-approvals-and-sandbox": "sandbox",
              "--approve-for-me": "approval"},
}
#: Config overrides that set what a posture row sets, per harness: the flags that carry a
#: `key=value` override (in any mode), and each key with the posture row it stands in for.
#: An override with one of these keys, passed on or as a `config` item, is refused like
#: the row, in every mode.
POSTURE_KEYS = {
    "claude": ((), {}),
    "codex": (("-c", "--config", "--option"),
              {"sandbox_mode": "sandbox", "approval_policy": "approval"}),
}
#: Claude's `--settings` (a file or a JSON string) takes a `permissions` table whose
#: entries set what these posture rows set. A JSON string holding one is refused like the
#: row; a file is not read here.
SETTINGS_FLAG = "--settings"
POSTURE_SETTINGS = {"claude": {"defaultMode": "permission_mode", "allow": "allowed_tools",
                               "deny": "disallowed_tools"}}

TYPES = ("bool", "int", "float", "string", "enum", "path", "list")
KEY_FIELDS = ("type", "default", "doc", "values", "env", "retired")
OWN_FIELDS = ("flag", "aliases", "repeatable", "optional_value", "variadic", "modes",
              "items", "owned", "posture", "since", "hidden", "requires")
FIELDS = KEY_FIELDS + OWN_FIELDS
SPELLING_FIELDS = ("flag", "value", "join")
PLACEHOLDERS = ("{value}", "{toml}", "{cwd_toml}")
#: How long one `--help` (or `--version`) may run, in seconds.
HELP_TIMEOUT = 20.0

_FLAG = re.compile(r"--?[A-Za-z0-9][A-Za-z0-9-]*")
_NAME = re.compile(r"[a-z][a-z0-9_]*")
_VERSION = re.compile(r"\d+(?:\.\d+)+")
_PLACEHOLDER = re.compile(r"\{value\}|\{toml\}|\{cwd_toml\}")


# -- errors ---------------------------------------------------------------------------

class CatalogError(Exception):
    """A catalog file that does not hold to the format: the launcher's own bug."""

    def __init__(self, source: str, row: str | None, problem: str):
        self.source, self.row, self.problem = source, row, problem
        where = f"{source}: row {row!r}" if row else source
        super().__init__(f"{where}: {problem}")


class Refused(Exception):
    """A value or a flag a launch cannot take. `code` is the short code a refusal prints,
    `hint` what to do instead, `exit_code` what the command exits with."""

    code = "refused"
    exit_code = codes.REFUSED

    def __init__(self, row: str | None, problem: str, hint: str):
        self.row, self.problem, self.hint = row, problem, hint
        super().__init__(f"{problem}; {hint}")


class BadValue(Refused):
    """A value of the wrong type, or one an `enum` row does not take, or a flag the
    parser cannot read."""

    code = "bad-value"
    exit_code = codes.USAGE


class NotTaken(Refused):
    """A value chosen for this launch on a row the launch's mode does not compose."""

    code = "not-taken"

    def __init__(self, row: str, harness: str, mode: str):
        self.harness, self.mode = harness, mode
        command = COMMANDS[harness, mode]
        super().__init__(row, f"{row} is not taken by {command}",
                         f"leave {row} out of this launch, or launch another way")


class Owned(Refused):
    """A pass-through flag that spells a row the launcher sets itself, or a posture
    row, which is set through its own option so the posture check sees it."""

    code = "owned"

    def __init__(self, row: "Row", token: str):
        self.token = token
        if row.owned:
            problem = f"{token} sets {row.name}, which the launcher sets itself"
            hint = f"leave {token} out of the flags passed on"
        else:
            problem = f"{token} sets {row.name}, a permission posture row"
            hint = f"set it with {row.option}, so the posture check sees it"
        super().__init__(row.name, problem, hint)


class PostureFlag(Owned):
    """A pass-through flag with no row that sets what a posture row sets."""

    def __init__(self, row: str, token: str):
        self.token = token
        Refused.__init__(self, row, f"{token} sets what the posture row {row} sets, and "
                                    "has no row of its own for the posture check to see",
                         f"leave {token} out, and set {row} with {_option(row)} instead, "
                         "so the posture check sees it")


class PostureKey(Owned):
    """A config override or setting whose key sets what a posture row sets, which the
    posture check can't see there."""

    def __init__(self, row: str, key: str):
        self.token = key
        Refused.__init__(self, row, f"the config key {key} sets what the posture row "
                                    f"{row} sets, where the posture check can't see it",
                         f"leave {key} out, and set {row} with {_option(row)} instead, "
                         "so the posture check sees it")


def _option(row_name: str) -> str:
    """The launcher's option for a row: its name, dashed."""
    return "--" + row_name.replace("_", "-")


class NeedsModule(Refused):
    """A row whose `requires` module is not installed."""

    code = "needs-module"

    def __init__(self, row: "Row"):
        super().__init__(row.name, f"{row.name} needs {row.requires}",
                         f"install {names.distribution(row.requires)}, or leave "
                         f"{row.name} out")


# -- rows -----------------------------------------------------------------------------

def is_set(value: object) -> bool:
    """True when `value` sets its row: anything but None, "", false, [] and {}."""
    return not (value is None or value is False or value == "" or value == []
                or value == {})


def toml_literal(value: object) -> str:
    """`value` spelled as a TOML literal: a string quoted, a list bracketed."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_literal(item) for item in value) + "]"
    # A JSON string is a TOML basic string but for DEL, which TOML wants escaped.
    return json.dumps(str(value), ensure_ascii=False).replace("\x7f", "\\u007f")


@dataclass(frozen=True)
class Spelling:
    """How one mode spells a row: the flag, and the template its value takes, if any."""

    flag: str
    value: str | None = None
    join: str | None = None

    def render(self, value: object, cwd: str | os.PathLike | None) -> str:
        if self.value is None:
            return str(value)
        if "{cwd_toml}" in self.value and cwd is None:
            raise ValueError(f"the spelling {self.flag} {self.value} needs the launch's "
                             "directory")
        fill = {"{value}": lambda: str(value), "{toml}": lambda: toml_literal(value),
                "{cwd_toml}": lambda: toml_literal(str(cwd))}
        # One pass, so a value that holds a placeholder's spelling stays as given.
        return _PLACEHOLDER.sub(lambda m: fill[m.group(0)](), self.value)

    def key_pattern(self) -> re.Pattern | None:
        """For a templated spelling, what the first part of a value token (before `=`)
        must look like for the token to set this row; None when the value is free."""
        if self.value is None:
            return None
        head = self.value.split("=", 1)[0]
        parts = re.split(f"({_PLACEHOLDER.pattern})", head)
        return re.compile("".join(".+" if part in PLACEHOLDERS else re.escape(part)
                                  for part in parts))


@dataclass(frozen=True)
class Row:
    """One catalog row. `harness` is None for a launcher row."""

    name: str
    harness: str | None
    type: str
    default: object
    doc: str
    values: tuple | None = None
    env: str | None = None
    retired: str | None = None
    flag: str | None = None
    aliases: tuple = ()
    repeatable: bool = False
    optional_value: bool = False
    variadic: bool = False
    modes: Mapping[str, Spelling] = field(default_factory=lambda: MappingProxyType({}))
    items: str | None = None
    owned: bool = False
    posture: bool = False
    since: str | None = None
    hidden: bool = False
    requires: str | None = None

    @property
    def key(self) -> Key:
        """The row as a `moxi.config.Key`, read at use, for code that reads keys."""
        values = self.values if self.type == "enum" else None
        items = "str" if self.type == "list" else None
        return Key(name=self.name, type=self.type, default=self.default, doc=self.doc,
                   read="use", values=values, env=self.env, retired=self.retired,
                   needs=self.requires, items=items)

    @property
    def option(self) -> str:
        """The launcher's own option for the row, `--permission-mode`."""
        return "--" + self.name.replace("_", "-")

    @property
    def settable(self) -> bool:
        """A user may set the row, typed or in a preset: it is live and the launcher
        does not own it."""
        return self.retired is None and not self.owned

    def offered(self, available: Callable[[str], bool] | None = None) -> bool:
        """The row is offered on this install: it is live and its module is present."""
        if self.retired is not None:
            return False
        return self.requires is None or _available(available)(self.requires)

    def spellings(self) -> frozenset[str]:
        """Every flag that sets the row in some mode, its aliases included."""
        flags = {spelling.flag for spelling in self.modes.values()}
        if self.flag:
            flags.add(self.flag)
        if self.flag in flags:
            flags.update(self.aliases)
        return frozenset(flags)

    def check(self, value: object) -> object:
        """`value` as this row's type, or `BadValue`. A path expands `~`, must then be
        absolute, and reads as a string; "" reads None."""
        if self.retired is not None:
            raise BadValue(self.name, f"{self.name} is retired", self.retired)
        if self.optional_value and isinstance(value, bool):
            return value
        kind = self.type
        ok = {
            "bool": isinstance(value, bool),
            "int": isinstance(value, int) and not isinstance(value, bool),
            "float": isinstance(value, (int, float)) and not isinstance(value, bool),
            "list": isinstance(value, list),
        }.get(kind, isinstance(value, str))
        if not ok:
            want = {"int": "an int", "enum": "a string", "list": "a list"}.get(
                kind, f"a {kind}")
            if self.optional_value:
                want += " or true"
            raise BadValue(self.name, f"{self.name} is {_kind(value)}, not {want}",
                           f"set {self.name} to {want}{_choices(self)}")
        if kind == "float":
            return float(value)
        if kind == "enum" and value != "" and value not in self.values:
            raise BadValue(self.name, f"{self.name} cannot be {value!r}",
                           f"set it to one of {', '.join(map(repr, self.values))}")
        if kind == "path":
            return _path(self.name, value)
        if kind == "list":
            for i, item in enumerate(value):
                if not isinstance(item, str):
                    raise BadValue(self.name, f"{self.name} item {i + 1} is "
                                   f"{_kind(item)}, not a string",
                                   "make every item a string")
            if self.items == "path":
                return [_path(self.name, item) for item in value]
            return list(value)
        return value

    def tokens(self, mode: str, value: object, *, cwd: str | os.PathLike | None = None
               ) -> list[str]:
        """The tokens mode `mode` spells `value` as; [] when the value does not set the
        row. Raises `KeyError` when the mode does not take the row."""
        spelling = self.modes[mode]
        if not is_set(value):
            return []
        if self.type == "bool":
            return [spelling.flag] + ([spelling.render(value, cwd)]
                                      if spelling.value is not None else [])
        if self.optional_value:
            if value is True:
                return [spelling.flag]
            return [f"{spelling.flag}={spelling.render(value, cwd)}"]
        if self.type == "list":
            items = [str(item) for item in value]
            if spelling.value is not None and "{toml}" in spelling.value:
                return [spelling.flag, spelling.render(items, cwd)]
            if spelling.join is not None:
                return [spelling.flag,
                        spelling.join.join(spelling.render(item, cwd) for item in items)]
            out: list[str] = []
            for item in items:
                out += [spelling.flag, spelling.render(item, cwd)]
            return out
        return [spelling.flag, spelling.render(value, cwd)]


@dataclass(frozen=True)
class Help:
    """One `--help` the catalog is checked against."""

    name: str
    command: tuple[str, ...]
    modes: tuple[str, ...]
    others: frozenset[str]


@dataclass(frozen=True)
class Catalog:
    """One parsed catalog file: a harness's rows, or the launcher's (`harness` is
    `LAUNCHER`)."""

    harness: str
    rows: Mapping[str, Row]
    source: str
    checked: str | None = None
    helps: tuple[Help, ...] = ()

    def __getitem__(self, name: str) -> Row:
        return self.rows[name]

    def __contains__(self, name: object) -> bool:
        return name in self.rows

    def __iter__(self):
        return iter(self.rows.values())

    def offered(self, available: Callable[[str], bool] | None = None) -> dict[str, Row]:
        """The rows a user may set on this install, by name."""
        avail = _available(available)
        return {row.name: row for row in self if row.settable and row.offered(avail)}


# -- loading --------------------------------------------------------------------------

@cache
def load(harness: str) -> Catalog:
    """The shipped catalog of `harness`, read and checked once per process."""
    if harness not in MODES:
        raise ValueError(f"no catalog for harness {harness!r}; the harnesses are "
                         f"{', '.join(HARNESSES)}")
    catalog = _read(harness)
    clash = sorted(name for name, row in catalog.rows.items()
                   if name in launcher().rows and not row.owned)
    if clash:
        raise CatalogError(catalog.source, clash[0], "shares its name with a launcher row; "
                           "only an owned row may, and takes the launcher row's value")
    return catalog


@cache
def launcher() -> Catalog:
    """The launcher's own rows, read and checked once per process."""
    return _read(LAUNCHER)


def offered(harness: str, available: Callable[[str], bool] | None = None
            ) -> dict[str, Row]:
    """Every row a user may set for a `harness` launch on this install: the launcher's
    rows, then the harness's, by name."""
    avail = _available(available)
    return {**launcher().offered(avail), **load(harness).offered(avail)}


def row(harness: str, name: str) -> Row | None:
    """The row `name` a `harness` launch reads: a launcher row, else the harness's;
    None when neither has it."""
    if name in launcher():
        return launcher()[name]
    return load(harness).rows.get(name)


def _read(name: str) -> Catalog:
    from importlib.resources import files

    source = f"{__package__}/catalog/{name}.toml"
    try:
        text = files(__package__).joinpath("catalog", f"{name}.toml").read_text(
            encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CatalogError(source, None, f"cannot be read ({exc}); reinstall the "
                           "launcher") from None
    return parse_text(text, harness=name, source=source)


def parse_text(text: str, *, harness: str, source: str = "catalog") -> Catalog:
    """Read one catalog file's text. `harness` is a harness name, or `LAUNCHER` for the
    launcher's rows. Raises `CatalogError` on anything the format does not allow."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise CatalogError(source, None, f"is not valid TOML ({exc})") from None
    meta = data.pop("catalog", None)
    rows = {}
    for name, spec in data.items():
        rows[name] = _row(name, spec, harness, source)
    if harness == LAUNCHER:
        if meta is not None:
            raise CatalogError(source, None, "the launcher's rows take no [catalog] table")
        return Catalog(LAUNCHER, MappingProxyType(rows), source)
    checked, helps = _meta(meta, harness, source)
    catalog = Catalog(harness, MappingProxyType(rows), source, checked, helps)
    covered = {mode for help_ in helps for mode in help_.modes}
    if covered != {m for m in MODES[harness] if m != "host"}:
        raise CatalogError(source, None, "its [catalog.help] tables do not cover every "
                           "mode but host exactly once")
    return catalog


def _meta(meta, harness, source) -> tuple:
    def bad(problem):
        return CatalogError(source, None, problem)

    if not isinstance(meta, dict):
        raise bad("lacks its [catalog] table")
    unknown = sorted(set(meta) - {"harness", "checked", "help"})
    if unknown:
        raise bad(f"[catalog] has unknown field(s) {', '.join(unknown)}")
    if meta.get("harness") != harness:
        raise bad(f"[catalog] names harness {meta.get('harness')!r}, not {harness!r}")
    checked = meta.get("checked")
    if checked is not None and not (isinstance(checked, str)
                                    and _VERSION.fullmatch(checked)):
        raise bad(f"[catalog] checked {checked!r} is not a version")
    helps = []
    seen_modes: set = set()
    for name, spec in (meta.get("help") or {}).items():
        if not isinstance(spec, dict) or set(spec) - {"command", "modes", "others"}:
            raise bad(f"[catalog.help.{name}] takes only command, modes and others")
        command, modes = spec.get("command"), spec.get("modes")
        others = spec.get("others", [])
        if not _str_list(command) or not command:
            raise bad(f"[catalog.help.{name}] command is not a list of strings")
        if (not _str_list(modes) or not modes or "host" in modes
                or set(modes) - set(MODES[harness]) or seen_modes & set(modes)):
            raise bad(f"[catalog.help.{name}] modes must be {harness}'s modes, host "
                      "excluded, each in one help")
        seen_modes.update(modes)
        if not _str_list(others) or not all(_FLAG.fullmatch(f) for f in others):
            raise bad(f"[catalog.help.{name}] others is not a list of flags")
        helps.append(Help(name, tuple(command), tuple(modes), frozenset(others)))
    return checked, tuple(helps)


def _row(name, spec, harness, source) -> Row:
    def bad(problem):
        return CatalogError(source, name, problem)

    if not _NAME.fullmatch(name):
        raise bad("is not a row name: lower-case letters, digits and _")
    if not isinstance(spec, dict):
        raise bad("is a value, not a row's table")
    unknown = sorted(set(spec) - set(FIELDS))
    if unknown:
        raise bad(f"has unknown field(s) {', '.join(unknown)}")
    for required in ("type", "default", "doc"):
        if required not in spec:
            raise bad(f"lacks {required}")
    kind, doc, default = spec["type"], spec["doc"], spec["default"]
    if kind not in TYPES:
        raise bad(f"has type {kind!r}; use one of {', '.join(TYPES)}")
    if not isinstance(doc, str) or not doc.strip() or "\n" in doc:
        raise bad("has no one-line doc")
    flags = {f: spec.get(f, False) for f in ("repeatable", "optional_value", "variadic",
                                             "owned", "posture", "hidden")}
    for f, value in flags.items():
        if not isinstance(value, bool):
            raise bad(f"has {f} {value!r}, not a bool")
    retired = spec.get("retired")
    if retired is not None and (not isinstance(retired, str) or not retired.strip()):
        raise bad("has an empty retired; say what to do instead")

    values = spec.get("values")
    if values is not None:
        if (kind not in ("enum", "string") or not _str_list(values) or not values
                or len(set(values)) != len(values)):
            raise bad("has values, which only an enum or a string row takes, as a list "
                      "of distinct strings")
        values = tuple(values)
    elif kind == "enum":
        raise bad("is an enum without values")

    env = spec.get("env")
    if env is not None:
        prefix = names.env_prefix(MODULE)
        if (not isinstance(env, str) or not env.startswith(prefix) or env == prefix
                or not re.fullmatch(r"[A-Z0-9_]+", env)):
            raise bad(f"has env {env!r}; name one upper-case variable starting {prefix}")
        if kind == "list":
            raise bad("has env on a list")

    items = spec.get("items")
    if items is not None and (kind != "list" or items != "path"):
        raise bad("has items, which only a list row takes, as \"path\"")
    if (flags["repeatable"] or flags["variadic"]) and kind != "list":
        raise bad("is repeatable or variadic but not a list")

    flag = spec.get("flag")
    aliases = spec.get("aliases", [])
    raw_modes = spec.get("modes")
    if harness == LAUNCHER:
        for f in ("flag", "aliases", "modes", "optional_value", "since", "hidden"):
            if spec.get(f):
                raise bad(f"is a launcher row, which takes no {f}")
        modes = MappingProxyType({})
    else:
        if flag is not None and not (isinstance(flag, str) and _FLAG.fullmatch(flag)):
            raise bad(f"has flag {flag!r}, which is not a flag")
        if not _str_list(aliases) or not all(_FLAG.fullmatch(a) for a in aliases):
            raise bad("has aliases that are not a list of flags")
        if aliases and flag is None:
            raise bad("has aliases but no flag")
        modes = _modes(raw_modes, flag, harness, bad)
    if flags["optional_value"]:
        if kind != "string" or default is not False:
            raise bad("takes an optional value, so it is a string row whose default is "
                      "false")
    since = spec.get("since")
    if since is not None and not (isinstance(since, str) and _VERSION.fullmatch(since)):
        raise bad(f"has since {since!r}, which is not a version")
    requires = spec.get("requires")
    if requires is not None and not names.is_module_name(requires):
        raise bad(f"has requires {requires!r}, which is not a module name")
    if flags["owned"] and flags["posture"]:
        raise bad("is both owned and a posture row")

    built = Row(name=name, harness=None if harness == LAUNCHER else harness, type=kind,
                default=default, doc=doc, values=values, env=env, retired=retired,
                flag=flag, aliases=tuple(aliases), modes=modes, items=items,
                since=since, requires=requires, **flags)
    if retired is None:
        try:
            checked = (built.check(default) if not (kind == "path" and default == "")
                       else None)
        except BadValue as exc:
            raise bad(f"has a default that does not fit: {exc.problem}") from None
        if kind == "enum" and default != "" and checked not in values:
            raise bad("has a default outside its values")
    return built


def _modes(raw, flag, harness, bad) -> Mapping[str, Spelling]:
    if raw is None:
        raise bad("has no modes; a harness row names the commands it composes into")
    out: dict[str, Spelling] = {}
    if isinstance(raw, list):
        if not _str_list(raw) or not raw:
            raise bad("has modes that are not a list of mode names")
        if flag is None:
            raise bad("lists its modes by name but has no flag for them to take")
        raw = {mode: flag for mode in raw}
    if not isinstance(raw, dict) or not raw:
        raise bad("has modes that are neither a list of names nor a table of spellings")
    for mode, spelling in raw.items():
        if mode not in MODES[harness]:
            raise bad(f"names mode {mode!r}; {harness}'s modes are "
                      f"{', '.join(MODES[harness])}")
        if isinstance(spelling, str):
            spelling = {"flag": spelling}
        if not isinstance(spelling, dict) or set(spelling) - set(SPELLING_FIELDS):
            raise bad(f"spells mode {mode} with something other than a flag or a table "
                      f"of {', '.join(SPELLING_FIELDS)}")
        f, value, join = spelling.get("flag"), spelling.get("value"), spelling.get("join")
        if not (isinstance(f, str) and _FLAG.fullmatch(f)):
            raise bad(f"spells mode {mode} with {f!r}, which is not a flag")
        if value is not None and (not isinstance(value, str) or not value):
            raise bad(f"spells mode {mode} with a value template that is not a string")
        if join is not None and not isinstance(join, str):
            raise bad(f"spells mode {mode} with a join that is not a string")
        out[mode] = Spelling(f, value, join)
    return MappingProxyType(out)


# -- the modes rule -------------------------------------------------------------------

@dataclass(frozen=True)
class Fit:
    """What one mode composes of a launch's values. `applied` holds the harness rows the
    mode takes, by name, set values only; `not_applied` the rows set by a default that
    the mode does not take, which the launch records as not applied."""

    mode: str
    applied: Mapping[str, object]
    not_applied: tuple[str, ...]


def fit(harness: str, mode: str, values: Mapping[str, object],
        chosen: Iterable[str] = ()) -> Fit:
    """Apply the modes rule to a launch's resolved `values`.

    `chosen` names the rows whose value was chosen for this launch, typed or from the
    named preset; the others came from a default (the shipped one or `[defaults]`). A
    harness row whose `modes` lacks `mode` is not composed: when it was chosen,
    `NotTaken` is raised naming the row and the mode; when it is a default, it is left
    out and named in `not_applied`. Launcher rows are not the composer's flags and are
    left to the caller. An owned harness row that shares a launcher row's name (Codex's
    `cwd`) takes that row's value.
    """
    if mode not in MODES.get(harness, ()):
        raise ValueError(f"{harness} has no mode {mode!r}")
    chosen = frozenset(chosen)
    catalog = load(harness)
    applied: dict[str, object] = {}
    not_applied: list[str] = []
    for row_ in catalog:
        if row_.name not in values or not is_set(values[row_.name]):
            continue
        if mode in row_.modes:
            applied[row_.name] = values[row_.name]
        elif row_.name in chosen:
            raise NotTaken(row_.name, harness, mode)
        else:
            not_applied.append(row_.name)
    return Fit(mode, MappingProxyType(applied), tuple(not_applied))


# -- the parser -----------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    """A parser that raises `BadValue` instead of exiting."""

    def error(self, message):
        raise BadValue(None, message, "see moxi launcher catalog for the flags a launch "
                                      "takes")


class _Retired(argparse.Action):
    def __init__(self, *args, retired: str = "", **kwargs):
        self.retired = retired
        super().__init__(*args, nargs=0, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        raise BadValue(self.dest, f"{option_string} is retired", self.retired)


def build_parser(harness: str, *, available: Callable[[str], bool] | None = None,
                 prog: str = "moxi launcher run") -> argparse.ArgumentParser:
    """The parser of a `harness` launch's flags: one option per row offered on this
    install, the launcher's rows first. It has no `--help` of its own, abbreviates
    nothing, and leaves every option None unless typed."""
    parser = _Parser(prog=prog, add_help=False, allow_abbrev=False)
    avail = _available(available)
    rows = [*launcher(), *load(harness)]
    taken: set = set()
    for row_ in rows:
        if row_.option in taken or row_.name == EXTRA or row_.owned:
            continue
        if row_.retired is not None:
            parser.add_argument(row_.option, dest=row_.name, action=_Retired,
                                retired=row_.retired, help=argparse.SUPPRESS)
            taken.add(row_.option)
            continue
        if not row_.offered(avail):
            continue
        taken.add(row_.option)
        _add_option(parser, row_)
    return parser


def _add_option(parser: argparse.ArgumentParser, row_: Row) -> None:
    common = {"dest": row_.name, "default": None, "help": row_.doc}
    if row_.type == "bool":
        parser.add_argument(row_.option, action=argparse.BooleanOptionalAction, **common)
    elif row_.optional_value:
        parser.add_argument(row_.option, nargs="?", const=True, metavar="VALUE", **common)
    elif row_.type == "list":
        convert = _cli_path if row_.items == "path" else str
        if row_.variadic:
            parser.add_argument(row_.option, nargs="+", action="extend", type=convert,
                                metavar="VALUE", **common)
        else:
            parser.add_argument(row_.option, action="append", type=convert,
                                metavar="VALUE", **common)
    else:
        convert = {"int": int, "float": float, "path": _cli_path}.get(row_.type, str)
        choices = row_.values if row_.type == "enum" else None
        parser.add_argument(row_.option, type=convert, choices=choices,
                            metavar=row_.name.upper(), **common)


@dataclass(frozen=True)
class Parsed:
    """A launch's typed flags. `values` holds the rows typed, checked, by name;
    `unknown` the flags the parser did not know, in order, and `extra` those with the
    words after `--`, which go last on the harness's argv."""

    harness: str
    values: Mapping[str, object]
    unknown: tuple[str, ...]
    extra: tuple[str, ...]

    @property
    def warning(self) -> str | None:
        """The one warning line for the unknown flags, or None."""
        if not self.unknown:
            return None
        return ("passing on, last on the argv, flags the catalog does not know: "
                + " ".join(self.unknown))


def parse(argv: Sequence[str], harness: str, *,
          available: Callable[[str], bool] | None = None) -> Parsed:
    """Read a launch's flags for `harness`. Words after the first `--` are passed on as
    given; unknown flags before it are passed on too, after one warning. A passed-on flag
    that spells an owned or a posture row raises `Owned`; a flag of a row this install
    does not offer raises `NeedsModule`; a bad value raises `BadValue`. A typed relative
    path is read against this process's directory."""
    argv = list(argv)
    tail: list[str] = []
    if "--" in argv:
        cut = argv.index("--")
        argv, tail = argv[:cut], argv[cut + 1:]
    avail = _available(available)
    parser = build_parser(harness, available=avail)
    namespace, unknown = parser.parse_known_args(argv)
    _refuse_unoffered(harness, unknown, avail)
    values = {}
    for name, value in vars(namespace).items():
        if value is None:
            continue
        found = row(harness, name)
        values[name] = found.check(value) if found is not None else value
    extra = [*unknown, *tail]
    refuse_passthrough(harness, extra)
    return Parsed(harness, MappingProxyType(values), tuple(unknown), tuple(extra))


def typed_harness(argv: Sequence[str]) -> str | None:
    """The harness `argv` names with `--harness`, before any `--`, or None. A caller
    needs it to pick the catalog before parsing the rest."""
    argv = list(argv)
    if "--" in argv:
        argv = argv[:argv.index("--")]
    peek = _Parser(add_help=False, allow_abbrev=False)
    peek.add_argument("--harness", choices=HARNESSES)
    namespace, _ = peek.parse_known_args(argv)
    return namespace.harness


def refuse_passthrough(harness: str, tokens: Sequence[str]) -> None:
    """Raise `Owned` when a token of `tokens`, flags passed on to `harness`, spells an
    owned or a posture row in any of its modes, `PostureFlag` when it is a flag with
    no row that sets what a posture row sets (`POSTURE_FLAGS`), and `PostureKey` when it
    is a config override or a settings string whose key does (`POSTURE_KEYS`,
    `POSTURE_SETTINGS`). A templated spelling (Codex's `-c
    check_for_update_on_startup=false`) matches only when the value's key does."""
    rows = [r for r in load(harness) if r.owned or r.posture]
    standing_in = POSTURE_FLAGS.get(harness, {})
    key_flags, _ = POSTURE_KEYS.get(harness, ((), {}))
    tokens = list(tokens)
    for i, token in enumerate(tokens):
        flag, attached = _split_flag(token)
        if flag is None:
            continue
        if flag in standing_in:
            raise PostureFlag(standing_in[flag], flag)
        value = attached if attached is not None else (
            tokens[i + 1] if i + 1 < len(tokens) else None)
        for row_ in rows:
            if _spells(row_, flag, value):
                raise Owned(row_, flag)
        if value is not None and flag in key_flags:
            refuse_posture_keys(harness, [value])
        if value is not None and flag == SETTINGS_FLAG:
            refuse_posture_settings(harness, value)


def refuse_posture_keys(harness: str, items: Iterable[object]) -> None:
    """Raise `PostureKey` when a `key=value` config override of `items` has a key that
    sets what a posture row of `harness` sets (`POSTURE_KEYS`). A key that only starts
    with one is another key."""
    _, keys = POSTURE_KEYS.get(harness, ((), {}))
    for item in items:
        key = str(item).partition("=")[0].strip()
        if key in keys:
            raise PostureKey(keys[key], key)


def refuse_posture_settings(harness: str, value: str) -> None:
    """Raise `PostureKey` when `value`, given to Claude's `--settings`, is a JSON string
    whose `permissions` table has an entry that sets what a posture row sets
    (`POSTURE_SETTINGS`). A value that is not a JSON object is a settings file, which is
    not read here."""
    entries = POSTURE_SETTINGS.get(harness, {})
    if not entries:
        return
    try:
        settings = json.loads(value)
    except ValueError:
        return
    permissions = settings.get("permissions") if isinstance(settings, dict) else None
    if not isinstance(permissions, dict):
        return
    for entry, row_name in entries.items():
        if entry in permissions:
            raise PostureKey(row_name, f"permissions.{entry}")


def _split_flag(token: str) -> tuple:
    if token.startswith("--") and len(token) > 2:
        flag, eq, rest = token.partition("=")
        return flag, (rest if eq else None)
    if token.startswith("-") and len(token) >= 2 and token[1] != "-":
        return token[:2], (token[2:].removeprefix("=") or None)
    return None, None


def _spells(row_: Row, flag: str, value: str | None) -> bool:
    for spelling in row_.modes.values():
        names_ = {spelling.flag}
        if spelling.flag == row_.flag:
            names_.update(row_.aliases)
        if flag not in names_:
            continue
        pattern = spelling.key_pattern()
        if pattern is None:
            return True
        if value is not None and pattern.fullmatch(value.split("=", 1)[0]):
            return True
    return False


def _refuse_unoffered(harness, unknown, avail) -> None:
    hidden = {r.option: r for r in [*launcher(), *load(harness)]
              if r.retired is None and not r.owned and not r.offered(avail)}
    for token in unknown:
        found = hidden.get(token.split("=", 1)[0])
        if found is not None:
            raise NeedsModule(found)


# -- the --help diff ------------------------------------------------------------------

def help_flags(text: str) -> frozenset[str]:
    """The flag spellings a `--help` text lists: in its `Options:` section (the whole
    text when it has none), each line that starts, within six spaces, with a flag."""
    lines = text.splitlines()
    start = next((i + 1 for i, line in enumerate(lines) if line.strip() == "Options:"
                  and not line[:1].isspace()), None)
    if start is not None:
        end = next((i for i in range(start, len(lines))
                    if lines[i] and not lines[i][:1].isspace()
                    and lines[i].rstrip().endswith(":")), len(lines))
        lines = lines[start:end]
    found: set[str] = set()
    for line in lines:
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent > 6 or not _FLAG.match(stripped):
            continue
        head = re.split(r"\s{2,}", stripped, maxsplit=1)[0]
        for piece in head.split(","):
            word = re.split(r"[\s<\[=]", piece.strip(), maxsplit=1)[0]
            if _FLAG.fullmatch(word):
                found.add(word)
    return frozenset(found)


@dataclass(frozen=True)
class HelpDiff:
    """One `--help` compared with the catalog. `missing` is set when the command is not
    on PATH, `error` when its help could not be read."""

    help: Help
    version: str | None = None
    removed: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    gone: tuple[str, ...] = ()
    missing: bool = False
    error: str | None = None

    @property
    def command(self) -> str:
        return " ".join(self.help.command)


@dataclass(frozen=True)
class Diff:
    """A harness's `--help` texts against its catalog. `removed` flags (a row's spelling
    the help no longer lists) fail; `added` (listed but unknown to the catalog) and
    `gone` (an `others` flag no longer listed) warn."""

    harness: str
    helps: tuple[HelpDiff, ...]

    @property
    def missing(self) -> bool:
        return any(h.missing for h in self.helps)

    @property
    def clean(self) -> bool:
        return all(not (h.removed or h.added or h.gone or h.missing or h.error)
                   for h in self.helps)


Reader = Callable[[Sequence[str]], str | None]


def diff(harness: str, *, read: Reader | None = None) -> Diff:
    """Run each `--help` the catalog of `harness` is checked against and compare the
    flags it lists with the catalog's: each mode's spellings of its rows (hidden rows
    skipped, and rows whose `since` is newer than the installed version), their
    aliases, and the help's `others`. turns' host grammar is not diffed.

    `read(argv)` returns a command's output, or None when the command is not on PATH; it
    raises `OSError` or `subprocess.SubprocessError` when the command cannot run. The
    default runs it with `moxi.proc.run`."""
    catalog = load(harness)
    read = read or _run_help
    versions: dict = {}
    return Diff(harness, tuple(_diff_one(catalog, help_, read, versions)
                               for help_ in catalog.helps))


def _diff_one(catalog: Catalog, help_: Help, read: Reader, versions: dict) -> HelpDiff:
    import subprocess

    exe = help_.command[0]
    try:
        text = read(list(help_.command))
        if text is None:
            return HelpDiff(help_, missing=True)
        if exe not in versions:
            versions[exe] = read([exe, "--version"])
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as exc:
        return HelpDiff(help_, error=f"{' '.join(help_.command)} could not run: {exc}")
    found = help_flags(text)
    if not found:
        return HelpDiff(help_, error=f"{' '.join(help_.command)} lists no flags")
    match = _VERSION.search(versions[exe] or "")
    version = match.group(0) if match else None
    expected: set = set()
    known: set = set(help_.others)
    for row_ in catalog:
        for mode in help_.modes:
            spelling = row_.modes.get(mode)
            if spelling is None:
                continue
            known.add(spelling.flag)
            if spelling.flag == row_.flag:
                known.update(row_.aliases)
            if row_.hidden or row_.retired is not None or _newer(row_.since, version):
                continue
            expected.add(spelling.flag)
    return HelpDiff(help_, version=version,
                    removed=tuple(sorted(expected - found)),
                    added=tuple(sorted(found - known)),
                    gone=tuple(sorted(help_.others - found)))


def _newer(since: str | None, version: str | None) -> bool:
    if since is None or version is None:
        return False
    return _version_key(since) > _version_key(version)


def _version_key(text: str) -> tuple:
    return tuple(int(part) for part in text.split("."))


def _run_help(argv: Sequence[str]) -> str | None:
    import shutil

    from moxi import proc

    exe = shutil.which(argv[0])
    if exe is None:
        return None
    done = proc.run([exe, *argv[1:]], timeout=HELP_TIMEOUT)
    return (done.stdout or b"").decode("utf-8") + (done.stderr or b"").decode("utf-8")


# -- describing -----------------------------------------------------------------------

def describe(row_: Row, available: Callable[[str], bool] | None = None) -> dict:
    """One row as plain data, for `moxi launcher catalog --json`. `option` is the
    launcher's option for the row, None for an owned row and for `extra_flags`, which
    takes the words after `--`; `offered` is false for an owned row and for one whose
    module is absent."""
    return {
        "name": row_.name,
        "harness": row_.harness or LAUNCHER,
        "option": row_.option if row_.settable and row_.name != EXTRA else None,
        "type": row_.type,
        "default": row_.default,
        "doc": row_.doc,
        "values": list(row_.values) if row_.values else None,
        "flag": row_.flag,
        "modes": {mode: " ".join(filter(None, (s.flag, s.value)))
                  for mode, s in row_.modes.items()},
        "repeatable": row_.repeatable,
        "optional_value": row_.optional_value,
        "owned": row_.owned,
        "posture": row_.posture,
        "requires": row_.requires,
        "offered": not row_.owned and row_.offered(available),
    }


# -- helpers --------------------------------------------------------------------------

def _available(available: Callable[[str], bool] | None) -> Callable[[str], bool]:
    if available is not None:
        return available
    from . import slots

    return slots.links


def _str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _kind(value) -> str:
    for kind, name in ((bool, "a bool"), (int, "an int"), (float, "a float"),
                       (str, "a string"), (list, "a list"), (dict, "a table")):
        if isinstance(value, kind):
            return name
    return f"a {type(value).__name__}"


def _choices(row_: Row) -> str:
    return f" ({', '.join(map(repr, row_.values))})" if row_.type == "enum" else ""


def _path(name: str, value: str):
    if value == "":
        return None
    expanded = Path(value).expanduser()
    if not expanded.is_absolute():
        raise BadValue(name, f"{name} is the relative path {value!r}",
                       "give an absolute path, or one starting with ~")
    return str(expanded)


def _cli_path(text: str) -> str:
    """A path typed at a launch: `~` expanded, a relative one read against this
    process's directory."""
    if text == "":
        return text
    return str(Path(os.path.abspath(Path(text).expanduser())))
