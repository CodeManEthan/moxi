"""usage's end-of-run guard on its real roots leaves out the stores this machine's running
usage writes during a run, and only those."""

from __future__ import annotations

import os

import conftest


def _state():
    """usage's real state directory, the one guarded root the stores are in."""
    (state,) = {store.parent for store in conftest.LIVE}
    assert state in conftest.GUARDED
    return state


def test_the_stores_usage_writes_while_it_runs_are_left_out():
    state = _state()
    for name in ("readings.db", "readings.db-wal", "readings.db-shm", "index.db", "index.lock",
                 "restart-point.json", "marks", "ceiling"):
        assert state / name in conftest.LIVE, name
    assert conftest._live(os.fspath(state / "readings.db-wal"))
    assert conftest._live(os.fspath(state / "marks" / "claude-s1.json"))
    assert conftest._live(os.fspath(state / "ceiling" / "s1" / "a1" / "warn-250000"))


def test_any_other_file_under_the_real_roots_is_still_listed():
    state = _state()
    assert not conftest._live(os.fspath(state / "readings.dbx"))
    assert not conftest._live(os.fspath(state / "something-new.json"))
    for root in conftest.GUARDED:
        if root != state:
            assert not any(root == store or root in store.parents for store in conftest.LIVE)
