"""The re-stamp: `mark ROUND --restamp --record PTR` records the digest of the frozen copy
of the design's current version when an edit outside the round rewrote the copy and
`design.md` alike. What `next`, `commission` and the check read of the design says
which of three cases it is, the repair refuses what is not that case, it changes no
standing, and an ended round takes it while the other repairs still refuse one."""

from __future__ import annotations

import hashlib

import pytest

from moxi_rounds import (brake, dropping, ending, freezing, index, log, opening, repairs,
                         showing, state, verdicts, versions)
from moxi_rounds.cli import main
from moxi_rounds.errors import Refused

ROUND = "proj-a/a-round"
POINTER = "comms:4242"
PLAN = '[[stage]]\nid = "B1"\nrepo = "proj-a"\nbranch = "work"\nafter = []\n'


@pytest.fixture
def round_dir(tmp_path, add_project, capsys):
    add_project("proj-a", notes_root=tmp_path / "notes")
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="full")
    capsys.readouterr()
    return tmp_path / "notes" / "rounds" / "a-round"


_drafts = iter(range(1, 1000))


def fold(round_dir, text: str | None = None):
    """Write `design.md`, a new draft when `text` is None, and freeze it."""
    if text is None:
        text = f"# A design\n\nDraft {next(_drafts)}.\n\n```toml rounds\n{PLAN}```\n"
    (round_dir / "design.md").write_text(text, encoding="utf-8")
    return freezing.freeze(ROUND, by="scout")


def frozen_text(round_dir, n: int) -> str:
    return versions.file(round_dir, n).read_text(encoding="utf-8")


def edit_alike(round_dir, *numbers: int) -> None:
    """Rewrite design.md and the frozen copies `numbers` as a rename of the notes does:
    each copy and design.md gain the same line."""
    for n in numbers:
        copy = versions.file(round_dir, n)
        copy.write_text(copy.read_text(encoding="utf-8") + "Moved.\n", encoding="utf-8")
    newest = versions.file(round_dir, max(numbers)).read_text(encoding="utf-8")
    (round_dir / "design.md").write_text(newest, encoding="utf-8")


_reports = iter(range(1, 1000))


def verdict(round_dir, word: str, design: int):
    count = next(_reports)
    path = round_dir / f"report-{count}.md"
    path.write_text("# A report\n\n```toml rounds\n" + "\n".join([
        'kind = "design"', f'verdict = "{word}"', 'pass = "full"', 'by = "critic"',
        'model = "a-model"', f"design = {design}"]) + "\n```\n", encoding="utf-8")
    return verdicts.record(ROUND, path, by="critic")


def folded(round_dir) -> state.State:
    return state.fold(log.read(log.round_log(round_dir)))


def warned(round_dir):
    return versions.unchanged(folded(round_dir), round_dir, ROUND)


def restamp(record: str | None = POINTER):
    return repairs.restamp(ROUND, record=record, by="planner")


def refused(round_dir, record: str | None = POINTER) -> str:
    before = log.round_log(round_dir).read_bytes()
    with pytest.raises(Refused) as caught:
        restamp(record)
    assert log.round_log(round_dir).read_bytes() == before
    return str(caught.value)


# -- the three readings ----------------------------------------------------------------

def test_a_design_that_holds_its_digest_is_unchanged(round_dir):
    fold(round_dir)
    assert warned(round_dir) is None


def test_a_design_edited_alike_with_its_copy_is_offered_a_restamp(round_dir):
    fold(round_dir)
    edit_alike(round_dir, 1)

    found = warned(round_dir)

    assert found.reason == (
        "the frozen copy versions/design-v1.md was edited after v1 was frozen, as "
        "design.md was: the two match each other and neither is what v1 was frozen "
        "from")
    assert found.fix == (f"moxi rounds mark {ROUND} --restamp --record PTR, once you "
                         "know what edited them")


@pytest.mark.parametrize("copy_gone", [False, True])
def test_a_design_that_differs_from_its_copy_is_offered_a_fold(round_dir, copy_gone):
    fold(round_dir)
    (round_dir / "design.md").write_text("# A new draft\n", encoding="utf-8")
    if copy_gone:
        versions.file(round_dir, 1).unlink()

    found = warned(round_dir)

    assert found.reason == "design.md changed since v1 was frozen"
    assert found.fix == f"moxi rounds fold {ROUND}"


# -- the repair --------------------------------------------------------------------------

def test_a_restamp_records_the_copy_s_digest_and_the_warning_goes(round_dir):
    fold(round_dir)
    was = versions.digest(folded(round_dir))
    edit_alike(round_dir, 1)
    now = hashlib.sha256(versions.file(round_dir, 1).read_bytes()).hexdigest()

    done = restamp()

    assert {key: done.event[key] for key in ("type", "what", "version", "record",
                                             "fields", "read")} == {
        "type": "repair", "what": "digest", "version": 1, "record": POINTER,
        "fields": {"digest": {"was": was, "now": now}},
        "read": str(versions.file(round_dir, 1))}
    assert done.said.startswith(f"digest of v1: {was[:12]} to {now[:12]}")
    version = versions.current(folded(round_dir))
    assert (version.digest, version.restamped, versions.held(version)) == (was, now, now)
    assert warned(round_dir) is None


def test_a_design_that_holds_its_digest_writes_nothing_and_says_so(round_dir):
    fold(round_dir)
    before = log.round_log(round_dir).read_bytes()

    done = restamp()

    assert done.event is None
    assert done.said == "design.md holds the digest of v1: nothing to re-stamp, and " \
                        "nothing was written"
    assert log.round_log(round_dir).read_bytes() == before


def test_a_restamp_needs_a_pointer_of_a_known_shape(round_dir):
    fold(round_dir)
    edit_alike(round_dir, 1)
    assert "a re-stamp says where the lead named what edited the design" in refused(
        round_dir, None)
    assert "is not a pointer to where the lead named what edited the design" in refused(
        round_dir, "not-a-pointer")


def test_a_round_never_frozen_has_nothing_to_restamp(round_dir):
    assert "was never frozen, so there is no version to re-stamp" in refused(round_dir)


def test_a_design_that_differs_from_its_copy_is_refused_with_the_fold(round_dir):
    fold(round_dir)
    (round_dir / "design.md").write_text("# A new draft\n", encoding="utf-8")
    said = refused(round_dir)
    assert "design.md differs from the frozen copy of v1: that is a change of the " \
           "design" in said
    assert f"moxi rounds fold {ROUND}" in said


def test_a_copy_that_can_t_be_read_is_refused(round_dir):
    fold(round_dir)
    (round_dir / "design.md").write_text("# A new draft\n", encoding="utf-8")
    versions.file(round_dir, 1).unlink()
    assert "can't be read" in refused(round_dir)


def test_mark_restamp_prints_what_it_did_and_names_no_id(round_dir, capsys):
    fold(round_dir)
    edit_alike(round_dir, 1)
    assert main(["mark", ROUND, "B1", "--restamp", "--record", POINTER]) != 0
    assert "names nothing else" in capsys.readouterr().err

    assert main(["mark", ROUND, "--restamp", "--record", POINTER]) == 0

    assert capsys.readouterr().out.startswith(f"{ROUND}: digest of v1: ")


def test_a_restamp_is_shown_under_repairs_and_on_the_versions_line(round_dir):
    fold(round_dir)
    was = versions.digest(folded(round_dir))
    edit_alike(round_dir, 1)
    now = restamp().event["fields"]["digest"]["now"]

    found = showing.show(ROUND)
    text = showing.as_text(found)

    assert f"digest of v1: {was[:12]} to {now[:12]}, record {POINTER}" in text
    assert f"sha256 {now} (re-stamped from {was[:12]})" in text


# -- the standing ------------------------------------------------------------------------

def _record(round_dir) -> tuple:
    state_ = folded(round_dir)
    return (str(versions.standing(state_)), versions.sent_back(state_),
            versions.rewritten(state_), versions.text_unchanged_since(state_, 1),
            brake.asked(state_, name=ROUND))


@pytest.mark.parametrize("word", ["publishable", "fold-first"])
def test_a_restamp_changes_no_standing_and_a_freeze_of_the_same_text_keeps_it(
        round_dir, word):
    fold(round_dir)
    verdict(round_dir, word, design=1)
    fold(round_dir, frozen_text(round_dir, 1))
    before = _record(round_dir)
    assert before[0] == f"{word}@v2" and before[3] is True

    edit_alike(round_dir, 1, 2)
    restamp()

    assert _record(round_dir) == before
    fold(round_dir, frozen_text(round_dir, 2))
    state_ = folded(round_dir)
    assert str(versions.standing(state_)) == f"{word}@v3"
    assert versions.text_unchanged_since(state_, 1) is True
    assert versions.rewritten(state_) is before[2]
    assert versions.sent_back(state_) == before[1]


# -- an ended round ----------------------------------------------------------------------

def test_an_ended_round_takes_a_restamp_and_its_ending_and_index_stay(round_dir):
    fold(round_dir)
    dropping.drop(ROUND, by="operator", record="comms:1234567", run_by="planner")
    edit_alike(round_dir, 1)
    how = ending.how(folded(round_dir))
    row = index.read("proj-a", round_dir.parent, "a-round")

    done = restamp()

    assert done.event is not None and warned(round_dir) is None
    assert ending.how(folded(round_dir)) == how
    after = index.read("proj-a", round_dir.parent, "a-round")
    assert (after.status, after.ended) == (row.status, row.ended)
    with pytest.raises(Refused, match="dropped"):
        repairs.resync(ROUND, "B1", by="planner")
    with pytest.raises(Refused, match="dropped"):
        repairs.reread(ROUND, round_dir / "design.md", by="planner")
