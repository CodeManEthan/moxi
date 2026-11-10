"""rounds' config, read through `moxi.config` at each use.

The schema is `settings.toml` beside this file. `load()` gives the settings as they
stand. Every key is read at its use, so an edit takes effect at the next command.

`check_text(text)` is the module's whole loader over a config text, the announcement's
`CONFIG_CHECK`. It reads the text against the schema, then holds it to rounds' own rules
(`check`): `flows`, `models` and `projects` are tables the schema gives no item type, so
what a flow and a project table may hold is checked here.

A setting is read for a round or for a build. `round_setting` reads one of `ROUND_KEYS`
for the round's home project; `build_setting` reads one of `BUILD_KEYS` for the build's
repo. Each takes the project's own table first, then `defaults`.

A project's table is `[projects.<key>]`, and its name finds the project the way the
projects list finds one: ignoring case. So `project_table` gives the same table for
`proj-a` however the table's name or the key is written, and two tables whose names
differ only in case are refused, since both would be the one project's.
"""

from __future__ import annotations

from moxi.config import ConfigError, Schema

from . import flows
from .paths import MODULE

SCHEMA_RESOURCE = "settings.toml"

_schema: Schema | None = None


def schema() -> Schema:
    """The module's schema, parsed once per process."""
    global _schema
    if _schema is None:
        _schema = Schema.from_resource(__package__, SCHEMA_RESOURCE)
    return _schema


def load():
    """rounds' settings as they stand. Raises `moxi.config.ConfigError`."""
    from moxi import config

    return check(config.load(MODULE, schema()))


def check_text(text: str) -> None:
    """The module's whole loader over config text `text`. Raises
    `moxi.config.ConfigError` when it refuses."""
    from moxi import config

    check(config.check_text(MODULE, schema(), text))


def check(settings):
    """`settings` as read against the schema, held to rounds' own rules and returned as
    given. `load` and `check_text` both pass what they read through here. Raises
    `moxi.config.ConfigError` naming the key and the fix."""
    flows.check(settings)
    _check_sitting_at(settings.file, "defaults.sitting_at",
                      settings["defaults.sitting_at"])
    named: dict[str, str] = {}
    for name, table in settings["projects"].items():
        first = named.setdefault(name.casefold(), name)
        if first != name:
            raise ConfigError(
                settings.file, f"projects.{name}",
                f"names the project that [projects.{first}] names: a project's key is "
                "matched ignoring case, so the two tables are one project's",
                f"keep the project's settings in one table, [projects.{first}]")
        _check_project(settings.file, name, table)
    _check_folders_apart(settings)
    return settings


# --- a setting, for a round or for a build ----------------------------------------------

#: The keys read for a round, from its home project.
ROUND_KEYS = ("flow", "dir", "ratify", "trace", "idle_days", "dormant_days",
              "slice_minutes", "sitting_at")
#: The keys read for a build, from the build's repo.
BUILD_KEYS = ("into", "check", "finish", "check_on", "shared_paths", "keep_branch",
              "lock_ref", "brief")

#: What a finish action does.
MERGE = "merge"
PUSH = "push"
COMMAND = "command"
#: The finish of a repo that sets none: a local merge.
DEFAULT_FINISH = ({"do": MERGE},)

#: A project's own keys, which `defaults` does not hold, each with what it reads when the
#: project's table leaves it out. No `into` means the branch checked out at the repo's
#: root, and no `lock_ref` rounds' own name for the lock.
_PROJECT_ONLY = {"into": None, "check": None, "finish": DEFAULT_FINISH,
                 "shared_paths": (), "keep_branch": False, "lock_ref": None, "brief": ()}


def round_setting(settings, project: str, key: str):
    """The value of `key`, one of `ROUND_KEYS`, for a round whose home is `project`: the
    project's own, else the `defaults` value."""
    if key not in ROUND_KEYS:
        raise KeyError(f"{key!r} is not a setting read for a round")
    return _setting(settings, project, key)


def build_setting(settings, repo: str, key: str):
    """The value of `key`, one of `BUILD_KEYS`, for a build in the project `repo`: the
    project's own, else the `defaults` value, else what the key reads when unset.
    `finish` is the list of actions with each one's `id` filled in."""
    if key not in BUILD_KEYS:
        raise KeyError(f"{key!r} is not a setting read for a build")
    value = _setting(settings, repo, key)
    return finish_actions(value) if key == "finish" else value


def project_table(settings, project: str) -> dict:
    """The table of `settings` for the project `project`, empty when it has none. A
    table's name matches the key ignoring case, as the projects list matches a key."""
    wanted = project.casefold()
    for name, table in settings["projects"].items():
        if name.casefold() == wanted:
            return table
    return {}


def _setting(settings, project, key):
    import copy

    table = project_table(settings, project)
    if key in table:
        return table[key]
    if f"defaults.{key}" in settings:
        return settings[f"defaults.{key}"]
    value = _PROJECT_ONLY[key]
    return copy.deepcopy(list(value)) if isinstance(value, tuple) else value


def finish_actions(actions) -> list[dict]:
    """The finish `actions` of a config, each a copy with its `id`: a merge's or a push's
    is its kind when it names none."""
    return [{**action, "id": action.get("id", action["do"])} for action in actions]


# --- what a project table may hold ------------------------------------------------------

def _check_project(file, name, table) -> None:
    key = f"projects.{name}"
    if not isinstance(table, dict):
        raise ConfigError(file, key, f"is {flows.kind_of(table)}, not a table",
                          f"write the project's settings as a [{key}] table")
    for other, value in table.items():
        where = f"{key}.{other}"
        if other not in ROUND_KEYS and other not in BUILD_KEYS:
            raise ConfigError(
                file, where, "is not a key of a project table",
                "remove it; a project table takes "
                + ", ".join((*ROUND_KEYS, *BUILD_KEYS)))
        if other in _PROJECT_ONLY:
            _PROJECT_CHECKS[other](file, where, value)
        elif other != "dir":
            _check_as_default(file, where, other, value)
    if "sitting_at" in table:
        _check_sitting_at(file, f"{key}.sitting_at", table["sitting_at"])
    if "dir" in table:
        _check_dir(file, f"{key}.dir", name, table["dir"])


def _check_as_default(file, where, name, value) -> None:
    """Hold a project's value of the `defaults` key `name` to the type the schema gives
    that key."""
    declared = schema().keys[f"defaults.{name}"]
    kind = declared.type
    if kind == "enum":
        if value not in declared.values:
            raise ConfigError(file, where, f"is {value!r}, which it cannot be",
                              "use one of " + ", ".join(declared.values))
        return
    wanted = {"string": str, "bool": bool, "int": int}[kind]
    if not isinstance(value, wanted) or (kind == "int" and isinstance(value, bool)):
        raise ConfigError(file, where,
                          f"is {flows.kind_of(value)}, not {_KIND_WORDS[kind]}",
                          f"give {_KIND_WORDS[kind]}, as defaults.{name} takes")


def _check_sitting_at(file, where, value) -> None:
    if value < 0:
        raise ConfigError(file, where, f"is {value}, below 0",
                          "give the count of open questions at which a sitting is "
                          "suggested, or 0 to suggest none")


def _check_dir(file, where, project, value) -> None:
    import os

    if not isinstance(value, str) or not value:
        raise ConfigError(file, where, "is not a folder",
                          "give the rounds folder as a string")
    if os.path.isabs(os.path.expanduser(value)):
        return
    if has_notes_folder(project) is False:
        raise ConfigError(
            file, where,
            f"is the relative path {value!r}, and the project {project!r} has no notes "
            "folder to place it in",
            "give an absolute folder, or give the project a notes folder with `moxi "
            f"projects set {project} --notes-root DIR`")


def _check_folders_apart(settings) -> None:
    """Refuse two project tables whose `dir` settings reach one folder, once every
    link on the way is followed: a rounds folder belongs to one project. A relative
    `dir` is judged where the project's notes folder is known, and left alone where it
    is not."""
    import os

    from . import folders
    from .errors import RoundsError

    reached: dict[str, str] = {}
    for name, table in settings["projects"].items():
        if "dir" not in table:
            continue
        folder = os.path.expanduser(table["dir"])
        if not os.path.isabs(folder):
            try:
                folder = os.path.join(folders.notes_folder(name), folder)
            except RoundsError:
                continue
        real = os.path.realpath(folder)
        first = reached.setdefault(real, name)
        if first != name:
            raise ConfigError(
                settings.file, f"projects.{name}.dir",
                f"is {table['dir']!r}, which is the folder {real}, and "
                f"projects.{first}.dir is that folder too: a rounds folder belongs to "
                "one project",
                f"give {name} or {first} a rounds folder of its own")


def has_notes_folder(project: str) -> bool | None:
    """Whether the project `project` has a notes folder: the one its projects record
    names, else the one the notes module gives it by default. None when that can't be
    told: projects has no such project, or projects or an installed notes can't be
    read."""
    from . import folders
    from .errors import NoFolder, RoundsError

    try:
        folders.notes_folder(project)
    except NoFolder as none:
        return False if none.known else None
    except RoundsError:
        return None
    return True


def _check_string(file, where, value) -> None:
    if not isinstance(value, str) or not value:
        raise ConfigError(file, where, f"is {flows.kind_of(value)}, not a name",
                          "give it as a string, or leave the key out")


def _check_bool(file, where, value) -> None:
    if not isinstance(value, bool):
        raise ConfigError(file, where, f"is {flows.kind_of(value)}, not a boolean",
                          "give true or false")


def _check_strings(file, where, value) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(file, where, "is not a list of strings",
                          "give each item as a string")


def _check_check(file, where, value) -> None:
    """The repo's check: a command line that splits into a command, and its time limit."""
    import shlex

    if not isinstance(value, dict):
        raise ConfigError(file, where, f"is {flows.kind_of(value)}, not a table",
                          'write it as { command = "make check", timeout_s = 600 }')
    for other in value:
        if other not in ("command", "timeout_s"):
            raise ConfigError(file, f"{where}.{other}", "is not a key of a check",
                              "remove it; a check takes `command` and `timeout_s`")
    command = value.get("command")
    if not isinstance(command, str):
        raise ConfigError(file, f"{where}.command",
                          "is missing" if command is None else "is not a command line",
                          "give the check's command line as one string")
    try:
        words = shlex.split(command)
    except ValueError as exc:
        raise ConfigError(file, f"{where}.command",
                          f"does not split into a command and its arguments ({exc})",
                          "close every quote, as you would in a shell") from None
    if not words:
        raise ConfigError(file, f"{where}.command", "splits into no command",
                          "name the command to run, or remove `check`")
    flows.check_timeout(file, f"{where}.timeout_s", value.get("timeout_s"))


def _check_finish(file, where, value) -> None:
    """The repo's finish actions: each one well formed, and no two under one id."""
    if not isinstance(value, list):
        raise ConfigError(file, where, f"is {flows.kind_of(value)}, not a list",
                          'write it as a list of actions: [ { do = "merge" } ]')
    seen = {}
    for index, action in enumerate(value):
        here = f"{where}[{index}]"
        if not isinstance(action, dict):
            raise ConfigError(file, here, f"is {flows.kind_of(action)}, not a table",
                              'write each action as a table: { do = "merge" }')
        do = action.get("do")
        if do not in _ACTION_KEYS:
            raise ConfigError(
                file, f"{here}.do",
                "is missing" if do is None else f"is {do!r}, which it cannot be",
                f"use one of {', '.join(_ACTION_KEYS)}")
        for other in action:
            if other not in _ACTION_KEYS[do]:
                raise ConfigError(
                    file, f"{here}.{other}", f"is not a key of a {do} action",
                    f"remove it; a {do} action takes " + ", ".join(_ACTION_KEYS[do]))
        if "id" not in action and do == COMMAND:
            raise ConfigError(
                file, f"{here}.id", "is missing, and a command's records are kept "
                "under its id",
                'name the action: id = "deploy"')
        if "id" in action:
            _check_string(file, f"{here}.id", action["id"])
        if do == PUSH:
            _check_string(file, f"{here}.remote", action.get("remote"))
        if do == COMMAND:
            flows.check_argv(file, f"{here}.argv", action.get("argv"))
            flows.check_timeout(file, f"{here}.timeout_s", action.get("timeout_s"))
            if "repeat" in action:
                _check_bool(file, f"{here}.repeat", action["repeat"])
        name = action.get("id", do)
        if name in seen:
            raise ConfigError(
                file, f"{here}.id" if "id" in action else here,
                f"is {name!r}, the id of the action at {where}[{seen[name]}] too",
                "give each action an `id` of its own")
        seen[name] = index


#: The keys each kind of finish action takes.
_ACTION_KEYS = {MERGE: ("do", "id"), PUSH: ("do", "id", "remote"),
                COMMAND: ("do", "id", "argv", "timeout_s", "repeat")}

_PROJECT_CHECKS = {"into": _check_string, "check": _check_check, "finish": _check_finish,
                   "shared_paths": _check_strings, "keep_branch": _check_bool,
                   "lock_ref": _check_string, "brief": _check_strings}

_KIND_WORDS = {"string": "a string", "bool": "a boolean", "int": "an int"}


__all__ = ["MODULE", "SCHEMA_RESOURCE", "schema", "load", "check_text", "check",
           "ROUND_KEYS", "BUILD_KEYS", "MERGE", "PUSH", "COMMAND", "DEFAULT_FINISH",
           "round_setting", "build_setting", "project_table", "finish_actions",
           "has_notes_folder"]
