"""The page's gather: one reading of the open builds a gather, each round's part kept
while nothing it was made from changed, the git pass, the lock a route and the poller
share, the records, the strip's closed rounds, and that nothing is written."""

from __future__ import annotations

import asyncio
import os
import threading

import pytest

from moxi_rounds import (asking, board, checking, freezing, listing, log, next_step, open_builds,
                         opening, paths, showing, steps, verdicts)
from moxi_rounds.errors import RoundsError

ph = pytest.importorskip("moxi_rounds.pagehandlers")

BUSY = "kestrel/billing-export"
QUIET = "kestrel/ledger"
OTHER = "kestrel/archive"
BASE = "0" * 40


@pytest.fixture(autouse=True)
def _fresh_cards():
    ph._cards.clear()
    yield
    ph._cards.clear()


class Clock:
    def __init__(self):
        self.now_s = 1000.0

    def __call__(self) -> float:
        return self.now_s


@pytest.fixture
def kit(tmp_path, add_project, write_config, capsys):
    """Three rounds of `kestrel`: `billing-export`, verified with stage B1 commissioned
    (an open build); `ledger`, fresh; and `archive`, fresh. Returns the rounds folder."""
    add_project("kestrel", notes_root=tmp_path / "notes")
    write_config("")
    for name in (BUSY, QUIET, OTHER):
        opening.open_round(name, description="a placeholder round", by="planner")
    rounds_dir = tmp_path / "notes" / "rounds"
    verified(rounds_dir, BUSY)
    commission(rounds_dir / "billing-export")
    capsys.readouterr()
    return rounds_dir


def verified(rounds_dir, name: str) -> None:
    """Freeze a design of one stage, B1, for the round `name`, verified publishable."""
    round_dir = rounds_dir / name.partition("/")[2]
    (round_dir / "design.md").write_text(
        "# A design\n\n```toml rounds\n[[stage]]\nid = \"B1\"\nrepo = \"kestrel\"\n"
        "branch = \"branch-b1\"\nafter = []\n```\n", encoding="utf-8")
    freezing.freeze(name, by="lead")
    report = round_dir / "report-1.md"
    report.write_text('# Report\n\n```toml rounds\nverdict = "publishable"\npass = "full"\n'
                      'by = "checker"\nmodel = "m"\nkind = "design"\ndesign = 1\n```\n',
                      encoding="utf-8")
    verdicts.record(name, report, by="checker")


def append(round_dir, event_type: str, *, at: str | None = None, **fields) -> None:
    log.append(log.round_log(round_dir), event_type, fields, by="lead", at=at)


def ask(target: str, text: str = "Which ledger keeps the totals?") -> None:
    asking.ask(target, text, by="scout", origin="comms:1")


def commission(round_dir, stage_id: str = "B1", *, at: str | None = None) -> None:
    append(round_dir, "build.commissioned", at=at, stage=stage_id, repo="kestrel",
           branch=f"branch-{stage_id.lower()}", base=BASE, target="main",
           builder="builder-1", build="0123456789abcdef")


@pytest.fixture
def made(monkeypatch):
    """The names of the rounds whose part a gather made, in order."""
    names: list[str] = []
    real = ph._make

    def make(name, *args, **kwargs):
        names.append(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(ph, "_make", make)
    return names


def remade(board_: ph.Board, made: list[str], change) -> set[str]:
    """The rounds whose part is made again by the gather after `change`."""
    board_.current()
    made.clear()
    change()
    board_.current()
    return set(made)


# -- one reading a gather ----------------------------------------------------------------

def test_a_gather_reads_the_open_builds_once(kit, monkeypatch):
    calls = []
    real = open_builds.read
    monkeypatch.setattr(open_builds, "read",
                        lambda *args, **kwargs: calls.append(1) or real(*args, **kwargs))
    value = ph.Board(clock=Clock()).current()
    assert value["state"] == "up"
    assert calls == [1]


def test_steps_are_read_once_for_each_part_made(kit, monkeypatch, made):
    calls, depth = [], [0]
    real = steps.of

    def of(*args, **kwargs):
        # A step's own rule may read the steps again (the retro reads whether the
        # rest are done): only the reads the gather makes are counted.
        if not depth[0]:
            calls.append(1)
        depth[0] += 1
        try:
            return real(*args, **kwargs)
        finally:
            depth[0] -= 1

    monkeypatch.setattr(steps, "of", of)
    board_ = ph.Board(clock=Clock())
    board_.current()
    assert len(calls) == len(made) == 3
    ask(QUIET)
    made.clear()
    calls.clear()
    board_.current()
    assert len(calls) == 1 and made == [QUIET]


def test_a_token_that_did_not_change_gathers_nothing(kit, made):
    board_ = ph.Board(clock=Clock())
    first = board_.current()
    assert board_.current() is first
    assert board_.gathers == 1
    assert len(made) == 3


# -- what a part is kept under -----------------------------------------------------------

def test_a_part_is_made_again_when_its_own_log_is_written(kit, made):
    board_ = ph.Board(clock=Clock())
    assert remade(board_, made, lambda: ask(QUIET)) == {QUIET}


def test_a_part_is_made_again_when_its_project_s_log_is_written(kit, made):
    board_ = ph.Board(clock=Clock())

    assert remade(board_, made, lambda: ask("kestrel")) == {BUSY, QUIET, OTHER}


def test_a_part_is_made_again_when_its_design_is_edited(kit, made):
    board_ = ph.Board(clock=Clock())
    change = lambda: (kit / "billing-export" / "design.md").write_text(  # noqa: E731
        "# changed\n", encoding="utf-8")
    assert remade(board_, made, change) == {BUSY}


def test_every_part_is_made_again_when_the_config_changes(kit, made, write_config):
    board_ = ph.Board(clock=Clock())
    assert remade(board_, made, lambda: write_config("[models]\n")) == {BUSY, QUIET, OTHER}


def test_every_part_is_made_again_when_another_round_closes(kit, made):
    board_ = ph.Board(clock=Clock())
    assert remade(board_, made, lambda: append(kit / "archive", "round.closed")) == {
        BUSY, QUIET, OTHER}
    assert set(board_.parts) == {BUSY, QUIET} and set(board_.closed) == {OTHER}


def test_the_git_pass_makes_again_the_parts_of_rounds_git_reads(kit, made):
    clock = Clock()
    board_ = ph.Board(clock=clock)
    board_.current()
    made.clear()
    clock.now_s += ph.GIT_PASS_S - 1
    board_.current()
    assert made == []
    clock.now_s += 1
    board_.current()
    assert made == [BUSY]
    assert board_.gathers == 2


def test_a_build_s_git_state_is_the_current_reading_s_on_a_kept_part(kit, made,
                                                                     monkeypatch):
    said = ["first reading"]
    real = open_builds.read

    def read(*args, **kwargs):
        found = real(*args, **kwargs)
        return open_builds.OpenBuilds(
            found.computed, found.repo,
            tuple(open_builds.OpenBuild(one.round, one.build,
                                        problem=RoundsError(said[0]))
                  for one in found.builds))

    monkeypatch.setattr(open_builds, "read", read)
    board_ = ph.Board(clock=Clock())
    board_.current()
    said[0] = "second reading"
    made.clear()
    (kit / "ledger" / "design.md").write_text("# ledger\n", encoding="utf-8")
    board_.current()
    assert BUSY not in made
    facts = dict(board_.record("step", BUSY, column="build", stage="B1")["facts"])
    assert facts["problem"] == "second reading"


# -- the lock a route and the poller share -----------------------------------------------

def _held(monkeypatch):
    """The open builds' read held until `go` is set; `started` is set once it began."""
    started, go = threading.Event(), threading.Event()
    real = open_builds.read

    def read(*args, **kwargs):
        started.set()
        go.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(open_builds, "read", read)
    return started, go


def test_a_route_read_during_the_poller_s_gather_answers_that_gather_s_value(
        kit, monkeypatch):
    board_ = ph.Board(clock=Clock())
    started, go = _held(monkeypatch)
    got = {}
    poller = threading.Thread(target=lambda: got.setdefault("poller", board_.current()))
    poller.start()
    assert started.wait(10)
    route = threading.Thread(target=lambda: got.setdefault("route", board_.current()))
    route.start()
    go.set()
    poller.join(10)
    route.join(10)
    assert got["route"] is got["poller"]
    assert board_.gathers == 1


def test_the_poller_asks_nothing_while_a_gather_runs(kit, monkeypatch):
    board_ = ph.Board(clock=Clock())
    started, go = _held(monkeypatch)
    route = threading.Thread(target=board_.current)
    route.start()
    assert started.wait(10)
    assert board_.due() is False
    go.set()
    route.join(10)


def _feed(board_):
    """A feed of `board_` on the running loop, its poller stopped, and the values it
    published."""
    feed = ph._Feed(asyncio.get_running_loop(), board_)
    feed.task.cancel()
    published = []
    real = feed.topics.publish

    def publish(name, value):
        published.append(value)
        real(name, value)

    feed.topics.publish = publish
    return feed, published


def test_a_poller_after_a_route_s_gather_gathers_nothing_and_publishes_it(kit):
    board_ = ph.Board(clock=Clock())
    value = board_.current()

    async def run():
        feed, published = _feed(board_)
        await feed.tick()
        await feed.tick()
        return published

    published = asyncio.run(run())
    assert board_.gathers == 1
    assert published == [value]


def test_the_poller_publishes_only_a_value_that_changed(kit):
    board_ = ph.Board(clock=Clock())

    async def run():
        feed, published = _feed(board_)
        await feed.tick()
        board_.git_at = None
        await feed.tick()
        assert len(published) == 1
        ask(QUIET)
        await feed.tick()
        return published

    published = asyncio.run(run())
    assert board_.gathers == 3
    assert len(published) == 2


def test_the_poller_outlives_a_failed_tick(kit, monkeypatch):
    board_ = ph.Board(clock=Clock())
    monkeypatch.setattr(ph, "PERIOD_S", 0.01)
    failures = []

    def due():
        if not failures:
            failures.append(1)
            raise OSError("a passing failure")
        return True

    monkeypatch.setattr(board_, "due", due)
    monkeypatch.setattr(ph._Feed, "tick", _tick_always_due)

    async def run():
        feed = ph._Feed(asyncio.get_running_loop(), board_)
        for _ in range(200):
            await asyncio.sleep(0.01)
            if board_.gathers:
                break
        feed.task.cancel()

    asyncio.run(run())
    assert failures == [1]
    assert board_.gathers >= 1


async def _tick_always_due(self):
    from moxi import aio
    if await aio.offload(self.board.due):
        await self.snapshot.on_changed()


# -- the records -------------------------------------------------------------------------

def test_the_record_route_never_gathers(kit, made):
    board_ = ph.Board(clock=Clock())
    assert board_.record("step", BUSY, column="design") is None
    board_.current()
    ask(QUIET)
    made.clear()
    found = board_.record("step", BUSY, column="design")
    assert found["column"] == "design" and found["rev"]
    assert board_.record("card", QUIET)["name"] == QUIET
    assert made == [] and board_.gathers == 1


def test_a_closed_round_s_card_is_read_from_its_log_with_no_gather(kit):
    board_ = ph.Board(clock=Clock())
    append(kit / "archive", "round.closed")
    board_.current()
    found = board_.record("card", OTHER)
    assert found["ended"]["how"] == "closed"
    assert board_.gathers == 1


def test_a_huge_question_is_cut_and_its_record_with_it(kit):
    ask(QUIET, "x" * 50_000)
    board_ = ph.Board(clock=Clock())
    value = board_.current()
    [entry] = value["waiting"]["questions"]
    assert len(entry["text"]) == ph.TEXT_MAX_CHARS and entry["text"].endswith("…")
    assert entry["blocks"] == [] and entry["blocks_word"] == "all"
    found = board_.record("question", QUIET, item_id="Q1")
    assert len(found["item"]["text"]) == ph.TEXT_MAX_CHARS


# -- the strip ---------------------------------------------------------------------------

def _closed_rounds(kit, count: int) -> list[str]:
    """`count` closed rounds, each with one act a minute after the last, its log last
    written at that act. Returns their names, oldest first."""
    names = []
    for k in range(count):
        name = f"kestrel/old-{k:02d}"
        opening.open_round(name, description="a placeholder round", by="planner",
                           flow="fix")
        round_dir = kit / f"old-{k:02d}"
        at_s = 4_000_000_000 + 60 * k
        at = log.stamp(at_s)
        append(round_dir, "stage.declared", at=at, id="B1", repo="kestrel",
               branch="branch-b1", after=[])
        commission(round_dir, at=at)
        append(round_dir, "round.closed", at=at)
        os.utime(log.round_log(round_dir), ns=(at_s * 10**9, at_s * 10**9))
        names.append(name)
    return names


def test_the_strip_reads_a_closed_round_only_while_it_can_hold_an_act(kit, monkeypatch,
                                                                       capsys):
    names = _closed_rounds(kit, 13)
    capsys.readouterr()
    shown = []
    real = showing.show
    monkeypatch.setattr(showing, "show",
                        lambda name, **kwargs: shown.append(name) or real(name, **kwargs))
    board_ = ph.Board(clock=Clock())
    value = board_.current()
    read_closed = [name for name in shown if name.startswith("kestrel/old-")]
    assert read_closed == names[::-1][:board.STRIP_SIZE]
    every = [board_.parts[name].part for name in board_.parts]
    every += [ph._make(name, board_.token.settings, board_.token.on_closed(), read={})
              for name in names]
    assert value["recent"] == ph.cut(board.strip(every))


# -- the value ---------------------------------------------------------------------------

def test_a_gather_writes_nothing(kit, tmp_path):
    index_file = paths.index_file()
    before = {path: path.read_bytes() for path in [index_file, *tmp_path.rglob("*.jsonl")]
              if path.exists()}
    ph.Board(clock=Clock()).current()
    after = {path: path.read_bytes() for path in [index_file, *tmp_path.rglob("*.jsonl")]
             if path.exists()}
    assert after == before


def test_each_round_s_step_and_counts_are_the_listing_s(kit):
    value = ph.Board(clock=Clock()).current()
    listed = {one["name"]: one for one in listing.rounds(refresh=False).data()["rounds"]}
    assert {one["name"] for one in value["rounds"]} == set(listed)
    for one in value["rounds"]:
        assert one["step"] == listed[one["name"]]["step"]
        assert one["counts"] == listed[one["name"]]["counts"]


def test_a_round_whose_design_moved_has_the_step_next_reads_not_the_listing_s(kit,
                                                                              capsys):
    verified(kit, QUIET)
    (kit / "ledger" / "design.md").write_text("# changed\n", encoding="utf-8")
    capsys.readouterr()
    value = ph.Board(clock=Clock()).current()
    [row] = [one for one in value["rounds"] if one["name"] == QUIET]
    [listed] = [one for one in listing.rounds(refresh=False).data()["rounds"]
                if one["name"] == QUIET]
    assert listed["step"] == {"step": "commission", "stage": "B1", "state": "ready"}
    assert row["step"] == {"step": "commission", "stage": "B1", "state": "waiting"}
    groups = next_step.read(QUIET)["groups"]
    assert ("commission", "B1", "waiting") in {
        (one["step"], one["stage"], one["state"]) for one in groups}
    assert row["group"] == "stuck"


def test_a_round_that_can_t_be_read_is_a_row_and_the_rest_stand(kit):
    with log.round_log(kit / "ledger").open("ab") as fh:
        fh.write(b"not an event\n")
    value = ph.Board(clock=Clock()).current()
    assert [one["name"] for one in value["unreadable"]] == [QUIET]
    assert {one["name"] for one in value["rounds"]} == {BUSY, OTHER}


def test_a_failed_read_of_the_open_builds_is_the_page_s_error_whole(kit, monkeypatch):
    def read(*args, **kwargs):
        raise RoundsError("git could not be run", "install git")

    monkeypatch.setattr(open_builds, "read", read)
    value = ph.Board(clock=Clock()).current()
    assert value["state"] == "error"
    assert value["detail"] == "git could not be run; install git"
    assert value["rounds"] == []


def test_the_parts_seen_is_what_seen_of_reads_with_the_one_reading(kit, monkeypatch):
    given = []
    real = checking.seen_of
    monkeypatch.setattr(checking, "seen_of", lambda *args, **kwargs: given.append(
        kwargs.get("read")) or real(*args, **kwargs))
    ph.Board(clock=Clock()).current()
    assert all(read is not None for read in given)
