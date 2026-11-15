"""loadout's machine settings, read through `moxi.config` at each use.

The schema is `settings.toml` beside this file. It holds only what is true of one machine:
where the loadout folder is (`source`), whether a commit to it reinstalls it through
repo's git hook pieces (`reinstall_on_commit`), how many backups to keep of each harness
file (`backups.keep`), and each harness's skills and agents folders inside a project
(`project_folders`). Every user choice of what goes where is in the manifest instead
(`moxi_loadout.manifest`).

`read()` gives those keys as loadout uses them: the folder as an absolute `Path` (the
default folder in loadout's data directory when the key is empty), and `project_dirs`
merged over its default, so a file that names one harness or one kind changes only that
one.

`check_text(text)` is the module's whole loader over a config text, the announcement's
`CONFIG_CHECK`: the schema's check, then what the schema can't say. Each
`project_folders` entry is a table of `skills` and `agents`, each a relative folder that
stays inside the project (no `..`, not absolute, no drive); `backups.keep` is at least 1.

Standard library only at import; `moxi.config` is light.
"""

from __future__ import annotations

import os
import re

from moxi.config import ConfigError, Schema

MODULE = "loadout"
SCHEMA_RESOURCE = "settings.toml"

SOURCE = "source"
REINSTALL_ON_COMMIT = "reinstall_on_commit"
BACKUPS_KEEP = "backups.keep"
PROJECT_DIRS = "project_folders"
#: The kinds a harness has a project folder for.
DIR_KINDS = ("skills", "agents")

_SEPARATORS = re.compile(r"[\\/]+")
_HARNESS = re.compile(r"[a-z0-9][a-z0-9_-]*")
_DRIVE = re.compile(r"^[A-Za-z]:")

_schema: Schema | None = None


def schema() -> Schema:
    """The module's schema, parsed once per process."""
    global _schema
    if _schema is None:
        _schema = Schema.from_resource(__package__, SCHEMA_RESOURCE)
    return _schema


class Machine:
    """loadout's settings as it uses them.

    `source` is the loadout folder, an absolute `Path`; `chosen` is whether the config
    names it (False means the default folder). `reinstall_on_commit` is a bool;
    `backups_keep` is an int.
    `project_dirs` maps each harness to `{"skills": rel, "agents": rel}`, the default
    rows with the file's entries over them. `settings` is the `Settings` read.
    """

    __slots__ = ("settings", "source", "chosen", "reinstall_on_commit", "backups_keep",
                 "project_dirs")

    def __init__(self, settings, default_source):
        from pathlib import Path

        self.settings = settings
        given = settings[SOURCE]
        self.chosen = given is not None
        self.source = Path(os.path.normpath(given)) if given else Path(default_source)
        self.reinstall_on_commit = settings[REINSTALL_ON_COMMIT]
        self.backups_keep = settings[BACKUPS_KEEP]
        self.project_dirs = read_project_dirs(settings[PROJECT_DIRS], settings.file)

    def __repr__(self) -> str:
        return f"Machine(source={str(self.source)!r})"


def read() -> Machine:
    """loadout's settings as they stand. Raises `moxi.config.ConfigError`."""
    from moxi import config

    from moxi_loadout import stores

    settings = config.load(MODULE, schema())
    machine = Machine(settings, stores.default_source())
    _check_keep(machine.backups_keep, settings.file)
    return machine


def check_text(text: str) -> None:
    """The module's whole loader over config text `text`. Raises `ConfigError` when it
    refuses."""
    from moxi import config

    settings = config.check_text(MODULE, schema(), text)
    read_project_dirs(settings[PROJECT_DIRS], None)
    _check_keep(settings[BACKUPS_KEEP], None)


def default_dirs() -> dict[str, dict[str, str]]:
    """The schema's default `project_folders`, a fresh copy."""
    default = schema().keys[PROJECT_DIRS].default
    return {harness: dict(row) for harness, row in default.items()}


def read_project_dirs(value, file) -> dict[str, dict[str, str]]:
    """`value`, the file's `project_folders`, merged over the default and checked, or
    `ConfigError`. Each folder is returned as written, with `\\` and `/` as written."""
    merged = default_dirs()
    for harness, row in value.items():
        where = f"{PROJECT_DIRS}.{harness}"
        if not isinstance(harness, str) or not _HARNESS.fullmatch(harness):
            raise ConfigError(file, PROJECT_DIRS, f"names the harness {harness!r}",
                              "name a harness in lower-case letters, digits, - and _")
        if not isinstance(row, dict):
            raise ConfigError(file, where, f"is {type(row).__name__}, not a table",
                              f'write it as [{where}] with skills = "..." and agents = "..."')
        for kind, project_dir in row.items():
            if kind not in DIR_KINDS:
                raise ConfigError(file, f"{where}.{kind}", "is not a folder kind",
                                  "use skills or agents")
            merged.setdefault(harness, {})[kind] = relative_dir(
                project_dir, file, f"{where}.{kind}")
    return merged


def relative_dir(project_dir, file, key: str) -> str:
    """`project_dir` when it is a relative folder that stays inside a project, else
    `ConfigError` naming `key`."""
    fix = "give a folder relative to the project's root, such as .claude/skills"
    if not isinstance(project_dir, str):
        raise ConfigError(file, key, f"is {type(project_dir).__name__}, not a string", fix)
    parts = [p for p in _SEPARATORS.split(project_dir) if p not in ("", ".")]
    if not parts:
        raise ConfigError(file, key, "is empty", fix)
    if (os.path.isabs(project_dir) or project_dir[:1] in ("/", "\\", "~")
            or _DRIVE.match(project_dir)):
        raise ConfigError(file, key, f"is the absolute path {project_dir!r}", fix)
    if ".." in parts:
        raise ConfigError(file, key, f"{project_dir!r} climbs out of the project with ..", fix)
    return project_dir


def _check_keep(keep: int, file) -> None:
    if keep < 1:
        raise ConfigError(file, BACKUPS_KEEP, f"is {keep}",
                          "keep at least 1 backup of each harness file")


__all__ = [
    "MODULE", "SOURCE", "REINSTALL_ON_COMMIT", "BACKUPS_KEEP", "PROJECT_DIRS", "DIR_KINDS", "Machine",
    "schema", "read", "check_text", "default_dirs", "read_project_dirs", "relative_dir",
]
