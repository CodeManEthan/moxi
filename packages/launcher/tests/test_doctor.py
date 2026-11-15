"""The launcher's doctor rows: one per harness, the `--help` diff's verdict, and with cache
the row that says whether cache's watcher asks about the launched sessions."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from moxi.doctor import Row

from moxi_launcher import catalog, doctor, joiner, store

from test_catalog import FakeHarness, expected_flags, help_text, matching
from test_keepwarm import SID, launched

NOW = 1_000_000.0
OTHER = "22222222-2222-4222-8222-222222222222"
THIRD = "33333333-3333-4333-8333-333333333333"


@pytest.fixture(autouse=True)
def no_cache(monkeypatch):
    """cache absent unless a test hands the row an API of its own."""
    monkeypatch.setattr(joiner, "cache_api", lambda: None)


def rows_for(read) -> dict[str, Row]:
    return {row.name: row for row in doctor.rows(read=read)}


def test_one_row_per_harness_and_ok_when_the_help_matches():
    helps = {**matching("claude"), **matching("codex")}
    rows = rows_for(FakeHarness(helps, version="2.1.314"))
    assert set(rows) == {"catalog.claude", "catalog.codex"}
    assert all(row.module == "launcher" and row.state == "ok" for row in rows.values())
    assert rows["catalog.codex"].detail == ("the catalog matches codex --help and codex "
                                            "exec --help (codex 2.1.314)")


def test_a_removed_flag_fails_with_a_fix():
    helps = {**matching("claude"), **matching("codex")}
    helps[("claude", "--help")] = help_text(expected_flags("claude", "claude")
                                            - {"--permission-mode"})
    row = rows_for(FakeHarness(helps))["catalog.claude"]
    assert row.state == "fail"
    assert "claude --help no longer lists --permission-mode" in row.detail
    assert row.fix


def test_a_removed_flag_fails_though_the_other_help_could_not_be_read():
    helps = {**matching("claude"), **matching("codex")}
    helps[("codex", "--help")] = help_text(expected_flags("codex", "codex") - {"-m"})
    fake = FakeHarness(helps)

    def read(argv):
        if tuple(argv) == ("codex", "exec", "--help"):
            raise OSError("permission denied")
        return fake(argv)

    row = rows_for(read)["catalog.codex"]
    assert row.state == "fail" and row.fix
    assert "codex --help no longer lists -m" in row.detail
    assert "permission denied" in row.detail


def test_an_added_flag_warns_and_names_it():
    helps = {**matching("claude"), **matching("codex")}
    helps[("codex", "exec", "--help")] = help_text(expected_flags("codex", "exec")
                                                   | {"--new-one"})
    row = rows_for(FakeHarness(helps))["catalog.codex"]
    assert row.state == "warn"
    assert "codex exec --help lists flags the catalog does not know: --new-one" in row.detail


def test_many_flags_are_cut_to_a_count():
    new = {f"--new-{i:02d}" for i in range(12)}
    helps = {**matching("claude"), **matching("codex")}
    helps[("claude", "--help")] = help_text(expected_flags("claude", "claude") | new)
    row = rows_for(FakeHarness(helps))["catalog.claude"]
    assert row.detail.count("--new-") == doctor.SHOWN
    assert "and 4 more" in row.detail


def test_a_harness_not_on_path_is_off_and_one_that_cannot_run_warns():
    assert rows_for(FakeHarness({}, missing=True))["catalog.claude"].state == "off"
    row = rows_for(FakeHarness({}, fails=True))["catalog.codex"]
    assert row.state == "warn" and "could not run" in row.detail


def test_the_announcement_names_these_rows():
    from moxi_launcher import module

    assert module.DOCTOR == "moxi_launcher.doctor:rows"
    assert catalog.HARNESSES == ("claude", "codex")


# -- keep-warm.timer ---------------------------------------------------------------------


@pytest.fixture
def log(tmp_path, monkeypatch):
    """A launch log holding one launch of `SID`, where the row reads it."""
    path = tmp_path / "launcher.db"
    monkeypatch.setattr(store, "path", lambda env=None: path)
    conn = store.connect(path)
    launched(conn)
    yield conn
    conn.close()


def record(session, *, asks=("launcher.keep-warm",), ago_s=1.0, stale_after=30.0,
           keyed=True):
    found = {"v": 1, "session": session, "harness": "claude", "seen_at": NOW - ago_s,
             "stale_after": stale_after}
    if keyed:
        found["asks"] = None if asks is None else list(asks)
    return found


def cache(*, expected=("launcher.keep-warm",), records=(), asking=True):
    api = SimpleNamespace(clock_records=lambda: [dict(r) for r in records], asked=[])
    if asking:
        def ask(environ):
            api.asked.append(dict(environ))
            return expected
        api.asking = ask
    return api


def keep_warm(api) -> Row:
    return doctor.keep_warm_row(api, now=NOW)


def test_the_row_follows_the_harness_rows_when_cache_is_there(monkeypatch, log):
    monkeypatch.setattr(joiner, "cache_api", lambda: cache())
    helps = {**matching("claude"), **matching("codex")}
    names = [row.name for row in doctor.rows(read=FakeHarness(helps))]
    assert names == ["catalog.claude", "catalog.codex", "keep-warm.timer"]


def test_the_row_is_absent_without_cache():
    helps = {**matching("claude"), **matching("codex")}
    assert "keep-warm.timer" not in rows_for(FakeHarness(helps))


def test_a_cache_without_asking_warns_to_upgrade_it_and_offers_no_restart(log):
    row = keep_warm(cache(asking=False, records=[record(SID, keyed=False)]))
    assert (row.module, row.name, row.state) == ("launcher", "keep-warm.timer", "warn")
    assert "older than 0.1.6" in row.detail and "upgrade moxi-cache" in row.detail
    assert row.fix is None


def test_a_system_that_cannot_read_an_environment_is_off(log):
    api = cache(expected=None, records=[record(SID, keyed=False)])
    row = keep_warm(api)
    assert row.state == "off" and row.fix is None
    assert "environment can't be read" in row.detail
    assert api.asked == [{"MOXI_LAUNCHER_REQUEST": "doctor"}]


def test_cache_refusing_the_subscription_warns_and_points_at_its_timers(log):
    row = keep_warm(cache(expected=("launcher.recycle-on-expiry",), records=[record(SID)]))
    assert row.state == "warn" and row.fix == "moxi cache timers"
    assert "does not take the launcher's keep-warm subscription" in row.detail


def test_a_fresh_record_without_asks_of_any_session_warns_to_restart(log):
    row = keep_warm(cache(records=[record(OTHER, keyed=False)]))
    assert row.state == "warn" and row.fix == doctor.RESTART
    assert row.fix.startswith("moxi services restart cache/watch, or without services ")
    assert "started before cache was upgraded" in row.detail


def test_a_stale_record_without_asks_says_nothing_of_the_running_watcher(log):
    row = keep_warm(cache(records=[record(SID, keyed=False, ago_s=60.0)]))
    assert row.state == "ok"


def test_a_launched_session_s_record_without_the_subscription_warns_with_the_count(
        log):
    launched(log, request_id="r2", session_id=THIRD)
    row = keep_warm(cache(records=[record(SID, asks=[]), record(THIRD, asks=None),
                                   record(OTHER, asks=[])]))
    assert row.state == "warn" and row.fix == doctor.RESTART
    assert "does not ask about 2 launched sessions" in row.detail
    one = keep_warm(cache(records=[record(SID, asks=["launcher.recycle-on-expiry"])]))
    assert "does not ask about 1 launched session that" in one.detail


def test_the_name_in_every_launched_record_is_ok(log):
    row = keep_warm(cache(records=[record(SID), record(OTHER, asks=[])]))
    assert row.state == "ok" and row.fix is None
    assert row.detail == "cache's timer asks about every launched session it times"


def test_no_record_at_all_is_ok_when_cache_takes_the_subscription(log):
    row = keep_warm(cache())
    assert row.state == "ok"
    assert row.detail == ("cache takes the keep-warm subscription; no launched session is "
                          "being timed")


def test_no_launch_log_is_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda env=None: tmp_path / "none.db")
    assert keep_warm(cache(records=[record(SID, asks=[])])).state == "ok"


def test_a_launch_log_that_cannot_be_read_warns_and_names_the_error(log, monkeypatch):
    def busy(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store, "resolve", busy)
    row = keep_warm(cache(records=[record(SID, asks=[])]))
    assert row.state == "warn" and "disk I/O error" in row.detail
