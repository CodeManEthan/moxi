"""CI in place of the full check: a repo's `ci` setting, the reading of a commit's CI runs,
and whether CI's form of the check gate is met at a tip.

**The setting** (`declared`): the workflows whose runs stand in for the full check, each
named by its file's path in the repo; `local`, the short local run of what CI can't do,
or the word `none` where CI does all of it; `remote`, where `check` pushes the branch
before it reads; and the two commands that read CI, `status` (a commit's runs) and
`ids` (each workflow file's id), each bounded by `status_timeout_s`.

**The read** (`read`). The two commands run with no shell in the build's worktree, stdin
closed, with the caller's environment less every `GIT_` variable, and only their
standard output is read, as JSON. From `ids`: a list of objects, each with `id` and
`path`. From `status`: a list of objects, each with `headSha`, `workflowDatabaseId`,
`status`, `conclusion` and `createdAt`, and `url` when there is one. Either command
failing to start, exiting other than 0, running past its limit, or printing anything
else (an object missing a field, two `ids` objects with one `path`) is a failed read
(`ReadFailed`), and nothing is read from it.

**The reading** (`reading`). A path's id is the `id` of the one `ids` object with that
path; a path none names reads `absent`. Of the `status` objects whose
`workflowDatabaseId` is that id and whose `headSha` is the tip, the newest by
`createdAt`, the first listed on a tie, reads `green` (completed, with success),
`pending` (not completed) or `red` (completed, with any other conclusion); with none
the workflow reads `absent`. The whole reading is `green` only when every workflow
reads `green`, else the first of `red`, `absent` and `pending` that one reads.

**CI's form of the gate** (`why_not`), apart from the target: the newest `ci.read` of
the build at the tip reads `green` and named the workflows the setting names now; and,
unless `local` is `none`, the newest run of `check.local` passed at the tip with the
argv `local` gives now. Whether the tip holds the target is the finish's to read.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from moxi import proc

from . import builds, checking, gitreads
from . import settings as own
from . import state as states

#: The record of a read of CI.
READ = "ci.read"
#: The step name the short local run is recorded under. No step of the user's own
#: has a dot in its name, so none is read for it.
LOCAL = "check.local"
#: What `local` says for a repo whose CI does all of its check.
NONE = "none"
#: The default command that lists a commit's CI runs, and the one that maps workflow
#: files to ids.
STATUS = ("gh", "run", "list", "--commit", "{tip}", "--json",
          "databaseId,workflowDatabaseId,headSha,status,conclusion,createdAt,url",
          "--limit", "100")
IDS = ("gh", "workflow", "list", "--all", "--json", "id,path")
#: How long each of the two commands may run, unless the setting says.
STATUS_TIMEOUT_S = 60

GREEN = "green"
PENDING = "pending"
RED = "red"
ABSENT = "absent"
#: Which reading the whole is, where not every workflow reads green: the first of
#: these that one does.
_FIRST = (RED, ABSENT, PENDING)

_STATUS_FIELDS = ("headSha", "workflowDatabaseId", "status", "conclusion", "createdAt")


@dataclass(frozen=True)
class Declared:
    """A repo's `ci` setting: the `workflows`, the words of the `local` command as
    written (None for `none`) and its `local_timeout_s`, the `remote` to push to, the
    `status` and `ids` commands, and `status_timeout_s`."""

    workflows: tuple[str, ...]
    local: tuple[str, ...] | None
    local_timeout_s: float | None
    remote: str | None
    status: tuple[str, ...]
    ids: tuple[str, ...]
    status_timeout_s: float


def declared(settings, repo: str) -> Declared | None:
    """The `ci` setting of the repo, or None when it declares none."""
    found = own.build_setting(settings, repo, "ci")
    if not isinstance(found, Mapping):
        return None
    local = found.get("local")
    is_command = isinstance(local, Mapping)
    return Declared(
        workflows=tuple(found.get("workflows") or ()),
        local=tuple(shlex.split(local["command"])) if is_command else None,
        local_timeout_s=local.get("timeout_s") if is_command else None,
        remote=found.get("remote"),
        status=tuple(found.get("status") or STATUS),
        ids=tuple(found.get("ids") or IDS),
        status_timeout_s=found.get("status_timeout_s") or STATUS_TIMEOUT_S)


@dataclass(frozen=True)
class Now:
    """What CI's form of the gate compares at a tip, as the setting gives it now: the
    `workflows`, and the argv of the local run with its placeholders filled (None for
    `none`)."""

    workflows: tuple[str, ...]
    local: tuple[str, ...] | None


def now_of(found: Declared, values: Mapping[str, str]) -> Now:
    """What the setting `found` gives now for a build whose placeholders read `values`."""
    local = None if found.local is None else tuple(checking.fill(found.local, values))
    return Now(found.workflows, local)


# -- the read ----------------------------------------------------------------------------

class ReadFailed(Exception):
    """A read of CI that gave nothing: `command` is the argv that failed, `why` says how."""

    def __init__(self, command: Sequence[str], why: str):
        super().__init__(f"{shlex.join(command)}: {why}")
        self.command = tuple(command)
        self.why = why


@dataclass(frozen=True)
class Reading:
    """What a read of CI found at `tip`: the two argvs that ran, each workflow as
    `{"path", "id", "reading", "url"}`, and the `whole` reading."""

    tip: str
    status_argv: tuple[str, ...]
    ids_argv: tuple[str, ...]
    workflows: tuple[dict, ...]
    whole: str


def _json(argv: Sequence[str], *, cwd, timeout_s: float):
    try:
        done = proc.run(list(argv), timeout_s=timeout_s, env=gitreads.git_env(),
                        cwd=cwd)
    except subprocess.TimeoutExpired:
        raise ReadFailed(argv, f"ran past its limit of {timeout_s:g} s") from None
    except OSError as exc:
        raise ReadFailed(argv, f"could not be started: {exc}") from None
    if done.returncode != 0:
        said = (done.stderr or b"").decode(errors="replace").strip().splitlines()
        tail = f": {said[-1]}" if said else ""
        raise ReadFailed(argv, f"exited {done.returncode}{tail}")
    try:
        found = json.loads(done.stdout or b"")
    except ValueError:
        raise ReadFailed(argv, "printed something that is not JSON") from None
    if not isinstance(found, list) or not all(isinstance(one, dict) for one in found):
        raise ReadFailed(argv, "printed JSON that is not a list of objects")
    return found


def _ids(argv: Sequence[str], found: list[dict]) -> dict[str, object]:
    ids: dict[str, object] = {}
    for one in found:
        if "id" not in one or not isinstance(one.get("path"), str):
            raise ReadFailed(argv, f"printed an object with no id or path: {one!r}")
        if one["path"] in ids:
            raise ReadFailed(argv, f"printed two workflows with the path {one['path']}")
        ids[one["path"]] = one["id"]
    return ids


def _runs(argv: Sequence[str], found: list[dict]) -> list[dict]:
    for one in found:
        missing = [field for field in _STATUS_FIELDS if field not in one]
        if missing:
            raise ReadFailed(argv, f"printed a run with no {', '.join(missing)}: {one!r}")
    return found


def _word(run: Mapping) -> str:
    if run.get("status") != "completed":
        return PENDING
    return GREEN if run.get("conclusion") == "success" else RED


def reading(workflows: Sequence[str], ids: Mapping[str, object],
            runs: Sequence[Mapping], tip: str) -> tuple[tuple[dict, ...], str]:
    """Each workflow of `workflows` as `{"path", "id", "reading", "url"}`, read from the
    `ids` by path and the `runs` at `tip`, and the whole reading."""
    each = []
    for path in workflows:
        wanted = ids.get(path)
        at = [] if path not in ids else [
            run for run in runs
            if run.get("workflowDatabaseId") == wanted and run.get("headSha") == tip]
        newest = None
        for run in at:
            # The first listed wins a tie.
            if newest is None or str(run.get("createdAt")) > str(newest.get("createdAt")):
                newest = run
        each.append({"path": path, "id": wanted,
                     "reading": ABSENT if newest is None else _word(newest),
                     "url": None if newest is None else newest.get("url")})
    words = [one["reading"] for one in each]
    whole = GREEN if all(word == GREEN for word in words) else next(
        word for word in _FIRST if word in words)
    return tuple(each), whole


def read(found: Declared, *, values: Mapping[str, str], cwd) -> Reading:
    """Read CI at the tip `values["tip"]` with the setting `found`, running its two
    commands in `cwd`. Raises `ReadFailed` for a failed read."""
    status = tuple(checking.fill(found.status, values))
    ids = tuple(checking.fill(found.ids, values))
    runs = _runs(status, _json(status, cwd=cwd, timeout_s=found.status_timeout_s))
    by_path = _ids(ids, _json(ids, cwd=cwd, timeout_s=found.status_timeout_s))
    each, whole = reading(found.workflows, by_path, runs, values["tip"])
    return Reading(values["tip"], status, ids, each, whole)


# -- what the gate reads -----------------------------------------------------------------

def newest(state: states.State, stage: str, tip: str | None = None) -> dict | None:
    """The newest `ci.read` of the stage's last build, or with `tip` the newest at that
    commit; None when there is none."""
    build = builds.current(state, stage)
    if build is None:
        return None
    found = None
    for event in state.history:
        if (event["n"] > build.n and event["type"] == READ
                and event.get("stage") == stage
                and (tip is None or event.get("commit") == tip)):
            found = event
    return found


def why_not(state: states.State, stage: str, *, tip: str, now: Now) -> str | None:
    """Why CI's form of the check gate is not met for the stage at `tip`, apart from the
    target, in words; None when it is."""
    found = newest(state, stage, tip)
    if found is None:
        return f"CI has no reading on record for {stage} at {tip[:12]}"
    named = [one.get("path") for one in found.get("workflows") or ()]
    if named != list(now.workflows):
        return (f"CI was read at {tip[:12]} for {', '.join(map(str, named))}, and the "
                f"workflows named now are {', '.join(now.workflows)}")
    if found.get("reading") != GREEN:
        return f"CI reads {found.get('reading')} at {tip[:12]}"
    if now.local is not None:
        missing = checking.why_not(state, stage, LOCAL, tip=tip, argv=now.local)
        if missing is not None:
            return missing
    return None


def met(state: states.State, stage: str, *, tip: str, now: Now) -> bool:
    """Whether CI's form of the check gate is met for the stage at `tip`, apart from
    the target."""
    return why_not(state, stage, tip=tip, now=now) is None


__all__ = ["READ", "LOCAL", "NONE", "STATUS", "IDS", "STATUS_TIMEOUT_S", "GREEN",
           "PENDING", "RED", "ABSENT", "Declared", "declared", "Now", "now_of",
           "ReadFailed", "Reading", "reading", "read", "newest", "why_not", "met"]
