"""The manifest, `loadout.toml` in the loadout folder: what goes where, read and checked.

The user writes it by hand and loadout's verbs write it (`moxi_loadout.writes`). It is
read here with the standard library's `tomllib`; nothing here writes.

    [global]
    mode = "off"                     # off, managed or if-absent; file = "global.md"
    [user]                           # every session of this user
    skills = ["review", "launcher/moxi-handoff"]
    hooks = ["guard-rm"]
    permissions = ["git-reads"]
    mcp = ["docs"]
    disabled = ["mcp:docs@codex"]    # <kind>:<name>@<harness>; harness * for all
    [project."~/code/app"]           # a project by path; [project.app] names it by key
    skills = ["app-deploy"]
    agents = ["app-lead"]
    [skill.review]
    harnesses = ["claude"]           # default: every row of the core's harness table
    [agent.app-lead]
    codex = { model = "o4" }         # extra Codex keys for the converted agent file
    [hook.guard-rm]
    event = "PreToolUse"
    matcher = "Bash"
    command = "python3 {source}/hooks/guard_rm.py"
    timeout = 10
    [permissions.git-reads]
    allow = [{ shell = ["git", "status"] }]
    claude = { allow = ["WebFetch(domain:docs.python.org)"] }
    [mcp.docs]
    command = "uvx"
    args = ["docs-mcp", "--root", "{source}/docs"]

The rules this module holds:

- **Sections.** `[user]`, and one `[project.<name>]` per project. A name holding `/` or
  `\\`, or starting with `~` or `.`, is a path: `~` is expanded and a relative path is
  read from the loadout folder. Any other name is a project key, which projects resolves.
  Two sections for one path, or two keys that differ only in case, are refused.
- **Kinds.** Each section lists `skills`, `agents`, `hooks`, `permissions` and `mcp`, and
  the item tables are `[skill.<n>]`, `[agent.<n>]`, `[hook.<n>]`, `[permissions.<n>]` and
  `[mcp.<n>]`. A skill may be a module's, `<module>/<skill>`, whose table takes only
  `harnesses`. Hooks, permission rules and MCP servers are written at user scope only:
  one listed in a project section is not read as wanted, and `Manifest.refused` holds
  one line for it.
- **Unknown keys.** Every key is checked against the known set before anything is used;
  an unknown one is a usage error naming the closest known key. Values are checked for
  their type. Each problem is one line of the `ManifestError`.
- **disabled** holds `<kind>:<name>@<harness>` ids, `*` for every harness, and switches
  only within its own section.
- **{source}** in a hook's `command` expands to the folder's path quoted for the shell
  the harness runs hooks with; in `args`, `env` and a path it expands plain.

Standard library only.
"""

from __future__ import annotations

import difflib
import os
import re
import sys
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from moxi.exit import ERROR, USAGE

USER = "user"
PROJECT = "project"
GLOBAL = "global"
#: Every harness, in a disabled id.
EVERY = "*"
#: The global modes, the first the default.
MODES = ("off", "managed", "if-absent")
#: The global text's file in the folder, unless `[global] file` names another.
GLOBAL_FILE = "global.md"
#: The placeholder for the loadout folder's path.
SOURCE_TOKEN = "{source}"

#: Each item kind and the section list that names its items.
KINDS = ("skill", "agent", "hook", "permissions", "mcp")
LISTS = {"skill": "skills", "agent": "agents", "hook": "hooks",
         "permissions": "permissions", "mcp": "mcp"}
KIND_OF = {v: k for k, v in LISTS.items()}
#: The kinds written at user scope only.
USER_ONLY = ("hook", "permissions", "mcp")

TOP_KEYS = frozenset({GLOBAL, USER, PROJECT, *KINDS})
GLOBAL_KEYS = frozenset({"mode", "file"})
SECTION_KEYS = frozenset({*LISTS.values(), "disabled"})
ITEM_KEYS = {
    "skill": frozenset({"source", "harnesses"}),
    "agent": frozenset({"source", "harnesses", "codex", "opencode"}),
    "hook": frozenset({"event", "matcher", "command", "timeout", "harnesses"}),
    "permissions": frozenset({"allow", "deny", "ask", "claude", "codex", "opencode",
                              "harnesses"}),
    "mcp": frozenset({"command", "args", "env", "url", "headers", "harnesses"}),
}
#: A module skill's table takes only its harness list.
MODULE_SKILL_KEYS = frozenset({"harnesses"})
RULE_LISTS = ("allow", "deny", "ask")

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_HARNESS = re.compile(r"[a-z0-9][a-z0-9_-]*")
_MODULE = re.compile(r"[a-z0-9]+")
_ID = re.compile(r"(?P<kind>[a-z]+):(?P<name>[^@]+)@(?P<harness>[^@]+)")


class ManifestError(Exception):
    """The manifest can't be read or used: one line per problem, each naming the file.

    `exit_code` is 2 (usage: a key, a value or a name the manifest can't hold) unless the
    file couldn't be read at all (1).
    """

    def __init__(self, path, lines: Iterable[str], *, exit_code: int = USAGE):
        self.path = None if path is None else Path(path)
        self.lines = tuple(lines)
        self.exit_code = exit_code
        where = f"{self.path}: " if self.path is not None else ""
        super().__init__("\n".join(where + line for line in self.lines))


# -- ids and names -------------------------------------------------------------------------

@dataclass(frozen=True)
class Id:
    """`<kind>:<name>@<harness>`: one item for one harness, `*` for every harness."""

    kind: str
    name: str
    harness: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.name}@{self.harness}"

    def covers(self, kind: str, name: str, harness: str) -> bool:
        return (self.kind, self.name) == (kind, name) and self.harness in (EVERY, harness)


def name_problem(kind: str, name: object) -> str | None:
    """Why `name` can't name an item of `kind`, or None."""
    if not isinstance(name, str) or not name:
        return f"names {kind} {name!r}, which is not a name"
    if kind == "skill" and "/" in name:
        module, _, skill = name.partition("/")
        if _MODULE.fullmatch(module) and _NAME.fullmatch(skill):
            return None
        return (f"names the module skill {name!r}; write it <module>/<skill>, the module "
                "in lower-case letters and digits")
    if not _NAME.fullmatch(name):
        return (f"names {kind} {name!r}; a name is letters, digits, '.', '_' and '-', "
                "and starts with a letter or digit")
    return None


def is_module_skill(name: str) -> bool:
    """Whether skill `name` is a module's, `<module>/<skill>`."""
    return "/" in name


def parse_id(text: object) -> Id:
    """`text` as an `Id`, or `ValueError` saying why not."""
    match = _ID.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        raise ValueError(f"{text!r} is not <kind>:<name>@<harness>")
    kind, name, harness = match["kind"], match["name"], match["harness"]
    if kind not in KINDS:
        raise ValueError(f"{text!r} names the kind {kind!r}; use one of {', '.join(KINDS)}")
    if problem := name_problem(kind, name):
        raise ValueError(f"{text!r} {problem}")
    if harness != EVERY and not _HARNESS.fullmatch(harness):
        raise ValueError(f"{text!r} names the harness {harness!r}; use a harness name or *")
    return Id(kind, name, harness)


def is_path_name(name: str) -> bool:
    """Whether project section `name` names a path, not a project key."""
    return "/" in name or "\\" in name or name[:1] in ("~", ".")


def section_root(name: str, folder: str | os.PathLike) -> str:
    """Path section `name` as an absolute path: `~` expanded, a relative path read from
    the loadout folder `folder`, normalized (links are not resolved)."""
    path = os.path.expanduser(name)
    if not os.path.isabs(path):
        path = os.path.join(os.fspath(folder), path)
    return os.path.normpath(path)


# -- {source} ------------------------------------------------------------------------------

def expand(text: str, folder: str | os.PathLike) -> str:
    """`text` with `{source}` replaced by the folder's absolute path, unquoted: for an MCP
    server's `args` and `env`, and for paths."""
    return text.replace(SOURCE_TOKEN, os.path.abspath(os.fspath(folder)))


def expand_command(text: str, folder: str | os.PathLike, *,
                   windows: bool = sys.platform == "win32") -> str:
    """Hook command `text`, a shell string, with `{source}` replaced by the folder's
    absolute path quoted for the shell the harness runs hooks with: `shlex.quote` on Linux
    and macOS; on Windows, the quoting of `subprocess.list2cmdline`."""
    path = os.path.abspath(os.fspath(folder))
    if windows:
        import subprocess

        quoted = subprocess.list2cmdline([path])
    else:
        import shlex

        quoted = shlex.quote(path)
    return text.replace(SOURCE_TOKEN, quoted)


def item_source(table: Mapping, folder: str | os.PathLike) -> Path | None:
    """An item table's own `source`, as an absolute path, or None when it has none: `~`
    and `{source}` expanded, a relative path read from the loadout folder."""
    given = table.get("source")
    if given is None:
        return None
    path = os.path.expanduser(expand(given, folder))
    if not os.path.isabs(path):
        path = os.path.join(os.fspath(folder), path)
    return Path(os.path.normpath(path))


# -- the model -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Section:
    """One section: `[user]` or `[project.<name>]`.

    `name` is the table's name (`"user"` for the user section); `where` is `"user"`,
    `"path"` or `"key"`; `root` is a path section's absolute root, else None. `lists`
    maps each kind to the names it lists, in order, each once; `disabled` holds its ids.
    """

    name: str
    where: str
    root: str | None = None
    lists: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    disabled: tuple[Id, ...] = ()

    @property
    def is_user(self) -> bool:
        return self.where == USER

    @property
    def key(self) -> str | None:
        """A key section's project key, else None."""
        return self.name if self.where == "key" else None

    def names(self, kind: str) -> tuple[str, ...]:
        return self.lists.get(kind, ())

    def lists_item(self, kind: str, name: str) -> bool:
        return name in self.names(kind)

    def is_disabled(self, kind: str, name: str, harness: str) -> bool:
        """Whether this section's `disabled` switches `kind` `name` off for `harness`."""
        return any(i.covers(kind, name, harness) for i in self.disabled)

    def label(self) -> str:
        """How a line names the section: `user`, or `project <name>`."""
        return USER if self.is_user else f"{PROJECT} {self.name}"


@dataclass(frozen=True)
class Refusal:
    """An item the manifest lists where it can't be written: its section, kind and name,
    and the line that says so."""

    section: str
    kind: str
    name: str
    line: str


@dataclass(frozen=True)
class Manifest:
    """The manifest as read and checked.

    `path` is the file and `folder` the loadout folder; `exists` is False when there is
    no file yet (then nothing is listed). `mode` is `[global] mode`, or None when the
    file sets none (the install question is asked only then); `global_file` is the
    global text's absolute path. `items` maps `(kind, name)` to the item's table, as
    plain data. `refused` holds the items listed where they can't be written.
    """

    path: Path
    folder: Path
    exists: bool = True
    mode: str | None = None
    global_file: Path | None = None
    user: Section = field(default_factory=lambda: Section(USER, USER))
    projects: tuple[Section, ...] = ()
    items: Mapping[tuple[str, str], Mapping] = field(default_factory=dict)
    refused: tuple[Refusal, ...] = ()

    @property
    def global_mode(self) -> str:
        """The global mode in force: `mode`, else `off`."""
        return self.mode or MODES[0]

    def sections(self) -> tuple[Section, ...]:
        return (self.user, *self.projects)

    def section(self, name: str | None) -> Section | None:
        """The section `name` names: None for the user section, else a project section
        by its table name, by its root for a path, or by its key ignoring case."""
        if name is None:
            return self.user
        for s in self.projects:
            if s.name == name:
                return s
        if is_path_name(name):
            root = os.path.normpath(os.path.abspath(os.path.expanduser(name)))
            return next((s for s in self.projects if s.root == root), None)
        folded = name.casefold()
        return next((s for s in self.projects
                     if s.where == "key" and s.name.casefold() == folded), None)

    def listing(self, kind: str, name: str) -> tuple[Section, ...]:
        """Every section that lists `kind` `name`."""
        return tuple(s for s in self.sections() if s.lists_item(kind, name))

    def item(self, kind: str, name: str) -> Mapping:
        """Item `kind` `name`'s table, empty when the manifest has none."""
        return self.items.get((kind, name), {})

    def harnesses(self, kind: str, name: str) -> tuple[str, ...] | None:
        """The item's own harness list, or None for every row of the harness table."""
        given = self.item(kind, name).get("harnesses")
        return None if given is None else tuple(given)

    def source_of(self, kind: str, name: str) -> Path | None:
        """The item's own `source` key as an absolute path, or None."""
        return item_source(self.item(kind, name), self.folder)


# -- reading -------------------------------------------------------------------------------

def load(folder: str | os.PathLike | None = None) -> Manifest:
    """The manifest in `folder` (the configured loadout folder when None), read and
    checked. A missing file is an empty manifest with `exists` False. Raises
    `ManifestError`, and `moxi.config.ConfigError` when `folder` is None and loadout's
    config doesn't read."""
    from moxi_loadout import stores

    path = stores.manifest_path(folder)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise ManifestError(path, [f"can't be read: {exc}"], exit_code=ERROR) from None
    return parse(text, path=path)


def empty(path: str | os.PathLike) -> Manifest:
    """The manifest of a folder with no `loadout.toml`."""
    path = Path(path)
    folder = path.parent
    return Manifest(path=path, folder=folder, exists=False,
                    global_file=folder / GLOBAL_FILE)


def parse(text: str, *, path: str | os.PathLike) -> Manifest:
    """Manifest text `text`, the file at `path` (its folder is the loadout folder), read
    and checked. Raises `ManifestError` with every problem found."""
    path = Path(path)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(path, [f"is not valid TOML: {exc}"]) from None
    return _Reader(path, data).manifest()


class _Reader:
    def __init__(self, path: Path, data: dict):
        self.path = path
        self.folder = path.parent
        self.data = data
        self.lines: list[str] = []

    def problem(self, line: str) -> None:
        self.lines.append(line)

    def unknown(self, where: str, table: Mapping, known: Iterable[str]) -> None:
        known = sorted(known)
        for k in table:
            if k not in known:
                near = difflib.get_close_matches(k, known, n=1, cutoff=0.0)
                hint = f" (did you mean {near[0]}?)" if near else ""
                self.problem(f"unknown key {where + '.' if where else ''}{k}{hint}")

    def table(self, where: str, value) -> dict:
        if not isinstance(value, dict):
            self.problem(f"{where}: want a table, found {_kind(value)}")
            return {}
        return value

    def strings(self, where: str, value) -> list[str]:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            self.problem(f"{where}: want a list of strings, found {_kind(value)}")
            return []
        return value

    def string(self, where: str, value) -> None:
        if not isinstance(value, str):
            self.problem(f"{where}: want a string, found {_kind(value)}")

    def manifest(self) -> Manifest:
        data = self.data
        self.unknown("", data, TOP_KEYS)
        mode, global_file = self.global_table(self.table(GLOBAL, data.get(GLOBAL, {})))
        items = self.items()
        refused: list[Refusal] = []
        user = self.section(USER, USER, self.table(USER, data.get(USER, {})), refused)
        projects = []
        for name, value in self.table(PROJECT, data.get(PROJECT, {})).items():
            where = f"{PROJECT}.{_quoted(name)}"
            if not name.strip():
                self.problem(f"{where}: a project section needs a path or a key")
                continue
            kind = "path" if is_path_name(name) else "key"
            projects.append(self.section(name, kind, self.table(where, value), refused))
        self.duplicates(projects)
        if self.lines:
            raise ManifestError(self.path, self.lines)
        return Manifest(path=self.path, folder=self.folder, exists=True, mode=mode,
                        global_file=global_file, user=user, projects=tuple(projects),
                        items=items, refused=tuple(refused))

    def global_table(self, table: dict) -> tuple[str | None, Path]:
        self.unknown(GLOBAL, table, GLOBAL_KEYS)
        mode = table.get("mode")
        if mode is not None and mode not in MODES:
            self.problem(f"global.mode: is {mode!r}; use one of {', '.join(MODES)}")
            mode = None
        given = table.get("file", GLOBAL_FILE)
        if not isinstance(given, str) or not given.strip():
            self.problem(f"global.file: want a file name, found {_kind(given)}")
            given = GLOBAL_FILE
        return mode, item_source({"source": given}, self.folder)

    def section(self, name: str, where: str, table: dict, refused: list) -> Section:
        label = USER if where == USER else f"{PROJECT}.{_quoted(name)}"
        self.unknown(label, table, SECTION_KEYS)
        lists: dict[str, tuple[str, ...]] = {}
        for kind, key in LISTS.items():
            if key not in table:
                continue
            names = []
            for n in self.strings(f"{label}.{key}", table[key]):
                if problem := name_problem(kind, n):
                    self.problem(f"{label}.{key}: {problem}")
                elif where != USER and kind in USER_ONLY:
                    refused.append(Refusal(name, kind, n,
                                           f"{label}: {kind} {n} is written at user scope "
                                           f"only; move it to [user]"))
                elif n not in names:
                    names.append(n)
            if names:
                lists[kind] = tuple(names)
        disabled = []
        for text in self.strings(f"{label}.disabled", table.get("disabled", [])):
            try:
                ident = parse_id(text)
            except ValueError as exc:
                self.problem(f"{label}.disabled: {exc}")
                continue
            if ident not in disabled:
                disabled.append(ident)
        root = section_root(name, self.folder) if where == "path" else None
        return Section(name=name, where=where, root=root, lists=lists,
                       disabled=tuple(disabled))

    def duplicates(self, projects: list[Section]) -> None:
        roots: dict[str, str] = {}
        keys: dict[str, str] = {}
        for s in projects:
            seen, mark = (roots, s.root) if s.where == "path" else (keys, s.name.casefold())
            if mark in seen:
                self.problem(f"project.{_quoted(seen[mark])} and project.{_quoted(s.name)} "
                             "name one project; keep one section")
            else:
                seen[mark] = s.name

    def items(self) -> dict[tuple[str, str], dict]:
        out = {}
        for kind in KINDS:
            for name, value in self.table(kind, self.data.get(kind, {})).items():
                where = f"{kind}.{_quoted(name)}"
                if problem := name_problem(kind, name):
                    self.problem(f"{where}: {problem}")
                    continue
                table = self.table(where, value)
                if kind == "skill" and is_module_skill(name):
                    self.unknown(where, table, MODULE_SKILL_KEYS)
                else:
                    self.unknown(where, table, ITEM_KEYS[kind])
                self.item(kind, where, table)
                out[kind, name] = table
        return out

    def item(self, kind: str, where: str, table: dict) -> None:
        if "harnesses" in table:
            for h in self.strings(f"{where}.harnesses", table["harnesses"]):
                if not _HARNESS.fullmatch(h):
                    self.problem(f"{where}.harnesses: names the harness {h!r}")
        if "source" in table:
            self.string(f"{where}.source", table["source"])
        if kind == "agent":
            for h in ("codex", "opencode"):
                if h in table:
                    self.table(f"{where}.{h}", table[h])
        elif kind == "hook":
            for key in ("event", "command"):
                if key not in table:
                    self.problem(f"{where}: needs {key}")
                else:
                    self.string(f"{where}.{key}", table[key])
            if "matcher" in table:
                self.string(f"{where}.matcher", table["matcher"])
            timeout = table.get("timeout")
            if timeout is not None and (isinstance(timeout, bool)
                                        or not isinstance(timeout, int) or timeout < 1):
                self.problem(f"{where}.timeout: want a whole number of seconds, at least 1")
        elif kind == "permissions":
            for key in RULE_LISTS:
                if key in table:
                    self.neutral_rules(f"{where}.{key}", table[key])
            if "claude" in table:
                claude = self.table(f"{where}.claude", table["claude"])
                self.unknown(f"{where}.claude", claude, RULE_LISTS)
                for key in RULE_LISTS:
                    if key in claude:
                        self.strings(f"{where}.claude.{key}", claude[key])
            for h in ("codex", "opencode"):
                if h in table:
                    self.table(f"{where}.{h}", table[h])
        elif kind == "mcp":
            if ("command" in table) == ("url" in table):
                self.problem(f"{where}: needs command or url, one of the two")
            for key in ("command", "url"):
                if key in table:
                    self.string(f"{where}.{key}", table[key])
            if "args" in table:
                self.strings(f"{where}.args", table["args"])
            for key in ("env", "headers"):
                if key in table:
                    values = self.table(f"{where}.{key}", table[key])
                    for k, v in values.items():
                        self.string(f"{where}.{key}.{k}", v)

    def neutral_rules(self, where: str, rules) -> None:
        if not isinstance(rules, list):
            self.problem(f"{where}: want a list of rules, found {_kind(rules)}")
            return
        for i, rule in enumerate(rules, 1):
            at = f"{where}[{i}]"
            if not isinstance(rule, dict):
                self.problem(f"{at}: want {{ shell = [...] }}, found {_kind(rule)}; a rule "
                             "in a harness's own form goes under its key, such as claude")
                continue
            self.unknown(at, rule, ("shell",))
            shell = rule.get("shell")
            if (not isinstance(shell, list) or not shell
                    or not all(isinstance(w, str) and w for w in shell)):
                self.problem(f"{at}.shell: want a list of the command's leading words")


def _kind(value) -> str:
    return {dict: "a table", list: "a list", str: "a string", bool: "a boolean",
            int: "an integer", float: "a number"}.get(type(value), type(value).__name__)


def _quoted(name: str) -> str:
    """`name` as a TOML key in a dotted name: bare when it can be, else quoted."""
    return name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else '"' + name.replace(
        "\\", "\\\\").replace('"', '\\"') + '"'


__all__ = ["USER", "PROJECT", "GLOBAL", "EVERY", "MODES", "GLOBAL_FILE", "SOURCE_TOKEN",
           "KINDS", "LISTS", "KIND_OF", "USER_ONLY", "ManifestError", "Id", "Section",
           "Refusal", "Manifest", "name_problem", "is_module_skill", "parse_id",
           "is_path_name", "section_root", "expand", "expand_command", "item_source",
           "load", "empty", "parse"]
