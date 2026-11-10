"""The log file: what a read answers, what an append writes, and what makes a log
unreadable."""

from __future__ import annotations

import json

import pytest

from moxi import exit as exits
from moxi import files
from moxi_rounds import log
from moxi_rounds.errors import NoLog, RoundsError, TooNew, Unreadable

OPENED = {"description": "a placeholder round", "flow": "fix",
          "steps": [{"step": "build", "by": "builder"}]}
AT = "2026-01-02T03:04:05Z"


@pytest.fixture
def path(tmp_path):
    """A round's log in a folder that exists; the file does not."""
    folder = tmp_path / "rounds" / "a-round"
    folder.mkdir(parents=True)
    return log.round_log(folder)


def line(number, **changes):
    """One well-formed log line numbered `number`, with `changes` over its keys; a key set
    to None is left out."""
    event = {"v": 1, "n": number, "at": AT, "actor": "planner", "type": "finish.done",
             "stage": "B1", "tip": "0" * 40, **changes}
    event = {key: value for key, value in event.items() if value is not None}
    return json.dumps(event).encode() + b"\n"


def write(path, *lines):
    path.write_bytes(b"".join(lines))
    return path


# -- names -------------------------------------------------------------------------------

def test_a_rounds_log_and_a_projects_log_have_their_own_names(tmp_path):
    assert log.round_log(tmp_path / "a-round") == tmp_path / "a-round" / "round.jsonl"
    assert log.project_log(tmp_path) == tmp_path / "project.jsonl"


def test_a_stamp_is_utc_to_the_second():
    assert log.stamp(0) == "1970-01-01T00:00:00Z"
    assert len(log.stamp()) == len(AT) and log.stamp().endswith("Z")


# -- append ------------------------------------------------------------------------------

def test_an_append_writes_the_envelope_then_the_fields_as_one_line(path):
    event = log.append(path, "round.opened", OPENED, actor="planner", at=AT)

    assert event == {"v": 1, "n": 1, "at": AT, "actor": "planner", "type": "round.opened",
                     **OPENED}
    assert list(event)[:5] == ["v", "n", "at", "actor", "type"]
    raw = path.read_bytes()
    assert raw.endswith(b"\n") and raw.count(b"\n") == 1
    assert json.loads(raw) == event


def test_appends_number_from_one_with_no_gap(path):
    log.append(path, "round.opened", OPENED, actor="planner")
    log.append(path, "finish.done", {"stage": "B1", "tip": "0" * 40}, actor="coder")
    third = log.append(path, "ruling.shown", {"ids": ["C1"], "record": "comms:12"},
                       actor="planner")

    assert third["n"] == 3
    assert [event["n"] for event in log.read(path)] == [1, 2, 3]
    assert [event["actor"] for event in log.read(path)] == ["planner", "coder", "planner"]


def test_an_append_with_no_time_given_is_stamped_now(path):
    event = log.append(path, "round.opened", OPENED, actor="planner")

    assert log.stamp(0) < event["at"] <= log.stamp()


def test_an_append_leaves_the_earlier_bytes_as_they_were(path):
    log.append(path, "round.opened", OPENED, actor="planner", at=AT)
    log.append(path, "finish.done", {"stage": "B1", "tip": "0" * 40}, actor="coder")
    before = path.read_bytes()

    log.append(path, "finish.done", {"stage": "B2", "tip": "1" * 40}, actor="coder")

    after = path.read_bytes()
    assert after[:len(before)] == before
    assert after[len(before):].count(b"\n") == 1


def test_an_append_starts_an_empty_file_at_one(path):
    path.write_bytes(b"")

    assert log.append(path, "round.opened", OPENED, actor="planner")["n"] == 1


def test_a_projects_log_is_appended_and_read_the_same_way(tmp_path):
    path = log.project_log(tmp_path)

    log.append(path, "ruling.recorded", {"id": "S1", "text": "a placeholder ruling",
                                         "record": "comms:12"}, actor="planner")

    assert [event["type"] for event in log.read(path)] == ["ruling.recorded"]


def test_an_event_the_check_refuses_is_not_written(path):
    with pytest.raises(ValueError, match="not an event type"):
        log.append(path, "round.started", {}, actor="planner")
    with pytest.raises(ValueError, match="needs description"):
        log.append(path, "round.opened", {"flow": "fix", "steps": []}, actor="planner")

    assert not path.exists()


def test_an_append_with_no_actor_is_refused(path):
    with pytest.raises(ValueError, match="who ran the verb"):
        log.append(path, "round.opened", OPENED, actor="")

    assert not path.exists()


def test_an_unreadable_log_is_not_appended_to(path):
    write(path, line(1), b"{not json\n")
    before = path.read_bytes()

    with pytest.raises(Unreadable):
        log.append(path, "finish.done", {"stage": "B1", "tip": "0" * 40}, actor="coder")

    assert path.read_bytes() == before


# -- one file of any size ----------------------------------------------------------------

def test_the_cores_append_with_no_size_limit_never_moves_the_file_aside(tmp_path):
    folder = tmp_path / "logs"
    path = folder / "plain.jsonl"
    long_line = {"text": "x" * 1_200_000}

    for _ in range(3):
        files.append_record(path, long_line, max_bytes=None, durable=True)

    assert sorted(entry.name for entry in folder.iterdir()) == ["plain.jsonl"]
    assert path.stat().st_size > 3_600_000
    assert path.read_bytes().count(b"\n") == 3
    # The same helper with its default limit refuses that line.
    with pytest.raises(ValueError, match="longer than the log's cap"):
        files.append_record(folder / "capped.jsonl", long_line)


def test_a_log_past_one_megabyte_is_still_one_file(path):
    log.append(path, "round.opened", OPENED, actor="planner")
    for number in range(6):
        log.append(path, "ruling.recorded",
                   {"id": f"C{number + 1}", "text": "x" * 300_000, "record": "comms:12"},
                   actor="planner")
    first_megabyte = path.read_bytes()[:1_000_000]

    # One line that is itself past the size the core's append rolls at by default.
    last = log.append(path, "ruling.recorded",
                      {"id": "C7", "text": "y" * 1_200_000, "record": "comms:12"},
                      actor="planner")

    assert last["n"] == 8
    assert sorted(entry.name for entry in path.parent.iterdir()) == ["round.jsonl"]
    assert path.stat().st_size > 3_000_000
    assert path.read_bytes()[:1_000_000] == first_megabyte
    found = log.read(path)
    assert [event["n"] for event in found] == list(range(1, 9))
    assert found[-1] == last


# -- read --------------------------------------------------------------------------------

def test_a_log_that_does_not_exist_is_its_own_answer(path):
    assert not log.exists(path)
    with pytest.raises(NoLog) as caught:
        log.read(path)

    assert caught.value.file == path
    assert not isinstance(caught.value, Unreadable)
    assert caught.value.exit_code == exits.REFUSED
    assert str(path) in str(caught.value)


def test_an_empty_file_reads_as_no_events(path):
    path.write_bytes(b"")

    assert log.exists(path)
    assert log.read(path) == []


def test_a_read_gives_each_line_as_written(path):
    write(path, line(1), line(2, stage="B2"))

    assert log.read(path) == [json.loads(line(1)), json.loads(line(2, stage="B2"))]


def test_a_type_this_code_does_not_know_is_still_read(path):
    write(path, line(1), line(2, type="round.archived"))

    assert log.read(path)[1]["type"] == "round.archived"


def unreadable(path):
    with pytest.raises(Unreadable) as caught:
        log.read(path)
    return caught.value


def test_a_truncated_last_line_is_refused_with_its_line(path):
    write(path, line(1), line(2), line(3)[:25])

    error = unreadable(path)

    assert (error.file, error.line) == (path, 3)
    assert "cut short" in error.problem


def test_a_last_line_that_only_lost_its_newline_is_refused_too(path):
    write(path, line(1), line(2)[:-1])

    assert unreadable(path).line == 2


@pytest.mark.parametrize("bad", [b"{not json\n", b"\n", b"[1, 2]\n", b'"text"\n',
                                 b"\xff\xfe\n"])
def test_a_line_that_does_not_parse_is_refused_with_its_line(path, bad):
    write(path, line(1), bad, line(3))

    error = unreadable(path)

    assert (error.file, error.line) == (path, 2)


def test_a_gap_in_n_is_refused_with_its_line(path):
    write(path, line(1), line(2), line(4))

    error = unreadable(path)

    assert error.line == 3
    assert "n 4 where 3 comes next" in error.problem


def test_a_repeat_in_n_is_refused_with_its_line(path):
    write(path, line(1), line(2), line(2), line(3))

    error = unreadable(path)

    assert error.line == 3
    assert "n 2 where 3 comes next" in error.problem


def test_a_first_line_not_numbered_one_is_refused(path):
    write(path, line(2), line(3))

    assert unreadable(path).line == 1


@pytest.mark.parametrize("changes", [
    {"n": None}, {"n": "2"}, {"n": 2.0}, {"n": True}, {"v": None}, {"v": "1"},
    {"at": None}, {"at": 5}, {"actor": None}, {"actor": ""}, {"type": None},
    {"type": 7}])
def test_a_line_without_a_whole_envelope_is_refused_with_its_line(path, changes):
    write(path, line(1), line(2, **changes))

    assert unreadable(path).line == 2


def test_the_first_bad_line_is_the_one_named(path):
    write(path, line(1), b"{not json\n", line(5), line(6)[:10])

    assert unreadable(path).line == 2


def test_an_unreadable_log_says_the_file_the_line_and_the_way_back(path):
    write(path, line(1), line(3))

    error = unreadable(path)

    assert isinstance(error, RoundsError)
    assert error.exit_code == exits.REFUSED
    assert f"{path} line 2" in error.reason
    assert "restore one machine's copy of this file whole from git" in error.fix
    assert "run again the verbs the other machine ran" in error.fix
    assert error.fix in str(error)


def test_a_line_in_a_later_log_format_is_refused_as_too_new(path):
    write(path, line(1), line(2, v=2))

    error = unreadable(path)

    assert isinstance(error, TooNew)
    assert error.line == 2
    assert "upgrade moxi-rounds" in error.fix


def test_bytes_already_read_parse_to_the_events_a_read_of_the_file_gives(tmp_path):
    path = tmp_path / "round.jsonl"
    log.append(path, "round.opened",
               {"description": "a placeholder round", "flow": "fix", "steps": []},
               actor="planner")
    log.append(path, "finish.done", {"stage": "B1", "tip": "0" * 40}, actor="planner")
    data = path.read_bytes()
    assert log.parse(path, data) == log.read(path)
    assert log.parse(path, b"") == []
    with pytest.raises(Unreadable) as caught:
        log.parse(path, data[:-1])
    assert (caught.value.file, caught.value.line) == (path, 2)
