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
     "is not a key of ci"),
])
def test_the_config_check_refuses_a_ci_it_can_t_read(table, key, said):
    with pytest.raises(ConfigError) as caught:
        settings.check_text(f"[projects.proj-a]\n{table}")

    assert key in str(caught.value) and said in str(caught.value)


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

    def __init__(self, folder: Path):
        self.folder = folder
        folder.mkdir()
        self.script = folder / "stub.py"
        self.script.write_text(STUB, encoding="utf-8")
        self.ids({FLOW: 11})
        self.runs([])

    def argv(self, kind: str, *more: str) -> list[str]:
        return [PY, str(self.script), str(self.folder), kind, *more]

    def ids(self, by_path: dict) -> None:
        self._write("ids", [{"id": wid, "path": path} for path, wid in by_path.items()])

    def runs(self, found: list) -> None:
        self._write("status", found)

    def _write(self, kind: str, data) -> None:
        (self.folder / f"{kind}.json").write_text(json.dumps(data), encoding="utf-8")
        (self.folder / f"{kind}.exit").unlink(missing_ok=True)

    def exits(self, kind: str, code: int) -> None:
        (self.folder / f"{kind}.exit").write_text(str(code))


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
                  workflows=(FLOW,), status=None):
        full = shlex.join([PY, "-c", "print('full ran')"])
        if local == ci.NONE:
            local_toml = '"none"'
        else:
            local_toml = f"{{ command = {json.dumps(shlex.join([PY, '-c', local]))} }}"
        status = stub.argv("status", "{tip}") if status is None else status
        table = (f"workflows = {json.dumps(list(workflows))}, local = {local_toml}, "
                 f"status = {json.dumps(status)}, ids = {json.dumps(stub.argv('ids'))}")
        if remote is not None:
            table += f", remote = {json.dumps(remote)}"
        write_config(f"[projects.proj-a]\ncheck = {{ command = {json.dumps(full)} }}\n"
                     f"ci = {{ {table} }}\n")
        if done:
            return done[0]
        opening.open_round(ROUND, description="a placeholder round", actor="planner",
                           flow="fix")
        path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
        log.append(path, "stage.declared",
                   {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
                   actor="planner")
        made = commissioning.commission(ROUND, "B1", builder="coder", actor="planner")
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

    done = checking.check(ROUND, "B1", actor="coder")

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

    done = checking.check(ROUND, "B1", actor="coder")

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
        (stub.folder / "ids.json").write_text("not json")
    elif how == "missing-field":
        stub.runs([{"headSha": tree.rev()}])
    elif how == "two-ids":
        (stub.folder / "ids.json").write_text(json.dumps(
            [{"id": 1, "path": FLOW}, {"id": 2, "path": FLOW}]))
    if how == "timeout":
        config = paths.LAYOUT.config_file()
        config.write_text(config.read_text().replace("ci = { ",
                                                     "ci = { status_timeout_s = 1, "))
    before = path.read_bytes()

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", actor="coder")

    assert "CI could not be read" in str(caught.value)
    assert f"moxi rounds check {ROUND} B1 --full" in str(caught.value)
    assert path.read_bytes() == before


def test_full_runs_the_declared_check_and_meets_the_gate_whatever_ci_reads(setup, stub):
    path, tree, _made = setup()
    green_at(stub, tree.rev(), conclusion="failure")
    checking.check(ROUND, "B1", actor="coder")
    assert check_step(path).state == steps.READY

    done = checking.check(ROUND, "B1", actor="coder", full=True)

    assert (done.step, done.passed) == ("check", True)
    assert check_step(path).state == steps.DONE


def test_with_local_none_a_green_reading_alone_meets_the_gate(setup, stub):
    path, tree, _made = setup(ci.NONE)
    green_at(stub, tree.rev())

    checking.check(ROUND, "B1", actor="coder")

    assert events(path, "check.ran") == []
    assert check_step(path).state == steps.DONE


@pytest.mark.parametrize("fields", [{"conclusion": "failure"},
                                    {"status": "queued", "conclusion": ""},
                                    {"headSha": "b" * 40}])
def test_red_pending_and_absent_do_not_meet_the_gate(setup, stub, fields):
    path, tree, _made = setup()
    green_at(stub, tree.rev(), **fields)

    checking.check(ROUND, "B1", actor="coder")

    found = check_step(path)
    assert found.state == steps.READY
    assert found.owes[0].startswith("by the full check: check has no run on record")
    assert found.owes[1].startswith("by CI: CI reads ")


def test_a_reading_at_an_older_tip_does_not_meet_the_gate(setup, stub):
    path, tree, _made = setup()
    green_at(stub, tree.rev())
    checking.check(ROUND, "B1", actor="coder")

    tree.commit("more.txt")

    assert check_step(path).owes[1] == (f"by CI: CI has no reading on record for B1 at "
                                        f"{tree.rev()[:12]}")


def test_a_workflow_list_or_a_local_command_changed_since_does_not_meet_the_gate(
        setup, stub):
    path, tree, _made = setup()
    green_at(stub, tree.rev())
    checking.check(ROUND, "B1", actor="coder")

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

    done = checking.check(ROUND, "B1", actor="coder")

    assert done.pushed == "origin"
    assert repo.git("--git-dir", str(bare), "rev-parse", "refs/heads/work") == tree.rev()
    assert (tmp_path / "hook-ran").read_text() == "pushed\n"


def test_a_refused_push_records_nothing(setup, stub, repo, tmp_path):
    path, tree, _made = setup(remote="nowhere")
    green_at(stub, tree.rev())
    before = path.read_bytes()

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", actor="coder")

    assert "was refused, so nothing is recorded" in str(caught.value)
    assert path.read_bytes() == before


def test_a_repo_with_no_ci_is_checked_by_the_full_check(repo, write_config, tmp_path):
    full = shlex.join([PY, "-c", "print('full ran')"])
    write_config(f"[projects.proj-a]\ncheck = {{ command = {json.dumps(full)} }}\n")
    opening.open_round(ROUND, description="a placeholder round", actor="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               actor="planner")
    made = commissioning.commission(ROUND, "B1", builder="coder", actor="planner")
    type(repo)(made.worktree).commit("work.txt")

    done = checking.check(ROUND, "B1", actor="coder")

    assert isinstance(done, checking.Ran) and done.step == "check"
    assert events(path, ci.READ) == []


# -- where a reading is shown ------------------------------------------------------------

def test_next_and_show_give_the_newest_reading_and_what_the_gate_owes(setup, stub,
                                                                      capsys):
    _path, tree, _made = setup()
    green_at(stub, tree.rev(), conclusion="failure")
    checking.check(ROUND, "B1", actor="coder")

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
    folder = tmp_path / "bin"
    folder.mkdir()
    gh = folder / "gh"
    gh.write_text(f"#!/bin/sh\nexit {auth_exit}\n")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")


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
