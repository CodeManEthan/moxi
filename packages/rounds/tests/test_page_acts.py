"""The calls a page makes when the operator acts on it, run in process as a page server
runs them: each with a pointer the page minted for the one act, the actor
`actors.default()` gives, and a short wait for the lock. For each act, the event it
writes, each refusal the page can meet, and the repeat: an act whose response was lost
is found again by its pointer."""

from __future__ import annotations

import secrets
from pathlib import Path

import pytest
from moxi import cli

from moxi_rounds import (actors, asking, builds, commissioning, decisions, dropping, log,
                         moving, opening, ruling, shown, state, verdicts, waits, write)
from moxi_rounds import options as choices
from moxi_rounds.errors import BadArgument, Locked, NoRound, Refused, UnknownProject

PROJECT = "atlas"
ROUND = "atlas/billing-export"
ENDED = "atlas/ledger-close"
#: How long a page waits for a round's lock, in seconds.
WAIT_S = 5.0
#: The wait in the tests that hold the lock, kept short so they don't take the page's.
HELD_WAIT_S = 0.2
CHOICE = ["a=export nightly", "b=export on each close"]
SAID = "comms:1234567"
EARLY = "2026-01-02T03:04:05Z"
#: Times in the past the kit's rulings are recorded at, so a read-back can name them and
#: no list leaves one out for falling in the second it is computed in.
LEDGER_AT, S1_AT, C1_AT = ("2026-01-02T03:04:04Z", "2026-01-02T03:04:06Z",
                           "2026-01-02T03:04:07Z")


def minted(at: str | None = None) -> str:
    """A pointer for one act on the rounds page, minted as the page mints it: an act id
    of 43 characters, and the time to the second."""
    return f"page:rounds/{secrets.token_urlsafe(32)}@{log.stamp() if at is None else at}"


@pytest.fixture
def kit(git_repo, tmp_path, write_config, capsys):
    """`atlas` rooted at the test's git repo, with the round `billing-export` in the
    `fix` flow: questions Q1 (with options), Q2, Q3, each holding up B2; B1 commissioned to `builder-1` with
    a commit of work and a report recorded that declares the waiting item B1.W1 and the
    major decision B1.M1; B2 declared. `atlas` has P1 on its list (with options) and
    the standing ruling S1. The round `ledger-close` was dropped. Returns the round's
    folder."""
    write_config("")
    assert cli.main(["projects", "add", PROJECT, "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", PROJECT, "--notes-root",
                     str(tmp_path / "notes")]) == 0
    exclude = git_repo.root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text(".env\n", encoding="utf-8")
    opening.open_round(ROUND, description="export the billing records", by="builder-1",
                       flow="fix", lead="atlas-lead")
    round_dir = tmp_path / "notes" / "rounds" / "billing-export"
    for stage in ("B1", "B2"):
        log.append(log.round_log(round_dir), "stage.declared",
                   {"id": stage, "repo": PROJECT, "branch": f"work-{stage.lower()}",
                    "after": []}, by="atlas-lead")
    # Each holds up B2 only, so B1 can be commissioned.
    asking.ask(ROUND, "When does the export run?", by="atlas-lead", origin=SAID,
               options=CHOICE, blocks="B2")
    asking.ask(ROUND, "Is the export kept a year?", by="atlas-lead", origin=SAID,
               blocks="B2")
    asking.ask(ROUND, "Does the export name the payer?", by="atlas-lead", origin=SAID,
               blocks="B2")
    asking.ask(PROJECT, "Which zone are times shown in?", by="atlas-lead", origin=SAID,
               options=["a=the reader's", "b=UTC"])
    ruling.rule(PROJECT, text="Times are shown in the reader's zone.", record=SAID,
                by="atlas-lead", at=S1_AT)
    made = commissioning.commission(ROUND, "B1", builder="builder-1", by="atlas-lead")
    tree = type(git_repo)(made.worktree)
    tree.commit("export.txt", "the export")
    report = round_dir / "report-b1.md"
    report.write_text(
        "# report\n\n```toml rounds\n" + "\n".join([
            'kind = "build"', 'verdict = "publishable"', 'pass = "full"',
            'by = "critic"', 'model = "a-model"', 'stage = "B1"',
            f'head = "{tree.rev()}"']) +
        '\n\n[[wait]]\nid = "W1"\ntext = "check the export on a phone"'
        '\n\n[[decision]]\nid = "M1"\nweight = "major"\nforecloses = false\n'
        'text = "a failed export is retried twice"\n```\n', encoding="utf-8")
    verdicts.record(ROUND, report, by="critic")
    opening.open_round(ENDED, description="close the ledger", by="builder-1",
                       flow="fix")
    asking.ask(ENDED, "Is the ledger closed monthly?", by="atlas-lead", origin=SAID,
               options=CHOICE)
    ruling.rule(ENDED, text="The ledger closes on the first.", record=SAID,
                by="atlas-lead", at=LEDGER_AT)
    dropping.drop(ENDED, by="operator", record=SAID, run_by="atlas-lead",
                  withdraw_questions=True)
    capsys.readouterr()
    return round_dir


def folded(round_dir) -> state.State:
    return state.fold(log.read(log.round_log(round_dir)))


def last(round_dir) -> dict:
    return log.read(log.round_log(round_dir))[-1]


def raw(round_dir) -> bytes:
    return log.round_log(round_dir).read_bytes()


def own(event: dict) -> dict:
    """An event's own fields, the envelope's `n`, `v` and `at` left out."""
    return {key: value for key, value in event.items() if key not in ("v", "n", "at")}


def refused(call, *args, **more) -> list:
    """The reasons a call is refused for, having written nothing to any log."""
    with pytest.raises(Refused) as caught:
        call(*args, **more)
    return caught.value.unmet


def pick(target=ROUND, item="Q1", *, record=None, **more):
    """The page's call for an answer: an option picked, or his own words."""
    return ruling.rule(target, item=item, record=minted() if record is None else record,
                       by=actors.default(), wait_s=more.pop("wait_s", WAIT_S), **more)


def everything_logged(round_dir) -> dict:
    root = round_dir.parent
    return {str(path): path.read_bytes() for path in sorted(root.rglob("*.jsonl"))}


# -- the actor ---------------------------------------------------------------------------

def test_a_page_records_the_os_user_as_the_command_line_does(kit, monkeypatch):
    monkeypatch.setattr("getpass.getuser", lambda: "builder-1")

    assert actors.default() == "builder-1"
    pick(chose="a")
    assert last(kit)["actor"] == "builder-1"


def test_a_user_with_no_name_is_refused_as_a_bad_argument(monkeypatch):
    def nobody():
        raise KeyError("uid 4242")

    monkeypatch.setattr("getpass.getuser", nobody)
    with pytest.raises(BadArgument, match="the user this runs as has no name"):
        actors.default()


# -- pick an option ----------------------------------------------------------------------

def test_a_pick_is_one_ruling_of_the_question_with_the_pages_pointer(kit):
    pointer = minted()

    made = pick(chose="b", record=pointer)

    assert own(last(kit)) == {
        "actor": actors.default(), "type": "ruling.recorded", "id": "C1",
        "text": "export on each close", "record": pointer, "kind": "substance",
        "rules": "Q1", "chose": "b", "question": "Q1", "state": "ruled",
        "stages": ["B2"]}
    assert made.id == "C1"
    assert folded(kit).questions[0].chose == "b"


def test_a_pick_on_the_projects_list_is_a_ruling_of_the_project(kit):
    pointer = minted()

    pick(PROJECT, "P1", chose="b", record=pointer)

    event = log.read(log.project_log(kit.parent))[-1]
    assert (event["id"], event["rules"], event["chose"], event["record"]) == (
        "S2", "P1", "b", pointer)


def test_his_own_words_are_the_ruling_with_no_key_chosen(kit):
    pick(text="Export on each close, and nightly as well.")

    event = last(kit)
    assert (event["text"], event["question"], event["state"]) == (
        "Export on each close, and nightly as well.", "Q1", "ruled")
    assert "chose" not in event


def test_a_pick_of_a_question_already_ruled_names_the_ruling_to_supersede(kit):
    pick(chose="a")
    before = everything_logged(kit)

    [unmet] = refused(pick, chose="b")

    assert "Q1 is already ruled" in unmet.reason
    assert "--supersedes C1" in unmet.fix
    assert everything_logged(kit) == before


@pytest.mark.parametrize("item, chose, says", [
    ("Q9", "a", "has no question Q9"),
    ("Q1", "c", "c"),
    ("Q2", "a", "a"),
])
def test_a_pick_the_question_cant_take_is_refused(kit, item, chose, says):
    before = everything_logged(kit)

    unmet = refused(pick, item=item, chose=chose)

    assert any(says in one.reason for one in unmet)
    assert everything_logged(kit) == before


def test_a_pick_of_a_retired_key_is_refused(kit):
    choices.fill(ROUND, "Q1", by="atlas-lead", options=["c=export weekly"],
                 retire=["a"])
    before = everything_logged(kit)

    unmet = refused(pick, chose="a")

    assert any("retired" in one.reason for one in unmet)
    assert everything_logged(kit) == before


def test_a_pick_of_a_withdrawn_or_moved_question_is_refused(kit):
    pick(item="withdraw:Q2")
    moving.move(PROJECT, f"{ROUND}:Q3", by="atlas-lead", record=SAID)
    before = everything_logged(kit)

    [withdrawn] = refused(pick, item="Q2", text="Kept a year.")
    [moved] = refused(pick, item="Q3", text="It names the payer.")

    assert "Q2 is already withdrawn" in withdrawn.reason
    assert "P2" in moved.reason
    assert everything_logged(kit) == before


def test_a_pick_on_a_round_that_ended_is_refused(kit):
    unmet = refused(pick, ENDED, chose="a")

    assert any(f"{ENDED} was dropped and stays as it ended" in one.reason
               for one in unmet)


def test_a_pick_on_no_round_or_no_project_is_refused(kit):
    with pytest.raises(NoRound):
        pick("atlas/no-such-round", chose="a")
    with pytest.raises(UnknownProject):
        pick("kestrel/billing-export", chose="a")


# -- accept or reject a decision ---------------------------------------------------------

def test_an_accept_is_one_ruling_with_the_key_chosen_and_no_stages(kit):
    pointer = minted()

    pick(item="B1.M1=accept", record=pointer)

    event = last(kit)
    assert (event["rules"], event["chose"], event["record"]) == (
        "B1.M1=accept", "accept", pointer)
    [one] = decisions.of_round(folded(kit))
    assert (one.state, one.chose) == (decisions.RULED, "accept")


def test_a_reject_opens_a_fix_the_lead_owes(kit):
    pick(item="B1.M1=reject", record=minted())

    assert "B1.M1" in [one.key for one in waits.open_of(folded(kit))]


def test_an_accept_the_round_cant_take_is_refused(kit):
    pick(item="B1.M1=accept")
    before = everything_logged(kit)

    [again] = refused(pick, item="B1.M1=reject")
    [none] = refused(pick, item="B1.M2=accept")
    ended = refused(pick, ENDED, item="B1.M1=accept")

    assert "--supersedes C1" in again.fix
    assert f"{ROUND} has no decision B1.M2" in none.reason
    assert any("stays as it ended" in one.reason for one in ended)
    assert everything_logged(kit) == before


# -- read back one ruling ----------------------------------------------------------------

def test_a_read_back_marks_the_ruling_shown_once(kit):
    made = pick(chose="a")
    pointer = minted()

    marked = shown.mark(ROUND, made.id, record=pointer, by=actors.default(),
                        wait_s=WAIT_S)

    assert marked.marked == ("C1",)
    assert own(last(kit)) == {"actor": actors.default(), "type": "ruling.shown",
                              "ids": ["C1"], "record": pointer}
    before = raw(kit)
    again = shown.mark(ROUND, made.id, record=minted(), by=actors.default(),
                       wait_s=WAIT_S)
    assert (again.marked, again.already, again.event) == ((), ("C1",), None)
    assert raw(kit) == before


def test_a_read_back_of_a_register_entry_is_marked_in_the_projects_log(kit):
    shown.mark(PROJECT, "S1", record=minted(), by=actors.default(), wait_s=WAIT_S)

    assert log.read(log.project_log(kit.parent))[-1]["ids"] == ["S1"]


def test_a_read_back_the_page_cant_make_is_refused(kit):
    said = minted()
    pick(chose="a", record=said)
    before = everything_logged(kit)

    def mark(target, ids, record):
        return shown.mark(target, ids, record=record, by=actors.default(),
                          wait_s=WAIT_S)

    assert "has no ruling C9" in refused(mark, ROUND, "C9", minted())[0].reason
    assert "is the pointer of C1 itself" in refused(mark, ROUND, "C1", said)[0].reason
    assert "a page can only show a ruling on record" in refused(
        mark, ROUND, "C1", minted(EARLY))[0].reason
    assert "is of the project's register" in refused(
        mark, ROUND, "S1", minted())[0].reason
    assert everything_logged(kit) == before


def test_a_read_back_is_itself_never_marked(kit):
    through = shown.unshown(PROJECT).through
    pick(PROJECT, f"readback={through}")

    [unmet] = refused(shown.mark, PROJECT, "S2", record=minted(), by=actors.default())

    assert "S2 is a read-back" in unmet.reason


def test_a_round_that_ended_still_takes_a_read_back(kit):
    marked = shown.mark(ENDED, "C1", record=minted(), by=actors.default(),
                        wait_s=WAIT_S)

    assert marked.marked == ("C1",)


# -- read back the whole list ------------------------------------------------------------

def test_a_read_back_of_the_list_is_a_ruling_of_the_project_through_its_heading(kit):
    pick(chose="a", at=C1_AT)
    listed = shown.unshown(PROJECT)
    pointer = minted()

    pick(PROJECT, f"readback={listed.through}", record=pointer)

    event = log.read(log.project_log(kit.parent))[-1]
    assert (event["rules"], event["record"]) == (f"readback={listed.through}", pointer)
    assert shown.unshown(PROJECT).rows == ()


def test_a_read_back_through_a_time_that_is_no_rulings_is_refused(kit):
    unmet = refused(pick, PROJECT, f"readback={EARLY}")

    assert any(EARLY in one.reason for one in unmet)


# -- mark a job done ---------------------------------------------------------------------

def test_a_job_done_is_one_mark_and_a_second_writes_nothing(kit):
    pointer = minted()

    done = waits.done(ROUND, "B1.W1", record=pointer, by=actors.default(),
                      wait_s=WAIT_S)

    assert own(done.event) == {"actor": actors.default(), "type": "wait.marked",
                               "id": "B1.W1", "state": "done", "record": pointer}
    before = raw(kit)
    again = waits.done(ROUND, "B1.W1", record=minted(), by=actors.default(),
                       wait_s=WAIT_S)
    assert again.found and again.event is None and again.wait.record == pointer
    assert raw(kit) == before


def test_a_job_the_round_cant_mark_is_refused(kit):
    before = everything_logged(kit)

    def done(target, key):
        return waits.done(target, key, record=minted(), by=actors.default(),
                          wait_s=WAIT_S)

    assert "holds no waiting item B1.W9" in refused(done, ROUND, "B1.W9")[0].reason
    assert any("stays as it ended" in one.reason
               for one in refused(done, ENDED, "B1.W1"))
    assert everything_logged(kit) == before


# -- park a question ---------------------------------------------------------------------

@pytest.mark.parametrize("until", [None, "B2", f"round:{ENDED}"])
def test_a_park_is_one_ruling_and_the_question_reads_parked(kit, until):
    pointer = minted()

    pick(item="park:Q2", until=until, record=pointer)

    event = last(kit)
    assert (event["rules"], event["question"], event["state"], event["record"]) == (
        "park:Q2", "Q2", "parked", pointer)
    assert event.get("until") == until
    assert folded(kit).questions[1].state == "parked"


def test_a_park_the_round_cant_take_is_refused(kit):
    pick(item="Q1", chose="a")
    pick(item="withdraw:Q3")
    before = everything_logged(kit)

    assert any("Q1 is already ruled" in one.reason for one in refused(pick, item="park:Q1"))
    assert any("Q3 is already withdrawn" in one.reason for one in refused(pick, item="park:Q3"))
    with pytest.raises(BadArgument, match="is neither a stage nor a round"):
        pick(item="park:Q2", until="tomorrow")
    assert any("stays as it ended" in one.reason
               for one in refused(pick, ENDED, item="park:Q1"))
    assert everything_logged(kit) == before


# -- drop a build ------------------------------------------------------------------------

def drop(target=ROUND, stage="B1", *, record=None, wait_s=WAIT_S):
    return dropping.drop_build(target, stage, by=dropping.OPERATOR,
                               record=minted() if record is None else record,
                               run_by=actors.default(), wait_s=wait_s)


def test_a_drop_is_his_drop_with_the_pages_pointer_and_removes_the_kit_builds_dirs(
        kit, git_repo):
    build = builds.current(folded(kit), "B1")
    commissioned = log.read(log.round_log(kit))
    event = next(one for one in commissioned if one["type"] == "build.commissioned")
    tip = git_repo.rev("work-b1")
    pointer = minted()

    dropped = drop(record=pointer)

    assert own(dropped.event) == {
        "actor": actors.default(), "type": "build.dropped", "stage": "B1",
        "by": "operator", "record": pointer,
        "tips": [{"stage": "B1", "repo": PROJECT, "branch": "work-b1", "tip": tip}]}
    assert dropped.removed == (Path(event["worktree"]), Path(event["scratch"]))
    assert dropped.kept == ()
    assert git_repo.rev("work-b1") == tip
    assert builds.current(folded(kit), "B1").ended == builds.DROPPED
    assert build.ended is None


def test_a_drop_keeps_a_worktree_with_work_in_it_and_says_why(kit):
    event = next(one for one in log.read(log.round_log(kit))
                 if one["type"] == "build.commissioned")
    (Path(event["worktree"]) / "draft.txt").write_text("not committed\n",
                                                        encoding="utf-8")

    dropped = drop()

    assert dropped.removed == (Path(event["scratch"]),)
    [kept] = dropped.kept
    assert kept.place == Path(event["worktree"]) and "draft.txt" in kept.work
    assert (Path(event["worktree"]) / "draft.txt").is_file()


def test_a_drop_the_round_cant_take_is_refused(kit):
    drop()
    before = everything_logged(kit)

    assert "B1 has no open build: its last was dropped" in refused(drop)[0].reason
    assert "B2 has no build" in refused(drop, stage="B2")[0].reason
    assert "B7 is not a stage of" in refused(drop, stage="B7")[0].reason
    assert any("stays as it ended" in one.reason for one in refused(drop, ENDED))
    with pytest.raises(NoRound):
        drop("atlas/no-such-round")
    with pytest.raises(UnknownProject):
        drop("kestrel/billing-export")
    assert everything_logged(kit) == before


# -- a held lock -------------------------------------------------------------------------

@pytest.mark.parametrize("act", ["pick", "accept", "read back", "done", "park", "drop"])
def test_a_round_held_by_another_verb_gives_locked_and_writes_nothing(kit, holder, act):
    calls = {
        "pick": lambda: pick(chose="a", wait_s=HELD_WAIT_S),
        "accept": lambda: pick(item="B1.M1=accept", wait_s=HELD_WAIT_S),
        "read back": lambda: shown.mark(PROJECT, "S1", record=minted(),
                                        by=actors.default(), wait_s=HELD_WAIT_S),
        "done": lambda: waits.done(ROUND, "B1.W1", record=minted(), by=actors.default(),
                                   wait_s=HELD_WAIT_S),
        "park": lambda: pick(item="park:Q2", wait_s=HELD_WAIT_S),
        "drop": lambda: drop(wait_s=HELD_WAIT_S),
    }
    lock = (write.project_lock(PROJECT) if act == "read back"
            else write.round_lock(PROJECT, "billing-export"))
    held = holder(lock)
    before = everything_logged(kit)

    with pytest.raises(Locked) as stopped:
        calls[act]()

    assert stopped.value.holder == held.pid
    assert everything_logged(kit) == before


# -- the repeat --------------------------------------------------------------------------

def made_by(round_dir, pointer: str) -> list:
    """The rulings of the round whose pointer is `pointer`: what a page reads after a
    lost response to find whether its act was recorded."""
    return [one for one in folded(round_dir).rulings if one.record == pointer]


def test_an_act_run_again_after_a_lost_response_is_found_by_its_pointer(kit):
    pointer = minted()
    pick(chose="b", record=pointer)

    [unmet] = refused(pick, chose="b", record=pointer)

    assert "Q1 is already ruled" in unmet.reason
    [found] = made_by(kit, pointer)
    assert (found.id, found.chose) == ("C1", "b")
    assert made_by(kit, minted()) == []


def test_a_drop_run_again_is_found_by_its_pointer(kit):
    pointer = minted()
    drop(record=pointer)

    assert "has no open build" in refused(drop, record=pointer)[0].reason
    assert [one["stage"] for one in log.read(log.round_log(kit))
            if one["type"] == "build.dropped" and one["record"] == pointer] == ["B1"]


def test_two_acts_in_one_second_are_two_pointers(kit):
    at = log.stamp()
    first, second = minted(at), minted(at)

    pick(chose="a", record=first)
    pick(item="park:Q2", record=second)

    assert first != second
    assert [one.id for one in made_by(kit, first)] == ["C1"]
    assert [one.id for one in made_by(kit, second)] == ["C2"]
