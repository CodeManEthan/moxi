"""The write path: the steps every writing verb runs, what a refusal leaves, a short act
and a long one, and what happens after the append."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from moxi import exit as exits
from moxi import files
from moxi_rounds import log, state, write
from moxi_rounds.errors import NoLog, Refused, RoundsError, Unmet, Unreadable

OPENED = {"description": "a placeholder round", "flow": "fix",
          "steps": [{"step": "build", "by": "builder"}]}
DONE = {"stage": "B1", "tip": "0" * 40}
SHOWN = {"ids": ["S1"], "record": "comms:12"}
AT = "2026-01-02T03:04:05Z"

#: Exits 0 when the lock file named is free and 1 when another process holds it.
PROBE = textwrap.dedent("""
    import sys
    from moxi import files
    try:
        with files.lock(sys.argv[1], timeout_s=0):
            pass
    except files.LockTimeout:
        sys.exit(1)
""")

#: Appends `count` events to one round through the write path, as `actor`.
WRITER = textwrap.dedent("""
    import sys
    from moxi_rounds import write
    folder, actor, count = sys.argv[1], sys.argv[2], int(sys.argv[3])
    where = write.Where("proj-a", folder, "a-round")
    print("ready", flush=True)
    sys.stdin.readline()
    for number in range(count):
        write.record(where, actor=actor, event=lambda view, result: write.Entry(
            "finish.done", {"stage": f"B{number + 1}", "tip": "0" * 40}))
""")


@pytest.fixture
def where(tmp_path):
    """The round `proj-a/a-round`, opened, in a rounds folder of the test's own."""
    folder = tmp_path / "rounds"
    (folder / "a-round").mkdir(parents=True)
    where = write.Where("proj-a", folder, "a-round")
    log.append(where.round_log, "round.opened", OPENED, actor="planner", at=AT)
    return where


def done(view, result):
    return write.Entry("finish.done", DONE)


def shown(view, result):
    return write.Entry("ruling.shown", SHOWN, log=write.PROJECT)


def held_elsewhere(path) -> bool:
    """Whether a process other than a new one holds the lock file `path` (this one, in
    these tests)."""
    return subprocess.run([sys.executable, "-c", PROBE, str(path)], timeout=30).returncode == 1


def types(path):
    return [event["type"] for event in log.read(path)]


# -- the steps ---------------------------------------------------------------------------

def test_a_write_reads_gates_acts_and_appends_in_that_order(where):
    order = []

    def facts(where):
        order.append("facts")
        return {"tip": "1" * 40}

    def gate(view):
        order.append("gate")

    def act(view):
        order.append("act")
        return "the act's result"

    def event(view, result):
        order.append("event")
        assert result == "the act's result"
        return write.Entry("finish.done", {"stage": "B1", "tip": view.facts["tip"]})

    written = write.record(where, actor="coder", event=event, gates=[gate], act=act,
                           facts=facts, at=AT, on_append=[lambda written:
                                                          order.append("after")])

    assert order == ["facts", "gate", "act", "event", "after"]
    assert written.events == ({"v": 1, "n": 2, "at": AT, "actor": "coder",
                               "type": "finish.done", "stage": "B1", "tip": "1" * 40},)
    assert written.result == "the act's result" and written.failed == ()
    assert log.read(where.round_log)[1:] == list(written.events)


def test_the_view_holds_both_logs_their_states_and_the_verbs_facts(where):
    log.append(where.project_log, "ruling.shown", SHOWN, actor="planner", at=AT)
    seen = []

    write.record(where, actor="planner", event=done, gates=[seen.append],
                 facts=lambda where: "a fact")

    view = seen[0]
    assert view.where == where and view.facts == "a fact"
    assert [event["type"] for event in view.events] == ["round.opened"]
    assert view.state == state.fold(view.events) and view.state.status == state.OPEN
    assert [event["type"] for event in view.project_events] == ["ruling.shown"]
    assert view.project_state == state.fold(view.project_events)


def test_a_write_with_no_act_and_no_gate_appends_its_event(where):
    written = write.record(where, actor="planner", event=done)

    assert written.result is None
    assert types(where.round_log) == ["round.opened", "finish.done"]


def test_a_verb_may_append_several_events_and_to_both_logs(where):
    def event(view, result):
        return [write.Entry("finish.done", DONE), write.Entry("ruling.shown", SHOWN,
                                                              log=write.PROJECT),
                write.Entry("round.closed", {})]

    written = write.record(where, actor="planner", event=event, with_project=True)

    assert [(event["type"], event["n"]) for event in written.events] == [
        ("finish.done", 2), ("ruling.shown", 1), ("round.closed", 3)]
    assert [event["n"] for event in written.round_events] == [1, 2, 3]
    assert list(written.round_events) == log.read(where.round_log)
    assert list(written.project_events) == log.read(where.project_log)


def test_a_verb_that_gives_no_event_appends_nothing(where):
    before = where.round_log.read_bytes()

    written = write.record(where, actor="planner", event=lambda view, result: None)

    assert written.events == () and where.round_log.read_bytes() == before


def test_a_write_on_a_project_alone_appends_to_the_projects_log(where):
    project = write.Where("proj-a", where.folder)
    seen = []

    written = write.record(project, actor="planner", event=shown, gates=[seen.append])

    assert project.round_log is None
    assert seen[0].events == [] and seen[0].state.status is None
    assert written.round_events == ()
    assert types(where.project_log) == ["ruling.shown"]
    assert types(where.round_log) == ["round.opened"]


def test_a_verb_that_opens_a_round_reads_a_missing_log_as_no_events(where):
    (where.folder / "b-round").mkdir()
    other = write.Where("proj-a", where.folder, "b-round")
    seen = []

    write.record(other, actor="planner", opens=True, gates=[seen.append],
                 event=lambda view, result: write.Entry("round.opened", OPENED))

    assert seen[0].events == [] and seen[0].state.status is None
    assert types(other.round_log) == ["round.opened"]


def test_any_other_verb_refuses_a_round_that_has_no_log(where):
    other = write.Where("proj-a", where.folder, "b-round")
    ran = []

    with pytest.raises(NoLog):
        write.record(other, actor="planner", event=done, gates=[ran.append],
                     act=ran.append)

    assert ran == [] and not other.round_log.exists()


@pytest.mark.parametrize("which", ["round", "project"])
def test_an_unreadable_log_stops_the_verb_before_its_gates(where, which):
    path = where.round_log if which == "round" else where.project_log
    path.write_bytes(b"not an event\n")
    ran = []

    with pytest.raises(Unreadable) as caught:
        write.record(where, actor="planner", event=done, gates=[ran.append],
                     act=ran.append)

    assert caught.value.file == path and caught.value.line == 1
    assert ran == [] and path.read_bytes() == b"not an event\n"


# -- a refusal ---------------------------------------------------------------------------

def test_a_refused_write_names_every_unmet_gate_and_the_command_that_meets_each(where):
    ran = []
    first = Unmet("question Q1 is open", "moxi rounds rule proj-a/a-round Q1 --record PTR")
    second = Unmet("the build has no check", "moxi rounds check proj-a/a-round B1")
    third = Unmet("the worktree is dirty", "git -C <worktree> status")

    with pytest.raises(Refused) as caught:
        write.record(where, actor="planner", event=done, act=ran.append,
                     on_append=[ran.append],
                     gates=[lambda view: first, lambda view: None,
                            lambda view: [second, third], lambda view: []])

    error = caught.value
    assert error.unmet == (first, second, third)
    for unmet in (first, second, third):
        assert unmet.reason in str(error) and unmet.fix in str(error)
    assert isinstance(error, RoundsError) and error.exit_code == exits.REFUSED == 3
    assert ran == []


def test_a_refused_write_leaves_both_logs_byte_identical(where):
    log.append(where.project_log, "ruling.shown", SHOWN, actor="planner", at=AT)
    before = where.round_log.read_bytes(), where.project_log.read_bytes()
    listed = sorted(where.folder.rglob("*"))

    with pytest.raises(Refused):
        write.record(where, actor="planner", with_project=True,
                     event=lambda view, result: [done(view, result), shown(view, result)],
                     gates=[lambda view: Unmet("it is not time", "moxi rounds next")])

    assert (where.round_log.read_bytes(), where.project_log.read_bytes()) == before
    assert sorted(where.folder.rglob("*")) == listed


def test_a_refusal_frees_the_lock_for_the_next_verb(where):
    with pytest.raises(Refused):
        write.record(where, actor="planner", event=done,
                     gates=[lambda view: Unmet("it is not time", "moxi rounds next")])

    written = write.record(where, actor="planner", event=done, wait_s=0)

    assert written.events[0]["n"] == 2


def test_an_unmet_gate_always_carries_its_command():
    with pytest.raises(ValueError, match="the command"):
        Unmet("it is not time", "")
    with pytest.raises(ValueError, match="at least one"):
        Refused([])


def test_a_gate_that_returns_something_else_is_a_mistake_in_the_verb(where):
    before = where.round_log.read_bytes()

    with pytest.raises(TypeError, match="Unmet"):
        write.record(where, actor="planner", event=done, gates=[lambda view: ["no"]])

    assert where.round_log.read_bytes() == before


# -- events that can't be written --------------------------------------------------------

@pytest.mark.parametrize("entries, problem", [
    ([write.Entry("finish.done", DONE), write.Entry("no.such", {})], "not an event type"),
    ([write.Entry("finish.done", DONE), write.Entry("ruling.shown", SHOWN,
                                                    log=write.PROJECT)], "with_project"),
    ([write.Entry("finish.done", DONE), write.Entry("finish.done", DONE, log="other")],
     "not a log"),
])
def test_one_bad_event_stops_every_event_of_the_verb(where, entries, problem):
    before = where.round_log.read_bytes()

    with pytest.raises(ValueError, match=problem):
        write.record(where, actor="planner", event=lambda view, result: entries)

    assert where.round_log.read_bytes() == before and not where.project_log.exists()


def test_a_write_on_a_project_alone_has_no_rounds_log_to_append_to(where):
    with pytest.raises(ValueError, match="project alone"):
        write.record(write.Where("proj-a", where.folder), actor="planner", event=done)


# -- a short act -------------------------------------------------------------------------

def test_a_short_act_runs_with_the_lock_held(where):
    held = []

    write.record(where, actor="planner", event=done, act=lambda view: held.append(
        held_elsewhere(write.round_lock("proj-a", "a-round"))))

    assert held == [True]
    assert not held_elsewhere(write.round_lock("proj-a", "a-round"))


def test_an_act_that_fails_appends_nothing_and_frees_the_lock(where):
    before = where.round_log.read_bytes()

    def act(view):
        raise OSError("the disk is full")

    with pytest.raises(OSError, match="disk is full"):
        write.record(where, actor="planner", event=done, act=act)

    assert where.round_log.read_bytes() == before
    with files.lock(write.round_lock("proj-a", "a-round"), timeout_s=0):
        pass


# -- a long act --------------------------------------------------------------------------

def test_a_long_act_runs_with_no_lock_held(where):
    held = []

    def act(view):
        held.append(held_elsewhere(write.round_lock("proj-a", "a-round")))
        held.append(held_elsewhere(write.project_lock("proj-a")))

    write.record(where, actor="coder", event=done, act=act, long_act=True,
                 with_project=True)

    assert held == [False, False]


def test_while_a_long_act_runs_a_second_write_on_the_round_completes(where):
    def act(view):
        second = write.record(where, actor="planner", wait_s=0,
                              event=lambda view, result: write.Entry("ruling.shown", SHOWN))
        return second.events[0]["n"]

    def fact(view, result):
        # The log is read again after the act, so the other verb's event is in view.
        assert [event["type"] for event in view.events] == ["round.opened", "ruling.shown"]
        return write.Entry("check.ran", {"stage": "B1", "exit_code": 0})

    written = write.record(where, actor="coder", event=fact, act=act, long_act=True)

    assert written.result == 2
    assert [(event["n"], event["type"], event["actor"])
            for event in log.read(where.round_log)] == [
        (1, "round.opened", "planner"), (2, "ruling.shown", "planner"),
        (3, "check.ran", "coder")]


def test_a_long_acts_gates_are_met_before_it_runs_and_not_asked_again(where):
    asked = []

    def gate(view):
        asked.append(len(view.events))

    def act(view):
        write.record(where, actor="planner", event=done)

    write.record(where, actor="coder", event=done, gates=[gate], act=act, long_act=True)

    assert asked == [1]
    assert types(where.round_log) == ["round.opened", "finish.done", "finish.done"]


def test_a_refused_long_act_never_runs(where):
    ran = []
    before = where.round_log.read_bytes()

    with pytest.raises(Refused):
        write.record(where, actor="coder", event=done, act=ran.append, long_act=True,
                     gates=[lambda view: Unmet("it is not time", "moxi rounds next")])

    assert ran == [] and where.round_log.read_bytes() == before


def test_a_long_acts_facts_are_read_again_after_it(where):
    tips = iter(["1" * 40, "2" * 40])
    seen = []

    def event(view, result):
        seen.append(view.facts)

    write.record(where, actor="coder", event=event, act=lambda view: seen.append(view.facts),
                 facts=lambda where: next(tips), long_act=True)

    assert seen == ["1" * 40, "2" * 40]


def test_a_long_acts_event_may_refuse_on_what_the_second_read_found(where):
    def act(view):
        write.record(where, actor="planner", event=done)

    def event(view, result):
        raise Refused([Unmet("the build moved while the command ran",
                             "moxi rounds check proj-a/a-round B1")])

    with pytest.raises(Refused):
        write.record(where, actor="coder", event=event, act=act, long_act=True)

    assert types(where.round_log) == ["round.opened", "finish.done"]
    with files.lock(write.round_lock("proj-a", "a-round"), timeout_s=0):
        pass


def test_a_long_act_is_named_as_one(where):
    with pytest.raises(ValueError, match="needs the act"):
        write.record(where, actor="coder", event=done, long_act=True)


# -- two writers -------------------------------------------------------------------------

def test_two_writers_on_one_round_number_their_events_with_no_gap(where):
    count = 25
    writers = [subprocess.Popen(
        [sys.executable, "-c", WRITER, str(where.folder), actor, str(count)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        for actor in ("coder", "critic")]
    for writer in writers:
        assert writer.stdout.readline().strip() == "ready"
    for writer in writers:  # both are loaded before either starts
        writer.stdin.write("go\n")
        writer.stdin.flush()
    for writer in writers:
        writer.communicate(timeout=120)
        assert writer.returncode == 0

    events = log.read(where.round_log)  # a gap or a repeat would make this unreadable
    assert [event["n"] for event in events] == list(range(1, 2 * count + 2))
    by_actor = [event["actor"] for event in events[1:]]
    assert by_actor.count("coder") == by_actor.count("critic") == count
    for actor in ("coder", "critic"):
        assert [event["stage"] for event in events[1:] if event["actor"] == actor] == [
            f"B{number + 1}" for number in range(count)]


# -- after the append --------------------------------------------------------------------

def test_the_callbacks_after_an_append_get_what_was_written_with_the_lock_held(where):
    seen = []

    def on_append(written):
        seen.append((written, held_elsewhere(write.round_lock("proj-a", "a-round"))))

    written = write.record(where, actor="planner", event=done, on_append=[on_append])

    assert seen == [(written, True)]
    assert seen[0][0].where == where
    assert [event["type"] for event in seen[0][0].view.events] == ["round.opened"]
    assert [event["type"] for event in seen[0][0].round_events] == ["round.opened",
                                                                    "finish.done"]


def test_a_failure_after_the_append_keeps_the_event(where):
    ran = []

    def render(written):
        raise OSError("the note can't be written")

    def index(written):
        ran.append("index")

    written = write.record(where, actor="planner", event=done, on_append=[render, index])

    assert types(where.round_log) == ["round.opened", "finish.done"]
    assert ran == ["index"]
    assert [(callback, str(error)) for callback, error in written.failed] == [
        (render, "the note can't be written")]
    assert written.events[0]["n"] == 2
    # The lock is free, so the next verb writes, and its callbacks run again.
    again = write.record(where, actor="planner", event=done, wait_s=0, on_append=[index])
    assert again.failed == () and ran == ["index", "index"]


def test_every_write_calls_the_modules_own_callbacks_unless_given_others(
        where, monkeypatch):
    seen = []
    monkeypatch.setattr(write, "ON_APPEND", (seen.append,))

    written = write.record(where, actor="planner", event=done)
    write.record(where, actor="planner", event=done, on_append=[])

    assert seen == [written]


def test_the_callbacks_run_after_a_long_acts_append_too(where):
    seen = []

    write.record(where, actor="coder", event=done, act=lambda view: None, long_act=True,
                 on_append=[seen.append])

    assert [event["n"] for event in seen[0].events] == [2]


def test_no_callback_runs_when_the_verb_is_refused_or_its_act_fails(where):
    seen = []

    with pytest.raises(Refused):
        write.record(where, actor="planner", event=done, on_append=[seen.append],
                     gates=[lambda view: Unmet("it is not time", "moxi rounds next")])
    with pytest.raises(ZeroDivisionError):
        write.record(where, actor="planner", event=done, on_append=[seen.append],
                     act=lambda view: 1 / 0)

    assert seen == []
