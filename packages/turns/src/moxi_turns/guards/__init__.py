"""Guards: values that refuse a tool call before it runs, or add a line after it, and
the loader that reads them.

A guard is a `Guard`, built from plain data. It names the tool calls it covers per
harness (`calls`), whose calls (`who`: the main loop's, a subagent's, or both), an
optional in-process pre-filter on the tool's input (`match`), and either a `judge`, Python
code that decides, or a `deny` text that refuses every covered, matched call with no code.
The judge is `fn(call: ToolCall) -> None | Allow | Deny`: None or `Allow()` lets the call
run, `Allow(line)` lets it run and adds one line to what the model reads after the tool,
and `Deny(reason)` refuses it with `reason`.

Guards come from two places:

- a module's entry in the group `moxi.turns.guards`, named by the module, whose target is
  a Python module holding `GUARDS`, a tuple of `Guard` values. The guard's id is
  `<module>.<name>`;
- a custom file on turns' `guards.on` list: a `.toml` file of `[[guard]]` tables, each a
  data-only guard (every field but `judge`), or a `.py` file holding `GUARDS`, whose
  `judge` may name a function of the same file as `":fn"`. Its id is
  `file:<stem>.<name>`.

A guard runs only when its id or its file is on `guards.on`, or its `switch` passes the
core's switch check and reads true. A switch is a setting of the offering module: a bool
read at use, with no `env` and default false, or an entry of a switch table. One that
fails the check reads off and is a problem, listed or not. A custom file has no module, so
a `switch` there is refused.

Codex's hook matcher also selects by alias (`Agent` for `spawn_agent`, `Write` and `Edit`
for `apply_patch`), but turns matches the canonical tool name the hook input carries, so a
guard naming an alias would never cover the call. `calls.codex` holds canonical names
only: the loader refuses an alias and names the canonical name, and maps none.

A `match` regex is held to what Python's `re` and JavaScript's `RegExp` both read the
same: no inline flags, no lookbehind, no named, atomic or conditional groups, no
possessive quantifiers, no `\\A` or `\\Z`. `match_flags = "i"` makes every regex of the
guard case-blind. Both sides compile without Unicode classes (Python with `re.ASCII`).

This module imports nothing beyond the standard library when it is imported, so a guard
module that imports it pays for nothing else; the loader reaches the core only when it
runs.

It also holds turns' own `GUARDS`, the three it ships (removal, no-nested-agents and
question mode, each off by default), and `LISTENERS`, question mode's prompt reminder.
Their judges live in `removal` and `question` beside it, imported only when asked.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import re
import sys
import threading
import time
import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("moxi.turns.guards")

#: The entry-point group modules declare their guards in.
GROUP = "moxi.turns.guards"
#: turns' config key: the guard ids and custom files that are on.
ON_KEY = "guards.on"
#: turns' config key: the most any judge may take, in milliseconds.
MAX_WAIT_KEY = "guards.max_wait_ms"
#: The harnesses a guard may name in `calls`.
HARNESSES = ("claude", "codex", "opencode")
#: Whose calls a guard covers.
WHO = ("any", "main", "agents")
#: The facts a judge may ask for beyond the call.
WANTS = ("prompts",)
#: What a broken guard does with its call.
ON_ERROR = ("allow", "deny")
#: The `match_flags` a guard may give: none, or case-blind.
MATCH_FLAGS = ("", "i")
#: A judge's bound when its guard names none, in milliseconds.
DEADLINE_MS = 500
#: `guards.max_wait_ms`'s default.
MAX_WAIT_MS = 3000
#: Codex's hook-matcher aliases and the canonical tool name each selects, as Codex 0.153.0
#: spells them (`hooks/src/events/hook_names.rs`). A later Codex's new alias is a change
#: to this table.
CODEX_ALIASES = {"Agent": "spawn_agent", "Write": "apply_patch", "Edit": "apply_patch"}
#: The Codex version `CODEX_ALIASES` was read from.
CODEX_ALIASES_VERSION = "0.153.0"
#: The custom file forms.
FILE_SUFFIXES = (".toml", ".py")
#: Where a guard came from: a module's entry, or a custom file of either form.
SOURCES = ("module", "toml", "py")

_NAME = re.compile(r"[a-z0-9-]+")
_TARGET = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*:[A-Za-z_]\w*")
_OWN_TARGET = re.compile(r":[A-Za-z_]\w*")
_SWITCH = re.compile(r"[a-z0-9_-]+(\.[a-z0-9_-]+)+")
_MODULE = re.compile(r"[a-z][a-z0-9]*")


# -- the values --------------------------------------------------------------------------

@dataclass(frozen=True)
class Prompt:
    """One prompt of the current main turn, as the harness gave it: its origin kind
    (`composer`, `plugin`, ...; None where the harness gives none) and its text."""

    origin: str | None
    text: str


@dataclass(frozen=True)
class ToolCall:
    """The call a judge decides.

    `agent_id` is None on the main loop. `record_path` is the file the harness writes the
    session's records to, None where it has none. `env` is the endpoint's environment,
    which is the launch's. `prompts` holds the current main turn's prompts when the guard
    `wants` them, else nothing.
    """

    harness: str
    session_id: str | None
    agent_id: str | None
    tool: str
    tool_use_id: str | None = None
    input: Mapping[str, object] = field(default_factory=dict)
    cwd: str | None = None
    record_path: str | None = None
    env: Mapping[str, str] = field(default_factory=dict, repr=False)
    prompts: tuple[Prompt, ...] = ()


@dataclass(frozen=True)
class Allow:
    """Let the call run; `line`, when given, is added to what the model reads after it."""

    line: str | None = None


@dataclass(frozen=True)
class Deny:
    """Refuse the call; the model reads `reason`."""

    reason: str


@dataclass(frozen=True)
class Guard:
    """One guard, as plain data. Building one checks nothing: `problems()` says what is
    wrong with it, and the loader refuses a guard with any."""

    #: Lower-case letters, digits and `-`; unique within its module.
    name: str
    #: `{harness: (pattern, ...)}`; `*` in a pattern matches any run of characters.
    calls: Mapping[str, tuple[str, ...]]
    #: `any`, `main` or `agents`.
    who: str = "any"
    #: `{input field: regex}`: every field must be a string the regex finds. It must
    #: accept every call the judge could refuse; it only spares the trip.
    match: Mapping[str, str] | None = None
    #: "" or "i" (case-blind).
    match_flags: str = ""
    #: `"package.module:function"`; `":function"` in a custom `.py` file; None makes the
    #: guard data-only.
    judge: str | None = None
    #: A data-only guard's refusal.
    deny: str = ""
    #: Facts the judge needs beyond the call: `prompts`.
    wants: tuple[str, ...] = ()
    #: What a break does: `allow` lets the call through, `deny` refuses it.
    on_error: str = "allow"
    #: The judge's bound, clamped to `guards.max_wait_ms`.
    deadline_ms: int = DEADLINE_MS
    #: A setting of the offering module that turns the guard on when it reads true.
    switch: str | None = None
    #: One line for the doctor and the install text.
    doc: str = ""

    def problems(self, source: str = "module") -> list[str]:
        """What is wrong with this guard, in plain words; empty when nothing is.
        `source` is where it came from: `module`, `toml` or `py`."""
        return _problems(self, source)

    def patterns(self, harness: str) -> tuple[str, ...]:
        """The patterns it covers on `harness`; none when it names no such harness."""
        calls = self.calls if isinstance(self.calls, Mapping) else {}
        return tuple(calls.get(harness, ()))

    def deadline(self, max_wait_ms: int = MAX_WAIT_MS) -> int:
        """The judge's bound in milliseconds, clamped to `max_wait_ms`."""
        return max(1, min(int(self.deadline_ms), int(max_wait_ms)))


# -- checks ------------------------------------------------------------------------------

def _problems(g: Guard, source: str) -> list[str]:
    out: list[str] = []
    if source not in SOURCES:
        raise ValueError(f"source {source!r}: use one of {', '.join(SOURCES)}")
    if not isinstance(g.name, str) or not _NAME.fullmatch(g.name):
        out.append(f"name {g.name!r} must be lower-case letters, digits and '-'")
    out.extend(_calls_problems(g.calls))
    if g.who not in WHO:
        out.append(f"who {g.who!r} must be one of {', '.join(WHO)}")
    if g.match_flags not in MATCH_FLAGS:
        out.append(f"match_flags {g.match_flags!r} must be \"\" or \"i\"")
    if g.match is not None:
        if not isinstance(g.match, Mapping) or not g.match:
            out.append("match must be a table of input fields to regexes, or absent")
        else:
            for name, rx in g.match.items():
                if not isinstance(name, str) or not name:
                    out.append(f"match names the field {name!r}; name a field of the "
                               "tool's input")
                    continue
                why = regex_problem(rx)
                if why is not None:
                    out.append(f"match.{name}: {why}")
    if g.judge is None:
        if not isinstance(g.deny, str) or not g.deny.strip():
            out.append("a guard with no judge refuses every call it covers, so it needs "
                       "a deny text")
    elif source == "toml":
        out.append("a .toml guard is data-only and names no judge; write a .py file for "
                   "a judge")
    elif not isinstance(g.judge, str) or not (
            _TARGET.fullmatch(g.judge) or (source == "py" and _OWN_TARGET.fullmatch(g.judge))):
        own = ', or ":function" for a function of the same file' if source == "py" else ""
        out.append(f'judge {g.judge!r} must be "package.module:function"{own}')
    if not isinstance(g.deny, str):
        out.append("deny must be text")
    if not isinstance(g.wants, (tuple, list)) or any(w not in WANTS for w in g.wants):
        out.append(f"wants {g.wants!r} must be a tuple of {', '.join(WANTS)}")
    if g.on_error not in ON_ERROR:
        out.append(f"on_error {g.on_error!r} must be \"allow\" or \"deny\"")
    if (not isinstance(g.deadline_ms, int) or isinstance(g.deadline_ms, bool)
            or g.deadline_ms <= 0):
        out.append(f"deadline_ms {g.deadline_ms!r} must be a whole number of "
                   "milliseconds above 0")
    if g.switch is not None:
        if source != "module":
            out.append("a custom guard file has no module, so it can name no switch; put "
                       "the file on guards.on to turn its guards on")
        elif not isinstance(g.switch, str) or not _SWITCH.fullmatch(g.switch):
            out.append(f"switch {g.switch!r} must name a setting of the module "
                       "(a dotted key such as guards.removal)")
    if not isinstance(g.doc, str) or "\n" in g.doc:
        out.append("doc must be one line")
    return out


def _calls_problems(calls: object) -> list[str]:
    if not isinstance(calls, Mapping):
        return ["calls must be a table of harnesses to tuples of tool-name patterns"]
    out: list[str] = []
    for harness, patterns in calls.items():
        if harness not in HARNESSES:
            out.append(f"calls names the harness {harness!r}; use "
                       f"{', '.join(HARNESSES)}")
            continue
        if not isinstance(patterns, (tuple, list)):
            out.append(f"calls.{harness} must be a tuple of tool-name patterns, not "
                       f"{type(patterns).__name__}")
            continue
        for p in patterns:
            if not isinstance(p, str) or not p or any(c.isspace() for c in p):
                out.append(f"calls.{harness} holds {p!r}; a pattern is a tool name, "
                           "with * for any run of characters")
            elif harness == "codex" and p in CODEX_ALIASES:
                out.append(f"calls.codex names {p!r}, Codex's alias of "
                           f"{CODEX_ALIASES[p]!r}; name {CODEX_ALIASES[p]!r}: turns "
                           "matches the canonical tool name a Codex hook carries")
    return out


def regex_problem(rx: object) -> str | None:
    """Why `rx` is not a regex both Python's `re` and JavaScript's `RegExp` read the same,
    or None when it is."""
    if not isinstance(rx, str) or not rx:
        return f"{rx!r} is not a regex"
    try:
        re.compile(rx, re.ASCII)
    except re.error as exc:
        return f"{rx!r} does not compile: {exc}"
    i, n, in_class = 0, len(rx), False
    quantified = False
    while i < n:
        c = rx[i]
        if c == "\\":
            if not in_class and rx[i + 1:i + 2] in ("A", "Z"):
                return f"{rx!r} uses \\{rx[i + 1]}, which JavaScript lacks; use ^ or $"
            i += 2
            quantified = False
            continue
        if in_class:
            in_class = c != "]"
            i += 1
            continue
        if c == "+" and quantified:
            return f"{rx!r} uses a possessive quantifier, which JavaScript lacks"
        quantified = False
        if c == "[":
            i += 1
            if rx[i:i + 1] == "^":
                i += 1
            if rx[i:i + 1] == "]":
                return f"{rx!r} opens a class with ], which JavaScript reads as an empty " \
                       "class; write \\]"
            in_class = True
            continue
        if c == "(" and rx[i + 1:i + 2] == "?":
            kind = rx[i + 2:i + 4]
            if kind[:1] in (":", "=", "!"):
                i += 3
                continue
            if kind in ("<=", "<!"):
                return f"{rx!r} uses a lookbehind, which the shared subset leaves out"
            if kind[:1] in ("P", "<"):
                return f"{rx!r} uses a named group, which the two dialects spell apart"
            if kind[:1] == ">":
                return f"{rx!r} uses an atomic group, which JavaScript lacks"
            if kind[:1] in ("#", "("):
                return f"{rx!r} uses a comment or conditional group, which JavaScript lacks"
            return f"{rx!r} uses inline flags; set match_flags = \"i\" instead"
        if c in "*+?":
            quantified = rx[i - 1:i] != "(" if i else False
        elif c == "}":
            quantified = re.search(r"\{\d+(,\d*)?$", rx[:i]) is not None
        i += 1
    return None


def compile_match(guard: Guard) -> dict[str, re.Pattern]:
    """The guard's `match` regexes, compiled as the runner's engine reads them: ASCII
    classes, and case-blind under `match_flags = "i"`. Raises `re.error`."""
    flags = re.ASCII | (re.IGNORECASE if guard.match_flags == "i" else 0)
    return {name: re.compile(rx, flags) for name, rx in (guard.match or {}).items()}


# -- the loader --------------------------------------------------------------------------

@dataclass(frozen=True)
class Found:
    """One guard the loader read, and where from."""

    id: str
    guard: Guard
    source: str
    #: The offering module; None for a custom file.
    module: str | None = None
    #: The custom file, as the list names it; None for a module's guard.
    path: str | None = None
    #: A custom `.py` file's own namespace, where `":fn"` judges resolve.
    space: object = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class On:
    """A guard that runs, and what turned it on: `list` (`guards.on`), `switch`, or
    both."""

    found: Found
    by: tuple[str, ...]

    @property
    def id(self) -> str:
        return self.found.id

    @property
    def guard(self) -> Guard:
        return self.found.guard


@dataclass(frozen=True)
class Problem:
    """Why a guard, a list entry or a module's guards could not be read.

    `kind` is `shape` (a field is wrong), `switch` (the switch fails the core's check or
    its module's config does not load), `load` (the module's guards or the custom file do
    not load), or `missing` (a list entry naming a module that offers no guards). `on` is
    whether the guard was listed or its switch read true; a `switch` problem counts
    whether or not it was. `id` is the guard's id, or the list entry, or the module's
    name for a module whose guards do not load.
    """

    id: str
    kind: str
    reason: str
    on: bool = False


@dataclass(frozen=True)
class Loaded:
    """What the loader read: the guards that run, those available and off, and every
    problem."""

    on: tuple[On, ...] = ()
    off: tuple[Found, ...] = ()
    problems: tuple[Problem, ...] = ()
    #: How long each offering module's guards took to import, in milliseconds.
    import_ms: Mapping[str, float] = field(default_factory=dict)

    def get(self, guard_id: str) -> On | None:
        for o in self.on:
            if o.id == guard_id:
                return o
        return None


#: `(module, key) -> (value, problem)`: a switch's reading, with why it reads off when it
#: can't be read.
SwitchReader = Callable[[str, str], "tuple[bool, str | None]"]


def is_file_entry(entry: str) -> bool:
    """A `guards.on` entry naming a file: it holds a path separator or starts with `~`."""
    return entry.startswith("~") or "/" in entry or os.sep in entry \
        or bool(os.altsep and os.altsep in entry)


def load(on: Iterable[str] | None = None, *, entries: Iterable | None = None,
         switches: SwitchReader | None = None, env: Mapping[str, str] | None = None
         ) -> Loaded:
    """Read every module's guards and the custom files on the list, and say which run.

    `on` is `guards.on`, read from turns' config when None. `entries`, each with `.name`
    and `.load()`, stand in for the group (tests); `switches` reads a module's switch
    (`read_switch` by default, in `env`). Never raises."""
    if on is None:
        on = _list_setting(env)
    listed = list(dict.fromkeys(e for e in on if isinstance(e, str)))
    if entries is None:
        from moxi import modules

        entries = modules.extensions(GROUP)
    if switches is None:
        def switches(module: str, key: str) -> tuple[bool, str | None]:
            return read_switch(module, key, env=env)

    problems: list[Problem] = []
    found: list[tuple[Found, list[str]]] = []
    import_ms: dict[str, float] = {}
    loaded_modules: set[str] = set()
    by_name: dict[str, list] = {}
    for ep in entries:
        by_name.setdefault(ep.name, []).append(ep)
    ids = [e for e in listed if not is_file_entry(e)]
    for module in sorted(by_name):
        claims = by_name[module]
        why = None
        if len(claims) > 1:
            why = (f"{len(claims)} distributions declare guards named {module!r}; "
                   "uninstall all but one")
        elif not _MODULE.fullmatch(module):
            why = f"{module!r} is not a Moxi module name"
        if why is None:
            t0 = time.perf_counter()
            try:
                target = claims[0].load()
                rows = _guards_of(target)
            except Exception as exc:  # noqa: BLE001 - a broken module costs its own guards
                why = f"its guards do not load: {type(exc).__name__}: {exc}"
            import_ms[module] = (time.perf_counter() - t0) * 1000
        if why is not None:
            problems.append(Problem(module, "load", why))
            problems.extend(Problem(i, "load", why, on=True) for i in ids
                            if i.partition(".")[0] == module)
            loaded_modules.add(module)
            continue
        loaded_modules.add(module)
        found.extend(_named(rows, module, problems, source="module",
                            prefix=f"{module}.", path=None, space=None))
    known = {f.id for f, _ in found}
    for entry in listed:
        module = entry.partition(".")[0]
        if is_file_entry(entry):
            found.extend(_file_guards(entry, problems))
        elif module not in loaded_modules:
            problems.append(Problem(entry, "missing", f"guards.on names {entry}, but "
                                    f"module {module} offers no guards: it is not "
                                    "installed, or declares none", on=True))
        elif entry not in known and not any(p.id == entry for p in problems):
            problems.append(Problem(entry, "load", f"guards.on names {entry}, but module "
                                    f"{module} declares no such guard", on=True))
    return _select(found, listed, switches, problems, import_ms)


def _list_setting(env) -> list[str]:
    try:
        from moxi.config import ConfigError

        from .. import config
    except ImportError as exc:  # pragma: no cover - turns always has its core
        log.warning("guards.on not read: %s", exc)
        return []
    try:
        return list(config.load(env)[ON_KEY])
    except ConfigError as exc:
        log.warning("turns' config does not read (%s); no guard is on by the list", exc)
        return []


def _guards_of(target: object) -> list[object]:
    rows = getattr(target, "GUARDS", None)
    if not isinstance(rows, (tuple, list)):
        raise ValueError("it defines no GUARDS tuple")
    return list(rows)


def _named(rows: list[object], owner: str, problems: list[Problem], *, source: str,
           prefix: str, path: str | None, space: object) -> list[tuple[Found, list[str]]]:
    """`rows` as found guards with ids `<prefix><name>`, each with what is wrong beyond
    its own fields (a name declared twice). A row that is not a `Guard` is a problem of
    `owner`'s."""
    guards: list[Guard] = []
    for i, g in enumerate(rows):
        if isinstance(g, Guard):
            guards.append(g)
        else:
            problems.append(Problem(owner, "load", f"GUARDS item {i} is a "
                                    f"{type(g).__name__}, not a Guard"))
    names = [g.name if isinstance(g.name, str) else repr(g.name) for g in guards]
    out = []
    for g, name in zip(guards, names):
        twice = [f"its name {name!r} is declared twice"] if names.count(name) > 1 else []
        out.append((Found(f"{prefix}{name}", g, source,
                          owner if source == "module" else None, path, space), twice))
    return out


def _file_guards(entry: str, problems: list[Problem]) -> list[tuple[Found, list[str]]]:
    path = Path(entry).expanduser()
    if not path.is_absolute():
        problems.append(Problem(entry, "load", "not an absolute path; write it from / or ~",
                                on=True))
        return []
    if path.suffix not in FILE_SUFFIXES:
        problems.append(Problem(entry, "load", "a custom guard file is a .toml or a .py "
                                "file", on=True))
        return []
    prefix = f"file:{path.stem}."
    try:
        if path.suffix == ".toml":
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
            rows = _toml_guards(data)
            space = None
        else:
            space = _exec_file(path)
            rows = _guards_of(space)
    except Exception as exc:  # noqa: BLE001 - a broken file costs its own guards
        problems.append(Problem(entry, "load", f"the file does not load: "
                                f"{type(exc).__name__}: {exc}", on=True))
        return []
    source = "toml" if path.suffix == ".toml" else "py"
    return _named(rows, entry, problems, source=source, prefix=prefix, path=entry,
                  space=space)


#: The fields a `.toml` guard may set: every field but `judge`, which is refused with a
#: word on why.
_TOML_FIELDS = frozenset(f for f in Guard.__dataclass_fields__) - {"judge"}


def _toml_guards(data: Mapping) -> list[Guard]:
    tables = data.get("guard")
    extra = sorted(set(data) - {"guard"})
    if extra:
        raise ValueError(f"it holds {', '.join(extra)}; a guard file holds only [[guard]] "
                         "tables")
    if not isinstance(tables, list) or not all(isinstance(t, dict) for t in tables):
        raise ValueError("it holds no [[guard]] tables")
    out: list[Guard] = []
    for i, t in enumerate(tables):
        unknown = sorted(set(t) - _TOML_FIELDS - {"judge"})
        if unknown:
            raise ValueError(f"guard {i + 1} has unknown field(s) {', '.join(unknown)}")
        for required in ("name", "calls"):
            if required not in t:
                raise ValueError(f"guard {i + 1} has no {required}")
        values = dict(t)
        if isinstance(values["calls"], dict):
            values["calls"] = {h: tuple(p) if isinstance(p, list) else p
                               for h, p in values["calls"].items()}
        if isinstance(values.get("wants"), list):
            values["wants"] = tuple(values["wants"])
        out.append(Guard(**values))
    return out


def _exec_file(path: Path) -> object:
    stem = re.sub(r"\W", "_", path.stem)
    name = f"moxi_turns_guard_file_{stem}_{abs(hash(str(path))):x}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"{path} cannot be read as Python")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _select(found: list[tuple[Found, list[str]]], listed: list[str],
            switches: SwitchReader, problems: list[Problem],
            import_ms: dict[str, float]) -> Loaded:
    on: list[On] = []
    off: list[Found] = []
    seen: dict[str, str] = {}
    listed_set = set(listed)
    for f, before in found:
        g = f.guard
        by: list[str] = []
        if f.id in listed_set or (f.path is not None and f.path in listed_set):
            by.append("list")
        if f.source == "module" and isinstance(g.switch, str) and _SWITCH.fullmatch(g.switch):
            try:
                value, why = switches(f.module, g.switch)
            except Exception as exc:  # noqa: BLE001 - a switch read never breaks the load
                value, why = False, f"{type(exc).__name__}: {exc}"
            if why is not None:
                problems.append(Problem(f.id, "switch", f"switch {f.module} {g.switch} "
                                        f"reads off: {why}", on=bool(by)))
            elif value:
                by.append("switch")
        shape = before + g.problems(f.source)
        if f.id in seen:
            shape.insert(0, f"its id is taken by {seen[f.id]}")
        else:
            seen[f.id] = f.path or f.module or f.id
        if shape:
            problems.append(Problem(f.id, "shape", "; ".join(shape), on=bool(by)))
            continue
        if by:
            on.append(On(f, tuple(by)))
        else:
            off.append(f)
    return Loaded(tuple(on), tuple(off), tuple(problems), dict(import_ms))


def read_switch(module: str, key: str, *, env: Mapping[str, str] | None = None,
                schema=None) -> tuple[bool, str | None]:
    """Module `module`'s switch `key` as the core reads it: `(value, None)`, or
    `(False, why)` when it fails the core's switch check (a bool read at use, no `env`,
    default false; or a switch table's entry) or the module's config does not load.
    `schema` stands in for the module's own (tests)."""
    from moxi import config as moxi_config
    from moxi.units import switch_problem

    try:
        if schema is None:
            schema = _schema_of(module)
    except Exception as exc:  # noqa: BLE001 - a module that can't be read reads off
        return False, f"its settings do not read: {exc}"
    problem = switch_problem(schema, key)
    if problem is not None:
        return False, problem
    try:
        return bool(moxi_config.load(module, schema, env=env)[key]), None
    except (moxi_config.ConfigError, KeyError) as exc:
        return False, f"its config does not load: {exc}"


def _schema_of(module: str):
    if module == "turns":
        from .. import config

        return config.schema()
    from moxi import config as moxi_config
    from moxi import modules

    found = modules.load(module)
    if found.state != modules.PRESENT:
        raise LookupError(f"module {module} is {found.state}")
    return moxi_config.schema_for(found)


# -- judges ------------------------------------------------------------------------------

_judges: dict[str, Callable] = {}
_judges_lock = threading.Lock()


def judge_of(found: Found) -> Callable[[ToolCall], object]:
    """The function `found`'s `judge` names, imported at the first ask. Raises when the
    guard has none or the target does not import."""
    target = found.guard.judge
    if not isinstance(target, str):
        raise LookupError(f"guard {found.id} has no judge")
    module_name, _, attr = target.partition(":")
    if not module_name:
        if found.space is None:
            raise LookupError(f"guard {found.id} names {target!r}, which needs its own "
                              ".py file")
        return getattr(found.space, attr)
    key = f"{found.id}={target}"
    with _judges_lock:
        fn = _judges.get(key)
        if fn is None:
            fn = getattr(importlib.import_module(module_name), attr)
            _judges[key] = fn
        return fn


# -- turns' own guards -------------------------------------------------------------------

#: The removal guard's pre-filter on `command`: an `rm` or `rmdir` word, any case, with any
#: run of `\`, `'`, `"`, `{`, `}` and `$` between its letters, since the judge also reads
#: the command with those deleted. It passes every command the judge could refuse.
REMOVAL_MATCH = r"""\b(r[\\'"{}$]*m([\\'"{}$]*d[\\'"{}$]*i[\\'"{}$]*r)?)(?![A-Za-z_])"""
#: The no-nested-agents guard's refusal.
NO_NESTED_AGENTS_DENY = (
    "Subagents may not start subagents. Finish what you can, then end your turn with a "
    "report to your parent agent saying what else needs doing and why. Your parent decides "
    "whether to start another worker."
)

#: turns' shipped guards, each off until its switch or `guards.on` turns it on. The group
#: entry `turns = "moxi_turns.guards"` names this module.
GUARDS = (
    Guard(name="removal", calls={"claude": ("Bash",)}, match={"command": REMOVAL_MATCH},
          match_flags="i", judge="moxi_turns.guards.removal:judge", on_error="allow",
          switch="guards.removal",
          doc="Refuse shell removals Claude Code would stop for the user's approval even in "
              "bypass mode."),
    Guard(name="no-nested-agents",
          calls={"claude": ("Agent", "Task"), "codex": ("spawn_agent",)}, who="agents",
          deny=NO_NESTED_AGENTS_DENY, switch="guards.no_nested_agents",
          doc="Stop subagents from starting subagents of their own."),
    Guard(name="question-mode", calls={"claude": ("AskUserQuestion",)},
          judge="moxi_turns.guards.question:judge", wants=("prompts",), on_error="deny",
          switch="guards.question_mode",
          doc="Let agents ask the person questions only in a turn where they typed something "
              "about a question."),
)

#: turns' listener rows (the group `moxi.turns.listeners`): question mode's reminder.
LISTENERS = (
    (("prompt",), "moxi_turns.guards.question:on_prompt"),
)


__all__ = ["GROUP", "ON_KEY", "MAX_WAIT_KEY", "HARNESSES", "WHO", "WANTS", "ON_ERROR",
           "MATCH_FLAGS", "DEADLINE_MS", "MAX_WAIT_MS", "CODEX_ALIASES",
           "CODEX_ALIASES_VERSION", "FILE_SUFFIXES", "SOURCES", "Prompt", "ToolCall",
           "Allow", "Deny", "Guard", "Found", "On", "Problem", "Loaded", "SwitchReader",
           "regex_problem", "compile_match", "is_file_entry", "load", "read_switch",
           "judge_of", "REMOVAL_MATCH", "NO_NESTED_AGENTS_DENY", "GUARDS", "LISTENERS"]
