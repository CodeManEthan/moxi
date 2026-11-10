"""Reports and their cost, over index rows written through the store as the pass writes
them: sessions requests made into rows, one transaction per source, the claim step after
each, and the stored claims checked against the view every time."""

import datetime as dt
import json
import os
import sys
import time

import pytest

import moxi.store
from moxi.files import LockTimeout
from moxi_sessions import api as sessions
from moxi_usage import api, cli, cost, models, reports, store
from moxi_usage.settings import Override

from conftest import doc, write_config

UNKNOWN = sessions.UNKNOWN
UTC = dt.timezone.utc


def at(text: str) -> float:
    """Epoch seconds for an ISO time in UTC."""
    return dt.datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


ANTHROPIC = {"cache_read": 0.1, "cache_write_5m": 1.25, "cache_write_1h": 2.0}
OPENAI = {"cache_write_5m": 1.0, "cache_write_1h": 1.0}
MODELS = {
    "m-a": {"input": 3.0, "output": 15.0, "provider": "anthropic", "window": 200000,
            "tiers": {"fast": {"speed": "fast", "input": 6.0, "output": 30.0},
                      "above_200k": {"above_tokens": 200000, "input": 6.0,
                                     "output": 22.5, "cache_read": 0.6}}},
    "m-b": {"input": 1.0, "output": 5.0, "provider": "anthropic",
            "tiers": {"above_100k": {"above_tokens": 100000, "input": 2.0,
                                     "output": 10.0}}},
    "gpt-x": {"input": 1.0, "output": 8.0, "cache_read": 0.1, "provider": "openai",
              "tiers": {"priority": {"service_tier": "priority", "input": 2.0,
                                     "output": 16.0, "cache_read": 0.2}}},
    "half": {"input": 1.0, "output": 2.0, "provider": "other"},
}


def table(*overrides) -> models.Table:
    return models.Table(doc(MODELS, defaults={"anthropic": ANTHROPIC, "openai": OPENAI}),
                        overrides=overrides)


@pytest.fixture
def conn():
    connection = store.open_index()
    yield connection
    connection.close()


def request(rid, ts, *, model="m-a", harness="claude", input=1000, cache_read=0,
            cache_write=0, cache_write_1h=0, output=100, **fields):
    """A sessions request as a harness reader would give it."""
    return sessions.Request(harness=harness, request_id=rid, time=ts, model=model,
                            input_tokens=input, cache_read_input_tokens=cache_read,
                            cache_creation_input_tokens=cache_write,
                            cache_creation_1h_input_tokens=cache_write_1h,
                            output_tokens=output, **fields)


def source(conn, path, requests, *, lane="claude", harness="claude", kind="main",
           session_id="s1", rank=0, size=100, part="", **fields) -> int:
    """One source's transaction, as the pass writes it."""
    rows = [store.request_row(r, lane=lane, path=path) for r in requests]
    times = [r.ts for r in rows if r.ts is not None]
    touched: set[int] = set()
    with moxi.store.transaction(conn):
        file_id = store.upsert_file(
            conn, lane=lane, path=path, part=part, touched=touched, harness=harness,
            rank=rank, kind=kind, read_at=1.0, session_id=session_id, size=size,
            first_ts=min(times, default=None), last_ts=max(times, default=None), **fields)
        store.upsert_requests(conn, file_id, rows, touched=touched)
        store.claim(conn, touched)
    assert store.check(conn) == []
    return file_id


def one(report, **match):
    found = [row for row in report.rows if all(row[k] == v for k, v in match.items())]
    assert len(found) == 1, (match, report.rows)
    return found[0]


# --- cost --------------------------------------------------------------------------------

def test_the_five_terms_price_fresh_input_reads_both_writes_and_output(conn):
    source(conn, "/p/a.jsonl", [request("r1", at("2026-10-01T12:00"), input=1000,
                                        cache_read=200, cache_write=300, cache_write_1h=100,
                                        output=100)])
    row = one(reports.daily(conn, tz="UTC", table=table()), day="2026-10-01")
    # fresh 500 x 3 + read 200 x 0.3 + 5m write 200 x 3.75 + 1h write 100 x 6 + out 100 x 15
    assert row["cost"] == pytest.approx((1500 + 60 + 750 + 600 + 1500) / 1e6)
    assert (row["input"], row["fresh_input"], row["cache_read"], row["cache_write"],
            row["cache_write_1h"], row["output"], row["tokens"]) == (
        1000, 500, 200, 300, 100, 100, 1100)
    assert row["complete"] and row["unknown_rows"] == 0 and row["unpriced_tokens"] == 0


def test_fresh_input_is_floored_per_request():
    sums = cost.Sums(requests=1, input=10, fresh=0, cache_read=50, output=0)
    facts = table().lookup("m-a")
    priced = cost.price(sums, facts, ANTHROPIC)
    assert priced.cost == pytest.approx(50 * 0.3 / 1e6)


def test_an_unknown_model_is_unpriced_never_zero(conn):
    source(conn, "/p/a.jsonl", [request("r1", at("2026-10-01T12:00"), model="mystery-9"),
                                request("r2", at("2026-10-01T13:00"))])
    report = reports.daily(conn, tz="UTC", table=table())
    mystery = one(report, model="mystery-9")
    assert mystery["cost"] is UNKNOWN
    assert mystery["unpriced_tokens"] == 1100 and not mystery["complete"]
    assert report.unpriced_models == ["mystery-9"]
    # the total keeps the priced part and counts the rest as unpriced
    assert report.total["cost"] == pytest.approx((1000 * 3 + 100 * 15) / 1e6)
    assert report.total["unpriced_tokens"] == 1100
    data = report.as_dict()
    row = next(r for r in data["rows"] if r["model"] == "mystery-9")
    assert "cost" not in row and row["unknown"] == ["cost"]
    json.dumps(data)


def test_a_term_with_tokens_and_no_price_unprices_the_group(conn):
    # `half` has no cache-write price and its provider no default multiplier
    source(conn, "/p/a.jsonl", [request("r1", at("2026-10-01T12:00"), model="half",
                                        cache_write=100)])
    row = one(reports.models_report(conn, table=table()), model="half")
    assert row["cost"] is UNKNOWN and row["unpriced_models"] == ["half"]
    source(conn, "/p/b.jsonl", [request("r2", at("2026-10-01T12:00"), model="half")],
           session_id="s2")
    report = reports.daily(conn, tz="UTC", table=table())
    row = one(report, model="half")
    # both requests sum into one price group, which a missing price leaves unpriced whole
    assert row["cost"] is UNKNOWN and row["requests"] == 2
    assert row["unpriced_tokens"] == 2200 and not row["complete"]
    assert report.total["cost"] is UNKNOWN


def test_unknown_rows_flags_a_group_mixing_known_and_unknown_counts(conn):
    source(conn, "/p/a.jsonl", [
        request("r1", at("2026-10-01T12:00")),
        request("r2", at("2026-10-01T13:00"), output=UNKNOWN),
        request("r3", at("2026-10-01T14:00"), reasoning_output_tokens=UNKNOWN)])
    row = one(reports.daily(conn, tz="UTC", table=table()), day="2026-10-01")
    assert row["requests"] == 3 and row["output"] == 200
    assert row["unknown_rows"] == 1 and not row["complete"]
    assert row["cost"] == pytest.approx((3000 * 3 + 200 * 15) / 1e6)


def test_an_unknown_cache_write_under_a_writes_as_input_rule_is_priced_and_named(conn):
    rows = [request("t1", at("2026-10-01T12:00"), harness="codex", model="gpt-x",
                    input=1000, cache_read=400, cache_write=UNKNOWN, cache_write_1h=UNKNOWN,
                    output=50)]
    source(conn, "/c/r.jsonl", rows, lane="codex", harness="codex", rank=1)
    report = reports.daily(conn, tz="UTC", table=table())
    row = one(report, model="gpt-x")
    assert row["unknown_rows"] == 0 and row["complete"]
    assert row["rules"] == [cost.rule_name("openai")] and report.rules == row["rules"]
    assert row["cost"] == pytest.approx((600 * 1.0 + 400 * 0.1 + 50 * 8.0) / 1e6)


def test_an_unknown_cache_write_without_such_a_rule_counts_as_unknown(conn):
    source(conn, "/p/a.jsonl", [request("r1", at("2026-10-01T12:00"), cache_write=UNKNOWN,
                                        cache_write_1h=UNKNOWN)])
    row = one(reports.daily(conn, tz="UTC", table=table()), model="m-a")
    assert row["unknown_rows"] == 1 and row["rules"] == [] and not row["complete"]


def test_fast_mode_prices_its_cache_on_the_fast_input_price(conn):
    source(conn, "/p/a.jsonl", [
        request("r1", at("2026-10-01T12:00"), speed="fast", input=1000, cache_read=500,
                cache_write=300, cache_write_1h=100, output=100),
        request("r2", at("2026-10-01T12:05"), speed="standard", output=100)])
    report = reports.daily(conn, tz="UTC", table=table())
    row = one(report, model="m-a")
    fast = 200 * 6 + 500 * 0.6 + 200 * 7.5 + 100 * 12 + 100 * 30
    standard = 1000 * 3 + 100 * 15
    assert row["cost"] == pytest.approx((fast + standard) / 1e6)
    paid = cost.prices(table().lookup("m-a"), ANTHROPIC, speed="fast")
    assert paid.tier == "fast"
    assert paid.from_tier_input == ("cache_read", "cache_write_5m", "cache_write_1h")


def test_prompt_size_tiers_take_bounds_from_each_model_in_range(conn):
    source(conn, "/p/a.jsonl", [
        request("r1", at("2026-10-01T12:00"), model="m-a", input=150_000, output=0),
        request("r2", at("2026-10-01T12:01"), model="m-a", input=250_000, output=0),
        request("r3", at("2026-10-01T12:02"), model="m-b", input=150_000, output=0)])
    report = reports.models_report(conn, table=table())
    # m-a: 150k is under its 200k bound; 250k is above it
    assert one(report, model="m-a")["cost"] == pytest.approx(
        (150_000 * 3 + 250_000 * 6) / 1e6)
    # m-b: 150k is above its 100k bound, though m-a's bound puts it in the same bucket
    assert one(report, model="m-b")["cost"] == pytest.approx(150_000 * 2 / 1e6)


def test_a_service_tier_picks_its_tier_and_a_write_bills_at_the_tier_input(conn):
    source(conn, "/c/r.jsonl", [
        request("t1", at("2026-10-01T12:00"), harness="codex", model="gpt-x",
                service_tier="priority", input=1000, cache_read=400, cache_write=100,
                output=10),
        request("t2", at("2026-10-01T12:01"), harness="codex", model="gpt-x",
                service_tier="default", input=1000, output=10)],
        lane="codex", harness="codex")
    row = one(reports.daily(conn, tz="UTC", table=table()), model="gpt-x")
    priority = 500 * 2.0 + 400 * 0.2 + 100 * 2.0 + 10 * 16.0
    default = 1000 * 1.0 + 10 * 8.0
    assert row["cost"] == pytest.approx((priority + default) / 1e6)


def test_price_periods_price_each_request_on_its_own_time(conn):
    change = at("2026-10-01T12:00")
    cheaper = Override(id="m-a", prices={"input": 1.0, "output": 5.0}, start=change,
                       number=1)
    source(conn, "/p/a.jsonl", [request("r1", change - 60), request("r2", change + 60)])
    report = reports.daily(conn, tz="UTC", table=table(cheaper))
    row = one(report, model="m-a")
    assert row["requests"] == 2
    assert row["cost"] == pytest.approx(((1000 * 3 + 100 * 15) + (1000 * 1 + 100 * 5)) / 1e6)
    provenance = one(reports.models_report(conn, table=table(cheaper)),
                     model="m-a")["provenance"]
    assert [p["layer"] for p in provenance] == ["shipped", "override"]


def test_synthetic_requests_are_free(conn):
    source(conn, "/p/a.jsonl", [request("r1", at("2026-10-01T12:00"), model="<synthetic>",
                                        synthetic=True)])
    row = one(reports.daily(conn, tz="UTC", table=table()), model="<synthetic>")
    assert row["cost"] == 0 and row["complete"]


# --- what a report counts ----------------------------------------------------------------

def test_reports_count_the_claimed_copy_once(conn):
    t = at("2026-10-01T12:00")
    source(conn, "/live/s.jsonl", [request("r1", t), request("r2", t + 1)], size=400)
    source(conn, "/archive/s.jsonl", [request("r1", t), request("r2", t + 1)],
           lane="archive:old", rank=3, size=100)
    report = reports.daily(conn, tz="UTC", table=table())
    assert report.total["requests"] == 2 and report.total["input"] == 2000


def test_an_excluded_file_is_not_counted(conn):
    t = at("2026-10-01T12:00")
    source(conn, "/c/imported.jsonl", [request("t1", t, harness="codex", model="gpt-x")],
           lane="codex", harness="codex", excluded="imported")
    assert reports.daily(conn, tz="UTC", table=table()).rows == []


def test_daily_and_monthly_group_by_day_or_month_and_model(conn):
    source(conn, "/p/a.jsonl", [
        request("r1", at("2026-09-30T12:00")), request("r2", at("2026-10-01T12:00")),
        request("r3", at("2026-10-01T13:00"), model="m-b"),
        request("r4", at("2026-10-02T13:00"))])
    daily = reports.daily(conn, tz="UTC", table=table())
    assert [(r["day"], r["model"], r["requests"]) for r in daily.rows] == [
        ("2026-09-30", "m-a", 1), ("2026-10-01", "m-a", 1), ("2026-10-01", "m-b", 1),
        ("2026-10-02", "m-a", 1)]
    monthly = reports.monthly(conn, tz="UTC", table=table())
    assert [(r["month"], r["model"], r["requests"]) for r in monthly.rows] == [
        ("2026-09", "m-a", 1), ("2026-10", "m-a", 2), ("2026-10", "m-b", 1)]
    assert daily.total["requests"] == monthly.total["requests"] == 4


def test_days_are_cut_in_the_zone_asked_for(conn):
    source(conn, "/p/a.jsonl", [request("r1", at("2026-10-01T23:30"))])
    days = {tz: [r["day"] for r in reports.daily(conn, tz=tz, table=table()).rows]
            for tz in ("UTC", "Asia/Tokyo", "America/New_York")}
    assert days == {"UTC": ["2026-10-01"], "Asia/Tokyo": ["2026-10-02"],
                    "America/New_York": ["2026-10-01"]}


def test_a_day_after_a_daylight_saving_change_is_the_zones_own_day(conn):
    # New York moves from UTC-5 to UTC-4 at 2026-03-08 07:00 UTC
    source(conn, "/p/a.jsonl", [request("r1", at("2026-03-01T04:30")),
                                request("r2", at("2026-03-09T04:30"))])
    report = reports.daily(conn, tz="America/New_York", table=table())
    assert [r["day"] for r in report.rows] == ["2026-02-28", "2026-03-09"]


def test_no_tz_is_local_time(conn):
    before = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Tokyo"
    time.tzset()
    try:
        source(conn, "/p/a.jsonl", [request("r1", at("2026-10-01T23:30"))])
        report = reports.daily(conn, table=table())
        assert report.tz == "local" and [r["day"] for r in report.rows] == ["2026-10-02"]
        # a bare date is local midnight too
        assert reports.daily(conn, since="2026-10-02", table=table()).total["requests"] == 1
    finally:
        if before is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = before
        time.tzset()


def test_since_and_until_are_inclusive_days_in_the_zone(conn):
    source(conn, "/p/a.jsonl", [request(f"r{d}", at(f"2026-10-{d:02d}T12:00"))
                                for d in range(1, 6)])
    report = reports.daily(conn, since="2026-10-02", until="2026-10-04", tz="UTC",
                           table=table())
    assert [r["day"] for r in report.rows] == ["2026-10-02", "2026-10-03", "2026-10-04"]
    assert report.since == at("2026-10-02T00:00") and report.until == at("2026-10-05T00:00")
    # in Tokyo, 2026-10-02 starts at 2026-10-01T15:00 UTC
    tokyo = reports.daily(conn, since="2026-10-02", until="2026-10-02", tz="Asia/Tokyo",
                          table=table())
    assert tokyo.total["requests"] == 1 and tokyo.rows[0]["day"] == "2026-10-02"
    assert reports.daily(conn, since=dt.date(2026, 10, 5), tz="UTC",
                         table=table()).total["requests"] == 1
    with pytest.raises(reports.ReportError):
        reports.daily(conn, since="yesterday", table=table())
    with pytest.raises(reports.ReportError):
        reports.daily(conn, since="2026-10-04", until="2026-10-02", table=table())


def test_filters_by_harness_model_and_project(conn):
    t = at("2026-10-01T12:00")
    source(conn, "/p/a.jsonl", [request("r1", t)], cwd="/w/app", project="key-app")
    source(conn, "/p/b.jsonl", [request("r2", t, model="m-b")], session_id="s2",
           cwd="/w/tool")
    source(conn, "/c/r.jsonl", [request("t1", t, harness="codex", model="gpt-x")],
           lane="codex", harness="codex", session_id="s3", cwd="/w/app-wt",
           project="key-app")

    def count(**filters):
        return reports.daily(conn, tz="UTC", table=table(), **filters).total["requests"]

    assert count() == 3
    assert count(harness="codex") == 1 and count(harness="claude") == 2
    assert count(model="m-b") == 1
    assert count(project="key-app") == 2           # the stored key, across worktrees
    assert count(project="/w/tool") == 1           # a row with no key, by its cwd
    assert count(project="/w/app") == 0            # a keyed row is not found by its cwd
    report = reports.daily(conn, harness="codex", model="gpt-x", table=table())
    assert report.filters == {"harness": "codex", "model": "gpt-x"}
    with pytest.raises(reports.ReportError):
        count(harness="nope")


def test_the_model_is_the_one_asked_for_else_the_one_that_answered(conn):
    source(conn, "/p/a.jsonl", [request("r1", at("2026-10-01T12:00"), model=UNKNOWN,
                                        response_model="m-b")])
    row = one(reports.models_report(conn, table=table()), model="m-b")
    assert row["cost"] == pytest.approx((1000 * 1 + 100 * 5) / 1e6)


def test_sessions_hold_their_subagents_and_child_sessions(conn):
    t = at("2026-10-01T12:00")
    source(conn, "/p/s1.jsonl", [request("r1", t)], session_id="s1", title="Fix the build",
           cwd="/w/app", project="key-app")
    source(conn, "/p/s1/subagents/agent-a.jsonl", [request("r2", t + 60, model="m-b")],
           session_id="s1", kind="subagent", agent_id="a", cwd="/w/app")
    db = "/oc/opencode.db"
    source(conn, db, [request("o1", t + 5, harness="opencode")], lane="opencode",
           harness="opencode", part="ses_p", session_id="ses_p", title="Parent",
           cwd="/w/oc", rank=2)
    source(conn, db, [request("o2", t + 7, harness="opencode")], lane="opencode",
           harness="opencode", part="ses_c", session_id="ses_c", parent_session="ses_p",
           kind="subagent", title="Child", cwd="/w/oc", rank=2)
    report = reports.sessions(conn, table=table())
    assert [(r["harness"], r["session_id"]) for r in report.rows] == [
        ("claude", "s1"), ("opencode", "ses_p")]
    claude = one(report, session_id="s1")
    assert claude["requests"] == 2 and claude["models"] == ["m-a", "m-b"]
    assert (claude["title"], claude["cwd"], claude["project"]) == (
        "Fix the build", "/w/app", "key-app")
    assert (claude["first_ts"], claude["last_ts"]) == (t, t + 60)
    opencode = one(report, session_id="ses_p")
    assert opencode["requests"] == 2 and opencode["title"] == "Parent"


def test_models_report_carries_the_price_provenance(conn):
    source(conn, "/p/a.jsonl", [request("r1", at("2026-10-01T12:00"), model="m-a-20250101"),
                                request("r2", at("2026-10-01T12:01"), model="m-b"),
                                request("r3", at("2026-10-01T12:02"), model="m-b")])
    report = reports.models_report(conn, table=table())
    assert [r["model"] for r in report.rows] == ["m-b", "m-a-20250101"]
    dated = one(report, model="m-a-20250101")
    assert dated["provenance"][0]["rule"] == "date suffix"
    assert dated["provenance"][0]["row"] == "m-a"


def test_projects_group_by_the_stored_key_then_by_cwd(conn):
    t = at("2026-10-01T12:00")
    source(conn, "/p/a.jsonl", [request("r1", t)], cwd="/w/app", project="key-app")
    source(conn, "/p/b.jsonl", [request("r2", t)], session_id="s2", cwd="/w/app-wt",
           project="key-app")
    source(conn, "/p/c.jsonl", [request("r3", t)], session_id="s3", cwd="/w/gone")
    source(conn, "/p/d.jsonl", [request("r4", t, input=5000)], session_id="s4",
           cwd="/w/gone")
    report = reports.projects(conn, table=table())
    assert [(r["project"], r["cwd"], r["requests"]) for r in report.rows] == [
        (None, "/w/gone", 2), ("key-app", None, 2)]


def stored_keys(conn):
    """Files that kept a project key from an earlier pass, beside files with none."""
    t = at("2026-10-01T12:00")
    source(conn, "/p/a.jsonl", [request("r1", t)], cwd="/w/app", project="key-app")
    source(conn, "/p/b.jsonl", [request("r2", t), request("r3", t + 60)], session_id="s2",
           cwd="/w/app-wt", project="key-app")
    source(conn, "/p/c.jsonl", [request("r4", t, input=5000)], session_id="s3",
           cwd="/w/app")
    source(conn, "/p/d.jsonl", [request("r5", t, model="m-b")], session_id="s4")


def test_projects_by_folder_group_by_cwd_alone(conn):
    stored_keys(conn)
    report = reports.projects(conn, by="folder", table=table())
    assert [(r["project"], r["cwd"], r["requests"]) for r in report.rows] == [
        (None, "/w/app", 2), (None, "/w/app-wt", 2), (None, None, 1)]
    assert one(report, cwd="/w/app")["models"] == ["m-a"]
    by_key = reports.projects(conn, table=table())
    assert [(r["project"], r["cwd"], r["requests"]) for r in by_key.rows] == [
        (None, "/w/app", 1), ("key-app", None, 3), (None, None, 1)]
    assert report.total == by_key.total
    assert reports.projects(conn, by="project", table=table()).rows == by_key.rows
    assert reports.projects(conn, by=None, table=table()).rows == by_key.rows
    with pytest.raises(reports.ReportError, match="not a key of the projects report"):
        reports.projects(conn, by="session", table=table())


def test_the_folder_filter_matches_the_cwd_whatever_key_a_file_carries(conn):
    stored_keys(conn)

    def count(**filters):
        return reports.daily(conn, tz="UTC", table=table(), **filters).total["requests"]

    assert count(folder="/w/app") == 2             # the keyed file and the one with none
    assert count(project="/w/app") == 1            # the project filter finds only the latter
    assert count(folder="/w/app-wt") == 2
    assert count(folder="/w/app-wt/") == 2
    assert count(folder="/w/app", project="key-app") == 1
    assert count(folder="/w") == 0                 # a folder above matches nothing under it
    assert count(folder=None) == count(folder="") == 5
    assert reports.daily(conn, folder="/w/app/", table=table()).filters == {
        "folder": "/w/app"}
    for bad in ("app", "./app", "~/app"):
        with pytest.raises(reports.ReportError, match="whole path"):
            count(folder=bad)
    for kind, fn in reports.REPORTS.items():
        if kind == "status":
            continue
        given = {"session": "s2"} if kind in ("session", "requests") else {}
        found = fn(conn, folder="/w/app-wt", table=table(), **given)
        assert found.total["requests"] == 2, kind
        assert fn(conn, folder="/w/gone", table=table(), **given).total["requests"] == 0
    with pytest.raises(reports.ReportError, match="takes no folder"):
        reports.status(conn, folder="/w/app")


def test_report_takes_a_folder_and_by_for_projects(conn, passes):
    stored_keys(conn)
    assert api.report("sessions", folder="/w/app", fresh=False).total["requests"] == 2
    found = api.report("projects", by="folder", folder="/w/app-wt", fresh=False)
    assert [(r["project"], r["cwd"]) for r in found.rows] == [(None, "/w/app-wt")]
    assert found.filters == {"folder": "/w/app-wt"}
    assert reports.read("projects", by="folder").total["requests"] == 5
    with pytest.raises(ValueError, match="not a key of the projects report"):
        api.report("projects", by="model")
    with pytest.raises(ValueError, match="whole path"):
        api.report("daily", folder="app", fresh=False)
    with pytest.raises(ValueError, match="takes no folder"):
        api.report("status", folder="/w/app", fresh=False)
    assert passes == []


def test_a_folder_on_no_index_is_still_checked_and_named(monkeypatch):
    monkeypatch.setattr(reports, "run_pass", lambda *, env=None: None)
    assert not store.index_path().exists()
    found = api.report("projects", by="folder", folder="/w/app/")
    assert found.rows == [] and found.filters == {"folder": "/w/app"}
    with pytest.raises(ValueError, match="whole path"):
        api.report("daily", folder="app")
    with pytest.raises(ValueError, match="takes no folder"):
        api.report("status", folder="/w/app")


def test_no_report_reads_projects(conn, monkeypatch):
    source(conn, "/p/a.jsonl", [request("r1", at("2026-10-01T12:00"))], cwd="/w/app")
    monkeypatch.delitem(sys.modules, "moxi_projects", raising=False)
    for kind, fn in reports.REPORTS.items():
        fn(conn, table=table(),
           **({"session": "s1"} if kind in ("session", "requests") else {}))
    assert "moxi_projects" not in sys.modules
    text = open(reports.__file__, encoding="utf-8").read()
    assert "for_path" not in text.replace("calls `for_path`", "")
    assert "import moxi_projects" not in text and "modules.load" not in text


def test_an_empty_index_gives_an_empty_report(conn):
    for kind, fn in reports.REPORTS.items():
        report = fn(conn, table=table(),
                    **({"session": "s1"} if kind in ("session", "requests") else {}))
        assert report.rows == [] and report.total["requests"] == 0 and report.complete
        if kind != "status":
            assert report.total["cost"] == 0


def test_a_range_read_uses_the_won_and_time_index(conn):
    source(conn, "/p/a.jsonl", [request("r1", at("2026-10-01T12:00"))])
    plan = " | ".join(reports.explain(conn, since="2026-10-01", until="2026-10-30"))
    assert "requests_won_ts" in plan, plan
    plan = " | ".join(reports.explain(conn, since="2026-10-01", tz="America/New_York"))
    assert "requests_won_ts" in plan, plan


# --- one session ---------------------------------------------------------------------------

#: The sums a `session` report's total and a `sessions` row share.
SESSION_SUMS = ("requests", "input", "fresh_input", "cache_read", "cache_write",
                "cache_write_1h", "output", "tokens", "unpriced_tokens", "unknown_rows",
                "complete")


def one_session_index(conn):
    """A Claude session with its main file, a subagent and a workflow agent over three
    days; an OpenCode parent with a child session; and a second Claude session whose
    earlier copy of one request holds that request's claim."""
    day1, day2, day3 = (at(f"2026-10-0{d}T12:00") for d in (1, 2, 3))
    source(conn, "/p/s1.jsonl", [request("r1", day1), request("r2", day2, model="m-b"),
                                 request("r3", day3, output=UNKNOWN)],
           session_id="s1", title="Tidy the parser", cwd="/w/app", size=900)
    source(conn, "/p/s1/subagents/agent-a.jsonl",
           [request("a1", day1 + 60), request("a2", day2 + 60, model="mystery-9"),
            request("shared", day2 + 120)],
           session_id="s1", kind="subagent", agent_id="a", title="Read the tests",
           cwd="/w/app")
    source(conn, "/p/s1/subagents/workflows/w1/agent-b.jsonl",
           [request("b1", day3 + 60, model="m-b", cache_read=400, cache_write=200)],
           session_id="s1", kind="workflow", agent_id="b", cwd="/w/app")
    # a later session holding an earlier copy of `shared`: its copy is the claimed one
    source(conn, "/p/s2.jsonl", [request("shared", day2 + 120), request("s2r", day1 - 60)],
           session_id="s2", title="Another task", cwd="/w/app")
    db = "/oc/opencode.db"
    source(conn, db, [request("o1", day1 + 5, harness="opencode"),
                      request("o2", day2 + 5, harness="opencode", model="m-b")],
           lane="opencode", harness="opencode", part="ses_p", session_id="ses_p",
           title="Parent", cwd="/w/oc", rank=2)
    source(conn, db, [request("o3", day2 + 7, harness="opencode"),
                      request("o4", day3 + 7, harness="opencode")],
           lane="opencode", harness="opencode", part="ses_c", session_id="ses_c",
           parent_session="ses_p", agent_id="ses_c", kind="subagent", title="Child",
           cwd="/w/oc", rank=2)


def assert_session_matches(conn, harness, session_id, **filters):
    """The `session` report's total equals its `sessions` row under the same filters."""
    report = reports.session(conn, session=session_id, harness=harness, tz="UTC",
                             table=table(), **filters)
    found = [row for row in reports.sessions(conn, harness=harness, tz="UTC", table=table(),
                                             **filters).rows
             if row["session_id"] == session_id]
    if not found:
        assert report.rows == [] and report.total["requests"] == 0
        return report
    (whole,) = found
    for name in SESSION_SUMS:
        assert report.total[name] == whole[name], name
    assert report.total["cost"] == pytest.approx(whole["cost"])
    assert sum(r["requests"] for r in report.rows) == whole["requests"]
    assert min(r["first_ts"] for r in report.rows) == whole["first_ts"]
    assert max(r["last_ts"] for r in report.rows) == whole["last_ts"]
    return report


def test_the_session_report_splits_one_session_by_agent_and_model(conn):
    one_session_index(conn)
    report = reports.session(conn, session="s1", tz="UTC", table=table())
    assert report.kind == "session" and report.filters == {"session": "s1"}
    assert [(r["agent"], r["kind"], r["model"], r["requests"]) for r in report.rows] == [
        ("main", "main", "m-a", 2), ("main", "main", "m-b", 1),
        ("a", "subagent", "m-a", 1), ("a", "subagent", "mystery-9", 1),
        ("b", "workflow", "m-b", 1)]
    main = one(report, agent="main", model="m-a")
    assert main["title"] == "Tidy the parser" and main["unknown_rows"] == 1
    assert not main["complete"]
    assert (main["first_ts"], main["last_ts"]) == (at("2026-10-01T12:00"),
                                                   at("2026-10-03T12:00"))
    agent = one(report, agent="a", model="mystery-9")
    assert agent["title"] == "Read the tests" and agent["cost"] is UNKNOWN
    assert agent["unpriced_tokens"] == 1100
    workflow = one(report, agent="b")
    assert workflow["title"] is None and workflow["cache_read"] == 400
    assert workflow["cost"] == pytest.approx(
        (400 * 1 + 400 * 0.1 + 200 * 1.25 + 100 * 5) / 1e6)
    assert report.unpriced_models == ["mystery-9"]
    json.dumps(report.as_dict())


def test_the_session_report_equals_its_sessions_row(conn):
    one_session_index(conn)
    for harness, sid in (("claude", "s1"), ("claude", "s2"), ("opencode", "ses_p")):
        assert_session_matches(conn, harness, sid)
        assert_session_matches(conn, harness, sid, since="2026-10-02", until="2026-10-02")
        assert_session_matches(conn, harness, sid, since="2026-10-02")
        assert_session_matches(conn, harness, sid, model="m-b")


def test_the_session_report_counts_a_child_session_under_its_parent(conn):
    one_session_index(conn)
    report = assert_session_matches(conn, "opencode", "ses_p")
    assert [(r["agent"], r["kind"], r["title"], r["requests"]) for r in report.rows] == [
        ("main", "main", "Parent", 1), ("main", "main", "Parent", 1),
        ("ses_c", "subagent", "Child", 2)]
    # the child's own id is no session of its own
    assert reports.session(conn, session="ses_c", table=table()).rows == []
    narrow = reports.session(conn, session="ses_p", since="2026-10-03", until="2026-10-03",
                             tz="UTC", table=table())
    assert [(r["agent"], r["requests"]) for r in narrow.rows] == [("ses_c", 1)]


def test_an_agent_of_several_titled_files_takes_the_title_of_its_earliest(conn):
    day1, day2 = at("2026-10-01T12:00"), at("2026-10-02T12:00")
    source(conn, "/p/s9.jsonl", [request("m1", day1)], session_id="s9", title="Main")
    # agent x's later file is written first, agent y's earlier one first
    source(conn, "/p/s9/x-2.jsonl", [request("x2", day2)], session_id="s9",
           kind="subagent", agent_id="x", title="Applied the plan")
    source(conn, "/p/s9/x-1.jsonl", [request("x1", day1 + 60, model="m-b")],
           session_id="s9", kind="subagent", agent_id="x", title="Wrote the plan")
    source(conn, "/p/s9/y-1.jsonl", [request("y1", day1 + 90)], session_id="s9",
           kind="subagent", agent_id="y", title="Begun")
    source(conn, "/p/s9/y-2.jsonl", [request("y2", day2 + 90)], session_id="s9",
           kind="subagent", agent_id="y", title="Carried on")
    report = reports.session(conn, session="s9", tz="UTC", table=table())
    assert {(r["agent"], r["title"]) for r in report.rows} == {
        ("main", "Main"), ("x", "Wrote the plan"), ("y", "Begun")}
    # over a range that leaves out the earlier files, the later titles stand
    later = reports.session(conn, session="s9", since="2026-10-02", tz="UTC", table=table())
    assert {(r["agent"], r["title"]) for r in later.rows} == {
        ("x", "Applied the plan"), ("y", "Carried on")}


def test_a_key_another_sessions_copy_claims_is_counted_once(conn):
    one_session_index(conn)
    s1 = reports.session(conn, session="s1", tz="UTC", table=table())
    s2 = reports.session(conn, session="s2", tz="UTC", table=table())
    assert one(s1, agent="a", model="m-a")["requests"] == 1        # a1 only
    assert one(s2, agent="main", model="m-a")["requests"] == 2      # shared and s2r
    daily = reports.daily(conn, harness="claude", tz="UTC", table=table())
    assert s1.total["requests"] == 6 and s2.total["requests"] == 2
    assert s1.total["requests"] + s2.total["requests"] == daily.total["requests"] == 8


def test_one_sessions_read_goes_by_its_files(conn):
    for since, until in ((None, None), ("2026-10-01", "2026-10-30")):
        query = reports._query(since, until, None, None, None, "UTC", "s1")
        sql, args = reports._grouped_sql(query, {"agent": reports._AGENT_SQL}, [], [])
        plan = " | ".join(row[-1] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, args))
        assert "SEARCH r USING PRIMARY KEY (file_id=?)" in plan, plan
        assert "requests_won_ts" not in plan, plan


def test_the_session_report_needs_a_session(conn):
    one_session_index(conn)
    with pytest.raises(ValueError):
        reports.session(conn, table=table())
    with pytest.raises(ValueError):
        reports.session(conn, session="", table=table())
    assert reports.session(conn, session="s1", harness="codex", table=table()).rows == []


def test_report_takes_a_session_for_the_session_kind_only(conn, passes):
    one_session_index(conn)
    report = api.report("session", session="s1", harness="claude", fresh=False)
    assert report.total["requests"] == 6 and report.filters == {"harness": "claude",
                                                                "session": "s1"}
    with pytest.raises(ValueError):
        api.report("session", fresh=False)
    for kind in api.KINDS:
        if kind not in ("session", "terms", "requests"):
            with pytest.raises(ValueError):
                api.report(kind, session="s1", fresh=False)
            with pytest.raises(ValueError):
                reports.read(kind, session="s1")
    # the refusal comes before the pass
    with pytest.raises(ValueError):
        api.report("daily", session="s1")
    assert passes == []


def test_the_session_report_refuses_without_a_session_on_no_index(monkeypatch):
    monkeypatch.setattr(reports, "run_pass", lambda *, env=None: None)
    with pytest.raises(ValueError):
        api.report("session", fresh=False)
    report = api.report("session", session="s1", fresh=False)
    assert report.rows == [] and "no index yet" in report.index["note"]


# --- the cost terms ------------------------------------------------------------------------

#: When the dated override row of `terms_index` starts: m-a is cheaper from then on.
CHANGE = at("2026-10-02T06:00")
CHEAPER = Override(id="m-a", prices={"input": 1.0, "output": 5.0}, start=CHANGE, number=1)
#: The sums a `terms` row shares with the report it is checked against.
TERM_SUMS = ("requests", "input", "fresh_input", "cache_read", "cache_write",
             "cache_write_1h", "output", "tokens", "unpriced_tokens", "unknown_rows",
             "complete", "unpriced_models", "rules")


def terms_index(conn):
    """A Claude session over three days with a fast-mode group, requests on both sides
    of a dated override row, an unknown model and an unknown output count, and a subagent;
    and a Codex session on a priority tier."""
    day1, day2, day3 = (at(f"2026-10-0{d}T12:00") for d in (1, 2, 3))
    source(conn, "/p/s1.jsonl", [
        request("r1", day1, cache_read=200, cache_write=300, cache_write_1h=100),
        request("r2", day1 + 60, speed="fast", cache_read=500, output=400),
        request("r3", day2, output=UNKNOWN),
        request("r4", day2 + 60, model="mystery-9"),
        request("r5", day3, speed="fast")], session_id="s1", title="Main")
    source(conn, "/p/s1/subagents/agent-a.jsonl", [
        request("a1", day2 + 120, model="m-b"), request("a2", day3 + 60, speed="fast")],
        session_id="s1", kind="subagent", agent_id="a", title="Helper")
    source(conn, "/c/r.jsonl", [
        request("t1", day1, harness="codex", model="gpt-x", service_tier="priority",
                cache_read=400),
        request("t2", day3, harness="codex", model="gpt-x")],
        lane="codex", harness="codex", session_id="s2", rank=1)


def assert_adds_up(report):
    """Each group's terms add to its cost, and each row's groups and split to the row's."""
    for row in report.rows:
        priced = [g for g in row["groups"] if g["cost"] is not UNKNOWN]
        for group in priced:
            assert group["cost"] == pytest.approx(sum(t["cost"] for t in group["terms"]),
                                                  abs=1e-12)
        if row["cost"] is UNKNOWN:
            assert priced == [] and row["split"] == []
            continue
        assert abs(row["cost"] - sum(g["cost"] for g in priced)) < 1e-9
        assert abs(row["cost"] - sum(e["cost"] for e in row["split"])) < 1e-9
    total = report.total
    assert abs(total["cost"] - sum(e["cost"] for e in total["split"])) < 1e-9


def assert_same_row(found, expected):
    for name in TERM_SUMS:
        assert found[name] == expected[name], name
    if expected["cost"] is UNKNOWN:
        assert found["cost"] is UNKNOWN
    else:
        assert found["cost"] == pytest.approx(expected["cost"], abs=1e-9)


def test_terms_by_model_equals_the_models_report(conn):
    terms_index(conn)
    current = table(CHEAPER)
    for filters in ({}, {"since": "2026-10-02"}, {"harness": "claude"}, {"model": "m-a"}):
        report = reports.terms(conn, tz="UTC", table=current, **filters)
        models_ = reports.models_report(conn, tz="UTC", table=current, **filters)
        assert {r["model"] for r in report.rows} == {r["model"] for r in models_.rows}
        for row in models_.rows:
            assert_same_row(one(report, model=row["model"]), row)
        assert_same_row({**report.total, "unpriced_models": report.unpriced_models,
                         "rules": report.rules},
                        {**models_.total, "unpriced_models": models_.unpriced_models,
                         "rules": models_.rules})
        assert_adds_up(report)
    report = reports.terms(conn, tz="UTC", table=current)
    # by cost, highest first; the wholly unpriced model last
    costs = [r["cost"] for r in report.rows]
    assert costs[-1] is UNKNOWN and costs[:-1] == sorted(costs[:-1], reverse=True)
    json.dumps(report.as_dict())


@pytest.mark.parametrize("by, kind", [("day", "daily"), ("month", "monthly")])
def test_terms_by_day_or_month_equals_daily_or_monthly_summed_over_models(conn, by, kind):
    terms_index(conn)
    current = table(CHEAPER)
    for tz in ("UTC", "Asia/Tokyo"):
        report = reports.terms(conn, by=by, tz=tz, table=current)
        periods = reports.REPORTS[kind](conn, tz=tz, table=current)
        keys = sorted({r[by] for r in periods.rows})
        assert [r[by] for r in report.rows] == keys
        for key in keys:
            rows = [r for r in periods.rows if r[by] == key]
            found = one(report, **{by: key})
            for name in ("requests", "input", "output", "tokens", "unpriced_tokens",
                         "unknown_rows"):
                assert found[name] == sum(r[name] for r in rows), name
            priced = [r["cost"] for r in rows if r["cost"] is not UNKNOWN]
            if priced:
                assert found["cost"] == pytest.approx(sum(priced), abs=1e-9)
            else:
                assert found["cost"] is UNKNOWN
        assert_adds_up(report)


def test_terms_by_agent_equals_the_session_report_per_agent(conn):
    terms_index(conn)
    current = table(CHEAPER)
    for filters in ({}, {"since": "2026-10-02"}, {"model": "m-a"}):
        report = reports.terms(conn, by="agent", session="s1", tz="UTC", table=current,
                               **filters)
        session = reports.session(conn, session="s1", tz="UTC", table=current, **filters)
        agents = list(dict.fromkeys(r["agent"] for r in session.rows))
        assert [r["agent"] for r in report.rows] == agents
        for agent in agents:
            rows = [r for r in session.rows if r["agent"] == agent]
            found = one(report, agent=agent)
            assert found["kind"] == rows[0]["kind"]
            for name in ("requests", "input", "output", "tokens", "unpriced_tokens",
                         "unknown_rows"):
                assert found[name] == sum(r[name] for r in rows), name
            priced = [r["cost"] for r in rows if r["cost"] is not UNKNOWN]
            assert found["cost"] == pytest.approx(sum(priced), abs=1e-9)
        assert report.total["cost"] == pytest.approx(session.total["cost"], abs=1e-9)
        assert_adds_up(report)
    # `session` alone narrows the model rows to that session
    narrow = reports.terms(conn, session="s1", tz="UTC", table=current)
    assert narrow.filters == {"session": "s1"} and "gpt-x" not in {
        r["model"] for r in narrow.rows}


def test_terms_groups_are_the_reconcile_lines(conn):
    terms_index(conn)
    report = reports.terms(conn, tz="UTC", table=table(CHEAPER))
    m_a = one(report, model="m-a")
    lines = {(g["tier"], g["period"] and g["period"].get("from")): g for g in m_a["groups"]}
    # standard and fast before the dated row; after it, the row's own prices (the row
    # gives no tiers), its three requests standard and fast alike: three lines
    assert set(lines) == {(None, None), ("fast", None), (None, "2026-10-02T06:00:00Z")}
    early_fast = lines[("fast", None)]
    assert early_fast["requests"] == 1 and early_fast["above"] == 0
    # the provider the requests named (Claude's name none), as the lookup takes it
    assert early_fast["provider"] is None and early_fast["model"] == "m-a"
    assert early_fast["provenance"]["layer"] == "shipped"
    assert {t["class"]: t["price_from"] for t in early_fast["terms"]} == {
        "input": "tier", "cache_read": "tier-input", "cache_write_5m": "tier-input",
        "cache_write_1h": "tier-input", "output": "tier"}
    # fresh 500 x 6, read 500 x 0.6, out 400 x 30
    assert early_fast["cost"] == pytest.approx((500 * 6 + 500 * 0.6 + 400 * 30) / 1e6)
    late = lines[(None, "2026-10-02T06:00:00Z")]
    assert late["provenance"]["layer"] == "override" and late["requests"] == 3
    assert late["unknown_rows"] == 1                     # r3's output was not reported
    assert {t["class"]: t["price"] for t in late["terms"]}["input"] == 1.0
    # the fast-mode output is its own entry in the split
    split = {(e["class"], e["tier"]): e for e in m_a["split"]}
    assert split[("output", "fast")]["tokens"] == 400
    assert split[("output", None)]["tokens"] == 100 + 100 + 100
    assert [(e["class"], e["tier"]) for e in m_a["split"]] == [
        ("input", None), ("input", "fast"), ("cache_read", None), ("cache_read", "fast"),
        ("cache_write_5m", None), ("cache_write_1h", None), ("output", None),
        ("output", "fast")]
    assert_adds_up(report)


def test_an_unpriced_model_adds_nothing_to_the_split(conn):
    terms_index(conn)
    report = reports.terms(conn, tz="UTC", table=table(CHEAPER))
    mystery = one(report, model="mystery-9")
    assert mystery["cost"] is UNKNOWN and mystery["split"] == []
    (group,) = mystery["groups"]
    assert group["cost"] is UNKNOWN and group["why"] == "the model table does not know the model"
    assert all(t["cost"] is UNKNOWN for t in group["terms"])
    assert report.total["unpriced_tokens"] == 1100
    # the split holds every priced token: fresh input, cache reads and writes and output
    priced = [r for r in report.rows if r["cost"] is not UNKNOWN]
    assert sum(e["tokens"] for e in report.total["split"]) == sum(
        r["fresh_input"] + r["cache_read"] + r["cache_write"] + r["output"] for r in priced)
    data = report.as_dict()
    row = next(r for r in data["rows"] if r["model"] == "mystery-9")
    assert row["unknown"] == ["cost"] and row["groups"][0]["unknown"] == ["cost"]
    assert row["groups"][0]["terms"][0] == {"class": "input", "tokens": 1000,
                                            "price_from": None,
                                            "unknown": ["price", "cost"]}
    json.dumps(data)


def test_terms_by_agent_matches_the_session_report_where_one_file_is_unpriced(conn):
    # one agent of two titled files; only the first file's requests have tokens in the
    # term `half` has no price for, so the session report prices the second file's group
    t = at("2026-10-01T12:00")
    source(conn, "/p/s9.jsonl", [request("m1", t)], session_id="s9", title="Main")
    source(conn, "/p/s9/x-1.jsonl", [request("x1", t + 60, model="half", cache_write=100)],
           session_id="s9", kind="subagent", agent_id="x", title="Wrote the plan")
    source(conn, "/p/s9/x-2.jsonl", [request("x2", t + 120, model="half")],
           session_id="s9", kind="subagent", agent_id="x", title="Applied the plan")
    report = reports.terms(conn, by="agent", session="s9", tz="UTC", table=table())
    session = reports.session(conn, session="s9", tz="UTC", table=table())
    x = one(report, agent="x")
    assert x["cost"] == pytest.approx(one(session, agent="x")["cost"])
    assert x["cost"] == pytest.approx((1000 * 1 + 100 * 2) / 1e6)
    assert x["unpriced_tokens"] == one(session, agent="x")["unpriced_tokens"] == 1100
    priced, unpriced = sorted(x["groups"], key=lambda g: g["cost"] is UNKNOWN)
    assert unpriced["why"] == "no cache_write_5m price" and priced["requests"] == 1
    assert_adds_up(report)
    # by model, the two files' requests are one group, which the missing price unprices
    assert one(reports.terms(conn, tz="UTC", table=table()), model="half")["cost"] is UNKNOWN


def test_terms_show_a_prefix_row_with_a_digit_in_provenance(conn):
    row = Override(id="m-c1-", match="prefix", prices={"input": 2.0, "output": 4.0},
                   number=1)
    source(conn, "/p/a.jsonl", [request("r1", at("2026-10-01T12:00"), model="m-c1-mini")])
    report = reports.terms(conn, tz="UTC", table=table(row))
    (group,) = one(report, model="m-c1-mini")["groups"]
    assert group["provenance"]["rule"] == "prefix" and group["provenance"]["row"] == "m-c1-"
    assert group["provenance"]["layer"] == "override"
    assert group["cost"] == pytest.approx((1000 * 2 + 100 * 4) / 1e6)


def test_terms_refuse_what_they_cannot_use(conn):
    terms_index(conn)
    with pytest.raises(reports.ReportError, match="needs a session"):
        reports.terms(conn, by="agent", table=table())
    with pytest.raises(reports.ReportError):
        reports.terms(conn, by="week", table=table())


def test_report_takes_by_for_terms_and_agent_for_requests(conn, passes):
    terms_index(conn)
    report = api.report("terms", by="day", tz="UTC", fresh=False)
    assert [r["day"] for r in report.rows] == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert {r["model"] for r in api.report("terms", fresh=False).rows} == {
        "m-a", "m-b", "gpt-x", "mystery-9"}
    by_agent = api.report("terms", by="agent", session="s1", fresh=False)
    assert [r["agent"] for r in by_agent.rows] == ["main", "a"]
    assert api.report("requests", session="s1", agent="a", fresh=False).total[
        "requests"] == 2
    for kind in api.KINDS:
        given = {"session": "s1"} if kind in ("session", "requests") else {}
        if kind not in ("terms", "projects"):
            with pytest.raises(ValueError, match="takes no by"):
                api.report(kind, by="model", fresh=False, **given)
        if kind != "requests":
            with pytest.raises(ValueError, match="takes no agent; only the requests"):
                api.report(kind, agent="a", fresh=False, **given)
    with pytest.raises(ValueError):
        api.report("terms", by="agent", fresh=False)
    with pytest.raises(ValueError):
        reports.read("terms", by="agent")
    with pytest.raises(ValueError):
        api.report("terms", by="year", fresh=False)
    # the refusals come before the pass
    with pytest.raises(ValueError):
        api.report("terms", by="agent")
    with pytest.raises(ValueError):
        api.report("daily", by="day")
    assert passes == []


def test_terms_on_no_index_are_empty_with_a_split(monkeypatch):
    monkeypatch.setattr(reports, "run_pass", lambda *, env=None: None)
    report = api.report("terms", fresh=False)
    assert report.rows == [] and report.total["split"] == [] and report.total["cost"] == 0
    assert "no index yet" in report.index["note"]
    with pytest.raises(ValueError):
        api.report("terms", by="agent", fresh=False)


# --- the API -----------------------------------------------------------------------------

@pytest.fixture
def passes(monkeypatch):
    """Fake the pass a fresh report runs; record each call."""
    calls = []

    def fake(*, env=None):
        calls.append(env)
        conn = store.open_index()
        conn.close()

    monkeypatch.setattr(reports, "run_pass", fake)
    return calls


def test_report_runs_the_pass_first_when_fresh(conn, passes):
    source(conn, "/p/a.jsonl", [request("r1", at("2026-10-01T12:00"))])
    report = api.report("daily", tz="UTC")
    assert len(passes) == 1 and report.index["passed"] and report.index["note"] is None
    assert report.total["requests"] == 1
    report = api.report("models", fresh=False)
    assert len(passes) == 1 and not report.index["passed"]
    assert "no pass ran" in report.index["note"]


def test_report_reads_the_index_as_it_is_when_another_pass_holds_it(conn, monkeypatch):
    source(conn, "/p/a.jsonl", [request("r1", at("2026-10-01T12:00"))])

    def busy(*, env=None):
        raise LockTimeout(store.lock_path(), 4242)

    monkeypatch.setattr(reports, "run_pass", busy)
    report = api.report("sessions")
    assert report.total["requests"] == 1 and not report.index["passed"]
    assert "pid 4242" in report.index["note"]


def test_a_fresh_report_runs_the_real_pass_under_its_env(tmp_path):
    env = {**os.environ, "MOXI_USAGE_HOME": str(tmp_path / "elsewhere")}
    assert store.index_path(env) != store.index_path()
    report = api.report("daily", env=env)
    assert report.index["passed"] and report.index["last_pass"] is not None
    assert store.index_path(env).exists() and not store.index_path().exists()


def test_report_with_no_index_is_empty_and_says_so(monkeypatch):
    monkeypatch.setattr(reports, "run_pass", lambda *, env=None: None)
    report = api.report("daily", fresh=False)
    assert report.rows == [] and "no index yet" in report.index["note"]
    assert not store.index_path().exists()
    with pytest.raises(api.ReportError):
        api.report("weekly", fresh=False)


def test_report_kinds_are_the_designed_nine():
    assert api.KINDS == ("daily", "monthly", "sessions", "models", "projects", "session",
                         "terms", "requests", "status")
    assert tuple(reports.REPORTS) == api.KINDS


# --- the verbs ---------------------------------------------------------------------------

def test_report_verbs_print_json_and_no_pass_skips_the_pass(conn, monkeypatch, capsys):
    write_config('[[model]]\nid = "test-model-1"\ninput = 3.0\noutput = 15.0\n')
    source(conn, "/p/a.jsonl", [request("r1", at("2026-10-01T23:30"), model="mystery-9"),
                                request("r2", at("2026-10-01T12:00"),
                                        model="test-model-1")])

    def refuse(*, env=None):
        raise AssertionError("--no-pass ran the pass")

    monkeypatch.setattr(reports, "run_pass", refuse)
    assert cli.main(["daily", "--no-pass", "--utc", "--json", "--since", "2026-10-01",
                     "--until", "2026-10-01"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["kind"] == "daily" and data["tz"] == "UTC"
    assert data["unpriced_models"] == ["mystery-9"]
    assert [r["day"] for r in data["rows"]] == ["2026-10-01", "2026-10-01"]
    for verb in cli.REPORT_VERBS:
        assert cli.main([verb, "--no-pass"]) == 0
        out = capsys.readouterr().out
        assert "u: 1.1k tokens are unpriced" in out, out and "mystery-9" in out
        assert "no pass ran first" in out
    cli.main(["daily", "--no-pass", "--utc"])
    lines = capsys.readouterr().out.splitlines()
    mystery, priced, total = lines[1:4]
    assert mystery.split()[-2:] == ["unpriced", "u"] and "$" not in mystery
    assert priced.split()[-1] == "$0.0045"
    assert total.split()[-2:] == ["$0.0045", "u"]


def test_report_verbs_refuse_a_bad_date_with_exit_2(monkeypatch, capsys):
    monkeypatch.setattr(reports, "run_pass", lambda *, env=None: None)
    assert cli.main(["daily", "--no-pass", "--since", "soon"]) == 2
    assert "not a date" in capsys.readouterr().err


def test_report_verbs_pass_their_filters(monkeypatch, capsys):
    seen = {}

    def fake(kind, **kwargs):
        seen.update(kwargs, kind=kind)
        return reports.Report(kind=kind, rows=[], total=reports._Row({}).done())

    monkeypatch.setattr(reports, "report", fake)
    assert cli.main(["projects", "--harness", "codex", "--project", "k", "--model", "m",
                     "--since", "2026-10-01", "--until", "2026-10-02", "--utc"]) == 0
    assert seen == {"kind": "projects", "since": "2026-10-01", "until": "2026-10-02",
                    "harness": "codex", "project": "k", "model": "m", "tz": "UTC",
                    "fresh": True}
    cli.main(["daily", "--no-pass"])
    assert seen["fresh"] is False and seen["tz"] is None


def test_terms_verb_prints_each_groups_terms_adding_up_to_its_cost(conn, monkeypatch,
                                                                   capsys):
    write_config('[[model]]\nid = "test-model-1"\ninput = 3.0\noutput = 15.0\n'
                 'cache_read = 0.3\ncache_write_5m = 3.75\ncache_write_1h = 6.0\n'
                 'tiers.fast = {speed = "fast", input = 6.0, output = 30.0}\n')
    t = at("2026-10-01T12:00")
    source(conn, "/p/a.jsonl", [
        request("r1", t, model="test-model-1", input=1_234_567, cache_read=200_000,
                output=4_567),
        request("r2", t + 60, model="test-model-1", speed="fast", input=2_000_000,
                cache_read=1_500_000, cache_write=300_000, output=10_000),
        request("r3", at("2026-10-02T12:00"), model="mystery-9")])
    monkeypatch.setattr(reports, "run_pass", lambda *, env=None: None)
    assert cli.main(["terms", "--no-pass", "--utc"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("test-model-1  override exact") and lines[0].endswith(
        " 1 request")
    assert lines[1].split() == ["input", "1,034,567", "x", "$3.00/M", "=", "$3.10", "(your",
                                "config)"]
    fast = lines.index(next(line for line in lines if "tier fast" in line))
    assert lines[fast + 2].split()[:7] == ["cache", "read", "1,500,000", "x", "$0.30/M",
                                           "=", "$0.4500"]
    assert lines[fast + 6].split() == ["cost", "$3.08"]
    assert lines[fast + 7].split() == ["test-model-1", "total", "$6.31"]
    assert "  unpriced: the model table does not know the model" in lines
    assert lines[lines.index("  by class and tier:") - 1] == "total: 3 requests, $6.31 u"
    assert "    output, fast" in "\n".join(lines) and lines[-1].startswith("no pass ran")
    # by day: a heading per day, its groups under it
    assert cli.main(["terms", "--by", "day", "--no-pass", "--utc"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "2026-10-01: 2 requests, $6.31"
    assert lines[1].startswith("  test-model-1  override exact")
    assert "2026-10-02: 1 request, unpriced u" in lines
    assert "days in UTC" in lines
    # --model, --since and --until: one model's terms on one day
    assert cli.main(["terms", "--model", "test-model-1", "--since", "2026-10-01", "--until",
                     "2026-10-01", "--no-pass", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    (row,) = data["rows"]
    assert row["model"] == "test-model-1" and len(row["groups"]) == 2
    assert sum(t["cost"] for g in row["groups"] for t in g["terms"]) == pytest.approx(
        row["cost"])
    assert data["total"]["split"][0] == {"class": "input", "tier": None,
                                         "tokens": 1_034_567, "cost": pytest.approx(3.103701)}


def test_terms_verb_refuses_agent_without_a_session_with_exit_2(monkeypatch, capsys):
    monkeypatch.setattr(reports, "run_pass", lambda *, env=None: None)
    assert cli.main(["terms", "--by", "agent", "--no-pass"]) == 2
    assert "needs a session id" in capsys.readouterr().err
    with pytest.raises(SystemExit) as caught:
        cli.main(["terms", "--by", "week", "--no-pass"])
    assert caught.value.code == 2


def test_terms_verb_passes_by_and_session(monkeypatch, capsys):
    seen = {}

    def fake(kind, **kwargs):
        seen.update(kwargs, kind=kind)
        return reports.Report(kind=kind, rows=[], total=reports._terms_total().done())

    monkeypatch.setattr(reports, "report", fake)
    assert cli.main(["terms", "--by", "agent", "--session", "s1", "--harness", "claude"]) == 0
    assert seen == {"kind": "terms", "since": None, "until": None, "harness": "claude",
                    "project": None, "model": None, "tz": None, "fresh": True,
                    "by": "agent", "session": "s1"}
    assert "no requests in range" in capsys.readouterr().out


# --- the requests report ------------------------------------------------------------------

def line(n):
    """Where a request or a marker sits in a file: its line."""
    return {"at": {"line": n}}


def compactions(conn, file_id, *found):
    """Compaction marks of one file, each `(seq, ts, trigger, pre_tokens)`."""
    with moxi.store.transaction(conn):
        store.upsert_marks(conn, file_id, [store.MarkRow(seq, "compaction", ts, trigger, pre)
                                           for seq, ts, trigger, pre in found])


def requests_index(conn):
    """A Claude session over two turns with a compaction between them, a fast request and
    an unknown output count, then a subagent; and a resumed copy of it (another session)
    that carries the first three requests and the compaction between them, then one of
    its own."""
    t = at("2026-10-01T12:00")
    main = source(conn, "/p/s1.jsonl", [
        request("r1", t, turn_id="u1", cache_read=300, **line(2)),
        request("r2", t + 60, turn_id="u1", speed="fast", **line(3)),
        request("r3", t + 120, turn_id="u2", output=UNKNOWN, **line(6)),
        request("r4", t + 180, turn_id="u2", model="m-b", input=150_000, **line(7))],
        session_id="s1", title="Main", size=900, marks_read=1)
    compactions(conn, main, (4, t + 90, "auto", 167_412))
    agent = source(conn, "/p/s1/subagents/agent-a.jsonl", [
        request("a1", t + 30, model="m-b", **line(2)),
        request("a2", t + 40, model="m-b", **line(3))],
        session_id="s1", kind="subagent", agent_id="a", title="Helper", marks_read=1)
    resumed = source(conn, "/p/s2.jsonl", [
        request("r1", t, turn_id="u1", cache_read=300, **line(2)),
        request("r2", t + 60, turn_id="u1", speed="fast", **line(3)),
        request("r3", t + 120, turn_id="u2", output=UNKNOWN, **line(6)),
        request("n1", t + 600, turn_id="u9", **line(9))],
        session_id="s2", title="Resumed", size=100, marks_read=1)
    compactions(conn, resumed, (4, t + 90, "auto", 167_412))
    return main, agent, resumed


def sums_of(rows):
    names = ("fresh_input", "cache_read", "cache_write_5m", "cache_write_1h", "output")
    return {name: sum(row[name] for row in rows) for name in names}


def test_requests_give_one_row_per_request_in_order_priced_on_its_own(conn):
    requests_index(conn)
    report = reports.requests(conn, session="s1", tz="UTC", table=table())
    assert report.kind == "requests" and report.filters == {"session": "s1",
                                                             "agent": "main"}
    assert [r["n"] for r in report.rows] == [1, 2, 3, 4]
    assert [r["model"] for r in report.rows] == ["m-a", "m-a", "m-a", "m-b"]
    assert [r["tier"] for r in report.rows] == [None, "fast", None, "above_100k"]
    first = report.rows[0]
    assert (first["context"], first["fresh_input"], first["cache_read"]) == (1000, 700, 300)
    assert first["cost"] == pytest.approx((700 * 3 + 300 * 0.3 + 100 * 15) / 1e6)
    for row in report.rows:
        assert abs(sum(e["cost"] for e in row["split"]) - row["cost"]) < 1e-12
        assert [e["class"] for e in row["split"]] == list(cost.PRICE_TERMS)
    assert report.rows[2]["unknown"] == ["output"] and report.rows[0]["unknown"] == []
    # the total equals the session report's rows for the agent
    session = reports.session(conn, session="s1", tz="UTC", table=table())
    main = [r for r in session.rows if r["agent"] == "main"]
    for name in ("requests", "input", "fresh_input", "cache_read", "cache_write",
                 "output", "unknown_rows", "unpriced_tokens"):
        assert report.total[name] == sum(r[name] for r in main), name
    assert report.total["cost"] == pytest.approx(sum(r["cost"] for r in main))
    data = report.as_dict()
    json.dumps(data)
    assert data["rows"][2]["unknown"] == ["output"]


def test_requests_count_turns_and_place_the_compaction_between_rows(conn):
    requests_index(conn)
    report = reports.requests(conn, session="s1", tz="UTC", table=table())
    assert [(r["turn"], r["turn_start"]) for r in report.rows] == [
        (1, False), (1, False), (2, True), (2, False)]
    total = report.total
    assert total["turns"] == 2 and total["marks_known"] is True
    assert total["marks"] == [{"after": 2, "ts": at("2026-10-01T12:00") + 90,
                               "trigger": "auto", "pre_tokens": 167_412}]
    assert total["claimed_elsewhere"] == 0 and total["copied_marks"] == 0
    # a compaction outside the range is left out; one with no time only in an open range
    late = reports.requests(conn, session="s1", since="2026-10-02", tz="UTC", table=table())
    assert late.rows == [] and late.total["marks"] == []


def test_requests_without_turn_ids_have_null_turns(conn):
    t = at("2026-10-01T12:00")
    source(conn, "/oc/opencode.db", [request("o1", t, harness="opencode"),
                                     request("o2", t + 5, harness="opencode")],
           lane="opencode", harness="opencode", part="ses_p", session_id="ses_p", rank=2)
    report = reports.requests(conn, session="ses_p", tz="UTC", table=table())
    assert [(r["turn"], r["turn_start"]) for r in report.rows] == [(None, False)] * 2
    assert report.total["turns"] is None
    assert report.total["marks_known"] is False          # a row still to be read


def test_a_subagents_rows_by_agent(conn):
    requests_index(conn)
    report = reports.requests(conn, session="s1", agent="a", tz="UTC", table=table())
    assert [r["n"] for r in report.rows] == [1, 2] and report.filters["agent"] == "a"
    assert {r["model"] for r in report.rows} == {"m-b"} and report.total["marks"] == []
    assert reports.requests(conn, session="s1", agent="nobody", table=table()).rows == []


def test_a_resumed_files_copies_count_elsewhere_and_its_copied_compaction_is_not_drawn(
        conn):
    requests_index(conn)
    report = reports.requests(conn, session="s2", tz="UTC", table=table())
    assert [r["model"] for r in report.rows] == ["m-a"] and report.total["requests"] == 1
    assert report.total["claimed_elsewhere"] == 3
    assert report.total["marks"] == [] and report.total["copied_marks"] == 1
    # a compaction with a request of its own file on one side is the file's own
    with moxi.store.transaction(conn):
        conn.execute("DELETE FROM requests WHERE seq = 6 AND file_id = "
                     "(SELECT id FROM files WHERE path = '/p/s2.jsonl')")
    report = reports.requests(conn, session="s2", tz="UTC", table=table())
    assert report.total["copied_marks"] == 0 and len(report.total["marks"]) == 1


def test_markers_with_no_request_between_them_are_one_compaction(conn):
    t = at("2026-10-01T12:00")
    rollout = source(conn, "/c/r.jsonl", [
        request("t1", t, harness="codex", model="gpt-x", **line(3)),
        request("t2", t + 60, harness="codex", model="gpt-x", **line(8))],
        lane="codex", harness="codex", session_id="th1", rank=1, marks_read=1)
    # a `compacted` line, then a `context_compacted` event for the same compaction
    compactions(conn, rollout, (5, t + 30, None, None), (6, t + 31, "auto", None))
    report = reports.requests(conn, session="th1", tz="UTC", table=table())
    assert report.total["marks"] == [{"after": 1, "ts": t + 30, "trigger": "auto",
                                      "pre_tokens": None}]
    # one with no request of its file on either side is kept
    lone = source(conn, "/c/lone.jsonl", [], lane="codex", harness="codex",
                  session_id="th2", rank=1, marks_read=1)
    compactions(conn, lone, (2, None, None, None))
    report = reports.requests(conn, session="th2", table=table())
    assert report.total["marks"] == [{"after": 0, "ts": None, "trigger": None,
                                      "pre_tokens": None}]
    assert reports.requests(conn, session="th2", since="2026-10-01",
                            table=table()).total["marks"] == []


def test_the_window_is_the_reported_one_else_the_reports_table_by_provider(conn):
    t = at("2026-10-01T12:00")
    source(conn, "/c/r.jsonl", [
        request("t1", t, harness="codex", model="m-a", context_window=258_400),
        request("t2", t + 60, harness="codex", model="m-a"),
        request("t3", t + 120, harness="codex", model="zz-1", provider="acme"),
        request("t4", t + 180, harness="codex", model="mystery-9")],
        lane="codex", harness="codex", session_id="th1", rank=1)
    seeded = models.Table(doc({**MODELS, "acme/zz-1": {"input": 1.0, "output": 2.0,
                                                       "window": 64_000}},
                              defaults={"anthropic": ANTHROPIC}))
    report = reports.requests(conn, session="th1", tz="UTC", table=seeded)
    windows = [r["window"] for r in report.rows]
    assert windows[:3] == [258_400, 200_000, 64_000] and windows[3] is UNKNOWN
    # the table in effect does not know zz-1 at all: the window came from the report's own
    assert models.context_window("zz-1") is UNKNOWN
    data = report.as_dict()
    assert "window" not in data["rows"][3] and "window" in data["rows"][3]["unknown"]
    assert "cost" in data["rows"][3]["unknown"]


def context_index(conn):
    """A Codex thread with a reported window, a Claude session with a subagent and a
    later child session, and one id two harnesses hold."""
    t = at("2026-10-01T12:00")
    source(conn, "/c/r.jsonl", [
        request("t1", t, harness="codex", model="m-a", input=10_000, context_window=1000),
        request("t2", t + 60, harness="codex", model="m-a", input=51_680,
                context_window=258_400)],
        lane="codex", harness="codex", session_id="th1", rank=1)
    source(conn, "/p/s1.jsonl", [request("r2", t + 600, input=40_000),
                                 request("r1", t, input=30_000)], session_id="s1")
    source(conn, "/p/s1/subagents/agent-a.jsonl", [request("a1", t + 900, input=7)],
           session_id="s1", kind="subagent", agent_id="a")
    db = "/oc/opencode.db"
    source(conn, db, [request("o1", t, harness="opencode", model="mystery-9", input=600)],
           lane="opencode", harness="opencode", part="ses_p", session_id="ses_p", rank=2)
    source(conn, db, [request("o2", t + 99, harness="opencode", input=9)],
           lane="opencode", harness="opencode", part="ses_c", session_id="ses_c",
           parent_session="ses_p", agent_id="ses_c", kind="subagent", rank=2)
    source(conn, "/p/both.jsonl", [request("b1", t, input=111)], session_id="both")
    source(conn, "/c/both.jsonl", [request("b2", t, harness="codex", input=222)],
           lane="codex", harness="codex", session_id="both", rank=1)


def test_a_sessions_context_is_the_newest_request_of_its_main_file(conn):
    context_index(conn)

    def of(harness, sid):
        return reports.session_context(conn, harness, sid, table=table())

    assert of("codex", "th1") == {"tokens": 51_680, "window": 258_400}
    # the newest by time, not by place in the file, and never a subagent's
    assert of("claude", "s1") == {"tokens": 40_000, "window": 200_000}
    # a model the table does not know has no window; a child session is not the main file
    assert of("opencode", "ses_p") == {"tokens": 600, "window": None}
    assert of("opencode", "ses_c") is None
    assert of("claude", "th1") is None and of("claude", "nobody") is None
    assert of("claude", "") is None
    # with no harness given: the one that holds the id, and nothing when two do
    assert of(None, "s1") == of("claude", "s1")
    assert of(None, "both") is None
    assert of("claude", "both") == {"tokens": 111, "window": 200_000}
    assert of("codex", "both") == {"tokens": 222, "window": 200_000}


def test_a_context_is_none_when_the_newest_request_gave_no_prompt_size(conn):
    t = at("2026-10-01T12:00")
    source(conn, "/p/a.jsonl", [request("r1", t, input=500),
                                request("r2", t + 60, input=UNKNOWN)])
    assert reports.session_context(conn, "claude", "s1", table=table()) is None


def test_a_context_counts_only_a_claimed_request(conn):
    t = at("2026-10-01T12:00")
    # both files hold `shared`, the newest request of each; the earlier file's copy is
    # the claimed one, so the later file's newest claimed request is its own
    source(conn, "/p/old.jsonl", [request("first", t, input=100),
                                  request("shared", t + 60, input=700)], session_id="old")
    source(conn, "/p/new.jsonl", [request("mine", t + 30, input=300),
                                  request("shared", t + 60, input=700)], session_id="new")
    assert reports.session_context(conn, "claude", "old", table=table())["tokens"] == 700
    assert reports.session_context(conn, "claude", "new", table=table())["tokens"] == 300


def test_a_context_read_goes_by_the_sessions_files(conn):
    context_index(conn)
    plan = " | ".join(row[-1] for row in conn.execute(
        "EXPLAIN QUERY PLAN " + reports._NEWEST_SQL, ("s1", "claude")))
    assert "files_session" in plan, plan
    assert "SEARCH r USING PRIMARY KEY (file_id=?)" in plan, plan
    assert "requests_won_ts" not in plan and "SCAN" not in plan, plan


def test_contexts_read_the_index_as_it_is_and_are_none_without_one(conn, monkeypatch):
    context_index(conn)
    monkeypatch.setattr(models, "table", lambda *a, **k: table())
    keys = [("codex", "th1"), ("claude", "s1"), ("claude", "nobody"), ("codex", "th1")]
    assert reports.session_contexts(keys) == {
        ("codex", "th1"): {"tokens": 51_680, "window": 258_400},
        ("claude", "s1"): {"tokens": 40_000, "window": 200_000},
        ("claude", "nobody"): None}
    assert reports.session_contexts([]) == {}
    conn.execute(f"PRAGMA user_version = {store.INDEX_VERSION - 1}")
    conn.commit()
    assert reports.session_contexts(keys[:2]) == dict.fromkeys(keys[:2])
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.INDEX_VERSION - 1


def test_contexts_build_no_index(monkeypatch):
    assert not store.index_path().exists()
    assert reports.session_contexts([("claude", "s1")]) == {("claude", "s1"): None}
    assert not store.index_path().exists()


def test_requests_price_a_request_its_session_group_leaves_unpriced(conn):
    # one file of `half`: one request with tokens in the term it has no price for, and
    # one without; the session report sums both into one group, which stays unpriced
    t = at("2026-10-01T12:00")
    source(conn, "/p/s9.jsonl", [request("h1", t, model="half", cache_write=100),
                                 request("h2", t + 60, model="half")], session_id="s9")
    session = reports.session(conn, session="s9", tz="UTC", table=table())
    assert one(session, agent="main")["cost"] is UNKNOWN
    report = reports.requests(conn, session="s9", tz="UTC", table=table())
    unpriced, priced = report.rows
    assert unpriced["cost"] is UNKNOWN and unpriced["tier"] is None
    assert all(e["cost"] is UNKNOWN for e in unpriced["split"])
    assert priced["cost"] == pytest.approx((1000 * 1 + 100 * 2) / 1e6)
    assert report.total["cost"] == pytest.approx(priced["cost"])
    assert report.total["unpriced_tokens"] == 1100 < one(session, agent="main")[
        "unpriced_tokens"]


def test_requests_are_refused_without_a_session(conn, passes):
    with pytest.raises(reports.ReportError, match="needs a session"):
        reports.requests(conn, table=table())
    with pytest.raises(ValueError, match="needs a session"):
        api.report("requests", fresh=False)
    with pytest.raises(ValueError, match="needs a session"):
        api.report("requests", agent="a")
    assert passes == []


def test_requests_on_no_index_are_empty_and_say_their_marks_are_not_known(monkeypatch):
    monkeypatch.setattr(reports, "run_pass", lambda *, env=None: None)
    report = api.report("requests", session="s1", fresh=False)
    assert report.rows == [] and report.total["marks"] == []
    assert report.total["marks_known"] is False and "no index yet" in report.index["note"]


def test_requests_verb_prints_a_line_per_request_and_the_compaction(conn, monkeypatch,
                                                                    capsys):
    requests_index(conn)
    write_config('[[model]]\nid = "m-a"\ninput = 3.0\noutput = 15.0\nwindow = 200000\n'
                 'cache_read = 0.3\n')
    monkeypatch.setattr(reports, "run_pass", lambda *, env=None: None)
    assert cli.main(["requests", "s1", "--no-pass", "--utc"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("agent main: 4 requests, 2 turns, 1 compaction, window ")
    assert lines[1].split()[:6] == ["#", "time", "turn", "model", "context", "%"]
    assert lines[2].split()[:6] == ["1", "10-01", "12:00:00", "1", "m-a", "1.0k"]
    assert lines[4] == "       -- compaction (auto, 167,412 before) --"
    assert lines[5].split()[0] == "3" and lines[5].endswith(" ?")
    assert lines[7].startswith("total: 4 requests, $")
    assert "?: a token count the harness did not report" in lines[8]
    assert lines[-1].startswith("no pass ran first")
    assert cli.main(["requests", "s2", "--no-pass", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["kind"] == "requests" and data["total"]["claimed_elsewhere"] == 3
    assert len(data["rows"]) == 1, data["rows"]
    assert data["total"]["copied_marks"] == 1, data["total"]
    assert cli.main(["requests", "s2", "--no-pass"]) == 0
    out = capsys.readouterr().out
    assert "3 requests of this agent are counted under another file" in out
    assert "1 compactions copied from another file aren't shown" in out


def test_requests_verb_exits_1_with_no_request_and_2_on_a_bad_argument(conn, monkeypatch,
                                                                       capsys):
    requests_index(conn)
    monkeypatch.setattr(reports, "run_pass", lambda *, env=None: None)
    assert cli.main(["requests", "s1", "--agent", "nobody", "--no-pass"]) == 1
    assert "no request of agent nobody of session s1" in capsys.readouterr().err
    assert cli.main(["requests", "s1", "--since", "2027-01-01", "--no-pass"]) == 1
    assert cli.main(["requests", "s1", "--since", "soon", "--no-pass"]) == 2
    assert "not a date" in capsys.readouterr().err
    with pytest.raises(SystemExit) as caught:
        cli.main(["requests", "--no-pass"])
    assert caught.value.code == 2


def test_requests_verb_passes_session_and_agent(monkeypatch, capsys):
    seen = {}

    def fake(kind, **kwargs):
        seen.update(kwargs, kind=kind)
        return reports._empty("requests", None, None, None, None, None, None,
                              session=kwargs["session"], agent=kwargs["agent"])

    monkeypatch.setattr(reports, "report", fake)
    assert cli.main(["requests", "s1", "--agent", "a", "--harness", "claude"]) == 1
    assert seen == {"kind": "requests", "since": None, "until": None, "harness": "claude",
                    "project": None, "model": None, "tz": None, "fresh": True,
                    "session": "s1", "agent": "a"}
