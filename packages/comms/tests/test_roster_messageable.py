"""`moxi comms agents` and the session-start roster show the names an agent can message.

Against the daemon's `agents` answer:
`{"bindings": [...], "waiting": {name: {"count", "oldest"}}, "pending": {},
"unanswered": [...], "standing": [...]}`. The daemon's half of that answer is tested
with the queue; these tests hand the renderers a fake answer of that shape, except the
tests that ask the daemon itself.

The default view (the verb and the snapshot) is one row per live binding, then one row
per reserved send-only name, and nothing else. `--seats` is the operator's view: the same
rows, one waiting block, and the unanswered rows. The operator is a row like any other.
"""

from __future__ import annotations

import json

from conftest import wait_until

from moxi_comms import cli, roster_view
from moxi_comms.adapters import codex, opencode, shim_roster
from moxi_comms.client import DaemonClient
from moxi_comms.paths import daemon_socket

NOW = 1_800_000_000.0
DAY, HOUR = 86400, 3600

OPERATOR = {"qualified": "operator", "name": "operator", "scope": "global",
            "tier": "standing", "adapter": "claude-shim"}
EPHEMERAL = {"qualified": "agent-3", "name": "agent-3", "scope": "global",
             "tier": "ephemeral", "adapter": "claude-shim"}
REVIEWER = {"qualified": "reviewer@proj-b", "name": "reviewer", "scope": "proj-b",
            "tier": "chosen", "adapter": "codex"}


def answer(bindings=(OPERATOR, EPHEMERAL, REVIEWER), **over):
    """An `agents` answer in its one shape."""
    info = {
        "bindings": [dict(b) for b in bindings],
        "waiting": {"reviewer@proj-b": {"count": 3, "oldest": NOW - (2 * DAY + 3 * HOUR)},
                    "curator": {"count": 1, "oldest": NOW - 42 * 60}},
        "pending": {},
        "unanswered": [{"owing": "agent-3", "author": "operator", "message_id": "9001",
                        "for": 40 * 60}],
    }
    info.update(over)
    return info


DEFAULT = ("operator                 standing   adapter claude-shim\n"
           "agent-3                  ephemeral  adapter claude-shim\n"
           "reviewer@proj-b          chosen     adapter codex")


# ------------------------------------------------------------------- the default view

def test_the_default_view_is_the_messageable_names_and_nothing_else():
    """One row per live binding, `agent-N` and the operator included; no waiting mail,
    no unanswered rows, no keeps, no slot."""
    out = roster_view.render_agents(answer(), now=NOW)
    assert out == DEFAULT
    for absent in ("waiting", "curator", "unanswered", "9001",
                   "slot", "keeps", "pending", "held"):
        assert absent not in out, absent


def test_a_roster_of_no_bindings_is_the_empty_roster():
    out = roster_view.render_agents(answer(bindings=()), now=NOW)
    assert out == "no live bindings — nothing is addressable right now."


def test_the_default_view_reads_bindings_alone():
    """The snapshot and the delta render the default view, which needs nothing but
    `bindings`: an answer with no other key renders."""
    assert roster_view.render_agents({"bindings": [OPERATOR]}) == (
        "operator                 standing   adapter claude-shim")


def test_the_operator_is_absent_when_unbound():
    out = roster_view.render_agents(answer(bindings=(EPHEMERAL,)), now=NOW)
    assert "operator" not in out


# ------------------------------------------------------------------- --seats

SEATS = (
    "operator                 standing   adapter claude-shim\n"
    "agent-3                  ephemeral  adapter claude-shim\n"
    "reviewer@proj-b          chosen     adapter codex\n"
    "\n"
    "waiting (queued for each name until its next session start, /clear, compaction, or "
    "a turn of its own on a warm cache):\n"
    "  curator                  1 message(s), oldest waiting 42m\n"
    "  reviewer@proj-b          3 message(s), oldest waiting 2d 3h\n"
    "unanswered: agent-3 owes operator msg 9001, 40m")


def test_seats_is_every_binding_then_one_waiting_block_then_unanswered():
    """Waiting rows show count and oldest wait."""
    assert roster_view.render_agents(answer(), seats=True, now=NOW) == SEATS


def test_the_waiting_header_says_what_a_row_means():
    assert roster_view.WAITING_HEADER == (
        "waiting (queued for each name until its next session start, /clear, compaction, "
        "or a turn of its own on a warm cache):")


def test_the_old_headers_and_rows_are_gone():
    out = roster_view.render_agents(answer(), seats=True, now=NOW)
    assert "pending (waiting in #names for a matching bind):" not in out
    assert "held for next turn" not in out


def test_no_waiting_block_when_nothing_waits():
    out = roster_view.render_agents(answer(waiting={}, unanswered=[]),
                                    seats=True, now=NOW)
    assert out == ("operator                 standing   adapter claude-shim\n"
                   "agent-3                  ephemeral  adapter claude-shim\n"
                   "reviewer@proj-b          chosen     adapter codex")


def test_now_defaults_to_the_wall_clock(monkeypatch):
    monkeypatch.setattr(roster_view.time, "time", lambda: NOW + HOUR)
    out = roster_view.render_agents(answer(), seats=True)
    assert "  curator                  1 message(s), oldest waiting 1h 42m" in out


def test_seats_reads_waiting_and_not_pending():
    """`pending` is always empty in the answer and nothing renders it."""
    out = roster_view.render_agents(answer(pending={"ghost": 7}), seats=True, now=NOW)
    assert "ghost" not in out


# ------------------------------------------------------------------- the snapshot

def test_the_snapshot_body_is_the_default_view():
    envelope, online = shim_roster.render_snapshot(answer(), env={})
    assert online
    assert envelope.split("\n\n", 2)[2] == f"{shim_roster.LABEL}\n{DEFAULT}"


def test_codex_and_opencode_layer_one_carry_the_same_view():
    for module in (codex, opencode):
        text = module.layer_one_text(answer())
        assert text.endswith(f"{shim_roster.LABEL}\n{DEFAULT}"), module.__name__


def test_the_race_line_follows_an_empty_roster():
    """A stamped session that finds no binding at all gets the race line."""
    env = {"MOXI_COMMS_NAME": "rev"}
    envelope, _ = shim_roster.render_snapshot(answer(bindings=()), env=env)
    assert envelope.endswith(shim_roster.OWN_BINDING_RACE.format(name="rev"))
    envelope, _ = shim_roster.render_snapshot(answer(bindings=(OPERATOR,)), env=env)
    assert "may not have completed registration" not in envelope


def test_the_verb_and_the_snapshot_print_the_same_from_one_answer(rig, monkeypatch):
    """Snapshot equals verb, through the real CLI path and the real hook, both handed
    the same answer."""
    real = DaemonClient.call

    def query(self, kind, params=None, **kw):
        return answer() if kind == "agents" else real(self, kind, params, **kw)

    monkeypatch.setattr(DaemonClient, "call", query)
    code, out = rig.cli(["agents"])
    assert code == 0
    envelope, online = shim_roster.snapshot(daemon_socket(), env={})
    assert online and envelope.split("\n\n", 2)[2] == f"{shim_roster.LABEL}\n{out.rstrip()}"
    assert out.rstrip("\n") == DEFAULT


# ------------------------------------------------------------------- usable

#: An older comms' `shim_roster.usable`, verbatim: what a Codex or OpenCode adapter
#: started before the deploy still runs.
def older_usable(info) -> bool:
    return (isinstance(info, dict) and isinstance(info.get("bindings"), list)
            and isinstance(info.get("pending"), dict))


def test_usable_takes_an_answer_with_bindings_and_no_pending():
    assert shim_roster.usable({"bindings": []})
    assert shim_roster.usable({"bindings": [OPERATOR], "waiting": {}})
    assert shim_roster.usable(answer())


def test_usable_refuses_what_the_default_view_cannot_render():
    for info in (None, [], {}, {"pending": {}}, {"bindings": {}}, {"bindings": None},
                 {"error": "unknown query"}):
        assert not shim_roster.usable(info), info


def test_an_older_usable_accepts_the_answer_because_it_keeps_an_empty_pending():
    """The answer keeps `pending: {}`
    so an adapter started before the deploy renders its roster at /clear, /compact and
    resume instead of the offline line. Without the key, the old check fails."""
    info = answer()
    assert info["pending"] == {}
    assert older_usable(info)
    del info["pending"]
    assert not older_usable(info), "why the key is kept"


# ------------------------------------------------------------------- the deltas

def test_roster_of_is_every_binding():
    assert shim_roster.roster_of(answer()) == {
        "operator": {"tier": "standing", "adapter": "claude-shim"},
        "agent-3": {"tier": "ephemeral", "adapter": "claude-shim"},
        "reviewer@proj-b": {"tier": "chosen", "adapter": "codex"}}


def fake_fetch(monkeypatch, info):
    monkeypatch.setattr(shim_roster, "fetch", lambda *a, **k: info)


def test_an_operator_join_and_leave_ride_the_delta_like_any_name(monkeypatch, tmp_path):
    """The operator's join and leave ride the delta as any name's do."""
    state = tmp_path / "roster.json"
    shim_roster.write_state(state, {})
    fake_fetch(monkeypatch, answer(bindings=(OPERATOR,)))
    envelope, _ = shim_roster.delta(None, state, env={})
    assert envelope == ("(comms roster update: operator [standing] is now reachable — "
                        "`moxi comms agents` is always current.)")
    fake_fetch(monkeypatch, answer(bindings=()))
    envelope, _ = shim_roster.delta(None, state, env={})
    assert envelope == "(comms roster update: operator left — `moxi comms agents` is always current.)"
    assert json.loads(state.read_text()) == {}


def test_the_unanswered_line_still_rides_the_authors_delta(monkeypatch, tmp_path):
    """`UNANSWERED_LINE` is not roster content: it stays, reading `unanswered`."""
    state = tmp_path / "roster.json"
    shim_roster.write_state(state, shim_roster.roster_of(answer()))
    fake_fetch(monkeypatch, answer())
    envelope, _ = shim_roster.delta(None, state, env={"MOXI_COMMS_NAME": "operator",
                                                      "MOXI_COMMS_GLOBAL": "1"})
    assert envelope == "(comms: agent-3 ended a turn without answering your msg 9001.)"


# ------------------------------------------------------------------- send-only names

CONSOLE = {"qualified": "console", "name": "console", "scope": "global",
           "kind": "send-only", "module": "launcher"}
RELAY = {"qualified": "relay", "name": "relay", "scope": "global", "kind": "send-only",
         "module": None}
HERALD = {"qualified": "herald", "name": "herald", "scope": "global", "kind": "agent",
          "module": None}
SEND_ONLY_ROWS = [
    "console                  send-only, reserved for launcher",
    "relay                    send-only, no module holds it (kept from an older comms)"]


def test_a_reserved_name_is_a_send_only_row_after_the_bindings():
    """A send-only name follows the bindings in both views; a standing agent row isn't
    one (a standing name shows when it is bound)."""
    info = answer(standing=[HERALD, CONSOLE, RELAY])
    assert roster_view.render_agents(info, now=NOW).splitlines() == [
        *DEFAULT.splitlines(), *SEND_ONLY_ROWS]
    seats = roster_view.render_agents(info, seats=True, now=NOW).splitlines()
    assert seats[:5] == [*DEFAULT.splitlines(), *SEND_ONLY_ROWS]


def test_send_only_rows_show_below_the_empty_roster():
    info = answer(bindings=(), standing=[CONSOLE])
    assert roster_view.render_agents(info, now=NOW).splitlines() == [
        roster_view.NO_BINDINGS, SEND_ONLY_ROWS[0]]


def test_a_standing_list_of_the_wrong_shape_renders_no_rows():
    for bad in ({"console": CONSOLE}, ["console"], None):
        assert roster_view.render_agents(answer(standing=bad), now=NOW) == DEFAULT


def test_the_daemon_lists_a_reserved_name_and_the_verb_shows_it(rig):
    """The daemon's `standing` list carries an active and an unowned reserved name as
    send-only; a conflict row and a freed one aren't listed, and `agents` renders it."""
    from moxi_comms import reserved, secrets
    from moxi_comms.store import open_write
    from moxi.store import transaction

    reserved.reconcile(declared={"launcher": ["console"]}, chain=secrets.from_config())
    conn = open_write()
    try:
        with transaction(conn) as c:
            c.execute("INSERT INTO reserved(name, state) VALUES ('relay', 'unowned')")
            c.execute("INSERT INTO reserved(name, module, state, note) "
                      "VALUES ('held', 'pager', 'conflict', 'a live binding holds it')")
            c.execute("INSERT INTO reserved(name, module, state, freed_at) VALUES "
                      "('old', 'gone', 'active', '2026-01-01T00:00:00Z')")
    finally:
        conn.close()
    got = {s["qualified"]: s for s in rig.daemon._agents_query({})["standing"]}
    assert got["console"] == CONSOLE and got["relay"] == RELAY
    assert got["herald"]["kind"] == "agent"
    assert "held" not in got and "old" not in got
    code, out = rig.cli(["agents"])
    assert code == 0, out
    assert out.splitlines()[-2:] == SEND_ONLY_ROWS


# ------------------------------------------------------------------- help

def _agents_help():
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    verb = next(c.help for c in sub._choices_actions if c.dest == "agents")
    seats = next(a.help for a in sub.choices["agents"]._actions if "--seats" in a.option_strings)
    return verb, seats


def test_the_agents_help_says_who_you_can_message():
    assert _agents_help() == (
        "who you can message",
        "operator view: also show the mail waiting for each name")


# ------------------------------------------------------------------- the daemon's answer

def test_the_daemons_answer_keeps_an_empty_pending_beside_waiting(rig):
    """The daemon itself: with mail waiting for an unbound name, the answer counts it
    under `waiting`, keeps `pending` empty, and an older `usable` accepts it."""
    rig.shim(name="planner", scope="proj-a")
    code, out = rig.cli(["send", "--posture", "none", "--new-name", "curator@proj-a",
                         "waiting"],  name="planner", scope="proj-a")
    assert code == 0, out

    def info():
        c = DaemonClient(daemon_socket()).connect()
        try:
            return c.call("agents")
        finally:
            c.close()

    assert wait_until(lambda: "curator@proj-a" in (info().get("waiting") or {}))
    got = info()
    assert got["pending"] == {}
    assert got["waiting"]["curator@proj-a"]["count"] == 1
    assert older_usable(got) and shim_roster.usable(got)
