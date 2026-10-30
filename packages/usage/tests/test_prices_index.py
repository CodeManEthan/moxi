"""`prices update` names what a new snapshot does to the models the token index has seen,
and works the same with no index at all."""

import json

import moxi.store
from moxi_usage import cli, store

from test_cli import big_litellm, fetched  # noqa: F401  (a fixture)


def index_with(models_seen):
    conn = store.open_index()
    try:
        touched: set[int] = set()
        with moxi.store.transaction(conn):
            file_id = store.upsert_file(conn, lane="claude", path="/p/a.jsonl",
                                        touched=touched, harness="claude", rank=0,
                                        kind="main", read_at=1.0, first_ts=1.0)
            rows = [store.RequestRow(
                key=n, seq=n, ts=1.0 + n, model=model, response_model=None, provider=None,
                input=10, cache_read=0, cache_write=0, cache_write_1h=0, output=1,
                reasoning=None, service_tier=None, speed=None, harness_cost=None,
                sidechain=0, synthetic=0, turn_id=None, agent_id=None, unknown=None)
                for n, model in enumerate(models_seen)]
            store.upsert_requests(conn, file_id, rows, touched=touched)
            store.claim(conn, touched)
    finally:
        conn.close()


def test_update_names_the_index_models_it_reprices_prices_and_leaves_unpriced(
        fetched, capsys):  # noqa: F811
    fetched(big_litellm())
    index_with(["claude-opus-5-5", "brand-new-1", "mystery-9", "brand-new-1"])
    assert cli.main(["prices", "update", "--dry-run", "--json"]) == 0
    found = json.loads(capsys.readouterr().out)["in_index"]
    assert found == {"models": 3, "repriced": ["claude-opus-5-5"],
                     "priced": ["brand-new-1"], "unpriced": ["mystery-9"]}
    assert cli.main(["prices", "update", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "of the 3 models in the token index:" in out
    assert "repriced  1: claude-opus-5-5" in out and "priced    1: brand-new-1" in out
    assert "unpriced  1: mystery-9" in out


def test_update_without_an_index_still_works_and_says_so(fetched, capsys):  # noqa: F811
    fetched(big_litellm())
    assert cli.main(["prices", "update", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["written"] and result["in_index"] is None
    assert not store.index_path().exists()
    assert cli.main(["prices", "update", "--dry-run"]) == 0
    assert "no token index yet" in capsys.readouterr().out
