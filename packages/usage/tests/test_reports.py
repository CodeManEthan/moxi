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


def test_no_report_reads_projects(conn, monkeypatch):
    source(conn, "/p/a.jsonl", [request("r1", at("2026-10-01T12:00"))], cwd="/w/app")
    monkeypatch.delitem(sys.modules, "moxi_projects", raising=False)
    for kind, fn in reports.REPORTS.items():
        fn(conn, table=table(), **({"session": "s1"} if kind == "session" else {}))
    assert "moxi_projects" not in sys.modules
    text = open(reports.__file__, encoding="utf-8").read()
    assert "for_path" not in text.replace("calls `for_path`", "")
    assert "import moxi_projects" not in text and "modules.load" not in text


def test_an_empty_index_gives_an_empty_report(conn):
    for kind, fn in reports.REPORTS.items():
        report = fn(conn, table=table(), **({"session": "s1"} if kind == "session" else {}))
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
        if kind != "session":
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


def test_report_kinds_are_the_designed_seven():
    assert api.KINDS == ("daily", "monthly", "sessions", "models", "projects", "session",
                         "status")
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
