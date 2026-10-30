"""Send-only identities: `relay@proj-a`, a standing row of kind `send-only` that a
module's own process holds as a live binding, sends through its claim socket and is
never a recipient, bound or not. On both backends."""

from __future__ import annotations

import io
import os
import sqlite3
import subprocess
import sys

import pytest

from conftest import queued, wait_until, warm_session, write_config, write_transcript
from moxi_comms import attribution, cli, directory, machine_line, provision
from moxi_comms.claimhold import CLAIM_SOCKET_ENV, NOT_INSIDE, ClaimServer, call
from moxi_comms.client import DaemonClient, DaemonError
from moxi_comms.record import Message
from moxi_comms.paths import daemon_socket, comms_db

HOUR = 3600
CONSOLE = "relay@proj-a"
PLANNER = "planner@proj-a"
#: The words the refusal names the console with: config.toml's `[policy] labels`.
LABEL = "the module relay's send-only identity"
REFUSAL = (f"relay@proj-a is {LABEL} and receives no "
           "mail; nothing was sent. A brief that wants a report names whom to report to.")
#: A sender for the verbs: a name no adapter binds, sent as from a session holding it
#: offline on its own slot (the rig's `session_holding`).
LEAD = dict(name="captain", scope="proj-a")


# --------------------------------------------------------------------------- helpers

@pytest.fixture(autouse=True)
def console_label(env):
    """The box names its console in `[policy] labels`, as the operator's config does."""
    write_config(None, f'[policy.labels]\n"{CONSOLE}" = "{LABEL}"\n')


def provision_console(rig, out=None):
    """The provisioner's act, as the lead runs it at land: the subcommand, against the
    rig's record and store."""
    lines = out if out is not None else []
    code = provision.send_only_main([CONSOLE], record=rig.record,
                                    store=rig.store, out=lines.append)
    assert code == 0, lines
    return lines


@pytest.fixture
def console(rig):
    """`relay@proj-a` provisioned send-only, nobody holding it."""
    provision_console(rig)
    return rig


@pytest.fixture
def server(console):
    """A send-only console's binding: a `ClaimServer` with its own daemon arm."""
    srv = ClaimServer("relay", "proj-a", kind="send-only", arm=True).start()
    assert wait_until(lambda: CONSOLE in console.daemon.bindings), srv.refusal
    yield srv
    srv.stop()
    wait_until(lambda: CONSOLE not in console.daemon.bindings)


class Pushes:
    """A raw send-only binding of console that records every frame the daemon pushes."""

    def __init__(self):
        self.frames = []
        self.client = DaemonClient(daemon_socket(), on_push=self.frames.append).connect()
        self.reply = self.client.register(name="relay", scope="proj-a", project="proj-a",
                                          adapter="send-only")

    def delivers(self):
        return [f for f in self.frames if f.get("op") == "deliver"]

    def close(self):
        self.client.close()


@pytest.fixture
def pushes(console):
    p = Pushes()
    yield p
    p.close()
    wait_until(lambda: CONSOLE not in console.daemon.bindings)


def history(rig):
    return rig.record.history(rig.names_channel)


def register(**kw):
    c = DaemonClient(daemon_socket()).connect()
    try:
        return c.register(**kw)
    finally:
        c.close()


def got(shim, message_id):
    """Was this message handed to the shim?"""
    return any(p["message_id"] == message_id for p in shim.queue)


def nothing_kept_for(rig, qualified):
    d = rig.daemon
    return not queued(d, qualified)


def console_cli(rig, srv, argv):
    """`comms` run by the console's own process, the binding's `self` root."""
    return rig.cli(argv, env={CLAIM_SOCKET_ENV: str(srv.socket_path)},
                   project="proj-a")


# --------------------------------------------------------------------------- the register

def test_a_send_only_register_is_refused_for_a_row_that_is_not_send_only(console):
    for kw in (dict(name="planner", scope="proj-a"),       # a chosen name, no row
               dict(name="herald", global_=True),         # a global standing row
               dict()):                                    # the auto-namer
        with pytest.raises(DaemonError) as e:
            register(adapter="send-only", **kw)
        assert "is not a send-only identity" in str(e.value), kw
    assert not any(b.adapter == "send-only" for b in console.daemon.bindings.values())


@pytest.mark.parametrize("kind", ["claude-shim", "codex", "opencode"])
def test_an_agent_kind_register_of_console_is_refused(console, kind):
    with pytest.raises(DaemonError) as e:
        register(name="relay", scope="proj-a", project="proj-a", adapter=kind)
    assert (f"relay@proj-a is a send-only identity and receives no mail: it binds "
            f"only with the send-only adapter kind, never as {kind}") in str(e.value)
    assert CONSOLE not in console.daemon.bindings


def test_the_send_only_kind_binds_console_and_only_agents_seats_shows_it(server, console):
    """A send-only binding is not a name anyone can message, so the default view
    leaves it out; the operator's `--seats` view lists it."""
    b = console.daemon.bindings[CONSOLE]
    assert b.adapter == "send-only" and b.tier == "standing"
    code, out = console.cli(["agents"], **LEAD)
    assert code == 0 and CONSOLE not in out and "send-only" not in out
    code, out = console.cli(["agents", "--seats"], **LEAD)
    assert code == 0 and CONSOLE in out and "adapter send-only" in out


# --------------------------------------------------------------------------- the verbs

@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("tag,scope", [(CONSOLE, "proj-a"),
                                       (CONSOLE, "proj-b")])
def test_a_send_to_console_is_refused_before_posting(console, request, bound, tag, scope):
    """The qualified relay meets the send-only refusal from every scope. A bare
    `relay` is the global identity, not this one."""
    if bound:
        request.getfixturevalue("server")
    before = len(history(console))
    code, out = console.cli(["send", "--posture", "none", tag, "report back"],
                            name="captain", scope=scope)
    assert code == cli.EXIT_REFUSED
    assert out.strip() == REFUSAL
    assert len(history(console)) == before


@pytest.mark.parametrize("bound", [False, True])
def test_a_send_naming_console_among_others_is_refused_whole(console, request, bound):
    if bound:
        request.getfixturevalue("server")
    console.shim(name="planner", scope="proj-a")
    before = len(history(console))
    code, out = console.cli(["send", "--posture", "none", f"{PLANNER},{CONSOLE}", "x"], **LEAD)
    assert code == cli.EXIT_REFUSED and REFUSAL in out
    assert len(history(console)) == before


@pytest.mark.parametrize("bound", [False, True])
def test_a_reply_to_consoles_message_is_refused_before_posting(console, request, bound):
    """The console's own brief, then a reply to it: resolved to console, refused."""
    srv = request.getfixturevalue("server")
    console.shim(name="planner", scope="proj-a")
    code, out = console_cli(console, srv, ["send", PLANNER, "your brief", "--posture", "none"])
    assert code == 0, out
    brief = history(console)[-1]
    if not bound:
        srv.stop()
        assert wait_until(lambda: CONSOLE not in console.daemon.bindings)
    before = len(history(console))
    code, out = console.cli(["reply", brief.id, "done"], **LEAD)
    assert code == cli.EXIT_REFUSED
    assert out.splitlines()[-1] == REFUSAL
    code, out = console.cli(["send", "--reply-to", brief.id, CONSOLE, "done"], **LEAD)
    assert code == cli.EXIT_REFUSED and out.strip() == REFUSAL
    assert len(history(console)) == before


# --------------------------------------------------------------------------- the router

@pytest.mark.parametrize("next_turn", [False, True])
def test_a_record_message_naming_console_is_delivered_to_nobody_and_pended_for_nobody(
        console, next_turn):
    planner = console.shim(name="planner", scope="proj-a")
    body = machine_line.compose([CONSOLE, PLANNER], "by hand", next_turn=next_turn)
    msg = console.post_as("newbie", body, raw=True)
    if next_turn:
        assert wait_until(lambda: msg.id in queued(console.daemon, PLANNER))
    else:
        assert wait_until(lambda: got(planner, msg.id))
    assert nothing_kept_for(console, CONSOLE)
    assert console.daemon.send_only_seen == {msg.id: None}
    # A rescan re-reads the same line: still nobody.
    console.daemon.rescan()
    assert nothing_kept_for(console, CONSOLE)
    agents = DaemonClient(daemon_socket()).connect()
    try:
        view = agents.query("agents")
    finally:
        agents.close()
    assert CONSOLE not in view["waiting"] and view["pending"] == {}


def test_no_deliver_frame_reaches_the_send_only_binding(console):
    planner = console.shim(name="planner", scope="proj-a")
    # Before the bind: a register's replay hands nothing either.
    early = console.post_as("newbie", machine_line.compose([CONSOLE], "before the bind"), raw=True)
    assert wait_until(lambda: early.id in console.daemon.send_only_seen)
    p = Pushes()
    try:
        late = console.post_as("newbie", machine_line.compose([CONSOLE, PLANNER], "after the bind"), raw=True)
        assert wait_until(lambda: got(planner, late.id))
        console.daemon.rescan()
        assert p.delivers() == []
        assert nothing_kept_for(console, CONSOLE)
        # And the one place every delivery passes: it sends a send-only binding nothing.
        b = console.daemon.bindings[CONSOLE]
        author = console.daemon.attribute(late, live=False)
        console.daemon._deliver(b, late, machine_line.parse(late.content), author,
                                backlog=False)
        assert not wait_until(p.delivers, timeout=0.5)
    finally:
        p.close()


def test_mail_pended_for_console_before_its_row_was_send_only_goes_at_its_bind(rig):
    """A box that pended mail for relay@proj-a while it was an ordinary name, then
    provisioned the row: the send-only bind keeps none of it."""
    code, out = rig.cli(["send", "--posture", "none", "--new-name", CONSOLE, "before"], **LEAD)
    assert code == 0, out
    msg = history(rig)[-1]
    assert wait_until(lambda: msg.id in queued(rig.daemon, CONSOLE))
    provision_console(rig)
    p = Pushes()
    try:
        assert nothing_kept_for(rig, CONSOLE)
        assert not wait_until(p.delivers, timeout=0.3)
    finally:
        p.close()


def test_a_reply_once_line_naming_console_leaves_no_ask_open_for_it(console, pushes):
    console.shim(name="planner", scope="proj-a")
    console.shim(name="reviewer", scope="proj-a")
    both = console.post_as(PLANNER, machine_line.compose(
        [CONSOLE, "reviewer@proj-a"], "which branch?", posture="once"), raw=True)
    alone = console.post_as(PLANNER, machine_line.compose(
        [CONSOLE], "and you?", posture="once"), raw=True)
    assert wait_until(lambda: both.id in console.daemon.asks
                      and alone.id in console.daemon.send_only_seen)
    assert console.daemon.asks[both.id]["open"] == {"reviewer@proj-a"}
    assert not (console.daemon.asks.get(alone.id) or {}).get("open")
    assert not any(CONSOLE in a["open"] for a in console.daemon.asks.values())
    nominated = console.daemon._asks_query({"sender": CONSOLE,
                                            "recipients": [PLANNER]})
    assert nominated["asks"] == []


@pytest.mark.parametrize("scope", ["proj-a", "proj-b"])
def test_a_bare_console_is_the_global_console_an_ordinary_unbound_name(console, pushes,
                                                                     scope):
    """The scoped relay is bound. A bare `relay` is the global identity from every
    scope, a chosen name nobody has bound and never the send-only relay's."""
    code, out = console.cli(["send", "--posture", "none", "--new-name", "relay", "hello"],
                            name="captain", scope=scope)
    assert code == 0, out
    assert "NOTE: relay is not bound right now" in out
    msg = history(console)[-1]
    assert machine_line.parse(msg.content).recipients == ("relay",)
    assert wait_until(lambda: msg.id in queued(console.daemon, "relay"))
    assert pushes.delivers() == []
    assert nothing_kept_for(console, CONSOLE)
    r = console.daemon.resolve("relay")
    assert (r.qualified, r.tier, r.send_only, r.binding) == ("relay", "chosen",
                                                             False, None)


# --------------------------------------------------------------------------- sending as it

def test_a_send_from_inside_the_claimservers_session_is_verified_as_console(console,
                                                                           server):
    planner = console.shim(name="planner", scope="proj-a")
    code, out = console_cli(console, server, ["send", PLANNER, "your brief", "--posture", "none"])
    assert code == 0, out
    msg = history(console)[-1]
    assert (msg.author_kind, msg.author_id, msg.author_display) == (
        "identity", CONSOLE, CONSOLE)
    live = console.daemon.attribute(msg, live=True)
    assert (live.qualified, live.tier, live.attribution) == (CONSOLE, "standing",
                                                             attribution.VERIFIED)
    db = directory.connect(comms_db(), read_only=True)
    try:
        assert attribution.from_record(db, msg).attribution == attribution.VERIFIED
    finally:
        db.close()
    assert wait_until(lambda: got(planner, msg.id))


def test_a_comms_send_child_the_service_spawns_sends_as_console(console, server):
    """The shape the service uses for a first message: a `comms send` child spawned as a
    `tree` root of the binding. A child is a second process, so it needs a record it can
    reach: the local backend's, which every comms process writes directly."""
    if console.backend != "local":
        pytest.skip("a child process cannot reach the mock's in-memory record")
    planner = console.shim(name="planner", scope="proj-a")
    script = ("import sys\nfrom moxi_comms import cli\n"
              f"sys.exit(cli.main(['send', {PLANNER!r}, 'your brief', '--posture', 'none']))\n")
    env = dict(os.environ, MOXI_COMMS_BACKEND="local", MOXI_COMMS_PROJECT="proj-a")
    for var in ("MOXI_COMMS_NAME", "MOXI_COMMS_SCOPE", "MOXI_COMMS_SLOT", "MOXI_COMMS_GLOBAL"):
        env.pop(var, None)
    child = server.spawn([sys.executable, "-c", script], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out = child.communicate(timeout=60)[0]
    assert child.returncode == 0, out
    msg = history(console)[-1]
    assert msg.author_display == CONSOLE
    assert console.daemon.attribute(msg, live=True).attribution == attribution.VERIFIED
    assert wait_until(lambda: got(planner, msg.id))


def test_a_send_from_a_process_outside_it_is_refused(console, server):
    console.shim(name="planner", scope="proj-a")
    before = len(history(console))
    # A process the service did not spawn, holding the socket's path: not inside.
    script = ("import sys\nfrom moxi_comms import cli\n"
              f"sys.exit(cli.main(['send', {PLANNER!r}, 'x']))\n")
    env = dict(os.environ, **{CLAIM_SOCKET_ENV: str(server.socket_path)})
    done = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True,
                          text=True, timeout=60)
    assert done.returncode == cli.EXIT_REFUSED, done
    assert NOT_INSIDE in done.stdout and "nothing was sent" in done.stdout
    # A stamp of the name is no live session holding it.
    code, out = console.cli(["send", "--posture", "none", PLANNER, "x"], name="relay", scope="proj-a",
                            stamp_only=True)
    assert code == cli.EXIT_REFUSED
    assert f"not inside the session that holds {CONSOLE}" in out
    assert len(history(console)) == before
    answer = call(server.socket_path, "claim")
    assert answer["claim"]["qualified"] == CONSOLE        # the holder itself is inside


def test_the_console_sends_to_a_fresh_session_and_is_held_by_a_cold_one(console, server):
    """The cold-cache rule applies to the console as to any sender, on the recipient:
    a just-bound session with no turn reads fresh and gets it; a cold one holds it --
    posted, exit 0, and kept for its own next turn (cold-hold)."""
    fresh = console.shim(name="planner", scope="proj-a",
                         session_id="22222222-2222-4222-8222-222222222222")
    code, out = console_cli(console, server, ["send", PLANNER, "first turn", "--posture", "none"])
    assert code == 0, out
    assert wait_until(lambda: got(fresh, history(console)[-1].id))
    cold_sid = "33333333-3333-4333-8333-333333333333"
    write_transcript(cold_sid, age=2 * HOUR + 600)
    console.shim(name="reviewer", scope="proj-a",  session_id=cold_sid)
    before = len(history(console))
    code, out = console_cli(console, server, ["send", "reviewer@proj-a", "x", "--posture", "none"])
    assert code == 0, out
    assert out.splitlines()[1].startswith("held: reviewer@proj-a's cache went cold")
    assert out.splitlines()[-1].startswith("Sent and held: reviewer@proj-a reads this")
    assert len(history(console)) == before + 1


# --------------------------------------------------------------------------- provisioning

def test_the_provisioning_adds_a_send_only_row_without_credentials(rig):
    lines = provision_console(rig)
    db = directory.connect(comms_db(), read_only=True)
    try:
        row = directory.standing_row(db, "relay", "proj-a")
        assert directory.kind_of(row) == "send-only"
        assert directory.is_send_only(db, "relay", "proj-a")
        assert not directory.is_send_only(db, "herald", "global")
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        db.close()
    assert "webhooks" not in tables
    assert f"send-only {CONSOLE}: standing row, kind send-only" in lines
    if rig.store is not None:
        assert "proj-a/relay" not in rig.store.owners_present()
    # A re-run converges: still no webhooks or stored credentials.
    provision_console(rig)
    db = directory.connect(comms_db(), read_only=True)
    try:
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        db.close()
    assert "webhooks" not in tables


def test_the_provisioning_refuses_to_convert_an_agent_row(rig):
    with pytest.raises(SystemExit) as e:
        provision.send_only_main(["herald"], record=rig.record, store=rig.store,
                                 out=lambda _l: None)
    assert "already provisioned as a standing row of kind 'agent'" in str(e.value)
    db = directory.connect(comms_db(), read_only=True)
    try:
        assert directory.kind_of(directory.standing_row(db, "herald", "global")) == "agent"
    finally:
        db.close()


def test_the_provisioning_refuses_a_name_live_as_a_chosen_binding(rig):
    rig.shim(name="relay", scope="proj-a",  project="proj-a")
    with pytest.raises(SystemExit) as e:
        provision.send_only_main([CONSOLE], record=rig.record,
                                 store=rig.store, out=lambda _l: None)
    assert "currently live as a chosen binding" in str(e.value)


def test_an_identity_row_reads_agent_by_default_and_takes_no_other_kind(tmp_path):
    path = tmp_path / "comms.db"
    conn = directory.init(path)
    try:
        conn.execute("INSERT INTO identities(name, scope, created_at) "
                     "VALUES ('planner', 'proj-a', '2026-01-01')")
        assert directory.kind_of(directory.standing_row(conn, "planner", "proj-a")) == "agent"
        assert not directory.is_send_only(conn, "planner", "proj-a")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE identities SET kind='inbox' WHERE name='planner'")
    finally:
        conn.close()


def test_the_cli_dispatches_the_send_only_subcommand(rig, monkeypatch):
    seen = {}
    monkeypatch.setattr(provision, "send_only_main",
                        lambda argv: seen.setdefault("argv", argv) and 0)
    provision.main(["send-only", CONSOLE])
    assert seen["argv"] == [CONSOLE]


def test_a_bare_relay_is_global_while_the_scoped_send_only_row_exists(
        console):
    """The scoped send-only row does not change the meaning of a bare global name."""
    frames = []
    global_console = DaemonClient(daemon_socket(), on_push=frames.append).connect()
    try:
        global_console.register(name="relay", global_=True, project="proj-a",
                                adapter="claude-shim",
                                session_id=warm_session())
        assert "relay" in console.daemon.bindings
        r = console.daemon.resolve("relay")
        assert (r.qualified, r.send_only) == ("relay", False) and r.binding is not None
        r = console.daemon.resolve(CONSOLE)
        assert (r.qualified, r.send_only, r.binding) == (CONSOLE, True, None)
        code, out = console.cli(["send", "--posture", "none", "relay", "x"],
                                name="captain", scope="proj-a")
        assert code == 0, out
        mid = history(console)[-1].id
        assert machine_line.parse(history(console)[-1].content).recipients == ("relay",)
        assert wait_until(lambda: mid in console.daemon.bindings["relay"].delivered)
        before = len(history(console))
        code, out = console.cli(["send", "--posture", "none", CONSOLE, "x"], name="captain",
                                scope="proj-a")
        assert code == cli.EXIT_REFUSED and out.strip() == REFUSAL
        assert len(history(console)) == before
    finally:
        global_console.close()


def test_the_bind_logs_what_it_dropped(rig, caplog):
    code, out = rig.cli(["send", "--posture", "none", "--new-name", CONSOLE, "before"], **LEAD)
    assert code == 0, out
    msg = history(rig)[-1]
    assert wait_until(lambda: msg.id in queued(rig.daemon, CONSOLE))
    provision_console(rig)
    with caplog.at_level("INFO", logger="comms"):
        p = Pushes()
        p.close()
    assert (f"bind {CONSOLE}: dropped 1 message(s) queued for it from before its "
            f"row was send-only, delivered to nobody: {msg.id}") in caplog.text


def test_the_provisioning_refuses_a_retired_send_only_row(console):
    db = directory.connect(comms_db())
    try:
        db.execute("UPDATE identities SET status='retired' WHERE name='relay' "
                   "AND scope='proj-a'")
        db.commit()
    finally:
        db.close()
    lines = []
    with pytest.raises(SystemExit) as e:
        provision.send_only_main([CONSOLE], record=console.record,
                                 store=console.store, out=lines.append)
    assert f"{CONSOLE} is provisioned but retired; nothing was changed" in str(e.value)
    assert lines == []
    db = directory.connect(comms_db(), read_only=True)
    try:
        row = db.execute("SELECT status FROM identities WHERE name='relay'").fetchone()
        assert row["status"] == "retired"
    finally:
        db.close()


# --------------------------------------------------------------------------- slotless bindings

def test_a_send_only_register_answer_and_roster_have_no_slot(console, pushes):
    assert "slot" not in pushes.reply
    assert pushes.reply["identity"]["qualified"] == CONSOLE
    view = DaemonClient(daemon_socket()).connect()
    try:
        row = [b for b in view.query("agents")["bindings"] if b["qualified"] == CONSOLE]
    finally:
        view.close()
    assert row == [{"qualified": CONSOLE, "name": "relay", "scope": "proj-a",
                    "tier": "standing", "adapter": "send-only",
                    "endpoint": None, "endpoint_since": None}]


def test_a_second_send_only_register_is_refused_as_already_bound(console, pushes):
    with pytest.raises(DaemonError) as e:
        register(name="relay", scope="proj-a", project="proj-a", adapter="send-only")
    assert str(e.value) == f"{CONSOLE} is already bound (adapter send-only)"
    assert e.value.response["holder"] == {"qualified": CONSOLE, "adapter": "send-only"}


def test_the_binding_history_row_has_no_retired_column_and_replay_reads_its_post_verified(
        console, server):
    planner = console.shim(name="planner", scope="proj-a")
    code, out = console_cli(console, server, ["send", PLANNER, "your brief", "--posture", "none"])
    assert code == 0, out
    msg = history(console)[-1]
    assert wait_until(lambda: got(planner, msg.id))
    rows = [r for r in console.daemon.binding_history.intervals() if r["qualified"] == CONSOLE]
    assert rows and all("slot_id" not in r.keys() for r in rows)
    replayed = console.daemon.attribute(msg, live=False)
    assert (replayed.qualified, replayed.tier, replayed.attribution) == (
        CONSOLE, "standing", attribution.VERIFIED)


def test_agents_seats_omits_the_slot_column(console, server):
    console.shim(name="planner", scope="proj-a")
    code, out = console.cli(["agents", "--seats"], **LEAD)
    assert code == 0, out
    line = next(ln for ln in out.splitlines() if ln.startswith(CONSOLE))
    assert line.split() == [CONSOLE, "standing", "adapter", "send-only"]
    agent_line = next(ln for ln in out.splitlines() if ln.startswith(PLANNER))
    assert line.index("adapter") == agent_line.index("adapter")
    code, out = console.cli(["agents"], **LEAD)
    assert code == 0 and "slot" not in out


def test_the_claim_and_child_environment_have_no_slot(console, server):
    from moxi_comms import claimfile
    from moxi_comms.claimhold import claim_overlay
    assert "slot" not in server.holder.claim
    assert "slot" not in call(server.socket_path, "claim")["claim"]
    assert call(server.socket_path, "state")["state"] == "bound"
    assert "slot" not in claimfile.read(server.holder.key)["claim"]
    env = claim_overlay({CLAIM_SOCKET_ENV: str(server.socket_path),
                         "MOXI_COMMS_NAME": "stale"})
    assert (env["MOXI_COMMS_NAME"], env["MOXI_COMMS_SCOPE"]) == ("relay", "proj-a")
