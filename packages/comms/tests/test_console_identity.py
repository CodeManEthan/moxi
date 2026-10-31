"""Reserved send-only names: `console`, reserved for the launcher, sends only under its
module's token (`api.send_as`) and is never a recipient, from any sender. A send-only
name an older comms kept (`relay@proj-a`, `unowned`) is never a recipient either. A
scoped `console@proj-a` is an ordinary name. On both backends."""

from __future__ import annotations

import sqlite3

import pytest

from conftest import queued, wait_until, write_config
from moxi import exit as moxi_exit
from moxi.store import transaction
from moxi_comms import api, attribution, directory, machine_line, reserved, secrets, wire
from moxi_comms.client import DaemonClient, DaemonError
from moxi_comms.paths import comms_db, daemon_socket
from moxi_comms.store import open_write

CONSOLE = "console"
RELAY = "relay@proj-a"
PLANNER = "planner@proj-a"
#: The words the refusal names the console with: config.toml's `[policy] labels`.
LABEL = "the launcher's send-only name"
REFUSAL = (f"console is {LABEL} and receives no mail; nothing was sent. A brief that "
           "wants a report names whom to report to.")
RELAY_REFUSAL = ("relay@proj-a is a send-only identity and receives no mail; nothing was "
                 "sent. A brief that wants a report names whom to report to.")
#: A sender for the verbs, sent as from a session holding it (the rig's
#: `session_holding`).
LEAD = dict(name="captain", scope="proj-a")


@pytest.fixture(autouse=True)
def console_label(env):
    """The box names its console in `[policy] labels`, as the operator's config does."""
    write_config(None, f'[policy.labels]\n"{CONSOLE}" = "{LABEL}"\n')


@pytest.fixture
def console(rig):
    """`console` reserved for the launcher; `relay@proj-a` kept from an older comms."""
    reserved.reconcile(declared={"launcher": [CONSOLE]}, chain=secrets.from_config())
    conn = open_write()
    try:
        with transaction(conn) as c:
            c.execute("INSERT INTO reserved(name, state, note) VALUES (?, 'unowned', "
                      "'a send-only name of comms 0.2')", (RELAY,))
    finally:
        conn.close()
    return rig


def history(rig):
    return rig.record.history(rig.names_channel)


def got(shim, message_id):
    return any(p["message_id"] == message_id for p in shim.queue)


def send_as(to, text="your brief"):
    result = api.send_as("launcher", CONSOLE, to, text)
    assert result.outcome == "sent", result.lines()
    return result.message_id


# --------------------------------------------------------------------------- the verbs

@pytest.mark.parametrize("tag,words", [(CONSOLE, REFUSAL), (RELAY, RELAY_REFUSAL)])
@pytest.mark.parametrize("scope", ["proj-a", "proj-b"])
def test_a_send_to_a_reserved_name_is_refused_before_posting(console, tag, words, scope):
    before = len(history(console))
    code, out = console.cli(["send", "--posture", "none", tag, "report back"],
                            name="captain", scope=scope)
    assert code == moxi_exit.REFUSED
    assert out.strip() == words
    assert len(history(console)) == before


def test_a_send_naming_console_among_others_is_refused_whole(console):
    console.shim(name="planner", scope="proj-a")
    before = len(history(console))
    code, out = console.cli(["send", "--posture", "none", f"{PLANNER},{CONSOLE}", "x"],
                            **LEAD)
    assert code == moxi_exit.REFUSED and REFUSAL in out
    assert len(history(console)) == before


def test_a_reply_to_consoles_message_is_refused_before_posting(console):
    """The console's own brief, then a reply to it: resolved to console, refused."""
    console.shim(name="planner", scope="proj-a")
    brief = send_as([PLANNER])
    before = len(history(console))
    code, out = console.cli(["reply", brief, "done"], name="planner", scope="proj-a")
    assert code == moxi_exit.REFUSED
    assert out.splitlines()[-1] == REFUSAL
    code, out = console.cli(["send", "--reply-to", brief, CONSOLE, "done"],
                            name="planner", scope="proj-a")
    assert code == moxi_exit.REFUSED and out.strip() == REFUSAL
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
    assert not queued(console.daemon, CONSOLE)
    assert console.daemon.send_only_seen == {msg.id: None}
    console.daemon.rescan()
    assert not queued(console.daemon, CONSOLE)
    c = DaemonClient(daemon_socket()).connect()
    try:
        view = c.call("agents")
    finally:
        c.close()
    assert CONSOLE not in view["waiting"] and view["pending"] == {}


def test_a_reply_once_line_naming_console_leaves_no_ask_open_for_it(console):
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


def test_a_scoped_console_is_an_ordinary_name(console):
    """A reserved name is bare and global: `console@proj-a` is another identity, a
    chosen name nobody has bound, mailed like any."""
    code, out = console.cli(["send", "--posture", "none", "--new-name", "console@proj-a",
                             "hello"], **LEAD)
    assert code == 0, out
    msg = history(console)[-1]
    assert wait_until(lambda: msg.id in queued(console.daemon, "console@proj-a"))
    r = console.daemon.resolve("console@proj-a")
    assert (r.qualified, r.tier, r.send_only) == ("console@proj-a", "chosen", False)


def test_a_freed_name_is_mailed_again(console):
    reserved.reconcile(declared={}, chain=secrets.from_config())
    assert not console.daemon.resolve(CONSOLE).send_only
    assert console.daemon.resolve(RELAY).send_only, "nobody declared relay; it stays"


# --------------------------------------------------------------------------- sending as it

def test_a_send_as_console_is_verified_as_console(console):
    planner = console.shim(name="planner", scope="proj-a")
    mid = send_as([PLANNER])
    msg = console.record.get_message(mid)
    assert (msg.author_kind, msg.author_id) == ("identity", CONSOLE)
    assert machine_line.parse(msg.content).posture == "none"
    db = directory.connect(comms_db(), read_only=True)
    try:
        assert attribution.from_record(db, msg).attribution == attribution.VERIFIED
    finally:
        db.close()
    assert wait_until(lambda: got(planner, mid))
    assert planner.queue[-1]["author"]["name"] == CONSOLE


def test_nothing_sends_under_an_unowned_name(console):
    c = DaemonClient(daemon_socket()).connect()
    try:
        with pytest.raises(DaemonError) as e:
            c.send(RELAY, "any", machine_line.compose([PLANNER], "x"))
    finally:
        c.close()
    assert e.value.code == wire.NAME_RESERVED and "no module holds" in str(e.value)


# --------------------------------------------------------------------------- the reads

def test_the_roster_lists_reserved_names_as_send_only(console):
    reserved.reconcile(declared={"launcher": [CONSOLE], "pager": ["herald"]},
                       chain=secrets.from_config())
    standing = {s.qualified: s for s in api.roster().standing}
    assert (standing[CONSOLE].kind, standing[CONSOLE].module) == ("send-only", "launcher")
    assert (standing[RELAY].kind, standing[RELAY].module) == ("send-only", None)
    assert (standing["herald"].kind, standing["herald"].module) == ("agent", "pager"), \
        "a standing row in conflict stays an agent"


def test_no_standing_row_is_send_only(console):
    conn = sqlite3.connect(comms_db())
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO identities(name, scope, status, created_at, kind) "
                         "VALUES ('x', 'global', 'active', '2026-01-01', 'send-only')")
    finally:
        conn.close()
