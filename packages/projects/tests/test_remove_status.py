"""What a remove came to, kept for the page under the request's idempotency key: removed,
or refused with its sentences, read back through `remove_status`; the store's own
refusals answered directly, keeping nothing and making no file; a row past a day ignored
and pruned; the verb's remove keeping nothing."""

from __future__ import annotations

import sqlite3
import time

import pytest

from moxi_projects import acts, api, store
from moxi_projects.errors import Broken, Refused


def job(mark: str) -> str:
    """An idempotency key, 43 base64url characters, told apart by `mark`."""
    return (mark * 43)[:43]


def holds(sentence: str) -> str:
    return f'''
INTERFACE = 1

def refuse_remove(project):
    return {sentence!r}
'''


def remove(key: str, key_of_request: str, **named):
    return acts.remove(key, record=acts.record_status(key_of_request), **named)


def _rows(path) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT key, project, created FROM remove_status "
                            "ORDER BY created").fetchall()
    finally:
        conn.close()


def _user_version(path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _set_user_version(path, version: int) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
    finally:
        conn.close()


# --- kept outcomes -----------------------------------------------------------------------

def test_a_removed_project_is_kept_in_the_deletes_own_write(golden_copy, monkeypatch):
    writes = []
    write = store.write

    def counted(fn, where=None):
        writes.append(fn)
        return write(fn, where)

    monkeypatch.setattr(store, "write", counted)
    gone = remove("GAMMA", job("a"))
    assert gone.key == "gamma" and len(writes) == 1
    with pytest.raises(api.NotFound):
        api.get("gamma")
    status = acts.remove_status(job("a"))
    assert status["state"] == "removed" and status["key"] == "gamma"
    assert status["job"] == job("a") and status["project"] == "gamma"
    assert status["created"].endswith("Z")


def test_a_failed_delete_rolls_its_record_back(golden_copy, store_path):
    def record(conn, outcome):
        acts.record_status(job("a"))(conn, outcome)
        raise sqlite3.OperationalError("disk I/O error")

    with pytest.raises(api.Unreadable):
        acts.remove("gamma", record=record)
    assert api.get("gamma") and acts.remove_status(job("a")) is None


def test_a_project_with_agents_is_refused_and_the_refusal_kept(golden_copy):
    with pytest.raises(Refused) as caught:
        remove("alpha", job("a"))
    status = acts.remove_status(job("a"))
    assert status["state"] == "refused" and status["key"] == "alpha"
    assert status["sentences"] == [caught.value.reason]
    assert "helper scout" in status["reason"]
    assert status["fix"] == caught.value.fix and "agents remove alpha" in status["fix"]
    assert status["exit_code"] == 3
    assert api.get("alpha")


def test_a_contributions_refusal_keeps_every_sentence(site, golden_copy):
    site.contribution("svc", holds("services run for it; `moxi services stop` frees it"))
    site.contribution("links", holds("two watches name it; `moxi links clear` frees it"))
    with pytest.raises(Refused):
        remove("gamma", job("a"))
    status = acts.remove_status(job("a"))
    assert status["state"] == "refused"
    assert sorted(status["sentences"]) == [
        "links: two watches name it; `moxi links clear` frees it",
        "svc: services run for it; `moxi services stop` frees it"]
    assert "moxi projects remove gamma" in status["fix"]
    assert api.get("gamma")


def test_an_unknown_key_in_a_store_that_exists_is_kept(golden_copy):
    with pytest.raises(api.NotFound):
        remove("nosuch", job("a"))
    status = acts.remove_status(job("a"))
    assert status["state"] == "refused" and status["key"] == "nosuch"
    assert status["project"] == "nosuch" and "nosuch" in status["sentences"][0]


def test_the_check_again_inside_the_write_is_kept_after_its_rollback(golden_copy,
                                                                    monkeypatch):
    # The read sees no agents; by the write, one is there.
    seen = api.get("alpha")
    without = api.Project(seen.key, seen.root, seen.kind, seen.lead, (), seen.notes_root,
                          seen.created)
    with monkeypatch.context() as patched:
        patched.setattr(api, "get", lambda key: without)
        with pytest.raises(Refused, match="still has associated agents"):
            remove("alpha", job("a"))
    assert api.get("alpha").associated == ("helper", "scout")
    status = acts.remove_status(job("a"))
    assert status["state"] == "refused" and "helper scout" in status["sentences"][0]


# --- the store's own refusals --------------------------------------------------------------

def test_an_unknown_key_with_no_store_file_keeps_nothing_and_makes_no_file(store_path):
    with pytest.raises(api.NotFound):
        remove("nosuch", job("a"))
    assert not store_path.exists() and not store_path.parent.exists()
    assert acts.remove_status(job("a")) is None


def test_a_newer_store_keeps_nothing(golden_copy, store_path):
    _set_user_version(store_path, api._VERSION + 1)
    before = store_path.read_bytes()
    with pytest.raises(api.TooNew) as caught:
        remove("gamma", job("a"))
    assert caught.value.exit_code == 3
    assert store_path.read_bytes() == before


def test_an_unreadable_store_keeps_nothing(store_path):
    store_path.parent.mkdir(parents=True)
    store_path.write_bytes(b"this is not a database, not at all, " * 200)
    before = store_path.read_bytes()
    with pytest.raises(api.Unreadable) as caught:
        remove("gamma", job("a"))
    assert caught.value.exit_code == 1
    assert store_path.read_bytes() == before


def test_a_store_locked_past_the_wait_keeps_nothing(golden_copy, store_path, monkeypatch):
    monkeypatch.setattr(store, "TIMEOUT", 0.05)
    holder = sqlite3.connect(store_path, isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        with pytest.raises(api.Busy):
            remove("gamma", job("a"))
        # A refusal decided about the project whose own write is locked out is that
        # lock's answer, kept nowhere.
        with pytest.raises(api.Busy):
            remove("alpha", job("b"))
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert api.get("gamma") and api.get("alpha")
    assert acts.remove_status(job("a")) is None and acts.remove_status(job("b")) is None
    assert _rows(store_path) == []


def test_a_contribution_that_wont_load_keeps_nothing(site, golden_copy, store_path):
    site.contribution("broken", "raise ImportError('gone')\n")
    before = store_path.read_bytes()
    with pytest.raises(Broken):
        remove("gamma", job("a"))
    assert store_path.read_bytes() == before
    assert acts.remove_status(job("a")) is None


# --- the repeat and the day's window ------------------------------------------------------

def test_a_repeat_key_reads_the_first_record(golden_copy, store_path):
    remove("gamma", job("a"))
    first = acts.remove_status(job("a"))
    # Acting again under the same key keeps the first answer.
    with pytest.raises(api.NotFound):
        remove("gamma", job("a"))
    assert acts.remove_status(job("a")) == first
    assert [row[0] for row in _rows(store_path)] == [job("a")]


def test_keys_are_told_apart_by_case(golden_copy):
    remove("gamma", job("a"))
    assert acts.remove_status(job("A")) is None


def test_a_row_older_than_a_day_is_ignored(golden_copy, store_path, monkeypatch):
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                        time.gmtime(time.time() - store.STATUS_KEPT - 60))
    with monkeypatch.context() as patched:
        patched.setattr(store, "now", lambda: old)
        remove("gamma", job("a"))
    assert [row[0] for row in _rows(store_path)] == [job("a")]
    assert acts.remove_status(job("a")) is None


def test_a_row_inserting_write_prunes_rows_older_than_a_day(golden_copy, store_path,
                                                            monkeypatch):
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                        time.gmtime(time.time() - store.STATUS_KEPT - 60))
    recent = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                           time.gmtime(time.time() - store.STATUS_KEPT + 600))
    with monkeypatch.context() as patched:
        patched.setattr(store, "now", lambda: recent)
        remove("Beta", job("b"))
        patched.setattr(store, "now", lambda: old)
        remove("gamma", job("a"))
    # A write that keeps no row prunes nothing.
    acts.add_agent("delta.web", "watcher")
    assert [row[0] for row in _rows(store_path)] == [job("a"), job("b")]
    with pytest.raises(Refused):
        remove("alpha", job("c"))
    assert [row[0] for row in _rows(store_path)] == [job("b"), job("c")]
    assert acts.remove_status(job("b"))["key"] == "Beta"


# --- version 1 stores, and the verb ------------------------------------------------------

def test_a_version_1_store_answers_no_such_request_and_is_left_as_it_was(v1_copy,
                                                                        store_path):
    before = store_path.read_bytes()
    assert acts.remove_status(job("a")) is None
    assert store_path.read_bytes() == before
    assert _user_version(store_path) == 1


def test_a_kept_remove_migrates_a_version_1_store_first(v1_copy, store_path):
    with pytest.raises(Refused):
        remove("alpha", job("a"))
    assert _user_version(store_path) == 2
    assert acts.remove_status(job("a"))["state"] == "refused"


def test_without_a_record_a_refusal_writes_nothing(v1_copy, store_path, site):
    before = store_path.read_bytes()
    for key in ("alpha", "nosuch"):
        with pytest.raises((Refused, api.NotFound)):
            acts.remove(key)
    site.contribution("svc", holds("services run for it; `moxi services stop` frees it"))
    with pytest.raises(Refused):
        acts.remove("gamma")
    assert store_path.read_bytes() == before
    assert _user_version(store_path) == 1


def test_without_a_record_a_removal_keeps_no_row(golden_copy, store_path):
    acts.remove("gamma")
    assert _rows(store_path) == []
