"""The launch log's rules for a resumed session and for the base environment: a resume
keeps the id it resumes beside the launch that started it, `resolve` finds the newest
launch holding an id (skipping one that failed before any claim), and every launch records
its child's base names."""

from __future__ import annotations

import pytest

from moxi_launcher import store
from moxi_launcher.compose import common

SID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"


@pytest.fixture
def conn():
    c = store.connect()
    yield c
    c.close()


def put(conn, rid, *, sid=SID, at, harness="claude", **more):
    return store.insert(conn, request_id=rid, actor="ann", harness=harness,
                        target="slot", cwd="/w", values={}, argv=["claude"], env={},
                        posture="", session_id=sid, at=at, **more)


def test_a_resume_keeps_the_id_and_resolve_finds_the_new_launch(conn):
    put(conn, "a", at="2026-10-01T00:00:00.000000Z")
    store.claim(conn, "a", harness="claude", session_id=SID, endpoint="999999@1.0")
    put(conn, "b", resume_of="a", at="2026-10-02T00:00:00.000000Z")
    assert store.session_ids(conn, "b") == [(SID, "resume")]
    assert store.session_ids(conn, "a") == [(SID, "minted")]
    assert store.resolve(conn, SID).request_id == "b"
    # The ready recorder's resume source for the same pair changes nothing.
    assert store.learn_session(conn, "b", SID, "resume") is False


def test_an_id_another_launch_holds_is_still_taken_without_a_resume(conn):
    put(conn, "a", at="2026-10-01T00:00:00.000000Z")
    with pytest.raises(store.SessionTaken):
        put(conn, "c", at="2026-10-02T00:00:00.000000Z")
    put(conn, "x", sid="11111111-2222-4333-8444-555555555555",
        at="2026-10-02T00:00:00.000000Z")
    with pytest.raises(store.SessionTaken):
        put(conn, "d", resume_of="x", at="2026-10-03T00:00:00.000000Z")


def test_resolve_skips_a_resume_that_failed_before_any_claim(conn):
    put(conn, "a", at="2026-10-01T00:00:00.000000Z")
    put(conn, "b", resume_of="a", at="2026-10-02T00:00:00.000000Z")
    store.set_state(conn, "b", "failed", reason="spawn-failed")
    assert store.resolve(conn, SID).request_id == "a"
    # Claimed before it failed: it counts.
    put(conn, "c", resume_of="a", at="2026-10-03T00:00:00.000000Z")
    store.claim(conn, "c", harness="claude", session_id=SID, endpoint="999998@1.0")
    store.set_state(conn, "c", "failed", reason="no-ready")
    assert store.resolve(conn, SID).request_id == "c"


def test_a_lone_launch_that_failed_still_resolves(conn):
    put(conn, "a", at="2026-10-01T00:00:00.000000Z")
    store.set_state(conn, "a", "failed", reason="spawn-failed")
    assert store.resolve(conn, SID).request_id == "a"


def test_a_codex_resume_records_its_thread(conn):
    put(conn, "a", sid=None, harness="codex", at="2026-10-01T00:00:00.000000Z")
    store.learn_session(conn, "a", "thread-1", "sender")
    put(conn, "b", sid="thread-1", harness="codex", resume_of="a",
        at="2026-10-02T00:00:00.000000Z")
    assert store.resolve(conn, "thread-1").request_id == "b"


def test_a_resume_launch_learns_a_resumed_id_another_launch_holds(conn):
    put(conn, "a", at="2026-10-01T00:00:00.000000Z")
    put(conn, "b", sid=None, harness="codex", resume_of="a",
        at="2026-10-02T00:00:00.000000Z")
    assert store.learn_session(conn, "b", SID, "resume") is True
    assert store.learn_session(conn, "b", SID, "resume") is False
    put(conn, "c", sid=None, harness="codex", at="2026-10-03T00:00:00.000000Z")
    assert store.learn_session(conn, "c", SID, "resume") is False


def test_the_base_environment_is_recorded_and_holds_no_credential(conn):
    child = {"HOME": "/h", "PATH": "/bin", "LC_ALL": "C", "XDG_RUNTIME_DIR": "/run/u",
             "TZ": "UTC", "CLAUDE_CODE_OAUTH_TOKEN": "s3cr3t", "API_KEY": "k",
             "MOXI_LAUNCHER_REQUEST": "r"}
    base = common.base_env(child, windows=False)
    assert base == {"HOME": "/h", "PATH": "/bin", "LC_ALL": "C",
                    "XDG_RUNTIME_DIR": "/run/u", "TZ": "UTC",
                    "CLAUDE_CODE_OAUTH_TOKEN": None}
    put(conn, "a", at="2026-10-01T00:00:00.000000Z", base_env=base)
    assert store.get(conn, "a").base_env == base
    assert "s3cr3t" not in store.get(conn, "a").base_env_json
