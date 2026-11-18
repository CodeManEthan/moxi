"""CI in place of the full check: the `ci` setting, the reading of a commit's runs by
workflow path, a failed read, `check` in a repo that declares `ci`, CI's form of the
check gate, and the doctor's row."""

from __future__ import annotations

import json
import os
import shlex
import stat
import sys
import textwrap
from pathlib import Path

import pytest
from moxi.config import ConfigError

from moxi_rounds import (checking, ci, commissioning, doctor, log, opening, paths,
                         settings, showing, steps)
from moxi_rounds import state as states
from moxi_rounds.cli import main
from moxi_rounds.errors import Refused

ROUND = "proj-a/a-round"
PY = sys.executable
FLOW = ".github/workflows/ci.yml"
OTHER = ".github/workflows/lint.yml"

STUB = """
import json, sys
from pathlib import Path
here = Path(sys.argv[1])
kind = sys.argv[2]
code = here / f"{kind}.exit"
if code.exists():
    sys.exit(int(code.read_text()))
print((here / f"{kind}.json").read_text())
"""


def run(**fields) -> dict:
    found = {"databaseId": 1, "workflowDatabaseId": 11, "headSha": "a" * 40,
             "status": "completed", "conclusion": "success",
             "createdAt": "2026-11-01T10:00:00Z", "url": "https://example.test/run/1"}
    found.update(fields)
    return found


# -- the reading -------------------------------------------------------------------------

TIP = "a" * 40
IDS = {FLOW: 11, OTHER: 12}


@pytest.mark.parametrize("fields, word", [
    ({}, ci.GREEN),
    ({"status": "in_progress", "conclusion": ""}, ci.PENDING),
    ({"conclusion": "failure"}, ci.RED),
    ({"conclusion": "cancelled"}, ci.RED),
    ({"conclusion": "skipped"}, ci.RED),
    ({"headSha": "b" * 40}, ci.ABSENT),
])
def test_one_workflow_reads_from_its_run_at_the_tip(fields, word):
    each, whole = ci.reading([FLOW], IDS, [run(**fields)], TIP)

    assert (each[0]["reading"], whole) == (word, word)


def test_the_newest_run_at_the_tip_decides_and_the_first_listed_wins_a_tie():
    older = run(conclusion="failure", createdAt="2026-11-01T09:00:00Z")
    newer = run(createdAt="2026-11-01T11:00:00Z", url="https://example.test/run/2")
    assert ci.reading([FLOW], IDS, [older, newer], TIP)[0][0]["url"] == (
        "https://example.test/run/2")

    first, second = run(conclusion="failure"), run()
    assert ci.reading([FLOW], IDS, [first, second], TIP)[1] == ci.RED


@pytest.mark.parametrize("words, whole", [
    ((ci.GREEN, ci.GREEN), ci.GREEN),
    ((ci.PENDING, ci.RED), ci.RED),
    ((ci.PENDING, ci.ABSENT), ci.ABSENT),
    ((ci.GREEN, ci.PENDING), ci.PENDING),
])
def test_the_whole_is_green_only_when_every_workflow_is(words, whole):
    shapes = {ci.GREEN: {}, ci.PENDING: {"status": "queued"},
              ci.RED: {"conclusion": "failure"}}
    runs = [run(workflowDatabaseId=wid, **shapes[word])
            for wid, word in zip((11, 12), words) if word != ci.ABSENT]

    assert ci.reading([FLOW, OTHER], IDS, runs, TIP)[1] == whole


def test_two_workflow_files_that_share_a_name_are_read_by_path():
    runs = [run(workflowDatabaseId=11, conclusion="failure"), run(workflowDatabaseId=12)]

    each, whole = ci.reading([FLOW, OTHER], IDS, runs, TIP)

    assert [(one["path"], one["reading"]) for one in each] == [(FLOW, ci.RED),
                                                               (OTHER, ci.GREEN)]
    assert whole == ci.RED


def test_a_path_the_ids_do_not_name_reads_absent():
    each, whole = ci.reading([".github/workflows/gone.yml"], IDS, [run()], TIP)

    assert (each[0]["id"], each[0]["reading"], whole) == (None, ci.ABSENT, ci.ABSENT)


def job(name: str, **fields) -> dict:
    found = {"name": name, "status": "completed", "conclusion": "success",
             "url": f"https://example.test/job/{name}"}
    found.update(fields)
    return found


def by_jobs(names, jobs, **fields):
    asked = []

    def jobs_of(one):
        asked.append(one["databaseId"])
        return jobs

    each, whole = ci.reading([ci.Workflow(FLOW, tuple(names))], IDS, [run(**fields)], TIP,
                             jobs_of=jobs_of)
    return each[0], whole, asked


def test_a_workflow_read_by_its_jobs_reads_only_the_jobs_it_names():
    jobs = [job("linux"), job("browser (ui, chromium)", conclusion="failure")]

    flow, whole, asked = by_jobs(["linux"], jobs, conclusion="failure")

    assert (flow["reading"], whole, asked) == (ci.GREEN, ci.GREEN, [1])
    assert flow["jobs"] == [{"name": "linux", "reading": ci.GREEN,
                             "url": "https://example.test/job/linux"}]
    assert by_jobs(["linux", "browser (ui, chromium)"], jobs)[1] == ci.RED


@pytest.mark.parametrize("fields, run_fields, word", [
    ({"status": "in_progress", "conclusion": ""}, {}, ci.PENDING),
    ({"conclusion": "cancelled"}, {}, ci.RED),
    ({"conclusion": "skipped"}, {}, ci.RED),
    (None, {"status": "in_progress", "conclusion": ""}, ci.PENDING),
    (None, {}, ci.ABSENT),
])
def test_a_named_job_reads_from_the_first_job_of_that_name(fields, run_fields, word):
    jobs = [job("linux (x)"), job("other")]
    if fields is not None:
        jobs += [job("linux", **fields), job("linux")]

    flow, whole, _asked = by_jobs(["linux"], jobs, **run_fields)

    assert (flow["jobs"][0]["reading"], flow["reading"], whole) == (word, word, word)


def test_a_workflow_read_by_its_jobs_with_no_run_at_the_tip_reads_absent_unasked():
    flow, whole, asked = by_jobs(["linux"], [job("linux")], headSha="b" * 40)

    assert (flow["reading"], whole, asked) == (ci.ABSENT, ci.ABSENT, [])
    assert flow["jobs"] == [{"name": "linux", "reading": ci.ABSENT, "url": None}]


# -- the setting -------------------------------------------------------------------------

CHECK = 'check = { command = "make check" }\n'


@pytest.mark.parametrize("table, key, said", [
    ('ci = { workflows = ["a.yml"], local = "none" }\n', "projects.proj-a.ci",
     "declares no check"),
    (CHECK + 'ci = { local = "none" }\n', "ci.workflows", "is missing"),
    (CHECK + 'ci = { workflows = [], local = "none" }\n', "ci.workflows",
     "names no workflow"),
    (CHECK + 'ci = { workflows = ["/abs/ci.yml"], local = "none" }\n', "ci.workflows",
     "not a path inside the repo"),
    (CHECK + 'ci = { workflows = ["../ci.yml"], local = "none" }\n', "ci.workflows",
     "not a path inside the repo"),
    (CHECK + 'ci = { workflows = ["a.yml"] }\n', "ci.local", "is missing"),
    (CHECK + 'ci = { workflows = ["a.yml"], local = "all" }\n', "ci.local",
     "neither a command table"),
    (CHECK + 'ci = { workflows = ["a.yml"], local = { command = "a \'b" } }\n',
     "ci.local.command", "does not split"),
    (CHECK + 'ci = { workflows = ["a.yml"], local = "none", status = [] }\n',
     "ci.status", "is not a command"),
    (CHECK + 'ci = { workflows = ["a.yml"], local = "none", ids = [] }\n', "ci.ids",
     "is not a command"),
    (CHECK + 'ci = { workflows = ["a.yml"], local = "none", jobs = [] }\n', "ci.jobs",
     "is not a command"),
    (CHECK + 'ci = { workflows = ["a.yml"], local = "none", builds = [] }\n',
     "ci.builds", "is not a key of ci"),
    (CHECK + 'ci = { workflows = [{ path = "a.yml" }], local = "none" }\n',
     "ci.workflows.jobs", "is missing"),
    (CHECK + 'ci = { workflows = [{ path = "a.yml", jobs = [] }], local = "none" }\n',
     "ci.workflows.jobs", "is not a list of job names"),
    (CHECK + 'ci = { workflows = [{ path = "a.yml", jobs = ["x", ""] }], local = "none" }\n',
     "ci.workflows.jobs", "is not a list of job names"),
    (CHECK + 'ci = { workflows = [{ path = "a.yml", jobs = ["x", "x"] }], local = "none" }\n',
     "ci.workflows.jobs", "names a job twice"),
    (CHECK + 'ci = { workflows = [{ path = "a.yml", jobs = ["x"], all = true }], '
     'local = "none" }\n', "ci.workflows.all", "is not a key of a workflow entry"),
    (CHECK + 'ci = { workflows = [{ path = "../a.yml", jobs = ["x"] }], local = "none" }\n',
     "ci.workflows", "not a path inside the repo"),
    (CHECK + 'ci = { workflows = [{ jobs = ["x"] }], local = "none" }\n',
     "ci.workflows", "not a path inside the repo"),
    (CHECK + 'ci = { workflows = ["a.yml"], local = "none", ref = "ci/{branch}" }\n',
     "ci.ref", "no remote"),
])
def test_the_config_check_refuses_a_ci_it_can_t_read(table, key, said):
    with pytest.raises(ConfigError) as caught:
        settings.check_text(f"[projects.proj-a]\n{table}")

    assert key in str(caught.value) and said in str(caught.value)


@pytest.mark.parametrize("remote, problem", [('""', "is empty"),
                                             ("3", "is an int, not a name")])
def test_a_ci_remote_that_is_not_a_name_is_refused_in_words_that_agree(remote, problem):
    with pytest.raises(ConfigError) as caught:
        settings.check_text(f'[projects.proj-a]\n{CHECK}ci = {{ workflows = ["a.yml"], '
                            f'local = "none", remote = {remote} }}\n')

    assert caught.value.key == "projects.proj-a.ci.remote"
    assert caught.value.problem == problem
    assert caught.value.fix == "give a remote's name, or leave the key out"


@pytest.mark.parametrize("ref", [
    "{branch}", "ci{branch}", "/{branch}", "ci/{branch}/x", "ci/{branch}{branch}",
    "ci/{tip}/{branch}", "refs/heads/ci/{branch}", "a..b/{branch}", "ci x/{branch}",
    "ci//{branch}", ".ci/{branch}", "ci.lock/{branch}", "-ci/{branch}", "ci/", ""])
def test_the_config_check_refuses_a_ref_that_is_not_a_prefix_then_the_branch(ref):
    with pytest.raises(ConfigError) as caught:
        settings.check_text(f'[projects.proj-a]\n{CHECK}ci = {{ workflows = ["a.yml"], '
                            f'local = "none", remote = "origin", ref = "{ref}" }}\n')

    assert caught.value.key == "projects.proj-a.ci.ref"


@pytest.mark.parametrize("ref", ["ci/{branch}", "ci-full/{branch}", "a/b/{branch}"])
def test_a_ref_of_a_prefix_then_the_branch_is_taken(ref):
    settings.check_text(f'[projects.proj-a]\n{CHECK}ci = {{ workflows = ["a.yml"], '
                        f'local = "none", remote = "origin", ref = "{ref}" }}\n')

    assert ci.ref_for(ref, "a/b") == ref.replace("{branch}", "a/b")


FULL_BESIDE = 'local = "none", remote = "origin", ref = "ci/{{branch}}", full_ref = "{}"'


@pytest.mark.parametrize("full_ref", [
    "{branch}", "ci-full{branch}", "ci-full/{branch}/x", "ci-full/{tip}/{branch}",
    "refs/heads/ci-full/{branch}", "ci full/{branch}", ".ci-full/{branch}", "ci-full/"])
def test_the_config_check_refuses_a_full_ref_that_is_not_a_prefix_then_the_branch(full_ref):
    with pytest.raises(ConfigError) as caught:
        settings.check_text(f'[projects.proj-a]\n{CHECK}ci = {{ workflows = ["a.yml"], '
                            + FULL_BESIDE.format(full_ref) + " }\n")

    assert caught.value.key == "projects.proj-a.ci.full_ref"
    assert 'full_ref = "ci-full/{branch}"' in caught.value.fix


@pytest.mark.parametrize("full_ref", ["ci/{branch}", "ci/full/{branch}", "ci/ci-full/{branch}"])
def test_the_config_check_refuses_a_full_ref_whose_prefix_and_ref_s_start_alike(full_ref):
    with pytest.raises(ConfigError) as caught:
        settings.check_text(f'[projects.proj-a]\n{CHECK}ci = {{ workflows = ["a.yml"], '
                            + FULL_BESIDE.format(full_ref) + " }\n")

    assert caught.value.key == "projects.proj-a.ci.full_ref"
    assert "one starts with the other" in caught.value.problem


def test_the_config_check_refuses_a_ref_that_starts_with_full_ref_s_prefix():
    with pytest.raises(ConfigError) as caught:
        settings.check_text(f'[projects.proj-a]\n{CHECK}ci = {{ workflows = ["a.yml"], '
                            'local = "none", remote = "origin", ref = "ci/full/{branch}", '
                            'full_ref = "ci/{branch}" }\n')

    assert caught.value.key == "projects.proj-a.ci.full_ref"
    assert "one starts with the other" in caught.value.problem


def test_the_config_check_refuses_a_full_ref_with_no_ref():
    with pytest.raises(ConfigError) as caught:
        settings.check_text(f'[projects.proj-a]\n{CHECK}ci = {{ workflows = ["a.yml"], '
                            'local = "none", remote = "origin", '
                            'full_ref = "ci-full/{branch}" }\n')

    assert caught.value.key == "projects.proj-a.ci.full_ref"
    assert caught.value.problem == "is set with no ref beside it"


@pytest.mark.parametrize("full_ref", ["ci-full/{branch}", "full/{branch}", "cix/{branch}"])
def test_a_full_ref_beside_a_ref_of_another_prefix_is_taken(full_ref):
    settings.check_text(f'[projects.proj-a]\n{CHECK}ci = {{ workflows = ["a.yml"], '
                        + FULL_BESIDE.format(full_ref) + " }\n")


def test_the_setting_gives_both_templates_ref_s_first(write_config):
    write_config(f'[projects.proj-a]\n{CHECK}ci = {{ workflows = ["a.yml"], '
                 + FULL_BESIDE.format("ci-full/{branch}") + " }\n")

    found = ci.declared(settings.load(), "proj-a")

    assert (found.ref, found.full_ref) == ("ci/{branch}", "ci-full/{branch}")
    assert found.templates == ("ci/{branch}", "ci-full/{branch}")


def test_a_setting_that_reads_named_jobs_and_pushes_to_a_ci_ref_is_taken():
    settings.check_text(
        f'[projects.proj-a]\n{CHECK}ci = {{ remote = "origin", ref = "ci/{{branch}}", '
        'local = "none", jobs = ["gh", "run", "view", "{run}", "--json", "jobs"], '
        f'workflows = [{{ path = "{FLOW}", jobs = [\n  "linux",\n  "web (ubuntu-latest)"] }},'
        f'\n  "{OTHER}"] }}\n')


def test_a_ci_with_the_defaults_is_read_with_gh():
    settings.check_text(f'[projects.proj-a]\n{CHECK}ci = {{ workflows = ["{FLOW}"], '
                        'local = "none" }\n')


# -- the build ---------------------------------------------------------------------------

@pytest.fixture
def repo(git_repo, tmp_path):
    from moxi import cli

    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    return git_repo


class Stub:
    """The status and ids commands, printing what the test sets."""

    def __init__(self, stub_dir: Path):
        self.stub_dir = stub_dir
        stub_dir.mkdir()
        self.script = stub_dir / "stub.py"
        self.script.write_text(STUB, encoding="utf-8")
        self.ids({FLOW: 11})
        self.runs([])
        self.jobs([])

    def argv(self, kind: str, *more: str) -> list[str]:
        return [PY, str(self.script), str(self.stub_dir), kind, *more]

    def ids(self, by_path: dict) -> None:
        self._write("ids", [{"id": wid, "path": path} for path, wid in by_path.items()])

    def runs(self, found: list) -> None:
        self._write("status", found)

    def jobs(self, found: list) -> None:
        self._write("jobs", found)

    def _write(self, kind: str, data) -> None:
        (self.stub_dir / f"{kind}.json").write_text(json.dumps(data), encoding="utf-8")
        (self.stub_dir / f"{kind}.exit").unlink(missing_ok=True)

    def exits(self, kind: str, code: int) -> None:
        (self.stub_dir / f"{kind}.exit").write_text(str(code))


@pytest.fixture
def stub(tmp_path):
    return Stub(tmp_path / "stub")


@pytest.fixture
def setup(repo, stub, write_config, tmp_path, capsys):
    """`setup(local=..., remote=None, workflows=(FLOW,), extra="")`: write the config
    with a full check, `ci` reading the stub, and a local run (a Python line, or
    "none"); open the round with B1 commissioned and one commit on it. Returns
    `(round log, worktree repo, the commission)`."""
    done: list = []

    def configure(local: str = "print('local ran')", *, remote: str | None = None,
                  workflows=(FLOW,), status=None, ref: str | None = None,
                  full_ref: str | None = None, extra: str = ""):
        full = shlex.join([PY, "-c", "print('full ran')"])
        if local == ci.NONE:
            local_toml = '"none"'
        else:
            local_toml = f"{{ command = {json.dumps(shlex.join([PY, '-c', local]))} }}"
        status = stub.argv("status", "{tip}") if status is None else status
        flows = ", ".join(
            json.dumps(one) if isinstance(one, str)
            else f"{{ path = {json.dumps(one[0])}, jobs = {json.dumps(list(one[1]))} }}"
            for one in workflows)
        table = (f"workflows = [{flows}], local = {local_toml}, "
                 f"status = {json.dumps(status)}, ids = {json.dumps(stub.argv('ids'))}, "
                 f"jobs = {json.dumps(stub.argv('jobs', '{run}'))}")
        if remote is not None:
            table += f", remote = {json.dumps(remote)}"
        if ref is not None:
            table += f", ref = {json.dumps(ref)}"
        if full_ref is not None:
            table += f", full_ref = {json.dumps(full_ref)}"
        write_config(f"[projects.proj-a]\ncheck = {{ command = {json.dumps(full)} }}\n"
                     f"{extra}ci = {{ {table} }}\n")
        if done:
            return done[0]
        opening.open_round(ROUND, description="a placeholder round", by="planner",
                           flow="fix")
        path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
        log.append(path, "stage.declared",
                   {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
                   by="planner")
        made = commissioning.commission(ROUND, "B1", builder="coder", by="planner")
        tree = type(repo)(made.worktree)
        tree.commit("work.txt", "the work")
        capsys.readouterr()
        done.append((path, tree, made))
        return done[0]

    return configure


def events(path: Path, kind: str) -> list[dict]:
    return [one for one in log.read(path) if one["type"] == kind]


def green_at(stub: Stub, tip: str, **fields) -> None:
    stub.runs([run(**{"headSha": tip, **fields})])


def check_step(path: Path) -> steps.Step:
    state = states.fold(log.read_round(path))
    return steps.one(state, "check", "B1", name=ROUND,
                     seen=checking.seen_of(state, ROUND, settings=settings.load()))


# -- check by CI -------------------------------------------------------------------------

def test_a_check_by_ci_runs_the_local_part_and_records_the_read(setup, stub, repo):
    path, tree, _made = setup()
    green_at(stub, tree.rev())

    done = checking.check(ROUND, "B1", by="coder")

    [local] = events(path, "check.ran")
    [read] = events(path, ci.READ)
    assert (local["step"], local["commit"], local["exit_code"]) == (
        ci.LOCAL, tree.rev(), 0)
    assert Path(local["output"]).read_text().strip() == "local ran"
    assert (read["commit"], read["merge_base"], read["reading"]) == (
        tree.rev(), repo.rev("main"), ci.GREEN)
    assert read["status_argv"] == stub.argv("status", tree.rev())
    assert read["workflows"] == [{"path": FLOW, "id": 11, "reading": ci.GREEN,
                                  "url": "https://example.test/run/1"}]
    assert done.passed
    assert check_step(path).state == steps.DONE


def test_a_rerun_after_pending_reads_again_and_does_not_run_the_local_part_again(
        setup, stub, capsys):
    path, tree, _made = setup()
    green_at(stub, tree.rev(), status="in_progress", conclusion="")

    assert main(["check", ROUND, "B1"]) == 1
    assert "once CI ends" in capsys.readouterr().out
    assert check_step(path).state == steps.READY

    green_at(stub, tree.rev())
    assert main(["check", ROUND, "B1"]) == 0

    assert [one["step"] for one in events(path, "check.ran")] == [ci.LOCAL]
    assert [one["reading"] for one in events(path, ci.READ)] == [ci.PENDING, ci.GREEN]
    assert check_step(path).state == steps.DONE


def test_a_failed_local_part_stops_the_check_before_ci_is_read(setup, stub):
    path, tree, _made = setup("raise SystemExit(3)")
    green_at(stub, tree.rev())

    done = checking.check(ROUND, "B1", by="coder")

    assert [one["exit_code"] for one in events(path, "check.ran")] == [3]
    assert events(path, ci.READ) == [] and not done.passed


@pytest.mark.parametrize("how", ["exit", "bad-json", "missing-field", "two-ids",
                                 "not-on-path", "timeout"])
def test_a_failed_read_records_nothing_and_names_the_full_check(setup, stub, how,
                                                                tmp_path):
    status = None
    if how == "not-on-path":
        status = [str(tmp_path / "no-such-command")]
    elif how == "timeout":
        status = [PY, "-c", "import time; time.sleep(30)"]
    path, tree, _made = setup(ci.NONE, status=status)
    green_at(stub, tree.rev())
    if how == "exit":
        stub.exits("status", 4)
    elif how == "bad-json":
        (stub.stub_dir / "ids.json").write_text("not json")
    elif how == "missing-field":
        stub.runs([{"headSha": tree.rev()}])
    elif how == "two-ids":
        (stub.stub_dir / "ids.json").write_text(json.dumps(
            [{"id": 1, "path": FLOW}, {"id": 2, "path": FLOW}]))
    if how == "timeout":
        config = paths.LAYOUT.config_file()
        config.write_text(config.read_text().replace("ci = { ",
                                                     "ci = { status_timeout_s = 1, "))
    before = path.read_bytes()

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", by="coder")

    assert "CI could not be read" in str(caught.value)
    assert f"moxi rounds check {ROUND} B1 --full" in str(caught.value)
    assert path.read_bytes() == before


def test_full_runs_the_declared_check_and_meets_the_gate_whatever_ci_reads(setup, stub):
    path, tree, _made = setup()
    green_at(stub, tree.rev(), conclusion="failure")
    checking.check(ROUND, "B1", by="coder")
    assert check_step(path).state == steps.READY

    done = checking.check(ROUND, "B1", by="coder", full=True)

    assert (done.step, done.passed) == ("check", True)
    assert check_step(path).state == steps.DONE


def test_with_local_none_a_green_reading_alone_meets_the_gate(setup, stub):
    path, tree, _made = setup(ci.NONE)
    green_at(stub, tree.rev())

    checking.check(ROUND, "B1", by="coder")

    assert events(path, "check.ran") == []
    assert check_step(path).state == steps.DONE


@pytest.mark.parametrize("fields", [{"conclusion": "failure"},
                                    {"status": "queued", "conclusion": ""},
                                    {"headSha": "b" * 40}])
def test_red_pending_and_absent_do_not_meet_the_gate(setup, stub, fields):
    path, tree, _made = setup()
    green_at(stub, tree.rev(), **fields)

    checking.check(ROUND, "B1", by="coder")

    found = check_step(path)
    assert found.state == steps.READY
    assert found.owes[0].startswith("by the full check: check has no run on record")
    assert found.owes[1].startswith("by CI: CI reads ")


def test_a_reading_at_an_older_tip_does_not_meet_the_gate(setup, stub):
    path, tree, _made = setup()
    green_at(stub, tree.rev())
    checking.check(ROUND, "B1", by="coder")

    tree.commit("more.txt")

    assert check_step(path).owes[1] == (f"by CI: CI has no reading on record for B1 at "
                                        f"{tree.rev()[:12]}")


def test_a_workflow_list_or_a_local_command_changed_since_does_not_meet_the_gate(
        setup, stub):
    path, tree, _made = setup()
    green_at(stub, tree.rev())
    checking.check(ROUND, "B1", by="coder")

    setup(workflows=(FLOW, OTHER))
    assert "the workflows named now are" in check_step(path).owes[1]

    setup("print('another local')")
    assert "the command is now" in check_step(path).owes[1]


def test_the_check_pushes_to_the_remote_first_and_the_pre_push_hook_runs(
        setup, stub, repo, tmp_path):
    bare = tmp_path / "remote.git"
    repo.git("init", "-q", "--bare", str(bare))
    repo.git("remote", "add", "origin", str(bare))
    hook = Path(repo.git("rev-parse", "--git-common-dir"))
    hook = (hook if hook.is_absolute() else repo.root / hook) / "hooks" / "pre-push"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(f"#!/bin/sh\necho pushed >> {tmp_path / 'hook-ran'}\n")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    _path, tree, _made = setup(remote="origin")
    green_at(stub, tree.rev())

    done = checking.check(ROUND, "B1", by="coder")

    assert done.pushed == "origin"
    assert repo.git("--git-dir", str(bare), "rev-parse", "refs/heads/work") == tree.rev()
    assert (tmp_path / "hook-ran").read_text() == "pushed\n"


def test_a_check_reads_the_jobs_named_and_records_each(setup, stub):
    path, tree, _made = setup(ci.NONE, workflows=[(FLOW, ("linux", "web (ubuntu-latest)"))])
    green_at(stub, tree.rev(), databaseId=77, conclusion="failure")
    stub.jobs([job("linux"), job("web (ubuntu-latest)"),
               job("browser (ui, chromium)", conclusion="failure")])

    done = checking.check(ROUND, "B1", by="coder")

    [read] = events(path, ci.READ)
    assert done.passed and read["reading"] == ci.GREEN
    assert read["jobs_argvs"] == [stub.argv("jobs", "77")]
    assert [(one["name"], one["reading"]) for one in read["workflows"][0]["jobs"]] == [
        ("linux", ci.GREEN), ("web (ubuntu-latest)", ci.GREEN)]
    assert check_step(path).state == steps.DONE

    setup(ci.NONE, workflows=[(FLOW, ("linux",))])
    owes = check_step(path).owes[1]
    assert f"for {FLOW} (jobs linux, web (ubuntu-latest))" in owes
    assert f"named now are {FLOW} (jobs linux)" in owes
    setup(ci.NONE, workflows=[FLOW])
    assert "the workflows named now are" in check_step(path).owes[1]


def test_with_no_run_at_the_tip_a_workflow_read_by_its_jobs_owes_ci_reading_absent(
        setup, stub):
    path, tree, _made = setup(ci.NONE, workflows=[(FLOW, ("linux", "web (ubuntu-latest)"))])
    stub.runs([])

    checking.check(ROUND, "B1", by="coder")

    [read] = events(path, ci.READ)
    assert [(one["name"], one["reading"]) for one in read["workflows"][0]["jobs"]] == [
        ("linux", ci.ABSENT), ("web (ubuntu-latest)", ci.ABSENT)]
    assert check_step(path).owes[1] == f"by CI: CI reads absent at {tree.rev()[:12]}"


def test_a_failed_job_read_records_nothing(setup, stub):
    path, tree, _made = setup(ci.NONE, workflows=[(FLOW, ("linux",))])
    green_at(stub, tree.rev())
    stub.jobs([{"name": "linux"}])
    before = path.read_bytes()

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", by="coder")

    assert "printed a job with no status, conclusion" in str(caught.value)
    assert path.read_bytes() == before


def bare_remote(repo, tmp_path, hook: str = "") -> Path:
    bare = tmp_path / "remote.git"
    repo.git("init", "-q", "--bare", str(bare))
    repo.git("remote", "add", "origin", str(bare))
    if hook:
        hooks = Path(repo.git("rev-parse", "--git-common-dir"))
        hooks = (hooks if hooks.is_absolute() else repo.root / hooks) / "hooks"
        hooks.mkdir(exist_ok=True)
        (hooks / "pre-push").write_text(hook)
        (hooks / "pre-push").chmod(0o755)
    return bare


def test_with_a_ref_the_tip_is_forced_to_ci_s_own_branch_and_recorded(
        setup, stub, repo, tmp_path, capsys):
    bare = bare_remote(repo, tmp_path,
                       f"#!/bin/sh\ncat >> {tmp_path / 'hook-read'}\n")
    path, tree, _made = setup(remote="origin", ref="ci/{branch}")
    green_at(stub, tree.rev())

    assert main(["check", ROUND, "B1"]) == 0

    assert "pushed: origin ci/work, forced" in capsys.readouterr().out
    assert repo.git("--git-dir", str(bare), "rev-parse", "refs/heads/ci/work") == tree.rev()
    assert repo.git("--git-dir", str(bare), "branch", "--list", "work") == ""
    [local] = events(path, "check.ran")
    [read] = events(path, ci.READ)
    assert local["pushed"] == read["pushed"] == {"remote": "origin", "ref": "ci/work"}

    tree.git("commit", "-q", "--amend", "-m", "the work, reworded")
    green_at(stub, tree.rev())
    assert checking.check(ROUND, "B1", by="coder").ref == "ci/work"
    assert repo.git("--git-dir", str(bare), "rev-parse", "refs/heads/ci/work") == tree.rev()
    assert f"{tree.rev()} {tree.rev()} refs/heads/ci/work" in (tmp_path / "hook-read").read_text()


def test_the_check_never_pushes_the_name_full_ref_gives(setup, stub, repo, tmp_path,
                                                        capsys):
    bare = bare_remote(repo, tmp_path,
                       f"#!/bin/sh\ncat >> {tmp_path / 'hook-read'}\n")
    path, tree, _made = setup(remote="origin", ref="ci/{branch}",
                              full_ref="ci-full/{branch}")
    green_at(stub, tree.rev())

    assert main(["check", ROUND, "B1"]) == 0

    assert "pushed: origin ci/work, forced" in capsys.readouterr().out
    pushed = repo.git("--git-dir", str(bare), "for-each-ref", "--format=%(refname)")
    assert pushed.split() == ["refs/heads/ci/work"]
    assert "ci-full" not in (tmp_path / "hook-read").read_text()
    [local] = events(path, "check.ran")
    [read] = events(path, ci.READ)
    assert local["pushed"] == read["pushed"] == {"remote": "origin", "ref": "ci/work"}


#: A pre-push hook that, like a push guard, refuses a push with no ref lines, and keeps
#: each line it reads.
NEEDS_A_LINE = ("#!/bin/sh\nread line || {{ echo 'no ref lines' >&2; exit 1; }}\n"
                "echo \"$line\" >> {}\n")


def test_a_check_again_at_a_tip_the_ref_already_holds_pushes_nothing_and_reads_ci(
        setup, stub, repo, tmp_path, capsys):
    read = tmp_path / "hook-read"
    bare = bare_remote(repo, tmp_path, NEEDS_A_LINE.format(read))
    path, tree, _made = setup(remote="origin", ref="ci/{branch}")
    green_at(stub, tree.rev())
    assert main(["check", ROUND, "B1"]) == 0
    capsys.readouterr()

    assert main(["check", ROUND, "B1"]) == 0

    said = capsys.readouterr().out
    assert "pushed: nothing, origin ci/work is already at the tip" in said
    assert len(read.read_text().splitlines()) == 1
    assert repo.git("--git-dir", str(bare), "rev-parse", "refs/heads/ci/work") == tree.rev()
    reads = events(path, ci.READ)
    assert len(reads) == 2
    assert reads[-1]["pushed"] == {"remote": "origin", "ref": "ci/work"}


def test_a_ref_at_another_commit_is_pushed_again(setup, stub, repo, tmp_path):
    read = tmp_path / "hook-read"
    bare = bare_remote(repo, tmp_path, NEEDS_A_LINE.format(read))
    _path, tree, _made = setup(remote="origin", ref="ci/{branch}")
    repo.git("push", "-q", "--no-verify", "origin", "main:refs/heads/ci/work")
    green_at(stub, tree.rev())

    assert checking.check(ROUND, "B1", by="coder").already is False

    assert repo.git("--git-dir", str(bare), "rev-parse", "refs/heads/ci/work") == tree.rev()
    assert len(read.read_text().splitlines()) == 1


def test_a_branch_ls_remote_lists_by_its_tail_alone_is_not_taken_for_the_ref(
        setup, stub, repo, tmp_path):
    bare = bare_remote(repo, tmp_path)
    _path, tree, _made = setup(remote="origin", ref="ci/{branch}")
    repo.git("push", "-q", "origin", f"{tree.rev()}:refs/heads/x/refs/heads/ci/work")
    green_at(stub, tree.rev())

    assert checking.check(ROUND, "B1", by="coder").already is False

    assert repo.git("--git-dir", str(bare), "rev-parse", "refs/heads/ci/work") == tree.rev()


def test_without_a_ref_a_branch_already_at_the_tip_is_not_pushed_again(
        setup, stub, repo, tmp_path):
    read = tmp_path / "hook-read"
    bare_remote(repo, tmp_path, NEEDS_A_LINE.format(read))
    _path, tree, _made = setup(remote="origin")
    green_at(stub, tree.rev())
    assert checking.check(ROUND, "B1", by="coder").already is False

    assert checking.check(ROUND, "B1", by="coder").already is True

    assert len(read.read_text().splitlines()) == 1


def test_without_a_ref_the_push_goes_to_the_branch_name_and_records_no_ref(
        setup, stub, repo, tmp_path):
    bare = bare_remote(repo, tmp_path)
    path, tree, _made = setup(remote="origin")
    green_at(stub, tree.rev())

    assert checking.check(ROUND, "B1", by="coder").ref is None

    assert repo.git("--git-dir", str(bare), "rev-parse", "refs/heads/work") == tree.rev()
    assert all("pushed" not in one for one in events(path, ci.READ))


def test_a_push_to_ci_s_ref_a_guard_refuses_records_nothing_and_names_the_way_on(
        setup, stub, repo, tmp_path):
    bare_remote(repo, tmp_path, "#!/bin/sh\necho 'a secret in abc123' >&2\nexit 1\n")
    path, tree, _made = setup(remote="origin", ref="ci/{branch}")
    green_at(stub, tree.rev())
    before = path.read_bytes()

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", by="coder")

    said = str(caught.value)
    assert "a secret in abc123" in said and "was refused, so nothing is recorded" in said
    assert "give this text to your lead" in said and "--no-verify" in said
    assert path.read_bytes() == before


def test_a_build_on_the_target_is_refused_before_any_push(setup, stub, repo, tmp_path):
    bare = bare_remote(repo, tmp_path)
    path, tree, _made = setup(remote="origin", ref="ci/{branch}")
    setup(remote="origin", ref="ci/{branch}", extra='into = "work"\n')
    green_at(stub, tree.rev())
    before = path.read_bytes()

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", by="coder")

    assert "ci/work is the lead's to push" in str(caught.value)
    assert repo.git("--git-dir", str(bare), "for-each-ref") == ""
    assert path.read_bytes() == before


def test_a_refused_push_records_nothing(setup, stub, repo, tmp_path):
    path, tree, _made = setup(remote="nowhere")
    green_at(stub, tree.rev())
    before = path.read_bytes()

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", by="coder")

    assert "was refused, so nothing is recorded" in str(caught.value)
    assert path.read_bytes() == before


def test_a_repo_with_no_ci_is_checked_by_the_full_check(repo, write_config, tmp_path):
    full = shlex.join([PY, "-c", "print('full ran')"])
    write_config(f"[projects.proj-a]\ncheck = {{ command = {json.dumps(full)} }}\n")
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               by="planner")
    made = commissioning.commission(ROUND, "B1", builder="coder", by="planner")
    type(repo)(made.worktree).commit("work.txt")

    done = checking.check(ROUND, "B1", by="coder")

    assert isinstance(done, checking.Ran) and done.step == "check"
    assert events(path, ci.READ) == []


# -- where a reading is shown ------------------------------------------------------------

def test_next_and_show_give_the_newest_reading_and_what_the_gate_owes(setup, stub,
                                                                      capsys):
    _path, tree, _made = setup()
    green_at(stub, tree.rev(), conclusion="failure")
    checking.check(ROUND, "B1", by="coder")

    assert main(["next", ROUND]) == 0
    printed = capsys.readouterr().out
    assert f"ci:    ci red at {tree.rev()[:7]}" in printed
    assert f"{FLOW}: red   https://example.test/run/1" in printed
    assert "owes:  by CI: CI reads red" in printed

    [row] = json.loads(showing.as_json(showing.show(ROUND)))["builds"]
    assert (row["ci"]["reading"], [one["step"] for one in row["step_runs"]]) == (
        ci.RED, [ci.LOCAL])
    assert f"ci red at {tree.rev()[:7]}" in showing.as_text(showing.show(ROUND))


# -- the doctor --------------------------------------------------------------------------

def ci_rows() -> list:
    return [row for row in doctor.rows() if row.name.startswith("ci ")]


def gh_on_path(tmp_path, monkeypatch, auth_exit: int) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!/bin/sh\nexit {auth_exit}\n")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def default_ci(write_config, local: str = '"none"') -> None:
    write_config(textwrap.dedent(f"""
        [projects.proj-a]
        check = {{ command = "make check" }}
        ci = {{ workflows = ["{FLOW}"], local = {local} }}
    """))


def test_the_doctor_warns_when_gh_is_not_on_path(repo, write_config, monkeypatch,
                                                  tmp_path):
    default_ci(write_config)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    [row] = ci_rows()

    assert (row.name, row.state) == ("ci proj-a", "warn")
    assert "gh is not on PATH" in row.detail and "--full" in row.fix


def test_the_doctor_warns_when_gh_is_not_logged_in(repo, write_config, monkeypatch,
                                                   tmp_path):
    default_ci(write_config)
    gh_on_path(tmp_path, monkeypatch, 1)

    [row] = ci_rows()

    assert row.state == "warn" and "gh auth status does not exit 0" in row.detail


def test_the_doctor_names_a_repo_that_meets_the_gate_on_ci_alone(repo, write_config,
                                                                 monkeypatch, tmp_path):
    default_ci(write_config)
    gh_on_path(tmp_path, monkeypatch, 0)
    [alone] = ci_rows()

    default_ci(write_config, '{ command = "make quick" }')
    [with_local] = ci_rows()

    assert alone.state == "ok" and "on CI alone" in alone.detail
    assert with_local.state == "ok" and "on CI alone" not in with_local.detail


@pytest.mark.parametrize("gh", ["missing", "logged out"])
def test_the_doctor_names_a_repo_on_ci_alone_in_a_row_that_warns(
        repo, write_config, monkeypatch, tmp_path, gh):
    if gh == "missing":
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    else:
        gh_on_path(tmp_path, monkeypatch, 1)
    default_ci(write_config)
    [alone] = ci_rows()

    default_ci(write_config, '{ command = "make quick" }')
    [with_local] = ci_rows()

    assert alone.state == "warn" and "on CI alone" in alone.detail
    assert with_local.state == "warn" and "on CI alone" not in with_local.detail


def test_the_doctor_asks_gh_with_no_git_variable_of_the_caller(repo, write_config,
                                                               monkeypatch, tmp_path):
    default_ci(write_config)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text('#!/bin/sh\n[ -z "$GIT_DIR" ] || exit 1\nexit 0\n')
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))

    [row] = ci_rows()

    assert row.state == "ok"
