"""The model pin: a row's model kept, the transcript's newest request read through
sessions for Claude and Codex, and each reason the launch runs the default instead."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from moxi_sessions.testing.claude import ClaudeTranscript
from moxi_sessions.testing.codex import CodexRollout

from moxi_launcher import joiner, pin, store

SID = "0190b000-0000-7000-8000-00000000c1a0"
THREAD = "0190c000-0000-7000-8000-0000000c0de1"


@pytest.fixture
def conn(tmp_path):
    conn = store.connect(tmp_path / "launcher.db")
    yield conn
    conn.close()


@pytest.fixture
def sessions():
    found = joiner.sessions_api()
    if found is None:
        pytest.skip("sessions is not installed here")
    return found


def row_of(conn, tmp_path, *, harness="claude", values=None, session_id=SID):
    base_env = ({"CLAUDE_CONFIG_DIR": str(tmp_path / "cfg"), "HOME": str(tmp_path)}
                if harness == "claude" else {"CODEX_HOME": str(tmp_path / "codex")})
    store.insert(conn, request_id="r1", by="ann", harness=harness, target="slot",
                 cwd="/work", values=values or {}, argv=[harness], env={},
                 posture="operator", session_id=session_id, slot="3", base_env=base_env)
    return store.get(conn, "r1")


def claude_transcript(tmp_path) -> ClaudeTranscript:
    return ClaudeTranscript(tmp_path / "cfg" / "projects", session_id=SID)


class Sessions:
    """sessions' `find` and `last_request` over hits and requests made up here."""

    def __init__(self, hits=(), requests=(), *, find_error=None, read_error=None):
        self.hits = [SimpleNamespace(source=f"s{n}", compressed=compressed)
                     for n, compressed in enumerate(hits)]
        self.requests = list(requests)
        self.find_error, self.read_error = find_error, read_error
        self.asked = []

    def find(self, harness, session_id, *, root=None):
        self.asked.append((harness, session_id))
        if self.find_error is not None:
            raise self.find_error
        return SimpleNamespace(hits=tuple(self.hits))

    def last_request(self, source):
        if self.read_error is not None:
            raise self.read_error
        return SimpleNamespace(request=self.requests[int(source[1:])])


def request(model=None, response_model=None, time=None):
    return SimpleNamespace(model=model, response_model=response_model, time=time)


def test_a_row_that_names_a_model_keeps_it_and_reads_nothing(conn, tmp_path):
    row = row_of(conn, tmp_path, values={"model": "opus"})
    seen = Sessions()
    assert pin.choose(row, SID, sessions=seen) == pin.Pin("opus", "row")
    assert seen.asked == []


def test_a_claude_transcript_pins_the_request_before_a_synthetic_one(conn, tmp_path,
                                                                      sessions):
    t = claude_transcript(tmp_path)
    t.user("go")
    t.assistant("working", model="claude-fable-5-2-20261101")
    t.synthetic()
    t.write()
    got = pin.choose(row_of(conn, tmp_path), SID, sessions=sessions, env={})
    assert got == pin.Pin("claude-fable-5-2-20261101", "pinned",
                          "claude-fable-5-2-20261101")


def test_a_codex_rollout_pins_its_model(conn, tmp_path, sessions):
    rollout = CodexRollout(tmp_path / "codex" / "sessions", thread_id=THREAD)
    rollout.turn("gpt-6-codex")
    rollout.request()
    rollout.write()
    got = pin.choose(row_of(conn, tmp_path, harness="codex", session_id=THREAD), THREAD,
                     sessions=sessions, env={})
    assert got.model == "gpt-6-codex" and got.how == "pinned"


def test_the_newest_of_several_codex_rollouts_wins_not_the_first_found(conn, tmp_path,
                                                                       sessions):
    root = tmp_path / "codex" / "sessions"
    old = CodexRollout(root, thread_id=THREAD)
    old.turn("gpt-5-codex")
    old.request()
    old.write()
    new = CodexRollout(root, thread_id=THREAD, start=old.clock + 86400 * 3)
    new.turn("gpt-6-codex")
    new.request()
    new.write()
    got = pin.choose(row_of(conn, tmp_path, harness="codex", session_id=THREAD), THREAD,
                     sessions=sessions, env={})
    assert got.model == "gpt-6-codex"


def test_a_request_with_a_time_outranks_one_without_and_a_tie_goes_to_the_later_hit(
        conn, tmp_path):
    row = row_of(conn, tmp_path)
    timed = Sessions(hits=(False, False), requests=(request("a", time=5.0), request("b")))
    assert pin.choose(row, SID, sessions=timed).model == "a"
    tie = Sessions(hits=(False, False), requests=(request("a", time=5.0),
                                                   request("b", time=5.0)))
    assert pin.choose(row, SID, sessions=tie).model == "b"


def test_the_response_model_stands_in_for_a_request_naming_none(conn, tmp_path):
    seen = Sessions(hits=(False,), requests=(request(response_model="claude-x-1"),))
    assert pin.choose(row_of(conn, tmp_path), SID, sessions=seen).model == "claude-x-1"


def test_no_sessions_runs_the_default(conn, tmp_path):
    assert pin.choose(row_of(conn, tmp_path), SID, sessions=None) == pin.Pin(
        None, "default:no-sessions")


@pytest.mark.parametrize("seen", [
    Sessions(find_error=OSError("no root")),
    Sessions(hits=()),
    Sessions(hits=(True,), requests=(request("a"),)),
    Sessions(hits=(False,), requests=(request("a"),), read_error=OSError("gone")),
], ids=["find-failed", "not-found", "compressed-only", "read-failed"])
def test_a_transcript_that_cant_be_read_runs_the_default(conn, tmp_path, seen):
    got = pin.choose(row_of(conn, tmp_path), SID, sessions=seen)
    assert got == pin.Pin(None, "default:transcript-unreadable")


def test_a_missing_claude_file_is_unreadable(conn, tmp_path, sessions):
    got = pin.choose(row_of(conn, tmp_path), SID, sessions=sessions, env={})
    assert got.how == "default:transcript-unreadable"


def test_a_transcript_with_no_request_naming_a_model_runs_the_default(conn, tmp_path,
                                                                     sessions):
    t = claude_transcript(tmp_path)
    t.user("go")
    t.write()
    got = pin.choose(row_of(conn, tmp_path), SID, sessions=sessions, env={})
    assert got == pin.Pin(None, "default:no-model-line")
    nameless = Sessions(hits=(False,), requests=(request(time=1.0),))
    assert pin.choose(row_of_again(conn), SID, sessions=nameless).how == (
        "default:no-model-line")


def row_of_again(conn):
    return store.get(conn, "r1")


@pytest.mark.parametrize("raw", ["bad id", "-leading-dash", "x" * 129, "a;rm -rf"])
def test_an_id_not_of_a_models_shape_runs_the_default(conn, tmp_path, raw):
    seen = Sessions(hits=(False,), requests=(request(raw, time=1.0),))
    got = pin.choose(row_of(conn, tmp_path), SID, sessions=seen)
    assert got == pin.Pin(None, "default:bad-id", raw)


@pytest.mark.parametrize("raw", ["claude-opus-5-5", "claude-haiku-4-5-20251001",
                                 "gpt-6-codex", "us.anthropic.claude-x:1", "org/model@v2"])
def test_an_id_of_a_models_shape_is_pinned_as_read(raw):
    assert pin.shaped(raw)


def test_a_pin_goes_to_an_event_and_back():
    found = pin.Pin("claude-opus-5-5", "pinned", "claude-opus-5-5")
    assert pin.Pin.of(found.detail()) == found
    assert pin.Pin.of({"model": None, "how": "default:bad-id", "read": "x y"}) == pin.Pin(
        None, "default:bad-id", "x y")
