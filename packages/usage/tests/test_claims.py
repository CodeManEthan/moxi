"""The claim: `requests.won` is always the set the `claimed` view gives.

Every test checks, after every write it makes, that the stored claims equal the view's
over the whole index (`store.check` is empty), as the index's check verb does.
"""

import pytest

import moxi.store
from moxi_usage import store


@pytest.fixture
def conn():
    connection = store.open_index()
    yield connection
    connection.close()


def row(key, *, seq=1, ts=100.0) -> store.RequestRow:
    return store.RequestRow(key=key, seq=seq, ts=ts, model="m", response_model=None,
                            provider=None, input=10, cache_read=0, cache_write=0,
                            cache_write_1h=0, output=5, reasoning=None, service_tier=None,
                            speed=None, harness_cost=None, sidechain=0, synthetic=0,
                            turn_id=None, agent_id=None, unknown=None)


def write(conn, lane, path, keys=(), *, part="", **fields) -> int:
    """One source's transaction, as the pass makes it: the file row, its requests, the
    claim step for what it touched."""
    touched: set[int] = set()
    with moxi.store.transaction(conn):
        defaults = {} if store.get_file(conn, lane=lane, path=path, part=part) else {
            "harness": "claude", "rank": 0, "kind": "main", "read_at": 1.0}
        file_id = store.upsert_file(conn, lane=lane, path=path, part=part, touched=touched,
                                    **{**defaults, **fields})
        store.upsert_requests(conn, file_id, [row(k, seq=i) for i, k in enumerate(keys)],
                              touched=touched)
        store.claim(conn, touched)
    assert store.check(conn) == []
    return file_id


def winners(conn) -> dict[int, int]:
    """Each claimed key and the file that holds its claim."""
    return {r[0]: r[1] for r in conn.execute(
        "SELECT key, file_id FROM requests WHERE won = 1 ORDER BY key")}


def test_a_file_grows_an_earlier_copy_steals_keys_and_a_deleted_source_gives_them_back(conn):
    live = write(conn, "claude", "/live/s.jsonl", [1, 2], first_ts=100.0, size=200)
    assert winners(conn) == {1: live, 2: live}

    # the file grows: more requests, a larger size
    write(conn, "claude", "/live/s.jsonl", [3, 4], size=400, last_ts=200.0)
    assert winners(conn) == {1: live, 2: live, 3: live, 4: live}

    # an archive copy that starts earlier holds keys 1 and 2 (and 5 of its own): it steals
    old = write(conn, "archive:old", "/old/s.jsonl", [1, 2, 5], first_ts=50.0, size=100,
                rank=3)
    assert winners(conn) == {1: old, 2: old, 3: live, 4: live, 5: old}
    assert conn.execute("SELECT count(*) FROM requests WHERE won = 0").fetchone()[0] == 2

    # the archive source goes: its keys are claimed again in the same transaction
    touched: set[int] = set()
    with moxi.store.transaction(conn):
        store.delete_files(conn, [old], touched=touched)
        store.claim(conn, touched)
    assert store.check(conn) == []
    assert winners(conn) == {1: live, 2: live, 3: live, 4: live}


def test_the_order_is_first_time_then_size_then_rank_then_path(conn):
    a = write(conn, "claude", "/b.jsonl", [1], first_ts=10.0, size=100, rank=0)
    b = write(conn, "codex", "/a.jsonl", [1], first_ts=10.0, size=100, rank=1)
    assert winners(conn) == {1: a}                       # rank 0 before rank 1
    write(conn, "codex", "/a.jsonl", size=300)
    assert winners(conn) == {1: b}                       # larger size before rank
    c = write(conn, "claude", "/c.jsonl", [1], first_ts=5.0, size=1, rank=9)
    assert winners(conn) == {1: c}                       # earlier first time before all
    write(conn, "claude", "/c.jsonl", first_ts=10.0, size=300, rank=1)
    assert winners(conn) == {1: b}                       # all equal but path: /a first


def test_a_tie_on_every_designed_field_still_has_one_winner(conn):
    first = write(conn, "opencode", "/oc.db", [1], part="ses_a", harness="opencode", rank=2,
                  first_ts=1.0, size=10)
    write(conn, "opencode", "/oc.db", [1], part="ses_b", harness="opencode", rank=2,
          first_ts=1.0, size=10)
    assert winners(conn) == {1: first}


def test_an_excluded_file_leaves_the_claim_and_returns_to_it(conn):
    live = write(conn, "claude", "/live.jsonl", [1, 2], first_ts=100.0, size=10)
    roll = write(conn, "codex", "/roll.jsonl", [1, 2], first_ts=50.0, size=10, rank=1)
    assert winners(conn) == {1: roll, 2: roll}

    # a reused file whose import mark flips: no request is read, the claim moves anyway
    touched: set[int] = set()
    with moxi.store.transaction(conn):
        store.update_file(conn, roll, touched=touched, excluded="imported")
        store.claim(conn, touched)
    assert touched == {1, 2} and store.check(conn) == []
    assert winners(conn) == {1: live, 2: live}

    write(conn, "codex", "/roll.jsonl", excluded=None)
    assert winners(conn) == {1: roll, 2: roll}


def test_a_rank_change_on_a_reused_file_reclaims_its_keys(conn):
    one = write(conn, "archive:a", "/a/s.jsonl", [1], first_ts=1.0, size=1, rank=3)
    two = write(conn, "archive:b", "/b/s.jsonl", [1], first_ts=1.0, size=1, rank=4)
    assert winners(conn) == {1: one}
    write(conn, "archive:a", "/a/s.jsonl", rank=5)       # the config reordered the roots
    assert winners(conn) == {1: two}


def test_a_change_that_does_not_reorder_touches_nothing(conn):
    file_id = write(conn, "claude", "/s.jsonl", [1, 2], first_ts=1.0, size=1)
    touched: set[int] = set()
    with moxi.store.transaction(conn):
        store.update_file(conn, file_id, touched=touched, cursor="c9", held=None,
                          title="t", first_ts=1.0)
    assert touched == set()


def test_a_reset_reclaims_the_keys_it_deleted(conn):
    live = write(conn, "claude", "/live.jsonl", [1, 2], first_ts=100.0, size=10)
    early = write(conn, "claude", "/early.jsonl", [1, 2], first_ts=1.0, size=10)
    assert winners(conn) == {1: early, 2: early}
    touched: set[int] = set()
    with moxi.store.transaction(conn):
        store.delete_requests(conn, [early], touched=touched)
        store.upsert_requests(conn, early, [row(2)], touched=touched)
        store.claim(conn, touched)
    assert store.check(conn) == []
    assert winners(conn) == {1: live, 2: early}


def test_claim_all_settles_every_key(conn):
    a = write(conn, "claude", "/a.jsonl", [1, 2, 3], first_ts=5.0)
    write(conn, "claude", "/b.jsonl", [2, 3, 4], first_ts=1.0)
    conn.execute("UPDATE requests SET won = 0")
    assert store.check(conn) == [1, 2, 3, 4]
    with moxi.store.transaction(conn):
        assert store.claim_all(conn) == 4
    assert store.check(conn) == []
    assert winners(conn)[1] == a


def test_negative_a_reorder_without_the_claim_step_differs_from_the_view(conn):
    late = write(conn, "claude", "/late.jsonl", [1, 2, 3], first_ts=100.0, size=10)
    early = write(conn, "archive:x", "/x/early.jsonl", [2, 3], first_ts=50.0, size=10, rank=3)
    assert winners(conn) == {1: late, 2: early, 3: early}

    # reorder the archive copy past the live one by hand, skipping the claim step
    conn.execute("UPDATE files SET first_ts = 500.0 WHERE id = ?", (early,))
    assert store.check(conn) == [2, 3]

    # the claim step for those keys puts the stored set back on the view's
    with moxi.store.transaction(conn):
        store.claim(conn, store.file_keys(conn, early))
    assert store.check(conn) == []
    assert winners(conn) == {1: late, 2: late, 3: late}


def test_the_claim_step_reaches_copies_outside_any_time_range(conn):
    old = write(conn, "archive:x", "/x/s.jsonl", [1], first_ts=1.0, size=1, rank=3)
    conn.execute("UPDATE requests SET ts = 1.0 WHERE file_id = ?", (old,))
    touched: set[int] = set()
    with moxi.store.transaction(conn):
        new = store.upsert_file(conn, lane="claude", path="/s.jsonl", touched=touched,
                                harness="claude", rank=0, kind="main", read_at=1.0,
                                first_ts=900.0, size=1)
        store.upsert_requests(conn, new, [row(1, ts=1_000_000.0)], touched=touched)
        store.claim(conn, touched)
    assert store.check(conn) == [] and winners(conn) == {1: old}


# --- the duplicates ----------------------------------------------------------------------

def plans(conn, run) -> list[str]:
    """SQLite's plan for every query `run(conn)` makes, one line per step."""
    seen: list[str] = []
    conn.set_trace_callback(seen.append)
    try:
        run(conn)
    finally:
        conn.set_trace_callback(None)
    return [step[-1] for sql in seen if sql.lstrip().upper().startswith("SELECT")
            for step in conn.execute("EXPLAIN QUERY PLAN " + sql)]


def test_a_file_whose_every_request_another_claims_is_a_duplicate_of_its_first_ones(conn):
    first = write(conn, "claude", "/first.jsonl", [1, 2, 3], first_ts=1.0, size=30)
    write(conn, "claude", "/again.jsonl", [1, 2], first_ts=5.0, size=20)
    write(conn, "claude", "/more.jsonl", [2, 9], first_ts=6.0, size=20)     # 9 is its own
    write(conn, "codex", "/gone.jsonl", [3], first_ts=7.0, size=5, excluded="imported")
    [dup] = store.duplicates(conn)
    assert (dup["path"], dup["requests"], dup["of_path"]) == ("/again.jsonl", 2,
                                                              "/first.jsonl")
    assert winners(conn)[1] == first
    assert store.duplicates(conn, lanes=["codex"]) == []


def test_the_duplicates_read_each_files_rows_by_its_key_not_the_won_index(conn):
    # without stats, SQLite takes the won index for `won = 1`, where nearly every row is
    # won: each file then walks every claimed row, minutes on a large index
    write(conn, "claude", "/first.jsonl", [1, 2], first_ts=1.0, size=30)
    write(conn, "claude", "/again.jsonl", [1, 2], first_ts=5.0, size=20)
    steps = plans(conn, store.duplicates)
    assert steps and not any("requests_won_ts" in step for step in steps), steps
