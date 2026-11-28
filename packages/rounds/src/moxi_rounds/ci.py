"""CI in place of the full check: a repo's `ci` setting, the reading of a commit's CI runs,
and whether CI's form of the check gate is met at a tip.

**The setting** (`declared`): the workflows whose runs stand in for the full check, each
named by its file's path in the repo and read whole, or by the jobs of its run the entry
names (`Workflow`): by name, and by name pattern (`matrix`, each `*` standing for any
text), so a matrix's jobs need not be listed one by one. An entry may name its run's
`pick` job, the job that picks which of the others run; an entry marked
`reads_candidates` reads a merge queue's candidate (`candidate_workflows`), where the
others read a full run (`full_workflows`), a build's or the target's; and an entry's
`stands_for` names, for a job of its run, the job of the target's full run its failures
are compared with (`failures.is_own`); `local`, the short local run of what CI can't do, or the word `none`
where CI does all of it; `remote`, where `check` pushes the branch before it reads;
`ref`, a template of the branch name it pushes to, one fixed prefix ending in `/` and
then `{branch}` (`ref_problem`), where the ref is CI's own; `full_ref`, a second
template under the same rules, whose prefix and `ref`'s are not one the start of the
other, of a ref someone else pushes for a build's branch (`check` never pushes it),
which removal and the sweep read beside `ref`'s; and the three commands that read CI,
`status` (a commit's runs), `ids` (each workflow file's id) and `jobs` (one run's jobs),
each bounded by `status_timeout_s`.

**The read** (`read`). The commands run with no shell in the build's worktree, stdin
closed, with the caller's environment less every `GIT_` variable, and only their
standard output is read, as JSON. From `ids`: a list of objects, each with `id` and
`path`. From `status`: a list of objects, each with `headSha`, `workflowDatabaseId`,
`status`, `conclusion` and `createdAt`, and `url` when there is one. From `jobs`, run
once for each workflow read by its jobs that has a run at the tip, `{run}` filled with
that run's `databaseId`: a list of objects, each with `name`, `status` and
`conclusion`, and `url` when there is one. From `PICK_LOG`, run with the `--repo` the
setting's commands give (`gh_repo`) for the pick job of a completed run, `{job}` filled
with the job's `databaseId`: the job's log, whose last line holding `picked: ` and then
a JSON list of names gives the picked packages, and whose last line holding
`picked for <pattern>: ` and a JSON list gives what the pick picked for that `matrix`
pattern (`picks_in`); a pattern with no such line takes the picked packages. Any command failing to start, exiting other
than 0, running past its limit, or printing anything else (an object missing a field,
two `ids` objects with one `path`, a run read by its jobs with no `databaseId`, a pick
job's log with no `picked:` line) is a failed read (`ReadFailed`), and nothing is read from it.

**The reading** (`reading`). A path's id is the `id` of the one `ids` object with that
path; a path none names reads `absent`. Of the `status` objects whose
`workflowDatabaseId` is that id and whose `headSha` is the tip, the newest by
`createdAt`, the first listed on a tie, is the workflow's run (`run_of`); with none the
workflow reads `absent`. A workflow read whole reads its run: `green` (completed, with success),
`pending` (not completed) or `red` (completed, with any other conclusion). A workflow
read by its jobs reads each job it names from the first of the run's jobs with exactly
that name, in the same words, and by each `matrix` pattern every other job whose name the
pattern matches; a name no job has, or a pattern none matches, reads `pending` while the
run is not completed and `absent` once it is, and with no run at the tip each job it
names reads `absent`, unasked. Where the entry names a `pick` job and it reads `green`,
a job of a `matrix` pattern the pick did not run reads `skipped`: one concluded
`skipped`, and, once the run is completed, a pattern no job matches that the pick
picked nothing for (an empty list); a pattern no job matches that the pick picked
something for reads `absent`, as does every unmatched pattern where the pick is not
green. A job the entry names
as itself never reads `skipped`: concluded `skipped`, it reads `red`, and missing, it
reads `absent`. Such a workflow reads `green` only when every job it names reads `green`
or `skipped`, else the first of `red`, `absent` and `pending` that one reads; so does the
whole reading of every workflow. The picked packages are recorded as the workflow's
`picked`.

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
#: The job name a candidate's `check.local` run is read under.
LOCAL_JOB = "local"
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
#: A job of a `matrix` pattern the pick job did not run; it counts as green.
SKIPPED = "skipped"
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
#: The placeholder of `PICK_LOG` that a job's id fills.
JOB = "{job}"
#: The command that prints a job's log, read for the pick job's `picked:` line.
PICK_LOG = ("gh", "run", "view", "--job", JOB, "--log")
#: A line of a pick job's log that names what it picked, as a JSON list: `picked: ` the
#: packages, or `picked for <pattern>: ` what it picked for a `matrix` pattern's jobs.
_PICKED = re.compile(r"picked(?: for (.+?))?: (\[.*\])\s*$")


@dataclass(frozen=True)
class Workflow:
    """One workflow the setting names: its file's `path` in the repo, the `jobs` read of
    its run by name (None where the whole run is read), the name patterns of the jobs
    read by `matrix`, the `pick` job, whether it `reads_candidates` of a queue, and
    `stands_for`, each job of its run paired with the job of the target's full run it
    stands for."""

    path: str
    jobs: tuple[str, ...] | None = None
    matrix: tuple[str, ...] = ()
    pick: str | None = None
    reads_candidates: bool = False
    stands_for: tuple[tuple[str, str], ...] = ()

    @property
    def named(self) -> tuple:
        """What a `ci.read` record must name for this entry: path, jobs, patterns, pick."""
        return (self.path, self.jobs, self.matrix, self.pick)


@dataclass(frozen=True)
class Declared:
    """A repo's `ci` setting: the `workflows`, the words of the `local` command as
    written with `{memory_mib}` filled from its table (None for `none`), its
    `local_timeout_s` and `local_memory_mib` (None when it declares none), the
    `remote` to push to, the
    `ref` template (None where the tip goes to the build's own branch name), the
    `full_ref` template (None for none), the `status`, `ids` and `jobs` commands, and
    `status_timeout_s`."""

    workflows: tuple[Workflow, ...]
    local: tuple[str, ...] | None
    local_timeout_s: float | None
    remote: str | None
    status: tuple[str, ...]
    ids: tuple[str, ...]
    status_timeout_s: float
    ref: str | None = None
    jobs: tuple[str, ...] = JOBS
    full_ref: str | None = None
    local_memory_mib: int | None = None

    @property
    def templates(self) -> tuple[str, ...]:
        """The templates of CI's own refs that are set: `ref`'s, then `full_ref`'s."""
        return tuple(one for one in (self.ref, self.full_ref) if one is not None)

    @property
    def reads_jobs(self) -> bool:
        """Whether a workflow is read by its jobs, so the `jobs` command runs."""
        return any(one.jobs is not None for one in self.workflows)

    @property
    def full_workflows(self) -> tuple[Workflow, ...]:
        """The workflows a full run is read by: every one not marked `reads_candidates`."""
        return tuple(one for one in self.workflows if not one.reads_candidates)

    @property
    def candidate_workflows(self) -> tuple[Workflow, ...]:
        """The workflows a queue's candidate is read by: those marked `reads_candidates`,
        or, with none marked, the full run's."""
        return (tuple(one for one in self.workflows if one.reads_candidates)
                or self.full_workflows)


def _workflow(entry) -> Workflow:
    if isinstance(entry, Mapping):
        return Workflow(str(entry.get("path")), tuple(entry.get("jobs") or ()),
                        tuple(entry.get("matrix") or ()), entry.get("pick"),
                        reads_candidates=bool(entry.get("reads_candidates")),
                        stands_for=tuple((str(job), str(stood))
                                         for job, stood in
                                         (entry.get("stands_for") or {}).items()))
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
        local=(tuple(own.fill_memory(shlex.split(local["command"]), local)) if is_command
               else None),
        local_timeout_s=local.get("timeout_s") if is_command else None,
        local_memory_mib=own.memory_mib(local) if is_command else None,
        remote=found.get("remote"),
        status=tuple(found.get("status") or STATUS),
        ids=tuple(found.get("ids") or IDS),
        status_timeout_s=found.get("status_timeout_s") or STATUS_TIMEOUT_S,
        ref=found.get("ref"),
        jobs=tuple(found.get("jobs") or JOBS),
        full_ref=found.get("full_ref"))


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


def overlap_problem(ref: str, full_ref: str) -> str | None:
    """Why the templates `ref` and `full_ref` can't stand together: a prefix that starts
    with the other's would let one ref name belong to both. None when they can."""
    first, second = prefix_of(ref), prefix_of(full_ref)
    if first.startswith(second) or second.startswith(first):
        return (f"has the prefix {second!r}, and ref has {first!r}: one starts with the "
                "other, so a ref name could belong to both")
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
    return Now(found.full_workflows, local)


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


def _output(argv: Sequence[str], *, cwd, timeout_s: float) -> bytes:
    """What `argv` printed on its standard output. Raises `ReadFailed` when it fails."""
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
    return done.stdout or b""


def _json(argv: Sequence[str], *, cwd, timeout_s: float):
    try:
        found = json.loads(_output(argv, cwd=cwd, timeout_s=timeout_s))
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
    """`green` when every word is `green` or `skipped`, else the first of `_FIRST` that
    one is."""
    return GREEN if all(word in (GREEN, SKIPPED) for word in words) else next(
        word for word in _FIRST if word in words)


def matches(pattern: str, name: str) -> bool:
    """Whether the job name `name` matches the `matrix` pattern `pattern`, each `*` in it
    standing for any text, the rest for itself."""
    return re.fullmatch(".*".join(map(re.escape, pattern.split("*"))), name) is not None


def _job_words(flow: Workflow, run: Mapping, jobs: Sequence[Mapping],
               picked_of: Callable[[Mapping], Mapping[str | None, list[str]]] | None = None,
               ) -> tuple[list[dict], list[str] | None]:
    """Each job `flow` names, and each its `matrix` patterns match, as `{"name",
    "reading", "url"}` (a pattern's also with `matrix`), read from the run's `jobs`; and
    the packages its pick job picked, read by `picked_of` (`picks_in`'s form), None
    where none were read."""
    ended = run.get("status") == "completed"
    missing = ABSENT if ended else PENDING
    each: list[tuple[dict, Mapping | None]] = []
    for name in flow.jobs or ():
        job = next((one for one in jobs if one.get("name") == name), None)
        each.append(({"name": name, "reading": missing if job is None else _word(job),
                      "url": None if job is None else job.get("url")}, job))
    for pattern in flow.matrix:
        found = [one for one in jobs if str(one.get("name")) not in (flow.jobs or ())
                 and matches(pattern, str(one.get("name")))]
        each += [({"name": str(job.get("name")), "matrix": pattern,
                   "reading": _word(job), "url": job.get("url")}, job) for job in found]
        if not found:
            each.append(({"name": pattern, "matrix": pattern, "reading": missing,
                          "url": None}, None))
    pick = next((job for one, job in each
                 if flow.pick is not None and one["name"] == flow.pick
                 and "matrix" not in one and one["reading"] == GREEN), None)
    if pick is None:
        return [one for one, _job in each], None
    picks = picked_of(pick) if ended and picked_of is not None else None
    for one, job in each:
        # A job the entry names as itself never reads skipped: a skip of one is a lost
        # check, not one the pick left out.
        if "matrix" not in one:
            continue
        if job is None:
            # A pattern no job matches: skipped only where the pick picked nothing for it.
            if (one["reading"] == ABSENT and picks is not None
                    and not picks.get(one["matrix"], picks[None])):
                one["reading"] = SKIPPED
        elif job.get("status") == "completed" and job.get("conclusion") == "skipped":
            one["reading"] = SKIPPED
    return [one for one, _job in each], None if picks is None else list(picks[None])


def run_of(flow: Workflow, ids: Mapping[str, object], runs: Sequence[Mapping],
           tip: str) -> Mapping | None:
    """The run `flow` is read by at `tip`: of the `runs` of its file's id (by `ids`)
    whose `headSha` is `tip`, the newest by `createdAt`, the first listed on a tie; None
    when there is none."""
    if flow.path not in ids:
        return None
    wanted = ids[flow.path]
    newest = None
    for run in runs:
        if run.get("workflowDatabaseId") != wanted or run.get("headSha") != tip:
            continue
        # The first listed wins a tie.
        if newest is None or str(run.get("createdAt")) > str(newest.get("createdAt")):
            newest = run
    return newest


def reading(workflows: Sequence[str | Workflow], ids: Mapping[str, object],
            runs: Sequence[Mapping], tip: str, *,
            jobs_of: Callable[[Mapping], Sequence[Mapping]] | None = None,
            picked_of: Callable[[Mapping], Mapping[str | None, list[str]]] | None = None,
            ) -> tuple[tuple[dict, ...], str]:
    """Each workflow of `workflows` as `{"path", "id", "reading", "url"}`, read from the
    `ids` by path and the `runs` at `tip`, and the whole reading. A workflow read by its
    jobs also holds `jobs`, each as `{"name", "reading", "url"}`, read from what
    `jobs_of` gives for its run, or each `absent` where it has no run at `tip`; with
    `matrix` patterns, a `pick` job or `stands_for` it names them too (`matrix`, `pick`,
    `stands_for`), and the packages its green pick job picked, which `picked_of` reads
    from that job in `picks_in`'s form, as `picked`."""
    each = []
    for flow in workflows:
        flow = flow if isinstance(flow, Workflow) else Workflow(flow)
        wanted = ids.get(flow.path)
        newest = run_of(flow, ids, runs, tip)
        one = {"path": flow.path, "id": wanted,
               "reading": ABSENT if newest is None else _word(newest),
               "url": None if newest is None else newest.get("url")}
        if flow.matrix:
            one["matrix"] = list(flow.matrix)
        if flow.pick is not None:
            one["pick"] = flow.pick
        if flow.stands_for:
            one["stands_for"] = dict(flow.stands_for)
        if flow.jobs is not None and newest is None:
            one["jobs"] = [{"name": name, "reading": ABSENT, "url": None}
                           for name in flow.jobs] + [
                {"name": pattern, "matrix": pattern, "reading": ABSENT, "url": None}
                for pattern in flow.matrix]
        elif flow.jobs is not None:
            one["jobs"], picked = _job_words(
                flow, newest, jobs_of(newest) if jobs_of is not None else (), picked_of)
            if picked is not None:
                one["picked"] = picked
            one["reading"] = _whole([job["reading"] for job in one["jobs"]])
        each.append(one)
    return tuple(each), _whole([one["reading"] for one in each])


def _jobs(argv: Sequence[str], found: list[dict]) -> list[dict]:
    for one in found:
        missing = [field for field in _JOB_FIELDS if field not in one]
        if missing:
            raise ReadFailed(argv, f"printed a job with no {', '.join(missing)}: {one!r}")
    return found


def read(found: Declared, *, values: Mapping[str, str], cwd) -> Reading:
    """Read CI at the tip `values["tip"]` by the setting's full workflows
    (`Declared.full_workflows`), running its commands in `cwd`. Raises `ReadFailed` for
    a failed read."""
    return read_runs(found, values=values, cwd=cwd)[0]


def read_runs(found: Declared, *, values: Mapping[str, str], cwd,
              is_candidate: bool = False) -> tuple[Reading, tuple[dict, ...]]:
    """`read`'s reading, by the candidate's workflows with `is_candidate`
    (`Declared.candidate_workflows`), and the runs at the tip that each workflow read
    (`run_of`: the newest of its file's runs there), as the `status` command printed
    them."""
    status = tuple(checking.fill(found.status, values))
    ids = tuple(checking.fill(found.ids, values))
    runs = _runs(status, _json(status, cwd=cwd, timeout_s=found.status_timeout_s))
    by_path = _ids(ids, _json(ids, cwd=cwd, timeout_s=found.status_timeout_s))
    jobs_argvs: list[tuple[str, ...]] = []

    def jobs_of(run: Mapping) -> list[dict]:
        if run.get("databaseId") is None:
            raise ReadFailed(status, f"printed a run with no databaseId: {dict(run)!r}")
        argv, jobs = run_jobs(found, str(run["databaseId"]), values=values, cwd=cwd)
        jobs_argvs.append(argv)
        return jobs

    def picked_of(job: Mapping) -> dict[str | None, list[str]]:
        if job.get("databaseId") is None:
            raise ReadFailed(found.jobs, f"printed a pick job with no databaseId: "
                                         f"{dict(job)!r}")
        argv = (*(word.replace(JOB, str(job["databaseId"])) for word in PICK_LOG),
                *gh_repo(found))
        return picks_in(argv, _output(argv, cwd=cwd, timeout_s=found.status_timeout_s)
                        .decode(errors="replace"))

    flows = found.candidate_workflows if is_candidate else found.full_workflows
    each, whole = reading(flows, by_path, runs, values["tip"], jobs_of=jobs_of,
                          picked_of=picked_of)
    read_by: list[dict] = []
    for flow in flows:
        run = run_of(flow, by_path, runs, values["tip"])
        if run is not None and not any(run is one for one in read_by):
            read_by.append(run)
    return (Reading(values["tip"], status, ids, each, whole, tuple(jobs_argvs)),
            tuple(read_by))


def picks_in(argv: Sequence[str], log: str) -> dict[str | None, list[str]]:
    """What a pick job's `log` names: under None the packages, the JSON list after
    `picked: ` on its last line that holds one, and under each `matrix` pattern what it
    picked for that pattern, the JSON list after `picked for <pattern>: ` on its last
    line that holds one. Raises `ReadFailed`, naming `argv`, when no line names the
    packages."""
    found: dict[str | None, list[str]] = {}
    for line in log.splitlines():
        match = _PICKED.search(line)
        if match is None:
            continue
        try:
            names = json.loads(match.group(2))
        except ValueError:
            continue
        if isinstance(names, list) and all(isinstance(one, str) for one in names):
            found[match.group(1)] = names
    if None not in found:
        raise ReadFailed(argv, "printed no line picked: <JSON list of names>")
    return found



def gh_repo(found: Declared) -> tuple[str, ...]:
    """The `--repo` argument the setting's `status`, `ids` or `jobs` command gives gh,
    the first found, as `("--repo", NAME)`; empty when none gives one."""
    for argv in (found.status, found.ids, found.jobs):
        for index, word in enumerate(argv):
            if word in ("--repo", "-R") and index + 1 < len(argv):
                return ("--repo", argv[index + 1])
            if word.startswith("--repo="):
                return ("--repo", word.removeprefix("--repo="))
    return ()


def run_jobs(found: Declared, run: str, *, values: Mapping[str, str],
             cwd) -> tuple[tuple[str, ...], list[dict]]:
    """The jobs of the run `run` by the setting's `jobs` command, each with `name`,
    `status` and `conclusion`, and the argv that ran. Raises `ReadFailed` for a failed
    read."""
    argv = tuple(word.replace(RUN, run) for word in checking.fill(found.jobs, values))
    return argv, _jobs(argv, _json(argv, cwd=cwd, timeout_s=found.status_timeout_s))


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
    if named != [one.named for one in now.workflows]:
        return (f"CI was read at {tip[:12]} for {_names(named)}, and the workflows "
                f"named now are {_names(one.named for one in now.workflows)}")
    if found.get("reading") != GREEN:
        return f"CI reads {found.get('reading')} at {tip[:12]}"
    if now.local is not None:
        missing = checking.why_not(state, stage, LOCAL, tip=tip, argv=now.local)
        if missing is not None:
            return missing
    return None


def candidate_why_not(state: states.State, stage: str, *, tip: str,
                      has_local: bool) -> str | None:
    """Why a queue member's check is not met at `tip` by its candidate, in words; None
    when it is: the newest `ci.read` at `tip` names a candidate and reads green, or red
    only on failures excused as the target's own (`excused`, each `{"job", "test"}`),
    and the newest `check.local` at `tip` names that candidate and passed or has its job
    `local` excused. With no `has_local` (the repo's `local` is `none`), a missing
    `check.local` is no reason."""
    found = newest(state, stage, tip)
    if found is None or not found.get("candidate"):
        return f"no candidate of {stage}'s at {tip[:12]} was read"
    if found.get("reading") != GREEN and not (found.get("reading") == RED
                                              and found.get("excused")):
        return (f"the candidate {str(found['candidate'])[:12]} reads "
                f"{found.get('reading')}")
    local = checking.newest(state, stage, LOCAL)
    if local is None or local.get("commit") != tip \
            or local.get("candidate") != found["candidate"]:
        if has_local:
            return f"no {LOCAL} ran at the candidate {str(found['candidate'])[:12]}"
        return None
    if (local.get("exit_code") != 0
            and not any(one.get("job") == LOCAL_JOB for one in found.get("excused") or ())):
        return f"{LOCAL} exited {local.get('exit_code')} at the candidate"
    return None


def _named(recorded: Mapping) -> tuple:
    """A workflow of a `ci.read` record as the setting names one (`Workflow.named`): its
    path, the names of the jobs read by name (None where the whole run was read), its
    `matrix` patterns and its `pick` job."""
    jobs = recorded.get("jobs")
    return (str(recorded.get("path")),
            None if jobs is None else tuple(str(one.get("name")) for one in jobs
                                            if "matrix" not in one),
            tuple(str(one) for one in recorded.get("matrix") or ()),
            recorded.get("pick"))


def _names(named) -> str:
    def one(path, jobs, matrix, _pick) -> str:
        if jobs is None:
            return path
        return f"{path} (jobs {', '.join((*jobs, *matrix))})"

    return ", ".join(one(*each) for each in named)


def met(state: states.State, stage: str, *, tip: str, now: Now) -> bool:
    """Whether CI's form of the check gate is met for the stage at `tip`, apart from
    the target."""
    return why_not(state, stage, tip=tip, now=now) is None


__all__ = ["READ", "LOCAL", "NONE", "STATUS", "IDS", "JOBS", "BRANCH", "STATUS_TIMEOUT_S",
           "GREEN", "SKIPPED", "PENDING", "RED", "ABSENT", "Workflow", "Declared",
           "declared", "matches", "JOB", "PICK_LOG", "picks_in", "gh_repo",
           "ref_problem", "overlap_problem", "prefix_of", "ref_for", "RUN", "Now", "now_of",
           "ReadFailed", "Reading", "run_of", "reading", "read", "read_runs", "run_jobs",
           "newest",
           "why_not", "met",
           "LOCAL_JOB", "candidate_why_not"]
