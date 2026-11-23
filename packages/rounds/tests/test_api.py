"""The read API: each call gives the data its command prints, and writes nothing; and
the package keeps to its own side of the line between it and the modules around it."""

from __future__ import annotations

import ast
import json
import tomllib
from importlib import metadata
from pathlib import Path

import pytest
from moxi import modules

from moxi_rounds import (api, asking, dropping, errors, log, opening, paths, preparing,
                         ruling)
from moxi_rounds.cli import main

A = "proj-a/a-round"
B = "proj-a/b-round"
C = "proj-b/c-round"
D = "proj-b/d-round"
SAID = "Do agent-read times follow the settings?"
OTHER = "Is the status line the place for usage?"
THIRD = "Does a parsed row keep its own format?"
RULED = "Usage is not calculated in the status line; it may be shown there."

T1, T2, T3, T4, T5, T6 = (f"2026-01-02T03:04:0{second}Z" for second in range(1, 7))
NOW = "2026-01-05T03:04:01Z"

PACKAGE = Path(__file__).resolve().parents[1]

#: Every call the API has, by what it is asked for.
CALLS = {
    "the open rounds": lambda: api.rounds(),
    "the open rounds of one project": lambda: api.rounds("proj-a"),
    "the ended rounds": lambda: api.rounds(status="closed"),
    "one round": lambda: api.round_data(A),
    "a round that has ended": lambda: api.round_data(D),
    "what waits": lambda: api.waiting(),
    "a round's questions": lambda: api.questions(A),
    "a round's docket": lambda: api.questions(A, view="docket"),
    "a project's questions": lambda: api.questions("proj-a"),
}

#: The command that prints what each call gives.
COMMANDS = {
    "the open rounds": ["list"],
    "the open rounds of one project": ["list", "proj-a"],
    "the ended rounds": ["list", "--closed"],
    "one round": ["show", A],
    "a round that has ended": ["show", D],
    "what waits": ["list", "--waiting"],
    "a round's questions": ["list", A, "--questions"],
    "a round's docket": ["list", A, "--questions", "--view", "docket"],
    "a project's questions": ["list", "proj-a", "--questions"],
}


@pytest.fixture
def rounds(tmp_path, add_project, capsys, monkeypatch):
    """Two projects with two rounds each, holding one of everything a read gives: open
    and parked questions with and without options, a question on a project's list,
    rulings shown to nobody, and a round that was dropped. Returns the rounds folder
    of `proj-a`. Every reading the test computes is computed at `NOW`."""
    add_project("proj-a", notes_root=tmp_path / "notes-a")
    add_project("proj-b", notes_root=tmp_path / "notes-b")
    for name in (A, B, C, D):
        opening.open_round(name, description="a placeholder round", by="planner",
                           flow="fix", at=T1)
    rounds_dir = tmp_path / "notes-a" / "rounds"
    for stage in ("B1", "B2"):
        log.append(log.round_log(rounds_dir / "a-round"), "stage.declared",
                   {"id": stage, "repo": "proj-a", "branch": f"branch-{stage.lower()}"},
                   by="planner")
    ask(options=["a=prose yes, parsed rows no", "b=both follow the settings"])
    ask(text=OTHER, at=T2)
    ask(text=THIRD, at=T3)
    ask("proj-a", text=THIRD, at=T3)
    ask(C, at=T2)
    rule(item="park:Q2", until="B2", at=T4)
    rule(item="Q3", text="It does.", at=T4)
    rule(text=RULED, at=T5)
    rule("proj-a", text=RULED, at=T5)
    dropping.drop(D, by="operator", record="comms:30", run_by="planner", at=T6)
    capsys.readouterr()
    real = log.stamp
    monkeypatch.setattr(log, "stamp", lambda when_s=None: NOW if when_s is None
                        else real(when_s))
    return rounds_dir


def ask(target=A, text=SAID, *, at=T1, **more):
    return asking.ask(target, text, by="scout", origin="comms:11", at=at, **more)


def rule(target=A, *, at=T5, **more):
    return ruling.rule(target, record="comms:12", by="planner", at=at, **more)


def printed(capsys, argv):
    """What the command prints as data, having exited 0."""
    assert main([*argv, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def everything(tmp_path):
    """Every file the test's rounds, projects and settings are kept in, with its bytes
    and the time it was last written."""
    return {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
            for root_dir in {tmp_path, Path.home(), paths.data_dir(), paths.state_dir()}
            for path in sorted(root_dir.rglob("*")) if path.is_file()}


def elsewhere(rounds):
    """A line another machine's verb appended, which the index here has not seen."""
    log.append(log.round_log(rounds / "a-round"), "question.asked",
               {"id": "Q4", "text": OTHER, "from": "comms:13", "blocks": "all",
                "state": "open", "asked_by": "helper", "asked_at": T2}, by="helper")


def spoil(rounds, slug="a-round"):
    """Make a round's log one that can't be read."""
    with log.round_log(rounds / slug).open("ab") as fh:
        fh.write(b"not an event\n")


# -- each call is its command's data -----------------------------------------------------

def test_the_api_has_its_five_calls_and_no_other():
    assert api.__all__ == ["rounds", "round_data", "waiting", "questions", "builds"]
    assert modules.load("rounds", interface=1).api is api


@pytest.mark.parametrize("call", sorted(CALLS))
def test_a_call_gives_what_its_command_prints_as_data(rounds, capsys, call):
    found = CALLS[call]()

    assert found == printed(capsys, COMMANDS[call])
    # Plain data all the way down: what a caller gets survives being written out.
    assert json.loads(json.dumps(found)) == found


def test_the_calls_hold_what_the_rounds_hold(rounds):
    listing = api.rounds()
    assert [one["name"] for one in listing["rounds"]] == [A, B, C]
    assert listing["rounds"][0] == {
        "name": A, "project": "proj-a", "folder": str(rounds / "a-round"),
        "status": "open", "flow": "fix", "lead": None,
        "step": {"step": "commission", "stage": "B1", "state": "waiting"},
        "counts": {"open": 1, "parked": 1, "not_shown": 3}, "standing": None,
        "ended": None}
    [ended] = api.rounds(status="closed")["rounds"]
    assert (ended["name"], ended["status"]) == (D, "dropped")
    assert [one["name"] for one in api.rounds("proj-b")["rounds"]] == [C]

    one = api.round_data(A)
    assert (one["name"], one["status"], one["flow"]) == (A, "open", "fix")
    assert [question["id"] for question in one["questions"]] == ["Q1", "Q2", "Q3"]
    assert [made["id"] for made in one["rulings"]] == ["C1", "C2", "C3"]

    waits = api.waiting()
    assert [(entry["holder"], entry["id"]) for entry in waits["open"]] == [
        ("proj-a", "P1"), (A, "Q1"), (C, "Q1")]
    assert [(entry["holder"], entry["id"]) for entry in waits["parked"]] == [(A, "Q2")]
    assert [back["project"] for back in waits["not_shown"]] == ["proj-a"]

    assert [entry["id"] for entry in api.questions(A)["entries"]] == ["Q1", "Q2"]
    assert api.questions("proj-a")["entries"] == [*waits["open"][:2], *waits["parked"]]


def test_the_rounds_and_one_round_carry_its_lead(rounds):
    from moxi import cli

    from moxi_rounds import leads

    leads.mark(A, "atlas-lead", by="planner")
    assert cli.main(["projects", "set", "proj-a", "--lead", "kestrel"]) == 0

    assert {one["name"]: one["lead"] for one in api.rounds()["rounds"]} == {
        A: {"name": "atlas-lead", "from": "round"},
        B: {"name": "kestrel", "from": "project"}, C: None}
    assert api.round_data(A)["lead"] == {"name": "atlas-lead", "from": "round"}
    assert api.round_data(B)["lead"] == {"name": "kestrel", "from": "project"}
    assert api.round_data(D)["lead"] is None


def test_the_calls_say_what_step_a_round_is_at(rounds):
    assert [one["step"] for one in api.rounds()["rounds"]] == [
        {"step": "commission", "stage": "B1", "state": "waiting"},
        {"step": "commission", "stage": None, "state": "ready"},
        {"step": "commission", "stage": None, "state": "waiting"}]
    assert api.rounds(status="closed")["rounds"][0]["step"] is None
    at = api.round_data(A)["step"]
    assert (at["step"], at["stage"], at["state"]) == ("commission", "B1", "waiting")


def test_a_line_the_index_has_not_seen_is_in_what_every_call_gives(rounds, capsys):
    elsewhere(rounds)

    assert api.rounds()["rounds"][0]["counts"]["open"] == 2
    assert [one["id"] for one in api.round_data(A)["questions"]] == ["Q1", "Q2", "Q3", "Q4"]
    assert (A, "Q4") in [(one["holder"], one["id"]) for one in api.waiting()["open"]]
    assert [one["id"] for one in api.questions(A)["entries"]] == ["Q1", "Q4", "Q2"]
    for call in sorted(CALLS):
        assert CALLS[call]() == printed(capsys, COMMANDS[call]), call


def test_with_no_index_every_call_gives_the_same(rounds):
    with_index = {call: CALLS[call]() for call in CALLS}
    paths.index_file().unlink()

    assert {call: CALLS[call]() for call in CALLS} == with_index


# -- a read writes nothing ---------------------------------------------------------------

@pytest.mark.parametrize("index", ["fresh", "stale", "none"])
def test_no_call_changes_a_byte_or_a_time_of_any_file(rounds, tmp_path, capsys, index):
    if index == "fresh":
        printed(capsys, ["list"])   # brings the index up to every log
    elif index == "stale":
        elsewhere(rounds)
    else:
        paths.index_file().unlink()
    before = everything(tmp_path)
    assert any(path.endswith(".jsonl") for path in before)
    assert (str(paths.index_file()) in before) == (index != "none")

    for call in sorted(CALLS):
        CALLS[call]()
        assert everything(tmp_path) == before, call


def test_a_call_that_is_refused_changes_nothing_either(rounds, tmp_path):
    elsewhere(rounds)
    spoil(rounds, "b-round")
    before = everything(tmp_path)

    for refused in (lambda: api.round_data("proj-a"), lambda: api.round_data("proj-a/no-round"),
                    lambda: api.rounds("no-project"), lambda: api.questions(B),
                    lambda: api.rounds(status="dormant")):
        with pytest.raises(errors.RoundsError):
            refused()
    api.round_data(B)
    api.rounds()
    api.waiting()
    assert everything(tmp_path) == before


# -- a round that can't be read ----------------------------------------------------------

def test_a_round_that_cant_be_read_is_returned_as_unreadable(rounds, capsys):
    spoil(rounds)
    file = str(log.round_log(rounds / "a-round"))

    one = api.round_data(A)
    assert set(one) == {"name", "project", "slug", "folder", "notice", "unreadable"}
    assert one["unreadable"]["file"] == file and one["unreadable"]["line"] == 10
    assert one == printed(capsys, ["show", A])

    for status in ("open", "closed"):
        row = api.rounds(status=status)["rounds"][0]
        assert set(row) == {"name", "project", "folder", "unreadable"}
        assert row["name"] == A and file in row["unreadable"]["reason"]

    waits = api.waiting()
    assert [entry["holder"] for entry in waits["open"]] == ["proj-a", C]
    assert len(waits["unreadable"]) == 1 and file in waits["unreadable"][0]

    of_project = api.questions("proj-a")
    assert [entry["holder"] for entry in of_project["entries"]] == ["proj-a"]
    assert len(of_project["unreadable"]) == 1 and file in of_project["unreadable"][0]


@pytest.mark.parametrize("content", [b"", b'{"v":1,"n":1,"at":"2026-01-02T03:04:05Z",'
                                          b'"actor":"pilot","type":"repair"}\n'])
def test_a_log_that_opened_no_round_is_returned_as_unreadable(rounds, content):
    (rounds / "e-round").mkdir()
    file = log.round_log(rounds / "e-round")
    file.write_bytes(content)
    name = "proj-a/e-round"

    one = api.round_data(name)
    assert set(one) == {"name", "project", "slug", "folder", "notice", "unreadable"}
    assert (one["unreadable"]["file"], one["unreadable"]["line"]) == (str(file), 1)
    for status in ("open", "closed"):
        row = next(row for row in api.rounds(status=status)["rounds"]
                   if row["name"] == name)
        assert set(row) == {"name", "project", "folder", "unreadable"}
    assert any(str(file) in line for line in api.waiting()["unreadable"])
    assert any(str(file) in line for line in api.questions("proj-a")["unreadable"])
    with pytest.raises(errors.Unreadable):
        api.questions(name)


def test_the_questions_of_a_round_that_cant_be_read_are_refused_as_its_command_is(
        rounds, capsys):
    spoil(rounds)

    with pytest.raises(errors.Unreadable) as refused:
        api.questions(A)
    assert refused.value.line == 10 and refused.value.exit_code == 3
    assert main(["list", A, "--questions", "--json"]) == 3
    assert capsys.readouterr().out == ""


# -- what a call refuses -----------------------------------------------------------------

def test_a_call_raises_what_its_command_refuses(rounds):
    with pytest.raises(errors.Refused):
        api.round_data("proj-a")
    with pytest.raises(errors.NoRound):
        api.round_data("proj-a/no-round")
    with pytest.raises(errors.UnknownProject):
        api.rounds("no-project")
    with pytest.raises(errors.Refused):
        api.rounds(A)
    with pytest.raises(errors.Refused):
        api.questions("proj-a", view="docket")
    with pytest.raises(errors.BadArgument):
        api.questions(A, view="no-view")


@pytest.mark.parametrize("status", ["dropped", "dormant", "", None])
def test_a_listing_of_rounds_is_of_the_open_ones_or_of_the_ended_ones(rounds, status):
    with pytest.raises(errors.BadArgument) as refused:
        api.rounds(status=status)
    assert refused.value.exit_code == 2
    assert 'status="open"' in refused.value.fix and 'status="closed"' in refused.value.fix


# -- the line between rounds and the modules around it -----------------------------------

#: The modules rounds stands beside and takes nothing from.
APART = ("moxi_repo", "moxi_turns", "moxi_comms")


def imported(source: str) -> set[str]:
    """The top-level name of every module a source imports, wherever in it."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def test_no_source_of_the_package_imports_repo_turns_or_comms():
    sources = sorted((PACKAGE / "src").rglob("*.py"))
    assert len(sources) > 20

    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert not imported(text) & set(APART), path.name
        # Nor by name, for a module loaded through a string.
        for name in APART:
            assert name not in text, f"{path.name} names {name}"


def _name(requirement: str) -> str:
    """The distribution a requirement names, without its version or marker."""
    return requirement.split(";")[0].split("<")[0].split(">")[0].split("=")[0].strip()


def test_the_package_requires_none_of_repo_turns_or_comms_and_each_only_as_its_extra():
    declared = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))
    project = declared["project"]
    extras = project.get("optional-dependencies", {})

    required = {_name(one) for one in project["dependencies"]}
    assert not required & {"moxi-repo", "moxi-turns", "moxi-comms"}
    for extra, named in extras.items():
        names = {_name(one) for one in named}
        assert "moxi-repo" not in names
        assert ("moxi-turns" in names) == (extra == "turns")
        assert ("moxi-comms" in names) == (extra == "comms")
    assert "turns" in extras and "comms" in extras

    installed = metadata.distribution("moxi-rounds").requires or []
    for requirement in installed:
        name = _name(requirement)
        assert name != "moxi-repo"
        for module in ("turns", "comms"):
            if name == f"moxi-{module}":
                assert f"extra == '{module}'" in requirement.replace('"', "'")


def test_the_package_registers_its_module_its_listener_its_refusal_and_no_git_hook():
    declared = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))
    expected = {"moxi.modules", "moxi.turns.listeners", "moxi.projects"}
    assert set(declared["project"]["entry-points"]) == expected

    groups = {point.group for point in metadata.distribution("moxi-rounds").entry_points}
    assert groups == expected
    assert "moxi.repo.git_hooks" not in groups


def test_a_questions_entry_carries_its_prepared_answer(tmp_path, add_project, capsys):
    add_project("proj-a", notes_root=tmp_path / "notes")
    opening.open_round(A, description="a placeholder round", by="planner", flow="fix")
    asking.ask(A, "Is it so?", origin="comms:11", by="scout",
               options=["a=yes", "b=no"])
    path = tmp_path / "answer.md"
    path.write_text("```toml rounds\n" + "\n".join([
        'kind = "answer"', 'question = "Q1"', 'by = "builder-1"', 'model = "a-model"',
        'recommend = "b"', 'sure = "low"', 'why = "a guess"', 'flips = "a reading"',
        'read = ["comms:1234567"]',
        '[[option]]', 'key = "a"', 'costs = "time"', 'forecloses = "nothing"',
        '[[option]]', 'key = "b"', 'costs = "more"', 'forecloses = "nothing"',
    ]) + "\n```\n", encoding="utf-8")
    preparing.prepare(A, "Q1", path, by="helper")

    [entry] = api.questions(A)["entries"]
    assert entry["prepared"]["recommend"] == "b" and entry["prepared"]["current"]
    [question] = api.round_data(A)["questions"]
    assert question["prepared"] == entry["prepared"]
