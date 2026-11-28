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
`project_setting` reads one of `PROJECT_KEYS`, which only a project's own table holds:
its checklist, its watched paths and its verifier's brief lines.
The `sweep`, `check_lock` and `watched_paths_guard` keys are this machine's and belong to
no project, so they are read as they stand (`settings["sweep.armed"]`).

**Lanes.** `[lanes.<name>]`, also this machine's, names another machine a check can be
sent to: its ssh destination (`host`), the command line that runs Moxi there (`moxi`),
the words that run the ssh client here (`ssh`, used as given, never split), how long
reaching it may take (`connect_timeout_s`) and the lock file that machine's own
scheduled work holds (`shared_lock`), which the far side takes for each run; `lane`
gives one with its defaults filled in. A project sends checks only to the lanes its own
`[projects.<key>.lanes.<name>]` tables name, in the order it names them
(`project_lanes`): each may give the words the check runs there (`command`, `scoped`),
its time limit and `memory_mib` there, and the `preflight` lines run there before each
run. `check_text` prints, after its check, a note for each lane table that gives its own
words, with the project's words beside them (`lane_notes`): the two copies can drift. It
prints them for `moxi config` (check, set and unset), not for the doctor, which runs the
same check for its settings row and gives rows, not notes (`_under_doctor`).

A project's table is `[projects.<key>]`, and its name finds the project the way the
projects list finds one: ignoring case. So `project_table` gives the same table for
`proj-a` however the table's name or the key is written, and two tables whose names
differ only in case are refused, since both would be the one project's.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from moxi.config import ConfigError, Schema

from . import flows, paths
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
    """rounds' settings as they stand. Raises `moxi.config.ConfigError`, or
    `UnfencedTest` under a test whose rounds config directory no Moxi variable places:
    the core reads the file through a layout of its own, so rounds' config directory is
    resolved first, through `paths`."""
    from moxi import config

    paths.config_dir()
    return check(config.load(MODULE, schema()))


def check_text(text: str) -> None:
    """The module's whole loader over config text `text`. Raises
    `moxi.config.ConfigError` when it refuses."""
    import sys

    from moxi import config

    checked = check(config.check_text(MODULE, schema(), text))
    if _under_doctor():
        return
    for line in lane_notes(checked):
        print(line, file=sys.stderr)


def _under_doctor() -> bool:
    """Whether this call came from the core's doctor (`moxi.doctor`), which runs a
    module's `CONFIG_CHECK` for its settings row."""
    import inspect

    frame = inspect.currentframe()
    while frame is not None:
        name = str(frame.f_globals.get("__name__", ""))
        if name == "moxi.doctor" or name.startswith("moxi.doctor."):
            return True
        frame = frame.f_back
    return False


def check(settings):
    """`settings` as read against the schema, held to rounds' own rules and returned as
    given. `load` and `check_text` both pass what they read through here. Raises
    `moxi.config.ConfigError` naming the key and the fix."""
    flows.check(settings)
    _check_sitting_at(settings.file, "defaults.sitting_at",
                      settings["defaults.sitting_at"])
    _check_disposable(settings.file, "defaults.disposable",
                      settings["defaults.disposable"])
    _check_grace(settings.file, "sweep.grace_hours", settings["sweep.grace_hours"])
    _check_at_once(settings.file, "check_lock.at_once", settings["check_lock.at_once"])
    _check_need(settings.file, "check_lock.need_mib", settings["check_lock.need_mib"])
    _check_director(settings.file, "defaults.director", settings["defaults.director"])
    _check_lanes(settings.file, settings["lanes"])
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
        if isinstance(table, dict) and LANES_KEY in table:
            _check_project_lanes(settings.file, f"projects.{name}.{LANES_KEY}",
                                 table[LANES_KEY], settings["lanes"])
    _check_rounds_dirs_apart(settings)
    return settings


# --- a setting, for a round or for a build ----------------------------------------------

#: The keys read for a round, from its home project.
ROUND_KEYS = ("flow", "dir", "ratify", "trace", "idle_days", "dormant_days",
              "slice_minutes", "sitting_at")
#: The keys read for a build, from the build's repo.
BUILD_KEYS = ("into", "land", "check", "ci", "finish", "check_on", "shared_paths",
              "keep_branch", "lock_ref", "brief", "disposable", "director",
              "private_files")
#: The keys only a project's own table holds, with no `defaults` form: what the project's
#: code is checked against belongs to that project.
PROJECT_KEYS = ("checklist", "watched_paths", "verifier_brief")
#: A project's own table of the lanes it sends checks to, `[projects.<key>.lanes.<name>]`.
LANES_KEY = "lanes"
#: The keys of a lane, `[lanes.<name>]`, and of a project's use of one.
LANE_KEYS = ("host", "moxi", "ssh", "connect_timeout_s", "shared_lock")
PROJECT_LANE_KEYS = ("command", "scoped", "timeout_s", "memory_mib", "preflight")
#: What a lane reads where its table leaves a key out.
LANE_DEFAULTS = {"moxi": "moxi", "ssh": ("ssh",), "connect_timeout_s": 10}
#: The switch of the watched-paths guard, a key of this machine's.
GUARD_SWITCH = "watched_paths_guard.enabled"

#: How a repo's builds land (`land`): each by its own finish, or through the repo's
#: queue, where a finish joins the queue and the lander merges.
LAND_FINISH = "finish"
LAND_QUEUE = "queue"
#: What `{base}` in a check's `scoped` command line is filled in with: the merge-base of
#: the build's tip and its target.
BASE_FIELD = "{base}"
#: What `{memory_mib}` in a check's `command` and `scoped`, and in `ci.local`'s `command`,
#: is filled in with: that table's own `memory_mib`.
MEMORY_FIELD = "{memory_mib}"
#: The least `memory_mib` a check may declare, in MiB.
MEMORY_MIB_MIN = 64

#: What a finish action does.
MERGE = "merge"
PUSH = "push"
COMMAND = "command"
#: The finish of a repo that sets none: a local merge.
DEFAULT_FINISH = ({"do": MERGE},)

#: A project's own keys, which `defaults` does not hold, each with what it reads when the
#: project's table leaves it out. No `into` means the branch checked out at the repo's
#: root, and no `lock_ref` rounds' own name for the lock.
_PROJECT_ONLY = {"into": None, "land": LAND_FINISH, "check": None, "ci": None, "finish": DEFAULT_FINISH,
                 "shared_paths": (), "keep_branch": False, "lock_ref": None, "brief": (),
                 "private_files": ()}


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


def project_setting(settings, project: str, key: str) -> list:
    """The value of `key`, one of `PROJECT_KEYS`, in the project `project`'s own table:
    a copy, empty when the table leaves it out."""
    import copy

    if key not in PROJECT_KEYS:
        raise KeyError(f"{key!r} is not a project's own setting")
    return copy.deepcopy(list(project_table(settings, project).get(key, ())))


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


def memory_mib(table) -> int | None:
    """The `memory_mib` a check's table (the project's `check`, or `ci.local`) declares:
    the most memory one run of its command holds, in MiB; None when it declares none."""
    if not isinstance(table, Mapping):
        return None
    value = table.get("memory_mib")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def fill_memory(words, table) -> list[str]:
    """`words` with `{memory_mib}` filled in with the `memory_mib` of `table`, the table
    the words come from; kept as written where it declares none."""
    found = memory_mib(table)
    if found is None:
        return [str(word) for word in words]
    return [str(word).replace(MEMORY_FIELD, str(found)) for word in words]


def lane(settings, name: str) -> dict | None:
    """The lane `name` as `[lanes.<name>]` gives it, each key left out at its default
    (`LANE_DEFAULTS`; `shared_lock` None); None when the settings name no such lane."""
    found = settings["lanes"].get(name)
    if not isinstance(found, Mapping):
        return None
    filled = {"shared_lock": None, **LANE_DEFAULTS, **found}
    filled["ssh"] = [str(word) for word in filled["ssh"]]
    return filled


def project_lanes(settings, repo: str) -> dict[str, dict]:
    """The lanes the project `repo` sends checks to, by name in the order its
    `[projects.<key>.lanes.<name>]` tables name them, each that table (a copy)."""
    import copy

    found = project_table(settings, repo).get(LANES_KEY) or {}
    return {name: copy.deepcopy(dict(table)) for name, table in found.items()
            if isinstance(table, Mapping)}


def lane_notes(settings) -> list[str]:
    """For each project lane table that gives its own `command` or `scoped`, the
    project's words and the lane's under each other, with the line saying the lane's
    replace them there: two copies of a check's words, shown where they can drift."""
    lines: list[str] = []
    for project, table in settings["projects"].items():
        if not isinstance(table, Mapping):
            continue
        own_check = table.get("check") if isinstance(table.get("check"), Mapping) else {}
        for name, used in (table.get(LANES_KEY) or {}).items():
            given = [key for key in ("command", "scoped")
                     if isinstance(used, Mapping) and key in used]
            if not given:
                continue
            lines.append(f"note: [projects.{project}.{LANES_KEY}.{name}] gives its own "
                         f"words: on lane {name} these replace the project's")
            for key in given:
                width = len(key) + 9
                lines.append(f"  {'project ' + key:<{width}} {own_check.get(key, '(none)')}")
                lines.append(f"  {'lane ' + key:<{width}} {used[key]}")
    return lines


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
        if other == LANES_KEY:
            continue
        if other not in ROUND_KEYS and other not in BUILD_KEYS and other not in PROJECT_KEYS:
            raise ConfigError(
                file, where, "is not a key of a project table",
                "remove it; a project table takes "
                + ", ".join((*ROUND_KEYS, *BUILD_KEYS, *PROJECT_KEYS, LANES_KEY)))
        if other in _PROJECT_ONLY or other in PROJECT_KEYS:
            _PROJECT_CHECKS[other](file, where, value)
        elif other != "dir":
            _check_as_default(file, where, other, value)
    if "ci" in table and "check" not in table:
        raise ConfigError(
            file, f"{key}.ci", "is set in a project that declares no check, and the full "
            "check is the way past a CI run that is red or absent",
            f'declare the full check too: check = {{ command = "...", timeout_s = N }} '
            f"in [{key}]")
    if table.get("land") == LAND_QUEUE and "scoped" not in (table.get("check") or {}):
        raise ConfigError(
            file, f"{key}.land", "is \"queue\" in a project whose check names no scoped "
            "command, and a queued build is checked by that command alone",
            f'give the check its scoped command too: check = {{ command = "...", '
            f'scoped = "... --base {BASE_FIELD}", timeout_s = N }} in [{key}]')
    if "sitting_at" in table:
        _check_sitting_at(file, f"{key}.sitting_at", table["sitting_at"])
    if "director" in table:
        _check_director(file, f"{key}.director", table["director"])
    if "disposable" in table:
        _check_disposable(file, f"{key}.disposable", table["disposable"])
    if "dir" in table:
        _check_dir(file, f"{key}.dir", name, table["dir"])


def _check_as_default(file, where, name, value) -> None:
    """Hold a project's value of the `defaults` key `name` to the type the schema gives
    that key."""
    declared = schema().keys[f"defaults.{name}"]
    kind = declared.type
    if kind == "list":
        _check_strings(file, where, value)
        return
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


def _check_director(file, where, value) -> None:
    """The director's comms name: empty, or a name with a lead's name's shape."""
    from . import leads

    if value and not leads.is_name(value):
        raise ConfigError(file, where, f"is {value!r}, which is not a comms name",
                          "give the director's comms name: letters, digits, . _ -, and "
                          'at most one @scope; or "" to name nobody')


def _check_land(file, where, value) -> None:
    if value not in (LAND_FINISH, LAND_QUEUE):
        raise ConfigError(file, where, f"is {value!r}, which it cannot be",
                          f'use "{LAND_FINISH}" (each build lands by its own finish) or '
                          f'"{LAND_QUEUE}" (a finish joins the repo\'s queue)')


def _check_grace(file, where, value) -> None:
    if value < 1:
        raise ConfigError(file, where, f"is {value}, below 1",
                          "give the hours nothing in a leftover folder must have changed "
                          "before the sweep takes it, 1 or more")


def _check_at_once(file, where, value) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(file, where, f"is {value!r}, not a whole number of at least 1",
                          "give how many runs may hold the check lock at once: 1, or 2 "
                          "where two checks fit in memory")


def _check_need(file, where, value) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(file, where, f"is {value!r}, not a whole number of at least 0",
                          "give the free memory a second run needs, in MiB, or 0 for the "
                          "sum of the runs' memory_mib")


def _check_memory_mib(file, where, value) -> None:
    """A check's `memory_mib`: the most memory one run of its command holds, a whole
    number of MiB, at least `MEMORY_MIB_MIN`."""
    if isinstance(value, bool) or not isinstance(value, int) or value < MEMORY_MIB_MIN:
        raise ConfigError(file, where,
                          f"is {value!r}, not a whole number of at least {MEMORY_MIB_MIN}",
                          "give the most memory one run of the command holds, in MiB "
                          "(the MemoryMax its cap enforces), or leave it out to run the "
                          "command only alone")


def _check_lanes(file, value) -> None:
    """Each `[lanes.<name>]`: a table of `LANE_KEYS` with a `host`; `moxi` a command
    line; `ssh` a non-empty list of non-empty strings; `connect_timeout_s` a number of
    seconds above 0; `shared_lock` a path."""
    for name, table in value.items():
        where = f"lanes.{name}"
        _check_table_keys(file, where, table, LANE_KEYS, "lane")
        if not isinstance(table.get("host"), str) or not table["host"].strip():
            raise ConfigError(file, f"{where}.host",
                              "is missing" if "host" not in table else "is not a host",
                              "give the ssh destination as the ssh client takes it: a "
                              "Host of your ssh config, or user@host")
        if "moxi" in table and (not isinstance(table["moxi"], str)
                                or not table["moxi"].strip()):
            raise ConfigError(file, f"{where}.moxi", "is not a command line",
                              "give the command line that runs Moxi on that machine, as "
                              'its login shell reads it, or leave it out for "moxi"')
        ssh = table.get("ssh", ["ssh"])
        if (not isinstance(ssh, list) or not ssh
                or not all(isinstance(word, str) and word for word in ssh)):
            raise ConfigError(file, f"{where}.ssh",
                              "is not a non-empty list of non-empty strings",
                              'give the words that run the ssh client here, such as '
                              '["ssh"], or leave it out')
        timeout_s = table.get("connect_timeout_s", 10)
        if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
                or timeout_s <= 0):
            raise ConfigError(file, f"{where}.connect_timeout_s",
                              f"is {timeout_s!r}, not a number of seconds above 0",
                              "give how long reaching the host may take, in seconds, or "
                              "leave it out for 10")
        if "shared_lock" in table and (not isinstance(table["shared_lock"], str)
                                       or not table["shared_lock"].strip()):
            raise ConfigError(file, f"{where}.shared_lock", "is not a path",
                              "give the path, on that machine, of the lock file its "
                              "scheduled work holds, or leave it out")


def _check_project_lanes(file, where, value, lanes) -> None:
    """A project's `lanes`: a table of tables, each naming a lane `[lanes]` has, with
    `PROJECT_LANE_KEYS`."""
    if not isinstance(value, dict):
        raise ConfigError(file, where, f"is {flows.kind_of(value)}, not a table",
                          f"write each lane the project uses as a [{where}.<name>] table")
    for name, table in value.items():
        at = f"{where}.{name}"
        _check_table_keys(file, at, table, PROJECT_LANE_KEYS, "project's lane")
        if name not in lanes:
            known = ", ".join(lanes) or "none"
            raise ConfigError(file, at, f"names the lane {name}, which [lanes] does not "
                              f"have (its lanes: {known})",
                              f"add [lanes.{name}] with its host, or remove [{at}]")
        for key in ("command", "scoped"):
            if key in table:
                _check_command_line(file, f"{at}.{key}", table[key])
        flows.check_timeout(file, f"{at}.timeout_s", table.get("timeout_s"))
        if "memory_mib" in table:
            _check_memory_mib(file, f"{at}.memory_mib", table["memory_mib"])
        preflight = table.get("preflight", [])
        if (not isinstance(preflight, list)
                or not all(isinstance(line, str) and line.strip() for line in preflight)):
            raise ConfigError(file, f"{at}.preflight",
                              "is not a list of non-empty strings",
                              'give each command line to run there first as a string, '
                              'such as ["git --version"]')


def _check_disposable(file, where, value) -> None:
    """Refuse a `disposable` entry that is empty, holds a path separator, or would match
    every name: one with no character outside `*`, `?` and bracket expressions."""
    for entry in value:
        if not entry or "/" in entry or "\\" in entry:
            raise ConfigError(file, where, f"holds {entry!r}, which is not a name glob",
                              "give each entry as a glob for one part of a path, such "
                              "as .venv or *.pyc")
        if not _fixed_part(entry):
            raise ConfigError(file, where, f"holds {entry!r}, which matches every name, "
                              "so no ignored file would keep a worktree",
                              "name what may be lost, such as .venv or *.pyc")


def _fixed_part(glob: str) -> bool:
    """Whether `glob` holds a character outside `*`, `?` and bracket expressions, read
    as `fnmatch` reads them."""
    at = 0
    while at < len(glob):
        char = glob[at]
        if char in "*?":
            at += 1
            continue
        if char == "[":
            end = at + 1
            if end < len(glob) and glob[end] == "!":
                end += 1
            if end < len(glob) and glob[end] == "]":
                end += 1
            end = glob.find("]", end)
            if end != -1:
                at = end + 1
                continue
        return True
    return False


def _check_dir(file, where, project, value) -> None:
    import os

    if not isinstance(value, str) or not value:
        raise ConfigError(file, where, "is not a folder",
                          "give the rounds folder as a string")
    if os.path.isabs(os.path.expanduser(value)):
        return
    if has_notes_dir(project) is False:
        raise ConfigError(
            file, where,
            f"is the relative path {value!r}, and the project {project!r} has no notes "
            "folder to place it in",
            "give an absolute folder, or give the project a notes folder with `moxi "
            f"projects set {project} --notes-root DIR`")


def _check_rounds_dirs_apart(settings) -> None:
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
        rounds_dir = os.path.expanduser(table["dir"])
        if not os.path.isabs(rounds_dir):
            try:
                rounds_dir = os.path.join(folders.notes_dir(name), rounds_dir)
            except RoundsError:
                continue
        real = os.path.realpath(rounds_dir)
        first = reached.setdefault(real, name)
        if first != name:
            raise ConfigError(
                settings.file, f"projects.{name}.dir",
                f"is {table['dir']!r}, which is the folder {real}, and "
                f"projects.{first}.dir is that folder too: a rounds folder belongs to "
                "one project",
                f"give {name} or {first} a rounds folder of its own")


def has_notes_dir(project: str) -> bool | None:
    """Whether the project `project` has a notes folder: the one its projects record
    names, else the one the notes module gives it by default. None when that can't be
    told: projects has no such project, or projects or an installed notes can't be
    read."""
    from . import folders
    from .errors import NoFolder, RoundsError

    try:
        folders.notes_dir(project)
    except NoFolder as exc:
        return False if exc.known else None
    except RoundsError:
        return None
    return True


def _check_string(file, where, value, what: str = "a name", *,
                  needed: bool = False) -> None:
    """A key that holds a name: `what` says which, and `needed` that the key can't be
    left out."""
    fix = f"give {what}" if needed else f"give {what}, or leave the key out"
    if value is None:
        raise ConfigError(file, where, "is missing", fix)
    if not isinstance(value, str):
        raise ConfigError(file, where, f"is {flows.kind_of(value)}, not a name", fix)
    if not value:
        raise ConfigError(file, where, "is empty", fix)


def _check_bool(file, where, value) -> None:
    if not isinstance(value, bool):
        raise ConfigError(file, where, f"is {flows.kind_of(value)}, not a boolean",
                          "give true or false")


def _check_strings(file, where, value) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(file, where, "is not a list of strings",
                          "give each item as a string")


def _check_private_files(file, where, value) -> None:
    """Refuse a `private_files` value that is not a list of literal paths, each relative
    to the project's root, written with `/`, and named once. Whether a file is there,
    tracked or ignored is the repo's state, read by `commission` and the doctor."""
    _check_strings(file, where, value)
    seen = set()
    for entry in value:
        problem = _private_file_problem(entry)
        if problem:
            raise ConfigError(file, where, f"holds {entry!r}, {problem}",
                              "give each private file as a path relative to the "
                              "project's root, with / between its parts, such as "
                              "AGENTS.md or notes/local.md")
        if entry in seen:
            raise ConfigError(file, where, f"holds {entry!r} twice",
                              "give each private file once")
        seen.add(entry)


def _private_file_problem(entry: str) -> str:
    """What makes `entry` no literal relative path to a file, or "" when nothing does."""
    if not entry:
        return "which is empty"
    if any(ord(char) < 32 or ord(char) == 127 for char in entry):
        return "which holds a control character"
    if "\\" in entry:
        return "which holds a \\; write / between the parts"
    if entry.startswith(("/", "~")) or (len(entry) > 1 and entry[1] == ":"):
        return "which is not relative to the project's root"
    if any(char in entry for char in "*?["):
        return "which holds *, ? or [; each entry is a literal path, not a glob"
    if entry.startswith(":"):
        return "which starts with :, which git reads as pathspec magic"
    parts = entry.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return "which has an empty, . or .. part"
    if any(part.casefold() == ".git" for part in parts):
        return "which has a .git part"
    return ""


def _check_check(file, where, value, *,
                 keys=("command", "timeout_s", "memory_mib")) -> None:
    """The repo's check: a command line that splits into a command, its time limit, and
    the most memory one run holds (`memory_mib`, which `{memory_mib}` in the command
    line is filled with). The project's own check also takes `scoped`, the command line
    a queue project's build is checked by, `{base}` in it filled in at each run
    (`_check_project_check`); `ci.local` takes the others alone."""
    if not isinstance(value, dict):
        raise ConfigError(file, where, f"is {flows.kind_of(value)}, not a table",
                          'write it as { command = "make check", timeout_s = 600 }')
    for other in value:
        if other not in keys:
            raise ConfigError(file, f"{where}.{other}", "is not a key of a check",
                              "remove it; a check takes "
                              + ", ".join(f"`{key}`" for key in keys))
    _check_command_line(file, f"{where}.command", value.get("command"))
    if "scoped" in value:
        _check_command_line(file, f"{where}.scoped", value["scoped"])
    flows.check_timeout(file, f"{where}.timeout_s", value.get("timeout_s"))
    if "memory_mib" in value:
        _check_memory_mib(file, f"{where}.memory_mib", value["memory_mib"])


def _check_project_check(file, where, value) -> None:
    _check_check(file, where, value, keys=("command", "scoped", "timeout_s", "memory_mib"))


def _check_command_line(file, where, command) -> None:
    """A check's command line: one string that splits into a command."""
    import shlex

    if not isinstance(command, str):
        raise ConfigError(file, where,
                          "is missing" if command is None else "is not a command line",
                          "give the check's command line as one string")
    try:
        words = shlex.split(command)
    except ValueError as exc:
        raise ConfigError(file, where,
                          f"does not split into a command and its arguments ({exc})",
                          "close every quote, as you would in a shell") from None
    if not words:
        raise ConfigError(file, where, "splits into no command",
                          "name the command to run, or remove `check`")


#: A checklist item's id: lower-case words of letters and digits joined by `-`.
_ITEM_ID = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
#: The longest checklist item id.
_ITEM_ID_CHARS = 32
#: A skill's name as a harness lists it, optionally `<plugin>:<skill>`.
_SKILL_PART = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_SKILL = re.compile(rf"{_SKILL_PART}(:{_SKILL_PART})?")
#: The keys of a checklist item, and of a watched-paths entry (`only_new` may be left out).
_ITEM_KEYS = ("id", "text", "skills")
_WATCH_KEYS = ("pattern", "note", "skills", "only_new")


def _check_line(file, where, value, what: str) -> None:
    """One line of plain text, not empty."""
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        raise ConfigError(file, where,
                          "is missing" if value is None else "is not one line of text",
                          f"give {what} as one line of plain language")


def _check_skills(file, where, value) -> None:
    """A list of at least one skill name, each named once and as a harness lists it."""
    if not isinstance(value, list) or not value:
        raise ConfigError(file, where,
                          "is missing" if value is None else "is not a list of skill names",
                          'name at least one skill to load: skills = ["layout-review"]')
    seen = set()
    for name in value:
        if not isinstance(name, str) or not _SKILL.fullmatch(name):
            raise ConfigError(file, where, f"holds {name!r}, which is not a skill's name",
                              "name the skill as the harness lists it, as layout-review")
        if name in seen:
            raise ConfigError(file, where, f"names {name!r} twice",
                              "name each skill once")
        seen.add(name)


def _check_table_keys(file, where, entry, keys, what: str) -> None:
    if not isinstance(entry, dict):
        raise ConfigError(file, where, f"is {flows.kind_of(entry)}, not a table",
                          f"write each {what} as a table of " + ", ".join(keys))
    for other in entry:
        if other not in keys:
            raise ConfigError(file, f"{where}.{other}", f"is not a key of a {what}",
                              f"remove it; a {what} takes " + ", ".join(keys))


def _check_checklist(file, where, value) -> None:
    """The project's checklist: a list of items, each `id`, `text` and `skills`, every id
    used once."""
    if not isinstance(value, list):
        raise ConfigError(file, where, f"is {flows.kind_of(value)}, not a list",
                          'write it as a list of items: [ { id = "public-surface", text = '
                          '"...", skills = ["layout-review"] } ]')
    seen = set()
    for index, item in enumerate(value):
        here = f"{where}[{index}]"
        _check_table_keys(file, here, item, _ITEM_KEYS, "checklist item")
        item_id = item.get("id")
        if (not isinstance(item_id, str) or len(item_id) > _ITEM_ID_CHARS
                or not _ITEM_ID.fullmatch(item_id)):
            raise ConfigError(file, f"{here}.id",
                              "is missing" if item_id is None
                              else f"is {item_id!r}, which is not an item id",
                              f"give lower-case words of letters and digits joined by -, "
                              f"at most {_ITEM_ID_CHARS} characters, as public-surface")
        if item_id in seen:
            raise ConfigError(file, f"{here}.id", f"is {item_id!r}, an id used before",
                              "give each item an id of its own")
        seen.add(item_id)
        _check_line(file, f"{here}.text", item.get("text"), "the item")
        _check_skills(file, f"{here}.skills", item.get("skills"))


def _check_watched_paths(file, where, value) -> None:
    """The project's watched paths: a list of entries, each `pattern`, `note`, `skills`
    and optionally `only_new`."""
    if not isinstance(value, list):
        raise ConfigError(file, where, f"is {flows.kind_of(value)}, not a list",
                          'write it as a list of entries: [ { pattern = "packages/*/", '
                          'note = "...", skills = ["layout-review"] } ]')
    for index, entry in enumerate(value):
        here = f"{where}[{index}]"
        _check_table_keys(file, here, entry, _WATCH_KEYS, "watched-paths entry")
        pattern = entry.get("pattern")
        problem = _pattern_problem(pattern)
        if problem:
            raise ConfigError(file, f"{here}.pattern",
                              "is missing" if pattern is None else f"is {pattern!r}, {problem}",
                              "write / between parts; it matches a path written with \\ too"
                              if isinstance(pattern, str) and "\\" in pattern else
                              "give a path relative to the project's root, with / between "
                              "its parts and a / at the end for a folder, as packages/*/")
        _check_line(file, f"{here}.note", entry.get("note"), "the note")
        _check_skills(file, f"{here}.skills", entry.get("skills"))
        if "only_new" in entry:
            _check_bool(file, f"{here}.only_new", entry["only_new"])


def _pattern_problem(pattern) -> str:
    """What makes `pattern` no watched-paths pattern, or "" when nothing does. One `/` at
    its end is the folder mark, taken off before its parts are read; every other `/`
    stands between two parts that are not empty."""
    if not isinstance(pattern, str) or not pattern:
        return "which is empty"
    if any(ord(char) < 32 or ord(char) == 127 for char in pattern):
        return "which holds a control character"
    if "\\" in pattern:
        return "which holds a \\"
    if pattern.startswith("/"):
        return "which is not relative to the project's root"
    body = pattern[:-1] if pattern.endswith("/") else pattern
    parts = body.split("/")
    if re.match(r"[A-Za-z]:", parts[0]):
        return "which names a drive"
    if any(part == "" for part in parts):
        return "which has an empty part"
    if any(part in (".", "..") for part in parts):
        return "which has a . or .. part"
    for part in parts:
        if "**" in part and part != "**":
            return "which holds ** inside a part; ** stands alone, as a whole part"
        if _unclosed(part):
            return "which holds a [ with no closing ]"
    return ""


def _unclosed(part: str) -> bool:
    """Whether `part` holds a `[` with no `]` closing it, read as `fnmatch` reads one."""
    at = part.find("[")
    while at != -1:
        end = at + 1
        if end < len(part) and part[end] == "!":
            end += 1
        if end < len(part) and part[end] == "]":
            end += 1
        end = part.find("]", end)
        if end == -1:
            return True
        at = part.find("[", end + 1)
    return False


#: The keys of `ci`.
_CI_KEYS = ("workflows", "local", "remote", "ref", "full_ref", "status", "ids", "jobs",
            "status_timeout_s")
#: The keys of a `ci.workflows` entry written as a table.
_WORKFLOW_KEYS = ("path", "jobs", "matrix", "pick", "reads_candidates", "stands_for")


def _check_ci(file, where, value) -> None:
    """CI in place of the full check: the workflow files, each read whole or by the jobs
    it names, the local part, where the tip is pushed, the second template of CI's own
    refs, and the commands that read a commit's runs and a run's jobs."""
    if not isinstance(value, dict):
        raise ConfigError(file, where, f"is {flows.kind_of(value)}, not a table",
                          'write it as { workflows = [".github/workflows/ci.yml"], '
                          'local = "none" }')
    for other in value:
        if other not in _CI_KEYS:
            raise ConfigError(file, f"{where}.{other}", "is not a key of ci",
                              "remove it; ci takes " + ", ".join(_CI_KEYS))
    workflows = value.get("workflows")
    if not isinstance(workflows, list) or not workflows:
        raise ConfigError(file, f"{where}.workflows",
                          "is missing" if workflows is None else "names no workflow",
                          "name each workflow by its file's path in the repo: "
                          'workflows = [".github/workflows/ci.yml"]')
    for entry in workflows:
        _check_workflow(file, f"{where}.workflows", entry)
    if all(isinstance(entry, dict) and entry.get("reads_candidates") is True
           for entry in workflows):
        raise ConfigError(file, f"{where}.workflows",
                          "marks every workflow reads_candidates, so none reads a full run",
                          "name the full run's workflow too, with no reads_candidates key")
    local = value.get("local")
    if local is None:
        raise ConfigError(file, f"{where}.local", "is missing",
                          'declare the short local run of what CI can\'t do: local = '
                          '{ command = "...", timeout_s = N }, or local = "none" where '
                          "CI does all of it")
    if local != "none":
        if not isinstance(local, dict):
            raise ConfigError(file, f"{where}.local",
                              f"is {local!r}, neither a command table nor \"none\"",
                              'write local = { command = "...", timeout_s = N } or '
                              'local = "none"')
        _check_check(file, f"{where}.local", local)
    if "remote" in value:
        _check_string(file, f"{where}.remote", value["remote"], "a remote's name")
    if "ref" in value:
        _check_ref(file, f"{where}.ref", value["ref"])
        if "remote" not in value:
            raise ConfigError(file, f"{where}.ref", "is set with no remote to push it to",
                              f'set {where}.remote too: remote = "origin"')
    if "full_ref" in value:
        _check_full_ref(file, f"{where}.full_ref", value["full_ref"], value.get("ref"),
                        where=where)
    for key in ("status", "ids", "jobs"):
        if key in value:
            flows.check_argv(file, f"{where}.{key}", value[key])
    flows.check_timeout(file, f"{where}.status_timeout_s", value.get("status_timeout_s"))


def _names_problem(value) -> str | None:
    """Why `value` is not a list of names, each once; None when it is."""
    if (not isinstance(value, list) or not value
            or not all(isinstance(one, str) and one.strip() for one in value)):
        return "is not a list of job names"
    if len(set(value)) != len(value):
        return "names a job twice"
    return None


def _check_workflow(file, where, entry) -> None:
    """One `ci.workflows` entry: a path inside the repo, or a table of the `path`, the
    `jobs` read of its run by name, the `matrix` patterns of the jobs read by pattern
    (one of the two at least), the `pick` job among `jobs`, `reads_candidates`, true
    for the workflow a queue's candidate is read by, and `stands_for`, a table of job
    names: each job of its run to the job of the target's full run it stands for."""
    from pathlib import PurePosixPath

    path = entry
    if isinstance(entry, dict):
        for other in entry:
            if other not in _WORKFLOW_KEYS:
                raise ConfigError(file, f"{where}.{other}",
                                  "is not a key of a workflow entry",
                                  "remove it; an entry takes " + ", ".join(_WORKFLOW_KEYS))
        path = entry.get("path")
        jobs, matrix = entry.get("jobs"), entry.get("matrix")
        if jobs is not None or matrix is None:
            why = "is missing" if jobs is None else _names_problem(jobs)
            if why is not None:
                raise ConfigError(file, f"{where}.jobs", why,
                                  'name each job read once, as its run names it: '
                                  'jobs = ["linux"], or give the entry as its path '
                                  "alone to read the whole run")
        if matrix is not None:
            why = _names_problem(matrix)
            if why is not None:
                raise ConfigError(file, f"{where}.matrix", why.replace("job names",
                                                                       "name patterns"),
                                  "give each pattern once, * for any text in a name: "
                                  'matrix = ["browser (*)"]')
        pick = entry.get("pick")
        if pick is not None and (not isinstance(pick, str) or pick not in (jobs or ())):
            raise ConfigError(file, f"{where}.pick", f"is {pick!r}, which jobs does not name",
                              'name the job that picks the others, and list it in jobs: '
                              'pick = "pick", jobs = ["pick", ...]')
        reads = entry.get("reads_candidates")
        if reads is not None and not isinstance(reads, bool):
            raise ConfigError(file, f"{where}.reads_candidates",
                              f"is {reads!r}, not true or false",
                              "write reads_candidates = true on the workflow a queue's "
                              "candidate is read by")
        stands_for = entry.get("stands_for")
        if stands_for is not None and (
                not isinstance(stands_for, dict)
                or not all(isinstance(one, str) and one.strip()
                           for pair in stands_for.items() for one in pair)):
            raise ConfigError(file, f"{where}.stands_for",
                              f"is {stands_for!r}, not a table of job names",
                              "map each job of this run to the job of the target's full "
                              'run it stands for: stands_for = { scoped = "linux" }')
    parts = PurePosixPath(path).parts if isinstance(path, str) else ()
    if (not isinstance(path, str) or not path.strip() or path.startswith(("/", "\\"))
            or ".." in parts or ":" in path.split("/")[0]):
        raise ConfigError(file, where,
                          f"names {path!r}, which is not a path inside the repo",
                          "give each workflow's path relative to the repo's root, "
                          "with no .. part")


def _check_ref(file, where, value, *, example: str = 'ref = "ci/{branch}"') -> None:
    """`ci.ref`, or `ci.full_ref` with its own `example`: one fixed prefix that ends in
    `/`, then `{branch}`, and nothing else."""
    from . import ci

    _check_string(file, where, value, "a branch name template")
    why = ci.ref_problem(value)
    if why is not None:
        raise ConfigError(file, where, why,
                          f"give a fixed prefix ending in / and then {{branch}}: {example}")


def _check_full_ref(file, key, value, ref, *, where) -> None:
    """`ci.full_ref`: a template under `ref`'s rules, set only beside `ref`, with a
    prefix that `ref`'s does not start with and that does not start with `ref`'s."""
    from . import ci

    _check_ref(file, key, value, example='full_ref = "ci-full/{branch}"')
    if ref is None:
        raise ConfigError(file, key, "is set with no ref beside it",
                          f'set {where}.ref too: ref = "ci/{{branch}}"')
    why = ci.overlap_problem(ref, value)
    if why is not None:
        raise ConfigError(file, key, why,
                          "give the two templates prefixes that neither start with the "
                          'other: ref = "ci/{branch}", full_ref = "ci-full/{branch}"')


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
            _check_string(file, f"{here}.id", action["id"], "an id", needed=do == COMMAND)
        if do == PUSH:
            _check_string(file, f"{here}.remote", action.get("remote"), "a remote's name",
                          needed=True)
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

_PROJECT_CHECKS = {
    "into": lambda file, where, value: _check_string(file, where, value, "a branch's name"),
    "land": _check_land, "check": _check_project_check, "ci": _check_ci, "finish": _check_finish,
    "shared_paths": _check_strings, "keep_branch": _check_bool,
    "lock_ref": lambda file, where, value: _check_string(file, where, value, "a git ref"),
    "brief": _check_strings, "private_files": _check_private_files,
    "checklist": _check_checklist, "watched_paths": _check_watched_paths,
    "verifier_brief": _check_strings}

_KIND_WORDS = {"string": "a string", "bool": "a boolean", "int": "an int"}


__all__ = ["MODULE", "SCHEMA_RESOURCE", "schema", "load", "check_text", "check",
           "ROUND_KEYS", "BUILD_KEYS", "PROJECT_KEYS", "GUARD_SWITCH", "LAND_FINISH", "LAND_QUEUE", "BASE_FIELD", "MEMORY_FIELD", "MEMORY_MIB_MIN", "MERGE", "PUSH", "COMMAND", "DEFAULT_FINISH",
           "round_setting", "build_setting", "project_setting", "project_table",
           "finish_actions", "has_notes_dir", "memory_mib", "fill_memory",
           "LANES_KEY", "LANE_KEYS", "PROJECT_LANE_KEYS", "LANE_DEFAULTS", "lane",
           "project_lanes", "lane_notes"]
