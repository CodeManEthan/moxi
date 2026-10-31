"""The send rule in full, and the regression test for every kind of sender now that the
env path is closed, on both backends.

A process with no claim socket sends under no identity: a launch stamp is refused, with
or without a live claim file naming it, and the refusal names the identity the stamp
claims. The operator's lane is unchanged. Every sender inside a live session
sends exactly as on main: the Claude shim, codex and OpenCode; a module's own
process holding several bindings (a `self` root, and a `tree` child
root); a module's own process holding a standing name, in-process and from its
`comms send` child; and a session whose adapter died, by its claim file. Delivery, attribution and
the sender's output are main's: `sent <id> to <name> in #names`, one line, and `replying
to <name> (<tier>, verified) in #names` before it on a reply.

A claim that names no one (an auto-named launch before its first bind, through a daemon
outage too, and in a dead adapter's claim file) holds no identity: whoami, send and
reply say so."""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys

import pytest

from claim_kit import World, wait_for
from conftest import clock_reads_warm, wait_for_queue
from moxi import exit as moxi_exit
from moxi_comms import (attribution, claimfile, claimhold, cli, codex_threads, directory,
                        provision)
from moxi_comms.adapters.codex import CodexAdapter
from moxi_comms.adapters.codex_roster import CodexAgent
from moxi_comms.claimhold import CLAIM_SOCKET_ENV, NOT_INSIDE, ClaimServer
from moxi_comms.client import DaemonClient
from moxi_comms.membership import TREE, root_of
from moxi_comms.paths import comms_db, daemon_socket
from fake_turns import FakeSession, fake_find

BOB = "bob@proj-a"
ALICE = "alice@proj-a"

#: The refusal of a stamp no live claim file names.
STAMP_REFUSAL = ("this process has no comms claim socket, so it sends under no identity — "
                 "nothing was sent. Its launch stamp names {q}, but a stamp is not a live "
                 "session holding a name: a session sends as its identity through its own "
                 "claim socket.")

#: A child process that sends one message, from the claim socket in its environment.
CHILD_SEND = ("import sys\nfrom moxi_comms import cli\n"
              "sys.exit(cli.main(['send', sys.argv[1], sys.argv[2], '--posture', "
              "'none']))\n")

#: A child process that asks each socket named in argv for its claim.
CHILD_ASK = ("import json,sys\nfrom moxi_comms import claimhold\n"
             "out={}\n"
             "for path in sys.argv[1:]:\n"
             "    a=claimhold.call(path,'claim')\n"
             "    out[path]=a.get('claim',{}).get('qualified') if a.get('ok') else "
             "a.get('error')\n"
             "print(json.dumps(out))\n")


@pytest.fixture(autouse=True)
def _warm(monkeypatch):
    clock_reads_warm(monkeypatch, "codex", "opencode")


@pytest.fixture
def world(rig):
    w = World()
    yield w
    w.stop()


def history(rig):
    return rig.record.history(rig.names_channel)


def sent_line(out: str, to: str) -> str:
    """main's one line for a send: `sent <id> to <to> in #names`."""
    first = out.splitlines()[0]
    mid = first.split()[1]
    assert first == f"sent {mid} to {to} in #names", out
    return mid


def message(rig, mid):
    return next(m for m in history(rig) if str(m.id) == str(mid))


def claim_env(socket_path) -> dict:
    return {CLAIM_SOCKET_ENV: str(socket_path)}


def child_env(**extra) -> dict:
    """A child's environment: the rig's roots, the local record, no stamp."""
    env = dict(os.environ, MOXI_COMMS_BACKEND="local", MOXI_COMMS_PROJECT="proj-a")
    env.update(extra)
    for var in ("MOXI_COMMS_NAME", "MOXI_COMMS_SCOPE", "MOXI_COMMS_SLOT", "MOXI_COMMS_GLOBAL"):
        env.pop(var, None)
    return env


def round_trip(rig, socket_path, sender: str, bob, *, tier="chosen"):
    """The sender, through its claim socket, sends to bob; bob's message to the sender
    is answered with `comms reply` through the same socket. Each prints, delivers and
    attributes as on main."""
    code, out = rig.cli(["send", "--posture", "none", BOB, "hello bob"], env=claim_env(socket_path))
    assert code == 0 and out.count("\n") == 1, out
    mid = sent_line(out, BOB)
    wait_for_queue(bob, 1)
    got = bob.queue[-1]
    assert got["message_id"] == mid and got["text"] == "hello bob"
    assert (got["author"]["name"], got["author"]["tier"], got["author"]["attribution"]) == (
        sender, tier, "verified")
    posted = message(rig, mid)
    assert (posted.author_kind, posted.author_id, posted.author_display) == (
        "identity", sender, sender)
    # bob writes to the sender; the sender replies to that message
    asked = rig.post_as(BOB, [sender], "a question")
    code, out = rig.cli(["reply", str(asked.id), "an answer"], env=claim_env(socket_path))
    assert code == 0, out
    lines = out.splitlines()
    assert lines[0] == f"replying to {BOB} (chosen, verified) in #names", out
    reply = sent_line("\n".join(lines[1:]), BOB)
    assert len(lines) == 2, out
    wait_for_queue(bob, 2)
    got = bob.queue[-1]
    assert got["message_id"] == reply and got["author"]["name"] == sender
    assert got["author"]["attribution"] == "verified"
    assert f"re:{asked.id}" in message(rig, reply).content.splitlines()[0]
    return mid, reply


# --------------------------------------------------------------------------- refusals

def test_a_stamp_is_refused_when_a_live_claim_file_names_its_identity(rig, world):
    world.real("seat")
    before = len(history(rig))
    code, out = rig.cli(["send", "--posture", "none", "nobody", "hello"], name="seat",  scope="proj-a",
                        stamp_only=True)
    assert code == moxi_exit.REFUSED
    assert out.strip() == ("this process is not inside the session that holds "
                           "seat@proj-a — nothing was sent. A session sends as its "
                           "identity through its own claim socket.")
    assert len(history(rig)) == before


def test_a_stamp_is_refused_when_no_live_claim_file_names_it(rig):
    """A stamp alone sends nothing now that the env path is closed. The binding
    is there (a shim whose file is not live), the stamp names it, and nothing is sent."""
    bob = rig.shim(name="bob", scope="proj-a")
    code, out = rig.cli(["send", "--posture", "none", "nobody", "hello"], name="bob",  scope="proj-a",
                        stamp_only=True)
    assert code == 3
    assert out.strip() == STAMP_REFUSAL.format(q=BOB)
    assert history(rig) == []
    assert not bob.queue


def test_a_stamped_reply_is_refused_the_same_way(rig):
    rig.shim(name="bob", scope="proj-a")
    asked = rig.post_as("alice@proj-a", ["bob"], "a question")
    code, out = rig.cli(["reply", str(asked.id), "an answer"], name="bob", scope="proj-a",
                        stamp_only=True)
    assert code == 3
    assert STAMP_REFUSAL.format(q=BOB) in out
    assert [m.id for m in history(rig)] == [asked.id]


def test_a_global_stamp_names_its_global_identity(rig):
    code, out = rig.cli(["send", "--posture", "none", "nobody", "hi"], name="herald",
                        env={"MOXI_COMMS_GLOBAL": "1"}, stamp_only=True)
    assert code == 3 and out.strip() == STAMP_REFUSAL.format(q="herald")
    assert history(rig) == []


def test_no_stamp_and_no_socket_is_refused(rig):
    rig.shim(name="bob", scope="proj-a")
    code, out = rig.cli(["send", "--posture", "none", "bob@proj-a", "hi"])
    assert code == 3
    assert out.startswith("comms has no identity for this process: nothing was sent.")
    assert history(rig) == []


def test_a_stamp_with_a_claim_socket_is_not_read(rig, world):
    """With the socket the stamp is not read: the claim is the identity."""
    s = world.real("alice")
    rig.shim(name="bob", scope="proj-a")
    code, out = rig.cli(["send", "--posture", "none", BOB, "hi"], name="mallory",
                        env=claim_env(s.socket))
    assert code == 0, out
    assert history(rig)[-1].author_display == ALICE


# --------------------------------------------------------------------------- the operator

def test_the_operators_lane_outside_every_session_still_sends(rig):
    if rig.backend != "local":
        pytest.skip("the operator's credential is a token-store file, kept on local")
    bob = rig.shim(name="bob", scope="proj-a")
    code, out = rig.cli(["send", "--posture", "none", "bob@proj-a", "from the terminal"],
                        env={"MOXI_COMMS_OPERATOR": rig.prov.operator_user_id})
    assert code == 0, out
    sent_line(out, BOB)
    wait_for_queue(bob, 1)
    assert bob.queue[0]["author"] == {"name": "operator", "tier": "operator",
                                      "attribution": "verified"}


def test_the_operators_lane_inside_a_session_is_refused(rig, world):
    world.real("alice")          # this process is inside alice's session
    code, out = rig.cli(["send", "--posture", "none", "nobody", "hi"],
                        env={"MOXI_COMMS_OPERATOR": rig.prov.operator_user_id})
    assert code == 3 and "operator's lane is its own terminal" in out
    assert history(rig) == []


def test_the_operators_stamp_beside_an_agents_stamp_is_refused(rig):
    code, out = rig.cli(["send", "--posture", "none", "nobody", "hi"], name="alice", scope="proj-a",
                        env={"MOXI_COMMS_OPERATOR": rig.prov.operator_user_id})
    assert code == 3 and "claimed session is never the operator" in out
    assert history(rig) == []


# ---------------------------------------------------------------------------- every kind

def test_a_claude_shim_session_sends_and_replies_as_on_main(rig):
    me = root_of(os.getpid(), TREE)
    alice = rig.shim(name="alice", scope="proj-a",  listen_claims=True,
                     claim_roots=[me])
    bob = rig.shim(name="bob", scope="proj-a")
    round_trip(rig, alice.claims.socket_path, ALICE, bob)


def test_a_codex_session_sends_and_replies_as_on_main(rig, tmp_path):
    agent = CodexAgent(qualified=ALICE, name="alice", scope="proj-a",
                       cwd=str(tmp_path), thread_file=codex_threads.path(ALICE), directors=[])
    adapter = CodexAdapter(agent, find=fake_find(FakeSession()), autoreconnect=False,
                           clock=rig.clock)
    try:
        adapter.start()
        assert wait_for(lambda: ALICE in rig.daemon.bindings)
        assert rig.daemon.bindings[ALICE].adapter == "codex"
        bob = rig.shim(name="bob", scope="proj-a")
        round_trip(rig, adapter.claims.socket_path, ALICE, bob)
    finally:
        adapter.stop()


def test_an_opencode_session_sends_and_replies_as_on_main(rig, tmp_path, monkeypatch):
    from fake_opencode import FakeOpenCode

    from moxi_comms.adapters import opencode as oc
    from moxi_comms.adapters.opencode import OpenCodeAdapter
    from moxi_comms.adapters.opencode_roster import OpenCodeAgent, session_path
    from test_opencode_adapter import PASSWORD as OC_PASSWORD, Handle as OcHandle
    monkeypatch.setattr(oc, "FLUSH_RETRY", 0.05)
    fake = FakeOpenCode(password=OC_PASSWORD, default_model="opencode/minimax-m3.1-flash-free",
                        heartbeat=0.5).start()
    agent = OpenCodeAgent(qualified=ALICE, name="alice", scope="proj-a",
                          cwd=str(tmp_path), session_file=session_path(ALICE),
                          models=["opencode/minimax-m3.1-flash-free"])
    adapter = OpenCodeAdapter(agent, spawn=False, server=OcHandle(fake),
                              password=OC_PASSWORD, autoreconnect=False)
    adapter.run_opencode = lambda args: (-1, "")
    fake.on_log = adapter.on_log_line
    try:
        adapter.start()
        assert wait_for(lambda: ALICE in rig.daemon.bindings)
        assert rig.daemon.bindings[ALICE].adapter == "opencode"
        bob = rig.shim(name="bob", scope="proj-a")
        round_trip(rig, adapter.claims.socket_path, ALICE, bob)
    finally:
        adapter.stop()
        fake.stop()


class Loop:
    """Bindings held by a module's own process, one process holding several names (say
    `operator` and `archivist`): the process registers each binding on its own connection and reports it
    to a `ClaimServer` (a `self` root); the second binding also spawns children as
    `tree` roots of it."""

    def __init__(self):
        self.clients, self.servers = [], {}

    def hold(self, name, kind):
        client = DaemonClient(daemon_socket()).connect()
        answer = client.bind(name=name, scope="proj-a", project="proj-a",
                                 adapter=kind)
        server = ClaimServer(name, "proj-a", kind=kind).start()
        server.set_bound(token=answer["token"])
        self.clients.append(client)
        self.servers[name] = server
        return server

    def stop(self):
        for server in self.servers.values():
            server.stop()
        for client in self.clients:
            client.close()


@pytest.fixture
def loop(rig):
    held = Loop()
    yield held
    held.stop()


def test_a_loop_holding_two_bindings_sends_as_each_from_its_own_process(rig, loop):
    speaker = loop.hold("alice", "codex")
    driver = loop.hold("coder", "opencode")
    bob = rig.shim(name="bob", scope="proj-a")
    round_trip(rig, speaker.socket_path, ALICE, bob)
    code, out = rig.cli(["send", "--posture", "none", BOB, "from the second binding"],
                        env=claim_env(driver.socket_path))
    assert code == 0, out
    mid = sent_line(out, BOB)
    posted = message(rig, mid)
    assert (posted.author_kind, posted.author_id, posted.author_display) == (
        "identity", "coder@proj-a", "coder@proj-a")


def test_a_loops_tree_child_is_inside_its_binding_and_no_other(rig, loop):
    speaker = loop.hold("alice", "codex")
    driver = loop.hold("coder", "opencode")
    child = driver.spawn([sys.executable, "-c", CHILD_ASK, str(driver.socket_path),
                          str(speaker.socket_path)], env=child_env(),
                         stdout=subprocess.PIPE, text=True)
    answers = json.loads(child.communicate(timeout=60)[0])
    assert answers[str(driver.socket_path)] == "coder@proj-a"
    assert answers[str(speaker.socket_path)] == NOT_INSIDE      # `self` admits no child


def test_a_loops_tree_child_sends_as_its_binding(rig, loop):
    if rig.backend != "local":
        pytest.skip("a child process cannot reach the mock's in-memory record")
    driver = loop.hold("coder", "opencode")
    bob = rig.shim(name="bob", scope="proj-a")
    child = driver.spawn([sys.executable, "-c", CHILD_SEND, BOB, "from the child"],
                         env=child_env(), stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    out = child.communicate(timeout=60)[0]
    assert child.returncode == 0, out
    mid = sent_line(out, BOB)
    wait_for_queue(bob, 1)
    assert bob.queue[0]["author"] == {"name": "coder@proj-a", "tier": "chosen",
                                      "attribution": "verified"}
    assert message(rig, mid).author_id == "coder@proj-a"


def test_a_process_the_loop_did_not_spawn_is_refused(rig, loop):
    driver = loop.hold("coder", "opencode")
    before = len(history(rig))
    done = subprocess.run([sys.executable, "-c", CHILD_SEND, BOB, "x"],
                          env=child_env(**claim_env(driver.socket_path)),
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 3, done
    assert NOT_INSIDE in done.stdout and "nothing was sent" in done.stdout
    assert len(history(rig)) == before


@pytest.fixture
def console(rig):
    """A module's own process holding the standing `relay@proj-a` through a
    `ClaimServer` with its own daemon arm."""
    conn = directory.init(comms_db())
    try:
        provision.add_standing(rig.record, conn, "relay", "proj-a")
    finally:
        conn.close()
    srv = ClaimServer("relay", "proj-a", kind="claude-shim", arm=True).start()
    assert wait_for(lambda: "relay@proj-a" in rig.daemon.bindings), srv.refusal
    yield srv
    srv.stop()


def test_the_console_sends_in_process_as_on_main(rig, console):
    bob = rig.shim(name="bob", scope="proj-a")
    code, out = rig.cli(["send", BOB, "your brief", "--posture", "none"],
                        env=claim_env(console.socket_path), project="proj-a")
    assert code == 0 and out.count("\n") == 1, out
    mid = sent_line(out, BOB)
    wait_for_queue(bob, 1)
    assert bob.queue[0]["author"] == {"name": "relay@proj-a", "tier": "standing",
                                      "attribution": "verified"}
    posted = message(rig, mid)
    assert (posted.author_kind, posted.author_id) == ("identity", "relay@proj-a")


def test_the_consoles_comms_send_child_sends_as_on_main(rig, console):
    if rig.backend != "local":
        pytest.skip("a child process cannot reach the mock's in-memory record")
    bob = rig.shim(name="bob", scope="proj-a")
    child = console.spawn([sys.executable, "-c", CHILD_SEND, BOB, "your brief"],
                          env=child_env(MOXI_COMMS_PROJECT="acme"),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out = child.communicate(timeout=60)[0]
    assert child.returncode == 0, out
    mid = sent_line(out, BOB)
    wait_for_queue(bob, 1)
    assert bob.queue[0]["author"]["name"] == "relay@proj-a"
    live = rig.daemon.attribute(message(rig, mid), live=True)
    assert live.attribution == attribution.VERIFIED


# --------------------------------------------------------------------------- third parties, a dead adapter

def test_third_party_sends_to_a_seat_held_through_a_claim_are_as_on_main(rig, world):
    """carol's shim sends to the seat; the seat's holder is a session of another kind."""
    world.real("seat")
    carol = rig.shim(name="carol", scope="proj-a",  listen_claims=True,
                     claim_roots=[root_of(os.getpid(), TREE)])
    code, out = rig.cli(["send", "--posture", "none", "seat@proj-a", "for the seat"],
                        env=claim_env(carol.claims.socket_path))
    assert code == 0 and out.count("\n") == 1, out
    mid = sent_line(out, "seat@proj-a")
    assert wait_for(lambda: mid in rig.daemon.bindings["seat@proj-a"].delivered)
    assert message(rig, mid).author_display == "carol@proj-a"


def test_a_shim_that_died_cannot_send_by_its_claim_file(rig):
    """The claim file can name a dead adapter's identity but carries no token."""
    me = root_of(os.getpid(), TREE)
    alice = rig.shim(name="alice", scope="proj-a",  listen_claims=True,
                     claim_roots=[me])
    path = alice.claims.socket_path
    bob = rig.shim(name="bob", scope="proj-a")
    alice.stop()                                       # the shim dies; its session lives
    assert wait_for(lambda: ALICE not in rig.daemon.bindings)
    code, out = rig.cli(["send", "--posture", "none", BOB, "still here"], env=claim_env(path))
    assert code == 75
    assert "comms adapter is gone" in out and "holds no posting credential" in out
    assert history(rig) == []


# --------------------------------------------------------------------------- a claim with no name

#: The line for a session whose claim names no identity: a send, a reply and whoami say it.
NO_IDENTITY = "this session holds no comms identity"


def auto_named(rig, **extra):
    """An auto-named Claude session (`moxi-comms-claude` with no `--name`) at launch: its
    claim holder is up with `{"name": None, "qualified": None}` in `offline`, its claim
    socket serves, and this process is inside its session. Not yet bound."""
    shim = rig.shim(start=False, name=None, scope="proj-a",
                    claim_roots=[root_of(os.getpid(), TREE)], **extra)
    shim.claims.listen()
    claim = shim.claims.claim
    assert shim.claims.state == "offline"
    assert claim["name"] is None and claim["qualified"] is None
    return shim


def refuses_as_no_identity(rig, path, asked_id):
    """whoami, send and reply from inside the session: each says the no-identity line
    and exits 3, nothing is posted, and no `None` is printed as a name."""
    before = len(history(rig))
    code, out = rig.cli(["whoami"], env=claim_env(path))
    assert code == 3 and out.splitlines()[0] == NO_IDENTITY, out
    assert "None" not in out, out
    code, out = rig.cli(["send", "--posture", "none", BOB, "who am i"], env=claim_env(path))
    assert (code, out) == (3, f"{NO_IDENTITY} — nothing was sent.\n"), out
    code, out = rig.cli(["reply", str(asked_id), "an answer"], env=claim_env(path))
    # a reply says whom it answers first, as every refused reply does
    assert code == 3 and out.splitlines()[-1] == f"{NO_IDENTITY} — nothing was sent.", out
    assert "None" not in out, out
    assert len(history(rig)) == before


def test_an_auto_named_session_before_its_first_bind_holds_no_identity(rig):
    """The launch claim of an auto-named session names no one, so it holds
    no identity. Once it binds, it is the name the daemon gave it, as on main."""
    bob = rig.shim(name="bob", scope="proj-a")
    asked = rig.post_as(BOB, [BOB], "a question")
    shim = auto_named(rig)
    path = shim.claims.socket_path
    refuses_as_no_identity(rig, path, asked.id)
    assert claimhold.send_line(shim.claims.record()) == NO_IDENTITY
    assert claimhold.call(str(path), "claim")["line"] == NO_IDENTITY
    # the first bind: whoami names the bound identity and the session sends as it
    shim.start()
    q = shim.claims.claim["qualified"]
    assert q and shim.claims.state == "bound"
    code, out = rig.cli(["whoami"], env=claim_env(path))
    assert code == 0 and out.splitlines()[0] == f"{q} (bound)", out
    code, out = rig.cli(["send", "--posture", "none", BOB, "hello bob"], env=claim_env(path))
    assert code == 0 and out.count("\n") == 1, out
    mid = sent_line(out, BOB)
    assert wait_for(lambda: any(m["message_id"] == mid for m in bob.queue))
    assert [m["author"]["name"] for m in bob.queue if m["message_id"] == mid] == [q]
    assert message(rig, mid).author_display == q
    code, out = rig.cli(["reply", str(asked.id), "an answer"], env=claim_env(path))
    assert code == 0 and out.splitlines()[0].startswith(f"replying to {BOB} "), out
    reply = sent_line("\n".join(out.splitlines()[1:]), BOB)
    assert wait_for(lambda: any(m["message_id"] == reply for m in bob.queue))
    assert [m["author"]["name"] for m in bob.queue if m["message_id"] == reply] == [q]


def test_a_session_that_never_bound_holds_no_identity_through_a_daemon_outage(rig):
    """The same claim, with the daemon down from the launch on: the supervisor retries
    and the session still holds no identity while it does."""
    rig.shim(name="bob", scope="proj-a")
    asked = rig.post_as(BOB, [BOB], "a question")
    rig.daemon.stop()
    shim = auto_named(rig, autoreconnect=True, backoff=itertools.repeat(30.0))
    shim.start(tolerate_offline=True)
    assert shim.claims.state == "offline" and shim.claims.claim["name"] is None
    refuses_as_no_identity(rig, shim.claims.socket_path, asked.id)


def test_a_dead_adapter_whose_claim_file_names_no_one_is_refused(rig):
    """The adapter-died path reads the claim file: a claim there with no name holds no
    identity either."""
    rig.shim(name="bob", scope="proj-a")
    asked = rig.post_as(BOB, [BOB], "a question")
    shim = auto_named(rig)
    path = shim.claims.socket_path
    shim.stop()                                        # the shim dies; its session lives
    answer = claimhold.dead_adapter_answer(path)
    assert answer["dead_adapter"] and answer["claim"] is None
    assert answer["line"] == NO_IDENTITY
    refuses_as_no_identity(rig, path, asked.id)


@pytest.mark.parametrize("state", ["bound", "offline"])
def test_a_claim_with_no_name_holds_no_identity_in_any_held_state(state):
    record = {"state": state, "claim": {"name": None, "qualified": None}}
    assert claimfile.held_identity(record) is None
    assert claimhold.send_line(record) == NO_IDENTITY
    record["claim"].update(name="alice", qualified=ALICE)
    assert claimfile.held_identity(record) == ALICE
    assert claimhold.send_line(record) is None


def test_a_named_offline_claim_is_refused(rig):
    """Whoami still names an offline claim, but it has no posting token."""
    bob = rig.shim(name="bob", scope="proj-a")
    server = ClaimServer("alice", "proj-a", kind="claude-shim")
    server.holder.ever_held = True
    server.set_offline()
    server.start()
    try:
        path = server.socket_path
        code, out = rig.cli(["whoami"], env=claim_env(path))
        assert code == 0 and out.splitlines()[0] == f"{ALICE} (offline)", out
        code, out = rig.cli(["send", "--posture", "none", BOB, "from offline"], env=claim_env(path))
        assert code == 75
        assert "holds no posting credential" in out and "nothing was sent" in out
        assert history(rig) == []
    finally:
        server.stop()
