"""Paths under the home folder in a round's records: written `~/<rest>`, read back to the
place they name, and a record that holds either form is checked, shown and swept alike."""

from __future__ import annotations

import json
import os
import shlex
import sys
import textwrap
import time
from pathlib import Path

import pytest

from moxi_rounds import (builds, checking, commissioning, dropping, log, opening, places,
                         sweeping)
from moxi_rounds import settings as own
from moxi_rounds import state as states
from moxi_rounds.cli import main

ROUND = "proj-a/a-round"
PY = sys.executable


def set_home(monkeypatch, home: Path) -> Path:
    """Make `home` the home folder `~` names, on any system."""
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


# -- the two helpers ---------------------------------------------------------------------

@pytest.fixture
def home(monkeypatch, tmp_path) -> Path:
    return set_home(monkeypatch, tmp_path / "ann")


def test_home_itself_is_written_as_a_tilde(home):
    assert places.contract_home(home) == "~"
    assert places.contract_home(str(home)) == "~"


def test_a_path_under_home_is_written_from_a_tilde(home):
    assert places.contract_home(home / "a" / "b") == "~" + os.sep + os.path.join("a", "b")


def test_a_path_outside_home_is_written_as_it_is(home, tmp_path):
    outside = tmp_path / "elsewhere" / "x"
    assert places.contract_home(outside) == str(outside)


def test_a_path_that_only_starts_with_homes_name_is_not_under_it(home):
    sibling = str(home) + "2" + os.sep + "x"
    assert places.contract_home(sibling) == sibling
    assert places.contract_home(str(home) + "2") == str(home) + "2"


def test_a_tilde_path_and_a_relative_path_are_written_as_they_are(home):
    assert places.contract_home("~/a") == "~/a"
    assert places.contract_home(os.path.join("a", "b")) == os.path.join("a", "b")
    assert places.contract_home("") == ""


def test_a_home_given_with_a_trailing_separator_still_matches(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "ann") + os.sep)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "ann") + os.sep)
    assert places.contract_home(tmp_path / "ann" / "a") == "~" + os.sep + "a"


def test_a_tilde_path_reads_as_the_place_under_home(home):
    assert places.expand_home("~") == str(home)
    assert Path(places.expand_home("~/a/b")) == home / "a" / "b"


def test_a_whole_path_and_a_relative_path_read_as_they_are(home, tmp_path):
    assert places.expand_home(str(tmp_path / "x")) == str(tmp_path / "x")
    assert places.expand_home("a/b") == "a/b"
    assert places.expand_home("a/~/b") == "a/~/b"


def test_what_is_written_reads_back_to_the_same_path(home, tmp_path):
    for path in (home, home / "a", home / "a" / "b c", tmp_path / "x"):
        assert Path(places.expand_home(places.contract_home(path))) == path


# -- a round whose records hold the two forms --------------------------------------------

@pytest.fixture
def repo(git_repo, tmp_path):
    """The project `proj-a`, whose root is the test's git repo, keeping its rounds under
    a notes folder of its own."""
    from moxi import cli

    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    exclude = git_repo.root / ".git" / "info" / "exclude"
    exclude.write_text("__pycache__\n", encoding="utf-8")
    return git_repo


@pytest.fixture
def rounds_home() -> Path:
    """The folder rounds keeps worktrees and scratch folders under in the test, made."""
    found = Path(os.environ["MOXI_ROUNDS_HOME"])
    if not found.is_dir():
        found.mkdir(parents=True)
    return found


@pytest.fixture
def script(rounds_home, write_config) -> Path:
    """`proj-a`'s check: a passing script in rounds' own folder, named by its whole path."""
    found = rounds_home / "check.py"
    found.write_text("print('the check passed')\n", encoding="utf-8")
    command = shlex.join([PY, str(found)])
    write_config(f"[projects.proj-a]\ncheck = {{ command = {json.dumps(command)} }}\n")
    return found


def commission(repo, tmp_path) -> tuple[Path, object]:
    """B1 on `work` commissioned in the `fix` flow with one commit of work."""
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               by="planner")
    made = commissioning.commission(ROUND, "B1", builder="coder", by="planner")
    type(repo)(made.worktree).commit("work.txt", "the work")
    return path, made


def folded(path: Path) -> states.State:
    return states.fold(log.read_round(path))


def commissioned(path: Path) -> dict:
    state = folded(path)
    return checking.commission_of(state, builds.current(state, "B1"))


@pytest.fixture(params=["tilde", "whole"])
def built(request, repo, tmp_path, monkeypatch, rounds_home, script):
    """B1 commissioned with its folders recorded from `~` (rounds' folder is under the
    home folder) or as whole paths (it is not). Returns `(log, the commission, form)`."""
    if request.param == "tilde":
        set_home(monkeypatch, rounds_home)
    path, made = commission(repo, tmp_path)
    return path, made, request.param


def test_a_commission_writes_its_places_from_a_tilde_under_home(built):
    path, made, form = built
    event = commissioned(path)
    for kind, place in (("worktree", made.worktree), ("scratch", made.scratch)):
        assert event[kind].startswith("~" + os.sep) == (form == "tilde")
        assert Path(places.expand_home(event[kind])) == place
        assert place.is_dir()


def test_next_and_show_read_the_worktree_clean_from_either_form(built, capsys):
    path, _made, _form = built
    seen = checking.seen_of(folded(path), ROUND, settings=own.load())["B1"]
    assert seen.clean and seen.problem is None

    capsys.readouterr()
    assert main(["next", ROUND]) == 0
    assert main(["show", ROUND]) == 0
    out = capsys.readouterr().out
    assert "not clean" not in out and "not there" not in out
    assert f"worktree: {places.expand_home(commissioned(path)['worktree'])}" in out


def root() -> Path:
    from moxi_rounds import folders

    return folders.root("proj-a")[1]


def counts(path: Path, argv) -> str | None:
    """Why the newest check of B1 does not pass at its recorded commit with `argv`."""
    state = folded(path)
    run = checking.newest(state, "B1", checking.CHECK)
    return checking.why_not(state, "B1", checking.CHECK, tip=run["commit"], argv=argv)


def argv_now(path: Path, tip: str) -> list[str]:
    state = folded(path)
    return checking.argv_now(own.load(), ROUND, builds.current(state, "B1"), root=root(),
                             tip=tip)


def test_a_check_writes_its_argv_and_output_from_a_tilde_and_counts(built, script):
    path, made, form = built

    done = checking.check(ROUND, "B1", by="coder")

    assert done.passed
    [event] = [one for one in log.read(path) if one["type"] == checking.RAN]
    tilde = "~" + os.sep + "check.py"
    assert event["argv"] == [PY, tilde if form == "tilde" else str(script)]
    assert event["output"].startswith("~" + os.sep) == (form == "tilde")
    assert done.argv == (PY, str(script))
    assert done.output == made.scratch / "check-1.log" and done.output.is_file()
    now = argv_now(path, done.commit)
    assert now == [PY, str(script)]
    assert counts(path, now) is None


def test_a_run_of_another_command_still_does_not_count(built, script):
    path, _made, _form = built
    done = checking.check(ROUND, "B1", by="coder")

    why = counts(path, [PY, str(script), "--more"])

    assert why is not None and "the command is now" in why
    assert counts(path, [PY, str(script.with_name("other.py"))]) is not None
    assert done.passed


def tilde_record(path: Path, home: Path) -> None:
    """Rewrite the log as a notes repo that writes home paths as `~` does: every text
    that is the home folder or a path under it, at any depth."""
    prefix = str(home)

    def rewrite(value):
        if isinstance(value, str) and (value == prefix
                                       or value.startswith(prefix + os.sep)):
            return "~" + value[len(prefix):]
        if isinstance(value, list):
            return [rewrite(one) for one in value]
        if isinstance(value, dict):
            return {key: rewrite(one) for key, one in value.items()}
        return value

    lines = [json.dumps(rewrite(json.loads(line)), ensure_ascii=False)
             for line in path.read_text(encoding="utf-8").splitlines()]
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


@pytest.fixture
def rewritten(repo, tmp_path, monkeypatch, rounds_home, script):
    """B1 commissioned and checked with whole paths recorded, then the log rewritten
    with `~` for rounds' folder, now under the home folder. Returns `(log, the
    commission)`."""
    path, made = commission(repo, tmp_path)
    assert checking.check(ROUND, "B1", by="coder").passed
    set_home(monkeypatch, rounds_home)
    tilde_record(path, rounds_home)
    event = commissioned(path)
    assert event["worktree"].startswith("~") and event["scratch"].startswith("~")
    run = checking.newest(folded(path), "B1", checking.CHECK)
    assert run["argv"][-1].startswith("~") and run["output"].startswith("~")
    return path, made


def test_a_rewritten_record_reads_its_worktree_clean_and_its_run_counts(rewritten,
                                                                         capsys):
    path, made = rewritten
    seen = checking.seen_of(folded(path), ROUND, settings=own.load())["B1"]
    assert seen.clean and seen.problem is None
    run = checking.newest(folded(path), "B1", checking.CHECK)
    assert counts(path, argv_now(path, run["commit"])) is None

    capsys.readouterr()
    assert main(["next", ROUND]) == 0
    assert main(["show", ROUND]) == 0
    out = capsys.readouterr().out
    assert "not clean" not in out and "not there" not in out
    assert f"worktree: {made.worktree}" in out


def test_a_rewritten_record_is_checked_in_its_worktree(rewritten):
    path, made = rewritten

    done = checking.check(ROUND, "B1", by="coder")

    assert done.passed and done.output == made.scratch / "check-2.log"


def test_the_sweep_removes_the_places_a_rewritten_record_names(rewritten, write_config,
                                                                monkeypatch, tmp_path):
    path, made = rewritten
    log.append(path, "build.dropped", {"stage": "B1", "by": "lead",
                                       "record": "comms:1234567"}, by="planner")
    monkeypatch.setattr(sweeping, "_now", lambda: time.time() + 3 * 3600)
    write_config("[sweep]\narmed = true\n")
    # No folder named `~` is ever looked at in the folder the sweep runs from.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "~").mkdir()

    swept = sweeping.run(remove=True)

    assert {one.candidate.kind: one.fate for one in swept.leftovers} == {
        "worktree": "removed", "scratch": "removed"}
    assert not made.worktree.exists() and not made.scratch.exists()
    assert {Path(one.place) for one in swept.leftovers} == {made.worktree, made.scratch}
    assert (tmp_path / "~").is_dir()


def test_a_drop_removes_the_places_a_rewritten_record_names(rewritten):
    _path, made = rewritten

    dropping.drop_build(ROUND, "B1", by="operator", record="comms:1234567",
                        run_by="planner")

    assert not made.worktree.exists() and not made.scratch.exists()


def test_a_tilde_word_of_a_command_step_runs_as_the_path_under_home(
        repo, tmp_path, monkeypatch, write_config, rounds_home):
    set_home(monkeypatch, rounds_home)
    script = rounds_home / "staging.py"
    script.write_text("print('staged')\n", encoding="utf-8")
    write_config(textwrap.dedent(f"""
        [flows.mine]
        steps = [
          {{ step = "build", by = "builder" }},
          {{ step = "staging", does = "command", before = "finish", by = "builder", argv = {json.dumps([PY, "~/staging.py"])} }},
          {{ step = "finish", by = "builder" }},
        ]
    """))
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="mine")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               by="planner")
    made = commissioning.commission(ROUND, "B1", builder="coder", by="planner")
    type(repo)(made.worktree).commit("work.txt", "the work")

    done = checking.check(ROUND, "B1", by="coder", step="staging")

    assert done.passed and done.argv == (PY, str(script))
    assert done.output.read_text().strip() == "staged"
    run = checking.newest(folded(path), "B1", "staging")
    assert run["argv"] == [PY, "~" + os.sep + "staging.py"]


# -- a report's file -----------------------------------------------------------------------

def test_a_report_outside_the_round_is_recorded_from_a_tilde(home, tmp_path):
    from moxi_rounds import verdicts

    round_dir = tmp_path / "notes" / "rounds" / "a-round"
    assert verdicts.path(round_dir / "report.md", round_dir) == "report.md"
    assert verdicts.path(home / "r" / "report.md", round_dir) == (
        "~" + os.sep + os.path.join("r", "report.md"))
    assert verdicts.path(tmp_path / "report.md", round_dir) == str(tmp_path / "report.md")
