"""The trace check: what a design owes a row, what a version's rows are checked for,
what a freezing records of it, what a ruling without a row holds up, and the rulings a
build of one stage is given."""

from __future__ import annotations

import json

import pytest

from moxi_rounds import freezing, log, opening, ruling, state, steps, tracing, versions
from moxi_rounds.cli import main
from moxi_rounds.errors import NoLog

ROUND = "proj-a/a-round"
ASKED = "Do agent-read times follow the settings?"
SAID = "Times are shown in the reader's zone."
PTR = "comms:1234567"


@pytest.fixture
def rounds(tmp_path, add_project, write_config):
    """The rounds folder of `proj-a`, whose rounds are asked for the trace check."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    write_config("""\
        [projects.proj-a]
        trace = true
        """)
    return tmp_path / "notes" / "rounds"


@pytest.fixture
def opened(rounds, capsys):
    """`opened(*inherits)`: open `a-round` and return its folder."""

    def open_from(*inherits):
        opening.open_round(ROUND, description="a placeholder round", by="planner",
                           flow="full", inherits=inherits or None)
        capsys.readouterr()
        return rounds / "a-round"

    return open_from


@pytest.fixture(name="round_dir")
def round_dir_fixture(opened):
    return opened()


def question(id_, **more) -> str:
    lines = ["[[question]]", f'id = "{id_}"', f'text = "{ASKED}"']
    lines += [f"{key} = {value}" for key, value in more.items()]
    return "\n".join(lines)


def stage(id_, repo="proj-a") -> str:
    return "\n".join(["[[stage]]", f'id = "{id_}"', f'repo = "{repo}"',
                      f'branch = "branch-{id_.lower()}"', "after = []"])


def row(id_, how="carried", *, where="the index", record=None, asks=None,
        stages=None) -> str:
    lines = ["[[trace]]", f'id = "{id_}"', f'as = "{how}"']
    if where is not None:
        lines.append(f'where = "{where}"')
    if record is not None:
        lines.append(f'record = "{record}"')
    if asks is not None:
        lines.append(f'question = "{asks}"')
    if stages is not None:
        lines.append(f"stages = {json.dumps(list(stages))}")
    return "\n".join(lines)


def commitment(id_) -> str:
    return "\n".join(["[[commitment]]", f'id = "{id_}"',
                      'text = "A verdict is recorded against the version it read."',
                      'where = "the versions"'])


PLAN = (stage("B1"), stage("B2"), stage("B3"))

_folds = iter(range(1, 1000))


def fold(round_dir, *entries, plan=PLAN, **how):
    """Write `design.md` with the plan's stages and `entries`, and freeze it."""
    text = (f"# A design\n\nDraft {next(_folds)}.\n\n```toml rounds\n"
            + "\n\n".join([*plan, *entries]) + "\n```\n")
    (round_dir / "design.md").write_text(text, encoding="utf-8")
    return freezing.freeze(ROUND, by="scout", **how)


def rule(item=None, target=ROUND, text=SAID, **more):
    if item is not None and item[0] not in "QP":
        text = None
    return ruling.rule(target, item=item, record=PTR, by="planner", text=text,
                       **more).id


def folded(round_dir):
    return state.fold(log.read(log.round_log(round_dir)))


def register(round_dir):
    """The state of the project's log, empty while nothing is recorded on it."""
    try:
        return state.fold(log.read(log.project_log(round_dir.parent)))
    except NoLog:
        return state.State()


def result(round_dir) -> tracing.Result:
    """What the current version's freezing recorded of its check."""
    return tracing.recorded(versions.current(folded(round_dir)))


def ids(found) -> list[str]:
    return [one.id for one in found]


# -- the trace set -----------------------------------------------------------------------

def test_the_set_holds_the_rounds_rulings_that_say_what_is_built(round_dir):
    fold(round_dir, question("Q1"), question("Q2"), question("Q3"))
    plain = rule()
    answer = rule("Q1", text="They follow the settings.")
    acts = [rule("park:Q2"), rule("narrow:Q3=B1"), rule("skip:verify-design")]

    found = tracing.trace_set(folded(round_dir), register(round_dir))

    assert found == (plain, answer)
    assert not set(acts) & set(found)


def test_a_ruling_that_was_replaced_or_taken_back_is_not_in_the_set(round_dir):
    fold(round_dir)
    first, second = rule(), rule(text="Rows are sorted by name.")
    later = rule(text="Times are shown in UTC.", supersedes=first)
    rule(f"revoke:{second}")

    assert tracing.trace_set(folded(round_dir), register(round_dir)) == (later,)


def test_the_set_holds_the_standing_rulings_the_round_was_opened_with(opened):
    one = rule(target="proj-a")
    rule(target="proj-a", text="Rows are sorted by name.")
    other = rule(target="proj-a", text="Nothing is cached.")
    round_dir = opened(other, one)
    own = rule()

    assert tracing.trace_set(folded(round_dir), register(round_dir)) == (other, one, own)


def test_a_standing_ruling_made_from_the_round_is_not_in_its_set(round_dir):
    rule(standing=True)

    assert tracing.trace_set(folded(round_dir), register(round_dir)) == ()


def test_an_inherited_ruling_stands_in_the_set_as_the_ruling_that_replaced_it(opened):
    one = rule(target="proj-a")
    other = rule(target="proj-a", text="Nothing is cached.")
    kept = rule(target="proj-a", text="Rows are sorted by date.")
    round_dir = opened(one, other, kept)
    second = rule(target="proj-a", text="Times are shown in UTC.", supersedes=one)
    assert tracing.trace_set(folded(round_dir), register(round_dir)) == (second, other, kept)
    third = rule(target="proj-a", text="Times are shown in the zone of the page.",
                 supersedes=second)
    rule(f"revoke:{other}", target="proj-a")

    # The live end of what replaced it is owed a row; one taken back is owed none.
    assert tracing.trace_set(folded(round_dir), register(round_dir)) == (third, kept)
    assert tracing.trace_set(folded(round_dir), None) == (one, other, kept)


def test_an_inherited_ruling_whose_replacement_was_taken_back_leaves_the_set(opened):
    one = rule(target="proj-a")
    round_dir = opened(one)
    later = rule(target="proj-a", text="Times are shown in UTC.", supersedes=one)
    rule(f"revoke:{later}", target="proj-a")

    assert tracing.trace_set(folded(round_dir), register(round_dir)) == ()


def test_what_replaced_an_inherited_ruling_is_owed_before_the_rounds_own_rulings(opened):
    one = rule(target="proj-a")
    round_dir = opened(one)
    later = rule(target="proj-a", text="Times are shown in UTC.", supersedes=one)
    own = rule()

    assert tracing.trace_set(folded(round_dir), register(round_dir)) == (later, own)


def test_a_design_with_no_row_for_what_replaced_an_inherited_ruling_fails(opened):
    one = rule(target="proj-a")
    round_dir = opened(one)
    later = rule(target="proj-a", text="Times are shown in UTC.", supersedes=one)

    fold(round_dir)
    assert result(round_dir) == tracing.Result(missing=(later,))
    assert not tracing.passes(folded(round_dir))
    # A row for the replaced id is stale and names what replaced it, which is owed.
    fold(round_dir, row(one))
    assert result(round_dir) == tracing.Result(
        missing=(later,), stale=(tracing.Stale(one, replaced_by=later),))
    fold(round_dir, row(later))
    assert result(round_dir).passes


# -- the check ---------------------------------------------------------------------------

def test_a_version_with_a_row_for_everything_it_owes_passes(opened):
    kept = rule(target="proj-a")
    round_dir = opened(kept)
    plain = rule()

    frozen = fold(round_dir, row(kept), row(plain, "amended", record=PTR),
                  row("R1", "cut", record=PTR), commitment("R1"))

    assert frozen.event["trace"] == {
        "asked": True, "passes": True, "missing": [], "commitments": [],
        "unrecorded": [], "unplaced": [], "stale": []}
    assert result(round_dir) == tracing.Result()
    assert tracing.passes(folded(round_dir)) and tracing.is_asked(folded(round_dir))


def test_a_ruling_of_the_set_with_no_row_fails_the_check_by_id(opened):
    kept, left = rule(target="proj-a"), rule(target="proj-a", text="Nothing is cached.")
    round_dir = opened(kept, left)
    plain, other = rule(), rule(text="Rows are sorted by name.")
    rule("skip:verify-design")

    fold(round_dir, row(kept), row(plain))

    assert result(round_dir) == tracing.Result(missing=(left, other))
    assert not tracing.passes(folded(round_dir))


def test_a_commitment_with_no_row_fails_the_check_by_id(round_dir):
    fold(round_dir, commitment("R1"), commitment("R2"), row("R2"))

    assert result(round_dir) == tracing.Result(commitments=("R1",))
    assert not tracing.passes(folded(round_dir))


@pytest.mark.parametrize("how", ["amended", "cut"])
def test_a_row_that_changes_a_ruling_with_no_pointer_and_no_open_question_fails(
        round_dir, how):
    fold(round_dir, question("Q1"), question("Q2"))
    one, two, three, four, five = (rule(text=f"Ruling {n}.") for n in range(1, 6))
    rule("Q2", text="They do.")
    answered = "C6"

    fold(round_dir, question("Q1"),
         row(one, how), row(two, how, record="the operator said so"),
         row(three, how, record=PTR), row(four, how, asks="Q1"),
         row(five, how, asks="Q2"), row(answered, how, asks="Q7"))

    assert result(round_dir) == tracing.Result(unrecorded=(one, two, five, answered))


def test_a_changed_row_may_name_a_question_its_own_block_asks(round_dir):
    plain = rule()

    fold(round_dir, question("Q1"), row(plain, "amended", asks="Q1"))

    assert result(round_dir) == tracing.Result()


def test_a_carried_row_that_does_not_say_where_fails_the_check_by_id(round_dir):
    one, two, three = (rule(text=f"Ruling {n}.") for n in range(1, 4))

    fold(round_dir, row(one, where=None), row(two, where=" "), row(three),
         row("R1", "cut", where=None, record=PTR), commitment("R1"))

    assert result(round_dir) == tracing.Result(unplaced=(one, two))


def test_a_row_that_cites_a_replaced_ruling_is_stale_with_what_stands_in_its_place(
        round_dir):
    first = rule()
    second = rule(text="Times are shown in UTC.", supersedes=first)
    third = rule(text="Times are shown in the zone of the page.", supersedes=second)

    fold(round_dir, row(first), row(third))

    assert result(round_dir) == tracing.Result(
        stale=(tracing.Stale(first, replaced_by=third),))
    assert not tracing.passes(folded(round_dir))


def test_a_row_that_cites_a_ruling_taken_back_is_stale_with_nothing_in_its_place(
        round_dir):
    first = rule()
    taken = rule(f"revoke:{first}")

    fold(round_dir, row(first))

    assert result(round_dir) == tracing.Result(
        stale=(tracing.Stale(first, revoked_by=taken),))


def test_a_row_that_cites_a_standing_ruling_corrected_since_is_stale(opened):
    one = rule(target="proj-a")
    other = rule(target="proj-a", text="Nothing is cached.")
    round_dir = opened(one, other)
    later = rule(target="proj-a", text="Times are shown in UTC.", supersedes=one)
    taken = rule(f"revoke:{other}", target="proj-a")

    fold(round_dir, row(one), row(other))

    assert result(round_dir) == tracing.Result(missing=(later,), stale=(
        tracing.Stale(one, replaced_by=later), tracing.Stale(other, revoked_by=taken)))


def test_every_clause_that_fails_is_recorded_at_once(round_dir):
    first, left = rule(), rule(text="Rows are sorted by name.")
    later = rule(text="Times are shown in UTC.", supersedes=first)

    frozen = fold(round_dir, row(first, "amended"), row("R2", where=""),
                  commitment("R1"), commitment("R2"))

    assert frozen.event["trace"] == {
        "asked": True, "passes": False, "missing": [left, later],
        "commitments": ["R1"], "unrecorded": [first], "unplaced": ["R2"],
        "stale": [{"id": first, "replaced_by": later, "revoked_by": None}]}


def test_a_version_that_fails_is_frozen_and_the_freezing_says_what_fails(
        round_dir, capsys):
    plain = rule()
    (round_dir / "design.md").write_text(
        "```toml rounds\n" + "\n\n".join([*PLAN, commitment("R1")]) + "\n```\n",
        encoding="utf-8")
    capsys.readouterr()

    assert main(["fold", ROUND]) == 0

    assert versions.current(folded(round_dir)).n == 1
    assert capsys.readouterr().out.splitlines()[-1] == (
        f"trace: v1 fails: no row for {plain}; no row for the commitment R1")


def test_a_version_frozen_as_a_change_of_records_is_checked_too(round_dir):
    plain = rule()
    fold(round_dir)

    fold(round_dir, row(plain), records_only=True, record="file:notes.md#why")

    assert [one.trace["passes"] for one in folded(round_dir).versions] == [False, True]


# -- where the check is not asked for ----------------------------------------------------

def test_a_version_frozen_where_the_check_is_off_records_that_it_was_not_asked_for(
        round_dir, write_config, capsys):
    write_config("")
    rule()

    frozen = fold(round_dir, commitment("R1"))

    assert frozen.event["trace"] == {"asked": False}
    assert result(round_dir) == tracing.NOT_ASKED
    found = folded(round_dir)
    assert tracing.passes(found) and not tracing.is_asked(found)
    assert main(["fold", ROUND]) == 0
    assert not any(line.startswith("trace:")
                   for line in capsys.readouterr().out.splitlines())


def test_a_changed_setting_reaches_the_next_version_and_no_version_already_frozen(
        round_dir, write_config):
    plain = rule()
    fold(round_dir)
    before = log.round_log(round_dir).read_bytes()
    assert not tracing.passes(folded(round_dir))
    assert ids(tracing.holding(folded(round_dir), "B1")) == [plain]

    write_config("")

    found = folded(round_dir)
    assert log.round_log(round_dir).read_bytes() == before
    assert not tracing.passes(found) and ids(tracing.holding(found, "B1")) == [plain]

    fold(round_dir)

    found = folded(round_dir)
    assert [one.trace["asked"] for one in found.versions] == [True, False]
    assert tracing.passes(found) and tracing.holding(found, "B1") == ()


def test_a_design_never_frozen_does_not_pass_and_holds_nothing(round_dir):
    rule()

    found = folded(round_dir)

    assert not tracing.passes(found) and not tracing.is_asked(found)
    assert tracing.holding(found, "B1") == ()
    assert tracing.given(found, register(round_dir), "B1") == ()
    assert tracing.shown(found, register(round_dir)) is None


def test_a_version_whose_event_holds_no_result_did_not_run_the_check(round_dir):
    # Core's gates read a trace-check run that failed as no pass.
    fold(round_dir)
    events = log.read(log.round_log(round_dir))
    del events[-1]["trace"]

    found = state.fold(events)

    assert tracing.recorded(versions.current(found)) is None
    assert tracing.outcome(versions.current(found)) == tracing.NOT_RUN
    assert not tracing.passes(found) and not tracing.is_asked(found)


# -- what a ruling with no row holds up --------------------------------------------------

def test_a_ruling_recorded_after_the_freezing_holds_only_the_stages_it_applies_to(
        round_dir):
    fold(round_dir, question("Q1", blocks='["B3"]'))
    plain = rule()
    scoped = rule(text="The second build sorts rows.", stages="B2")
    answer = rule("Q1", text="They do.")

    found = folded(round_dir)

    assert tracing.passes(found)
    assert ids(tracing.holding(found, "B1")) == [plain]
    assert ids(tracing.holding(found, "B2")) == [plain, scoped]
    assert ids(tracing.holding(found, "B3")) == [plain, answer]


def test_the_answer_to_a_question_that_held_no_stage_holds_none(round_dir):
    fold(round_dir, question("Q1", blocks='"none"'))
    rule("Q1", text="They do.")

    found = folded(round_dir)

    assert [tracing.holding(found, one) for one in ("B1", "B2", "B3")] == [(), (), ()]


def test_an_act_on_the_process_holds_no_stage(round_dir):
    fold(round_dir, question("Q1"), question("Q2"), question("Q3"))
    rule("park:Q1"), rule("narrow:Q2=B1"), rule("withdraw:Q3")
    rule("skip:verify-design"), rule("skip:docket:B1")

    found = folded(round_dir)

    assert [tracing.holding(found, one) for one in ("B1", "B2", "B3")] == [(), (), ()]


def test_a_ruling_holds_its_stages_until_a_version_cites_it(round_dir):
    fold(round_dir)
    plain = rule(stages="B1")

    fold(round_dir)
    assert ids(tracing.holding(folded(round_dir), "B1")) == [plain]

    fold(round_dir, row(plain))
    assert tracing.holding(folded(round_dir), "B1") == ()


def test_a_replacement_holds_its_stages_until_a_row_cites_it_and_the_old_row_none(
        round_dir):
    fold(round_dir)
    first = rule(stages="B1,B2")
    fold(round_dir, row(first))
    later = rule(text="Times are shown in UTC.", stages="B2", supersedes=first)

    found = folded(round_dir)

    assert tracing.holding(found, "B1") == ()
    assert ids(tracing.holding(found, "B2")) == [later]
    assert tracing.stale(found, register(round_dir)) == (
        tracing.Stale(first, replaced_by=later),)
    assert result(round_dir) == tracing.Result()

    fold(round_dir, row(later))

    assert tracing.holding(folded(round_dir), "B2") == ()
    assert tracing.stale(folded(round_dir), register(round_dir)) == ()


def test_a_ruling_taken_back_leaves_a_stale_row_that_holds_nothing(round_dir):
    first = rule()
    fold(round_dir, row(first))
    taken = rule(f"revoke:{first}")

    found = folded(round_dir)

    assert [tracing.holding(found, one) for one in ("B1", "B2", "B3")] == [(), (), ()]
    assert tracing.stale(found, register(round_dir)) == (
        tracing.Stale(first, revoked_by=taken),)
    assert tracing.passes(found)


def test_a_ruling_with_no_row_holds_nothing_where_the_check_was_not_asked_for(
        round_dir, write_config):
    write_config("")
    fold(round_dir)
    plain = rule()

    found = folded(round_dir)

    assert tracing.holding(found, "B1") == ()
    assert ids(tracing.unrowed(found, "B1")) == [plain]


# -- the rulings a build is given --------------------------------------------------------

def test_a_build_is_given_the_rulings_its_stage_has_a_carried_or_amended_row_for(
        opened):
    kept = rule(target="proj-a")
    rule(target="proj-a", text="Nothing is cached.")
    round_dir = opened(kept)
    carried, amended, cut, scoped, uncited = (
        rule(text=f"Ruling {n}.") for n in range(1, 6))
    fold(round_dir, row(kept), row(carried), row(amended, "amended", record=PTR),
         row(cut, "cut", record=PTR), row(scoped, stages=["B2", "B3"]),
         row("R1"), commitment("R1"))

    found = folded(round_dir)

    for stage_id, expected in (("B1", [kept, carried, amended]),
                               ("B2", [kept, carried, amended, scoped])):
        given = tracing.given(found, register(round_dir), stage_id)
        assert [one.ruling.id for one in given] == expected
        assert all(one.replaces is None for one in given)
    assert tracing.given(found, register(round_dir), "B1")[1].ruling.text == "Ruling 1."
    assert uncited not in ids(one.ruling for one in
                              tracing.given(found, register(round_dir), "B2"))


def test_a_replaced_ruling_is_given_as_the_ruling_that_stands_in_its_place(round_dir):
    first = rule()
    fold(round_dir, row(first))
    second = rule(text="Times are shown in UTC.", supersedes=first)
    third = rule(text="Times are shown in the zone of the page.", supersedes=second)

    (given,) = tracing.given(folded(round_dir), register(round_dir), "B1")

    assert (given.ruling.id, given.replaces) == (third, first)
    assert given.ruling.text == "Times are shown in the zone of the page."


def test_a_ruling_taken_back_is_not_given_and_neither_is_one_whose_replacement_was(
        round_dir):
    first, other = rule(), rule(text="Rows are sorted by name.")
    fold(round_dir, row(first), row(other))
    rule(f"revoke:{first}")
    later = rule(text="Rows are sorted by date.", supersedes=other)
    rule(f"revoke:{later}")

    assert tracing.given(folded(round_dir), register(round_dir), "B1") == ()


def test_a_replacement_whose_scope_leaves_the_stage_out_is_not_given_there(round_dir):
    fold(round_dir)
    first = rule()
    fold(round_dir, row(first))
    later = rule(text="Times are shown in UTC.", stages="B2", supersedes=first)

    found = folded(round_dir)

    assert tracing.given(found, register(round_dir), "B1") == ()
    assert [(one.ruling.id, one.replaces)
            for one in tracing.given(found, register(round_dir), "B2")] == [(later, first)]


def test_a_standing_replacement_is_given_where_its_scope_names_the_stages_repo(
        opened, add_project):
    add_project("proj-b")
    one = rule(target="proj-a")
    other = rule(target="proj-a", text="Nothing is cached.")
    round_dir = opened(one, other)
    plan = (stage("B1"), stage("B2", repo="proj-b"))
    fold(round_dir, row(one), row(other), plan=plan)
    scoped = rule(target="proj-a", text="Times are shown in UTC.", supersedes=one,
                  scope="proj-b:src/**")
    wide = rule(target="proj-a", text="Nothing is cached, ever.", supersedes=other)

    found = folded(round_dir)

    assert [(one.ruling.id, one.replaces)
            for one in tracing.given(found, register(round_dir), "B1")] == [(wide, other)]
    assert [(one.ruling.id, one.replaces)
            for one in tracing.given(found, register(round_dir), "B2")] == [
                (scoped, one), (wide, other)]


def test_a_row_that_cites_an_act_on_the_process_or_no_ruling_gives_nothing(round_dir):
    fold(round_dir, question("Q1"))
    parked = rule("park:Q1")
    fold(round_dir, question("Q1"), row(parked), row("C9"), row("S4"))

    assert tracing.given(folded(round_dir), register(round_dir), "B1") == ()


def test_a_build_is_given_the_same_rulings_with_the_check_on_or_off(
        round_dir, write_config):
    write_config("")
    first = rule()
    fold(round_dir, row(first))
    later = rule(text="Times are shown in UTC.", supersedes=first)

    assert [(one.ruling.id, one.replaces) for one in
            tracing.given(folded(round_dir), register(round_dir), "B1")] == [(later, first)]


# -- show and the note -------------------------------------------------------------------

def shown_lines(capsys) -> list[str]:
    capsys.readouterr()
    assert main(["show", ROUND]) == 0
    return capsys.readouterr().out.splitlines()


def test_the_result_its_ids_and_the_stale_rows_are_shown_and_in_the_note(
        round_dir, capsys):
    first, left = rule(), rule(text="Rows are sorted by name.")
    later = rule(text="Times are shown in UTC.", supersedes=first)
    fold(round_dir, row(first, "amended"), row("R2", where=""), commitment("R1"),
         commitment("R2"))

    said = (f"trace: v1 fails: no row for {left}, {later}; no row for the commitment "
            f"R1; amended or cut with no pointer and no open question: {first}; "
            f"carried with no where: R2; stale: {first}")
    stale = f"stale row: {first}, replaced by {later}"
    lines = shown_lines(capsys)
    assert said in lines and stale in lines
    note = (round_dir / "round-note.md").read_text(encoding="utf-8").splitlines()
    assert f"- {said}" in note and f"- {stale}" in note
    assert main(["show", ROUND, "--json"]) == 0
    found = json.loads(capsys.readouterr().out)
    row_data = {"id": first, "replaced_by": later, "revoked_by": None}
    assert found["trace"] == {
        "version": 1, "asked": True, "passes": False, "missing": [left, later],
        "commitments": ["R1"], "unrecorded": [first], "unplaced": ["R2"],
        "stale": [row_data], "stale_now": [row_data]}
    assert found["versions"][0]["trace"]["missing"] == [left, later]


def test_a_version_that_passes_is_shown_as_passing(round_dir, capsys):
    plain = rule()
    fold(round_dir, row(plain))

    assert "trace: v1 passes" in shown_lines(capsys)
    note = (round_dir / "round-note.md").read_text(encoding="utf-8").splitlines()
    assert "- trace: v1 passes" in note


def test_a_row_gone_stale_since_the_freezing_is_shown_beside_the_recorded_result(
        round_dir, capsys):
    first, other = rule(), rule(text="Rows are sorted by name.")
    fold(round_dir, row(first), row(other))
    taken = rule(f"revoke:{first}")
    later = rule(text="Rows are sorted by date.", supersedes=other)

    lines = shown_lines(capsys)

    assert "trace: v1 passes" in lines
    assert f"stale row: {first}, taken back by {taken}" in lines
    assert f"stale row: {other}, replaced by {later}" in lines
    note = (round_dir / "round-note.md").read_text(encoding="utf-8").splitlines()
    assert f"- stale row: {first}, taken back by {taken}" in note


def test_where_the_check_is_off_only_the_stale_rows_are_shown(
        round_dir, write_config, capsys):
    write_config("")
    first = rule()
    fold(round_dir, row(first))
    later = rule(text="Times are shown in UTC.", supersedes=first)

    lines = shown_lines(capsys)

    assert not any(line.startswith("trace:") for line in lines)
    assert f"stale row: {first}, replaced by {later}" in lines
    assert main(["show", ROUND, "--json"]) == 0
    found = json.loads(capsys.readouterr().out)["trace"]
    assert (found["version"], found["asked"]) == (1, False)
    assert found["stale_now"] == [
        {"id": first, "replaced_by": later, "revoked_by": None}]


def test_what_replaced_an_inherited_ruling_after_the_freezing_holds_the_stage(opened):
    one = rule(target="proj-a")
    scoped = rule(target="proj-a", text="Rows are sorted by date.",
                  scope="proj-b:src/**")
    round_dir = opened(one, scoped)
    fold(round_dir, row(one), row(scoped))
    assert tracing.holding(folded(round_dir), "B1", register(round_dir)) == ()

    later = rule(target="proj-a", text="Times are shown in UTC.", supersedes=one)
    narrower = rule(target="proj-a", text="Rows are sorted by name.",
                    scope="proj-b:src/**", supersedes=scoped)

    # A standing ruling holds a stage when its scope names no repo or the stage's.
    assert ids(tracing.holding(folded(round_dir), "B1", register(round_dir))) == [later]
    assert narrower not in ids(tracing.holding(folded(round_dir), "B2", register(round_dir)))
    # Without the project's log, only the round's own rulings are read.
    assert tracing.holding(folded(round_dir), "B1") == ()
    found = steps.one(folded(round_dir), "commission", "B1", name=ROUND,
                      register=register(round_dir))
    assert [one.reason.split(" until ")[0] for one in found.waits][-1:] == [
        f"{later} holds B1"]
