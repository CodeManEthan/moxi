"""`moxi rounds list ROUND|PROJECT --questions`: the questions that wait on the operator,
written out as markdown, as data and as two files, with the rulings not yet shown to
him after them."""

from __future__ import annotations

import json

import pytest
from moxi import cli

from moxi_rounds import (asking, export, folders, index, log, moving, opening, options,
                         paths, questions, ruling, state)
from moxi_rounds.cli import main
from moxi_rounds.errors import BadArgument, NoRound, Refused, Unreadable

A = "proj-a/a-round"
B = "proj-a/b-round"
SAID = "Do agent-read times follow the settings?"
OTHER = "Is the status line the place for usage?"
THIRD = "Does a parsed row keep its own format?"
RULED = "Usage is not calculated in the status line; it may be shown there."

#: Times a second apart, and one long after them all, in which an export is computed.
T1, T2, T3, T4, T5, T6 = (f"2026-01-02T03:04:0{second}Z" for second in range(1, 7))
NOW = "2026-01-03T00:00:00Z"

#: The fields every entry has, as data.
BARE = {"holder", "id", "text", "from", "blocks", "state", "by", "at"}


@pytest.fixture
def rounds(tmp_path, add_project, capsys, monkeypatch):
    """The rounds folder of the project `proj-a`, with the rounds `a-round` and
    `b-round` opened in it; `a-round` has the stages B1 and B2. Every export the test
    computes is computed at `NOW`."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    for slug in ("a-round", "b-round"):
        opening.open_round(f"proj-a/{slug}", description="a placeholder round",
                           actor="planner", flow="fix")
    folder = tmp_path / "notes" / "rounds"
    for stage in ("B1", "B2"):
        log.append(log.round_log(folder / "a-round"), "stage.declared",
                   {"id": stage, "repo": "proj-a", "branch": f"branch-{stage.lower()}"},
                   actor="planner")
    capsys.readouterr()
    real = log.stamp
    monkeypatch.setattr(log, "stamp", lambda when_s=None: NOW if when_s is None
                        else real(when_s))
    return folder


def ask(target=A, text=SAID, *, at=T1, actor="scout", origin="comms:11", **more):
    return asking.ask(target, text, actor=actor, origin=origin, at=at, **more)


def rule(target=A, *, at=T5, record="comms:12", **more):
    return ruling.rule(target, record=record, actor="planner", at=at, **more)


def park(target, question_id, *, until=None, at=T5):
    return rule(target, item=f"park:{question_id}", until=until, at=at)


def finish(rounds, stage, slug="a-round"):
    """Record a stage as finished, as a later verb will."""
    log.append(log.round_log(rounds / slug), "finish.done",
               {"stage": stage, "tip": "1" * 40}, actor="pilot")


def entries(name=A):
    return [entry.data() for entry in export.questions_of(name).entries]


def named(found):
    return [(entry["holder"], entry["id"], entry["state"]) for entry in found]


def printed(capsys, *argv):
    """What `moxi rounds list` prints for the arguments, having exited 0."""
    assert main(["list", *argv]) == 0
    return capsys.readouterr().out


def everything(*folders):
    """Every file under the folders, with its bytes and the time it was last written."""
    return {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
            for folder in folders for path in sorted(folder.rglob("*"))
            if path.is_file()}


def store(rounds):
    """The logs, the index and the lock files: everything rounds keeps."""
    return everything(rounds, paths.data_dir(), paths.state_dir())


# -- one computation, three forms --------------------------------------------------------

def test_the_markdown_the_data_and_the_two_files_hold_the_same_entries(
        rounds, tmp_path, capsys):
    ask(options=["a=prose yes, parsed rows no", "b=both follow the settings"])
    ask(text=OTHER, at=T2, blocks="B2")
    ask(text=THIRD, at=T3)
    park(A, "Q3", until="B2")
    rule(text=RULED, at=T4)
    out = tmp_path / "out"

    markdown = printed(capsys, A, "--questions")
    data = printed(capsys, A, "--questions", "--json")
    paths_printed = printed(capsys, A, "--questions", "--out", str(out))

    assert paths_printed.splitlines() == [str(out / "questions.md"),
                                          str(out / "questions.json")]
    assert (out / "questions.md").read_text(encoding="utf-8") == markdown
    assert (out / "questions.json").read_text(encoding="utf-8") == data
    assert sorted(path.name for path in out.iterdir()) == ["questions.json",
                                                           "questions.md"]
    found = json.loads(data)
    assert found["entries"] == entries()
    assert named(found["entries"]) == [(A, "Q1", "open"), (A, "Q2", "open"),
                                       (A, "Q3", "parked")]
    # Every entry of the data is an entry of the markdown, with all it holds.
    for entry in found["entries"]:
        section = markdown.split(f"### {A} {entry['id']}: ")[1].split("\n### ")[0]
        section = section.split("\n## ")[0]
        assert section.splitlines()[0] == entry["text"]
        held = entry["blocks"]
        for said in (f"- from: {entry['from']}", f"- state: {entry['state']}",
                     f"- blocks: {held if isinstance(held, str) else ','.join(held)}",
                     f"- by: {entry['by']}", f"- at: {entry['at']}"):
            assert said in section
        for option in entry.get("options", []):
            assert f"  - {option['key']}: {option['text']}" in section
    assert markdown.count("\n### ") == len(found["entries"])


def test_an_export_is_of_what_the_records_say_when_it_is_asked_for(rounds):
    ask()
    first = export.questions_of(A)
    ask(text=OTHER, at=T2)
    rule(item="Q1", text="Prose follows the settings.", at=T3)

    assert [entry.id for entry in first.entries] == ["Q1"]
    assert [entry.id for entry in export.questions_of(A).entries] == ["Q2"]


def test_the_export_says_what_it_is_of_and_when_it_was_computed(rounds):
    ask()
    ask("proj-a", at=T2)

    of_round, of_project = export.questions_of(A).data(), export.questions_of(
        "proj-a").data()

    assert {key: of_round[key] for key in ("of", "project", "round", "view",
                                           "computed")} == {
        "of": A, "project": "proj-a", "round": A, "view": "docket", "computed": NOW}
    assert {key: of_project[key] for key in ("of", "project", "round", "view",
                                             "computed")} == {
        "of": "proj-a", "project": "proj-a", "round": None, "view": None,
        "computed": NOW}
    assert list(of_round) == ["of", "project", "round", "view", "computed", "entries",
                              "unreadable", "read_back"]


def test_out_with_json_writes_the_two_files_and_prints_their_paths(
        rounds, tmp_path, capsys):
    ask()
    out = tmp_path / "deep" / "out"

    said = printed(capsys, A, "--questions", "--json", "--out", str(out))

    assert said.splitlines() == [str(out / "questions.md"), str(out / "questions.json")]
    assert json.loads((out / "questions.json").read_text(encoding="utf-8"))[
        "entries"] == entries()


def test_a_second_export_into_a_folder_replaces_the_first(rounds, tmp_path, capsys):
    ask()
    out = tmp_path / "out"
    printed(capsys, A, "--questions", "--out", str(out))
    ask(text=OTHER, at=T2)

    printed(capsys, A, "--questions", "--out", str(out))

    assert OTHER in (out / "questions.md").read_text(encoding="utf-8")
    assert sorted(path.name for path in out.iterdir()) == ["questions.json",
                                                           "questions.md"]


def test_a_folder_that_cant_be_written_is_an_error(rounds, tmp_path, capsys):
    ask()
    (tmp_path / "taken").write_text("a file, not a folder", encoding="utf-8")

    assert main(["list", A, "--questions", "--out", str(tmp_path / "taken")]) == 1

    assert "can't be written" in capsys.readouterr().err


def test_the_real_command_lists_the_questions(rounds, capsys):
    ask()

    assert cli.main(["rounds", "list", A, "--questions", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["entries"] == entries()


# -- an export records nothing -----------------------------------------------------------

def test_no_form_of_an_export_changes_a_byte_of_a_log_or_of_the_index(
        rounds, tmp_path, capsys):
    ask(options=["a=one line", "b=another line"])
    ask("proj-a", at=T2)
    park(A, "Q1")
    rule(text=RULED, at=T4)
    before = store(rounds)
    assert any(path.endswith("index.db") for path in before)

    for argv in ([A, "--questions"], [A, "--questions", "--json"],
                 [A, "--questions", "--out", str(tmp_path / "out")],
                 ["proj-a", "--questions"], ["proj-a", "--questions", "--json"],
                 ["proj-a", "--questions", "--out", str(tmp_path / "out")]):
        printed(capsys, *argv)
        assert store(rounds) == before


def test_a_question_the_index_has_not_seen_is_exported_and_the_index_is_left_stale(
        rounds):
    ask()
    # Another machine's verb: a line the index here has not seen.
    log.append(log.round_log(rounds / "a-round"), "question.asked",
               {"id": "Q2", "text": OTHER, "from": "comms:13", "blocks": "all",
                "state": "open", "asked_by": "helper", "asked_at": T2}, actor="helper")
    before = store(rounds)

    assert [(entry.id, entry.by) for entry in export.questions_of(A).entries] == [
        ("Q1", "scout"), ("Q2", "helper")]
    assert [entry.id for entry in export.questions_of("proj-a").entries] == ["Q1", "Q2"]
    assert store(rounds) == before


def test_an_export_with_no_index_makes_none(rounds):
    ask()
    paths.index_file().unlink()

    assert [entry.id for entry in export.questions_of("proj-a").entries] == ["Q1"]
    assert not paths.index_file().exists()


# -- an entry ----------------------------------------------------------------------------

def test_a_bare_question_is_exported_bare(rounds, capsys):
    ask(actor="scout", origin="comms:11", at=T1)

    assert entries() == [{"holder": A, "id": "Q1", "text": SAID, "from": "comms:11",
                          "blocks": "all", "state": "open", "by": "scout", "at": T1}]
    section = printed(capsys, A, "--questions").split("## Open\n")[1].split("\n## ")[0]
    assert section.strip().splitlines() == [
        f"### {A} Q1: {SAID}", "", "- from: comms:11", "- blocks: all",
        "- state: open", "- by: scout", f"- at: {T1}"]


def test_a_question_with_options_is_exported_with_them_in_the_order_added(
        rounds, capsys):
    ask(options=["b=both follow the settings", "a=prose yes, parsed rows no"])
    options.fill(A, "Q1", actor="helper", options=["c=neither does"])

    [entry] = entries()

    assert entry["options"] == [{"key": "b", "text": "both follow the settings"},
                                {"key": "a", "text": "prose yes, parsed rows no"},
                                {"key": "c", "text": "neither does"}]
    assert set(entry) == BARE | {"options"}
    assert ("- options:\n  - b: both follow the settings\n"
            "  - a: prose yes, parsed rows no\n  - c: neither does\n"
            ) in printed(capsys, A, "--questions")


def test_a_retired_option_is_in_no_form_of_the_export(rounds, tmp_path, capsys):
    ask(options=["a=prose yes, parsed rows no", "b=both follow the settings",
                 "c=a line that was wrong"])
    options.fill(A, "Q1", actor="helper", retire=["c"], options=["d=the right line"])

    [entry] = entries()
    printed(capsys, A, "--questions", "--out", str(tmp_path / "out"))

    assert [option["key"] for option in entry["options"]] == ["a", "b", "d"]
    for text in (printed(capsys, A, "--questions"),
                 printed(capsys, A, "--questions", "--json"),
                 (tmp_path / "out" / "questions.md").read_text(encoding="utf-8"),
                 (tmp_path / "out" / "questions.json").read_text(encoding="utf-8")):
        assert "a line that was wrong" not in text and "the right line" in text


def test_every_entry_says_who_asked_it_and_when(rounds, capsys):
    ask(actor="scout", at=T1)
    ask(text=OTHER, actor="critic", at=T2, options=["a=yes", "b=no"])
    ask("proj-a", text=THIRD, actor="curator", at=T3)
    ask(B, actor="helper", at=T4)
    park(A, "Q2")

    for name in (A, "proj-a"):
        found = entries(name)
        assert found and all(entry["by"] and entry["at"] for entry in found)
        markdown = printed(capsys, name, "--questions")
        for entry in found:
            section = markdown.split(f"### {entry['holder']} {entry['id']}: ")[1]
            section = section.split("\n### ")[0].split("\n## ")[0]
            assert f"- by: {entry['by']}\n- at: {entry['at']}" in section
    assert [(entry["id"], entry["by"], entry["at"]) for entry in entries("proj-a")] == [
        ("P1", "curator", T3), ("Q1", "scout", T1), ("Q1", "helper", T4),
        ("Q2", "critic", T2)]


def test_an_entry_says_what_its_question_holds_up(rounds, capsys):
    ask()
    ask(text=OTHER, at=T2, blocks="B1,B2")
    ask(text=THIRD, at=T3, blocks="none")

    assert [entry["blocks"] for entry in entries()] == ["all", ["B1", "B2"], "none"]
    markdown = printed(capsys, A, "--questions")
    for said in ("- blocks: all", "- blocks: B1,B2", "- blocks: none"):
        assert said in markdown


def test_a_question_moved_to_the_projects_list_says_where_it_was_and_what_it_held_up(
        rounds, capsys):
    ask(blocks="B1")
    moving.move("proj-a", f"{A}:Q1", actor="planner", record="comms:14")

    [entry] = entries("proj-a")

    assert (entry["holder"], entry["id"], entry["blocks"]) == ("proj-a", "P1", "none")
    assert (entry["moved_from"], entry["held"]) == (f"{A}:Q1", ["B1"])
    assert (entry["by"], entry["at"]) == ("scout", T1)
    assert f"- moved from: {A}:Q1, where it held up B1" in printed(
        capsys, "proj-a", "--questions")
    assert entries(A) == []


# -- a round's export: the docket --------------------------------------------------------

def test_the_docket_holds_every_open_question_of_the_round(rounds):
    ask()
    ask(text=OTHER, at=T2, blocks="B2")
    ask(text=THIRD, at=T3, blocks="none")
    ask(text="Is a fourth one listed?", at=T4, options=["a=yes", "b=no"])
    ask(text="Is one that was ruled listed?", at=T4)
    ask(text="Is one that was withdrawn listed?", at=T4)
    rule(item="Q5", text="No.", at=T5)
    rule(item="withdraw:Q6", at=T6)
    ask(B, at=T4)

    found = export.questions_of(A)

    assert named(entry.data() for entry in found.entries) == [
        (A, "Q1", "open"), (A, "Q2", "open"), (A, "Q3", "open"), (A, "Q4", "open")]
    assert found.view == "docket" and found.open == found.entries


@pytest.mark.parametrize("ratify", ["majors", "majors+minors", "off"])
def test_no_setting_leaves_an_open_question_off_the_docket(
        rounds, write_config, ratify):
    write_config(f"""\
        [projects.proj-a]
        ratify = "{ratify}"
        sitting_at = 0
        """)
    ask()
    ask(text=OTHER, at=T2, blocks="none")

    assert named(entries()) == [(A, "Q1", "open"), (A, "Q2", "open")]


def test_naming_the_docket_gives_what_no_view_gives(rounds, capsys):
    ask()
    park(A, "Q1")
    ask(text=OTHER, at=T2)

    assert printed(capsys, A, "--questions", "--view", "docket") == printed(
        capsys, A, "--questions")
    assert export.questions_of(A, view="docket") == export.questions_of(A)


def test_parked_questions_follow_under_their_own_heading(rounds, capsys):
    ask()
    ask(text=OTHER, at=T2)
    ask(text=THIRD, at=T3)
    park(A, "Q1")
    park(A, "Q2", until="B2", at=T6)

    found = entries()
    markdown = printed(capsys, A, "--questions")

    assert named(found) == [(A, "Q3", "open"), (A, "Q1", "parked"), (A, "Q2", "parked")]
    assert "until" not in found[1] and found[2]["until"] == "B2"
    head, parked = markdown.split("## Parked: not put to the operator\n")
    parked = parked.split("\n## ")[0]
    opened = head.split("## Open\n")[1]
    assert THIRD in opened and SAID not in opened and OTHER not in opened
    assert SAID in parked and OTHER in parked and THIRD not in parked
    assert "- state: parked\n" in parked and "- until: B2" in parked
    assert "docket, computed 2026-01-03T00:00:00Z: 1 open, 2 parked" in markdown


def test_a_parked_question_whose_wait_is_over_is_on_the_docket_as_open(rounds):
    ask()
    ask(text=OTHER, at=T2)
    park(A, "Q1", until="B1")
    park(A, "Q2", until=f"round:{B}", at=T6)
    assert named(entries()) == [(A, "Q1", "parked"), (A, "Q2", "parked")]

    finish(rounds, "B1")
    assert named(entries()) == [(A, "Q1", "open"), (A, "Q2", "parked")]
    assert "until" not in entries()[0]

    log.append(log.round_log(rounds / "b-round"), "round.closed", {}, actor="planner")
    assert named(entries()) == [(A, "Q1", "open"), (A, "Q2", "open")]


def test_a_round_with_no_question_exports_none(rounds, capsys):
    found = export.questions_of(A)
    markdown = printed(capsys, A, "--questions")

    assert found.entries == () and found.data()["entries"] == []
    assert "No question is open." in markdown and "No question is parked." in markdown
    assert "\n### " not in markdown


# -- a project's export ------------------------------------------------------------------

def test_a_projects_export_holds_its_list_and_every_open_round(rounds, capsys):
    ask("proj-a", text="Is the list's own question here?", at=T1)
    ask("proj-a", text="Is a parked one of the list here?", at=T2)
    ask(A, at=T3)
    ask(A, text=OTHER, at=T4)
    ask(B, text=THIRD, at=T4, options=["a=yes", "b=no"])
    ask(B, text="Is one that was ruled listed?", at=T4)
    park("proj-a", "P2")
    park(A, "Q2", at=T6)
    rule(B, item="Q2", text="No.", at=T6)

    found = export.questions_of("proj-a")
    markdown = printed(capsys, "proj-a", "--questions")

    assert named(entry.data() for entry in found.entries) == [
        ("proj-a", "P1", "open"), (A, "Q1", "open"), (B, "Q1", "open"),
        ("proj-a", "P2", "parked"), (A, "Q2", "parked")]
    assert found.round is None and found.view is None
    head, parked = markdown.split("## Parked: not put to the operator\n")
    for said in ("### proj-a P1: ", f"### {A} Q1: {SAID}", f"### {B} Q1: {THIRD}"):
        assert said in head and said not in parked
    for said in ("### proj-a P2: ", f"### {A} Q2: {OTHER}"):
        assert said in parked and said not in head
    assert "computed 2026-01-03T00:00:00Z: 3 open, 2 parked" in markdown
    assert "docket" not in markdown


def test_a_projects_export_is_the_rows_waiting_in_the_project(rounds):
    ask("proj-a", at=T1)
    ask(A, at=T2)
    ask(B, at=T3, options=["a=yes", "b=no"])
    park(B, "Q1")

    rows = export.waiting(folders.home("proj-a"))

    assert rows.entries == export.questions_of("proj-a").entries
    assert [entry.data() for entry in rows.entries] == entries("proj-a")
    assert rows.unreadable == ()


def test_a_projects_export_holds_no_question_of_another_project(
        rounds, tmp_path, add_project, capsys):
    add_project("proj-b", notes_root=tmp_path / "other")
    opening.open_round("proj-b/c-round", description="a placeholder round",
                       actor="planner", flow="fix")
    ask("proj-b/c-round", text=OTHER)
    ask("proj-b", text=THIRD)
    ask()

    assert named(entries("proj-a")) == [(A, "Q1", "open")]
    assert named(entries("proj-b")) == [("proj-b", "P1", "open"),
                                        ("proj-b/c-round", "Q1", "open")]


def test_a_view_on_a_project_is_refused(rounds, capsys):
    ask("proj-a")

    assert main(["list", "proj-a", "--questions", "--view", "docket"]) == 3

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "a project's export has no view" in captured.err
    assert "run: moxi rounds list proj-a --questions" in captured.err
    with pytest.raises(Refused):
        export.questions_of("proj-a", view="docket")


# -- the rulings not yet shown -----------------------------------------------------------

def test_every_export_ends_with_the_rulings_not_yet_shown_which_are_no_entries(
        rounds, capsys):
    ask()
    ruling.rule(A, text=RULED, record="comms:12", actor="planner", at=T2)
    ruling.rule("proj-a", text="Rows keep their format.", record="comms:13",
                actor="planner", at=T3)
    ruling.rule(B, text="A ruling of the other round.", record="comms:15",
                actor="planner", at=T4)

    for name in (A, B, "proj-a"):
        found = export.questions_of(name).data()
        markdown = printed(capsys, name, "--questions")

        assert found["read_back"]["through"] == T4
        assert [(row["holder"], row["id"]) for row in found["read_back"]["rows"]] == [
            (A, "C1"), ("proj-a", "S1"), (B, "C1")]
        assert found["read_back"]["rows"][0] == {
            "holder": A, "id": "C1", "text": RULED, "rules": None,
            "record": "comms:12", "supersedes": None, "revokes": None, "at": T2}
        assert all(entry["id"].startswith(("Q", "P")) for entry in found["entries"])
        assert RULED not in json.dumps(found["entries"])
        last = markdown.split("\n## ")[-1]
        assert last.startswith("Rulings not yet shown to the operator\n")
        assert f"not yet shown, through {T4}\n" in last
        assert f"- {A} C1: {RULED} (record: comms:12)" in last
        assert f"moxi rounds rule proj-a readback={T4} --record PTR" in last
        assert "\n### " not in last
        assert RULED not in markdown.split("## Rulings not yet shown")[0]


def test_a_ruling_shown_is_off_the_list_and_one_that_replaces_says_so(rounds, capsys):
    ruling.rule(A, text=RULED, record="comms:12", actor="planner", at=T1)
    ruling.rule(A, text="Usage is shown in the status line.", record="comms:13",
                actor="planner", at=T2)
    ruling.rule(A, text="Usage is shown nowhere.", record="comms:16", actor="planner",
                at=T3, supersedes="C2")
    ruling.rule("proj-a", item=f"readback={T1}", record="comms:20", actor="planner",
                at=T4)

    found = export.questions_of(A).data()["read_back"]

    assert [(row["id"], row["supersedes"]) for row in found["rows"]] == [
        ("C2", None), ("C3", "C2")]
    assert found["through"] == T3
    assert f"- {A} C3: Usage is shown nowhere. (record: comms:16; replaces C2)" in (
        printed(capsys, A, "--questions"))


def test_with_every_ruling_shown_the_section_is_there_and_empty(rounds, capsys):
    ask()

    found = export.questions_of(A).data()
    markdown = printed(capsys, A, "--questions")

    assert found["read_back"] == {"through": None, "rows": []}
    assert markdown.rstrip().endswith(
        "## Rulings not yet shown to the operator\n\n"
        "none, of the rulings of proj-a that could be read")


# -- what can't be listed ----------------------------------------------------------------

def test_questions_with_no_round_or_project_named_is_refused(rounds, capsys):
    assert main(["list", "--questions"]) == 3

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "run: moxi rounds list ROUND|PROJECT --questions" in captured.err


@pytest.mark.parametrize("argv", [
    [A, "--questions", "--view", "ratify"], [A, "--questions", "--view"],
    [A, "--questions", "--out"], ["proj-a/A-Round", "--questions"]])
def test_a_listing_that_cant_be_read_is_a_usage_error(rounds, capsys, argv):
    assert main(["list", *argv]) == 2
    assert capsys.readouterr().out == ""


def test_a_view_that_is_none_of_a_rounds_is_a_usage_error():
    with pytest.raises(BadArgument):
        export.questions_of(A, view="ratify")


def test_a_round_that_was_never_opened_is_refused(rounds, capsys):
    assert main(["list", "proj-a/no-round", "--questions"]) == 3

    assert "moxi rounds open proj-a/no-round" in capsys.readouterr().err
    with pytest.raises(NoRound):
        export.questions_of("proj-a/no-round")


def test_a_project_the_projects_list_does_not_hold_is_refused(rounds, capsys):
    assert main(["list", "proj-z", "--questions"]) == 3
    assert capsys.readouterr().out == ""


def test_a_round_whose_log_cant_be_read_is_not_exported_as_empty(rounds, capsys):
    ask()
    ask(B, text=OTHER, at=T2)
    with log.round_log(rounds / "a-round").open("ab") as file:
        file.write(b"not an event\n")

    assert main(["list", A, "--questions"]) == 3
    captured = capsys.readouterr()
    assert captured.out == "" and "can't be read" in captured.err
    with pytest.raises(Unreadable):
        export.questions_of(A)

    # The project's export holds what can be read, and names the log that can't.
    found = export.questions_of("proj-a")
    markdown = printed(capsys, "proj-a", "--questions")
    assert named(entry.data() for entry in found.entries) == [(B, "Q1", "open")]
    [reason] = found.unreadable
    assert str(log.round_log(rounds / "a-round")) in reason
    assert found.data()["unreadable"] == [reason]
    assert ("## Logs that can't be read\n\nWhat these hold is not in this export.\n\n"
            f"- {reason}\n") in markdown
    assert markdown.index("## Logs that can't be read") < markdown.index(
        "## Rulings not yet shown")


def test_help_says_what_list_does(capsys):
    assert main(["list", "--help"]) == 0

    said = " ".join(capsys.readouterr().out.split())
    for part in ("ROUND|PROJECT", "--questions", "--view docket", "--json", "--out DIR",
                 "questions.md and questions.json"):
        assert part in said


# -- the state a row reads as ------------------------------------------------------------

def test_a_row_of_the_index_reads_as_its_question_does(rounds):
    ask()
    ask(text=OTHER, at=T2)
    ask(text=THIRD, at=T3)
    ask(text="Is a fourth one listed?", at=T4)
    park(A, "Q1")
    park(A, "Q2", until="B1")
    park(A, "Q3", until="B2", at=T6)
    finish(rounds, "B1")

    folded = state.fold(log.read(log.round_log(rounds / "a-round")))
    bodies = dict(questions.waiting(folded))

    assert [questions.listed(bodies[question.id]) for question in folded.questions] == [
        questions.current(question, folded) for question in folded.questions] == [
        "parked", "open", "parked", "open"]
    assert bodies["Q2"]["reached"] is True and "reached" not in bodies["Q3"]
    for closed, reads in ((True, "open"), (False, "parked")):
        assert questions.listed({"state": "parked", "until": f"round:{B}"},
                                closed=lambda name, closed=closed: closed) == reads
    assert index.read("proj-a", rounds, "a-round").items[1].body["reached"] is True
