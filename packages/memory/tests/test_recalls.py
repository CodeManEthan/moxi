"""The subconscious's log: lines written in place under the session's lock, `off` lines
written once per reason, and `moxi memory subconscious list`."""

import json
import threading

import pytest

from moxi_memory import cli, recalls

SESSION = "0190b000-0000-7000-8000-000000000001"
OTHER = "0190b000-0000-7000-8000-000000000002"


def memory(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_a_line_is_written_in_place_and_new_ones_after_it():
    with recalls.session_lock(SESSION):
        recalls.put_line(SESSION, {"recall": "r1", "state": recalls.OPEN}, now=10.0)
        recalls.put_line(SESSION, {"recall": "r2", "state": recalls.OPEN}, now=11.0)
        recalls.put_line(SESSION, {"recall": "r1", "state": recalls.BACK, "at": 10.0},
                         now=12.0)
    lines = recalls.read_lines(SESSION)
    assert [(line["recall"], line["state"]) for line in lines] == [("r1", "back"),
                                                                   ("r2", "open")]
    assert lines[0]["at"] == 10.0 and lines[0]["updated"] == 12.0
    assert lines[0]["session"] == SESSION
    assert recalls.find_line(SESSION, "r2")["state"] == "open"
    assert recalls.find_line(SESSION, "r3") is None


def test_a_line_in_no_known_state_is_refused():
    with recalls.session_lock(SESSION), pytest.raises(ValueError):
        recalls.put_line(SESSION, {"recall": "r1", "state": "sending"})
    assert recalls.read_lines(SESSION) == []


def test_a_session_id_that_cant_name_a_file_is_refused():
    for bad in ("../x", "", "a/b", ".hidden"):
        assert not recalls.is_safe(bad)
        with pytest.raises(ValueError):
            recalls.log_path(bad)


def test_an_off_line_is_written_once_per_reason():
    assert recalls.note_off(SESSION, "no turns fork", now=1.0)
    assert not recalls.note_off(SESSION, "no turns fork", now=2.0)
    assert recalls.note_off(SESSION, "another reason", now=3.0)
    with recalls.session_lock(SESSION):
        recalls.put_line(SESSION, {"recall": "r1", "state": recalls.BACK}, now=4.0)
    # a recall ran since, so the same reason is logged again
    assert recalls.note_off(SESSION, "another reason", now=5.0)
    states = [(line["state"], line.get("reason")) for line in recalls.read_lines(SESSION)]
    assert states == [("off", "no turns fork"), ("off", "another reason"), ("back", None),
                      ("off", "another reason")]


def test_writers_in_threads_of_one_process_lose_no_line():
    def write(n):
        with recalls.session_lock(SESSION):
            recalls.put_line(SESSION, {"recall": f"r{n}", "state": recalls.OPEN})

    threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(line["recall"] for line in recalls.read_lines(SESSION)) == sorted(
        f"r{n}" for n in range(8))


def test_a_line_that_does_not_read_is_skipped():
    path = recalls.log_path(SESSION)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"recall": "r1", "state": "back"}\nnot json\n[1]\n')
    assert [line["recall"] for line in recalls.read_lines(SESSION)] == ["r1"]


def test_list_with_nothing_logged(capsys):
    code, out, _ = memory(capsys, "subconscious", "list")
    assert code == 0 and out == "no recalls logged\n"


def _two_sessions():
    with recalls.session_lock(SESSION):
        recalls.put_line(SESSION, {
            "recall": "r1", "state": recalls.BACK, "start": recalls.PROMPT,
            "head": recalls.head("why did the\nbuild fail"), "route": "subagent",
            "rounds": [{"round": 1, "text": "Recalled from past sessions:\n- a point",
                        "at": 20.0},
                       {"round": 2, "lost": "gone", "error": "gone: the bound passed"}],
            "hurried": True}, now=20.0)
    recalls.note_off(OTHER, "no turns fork in this session", now=10.0)


def test_list_shows_every_session_oldest_first(capsys):
    _two_sessions()
    code, out, _ = memory(capsys, "subconscious", "list")
    assert code == 0
    first, second = out.splitlines()
    assert first.startswith("-  off  ") and first.endswith(
        f"session {OTHER}  no turns fork in this session")
    assert second.startswith("r1  back  ")
    assert f"session {SESSION}  prompt  \"why did the build fail\"  subagent  rounds 2  hurried" in second


def test_list_for_one_session_shows_each_rounds_answer(capsys):
    _two_sessions()
    code, out, _ = memory(capsys, "subconscious", "list", "--session", SESSION)
    assert code == 0
    lines = out.splitlines()
    assert len(lines) == 3 and lines[0].startswith("r1  back")
    assert lines[1].startswith("    round 1  ") and lines[1].endswith(
        "Recalled from past sessions: - a point")
    assert lines[2] == "    round 2  -  (no answer: gone)"


def test_list_json_prints_the_lines_as_stored(capsys):
    _two_sessions()
    code, out, _ = memory(capsys, "subconscious", "list", "--json")
    assert code == 0
    lines = json.loads(out)
    assert [line["state"] for line in lines] == ["off", "back"]
    assert lines[1]["rounds"][0]["text"].endswith("- a point")


def test_list_refuses_a_session_that_is_not_an_id(capsys):
    code, out, err = memory(capsys, "subconscious", "list", "--session", "../x")
    assert code == 2 and out == "" and "is not a session id" in err
