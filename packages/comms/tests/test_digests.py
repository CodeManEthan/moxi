"""The daemon's digest methods: `open_digest`, `injected` naming a digest, and a turn's
start marking it, through a real socket, on both record backends."""

from __future__ import annotations

import pytest
from conftest import projects_root, store_rows, wait_until, warm_session

from moxi_comms import client as client_mod, wire

PLANNER = "planner@proj-a"


def planner(bind):
    """A bound planner with a warm session behind it, so the cache gate lets mail
    through live."""
    return bind(name="planner", scope="proj-a", session_id=warm_session(),
                projects_root=str(projects_root()))


def handed(ids) -> bool:
    got = {m for m, _, st in store_rows(PLANNER) if st == "handed"}
    return set(ids) <= got


def test_open_digest_takes_the_handed_rows_into_one_digest(rig, bind):
    c = planner(bind)
    ids = [rig.post_as("main@proj-a", [PLANNER], f"m{i}").id for i in range(3)]
    assert wait_until(lambda: handed(ids))
    answer = c.call("open_digest", {"ids": ids[:2] + ["4611686018427387900"]})
    assert answer["ids"] == ids[:2] and answer["dropped"] == []
    digest = answer["digest"]
    states = {m: st for m, _, st in store_rows(PLANNER)}
    assert states == {ids[0]: "in_digest", ids[1]: "in_digest", ids[2]: "handed"}
    assert rig.daemon.books.digest(digest)["ids"] == ids[:2]
    b = rig.daemon.bindings[PLANNER]
    assert not (set(ids[:2]) & b.window), "the digest's ids left the window"


def test_open_digest_drops_a_withdrawn_message_and_names_it(rig, bind):
    if rig.backend != "local":
        pytest.skip("only the local record keeps its messages in comms.db")
    c = planner(bind)
    mid = rig.post_as("main@proj-a", [PLANNER], "take it back").id
    assert wait_until(lambda: handed([mid]))
    rig.daemon.books._conn.execute(
        "UPDATE messages SET withdrawn_at = '2027-01-01T00:00:00Z' WHERE id = ?",
        (int(mid),))
    answer = c.call("open_digest", {"ids": [mid]})
    assert answer["ids"] == [] and answer["dropped"] == [{"id": mid,
                                                           "reason": "withdrawn"}]
    assert store_rows(PLANNER) == []


def test_injected_keeps_the_digest_text_and_a_turns_ref_marks_it_started(rig, bind):
    c = planner(bind)
    mid = rig.post_as("main@proj-a", [PLANNER], "hello").id
    assert wait_until(lambda: handed([mid]))
    digest = c.call("open_digest", {"ids": [mid]})["digest"]
    c.notify("injected", {"identity": PLANNER, "digest": digest, "text": "the digest"})
    c.notify("activity", {"identity": PLANNER, "ref": digest})
    c.call("max_id")
    got = rig.daemon.books.digest(digest)
    assert got["text"] == "the digest" and got["started_at"] is not None
    assert c.call("last_digest", {"identity": PLANNER})["digest"] == "the digest"


def test_injected_text_is_capped(rig, bind):
    c = planner(bind)
    mid = rig.post_as("main@proj-a", [PLANNER], "hello").id
    assert wait_until(lambda: handed([mid]))
    digest = c.call("open_digest", {"ids": [mid]})["digest"]
    c.notify("injected", {"identity": PLANNER, "digest": digest, "text": "x" * 70_000})
    c.call("max_id")
    assert len(rig.daemon.books.digest(digest)["text"]) == 64 * 1024


def test_open_digest_needs_a_binding_and_a_list(rig, bind):
    c = planner(bind)
    with pytest.raises(client_mod.DaemonError) as bad:
        c.call("open_digest", {"ids": "1"})
    assert bad.value.code == wire.INVALID_PARAMS
    other = client_mod.DaemonClient(c.socket_path).connect()
    try:
        with pytest.raises(client_mod.DaemonError) as unbound:
            other.call("open_digest", {"ids": []})
        assert unbound.value.code == wire.NOT_BOUND
    finally:
        other.close()


def test_a_busy_store_is_store_busy_and_commits_nothing(rig, bind, monkeypatch):
    import sqlite3

    c = planner(bind)

    def busy(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(rig.daemon.books, "open_digest", busy)
    with pytest.raises(client_mod.DaemonError) as e:
        c.call("open_digest", {"ids": []})
    assert e.value.code == wire.STORE_BUSY and e.value.try_again
