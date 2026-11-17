"""The handlers of loadout's page (`moxi_loadout.page` declares the routes).

**Reads** answer from the calls the verbs print: `install.status()` (`status --json`),
`install.status_plan()` (the view: `status`'s outcome with the plan it came from),
`listing.entries()` (`list --json`), `install.global_preview()` (`global show --json`),
`moxi_loadout.sourcedir` (the folder's own files, as git and repo read them),
`moxi_loadout.preview` (what a Replace would do, on `status`'s plan),
projects' `list_projects()`, and the job records `moxi_loadout.pagejob` keeps. They run
here, off the event loop on the base's threads, take neither `install.lock` nor the core
lock, and give git the time the doctor gives it: each call of the would-commit check
`doctor.GIT_SECONDS`, all of them together `doctor.GIT_BUDGET`, and the core placer's
tracked check `doctor.PLACE_GIT_SECONDS` in all, so no read nears the shell's 30 second
proxy deadline. A read never raises: a manifest, ledger or config that can't be read is a
200 whose `problem` says so, as the verb would print it; any other failure is a `problem`
naming only the exception's class, logged once. A projects error is the picker's
`reason`, never a failure.

**Acts** each start a page job running the same verb the user would type, and answer 202
with `/api/job?job=<id>`; nothing here writes the manifest or installs. Before the job
starts an act refuses (422, nothing written):

- an item name, an id or a mode the verb would refuse;
- a project section named by a path relative to a directory, unless the manifest has a
  section of exactly that name: the job runs from loadout's state directory, so such a
  path would name another place than the page meant. A section is named exactly as the
  manifest names it, by its project key, or by an absolute path, and one the manifest
  names by a relative path goes to the job as its absolute root;
- a `--replace` path that isn't absolute, for the same reason;
- `enable` or `disable` naming both the user section and a project section;
- a `job` that isn't 32 hex characters;
- a job that didn't start (its record is removed), or a `job` that names a job of other
  arguments.

`/api/replace` and `/api/global-remove` run as the job their body's `job` names, the id
the page mints for each press, so a repeat of the press (two at once, or a retry after
the page server restarted) finds the job it started and answers 202 for it again,
without starting a second. Other acts use a fresh id.

Values go to the verb as its own flags (`--project=P`, `--replace=ID`), so no value is
read as a flag.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Mapping, Sequence
from typing import Any

from moxi_loadout import pagejob
from moxi_loadout.page import JOB_ID_CHARS, JOB_ROUTE, lb

log = logging.getLogger("moxi.loadout.page")

_logged: set[type] = set()
_logged_lock = threading.Lock()


def _log_once(err: BaseException, what: str) -> None:
    with _logged_lock:
        first = type(err) not in _logged
        _logged.add(type(err))
    if first:
        log.error("%s (%s)", what, type(err).__name__, exc_info=err)


def _problem(err: Exception, what: str) -> str:
    """The line a read gives for `exc`: the verb's own words for a manifest, ledger or
    config that can't be read, else a fixed line naming only the class (logged once)."""
    from moxi.config import ConfigError

    from moxi_loadout.ledger import LedgerError
    from moxi_loadout.manifest import ManifestError

    if isinstance(err, (ManifestError, LedgerError, ConfigError)):
        return f"loadout: {err}"
    _log_once(err, f"loadout's page couldn't read {what}")
    return (f"loadout's page couldn't read {what} ({type(err).__name__}); the page "
            "server's log has the details")


# -- reads ----------------------------------------------------------------------------------

def _git():
    """The would-commit check's git, bounded as the doctor bounds it."""
    from moxi_loadout import doctor, ignores

    return ignores.Git(timeout=doctor.GIT_SECONDS, budget=doctor.GIT_BUDGET)


def _status():
    from moxi_loadout import doctor, homes, install

    with homes.as_page():
        return install.status(git=_git(), git_seconds=doctor.PLACE_GIT_SECONDS)


def status(call: Any) -> dict[str, Any]:
    """`GET /api/status`: what `moxi loadout status --json` prints."""
    try:
        outcome = _status()
    except Exception as exc:  # noqa: BLE001 - a read says what failed, it never raises
        return {"problem": _problem(exc, "the status"), "rows": [], "lines": [],
                "notes": [], "counts": {}, "exit": 1, "kept": []}
    return {"problem": None, **outcome.as_json()}


def listing(call: Any) -> dict[str, Any]:
    """`GET /api/list`: every item each section lists (`moxi loadout list --json`), and
    the sections with the names an act takes."""
    from moxi_loadout import listing as listing_mod
    from moxi_loadout import manifest, settings

    try:
        source = settings.read().source
        found = manifest.load(source)
        items = listing_mod.entries(source)
    except Exception as exc:  # noqa: BLE001
        return {"problem": _problem(exc, "the manifest"), "manifest": None, "sections": [],
                "items": []}
    return {
        "problem": None,
        "manifest": os.fspath(found.path),
        "sections": _sections_of(found),
        "items": [{**e, "harnesses": None if e["harnesses"] is None else list(e["harnesses"])}
                  for e in items],
    }


def global_preview(call: Any) -> dict[str, Any]:
    """`GET /api/global`: what `moxi loadout global show --json` prints, and whether the
    manifest chose the mode."""
    from moxi_loadout import doctor, install, manifest, settings

    try:
        preview = install.global_preview(git_seconds=doctor.PLACE_GIT_SECONDS)
        chosen = manifest.load(settings.read().source).mode is not None
    except Exception as exc:  # noqa: BLE001
        return {"problem": _problem(exc, "the global file"), "mode": "off", "chosen": False,
                "files": []}
    return {"problem": None, **_global(preview, chosen=chosen)}


def _global(preview: Mapping[str, Any], *, chosen: bool) -> dict[str, Any]:
    """`/api/global`'s mode, `chosen` and files, from `install.global_preview`'s answer."""
    return {
        "mode": preview["mode"],
        "chosen": chosen,
        "files": [{"file": os.fspath(f["file"]), "harness": f["harness"],
                   "parts": [os.fspath(p) for p in f["parts"]], "changes": bool(f["changes"]),
                   "diff": f["diff"], "placed": bool(f.get("placed", False)),
                   "state": f.get("state")} for f in preview["files"]],
    }


def _view_global(preview: Mapping[str, Any], found) -> dict[str, Any]:
    """The view's global file: `/api/global`'s, with the global text's path and each
    file's form."""
    shown = _global(preview, chosen=found.mode is not None)
    for file, given in zip(shown["files"], preview["files"]):
        file["form"] = given.get("form")
    source = found.global_file
    return {"source": None if source is None else os.fspath(source), **shown}


def _items(found) -> list[dict[str, Any]]:
    """`/api/list`'s items, from a manifest already read."""
    from moxi_loadout import listing as listing_mod

    return [{**e, "harnesses": None if e["harnesses"] is None else list(e["harnesses"])}
            for e in listing_mod.entries_of(found)]


def _sections_of(found) -> list[dict[str, Any]]:
    return [{"name": s.name, "where": s.where, "root": s.root} for s in found.sections()]


def _home() -> str | None:
    from moxi import paths

    try:
        return os.fspath(paths.home())
    except LookupError:
        return None


def view(call: Any) -> dict[str, Any]:
    """`GET /api/view`: everything the page's items draw, from the one plan `status`
    builds: `status`'s outcome (its rows, with `moxi_loadout.view`'s facts on each, its
    lines, notes and counts), whether every section resolved, the note, the home,
    loadout and manifest paths, the harness table's names, the sections, the items as
    `/api/list` gives them, and the global file as `/api/global` does."""
    from moxi_loadout import doctor, homes, install
    from moxi_loadout import view as view_mod

    try:
        with homes.as_page():
            done = install.status_plan(git=_git(), git_seconds=doctor.PLACE_GIT_SECONDS)
            note = homes.note()
        plan, outcome = done.plan, done.outcome.as_json()
        found = plan.manifest
        answer = {
            "problem": None,
            "lines": outcome["lines"],
            "notes": outcome["notes"],
            "counts": outcome["counts"],
            "note": note,
            "home": _home(),
            "folder": os.fspath(plan.loadout_dir),
            "manifest": os.fspath(found.path),
            "harnesses": list(plan.harnesses),
            "resolved_all": plan.resolved_all,
            "sections": view_mod.sections(plan),
            "items": [{**e, **view_mod.item_facts(plan, e["kind"], e["name"])}
                      for e in _items(found)],
            "rows": [{**r, **facts} for r, facts in zip(outcome["rows"],
                                                         view_mod.row_facts(done))],
            "item_notes": list(done.run.item_notes),
            "global": _view_global(install.global_preview_of(plan, done.run), found),
        }
    except Exception as exc:  # noqa: BLE001 - a read says what failed, it never raises
        return {"problem": _problem(exc, "the status"), "lines": [], "notes": [],
                "counts": {}, "note": None, "home": None, "folder": None, "manifest": None,
                "harnesses": [], "resolved_all": False, "sections": [], "items": [], "rows": [],
                "item_notes": [],
                "global": {"source": None, "mode": "off", "chosen": False, "files": []}}
    return answer


#: What `/api/folder` says when git fails or doesn't answer in its time; the line names
#: no path, so the folder's line shows only the path.
GIT_FAILED = "git couldn't read the loadout folder's work tree"
GIT_SLOW = "git didn't answer in time for the loadout folder's work tree"


def source_dir(call: Any) -> dict[str, Any]:
    """`GET /api/folder`: the loadout folder's files git hasn't committed (git bounded as
    the doctor bounds it), the skills and agents in it no section lists, and whether a
    commit installs it again (`moxi_loadout.sourcedir`). A manifest that can't be read
    lists nothing as unlisted; its problem is the view's."""
    from moxi_loadout import doctor, manifest, settings, sourcedir
    from moxi_loadout.ignores import Git, GitError

    answer: dict[str, Any] = {"state": "ok", "detail": None, "uncommitted": None,
                              "more": 0, "unlisted": [], "reinstalls": False}
    try:
        machine = settings.read()
    except Exception as exc:  # noqa: BLE001 - a read says what failed, it never raises
        return {**answer, "state": "error", "detail": _problem(exc, "the settings")}
    loadout_dir = machine.source
    try:
        found = sourcedir.uncommitted(
            loadout_dir, Git(timeout=doctor.GIT_SECONDS, budget=doctor.GIT_BUDGET))
    except GitError as exc:
        answer.update(state="error", detail=GIT_SLOW if exc.timed_out else GIT_FAILED)
    else:
        if found is not None:
            answer.update(uncommitted=found[:sourcedir.UNCOMMITTED_MAX],
                          more=max(0, len(found) - sourcedir.UNCOMMITTED_MAX))
    try:
        answer["unlisted"] = sourcedir.unlisted(manifest.load(loadout_dir))
    except Exception as exc:  # noqa: BLE001 - the view's problem says why
        _problem(exc, "the manifest")  # logs a failure of another kind, once
    answer["reinstalls"] = sourcedir.reinstalls(
        loadout_dir, enabled=machine.reinstall_on_commit, seconds=doctor.STATUS_SECONDS)
    return answer


def replace_preview(call: Any) -> dict[str, Any]:
    """`GET /api/replace-preview?id=&section=`: what a Replace of the row `id` names in
    `section` would do, for the confirm (`moxi_loadout.preview`): worked out on the plan
    `status` builds, under the sections the act runs with, git bounded as for the view.
    An `id` the verb would refuse is refused (422); a read that fails says so in `problem`
    with no case."""
    from moxi_loadout import doctor, homes, install
    from moxi_loadout import preview as preview_mod
    from moxi_loadout.manifest import parse_id

    text, section = call.query["id"], call.query.get("section")
    if not install._is_path(text) and not text.startswith("global:"):
        try:
            parse_id(text)
        except ValueError as exc:
            raise lb.Refused(f"loadout: {exc}") from None
    elif install._is_path(text) and not os.path.isabs(text):
        raise lb.Refused(f"loadout: {text}: name a path absolutely")
    try:
        with homes.as_page():
            answer = preview_mod.replace_preview(text, section or None, git=_git(),
                                                 git_seconds=doctor.PLACE_GIT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - a read says what failed, it never raises
        return {"problem": _problem(exc, "the replace preview"), "case": None,
                "target": None, "kept_in": None, "tracked": False, "others": 0,
                "label": "file", "diff": "", "lines": 0, "skipped": []}
    return {"problem": None, **answer}


def projects(call: Any) -> dict[str, Any]:
    """`GET /api/projects`: every project by key, for the project picker; `reason` says
    why there is none (projects absent, busy or unreadable), and `cause` names it
    (`absent`, `busy`, `unreadable`, `error`)."""
    from moxi_loadout import projects_link

    link, why = projects_link.api()
    if link is None:
        return {"projects": [], "reason": why,
                "cause": "absent" if why == projects_link.ABSENT else "error"}
    try:
        found = link.list_projects()
    except link.Busy:
        return {"projects": [], "reason": "projects is busy; try again", "cause": "busy"}
    except link.Unreadable as exc:
        return {"projects": [], "reason": f"projects can't be read: {exc}",
                "cause": "unreadable"}
    except Exception as exc:  # noqa: BLE001 - the picker says why, it never fails
        _log_once(exc, "loadout's page couldn't list the projects")
        return {"projects": [], "reason": f"projects couldn't be listed ({type(exc).__name__})",
                "cause": "error"}
    return {"projects": [{"key": str(p.key), "root": os.fspath(p.root)} for p in found],
            "reason": None, "cause": None}


def drift(call: Any) -> dict[str, Any]:
    """`GET /api/drift`: how many items aren't `ok` or `off`, and the note that the
    page would write elsewhere than the user's last run in a terminal (`moxi_loadout.homes`). An item counts once however many of its harnesses aren't ok (its rows share a
    section, a kind and a name). The note is never counted."""
    from moxi_loadout import homes
    from moxi_loadout.planner import FINE

    try:
        outcome = _status()
        with homes.as_page():
            note = homes.note()
    except Exception as exc:  # noqa: BLE001
        return {"problem": _problem(exc, "the status"), "count": 0, "note": None}
    items = {(r.scope, r.kind, r.name) for r in outcome.rows if r.state not in FINE}
    return {"problem": None, "count": len(items), "note": note}


def job(call: Any) -> dict[str, Any]:
    """`GET /api/job?job=<id>`: a page job, running, done or interrupted; 422 for an id
    that names no job (records go after a day)."""
    ident = call.query["job"]
    found = pagejob.read(ident)
    if found is None:
        raise lb.Refused(f"there is no page job {ident}; a job's record is kept for a day")
    outcome = found["outcome"]
    if isinstance(outcome, Mapping):
        # A job's record from before outcomes named what they kept kept nothing.
        outcome = {"kept": [], **outcome}
    return {**found, "outcome": outcome if isinstance(outcome, Mapping) else None,
            "argv": [str(a) for a in found["argv"]] if isinstance(found["argv"], list)
            else None}


# -- acts -----------------------------------------------------------------------------------

def _start(argv: Sequence[str], *, key: str | None = None) -> Any:
    """Start the job running `argv`, under id `key` when the page named one (a fresh id
    otherwise): 202 with its status path, or a refusal."""
    try:
        started = pagejob.start(argv, job=key)
    except (pagejob.Clash, ValueError) as exc:
        raise lb.Refused(f"loadout: {exc}") from None
    except Exception as exc:  # noqa: BLE001 - a job that didn't start is the act's refusal
        _log_once(exc, "a page job didn't start")
        raise lb.Refused(f"loadout: the job didn't start ({type(exc).__name__}); try again, "
                         "or run the command in a terminal") from None
    return lb.Accepted(f"{JOB_ROUTE}?job={started.job}")


def _section(name: str) -> str:
    """Project section `name` as the verb's flag, or a refusal: a path relative to a
    directory is taken only when the manifest has a section of exactly that name, and
    goes to the job as that section's absolute root, so the job never reads it relative
    to a directory."""
    from moxi_loadout import manifest, settings

    if not name.strip():
        raise lb.Refused("loadout: name a project section")
    if manifest.is_path_name(name) and not os.path.isabs(name):
        try:
            found = manifest.load(settings.read().source)
        except Exception as exc:  # noqa: BLE001 - the job would refuse it the same way
            raise lb.Refused(_problem(exc, "the manifest")) from None
        named = next((s for s in found.projects if s.name == name and s.root), None)
        if named is None:
            raise lb.Refused(f"loadout: project section {name}: name it as the manifest "
                             "does, by its project key, or by an absolute path")
        return f"--project={named.root}"
    return f"--project={name}"


def _sections(body: Mapping[str, Any]) -> list[str]:
    flags = ["--user"] if body.get("user") else []
    return flags + [_section(p) for p in body.get("projects") or ()]


def _item(verb: str, body: Mapping[str, Any]) -> Any:
    from moxi_loadout import manifest

    kind, name = body["kind"], body["name"]
    problem = manifest.name_problem(kind, name)
    if problem is not None:
        raise lb.Refused(f"loadout: the page {problem}")
    project = body.get("project")
    return _start([verb, kind, name, *([] if project is None else [_section(project)])])


def _toggle(verb: str, body: Mapping[str, Any]) -> Any:
    from moxi_loadout import manifest

    ident = body["id"]
    try:
        manifest.parse_id(ident)
    except ValueError as exc:
        raise lb.Refused(f"loadout: {exc}") from None
    project = body.get("project")
    if body.get("user") and project is not None:
        raise lb.Refused("loadout: name the user section or a project section, not both")
    flags = ["--user"] if body.get("user") else []
    if project is not None:
        flags.append(_section(project))
    return _start([verb, ident, *flags])


def add(call: Any) -> Any:
    """`POST /api/add`: `moxi loadout add KIND NAME [--project P]`, as a job."""
    return _item("add", call.body)


def remove(call: Any) -> Any:
    """`POST /api/remove`: `moxi loadout remove KIND NAME [--project P]`, as a job."""
    return _item("remove", call.body)


def enable(call: Any) -> Any:
    """`POST /api/enable`: `moxi loadout enable ID [--user | --project P]`, as a job."""
    return _toggle("enable", call.body)


def disable(call: Any) -> Any:
    """`POST /api/disable`: `moxi loadout disable ID [--user | --project P]`, as a job."""
    return _toggle("disable", call.body)


def global_mode(call: Any) -> Any:
    """`POST /api/global-mode`: `moxi loadout global mode MODE`, as a job. The mode `off`
    is `/api/global-remove`'s."""
    return _start(["global", "mode", call.body["mode"]])


def install(call: Any) -> Any:
    """`POST /api/install`: `moxi loadout install [--user] [--project P]...`, as a job."""
    return _start(["install", *_sections(call.body)])


def _job(body: Mapping[str, Any]) -> str:
    """The job id the page minted for this press, or a refusal."""
    job = body["job"]
    if len(job) != JOB_ID_CHARS or any(c not in "0123456789abcdef" for c in job):
        raise lb.Refused(f"loadout: a job id is {JOB_ID_CHARS} hex characters")
    return job


def replace(call: Any) -> Any:
    """`POST /api/replace`: `moxi loadout install --replace ID [--user | --project P]`, as
    the job the body names."""
    from moxi_loadout.install import _is_path

    value, body = call.body["replace"], call.body
    if not value.strip():
        raise lb.Refused("loadout: name what to replace: a path or <kind>:<name>@<harness>")
    if _is_path(value) and not os.path.isabs(value):
        raise lb.Refused(f"loadout: --replace {value}: name a path absolutely")
    project = body.get("project")
    if body.get("user") and project is not None:
        raise lb.Refused("loadout: name the user section or a project section, not both")
    flags = ["--user"] if body.get("user") else []
    if project is not None:
        flags.append(_section(project))
    return _start(["install", f"--replace={value}", *flags], key=_job(body))


def global_remove(call: Any) -> Any:
    """`POST /api/global-remove`: `moxi loadout global remove`, as the job the body names."""
    return _start(["global", "remove"], key=_job(call.body))


__all__ = ["status", "view", "listing", "global_preview", "source_dir", "replace_preview",
           "projects", "drift", "job", "add", "remove",
           "enable", "disable", "global_mode", "install", "replace", "global_remove"]
