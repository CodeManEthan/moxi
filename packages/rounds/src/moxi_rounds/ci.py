"""CI in place of the full check: a repo's `ci` setting, the reading of a commit's CI runs,
and whether CI's form of the check gate is met at a tip.

**The setting** (`declared`): the workflows whose runs stand in for the full check, each
named by its file's path in the repo and read whole, or by the jobs of its run the entry
names (`Workflow`); `local`, the short local run of what CI can't do, or the word `none`
where CI does all of it; `remote`, where `check` pushes the branch before it reads;
`ref`, a template of the branch name it pushes to, one fixed prefix ending in `/` and
then `{branch}` (`ref_problem`), where the ref is CI's own; and the three commands that
read CI, `status` (a commit's runs), `ids` (each workflow file's id) and `jobs` (one
run's jobs), each bounded by `status_timeout_s`.

**The read** (`read`). The commands run with no shell in the build's worktree, stdin
closed, with the caller's environment less every `GIT_` variable, and only their
standard output is read, as JSON. From `ids`: a list of objects, each with `id` and
`path`. From `status`: a list of objects, each with `headSha`, `workflowDatabaseId`,
`status`, `conclusion` and `createdAt`, and `url` when there is one. From `jobs`, run
once for each workflow read by its jobs that has a run at the tip, `{run}` filled with
that run's `databaseId`: a list of objects, each with `name`, `status` and
`conclusion`, and `url` when there is one. Any command failing to start, exiting other
than 0, running past its limit, or printing anything else (an object missing a field,
two `ids` objects with one `path`, a run read by its jobs with no `databaseId`) is a
failed read (`ReadFailed`), and nothing is read from it.

**The reading** (`reading`). A path's id is the `id` of the one `ids` object with that
path; a path none names reads `absent`. Of the `status` objects whose
`workflowDatabaseId` is that id and whose `headSha` is the tip, the newest by
`createdAt`, the first listed on a tie, is the workflow's run; with none the workflow
reads `absent`. A workflow read whole reads its run: `green` (completed, with success),
`pending` (not completed) or `red` (completed, with any other conclusion). A workflow
read by its jobs reads each job it names from the first of the run's jobs with exactly
that name, in the same words; a name no job has reads `pending` while the run is not
completed and `absent` once it is. Such a workflow reads `green` only when every job it
names does, else the first of `red`, `absent` and `pending` that one reads; so does the
whole reading of every workflow.

**CI's form of the gate** (`why_not`), apart from the target: the newest `ci.read` of
the build at the tip reads `green` and named the workflows the setting names now, each
with the jobs it names now; and,
unless `local` is `none`, the newest run of `check.local` passed at the tip with the
argv `local` gives now. Whether the tip holds the target is the finish's to read.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from collections.abc import Callable, Mapping, Sequence
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
#: The default command that lists one run's jobs, `{run}` its id.
JOBS = ("gh", "run", "view", "{run}", "--json", "jobs", "--jq", ".jobs")
#: The placeholder of `ref` that the build's branch fills.
BRANCH = "{branch}"
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
_JOB_FIELDS = ("name", "status", "conclusion")
#: The placeholder of `jobs` that a run's id fills.
RUN = "{run}"


@dataclass(frozen=True)
class Workflow:
    """One workflow the setting names: its file's `path` in the repo, and the `jobs`
    read of its run, None where the whole run is read."""

    path: str
    jobs: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Declared:
    """A repo's `ci` setting: the `workflows`, the words of the `local` command as
    written (None for `none`) and its `local_timeout_s`, the `remote` to push to, the
    `ref` template (None where the tip goes to the build's own branch name), the
    `status`, `ids` and `jobs` commands, and `status_timeout_s`."""

    workflows: tuple[Workflow, ...]
    local: tuple[str, ...] | None
    local_timeout_s: float | None
    remote: str | None
    status: tuple[str, ...]
    ids: tuple[str, ...]
    status_timeout_s: float
    ref: str | None = None
    jobs: tuple[str, ...] = JOBS

    @property
    def reads_jobs(self) -> bool:
        """Whether a workflow is read by its jobs, so the `jobs` command runs."""
        return any(one.jobs is not None for one in self.workflows)


def _workflow(entry) -> Workflow:
    if isinstance(entry, Mapping):
        return Workflow(str(entry.get("path")), tuple(entry.get("jobs") or ()))
    return Workflow(str(entry))


def declared(settings, repo: str) -> Declared | None:
    """The `ci` setting of the repo, or None when it declares none."""
    found = own.build_setting(settings, repo, "ci")
    if not isinstance(found, Mapping):
        return None
    local = found.get("local")
    is_command = isinstance(local, Mapping)
    return Declared(
        workflows=tuple(_workflow(one) for one in found.get("workflows") or ()),
        local=tuple(shlex.split(local["command"])) if is_command else None,
        local_timeout_s=local.get("timeout_s") if is_command else None,
        remote=found.get("remote"),
        status=tuple(found.get("status") or STATUS),
        ids=tuple(found.get("ids") or IDS),
        status_timeout_s=found.get("status_timeout_s") or STATUS_TIMEOUT_S,
        ref=found.get("ref"),
        jobs=tuple(found.get("jobs") or JOBS))


# -- the ref -----------------------------------------------------------------------------

#: What a part of a branch name may not hold, as git reads one.
_NOT_IN_REF = re.compile(r"[\x00-\x20\x7f~^:?*\[\\{}]|\.\.|@\{|//")


def ref_problem(template: str) -> str | None:
    """Why `template` is not a `ref`: it must be one fixed prefix ending in `/`, a
    branch name's start git would take, then `{branch}` once, and nothing else. None
    when it is one."""
    if template.count(BRANCH) != 1 or not template.endswith(BRANCH):
        return f"holds {BRANCH} other than once at its end"
    prefix = template[:-len(BRANCH)]
    if not prefix.endswith("/") or prefix == "/":
        return "has no fixed prefix ending in / before {branch}"
    if (prefix.startswith(("/", "-", "refs/")) or _NOT_IN_REF.search(prefix)
            or any(part.startswith(".") or part.endswith(".lock")
                   for part in prefix[:-1].split("/"))):
        return (f"has the prefix {prefix!r}, which git takes as no start of a branch "
                "name, or which holds another placeholder")
    return None


def prefix_of(template: str) -> str:
    """The fixed prefix of the `ref` template `template`, its `/` kept."""
    return template[:-len(BRANCH)]


def ref_for(template: str, branch: str) -> str:
    """The branch name the `ref` template `template` gives the branch `branch`."""
    return prefix_of(template) + branch


@dataclass(frozen=True)
class Now:
    """What CI's form of the gate compares at a tip, as the setting gives it now: the
    `workflows`, and the argv of the local run with its placeholders filled (None for
    `none`)."""

    workflows: tuple[Workflow, ...]
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
    """What a read of CI found at `tip`: the argvs of `status` and `ids` that ran, each
    workflow as `reading` gives it, the `whole` reading, and the argv of each `jobs`
    command that ran."""

    tip: str
    status_argv: tuple[str, ...]
    ids_argv: tuple[str, ...]
    workflows: tuple[dict, ...]
    whole: str
    jobs_argvs: tuple[tuple[str, ...], ...] = ()


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


def _whole(words: Sequence[str]) -> str:
    """`green` when every word is, else the first of `_FIRST` that one is."""
    return GREEN if all(word == GREEN for word in words) else next(
        word for word in _FIRST if word in words)


def _job_words(names: Sequence[str], run: Mapping, jobs: Sequence[Mapping]) -> list[dict]:
    """Each job of `names` as `{"name", "reading", "url"}`, read from the run's `jobs`."""
    each = []
    for name in names:
        job = next((one for one in jobs if one.get("name") == name), None)
        if job is None:
            word = PENDING if run.get("status") != "completed" else ABSENT
        else:
            word = _word(job)
        each.append({"name": name, "reading": word,
                     "url": None if job is None else job.get("url")})
    return each


def reading(workflows: Sequence[str | Workflow], ids: Mapping[str, object],
            runs: Sequence[Mapping], tip: str, *,
            jobs_of: Callable[[Mapping], Sequence[Mapping]] | None = None,
            ) -> tuple[tuple[dict, ...], str]:
    """Each workflow of `workflows` as `{"path", "id", "reading", "url"}`, read from the
    `ids` by path and the `runs` at `tip`, and the whole reading. A workflow read by its
    jobs also holds `jobs`, each as `{"name", "reading", "url"}`, read from what
    `jobs_of` gives for its run."""
    each = []
    for flow in workflows:
        flow = flow if isinstance(flow, Workflow) else Workflow(flow)
        wanted = ids.get(flow.path)
        at = [] if flow.path not in ids else [
            run for run in runs
            if run.get("workflowDatabaseId") == wanted and run.get("headSha") == tip]
        newest = None
        for run in at:
            # The first listed wins a tie.
            if newest is None or str(run.get("createdAt")) > str(newest.get("createdAt")):
                newest = run
        one = {"path": flow.path, "id": wanted,
               "reading": ABSENT if newest is None else _word(newest),
               "url": None if newest is None else newest.get("url")}
        if flow.jobs is not None:
            jobs = [] if newest is None else _job_words(
                flow.jobs, newest, jobs_of(newest) if jobs_of is not None else ())
            one["jobs"] = jobs
            if newest is not None:
                one["reading"] = _whole([job["reading"] for job in jobs])
        each.append(one)
    return tuple(each), _whole([one["reading"] for one in each])


def _jobs(argv: Sequence[str], found: list[dict]) -> list[dict]:
    for one in found:
        missing = [field for field in _JOB_FIELDS if field not in one]
        if missing:
            raise ReadFailed(argv, f"printed a job with no {', '.join(missing)}: {one!r}")
    return found


def read(found: Declared, *, values: Mapping[str, str], cwd) -> Reading:
    """Read CI at the tip `values["tip"]` with the setting `found`, running its
    commands in `cwd`. Raises `ReadFailed` for a failed read."""
    status = tuple(checking.fill(found.status, values))
    ids = tuple(checking.fill(found.ids, values))
    runs = _runs(status, _json(status, cwd=cwd, timeout_s=found.status_timeout_s))
    by_path = _ids(ids, _json(ids, cwd=cwd, timeout_s=found.status_timeout_s))
    jobs_argvs: list[tuple[str, ...]] = []

    def jobs_of(run: Mapping) -> list[dict]:
        if run.get("databaseId") is None:
            raise ReadFailed(status, f"printed a run with no databaseId: {dict(run)!r}")
        argv = tuple(word.replace(RUN, str(run["databaseId"]))
                     for word in checking.fill(found.jobs, values))
        jobs_argvs.append(argv)
        return _jobs(argv, _json(argv, cwd=cwd, timeout_s=found.status_timeout_s))

    each, whole = reading(found.workflows, by_path, runs, values["tip"], jobs_of=jobs_of)
    return Reading(values["tip"], status, ids, each, whole, tuple(jobs_argvs))


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
    named = [_named(one) for one in found.get("workflows") or ()]
    if named != [(one.path, one.jobs) for one in now.workflows]:
        return (f"CI was read at {tip[:12]} for {_names(named)}, and the workflows "
                f"named now are {_names((one.path, one.jobs) for one in now.workflows)}")
    if found.get("reading") != GREEN:
        return f"CI reads {found.get('reading')} at {tip[:12]}"
    if now.local is not None:
        missing = checking.why_not(state, stage, LOCAL, tip=tip, argv=now.local)
        if missing is not None:
            return missing
    return None


def _named(recorded: Mapping) -> tuple[str, tuple[str, ...] | None]:
    """A workflow of a `ci.read` record as the setting names one: its path, and the
    names of the jobs read, None where the whole run was read."""
    jobs = recorded.get("jobs")
    return (str(recorded.get("path")),
            None if jobs is None else tuple(str(one.get("name")) for one in jobs))


def _names(named) -> str:
    return ", ".join(path if jobs is None else f"{path} (jobs {', '.join(jobs)})"
                     for path, jobs in named)


def met(state: states.State, stage: str, *, tip: str, now: Now) -> bool:
    """Whether CI's form of the check gate is met for the stage at `tip`, apart from
    the target."""
    return why_not(state, stage, tip=tip, now=now) is None


__all__ = ["READ", "LOCAL", "NONE", "STATUS", "IDS", "JOBS", "BRANCH", "STATUS_TIMEOUT_S",
           "GREEN", "PENDING", "RED", "ABSENT", "Workflow", "Declared", "declared",
           "ref_problem", "prefix_of", "ref_for", "RUN", "Now", "now_of",
           "ReadFailed", "Reading", "reading", "read", "newest", "why_not", "met"]
