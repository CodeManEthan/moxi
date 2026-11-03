"""Seats and project presets: the user's `presets.toml`, checked, and one launch's values
resolved over the flag catalog.

The file is `CONFIG/presets.toml` (`moxi.paths.Layout("launcher").config_dir()`), read in
place by `moxi.config` and edited by the user. Moxi ships no presets.

    [defaults]                 # the user's per-row defaults; posture rows live here
    permission_mode = "bypassPermissions"

    [seat.planner]             # a seat: who
    harness = "claude"
    identity = "planner"       # comms name; ignored with a warning when comms is absent
    home = "moxi"              # a path, or a project key when projects is installed
    instructions_file = "~/agents/planner.md"

    [project.moxi]             # a project preset: where
    project = "moxi"           # or cwd = "/path"
    harness = "codex"

Every key of a seat or project preset is a row of the preset's harness or a launcher row
(`moxi launcher catalog` lists them); a seat may also name its `home`. A `[defaults]` key
is a launcher row or a row of any harness, and applies to launches of the harnesses that
have it. A seat's standing text is `instructions` or `instructions_file`, never both. A
preset names at most one place: `cwd`, `project`, `scratch` or (a seat's) `home`. A `home`
holding `/` or `\\`, or starting with `~` or `.`, is a path; any other is a project key.

Resolving one launch. Each row's value is, each later one winning: the catalog's shipped
default; `[defaults]`; the one preset named, if any; the flags typed. A launch names at
most one preset, a seat or a project preset, and never draws values from a second. Where
the session starts is taken whole from the latest of those that names a place. A row the
launch leaves out (`--leave-out`) is taken out of `[defaults]` and the preset first, so it
holds its shipped default whatever they set. A posture row whose value, from the preset,
typed or left out, differs from its effective default (the `[defaults]` value, else the
shipped one) needs `--off-posture`, else the launch is refused naming both values.

Projects, when installed, are read only here and only when a launch or a check asks:
`get(key)` for a project key, `for_path(dir)` for a directory's key, `Project.agents` for
the project listing. Never from a listener.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from moxi import exit as codes
from moxi import modules
from moxi.config import ConfigError, Schema
from moxi.config import load as load_config
from moxi.paths import Layout

from . import catalog, slots
from .catalog import BadValue, NeedsModule, Owned, Refused

MODULE = "launcher"
FILE = "presets.toml"
#: The preset kinds, as the file's tables and the launch log name them.
KINDS = ("seat", "project")
#: The seat key that is not a catalog row: where the seat starts, a path or a project key.
HOME = "home"
#: The row of config overrides, `key=value` each; one keyed as a posture row is refused.
CONFIG = "config"
#: The texts a launch takes inline or from a file: the pairs of rows that spell each.
TWO_SPELLINGS = (("brief", "brief_file"), ("instructions", "instructions_file"))
#: The rows (and the seat's `home`) that name where a session starts; a layer names one.
PLACE = ("cwd", "project", "scratch", HOME)
#: The launcher's share of Codex's start context, in characters. A Codex seat whose
#: identity block and instructions pass it is warned about at check. turns gives the
#: modules that answer at a Codex session's start 9000 characters between them, an even
#: share each, and comms answers there too, so the launcher counts on half.
CODEX_START_CEILING = 4500
#: turns' Codex host, which a Codex launch with turns runs under.
CODEX_HOST = "moxi-turns-codex-host"
#: The comms interface whose names the launcher calls: `identity_state`, and the Codex
#: wrapper `moxi comms codex` that ships with it. An older comms offers neither.
COMMS_INTERFACE = 2
#: What to install for that comms.
COMMS_NEEDED = "moxi-comms 0.2"
#: The start postures every install takes; `with:<identity>` needs comms.
POSTURES = ("operator", "go")
WITH = "with:"

SHIPPED, DEFAULTS, PRESET, TYPED = "shipped", "defaults", "preset", "typed"
#: The source of a row the launch leaves out: it holds its shipped default.
LEFT_OUT = "left-out"

_SCHEMA_TEXT = """
[defaults]
type = "table"
default = {}
doc = "The user's per-row defaults, under every preset; posture rows live here."
read = "use"

[seat]
type = "table"
default = {}
doc = "The seats, one table each: who a session is."
read = "use"

[project]
type = "table"
default = {}
doc = "The project presets, one table each: where a session works."
read = "use"
"""

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_schema: Schema | None = None


# -- refusals -------------------------------------------------------------------------

class UnknownPreset(Refused):
    """A launch names a preset the file does not hold."""

    code = "no-preset"

    def __init__(self, kind: str, name: str):
        self.kind, self.name = kind, name
        what = "seat" if kind == "seat" else "project preset"
        super().__init__(None, f"no {what} is named {name!r}",
                         "moxi launcher presets list shows them")


class BadPreset(Refused):
    """The preset a launch names, or the `[defaults]` under it, fails its check. Carries
    the first problem; exits as that problem does."""

    code = "bad-preset"

    def __init__(self, problem: "Problem"):
        self.found = problem
        self.exit_code = problem.exit_code
        super().__init__(problem.key, f"{problem.where}: {problem.problem}", problem.hint)


class OffPosture(Refused):
    """A posture row set off its effective default without `--off-posture`."""

    code = "off-posture"

    def __init__(self, row: str, value: object, default: object, source: str):
        self.value, self.default, self.source = value, default, source
        whose = {TYPED: "typed", LEFT_OUT: "left out"}.get(source, "the preset's")
        super().__init__(row, f"{row} {_show(value)} ({whose}) is off your default "
                              f"{_show(default)}",
                         "launch with --off-posture to allow it, or set it in [defaults]")


class NotYet(Refused):
    """A launch shape the links it needs do not take yet."""

    code = "not-yet"


class NeedsProjects(Refused):
    """A project key with projects not installed."""

    code = "needs-module"

    def __init__(self, what: str):
        super().__init__("project", f"{what} names a project key, which needs projects",
                         "install moxi-projects, or give a path")


class UnknownProject(Refused):
    """projects has no project by this key."""

    code = "no-project"

    def __init__(self, key: str, fix: str | None):
        self.key = key
        super().__init__("project", f"no project is named {key!r}",
                         fix or "moxi projects list shows them")


class ProjectsUnreadable(Refused):
    """projects' store could not be read, or was written by a newer projects."""

    code = "projects-unreadable"

    def __init__(self, reason: str, fix: str | None):
        super().__init__("project", f"projects can't be read: {reason}",
                         fix or "moxi doctor shows what is wrong")


class ProjectsBusy(ProjectsUnreadable):
    """projects' store stayed locked past its wait."""

    code = "busy"
    exit_code = codes.TEMPFAIL


# -- the file -------------------------------------------------------------------------

@dataclass(frozen=True)
class Preset:
    """One seat or project preset, as the file writes it."""

    kind: str
    name: str
    values: Mapping[str, object]

    @property
    def where(self) -> str:
        return f"{self.kind}.{self.name}"

    @property
    def harness(self) -> object:
        """The harness the preset names, or None."""
        return self.values.get("harness")


@dataclass(frozen=True)
class Presets:
    """The presets file at one reading. A file that does not exist reads empty."""

    path: Path
    defaults: Mapping[str, object]
    seats: Mapping[str, Preset]
    projects: Mapping[str, Preset]

    def table(self, kind: str) -> Mapping[str, Preset]:
        if kind not in KINDS:
            raise ValueError(f"no preset kind {kind!r}; the kinds are {', '.join(KINDS)}")
        return self.seats if kind == "seat" else self.projects

    def get(self, kind: str, name: str) -> Preset:
        """The preset `name` of `kind`, or `UnknownPreset`."""
        found = self.table(kind).get(name)
        if found is None:
            raise UnknownPreset(kind, name)
        return found

    def all(self) -> list[Preset]:
        """Every preset: the seats, then the project presets, each by name."""
        return [*(self.seats[n] for n in sorted(self.seats)),
                *(self.projects[n] for n in sorted(self.projects))]


def schema() -> Schema:
    """The schema `moxi.config` reads the file with: three tables, whose contents this
    module checks against the catalog."""
    global _schema
    if _schema is None:
        _schema = Schema.parse(_SCHEMA_TEXT, source=f"{__package__} presets schema")
    return _schema


def path(env: Mapping[str, str] | None = None) -> Path:
    """Where the user's presets live: `CONFIG/presets.toml`."""
    return Layout(MODULE).config_dir(env) / FILE


def read(file: str | os.PathLike | None = None, *,
         env: Mapping[str, str] | None = None) -> Presets:
    """Read the presets file (`file`, else `path(env)`) in place. Raises
    `moxi.config.ConfigError` naming the file when it is not TOML, holds a table other
    than `[defaults]`, `[seat.*]` and `[project.*]`, or a preset that is not a table."""
    where = Path(file) if file is not None else path(env)
    settings = load_config(MODULE, schema(), env=env, path=where)
    tables = {}
    for kind in KINDS:
        out = {}
        for name, values in settings[kind].items():
            if not isinstance(values, dict):
                raise ConfigError(where, f"{kind}.{name}", "is a value, not a preset's "
                                  "table", f"write it as [{kind}.{name}] with its keys "
                                  "under it")
            out[name] = Preset(kind, name, MappingProxyType(dict(values)))
        tables[kind] = MappingProxyType(out)
    return Presets(where, MappingProxyType(dict(settings["defaults"])), tables["seat"],
                   tables["project"])


# -- checking -------------------------------------------------------------------------

ERROR, WARNING = "error", "warning"


@dataclass(frozen=True)
class Problem:
    """One thing wrong with a preset. `where` is `defaults`, `seat.<name>` or
    `project.<name>`; `key` the key at fault, if one is; `level` `error` or `warning`."""

    where: str
    key: str | None
    problem: str
    hint: str
    code: str = "bad-value"
    level: str = ERROR
    exit_code: int = codes.USAGE

    @classmethod
    def of(cls, where: str, key: str | None, exc: Refused, level: str = ERROR
           ) -> "Problem":
        return cls(where, key, exc.problem, exc.hint, exc.code, level, exc.exit_code)

    def as_json(self) -> dict:
        return {"where": self.where, "key": self.key, "problem": self.problem,
                "hint": self.hint, "code": self.code, "level": self.level}

    def __str__(self) -> str:
        key = f" {self.key}:" if self.key else ""
        return f"{self.level}: {self.where}:{key} {self.problem}; {self.hint}"


def check(file: str | os.PathLike | None = None, *, env: Mapping[str, str] | None = None,
          available: Callable[[str], bool] | None = None,
          presets: Presets | None = None) -> list[Problem]:
    """`moxi launcher presets check`: every preset and `[defaults]`, checked. Errors and
    warnings, in file order; none means the file is good. Types and values come from the
    catalog; a row whose module is absent fails with "needs <module>"; a project key must
    be known to projects; an `instructions_file` must be readable; an `agent` role must be
    found among the harness's role files. A `moxi.config.ConfigError` (the file is not
    TOML, or holds a table it should not) is raised, not returned."""
    avail = _avail(available)
    found = presets if presets is not None else read(file, env=env)
    defaults, problems = _check_defaults(found.defaults, avail)
    # An empty harness is unset, as `resolve` reads it: the shipped default.
    base_harness = defaults.get("harness") or catalog.launcher()["harness"].default
    for preset in found.all():
        values, more = _check_preset(preset, base_harness, avail)
        problems += more
        if not any(p.level == ERROR for p in more):
            problems += _value_rules(preset, values, defaults, base_harness, avail)
        if not any(p.level == ERROR for p in more):
            problems += _check_deep(preset, values, defaults, base_harness, avail, env)
    return problems


def _value_rules(preset: Preset, values: Mapping[str, object],
                 defaults: Mapping[str, object], base_harness: str,
                 avail) -> list[Problem]:
    """The rules between rows (`catalog.refuse_values`) over what a launch of the preset
    resolves to with nothing typed: the shipped defaults, `[defaults]`, then the preset."""
    harness = preset.harness if preset.harness in catalog.HARNESSES else base_harness
    rows = catalog.offered(harness, avail)
    effective = {name: row.default for name, row in rows.items()}
    effective.update({k: v for k, v in defaults.items() if k in rows})
    effective.update({k: v for k, v in values.items() if k in rows})
    try:
        catalog.refuse_values(harness, effective, chosen=values, available=avail)
    except Refused as exc:
        return [Problem.of(preset.where, exc.row, exc)]
    return []


def _check_defaults(table: Mapping[str, object], avail) -> tuple[dict, list[Problem]]:
    """`[defaults]`: each key a launcher row or a row of some harness, its value checked
    against each such row."""
    values: dict = {}
    problems: list[Problem] = []
    where = "defaults"
    for key, value in table.items():
        rows = ([catalog.launcher()[key]] if key in catalog.launcher() else
                [catalog.load(h)[key] for h in catalog.HARNESSES if key in catalog.load(h)])
        if not rows:
            problems.append(Problem(where, key, f"{key} is not a launcher row or a row of "
                                    "any harness", "moxi launcher catalog lists the rows",
                                    "unknown-key"))
            continue
        problem = None
        for row in rows:
            problem = _row_problem(where, key, row, value, avail)
            if problem is not None:
                break
            values[key] = _checked(row, value)
        if problem is None and key in values:
            # A default reaches every launch of each harness that takes the row.
            problem = _passed_on_problem(where, key, values[key],
                                         catalog.HARNESSES if key == catalog.EXTRA
                                         else [h for h in catalog.HARNESSES
                                               if key in catalog.load(h)])
        if problem is not None:
            values.pop(key, None)
            problems.append(problem)
    problems += _check_place(where, values, allow_home=False)
    return values, problems


def _passed_on_problem(where: str, key: str, value: object,
                       harnesses: Sequence[str]) -> Problem | None:
    """The refusal every launch of one of `harnesses` would meet at compose for `value`
    of `key`: flags passed on (`extra_flags`) that spell an owned or a posture row, or
    stand in for one, and config overrides (`config`) keyed as a posture row."""
    for harness in harnesses:
        try:
            if key == catalog.EXTRA:
                catalog.refuse_passthrough(harness, value)
            elif key == CONFIG:
                catalog.refuse_posture_keys(harness, value)
        except Owned as exc:
            return Problem.of(where, key, exc)
    return None


def _check_preset(preset: Preset, base_harness: str, avail,
                  harness: str | None = None) -> tuple[dict, list[Problem]]:
    """One preset's keys against its harness's rows and the launcher's. `harness`, when
    given, is the harness the launch uses, which may differ from the preset's own."""
    where = preset.where
    problems: list[Problem] = []
    if not _NAME.fullmatch(preset.name):
        problems.append(Problem(where, None, f"{preset.name!r} is not a preset name",
                                "name it with letters, digits, ., _ and -, starting with "
                                "a letter or digit", "bad-name"))
    if harness is None:
        harness = preset.harness if preset.harness is not None else base_harness
        if harness not in catalog.HARNESSES:
            problems.append(Problem(where, "harness", f"harness cannot be {harness!r}",
                                    "set it to one of "
                                    + ", ".join(map(repr, catalog.HARNESSES))))
            return {}, problems
    values: dict = {}
    for key, value in preset.values.items():
        if key == HOME and preset.kind == "seat":
            if not isinstance(value, str) or not value.strip():
                problems.append(Problem(where, key, "home is not a path or a project key",
                                        "set it to a path, or a project key"))
                continue
            values[key] = value
            continue
        row = catalog.row(harness, key)
        if row is None:
            extra = " or the seat's home" if preset.kind == "seat" else ""
            problems.append(Problem(where, key, f"{key} is not a {harness} row or a "
                                    "launcher row", f"moxi launcher catalog {harness} "
                                    f"lists them{extra}", "unknown-key"))
            continue
        problem = _row_problem(where, key, row, value, avail, harness)
        if problem is not None:
            problems.append(problem)
            continue
        values[key] = _checked(row, value)
        problem = _passed_on_problem(where, key, values[key], (harness,))
        if problem is not None:
            problems.append(problem)
    for inline, from_file in TWO_SPELLINGS:
        if values.get(inline) and values.get(from_file):
            problems.append(Problem(where, inline, f"both {inline} and {from_file} are "
                                    "set", "keep one of them", f"two-{inline}"))
    problems += _check_place(where, values, allow_home=preset.kind == "seat")
    posture = values.get("posture")
    if posture:
        problem = _posture_problem(where, posture, avail)
        if problem is not None:
            problems.append(problem)
    if values.get("identity") and not avail("comms"):
        problems.append(Problem(where, "identity", "identity is ignored without comms",
                                "install moxi-comms, or leave identity out",
                                "needs-module", WARNING))
    return values, problems


def _row_problem(where, key, row, value, avail, harness=None) -> Problem | None:
    if row.retired is not None:
        return Problem(where, key, f"{key} is retired", row.retired, "retired")
    if row.owned:
        return Problem(where, key, f"{key} is set by the launcher itself",
                       f"leave {key} out", "owned", ERROR, codes.REFUSED)
    why = row.unoffered(avail, harness)
    if why is not None:
        return Problem.of(where, key, NeedsModule(row, *why))
    try:
        row.check(value)
    except BadValue as exc:
        return Problem.of(where, key, exc)
    return None


def _check_place(where: str, values: Mapping[str, object], *, allow_home: bool
                 ) -> list[Problem]:
    named = [key for key in PLACE if catalog.is_set(values.get(key))
             and (key != HOME or allow_home)]
    if len(named) > 1:
        return [Problem(where, named[1], f"{' and '.join(named)} each name where the "
                        "session starts", "keep one of them", "two-places")]
    return []


def _posture_problem(where: str, posture: object, avail) -> Problem | None:
    if not isinstance(posture, str):
        return None
    if posture.startswith(WITH):
        if not _NAME.fullmatch(posture[len(WITH):].split("@", 1)[0] or "-"):
            return Problem(where, "posture", f"posture {posture!r} names no identity",
                           "write it as with:<identity>")
        if not avail("comms"):
            return Problem(where, "posture", "the with: posture needs comms",
                           "install moxi-comms, or use operator or go", "needs-module",
                           ERROR, codes.REFUSED)
        return None
    if posture not in POSTURES:
        return Problem(where, "posture", f"posture cannot be {posture!r}",
                       "set it to operator, go, or with:<identity>")
    return None


def _check_deep(preset: Preset, values: Mapping[str, object],
                defaults: Mapping[str, object], base_harness: str, avail,
                env) -> list[Problem]:
    """The checks that read more than the file: projects, the instructions file, the
    agent role, and the Codex start-context ceiling."""
    where = preset.where
    problems: list[Problem] = []
    harness = values.get("harness", base_harness)
    root = None
    key = _project_key_of(values)
    if key is not None:
        try:
            project = _get_project(key, where, avail)
            root = project.root
        except Refused as exc:
            problems.append(Problem.of(where, "project" if "project" in values else HOME,
                                       exc))
    elif values.get(HOME):
        root = _home_path(str(values[HOME]), where, problems)
    elif values.get("cwd"):
        root = values["cwd"]
    text = None
    if values.get("instructions_file"):
        try:
            text = Path(str(values["instructions_file"])).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(Problem(where, "instructions_file", "instructions_file can't "
                                    f"be read ({exc})", "point it at a readable text file",
                                    "unreadable", ERROR, codes.REFUSED))
    elif values.get("instructions"):
        text = str(values["instructions"])
    role = values.get("agent")
    if role:
        found = roles(harness, cwd=root, env=env)
        if role not in found:
            problems.append(Problem(where, "agent", f"agent role {role!r} is not among "
                                    f"{harness}'s role files",
                                    "add the role's file to the harness's agents folder, "
                                    "or fix the name", "no-role", ERROR, codes.REFUSED))
    if harness == "codex" and text is not None:
        size = _codex_start_size(values, defaults, root, text, avail, env)
        if size > CODEX_START_CEILING:
            problems.append(Problem(where, "instructions", f"the identity block and "
                                    f"instructions come to {size} characters, past the "
                                    f"{CODEX_START_CEILING} of Codex start context turns "
                                    "gives the launcher", "shorten the instructions; turns "
                                    "cuts what passes and marks the cut", "too-long",
                                    WARNING))
    return problems


def _codex_start_size(values, defaults, root, text, avail, env) -> int:
    from .compose.common import identity_block

    posture = values.get("posture") or defaults.get("posture") or ""
    identity = values.get("identity") if avail("comms") else None
    block = identity_block(
        session_id="00000000-0000-0000-0000-000000000000", cwd=str(root or os.getcwd()),
        posture=str(posture), project_key=_project_key_of(values), identity=identity,
        scope=values.get("scope") if identity else None,
        handoff_dir=Layout(MODULE).data_dir(env) / "handoffs", agent_role=None)
    return len(block) + 2 + len(text.strip())


# -- agent roles ----------------------------------------------------------------------

#: Each harness's project-scope agents folder, relative to a project's directory.
PROJECT_AGENTS = {"claude": ".claude/agents", "codex": ".codex/agents"}


def roles(harness: str, *, cwd: str | os.PathLike | None = None,
          env: Mapping[str, str] | None = None) -> frozenset[str]:
    """The agent roles `harness` can start as: the role files in its agents folder (the
    core's harness table, `moxi config moxi harnesses`; its built-in folder when the row
    leaves it empty) and, with `cwd`, in the project's own agents folder. A role is a
    file's name without `.md`, and the `name:` its front matter gives."""
    environ = os.environ if env is None else env
    folders = [_agents_folder(harness, environ)]
    if cwd is not None and harness in PROJECT_AGENTS:
        folders.append(Path(cwd) / PROJECT_AGENTS[harness])
    found: set[str] = set()
    for folder in folders:
        if folder is None:
            continue
        try:
            files = [p for p in folder.iterdir() if p.suffix in (".md", ".toml")]
        except OSError:
            continue
        for file in files:
            found.add(file.stem)
            name = _front_matter_name(file)
            if name:
                found.add(name)
    return frozenset(found)


def _agents_folder(harness: str, environ: Mapping[str, str]) -> Path | None:
    configured = ""
    try:
        core = load_config("moxi", Schema.from_resource("moxi", "settings.toml"),
                           env=environ)
        for entry in core["harnesses"]:
            if isinstance(entry, dict) and entry.get("name") == harness:
                configured = str(entry.get("agents") or "")
                break
    except (ConfigError, OSError, KeyError):
        configured = ""
    if configured:
        return Path(configured).expanduser()
    if harness == "claude":
        base = environ.get("CLAUDE_CONFIG_DIR")
        return (Path(base).expanduser() if base else Path.home() / ".claude") / "agents"
    if harness == "codex":
        return Path.home() / ".codex" / "agents"
    return None


def _front_matter_name(file: Path) -> str | None:
    try:
        with open(file, encoding="utf-8") as fh:
            if fh.readline().strip() != "---":
                return None
            for _ in range(40):
                line = fh.readline()
                if not line or line.strip() == "---":
                    return None
                head, sep, rest = line.partition(":")
                if sep and head.strip() == "name":
                    return rest.strip().strip("'\"") or None
    except (OSError, UnicodeDecodeError):
        return None
    return None


# -- projects -------------------------------------------------------------------------

def projects_api(what: str = "this", available: Callable[[str], bool] | None = None):
    """projects' API module, loaded at the point of use; `NeedsProjects` when projects is
    not installed or does not load."""
    if not _avail(available)("projects"):
        raise NeedsProjects(what)
    found = modules.load("projects", interface=1)
    if not found.ok or found.api is None:
        raise NeedsProjects(what)
    return found.api


def _projects_call(api, call: Callable, *args):
    """Call projects, its errors turned into the launcher's refusals: `NotFound` 3 with
    its hint, `Busy` 75, and `Unreadable` 3 with its reason and fix. A store written by a
    newer projects (`TooNew`) is one the launcher can't read, so it refuses like
    `Unreadable`. projects' `Busy` and `TooNew` are kinds of its `Unreadable`, so `Busy`
    is matched first."""
    busy = getattr(api, "Busy", None)
    not_found = getattr(api, "NotFound", None)
    unreadable = tuple(kind for kind in (getattr(api, "TooNew", None),
                                         getattr(api, "Unreadable", None),
                                         getattr(api, "ProjectsError", None))
                       if isinstance(kind, type))
    try:
        return call(*args)
    except Exception as exc:
        if busy is not None and isinstance(exc, busy):
            raise ProjectsBusy(getattr(exc, "reason", str(exc)), getattr(exc, "fix", None)
                               ) from None
        if not_found is not None and isinstance(exc, not_found):
            raise UnknownProject(getattr(exc, "key", args[0] if args else ""),
                                 getattr(exc, "fix", None)) from None
        if unreadable and isinstance(exc, unreadable):
            raise ProjectsUnreadable(getattr(exc, "reason", str(exc)),
                                     getattr(exc, "fix", None)) from None
        raise


def _get_project(key: str, what: str, available=None):
    api = projects_api(what, available)
    return _projects_call(api, api.get, key)


def _project_for(directory: str, available) -> object | None:
    """The project `directory` is in, or None; None without projects."""
    if not available("projects"):
        return None
    try:
        api = projects_api("this", available)
    except NeedsProjects:
        return None
    return _projects_call(api, api.for_path, directory)


@dataclass(frozen=True)
class Agent:
    """One of a project's agents and the seats whose identity is its name."""

    name: str
    seats: tuple[str, ...]

    def as_json(self) -> dict:
        return {"agent": self.name, "seats": list(self.seats)}


def project_agents(key: str, *, presets: Presets | None = None,
                   env: Mapping[str, str] | None = None,
                   available: Callable[[str], bool] | None = None
                   ) -> tuple[str, list[Agent]]:
    """`moxi launcher presets --project KEY`: the project's key as projects spells it, and
    its agents (the lead first, then the set, names only), each with the seats whose
    `identity` matches its name, or none. A seat's home plays no part."""
    project = _get_project(key, f"--project {key}", available)
    found = presets if presets is not None else read(env=env)
    by_identity: dict[str, list[str]] = {}
    for seat in found.seats.values():
        identity = seat.values.get("identity")
        if isinstance(identity, str) and identity:
            by_identity.setdefault(identity.split("@", 1)[0], []).append(seat.name)
    return project.key, [Agent(name, tuple(sorted(by_identity.get(name, ()))))
                         for name in project.agents]


# -- resolving one launch -------------------------------------------------------------

@dataclass(frozen=True)
class Request:
    """What `resolve` reads of a launch request. Any object with these attributes will
    do (the API's `LaunchRequest` among them); a missing one reads its default here.

    `typed` is the flags typed, as `catalog.parse(...).values` gives them, and `extra`
    the flags passed on (`catalog.parse(...).extra`), which go in as typed
    `extra_flags`. `left_out` names the catalog rows the launch leaves out, whatever
    `[defaults]` or the preset sets. `cwd` is the caller's directory, the session's when
    nothing names a place; None reads this process's."""

    seat: str | None = None
    project_preset: str | None = None
    typed: Mapping[str, object] = field(default_factory=dict)
    extra: Sequence[str] = ()
    off_posture: bool = False
    cwd: str | None = None
    left_out: Sequence[str] = ()


@dataclass(frozen=True)
class Resolved:
    """One launch's values, resolved: what the composers take.

    `values` holds every row offered for this launch, the launcher's then the
    harness's, by name, at its resolved value (a row nothing set holds its shipped
    default). `values["cwd"]` and `values["project"]` hold the resolved directory and
    project key. `sources` says where each value came from: `shipped`, `defaults`,
    `preset`, `typed` or `left-out`; the three place rows (`cwd`, `project`, `scratch`)
    each carry the source of the place the launch starts in, when a layer names one.
    `chosen` names the rows the preset or the typed flags set, which `catalog.fit`
    refuses when the mode can't carry them.

    The launcher's own: `cwd` (absolute; None only for a scratch launch, whose directory
    the launch makes), `project_key` (projects' spelling) and the project's `agents`,
    `identity` and `scope` (None without comms), `posture` (the start posture, "" when
    unset), `target`, and `mode`, the command the composer builds (`interactive` or
    `print` for Claude; `interactive`, `exec` or `host` for Codex). `links` names the
    optional modules the launch goes through (`turns`, `comms`, `projects`, `slots`,
    `notes`, `usage`). `warnings` are one line each, for the caller to print.
    """

    harness: str
    mode: str
    values: Mapping[str, object]
    sources: Mapping[str, str]
    chosen: frozenset[str]
    preset_kind: str | None
    preset_name: str | None
    cwd: str | None
    scratch: bool
    project_key: str | None
    agents: tuple[str, ...]
    identity: str | None
    scope: str | None
    posture: str
    target: str
    off_posture: bool
    links: frozenset[str]
    warnings: tuple[str, ...] = ()

    def values_json(self) -> dict:
        """The values the launch row records: every resolved value, with `mode` and
        `off_posture`. The composer adds what `catalog.fit` leaves out."""
        return {**self.values, "mode": self.mode, "off_posture": self.off_posture}


def harness_of(request, *, presets: Presets | None = None,
               env: Mapping[str, str] | None = None) -> str:
    """The harness a request launches before its flags are parsed: the preset's, else
    the one `[defaults]` names, else the shipped default. A caller parses the typed flags
    against it unless `--harness` was typed (`catalog.typed_harness`). A request that
    leaves `harness` out is refused when it is laid (`layered`)."""
    found = presets if presets is not None else read(env=env)
    preset = _named(request, found)
    if preset is not None and preset.harness in catalog.HARNESSES:
        return str(preset.harness)
    if found.defaults.get("harness") in catalog.HARNESSES:
        return str(found.defaults["harness"])
    return str(catalog.launcher()["harness"].default)


@dataclass(frozen=True)
class Layered:
    """One launch's rows as the layers lay them, before the place is looked up or any
    rule between rows is checked: what `resolve` builds on, and what a form filled from a
    preset shows.

    `harness` is the harness laid for. `values` holds every row offered for it at its
    layered value, after the place rule and the one-spelling rule: a place row or a
    spelling that another layer's place or spelling replaced holds its shipped default,
    as a left-out row does. `sources` says where each value came from: `shipped`,
    `defaults`, `preset`, `typed` or `left-out`. `place` is the one place named, whole,
    as the latest layer that names one writes it (`place_source`); empty, with `shipped`,
    when none does. A seat's `home` can be that place, though it is no row. `chosen`
    names the rows the preset or the typed flags set. `preset` is the preset named, its
    left-out rows taken out. `defaults` is `[defaults]` as checked, left-out rows kept,
    which the posture rule reads. `warnings` are the preset's, one line each."""

    harness: str
    preset: Preset | None
    values: Mapping[str, object]
    sources: Mapping[str, str]
    chosen: frozenset[str]
    place: Mapping[str, object]
    place_source: str
    defaults: Mapping[str, object]
    warnings: tuple[str, ...] = ()


def layered(request, *, harness: str | None = None, presets: Presets | None = None,
            env: Mapping[str, str] | None = None,
            available: Callable[[str], bool] | None = None) -> Layered:
    """Lay one launch's rows: the catalog's shipped default, then `[defaults]`, then the
    one preset named, then the flags typed, each later one winning; then the one-spelling
    rule and the place rule. A row the request leaves out (`left_out`) is taken out of
    `[defaults]` and the preset before they are laid, and before the preset is checked
    against the harness, so it holds its shipped default, is not chosen, and neither rule
    sees it. `harness`, when given, is the harness laid for in place of the one the
    layers name; the `harness` row still holds the layers' value.

    Raises `UnknownPreset`, `BadPreset`, the catalog's `NeedsModule` for a typed row this
    install does not offer, and its `BadValue`: a left-out name that is no catalog row of
    any harness, a left-out `harness`, a row both typed and left out, a harness no catalog has, two places or
    two spellings of one text in one layer. A `moxi.config.ConfigError` when the file
    can't be read."""
    avail = _avail(available)
    found = presets if presets is not None else read(env=env)
    preset = _named(request, found)
    typed = dict(getattr(request, "typed", None) or {})
    extra = list(getattr(request, "extra", None) or ())
    if extra:
        typed[catalog.EXTRA] = extra
    left_out = _left_out(getattr(request, "left_out", None) or (), typed)

    defaults, problems = _check_defaults(found.defaults, avail)
    errors = [p for p in problems if p.level == ERROR and p.code != "needs-module"]
    if errors:
        raise BadPreset(errors[0])
    from_defaults = {k: v for k, v in defaults.items() if k not in left_out}
    if preset is not None and left_out:
        preset = Preset(preset.kind, preset.name, MappingProxyType(
            {k: v for k, v in preset.values.items() if k not in left_out}))

    shipped_harness = catalog.launcher()["harness"].default
    if harness is None:
        harness = typed.get("harness") or (preset.harness if preset is not None
                                           else None) \
            or from_defaults.get("harness") or shipped_harness
    if harness not in catalog.HARNESSES:
        raise BadValue("harness", f"harness cannot be {harness!r}",
                       "set it to one of " + ", ".join(map(repr, catalog.HARNESSES)))

    chosen_preset: dict = {}
    warnings: list[str] = []
    if preset is not None:
        chosen_preset, problems = _check_preset(preset, defaults.get("harness")
                                                or shipped_harness, avail, harness=harness)
        errors = [p for p in problems if p.level == ERROR]
        if errors:
            raise BadPreset(errors[0])
        warnings += [f"{p.where}: {p.problem}" for p in problems if p.level == WARNING
                     and p.key != "identity"]

    rows = catalog.offered(harness, avail)
    for key in typed:
        if key not in rows:
            row = catalog.row(harness, key)
            if row is not None and row.settable and not row.offered(avail, harness):
                raise NeedsModule(row, *(row.unoffered(avail, harness) or ()))
            raise BadValue(key, f"{key} is not a {harness} row or a launcher row",
                           f"moxi launcher catalog {harness} lists them")

    layers = ((DEFAULTS, from_defaults), (PRESET, chosen_preset), (TYPED, typed))
    values: dict = {}
    sources: dict = {}
    for name, row in rows.items():
        values[name], sources[name] = _fresh(row.default), SHIPPED
        for source, layer in layers:
            if name in layer:
                values[name], sources[name] = _unset_as_default(row, layer[name]), source
        if name in left_out:
            sources[name] = LEFT_OUT
    _one_spelling(layers, rows, values, sources)

    # Where the session starts: taken whole from the latest layer that names a place.
    place_source, place = SHIPPED, {}
    for source, layer in layers:
        named = {k: layer[k] for k in PLACE if catalog.is_set(layer.get(k))
                 and (k != HOME or source == PRESET)}
        if named:
            place_source, place = source, named
    if len(place) > 1:
        raise BadValue(next(iter(place)), f"{' and '.join(place)} each name where the "
                       "session starts", "give one of them")
    for name in PLACE:
        if name in rows and name not in place and sources[name] != LEFT_OUT:
            values[name], sources[name] = _fresh(rows[name].default), SHIPPED

    return Layered(
        harness=harness, preset=preset, values=MappingProxyType(values),
        sources=MappingProxyType(sources),
        chosen=frozenset(k for k in (*chosen_preset, *typed) if k in rows),
        place=MappingProxyType(place), place_source=place_source,
        defaults=MappingProxyType(defaults), warnings=tuple(warnings))


def _left_out(names: Sequence[str], typed: Mapping[str, object]) -> frozenset[str]:
    """The rows a request leaves out, each a catalog row of some harness, not `harness`
    (a launch needs one), and not typed too; else the catalog's `BadValue` naming the
    first that is not."""
    for name in names:
        if name == "harness":
            raise BadValue(name, "harness can't be left out: a launch needs one",
                           "give the harness to launch, or leave it to the preset")
        if name not in catalog.launcher() and not any(
                name in catalog.load(h) for h in catalog.HARNESSES):
            raise BadValue(name, f"{name} is not a launcher row or a row of any harness, "
                                 "so it can't be left out",
                           "moxi launcher catalog lists the rows")
        if name in typed:
            raise BadValue(name, f"{name} is both given and left out",
                           f"give {name} or leave it out, not both")
    return frozenset(names)


def resolve(request, *, presets: Presets | None = None,
            env: Mapping[str, str] | None = None,
            available: Callable[[str], bool] | None = None) -> Resolved:
    """Resolve one launch's values: shipped defaults, then `[defaults]`, then the one
    preset named, then the flags typed (`layered`), each row the request leaves out
    holding its shipped default. Reads projects when a place needs it. Raises a
    `catalog.Refused`: `UnknownPreset`, `BadPreset`, `OffPosture`, `NotYet`,
    `NeedsProjects`, `UnknownProject`, `ProjectsUnreadable`, `ProjectsBusy`, or the
    catalog's `BadValue`; a `moxi.config.ConfigError` when the file can't be read."""
    avail = _avail(available)
    laid = layered(request, presets=presets, env=env, available=avail)
    preset, harness, place = laid.preset, laid.harness, laid.place
    values, sources, chosen = dict(laid.values), dict(laid.sources), laid.chosen
    off_posture = bool(getattr(request, "off_posture", False))
    warnings = list(laid.warnings)

    caller = getattr(request, "cwd", None) or os.getcwd()
    cwd, scratch, project = _place(place, caller, avail,
                                   preset.where if preset is not None else "the launch")
    if place:
        for name in ("cwd", "project", "scratch"):
            sources[name] = laid.place_source
    values["cwd"] = cwd or ""
    values["scratch"] = scratch
    values["project"] = project.key if project is not None else ""

    _check_posture_rows(harness, values, sources, laid.defaults, off_posture)
    catalog.refuse_values(harness, values, chosen=chosen, available=avail)

    posture = values.get("posture") or ""
    if posture:
        problem = _posture_problem("the launch", posture, avail)
        if problem is not None:
            raise BadPreset(problem)

    identity = values.get("identity") or None
    scope = values.get("scope") or None
    if identity is None:
        # Only comms' wrapper takes these, and it runs only for a launch with an identity.
        for name in ("director", "scope"):
            if name in chosen and catalog.is_set(values.get(name)):
                warnings.append(f"{name} is ignored: the launch has no identity, and only "
                                "comms' wrapper takes it")
    if identity and not avail("comms"):
        warnings.append(f"identity {identity} is ignored: comms is not installed")
        identity = scope = None
        values["identity"], values["scope"] = "", ""
    target = str(values.get("target") or "terminal")
    mode, turns, more = mode_of(harness, target, identity=identity, available=avail)
    warnings += more
    catalog.refuse_wrapper_flags(harness, identity, values.get(catalog.EXTRA) or ())

    links = {name for name, on in (
        ("turns", turns), ("comms", identity is not None or posture.startswith(WITH)),
        ("projects", project is not None), ("slots", target == "slot"),
        ("notes", catalog.is_set(values.get("notes_dir"))),
        ("usage", catalog.is_set(values.get("warn_at")))) if on}
    return Resolved(
        harness=harness, mode=mode, values=MappingProxyType(values),
        sources=MappingProxyType(sources), chosen=chosen,
        preset_kind=preset.kind if preset is not None else None,
        preset_name=preset.name if preset is not None else None,
        cwd=cwd, scratch=scratch, project_key=project.key if project is not None else None,
        agents=tuple(project.agents) if project is not None else (),
        identity=identity, scope=scope, posture=str(posture), target=target,
        off_posture=off_posture, links=frozenset(links), warnings=tuple(warnings))


def _one_spelling(layers, rows, values: dict, sources: dict) -> None:
    """Each text given inline or from a file (`TWO_SPELLINGS`) is taken from the latest
    layer that gives it either way, so a typed `--instructions` replaces a seat's
    `instructions_file`; the other spelling goes back to its default. One layer giving
    both is refused, since one of them would go unread."""
    for inline, from_file in TWO_SPELLINGS:
        for source, layer in reversed(layers):
            given = [k for k in (inline, from_file) if catalog.is_set(layer.get(k))]
            if len(given) == 2:
                raise BadValue(inline, f"both {inline} and {from_file} are given",
                               "give one of them")
            if given:
                other = from_file if given[0] == inline else inline
                if other in rows and sources.get(other) not in (source, LEFT_OUT):
                    values[other], sources[other] = _fresh(rows[other].default), SHIPPED
                break


def mode_of(harness: str, target: str, *, identity: str | None = None,
            available: Callable[[str], bool] | None = None) -> tuple[str, bool, list[str]]:
    """The command a launch composes, whether it goes with turns, and any warning line.

    Claude: `interactive` in a terminal or slot, `print` headless. Codex: `exec`
    headless; in a terminal or slot `host` with turns, `interactive` without. A Codex
    launch with turns but without turns' host installed runs as one without turns, with
    a warning. A Codex launch with an identity runs comms' Codex wrapper, which execs
    turns' host, so it is `host` and refused where the host can't start or comms has no
    such wrapper. Refused for now: a headless launch with an identity (comms' headless
    wrapper flags)."""
    avail = _avail(available)
    turns = avail("turns")
    headless = target == "headless"
    if identity and headless:
        raise NotYet("identity", "a headless launch with an identity is not taken yet",
                     "it waits on comms' headless wrapper; launch in a terminal or slot, "
                     "or without an identity")
    if harness == "claude":
        return ("print" if headless else "interactive"), turns, []
    if identity:
        if not comms_codex():
            raise NotYet("identity", "a Codex launch with an identity needs comms' Codex "
                         "wrapper (moxi comms codex), which this comms does not offer",
                         f"install {COMMS_NEEDED}, or launch without an identity")
        if not turns or not _host_installed():
            raise NotYet("identity", "a Codex launch with an identity runs under turns' "
                         f"Codex host ({CODEX_HOST}), which can't start here",
                         "install moxi-turns with its codex extra, or launch without an "
                         "identity")
        return "host", True, []
    if headless:
        return "exec", False, []
    if not turns:
        return "interactive", False, []
    if not _host_installed():
        return "interactive", False, [f"turns' Codex host ({CODEX_HOST}) can't start "
                                      "here, as it is not installed or turns lacks its "
                                      "codex extra: launching codex without turns"]
    return "host", True, []


def comms_api() -> object | None:
    """comms' API, or None when comms is not installed or does not speak the interface
    the launcher calls (`COMMS_INTERFACE`): the identity check and the Codex wrapper come
    with it."""
    if not modules.available("comms"):
        return None
    found = modules.load("comms", interface=COMMS_INTERFACE)
    return found.api if found.ok else None


def comms_codex() -> bool:
    """Whether comms offers its Codex wrapper, `moxi comms codex`: every comms that speaks
    the launcher's interface does."""
    return comms_api() is not None


def codex_host() -> str | None:
    """turns' Codex host, when it can start here, else None: its script is installed in
    this environment, and turns says the host can start (`codex_host_available()`, true
    once turns' `codex` extra is installed). The script is installed with or without the
    extra, so the script alone does not count, and a turns that can't be reached at the
    interface the launcher calls has no host to offer."""
    from moxi import paths

    from . import first

    try:
        script = paths.script(CODEX_HOST)
    except FileNotFoundError:
        return None
    api = first.turns_api(quiet=True)
    available = getattr(api, "codex_host_available", None) if api is not None else None
    if available is None or not available():
        return None
    return str(script)


def _host_installed() -> bool:
    return codex_host() is not None


def _named(request, found: Presets) -> Preset | None:
    seat = getattr(request, "seat", None)
    project = getattr(request, "project_preset", None)
    if seat and project:
        raise BadValue(None, "a launch names at most one preset",
                       "give --seat or --project-preset, not both")
    if seat:
        return found.get("seat", seat)
    if project:
        return found.get("project", project)
    return None


def _place(place: Mapping[str, object], caller: str, avail, what: str
           ) -> tuple[str | None, bool, object | None]:
    """(cwd, scratch, project) from the one place named, else the caller's directory."""
    if place.get("scratch"):
        return None, True, None
    key = _project_key_of(place)
    if key is not None:
        project = _get_project(key, what, avail)
        return str(project.root), False, project
    if HOME in place:
        directory = _home_path(str(place[HOME]), what, None)
    else:
        directory = str(place.get("cwd") or os.path.abspath(caller))
    return directory, False, _project_for(directory, avail)


def _project_key_of(values: Mapping[str, object]) -> str | None:
    if values.get("project"):
        return str(values["project"])
    home = values.get(HOME)
    if isinstance(home, str) and home and not _is_path(home):
        return home
    return None


def _is_path(text: str) -> bool:
    return "/" in text or "\\" in text or text.startswith(("~", "."))


def _home_path(home: str, what: str, problems: list | None) -> str | None:
    expanded = Path(home).expanduser()
    if expanded.is_absolute():
        return str(expanded)
    hint = "give an absolute path, or one starting with ~"
    if problems is None:
        raise BadValue(HOME, f"home is the relative path {home!r}", hint)
    problems.append(Problem(what, HOME, f"home is the relative path {home!r}", hint))
    return None


def _check_posture_rows(harness, values, sources, defaults, off_posture) -> None:
    for row in catalog.load(harness):
        if not row.posture or row.name not in values:
            continue
        if sources[row.name] not in (PRESET, TYPED, LEFT_OUT):
            continue
        default = (_checked(row, defaults[row.name]) if row.name in defaults
                   else row.default)
        if values[row.name] != default and not off_posture:
            raise OffPosture(row.name, values[row.name], default, sources[row.name])


def _checked(row, value):
    return _unset_as_default(row, row.check(value))


def _unset_as_default(row, value):
    """A checked value; an unset path reads as the row's default."""
    return _fresh(row.default) if value is None else _fresh(value)


def _fresh(value):
    """`value`, a list copied, so no two launches share one."""
    return list(value) if isinstance(value, list) else value


def _avail(available: Callable[[str], bool] | None) -> Callable[[str], bool]:
    return available if available is not None else slots.links


def _show(value: object) -> str:
    return repr(value) if not isinstance(value, list) else "[" + ", ".join(
        map(repr, value)) + "]"


__all__ = [
    "Agent", "BadPreset", "CODEX_START_CEILING", "COMMS_INTERFACE", "ERROR", "KINDS",
    "LEFT_OUT", "Layered", "NeedsProjects", "NotYet", "OffPosture", "Preset", "Presets",
    "Problem", "ProjectsBusy", "ProjectsUnreadable", "Request", "Resolved",
    "UnknownPreset", "UnknownProject", "WARNING", "check", "comms_api", "comms_codex",
    "harness_of", "layered", "mode_of", "path", "project_agents", "projects_api", "read",
    "resolve", "roles", "schema",
]
