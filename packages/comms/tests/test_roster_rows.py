"""`api.roster_rows()`, the roster's one row per address, and `moxi comms agents --json`,
which prints it.

The join runs over stubs of `roster`, `session` and `owner` first, which hold the rules
(one row per address, the merge, the sort, each live row's cache fact, the roster handed
back, what an offline daemon and an error each end), then over a rig daemon, and the
verb's JSON is held to the read's rows.
"""

from __future__ import annotations

import dataclasses
import io
import json
import math
import time
from types import SimpleNamespace

import pytest
from conftest import cache_absent, wait_until

from moxi import exit as moxi_exit
from moxi_comms import api, cache, cli
from moxi_comms.client import VersionMismatch

from test_reply_enforcement import PLANNER, REVIEWER, P, R, indexed, send

LEAD = "lead@proj-a"
PLAN = "planner@proj-a"
SID_LEAD = "aaaaaaaa-1111-4111-8111-111111111111"
SID_PLAN = "bbbbbbbb-2222-4222-8222-222222222222"


def agent(qualified, *, adapter="claude-shim", directors=()):
    name, _, scope = qualified.partition("@")
    return api.Agent(qualified=qualified, name=name, scope=scope or "global",
                     tier="chosen", adapter=adapter, directors=directors)


def standing(qualified, kind="agent", module=None):
    name, _, scope = qualified.partition("@")
    return api.Standing(qualified=qualified, name=name, scope=scope or "global", kind=kind,
                        module=module)


class Daemon:
    """`roster`, `session` and `owner` as stubs: what each answers, what each raises, and
    every call made, with its deadline."""

    def __init__(self, bound=(), standing=(), sessions=None, owners=None, fail=None,
                 waiting=()):
        self.bound, self.standing = tuple(bound), tuple(standing)
        self.waiting = tuple(waiting)
        self.sessions = sessions or {}
        self.owners = owners or {}
        self.fail = fail or {}
        self.calls: list = []

    def _called(self, key, deadline):
        self.calls.append((key, deadline))
        if key in self.fail:
            raise self.fail[key]

    def roster(self, *, timeout_s):
        self._called("roster", timeout_s)
        return api.Roster(bound=self.bound, standing=self.standing, waiting=self.waiting)

    def session(self, identity, *, timeout_s):
        self._called(("session", identity), timeout_s)
        answer = self.sessions.get(identity, {"bound": False})
        return answer() if callable(answer) else answer

    def owner(self, session_id, *, timeout_s):
        self._called(("owner", session_id), timeout_s)
        return api.Owner(session_id=session_id, identity=None,
                         awaiting=tuple(self.owners.get(session_id, ())))

    def install(self, monkeypatch):
        for name in ("roster", "session", "owner"):
            monkeypatch.setattr(api, name, getattr(self, name))
        return self


def held(session_id, bound_at=1000.0):
    return {"bound": True, "adapter": "claude-shim", "session_id": session_id,
            "bound_at": bound_at, "cache_source": "/x/transcript.jsonl"}


def two_live(**kw):
    return Daemon(bound=[agent(PLAN), agent(LEAD, directors=("boss",))],
                  sessions={LEAD: held(SID_LEAD, 1000.0), PLAN: held(SID_PLAN, 2000.0)},
                  owners={SID_LEAD: [PLAN]}, **kw)


# --------------------------------------------------------------------------- the join

def test_one_row_per_address_live_first_then_by_name(monkeypatch):
    daemon = two_live(standing=[standing("main@proj-b"), standing("herald"),
                                standing(LEAD)]).install(monkeypatch)
    rows = api.roster_rows()
    assert [(r.qualified, r.live) for r in rows] == [
        (LEAD, True), (PLAN, True), ("herald", False), ("main@proj-b", False)]
    lead = rows[0]
    assert lead == api.RosterRow(
        qualified=LEAD, name="lead", scope="proj-a", live=True, kind="agent", module=None,
        tier="chosen", adapter="claude-shim", directors=("boss",), session_id=SID_LEAD,
        bound_at=1000.0, awaiting=(PLAN,))
    assert (rows[1].kind, rows[1].session_id, rows[1].bound_at, rows[1].awaiting) == (
        None, SID_PLAN, 2000.0, ())
    assert rows[2] == api.RosterRow(
        qualified="herald", name="herald", scope="global", live=False, kind="agent",
        module=None, tier=None, adapter=None)
    assert [call for call, _ in daemon.calls] == [
        "roster", ("session", PLAN), ("owner", SID_PLAN), ("session", LEAD),
        ("owner", SID_LEAD)]


def test_the_deadline_reaches_every_call(monkeypatch):
    daemon = two_live().install(monkeypatch)
    api.roster_rows(timeout_s=2)
    assert len(daemon.calls) == 5 and {d for _, d in daemon.calls} == {2}
    daemon.calls.clear()
    api.roster_rows()
    assert {d for _, d in daemon.calls} == {api.READ_DEADLINE}


def test_a_reserved_send_only_name_held_live_keeps_its_module(monkeypatch):
    Daemon(bound=[agent("console", adapter="send-only")],
           standing=[standing("console", api.SEND_ONLY, "launcher"),
                     standing("relay", api.SEND_ONLY)],
           sessions={"console": {"bound": True, "session_id": None, "bound_at": 5.0}}
           ).install(monkeypatch)
    console, relay = api.roster_rows()
    assert (console.qualified, console.live, console.kind, console.module) == (
        "console", True, "send-only", "launcher")
    assert (console.session_id, console.bound_at, console.awaiting) == (None, 5.0, ())
    assert (relay.live, relay.kind, relay.module) == (False, "send-only", None)


def test_a_standing_row_without_a_module_field_reads_as_none(monkeypatch):
    """A `Standing` from before it had `module` reads the same as one with None."""
    old = SimpleNamespace(qualified="main@proj-a", name="main", scope="proj-a",
                          kind="agent")
    Daemon(bound=[agent("main@proj-a")], standing=[old, standing("other", "agent", "m")],
           sessions={"main@proj-a": held(SID_LEAD)}).install(monkeypatch)
    live, other = api.roster_rows()
    assert (live.qualified, live.kind, live.module) == ("main@proj-a", "agent", None)
    assert (other.qualified, other.module) == ("other", "m")


def test_a_name_with_no_session_id_asks_no_owner(monkeypatch):
    daemon = Daemon(bound=[agent(LEAD), agent(PLAN)],
                    sessions={LEAD: {"bound": True, "session_id": None, "bound_at": 7},
                              PLAN: {"bound": False}}).install(monkeypatch)
    lead, plan = api.roster_rows()
    assert (lead.session_id, lead.bound_at, lead.awaiting) == (None, 7.0, ())
    assert isinstance(lead.bound_at, float)
    assert (plan.live, plan.session_id, plan.bound_at) == (True, None, None)
    assert not [call for call, _ in daemon.calls if call[0] == "owner"]


def test_no_names_is_no_rows(monkeypatch):
    Daemon().install(monkeypatch)
    assert api.roster_rows() == ()


# --------------------------------------------------------------------------- the roster

def test_with_roster_hands_back_the_roster_from_the_one_agents_call(monkeypatch):
    waiting = (api.Waiting(PLAN, 2, 100.0), api.Waiting("curator@proj-a", 1, 50.0))
    daemon = two_live(waiting=waiting).install(monkeypatch)
    rows, found = api.roster_rows(with_roster=True)
    assert [call for call, _ in daemon.calls].count("roster") == 1
    assert found.waiting == waiting and [a.qualified for a in found.bound] == [PLAN, LEAD]
    assert rows == api.roster_rows()


# --------------------------------------------------------------------------- the cache fact

TTL = 300.0


def read_as(kind, *, age=None, ttl=TTL, turned_at=None):
    """A bound `session()` answer whose cache reads `kind`. With `turned_at`, `age_s` is
    worked out at each call from the real clock, as the daemon's read is."""
    def answer():
        return {**held(SID_LEAD), "cache_kind": kind, "cache_ttl": ttl,
                "age_s": age if turned_at is None else time.time() - turned_at}
    return answer


def turned_at():
    # A whole second plus a quarter, so `turned_at + TTL` lies a quarter second from the
    # nearest rounding boundary and a read's latency can't move it across.
    return math.floor(time.time()) - 40 + 0.25


def lead_row(monkeypatch, answer):
    Daemon(bound=[agent(LEAD)], sessions={LEAD: answer}).install(monkeypatch)
    (row,) = api.roster_rows()
    return row


@pytest.mark.parametrize("kind", [cache.WARM, cache.COLD])
def test_a_warm_or_cold_read_gives_until_in_whole_seconds(monkeypatch, kind):
    at = turned_at()
    row = lead_row(monkeypatch, read_as(kind, turned_at=at))
    assert row.cache == api.CacheFact(kind=kind, until=float(round(at + TTL)))
    assert isinstance(row.cache.until, float) and row.cache.until.is_integer()


def test_a_cold_read_gives_the_time_it_went_cold(monkeypatch):
    at = turned_at() - 2 * TTL
    row = lead_row(monkeypatch, read_as(cache.COLD, turned_at=at))
    assert row.cache.kind == cache.COLD and row.cache.until < time.time()
    assert row.cache.until == float(round(at + TTL))


def test_two_reads_a_second_apart_give_the_same_until(monkeypatch):
    at = turned_at()
    first = lead_row(monkeypatch, read_as(cache.WARM, turned_at=at)).cache
    time.sleep(1.0)
    assert lead_row(monkeypatch, read_as(cache.WARM, turned_at=at)).cache == first


@pytest.mark.parametrize("answer", [
    read_as(cache.FRESH, age=0.0), read_as(cache.UNKNOWN, age=None, ttl=None),
    read_as(cache.NO_CACHE, age=None, ttl=None), read_as(cache.WARM, age=None),
    read_as(cache.WARM, age=10.0, ttl=None), read_as(cache.COLD, age=10.0, ttl=None),
    read_as(cache.WARM, age=True), read_as(cache.WARM, age="10"),
    read_as(cache.WARM, age=float("nan")), read_as(cache.WARM, age=10.0, ttl=float("inf")),
    read_as(cache.WARM, age=10 ** 400), read_as("hot", age=10.0),
    lambda: {**held(SID_LEAD)},
], ids=["fresh", "unknown", "no-cache", "no-age", "warm-no-lifetime", "cold-no-lifetime",
        "bool-age", "string-age", "nan-age", "infinite-lifetime", "overflowing-age",
        "another-kind", "no-read"])
def test_any_other_read_gives_no_cache_fact(monkeypatch, answer):
    row = lead_row(monkeypatch, answer)
    assert row.live and row.session_id == SID_LEAD and row.cache is None


def test_a_standing_row_and_an_unbound_answer_have_no_cache_fact(monkeypatch):
    Daemon(bound=[agent(LEAD)], standing=[standing("herald")],
           sessions={LEAD: {"bound": False, "cache_kind": "warm", "age_s": 1.0,
                            "cache_ttl": TTL}}).install(monkeypatch)
    lead, herald = api.roster_rows()
    assert lead.cache is None and herald.cache is None


def test_a_name_with_no_session_id_keeps_its_cache_fact(monkeypatch):
    answer = {**read_as(cache.WARM, age=10.0)(), "session_id": None}
    row = lead_row(monkeypatch, answer)
    assert row.session_id is None and row.cache is not None


# --------------------------------------------------------------------------- failures

# PLAN is the first name read, so a call that followed the failure would show here.
@pytest.mark.parametrize("step", ["roster", ("session", PLAN), ("owner", SID_PLAN)])
def test_the_first_offline_ends_the_read_with_no_further_call(monkeypatch, step):
    daemon = two_live(fail={step: api.DaemonOffline("comms offline")}).install(monkeypatch)
    with pytest.raises(api.DaemonOffline):
        api.roster_rows()
    assert daemon.calls[-1][0] == step


def test_an_error_on_the_roster_raises(monkeypatch):
    daemon = two_live(fail={"roster": api.DaemonError("refused")}).install(monkeypatch)
    with pytest.raises(api.DaemonError):
        api.roster_rows()
    assert [call for call, _ in daemon.calls] == ["roster"]


@pytest.mark.parametrize("step", [("session", LEAD), ("owner", SID_LEAD)])
def test_an_error_on_one_name_empties_only_that_rows_session_fields(monkeypatch, step):
    daemon = two_live(fail={step: api.DaemonError("no")})
    warm = read_as(cache.WARM, age=10.0)
    daemon.sessions = {LEAD: {**warm(), "session_id": SID_LEAD},
                       PLAN: {**warm(), "session_id": SID_PLAN, "bound_at": 2000.0}}
    daemon.install(monkeypatch)
    lead, plan = api.roster_rows()
    assert (lead.live, lead.adapter, lead.directors) == (True, "claude-shim", ("boss",))
    assert (lead.session_id, lead.bound_at, lead.awaiting, lead.cache) == (
        None, None, (), None)
    assert (plan.session_id, plan.bound_at) == (SID_PLAN, 2000.0)
    assert plan.cache is not None


# --------------------------------------------------------------------------- over a rig

def test_the_rows_over_a_rig_daemon(rig):
    reviewer = rig.shim(**REVIEWER)
    planner = rig.shim(**PLANNER)
    assert wait_until(lambda: {P, R} <= set(rig.daemon.bindings)
                      and all(rig.daemon.bindings[q].session_id for q in (P, R)))
    ask = send(rig, P, "do it", "--posture", "once", who=REVIEWER)
    indexed(rig, ask.id)
    rows = {row.qualified: row for row in api.roster_rows()}
    assert [q for q, row in rows.items() if row.live] == [P, R]
    assert [q for q, row in rows.items() if not row.live] == [
        "herald", "main@proj-a", "main@proj-b"]
    assert rows[R].session_id == reviewer.session_id and rows[R].awaiting == (P,)
    assert rows[P].session_id == planner.session_id and rows[P].awaiting == ()
    assert rows[P].adapter == "claude-shim" and isinstance(rows[P].bound_at, float)
    assert rows["herald"].kind == "agent" and rows["herald"].session_id is None


def reads_warm(monkeypatch, age_s, ttl_s):
    """The daemon's cache read answering `warm` at `age_s` with lifetime `ttl_s`."""
    monkeypatch.setattr(cache, "clock_state",
                        lambda adapter, session_id, **kw: cache.Clock(
                            cache.WARM, age_s=age_s, ttl_s=ttl_s))


def test_the_cache_fact_over_a_rig_daemon(rig, monkeypatch):
    reads_warm(monkeypatch, 20.0, TTL)
    rig.shim(**REVIEWER)
    assert wait_until(lambda: R in rig.daemon.bindings)
    before = time.time()
    (row,) = [row for row in api.roster_rows() if row.live]
    assert row.cache.kind == cache.WARM and row.cache.until.is_integer()
    assert before - 20.0 + TTL - 1 <= row.cache.until <= time.time() - 20.0 + TTL + 1


def test_with_cache_absent_every_row_has_no_cache_fact(rig, monkeypatch):
    cache_absent(monkeypatch)
    rig.shim(**REVIEWER)
    rig.shim(**PLANNER)
    assert wait_until(lambda: {P, R} <= set(rig.daemon.bindings))
    rows = api.roster_rows()
    assert [row.qualified for row in rows if row.live] == [P, R]
    assert all(row.cache is None for row in rows)
    assert api.session(R)["cache_kind"] == cache.NO_CACHE


# --------------------------------------------------------------------------- the verb

def agents_json(**kw):
    out = io.StringIO()
    code = cli.main(["agents", "--json"], ctx=cli.Context(out=out, **kw))
    return code, json.loads(out.getvalue())


def test_agents_json_prints_the_reads_rows(rig):
    rig.shim(**REVIEWER)
    assert wait_until(lambda: R in rig.daemon.bindings)
    code, out = rig.cli(["agents", "--json"])
    assert code == moxi_exit.OK
    got = json.loads(out)
    want = [dataclasses.asdict(row) for row in api.roster_rows()]
    assert got == {"ok": True, "rows": json.loads(json.dumps(want))}
    assert got["rows"][0]["qualified"] == R and list(got["rows"][0]) == list(
        api.RosterRow.__dataclass_fields__)
    assert "cache" in got["rows"][0]


def test_agents_json_prints_exactly_what_roster_rows_returns(monkeypatch):
    rows = (api.RosterRow(qualified=LEAD, name="lead", scope="proj-a", live=True,
                          kind=None, module=None, tier="chosen", adapter="codex",
                          directors=("boss",), session_id=SID_LEAD, bound_at=12.5,
                          awaiting=(PLAN,),
                          cache=api.CacheFact(kind="warm", until=1300.0)),
            api.RosterRow(qualified="herald", name="herald", scope="global", live=False,
                          kind="agent", module=None, tier=None, adapter=None))
    asked = []
    monkeypatch.setattr(api, "roster_rows", lambda **kw: asked.append(kw) or rows)
    assert agents_json() == (moxi_exit.OK, {"ok": True, "rows": [{
        "qualified": LEAD, "name": "lead", "scope": "proj-a", "live": True, "kind": None,
        "module": None, "tier": "chosen", "adapter": "codex", "directors": ["boss"],
        "session_id": SID_LEAD, "bound_at": 12.5, "awaiting": [PLAN],
        "cache": {"kind": "warm", "until": 1300.0}, "holder": None}, {
        "qualified": "herald", "name": "herald", "scope": "global", "live": False,
        "kind": "agent", "module": None, "tier": None, "adapter": None, "directors": [],
        "session_id": None, "bound_at": None, "awaiting": [], "cache": None,
        "holder": None}]})
    assert asked == [{"timeout_s": api.READ_DEADLINE}]
    agents_json(deadline=3)
    assert asked[-1] == {"timeout_s": 3}


@pytest.mark.parametrize(("error", "code", "word"), [
    (api.DaemonOffline("comms offline: no daemon"), moxi_exit.TEMPFAIL, "offline"),
    (VersionMismatch("the daemon is older"), moxi_exit.ERROR, "offline"),
    (cli.DaemonTimeout("timeout waiting for agents"), moxi_exit.ERROR, "timeout"),
    (api.DaemonError("method not found: agents"), moxi_exit.ERROR, "failed"),
])
def test_agents_json_failures(monkeypatch, error, code, word):
    def fail(**kw):
        raise error

    monkeypatch.setattr(api, "roster_rows", fail)
    assert agents_json() == (code, {"ok": False, "error": word, "detail": str(error)})


def test_agents_json_with_the_daemon_down_is_offline(rig):
    rig.daemon.stop()
    code, out = rig.cli(["agents", "--json"])
    got = json.loads(out)
    assert code == moxi_exit.TEMPFAIL
    assert (got["ok"], got["error"]) == (False, "offline") and "comms offline" in got["detail"]


def test_agents_takes_seats_or_json_not_both(capsys):
    with pytest.raises(SystemExit) as exit_:
        cli.main(["agents", "--seats", "--json"], ctx=cli.Context(out=io.StringIO()))
    assert exit_.value.code == moxi_exit.USAGE
    assert "not allowed with" in capsys.readouterr().err


def test_a_send_only_row_says_who_holds_it_in_text_and_json(rig):
    """A program's name reads `send-only, registered`, one an older comms kept `send-only,
    nothing holds it`, and every send-only row of `--json` carries `holder`."""
    from moxi_comms import reserved, secrets

    reserved.register("ticker", chain=secrets.from_config())
    reserved.reconcile(declared={"launcher": ["console"]}, chain=secrets.from_config())
    from moxi.store import transaction
    from moxi_comms.store import open_write

    conn = open_write()
    try:
        with transaction(conn) as c:
            c.execute("INSERT INTO reserved(name, state) VALUES ('relay', 'unowned')")
    finally:
        conn.close()
    code, out = rig.cli(["agents"])
    assert code == moxi_exit.OK, out
    assert f"{'ticker':<24} send-only, registered" in out.splitlines()
    assert f"{'console':<24} send-only, reserved for launcher" in out.splitlines()
    assert (f"{'relay':<24} send-only, nothing holds it (kept from an older comms)"
            in out.splitlines())
    code, out = rig.cli(["agents", "--json"])
    holders = {r["qualified"]: r["holder"] for r in json.loads(out)["rows"]
               if r["kind"] == "send-only"}
    assert holders == {"ticker": "program", "console": "module", "relay": None}
    rows = {r.qualified: r for r in api.roster_rows()}
    assert (rows["ticker"].kind, rows["ticker"].module, rows["ticker"].holder) == (
        "send-only", None, "program")
