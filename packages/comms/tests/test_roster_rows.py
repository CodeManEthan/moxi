"""`api.roster_rows()`, the roster's one row per address, and `moxi comms agents --json`,
which prints it.

The join runs over stubs of `roster`, `session` and `owner` first, which hold the rules
(one row per address, the merge, the sort, what an offline daemon and an error each
end), then once over a rig daemon, and the verb's JSON is held to the read's rows.
"""

from __future__ import annotations

import dataclasses
import io
import json
from types import SimpleNamespace

import pytest
from conftest import wait_until

from moxi import exit as moxi_exit
from moxi_comms import api, cli
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

    def __init__(self, bound=(), standing=(), sessions=None, owners=None, fail=None):
        self.bound, self.standing = tuple(bound), tuple(standing)
        self.sessions = sessions or {}
        self.owners = owners or {}
        self.fail = fail or {}
        self.calls: list = []

    def _called(self, key, deadline):
        self.calls.append((key, deadline))
        if key in self.fail:
            raise self.fail[key]

    def roster(self, *, deadline):
        self._called("roster", deadline)
        return api.Roster(bound=self.bound, standing=self.standing)

    def session(self, identity, *, deadline):
        self._called(("session", identity), deadline)
        return self.sessions.get(identity, {"bound": False})

    def owner(self, session_id, *, deadline):
        self._called(("owner", session_id), deadline)
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
    api.roster_rows(deadline=2)
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
    two_live(fail={step: api.DaemonError("no")}).install(monkeypatch)
    lead, plan = api.roster_rows()
    assert (lead.live, lead.adapter, lead.directors) == (True, "claude-shim", ("boss",))
    assert (lead.session_id, lead.bound_at, lead.awaiting) == (None, None, ())
    assert (plan.session_id, plan.bound_at) == (SID_PLAN, 2000.0)


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


def test_agents_json_prints_exactly_what_roster_rows_returns(monkeypatch):
    rows = (api.RosterRow(qualified=LEAD, name="lead", scope="proj-a", live=True,
                          kind=None, module=None, tier="chosen", adapter="codex",
                          directors=("boss",), session_id=SID_LEAD, bound_at=12.5,
                          awaiting=(PLAN,)),)
    asked = []
    monkeypatch.setattr(api, "roster_rows", lambda **kw: asked.append(kw) or rows)
    assert agents_json() == (moxi_exit.OK, {"ok": True, "rows": [{
        "qualified": LEAD, "name": "lead", "scope": "proj-a", "live": True, "kind": None,
        "module": None, "tier": "chosen", "adapter": "codex", "directors": ["boss"],
        "session_id": SID_LEAD, "bound_at": 12.5, "awaiting": [PLAN]}]})
    assert asked == [{"deadline": api.READ_DEADLINE}]
    agents_json(deadline=3)
    assert asked[-1] == {"deadline": 3}


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
