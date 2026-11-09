"""The handlers of loadout's page (`moxi_loadout.page` declares the routes).

**Reads** answer from the calls the verbs print: `install.status()` (`status --json`),
`listing.entries()` (`list --json`), `install.global_preview()` (`global show --json`),
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
- a job that didn't start (its record is removed), or a confirmed act's key that names a
  job of other arguments.

A confirmed act (`/api/replace`, `/api/global-off`) uses its idempotency key as the job's
id, so a retry after the page server restarted finds the job it started and answers 202
for it again, without starting a second. It answers `Accepted` or a refusal, never a
plain answer. Other acts use a fresh id.

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
from moxi_loadout.page import JOB_ROUTE, lb

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
    """The line a read gives for `err`: the verb's own words for a manifest, ledger or
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
    except Exception as err:  # noqa: BLE001 - a read says what failed, it never raises
        return {"problem": _problem(err, "the status"), "rows": [], "lines": [],
                "notes": [], "counts": {}, "exit": 1}
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
    except Exception as err:  # noqa: BLE001
        return {"problem": _problem(err, "the manifest"), "manifest": None, "sections": [],
                "items": []}
    return {
        "problem": None,
        "manifest": os.fspath(found.path),
        "sections": [{"name": s.name, "where": s.where, "root": s.root}
                     for s in found.sections()],
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
    except Exception as err:  # noqa: BLE001
        return {"problem": _problem(err, "the global file"), "mode": "off", "chosen": False,
                "files": []}
    return {
        "problem": None,
        "mode": preview["mode"],
        "chosen": chosen,
        "files": [{"file": os.fspath(f["file"]), "changes": bool(f["changes"]),
                   "diff": f["diff"], "placed": bool(f.get("placed", False)),
                   "state": f.get("state")} for f in preview["files"]],
    }


def projects(call: Any) -> dict[str, Any]:
    """`GET /api/projects`: every project by key, for the project picker; `reason` says
    why there is none (projects absent, busy or unreadable)."""
    from moxi_loadout import projects_link

    link, why = projects_link.api()
    if link is None:
        return {"projects": [], "reason": why}
    try:
        found = link.list_projects()
    except link.Busy:
        return {"projects": [], "reason": "projects is busy; try again"}
    except link.Unreadable as err:
        return {"projects": [], "reason": f"projects can't be read: {err}"}
    except Exception as err:  # noqa: BLE001 - the picker says why, it never fails
        _log_once(err, "loadout's page couldn't list the projects")
        return {"projects": [], "reason": f"projects couldn't be listed ({type(err).__name__})"}
    return {"projects": [{"key": str(p.key), "root": os.fspath(p.root)} for p in found],
            "reason": None}


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
    except Exception as err:  # noqa: BLE001
        return {"problem": _problem(err, "the status"), "count": 0, "note": None}
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
    return {**found, "outcome": outcome if isinstance(outcome, Mapping) else None,
            "argv": [str(a) for a in found["argv"]] if isinstance(found["argv"], list)
            else None}


# -- acts -----------------------------------------------------------------------------------

def _start(argv: Sequence[str], *, key: str | None = None) -> Any:
    """Start the job running `argv`, under `key` for a confirmed act: 202 with its status
    path, or a refusal."""
    try:
        started = pagejob.start(argv, job=key)
    except (pagejob.Clash, ValueError) as err:
        raise lb.Refused(f"loadout: {err}") from None
    except Exception as err:  # noqa: BLE001 - a job that didn't start is the act's refusal
        _log_once(err, "a page job didn't start")
        raise lb.Refused(f"loadout: the job didn't start ({type(err).__name__}); try again, "
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
        except Exception as err:  # noqa: BLE001 - the job would refuse it the same way
            raise lb.Refused(_problem(err, "the manifest")) from None
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
    except ValueError as err:
        raise lb.Refused(f"loadout: {err}") from None
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
    is `/api/global-off`'s, which is confirmed."""
    return _start(["global", "mode", call.body["mode"]])


def install(call: Any) -> Any:
    """`POST /api/install`: `moxi loadout install [--user] [--project P]...`, as a job."""
    return _start(["install", *_sections(call.body)])


def replace(call: Any) -> Any:
    """`POST /api/replace`, confirmed: `moxi loadout install --replace ID...` with the
    sections named, as a job under the act's key."""
    from moxi_loadout.install import _is_path

    values = list(call.body["replace"])
    if not values:
        raise lb.Refused("loadout: name what to replace: a path or <kind>:<name>@<harness>")
    for value in values:
        if not value.strip():
            raise lb.Refused("loadout: name what to replace: a path or "
                             "<kind>:<name>@<harness>")
        if _is_path(value) and not os.path.isabs(value):
            raise lb.Refused(f"loadout: --replace {value}: name a path absolutely")
    flags = [f"--replace={v}" for v in values]
    return _start(["install", *flags, *_sections(call.body)], key=call.key)


def global_off(call: Any) -> Any:
    """`POST /api/global-off`, confirmed: `moxi loadout global remove`, as a job under the
    act's key."""
    return _start(["global", "remove"], key=call.key)


__all__ = ["status", "listing", "global_preview", "projects", "drift", "job", "add", "remove",
           "enable", "disable", "global_mode", "install", "replace", "global_off"]
