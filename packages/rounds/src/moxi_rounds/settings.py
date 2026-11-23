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
The `sweep` keys are this machine's and belong to no project, so they are read as
they stand (`settings["sweep.armed"]`).

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
    _check_disposable(settings.file, "defaults.disposable",
                      settings["defaults.disposable"])
    _check_grace(settings.file, "sweep.grace_hours", settings["sweep.grace_hours"])
    _check_director(settings.file, "defaults.director", settings["defaults.director"])
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
    _check_rounds_dirs_apart(settings)
    return settings


# --- a setting, for a round or for a build ----------------------------------------------

#: The keys read for a round, from its home project.
ROUND_KEYS = ("flow", "dir", "ratify", "trace", "idle_days", "dormant_days",
              "slice_minutes", "sitting_at")
#: The keys read for a build, from the build's repo.
BUILD_KEYS = ("into", "land", "check", "ci", "finish", "check_on", "shared_paths",
              "keep_branch", "lock_ref", "brief", "disposable", "director")

#: How a repo's builds land (`land`): each by its own finish, or through the repo's
#: queue, where a finish joins the queue and the lander merges.
LAND_FINISH = "finish"
LAND_QUEUE = "queue"
#: What `{base}` in a check's `scoped` command line is filled in with: the merge-base of
#: the build's tip and its target.
BASE_FIELD = "{base}"

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


def _check_check(file, where, value, *, keys=("command", "timeout_s")) -> None:
    """The repo's check: a command line that splits into a command, and its time limit.
    The project's own check also takes `scoped`, the command line a queue project's
    build is checked by, `{base}` in it filled in at each run (`_check_project_check`);
    `ci.local` takes the first two alone."""
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


def _check_project_check(file, where, value) -> None:
    _check_check(file, where, value, keys=("command", "scoped", "timeout_s"))


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


#: The keys of `ci`.
_CI_KEYS = ("workflows", "local", "remote", "ref", "full_ref", "status", "ids", "jobs",
            "status_timeout_s")
#: The keys of a `ci.workflows` entry written as a table.
_WORKFLOW_KEYS = ("path", "jobs", "matrix", "pick", "reads_candidates")


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
    (one of the two at least), the `pick` job among `jobs`, and `reads_candidates`, true
    for the workflow a queue's candidate is read by."""
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
    "brief": _check_strings}

_KIND_WORDS = {"string": "a string", "bool": "a boolean", "int": "an int"}


__all__ = ["MODULE", "SCHEMA_RESOURCE", "schema", "load", "check_text", "check",
           "ROUND_KEYS", "BUILD_KEYS", "LAND_FINISH", "LAND_QUEUE", "BASE_FIELD", "MERGE", "PUSH", "COMMAND", "DEFAULT_FINISH",
           "round_setting", "build_setting", "project_table", "finish_actions",
           "has_notes_dir"]
