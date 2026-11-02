"""usage's doctor rows: the model table's age and gaps, the token index, Codex's import
marks, the restart-point measure and turns. Every root is in the test's tmp; `now` is an argument."""

import calendar
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

import pytest

import moxi.store
from moxi import modules
from moxi_sessions import api as sessions
from moxi_usage import doctor, index, models, store

from conftest import doc, write_config

ClaudeTranscript = sessions.testing.ClaudeTranscript
CodexRollout = sessions.testing.CodexRollout
token_usage = sessions.testing.token_usage
usage = sessions.testing.usage
write_imports = sessions.testing.write_imports

DAY = 86400.0
NOW = 1793400000.0                  # 2026-10-30 22:40 UTC
SESSION = "0190b000-0000-7000-8000-0000000000d1"
THREAD_A = "0190c000-0000-7000-8000-00000000d0a1"
THREAD_B = "0190c000-0000-7000-8000-00000000d0b2"


def row(name, *, now=NOW):
    [found] = [r for r in doctor.rows(now=now) if r.name == name]
    return found


def date_epoch(text):
    return float(calendar.timegm(dt.date.fromisoformat(text).timetuple()))


def run_pass(now=NOW):
    return index.pass_(now=now, projects=None, readings=None, workers=1)


def transcript(root, session=SESSION):
    t = ClaudeTranscript(Path(root), session_id=session, start=NOW - 3600)
    t.user("go")
    t.assistant("ok", usage=usage(output=11))
    t.write()
    return t


def rollout(thread, **kw):
    r = CodexRollout(Path(sessions.root("codex")), thread_id=thread, start=NOW - 3600, **kw)
    r.turn()
    r.user("go")
    r.request(token_usage(output=7), response_id=f"resp_{thread[-4:]}")
    r.turn_end()
    r.write()
    return r


# --- every row ---------------------------------------------------------------------------

def test_the_rows_come_in_order_and_each_is_usages():
    found = list(doctor.rows(now=NOW))
    assert [r.name for r in found] == ["models.snapshot", "models.unpriced", "index",
                                       "codex.imports", "restart_point", "marks", "turns"]
    assert {r.module for r in found} == {"usage"}


def test_the_rows_write_nothing():
    list(doctor.rows(now=NOW))
    assert not store.index_path().exists()
    assert not models.snapshot_path().exists()


# --- models.snapshot ---------------------------------------------------------------------

def test_the_shipped_table_inside_its_age_is_ok():
    shipped = date_epoch(models.shipped()["snapshot"]["date"])
    found = row("models.snapshot", now=shipped + 10 * DAY)
    assert found.state == "ok"
    assert "the shipped table" in found.detail and "10 days old" in found.detail


def test_a_table_past_max_age_days_warns_with_the_update_as_its_fix():
    shipped = date_epoch(models.shipped()["snapshot"]["date"])
    found = row("models.snapshot", now=shipped + 121 * DAY)
    assert found.state == "warn" and "121 days old" in found.detail
    assert "prices.max_age_days (120)" in found.detail
    assert found.fix == "moxi usage prices update"
    write_config("[prices]\nmax_age_days = 200\n")
    assert row("models.snapshot", now=shipped + 121 * DAY).state == "ok"


def test_the_snapshot_layer_dates_the_table_when_there_is_one():
    path = models.snapshot_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(doc({"m": {"input": 1.0, "output": 2.0}}, date="2026-05-01")))
    found = row("models.snapshot")
    assert found.state == "warn" and "prices update` wrote, of 2026-05-01" in found.detail


def test_a_snapshot_that_cannot_be_read_warns():
    path = models.snapshot_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    found = row("models.snapshot")
    assert found.state == "warn" and "the shipped table rules" in found.detail


def test_a_config_that_cannot_be_read_warns_on_both_model_rows():
    write_config("[prices\n")
    for name in ("models.snapshot", "models.unpriced"):
        found = row(name)
        assert found.state == "warn" and "config cannot be read" in found.detail
        assert found.fix == "moxi config usage"


# --- models.unpriced ---------------------------------------------------------------------

def put_requests(*requests):
    """One source holding `requests`, as the pass writes it."""
    conn = store.open_index()
    try:
        rows = [store.request_row(r, lane="claude", path="/p/a.jsonl") for r in requests]
        touched: set[int] = set()
        with moxi.store.transaction(conn):
            file_id = store.upsert_file(conn, lane="claude", path="/p/a.jsonl",
                                        touched=touched, harness="claude", rank=0,
                                        kind="main", read_at=1.0, session_id="s1",
                                        first_ts=min(r.ts for r in rows))
            store.upsert_requests(conn, file_id, rows, touched=touched)
            store.claim(conn, touched)
    finally:
        conn.close()


def request(rid, model, *, ts=NOW - DAY, synthetic=False):
    return sessions.Request(harness="claude", request_id=rid, time=ts, model=model,
                            input_tokens=10, output_tokens=5, synthetic=synthetic)


def test_with_no_index_no_model_is_named():
    found = row("models.unpriced")
    assert found.state == "ok" and "no token index yet" in found.detail


def test_models_seen_lately_with_no_price_or_no_window_are_named():
    write_config('[[model]]\nid = "priced-1"\ninput = 1.0\noutput = 2.0\nwindow = 1000\n'
                 '[[model]]\nid = "nowindow-1"\ninput = 1.0\noutput = 2.0\n')
    put_requests(request("r1", "priced-1"), request("r2", "nowindow-1"),
                 request("r3", "mystery-1"), request("r4", "mystery-1"),
                 request("r5", "long-ago-1", ts=NOW - 40 * DAY),
                 request("r6", "<synthetic>", synthetic=True))
    found = row("models.unpriced")
    assert found.state == "warn" and found.fix == "moxi usage prices update"
    assert "2 of the models seen in the last 30 days" in found.detail
    assert "mystery-1 (no price, no window), nowindow-1 (no window)" in found.detail
    assert "priced-1" not in found.detail and "long-ago-1" not in found.detail
    assert "synthetic" not in found.detail


def test_every_model_seen_known_is_ok():
    write_config('[[model]]\nid = "priced-1"\ninput = 1.0\noutput = 2.0\nwindow = 1000\n')
    put_requests(request("r1", "priced-1"))
    found = row("models.unpriced")
    assert found.state == "ok" and "(1) has a price and a window" in found.detail


# --- index -------------------------------------------------------------------------------

def test_no_index_yet_is_ok_and_says_how_it_is_built():
    found = row("index")
    assert found.state == "ok" and "no token index yet" in found.detail


def test_an_index_after_a_pass_gives_the_last_pass_and_its_counts():
    transcript(sessions.root("claude"))
    run_pass()
    found = row("index", now=NOW + 3 * 3600)
    assert found.state == "ok"
    assert found.detail.startswith("last pass 2026-10-30 22:40 UTC (3h ago): ")
    assert "1 request in 1 file over 3 lanes, 0 copies counted once" in found.detail


def test_an_index_older_than_a_week_warns():
    transcript(sessions.root("claude"))
    run_pass()
    found = row("index", now=NOW + 8 * DAY)
    assert found.state == "warn" and "the last pass is 8 days ago" in found.detail
    assert found.fix == "moxi usage index"


def test_an_offline_archive_lane_and_its_held_rows_are_named(tmp_path):
    drive = tmp_path / "drive"
    transcript(drive / "projects", "0190b000-0000-7000-8000-0000000000d2")
    write_config(f'[[claude.archive_roots]]\npath = "{drive}"\nid = "old"\n')
    run_pass()
    assert row("index").state == "ok"
    shutil.rmtree(drive)                                  # the drive unplugged
    run_pass()
    found = row("index")
    assert found.state == "warn"
    assert "archive:old was offline on the last pass" in found.detail
    assert "archive:old holds 1 row: archive:old could not be listed" in found.detail


def test_an_index_newer_than_this_usage_warns_and_is_not_read():
    run_pass()
    conn = store.connect()
    try:
        conn.execute(f"PRAGMA user_version = {store.INDEX_VERSION + 1}")
    finally:
        conn.close()
    for name in ("index", "codex.imports", "models.unpriced"):
        found = row(name)
        assert found.state == "warn" and "newer than this usage's" in found.detail
        assert found.fix == "upgrade moxi-usage"


# --- codex.imports -----------------------------------------------------------------------

def test_no_codex_here_is_off():
    transcript(sessions.root("claude"))
    run_pass()
    found = row("codex.imports")
    assert found.state == "off" and "no Codex rollouts" in found.detail


def test_no_imports_file_is_ok_nothing_was_imported():
    rollout(THREAD_A)
    run_pass()
    found = row("codex.imports")
    assert found.state == "ok" and "nothing was imported" in found.detail


def test_rollouts_left_out_by_the_imports_file_are_counted_in_the_row():
    rollout(THREAD_A)
    rollout(THREAD_B)
    write_imports(sessions.root("codex"), [THREAD_B])
    run_pass()
    found = row("codex.imports")
    assert found.state == "ok" and found.detail.startswith("1 rollout left out")


def test_an_unreadable_imports_file_warns_of_a_double_count():
    rollout(THREAD_A)
    write_imports(sessions.root("codex"), text="{not json")
    run_pass()
    found = row("codex.imports")
    assert found.state == "warn"
    assert "could not read Codex's imports file" in found.detail
    assert "counted twice" in found.detail


def test_rollouts_with_the_desktop_originator_alone_are_counted_and_named():
    rollout(THREAD_A, originator="Codex Desktop")
    rollout(THREAD_B, originator="Codex Desktop")
    run_pass()
    found = row("codex.imports")
    assert found.state == "warn"
    assert "counted 2 rollouts whose only import mark is the desktop app's" in found.detail


# --- restart_point -----------------------------------------------------------------------

def write_measure(*, measured_at="2026-10-28T00:00:00+00:00", models=None):
    from moxi_usage import thresholds

    path = thresholds.params_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = {"claude-model-x": {"main": {"n": 20, "S": 1.0, "P": 1.0, "g": 1.0, "w": 2.0},
                               "subagent": {"n": 20, "S": 1.0, "P": 1.0, "g": 1.0, "w": 2.0,
                                            "fallback": "main"}}}
    path.write_text(json.dumps({"schema": 1, "measured_at": measured_at,
                                "models": rows if models is None else models}))
    return path


def test_restart_point_with_nothing_measured_or_seen_is_ok():
    found = row("restart_point")
    assert found.state == "ok" and "not measured yet" in found.detail


PRICED = '[[model]]\nid = "claude-model-x"\ninput = 4.0\noutput = 20.0\n'


def measurable_sessions(count=20, *, entrypoint="cli", first=0):
    """`count` Claude sessions of 6 requests each, enough for the measure's rules when
    `count` is 20 or more and they are interactive."""
    for n in range(first, first + count):
        t = ClaudeTranscript(Path(sessions.root("claude")),
                             session_id=f"0190b000-0000-7000-8000-{n:012d}",
                             entrypoint=entrypoint, start=NOW - 3600)
        for k in range(1, 7):
            t.user("go")
            t.assistant("ok", request_id=f"req_{n}_{k}",
                        usage=usage(input=2, cache_read=1000 * k, cache_write=1000,
                                    output=11))
        t.write()


def test_restart_point_with_a_measurable_model_and_no_measure_warns_naming_it():
    write_config(PRICED)
    measurable_sessions()
    run_pass()
    found = row("restart_point")
    assert found.state == "warn" and found.fix == "moxi usage restart-point --measure"
    assert "has not been measured" in found.detail and "claude-model-x (main)" in found.detail


def test_restart_point_does_not_warn_for_what_measuring_again_would_not_give_a_row():
    """Thin, short and headless populations, and a model the table can't price, are
    counted in the detail; a measure would give them no row, so they are no warning."""
    measurable_sessions(19)                               # under 20 files
    measurable_sessions(20, entrypoint="sdk-cli", first=100)   # headless
    transcript(sessions.root("claude"), "0190b000-0000-7000-8000-0000000000f1")  # short
    run_pass()
    found = row("restart_point")
    assert found.state == "ok", found.detail
    assert "not measured yet, and nothing in the token index would give a row" in found.detail
    assert "1 model and population seen in the last 30 days get no row" in found.detail
    write_measure(models={})
    found = row("restart_point")
    assert found.state == "ok", found.detail
    assert "every population the measure would give a row has one" in found.detail
    # Enough interactive files, but the model table doesn't know the model.
    measurable_sessions(1, first=19)
    run_pass()
    found = row("restart_point")
    assert found.state == "ok", found.detail
    assert ("1 model of them has no usable input price in the model table (unknown, "
            "missing or 0), so no write cost" in found.detail)
    assert "claude-model-x" in found.detail


def test_restart_point_with_a_row_for_every_model_seen_is_ok():
    transcript(sessions.root("claude"))
    run_pass()
    write_measure()
    found = row("restart_point")
    assert found.state == "ok", found.detail
    assert "2 days ago: 1 population row for 1 model" in found.detail


def test_restart_point_warns_on_a_model_with_no_row_and_on_an_old_measure():
    write_config(PRICED)
    measurable_sessions()
    run_pass()
    write_measure(measured_at="2026-10-01T00:00:00+00:00", models={})
    found = row("restart_point")
    assert found.state == "warn"
    assert "past 14 days" in found.detail
    assert "no measured row, so no warn-at mark, for claude-model-x (main)" in found.detail


def test_restart_point_warns_on_a_file_it_cannot_read():
    write_measure().write_text("{")
    found = row("restart_point")
    assert found.state == "warn" and "is not JSON" in found.detail


# --- turns -------------------------------------------------------------------------------

def test_without_turns_the_row_is_off_and_says_what_needs_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "moxi_turns", None)
    assert not modules.available("turns")
    found = row("turns")
    assert found.state == "off"
    assert found.detail == ("turns is not installed: Claude limits, Codex limits from the "
                            "app-server, the warn-at mark and the worker ceiling need turns")


def test_with_turns_the_row_is_ok_and_names_what_usage_registers(monkeypatch):
    monkeypatch.setattr(modules, "available", lambda name: name == "turns")
    found = row("turns")
    assert found.state == "ok"
    assert found.detail == (
        "turns is installed; usage registers after-tool (the warn-at mark, with its "
        "floor), gone (forgetting a session's fired marks), usage (Claude limits), "
        "codex_limits (Codex limits from the app-server), and the guard usage.ceiling "
        "(the worker ceiling, off until turned on)")


# --- marks -------------------------------------------------------------------------------

def test_the_marks_row_sweeps_old_files_and_counts():
    import os

    from moxi_usage import mark

    directory = mark.state_dir()
    directory.mkdir(parents=True)
    for name, age in (("claude-old.json", 8), ("claude-old.json.lock", 8),
                      ("claude-new.json", 1)):
        (directory / name).write_text('{"schema": 1}')
        os.utime(directory / name, (NOW - age * 86400, NOW - age * 86400))
    found = row("marks")
    assert found.state == "ok"
    assert found.detail == ("1 session with fired marks kept; deleted 1 fired-mark file "
                            "older than 7 days and the ceiling markers of 0 agents older "
                            "than 7 days")
    assert sorted(p.name for p in directory.iterdir()) == ["claude-new.json"]


def test_the_marks_row_with_nothing_kept_is_ok():
    assert row("marks").state == "ok"


# --- the announcement --------------------------------------------------------------------

def test_the_announcement_loads_with_its_stores_doctor_and_stamps():
    found = modules.load("usage")
    assert found.ok, found.reason
    assert found.doctor == "moxi_usage.doctor:rows"
    assert set(found.stores) == {"index", "index_lock", "readings", "marks", "ceiling",
                                 "restart_point", "snapshot"}
    assert all(entry["private"] and entry["lazy"] for entry in found.stores.values())
    assert set(found.session_env) == {"MOXI_USAGE_WARN_AT", "MOXI_USAGE_CROSSING_AT"}
    assert set(found.optional) == {"turns", "projects"}
    assert dict(found.budgets) == {"hook_ms": 2.0}
    assert set(found.timers) == {"measure"}
    # each store's path is the one the code writes
    from moxi.paths import Layout

    from moxi_usage import readings

    layout = Layout("usage")
    where = {name: getattr(layout, f"{entry['root']}_dir")() / entry["path"]
             for name, entry in found.stores.items()}
    assert where["index"] == store.index_path()
    assert where["index_lock"] == store.lock_path()
    assert where["readings"] == readings.path()
    assert where["snapshot"] == models.snapshot_path()


def test_the_cores_doctor_runs_usages_rows_and_its_stores(capsys):
    from moxi import cli as moxi_cli

    code = moxi_cli.main(["doctor", "usage", "--json"])
    out = json.loads(capsys.readouterr().out)
    names = {r["name"]: r["state"] for r in out if r["module"] == "usage"}
    assert code == 0, out
    assert {"models.snapshot", "models.unpriced", "index", "codex.imports",
            "restart_point", "marks", "turns"} <= set(names)
    assert names["store index"] == names["store readings"] == "ok"


def test_the_index_status_is_worked_out_once_per_doctor_run(monkeypatch):
    from moxi_usage import reports

    transcript(sessions.root("claude"))
    run_pass()
    calls = []
    real = reports.status
    monkeypatch.setattr(reports, "status", lambda conn: calls.append(1) or real(conn))
    names = [r.name for r in doctor.rows(now=NOW)]
    assert "index" in names and "codex.imports" in names
    assert len(calls) == 1
