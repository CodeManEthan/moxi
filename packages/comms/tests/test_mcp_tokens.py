"""The per-session token of comms' tools: minting, the registry, rebinds, revocation."""

from __future__ import annotations

import base64
import itertools
import logging
import sqlite3

import pytest
from conftest import wait_until

from moxi_comms import bindings, claimfile, wire
from moxi_comms.client import DaemonClient, DaemonError
from moxi_comms.daemon import Daemon
from moxi_comms.mcp import tokens
from moxi_comms.membership import ProcView, Root
from moxi_comms.paths import comms_db, daemon_socket

PLANNER = "planner@proj-a"
_pids = itertools.count(52000, 10)


class View(ProcView):
    """A process table the test writes. `gone` answers for a pid with no entry, as the
    real views do from `/proc` or psutil; a pid in `broken` raises on every read."""

    def __init__(self):
        super().__init__()
        self.table: dict = {}
        self.broken: set = set()

    def stat(self, pid):
        if int(pid) in self.broken:
            raise PermissionError("access refused")
        return self.table.get(int(pid))

    def gone(self, pid):
        if int(pid) in self.broken:
            raise PermissionError("access refused")
        return int(pid) not in self.table

    def spawn(self) -> int:
        pid = next(_pids)
        self.table[pid] = (1, f"s{pid}")
        return pid


@pytest.fixture
def view(rig):
    v = View()
    rig.daemon.proc_view = v
    return v


class Session:
    """One session's adapter as the daemon sees it: a claim file whose root is a process
    in `view`, and a daemon connection that binds under that claim."""

    def __init__(self, view, *, name="planner", scope="proj-a", claim=None):
        self.view = view
        self.root = view.spawn()
        self.key = claim or f"{self.root}-s{self.root}"
        claimfile.write(self.key, {"key": self.key, "keying": "pid", "state": "bound",
                                   "roots": [Root(self.root, f"s{self.root}").as_dict()]})
        self.name, self.scope = name, scope
        self.client: DaemonClient | None = None
        self.token = tokens.mint()
        self.hash = tokens.digest(self.token)

    def bind(self, claim=...):
        self.client = DaemonClient(daemon_socket()).connect()
        self.client.bind(name=self.name, scope=self.scope, adapter="codex",
                         claim=self.key if claim is ... else claim)
        return self

    def register(self, token_hash=None) -> dict:
        return self.client.mcp_token(token_hash or self.hash)

    def drop(self, rig) -> None:
        """The connection ends; the session's process lives on. Returns once the daemon
        has released the binding: the name leaves `bindings` partway through that step,
        before the history row and the tool tokens lose the binding, all under the
        daemon's lock, so the lock is taken once after the name is gone."""
        self.client.close()
        self.client = None
        assert wait_until(lambda: f"{self.name}@{self.scope}" not in rig.daemon.bindings)
        with rig.daemon._lock:
            pass

    def end(self) -> None:
        """The session ends: its claim file goes, as a clean shutdown removes it."""
        claimfile.remove(self.key)

    def close(self) -> None:
        if self.client is not None:
            self.client.close()


@pytest.fixture
def sessions(view):
    made = []

    def make(**kw):
        s = Session(view, **kw)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


def rows() -> list:
    conn = bindings.connect(read_only=True)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM mcp_tokens ORDER BY created_at")]
    finally:
        conn.close()


def row_of(token_hash: str) -> dict:
    return next(r for r in rows() if r["token_hash"] == token_hash)


def live_binding_id(rig, qualified=PLANNER) -> int:
    return rig.daemon.bindings[qualified].history_id


def refused(call) -> DaemonError:
    with pytest.raises(DaemonError) as raised:
        call()
    return raised.value


def restart(rig, view) -> None:
    rig.daemon.stop()
    rig.daemon = Daemon(rig.record, clock=rig.clock, start_trim_thread=False)
    rig.daemon.proc_view = view
    rig.daemon.start()


# =========================================================================== minting

def test_a_token_is_32_random_bytes_and_only_its_hex_hash_is_kept():
    token = tokens.mint()
    assert len(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))) == 32
    assert tokens.mint() != token
    h = tokens.digest(token)
    assert tokens.is_digest(h) and len(h) == 64 and token not in h
    assert not tokens.is_digest(h.upper()) and not tokens.is_digest(token)


@pytest.mark.parametrize("key, ok", [
    ("0f9b3a2c-3c55-4f0e-9a51-2bb1f3b5e0aa", True), ("41000-1234567", True),
    ("41000-1730000000.12", True), ("srv-41000-55-console@acme", True),
    ("../x", False), ("a/b", False), ("", False), (".hidden", False), (7, False),
    ("x" * 257, False)])
def test_a_claim_key_names_a_file_in_the_claim_directory_only(key, ok):
    assert tokens.valid_claim_key(key) is ok


# =========================================================================== registering

def test_the_first_registration_inserts_a_row_bound_to_the_binding(rig, sessions):
    s = sessions().bind()
    assert s.register() == {"served": False}
    row = row_of(s.hash)
    assert (row["claim_key"], row["identity"], row["adapter"], row["binding_id"],
            row["revoked_at"], row["revoked_why"]) == (
                s.key, PLANNER, "codex", live_binding_id(rig), None, None)
    got = rig.daemon.mcp_tokens.verify(s.token)
    assert (got.identity, got.claim_key, got.bound) == (PLANNER, s.key, True)


def test_served_is_the_daemons_word_on_its_endpoint(rig, sessions):
    rig.daemon.mcp_served = True
    s = sessions().bind()
    assert s.register() == {"served": True}


def test_the_capability_is_in_the_hello(rig):
    c = DaemonClient(daemon_socket()).connect()
    try:
        assert "mcp" in c.capabilities
    finally:
        c.close()


def test_a_rebind_after_a_dropped_connection_binds_the_same_row_again(rig, sessions):
    s = sessions().bind()
    s.register()
    first = live_binding_id(rig)
    s.drop(rig)
    assert row_of(s.hash)["binding_id"] is None
    assert rig.daemon.mcp_tokens.verify(s.token).bound is False
    s.bind()
    assert s.register() == {"served": False}
    second = live_binding_id(rig)
    assert second != first
    assert row_of(s.hash)["binding_id"] == second and len(rows()) == 1
    assert rig.daemon.mcp_tokens.verify(s.token).binding_id == second


def test_a_rebind_after_a_daemon_restart_binds_the_same_row_again(rig, sessions, view):
    s = sessions().bind()
    s.register()
    s.client.close()
    s.client = None
    restart(rig, view)
    row = rig.daemon.mcp_tokens.verify(s.token)
    assert row is not None and row.identity == PLANNER and not row.bound
    s.bind()
    s.register()
    assert rig.daemon.mcp_tokens.verify(s.token).binding_id == live_binding_id(rig)
    assert row_of(s.hash)["revoked_at"] is None


def test_another_claims_hash_is_refused_taken_and_both_keys_are_logged(
        rig, sessions, caplog):
    caplog.set_level(logging.INFO)
    a = sessions().bind()
    a.register()
    b = sessions(name="reviewer").bind()
    e = refused(lambda: b.register(a.hash))
    assert e.code == wire.MCP_TOKEN_TAKEN == 1113
    assert len(rows()) == 1 and row_of(a.hash)["claim_key"] == a.key
    assert a.key in caplog.text and b.key in caplog.text
    assert rig.daemon.mcp_tokens.verify(a.token).identity == PLANNER


def test_the_same_claims_hash_under_another_name_is_refused_taken(rig, sessions):
    s = sessions().bind()
    s.register()
    s.drop(rig)
    s.name = "reviewer"
    s.bind()
    e = refused(s.register)
    assert e.code == wire.MCP_TOKEN_TAKEN
    assert row_of(s.hash)["identity"] == PLANNER and row_of(s.hash)["revoked_at"] is None


def test_a_new_hash_from_the_same_claim_supersedes_the_old(rig, sessions):
    s = sessions().bind()
    s.register()
    old_token, old_hash = s.token, s.hash
    s.token = tokens.mint()
    s.hash = tokens.digest(s.token)
    s.register()
    assert row_of(old_hash)["revoked_why"] == "superseded"
    assert row_of(old_hash)["revoked_at"] is not None
    assert rig.daemon.mcp_tokens.verify(old_token) is None
    assert rig.daemon.mcp_tokens.verify(s.token).identity == PLANNER
    e = refused(lambda: s.register(old_hash))
    assert e.code == wire.MCP_TOKEN_REVOKED == 1114


def test_a_revoked_hash_is_refused_revoked(rig, sessions):
    s = sessions().bind()
    s.register()
    rig.daemon.mcp_tokens.revoke_all("port-taken")
    e = refused(s.register)
    assert e.code == wire.MCP_TOKEN_REVOKED
    assert row_of(s.hash)["revoked_why"] == "port-taken"


def test_a_reserved_identity_is_refused_and_gets_no_row(rig, sessions):
    s = sessions().bind()
    conn = bindings.connect()
    try:
        conn.execute("INSERT INTO reserved(name, module, state) VALUES (?, 'mail', 'active')",
                     (PLANNER,))
    finally:
        conn.close()
    e = refused(s.register)
    assert e.code == wire.NAME_RESERVED
    assert rows() == []


def test_a_connection_with_no_binding_is_refused_not_bound(rig):
    c = DaemonClient(daemon_socket()).connect()
    try:
        assert refused(lambda: c.mcp_token(tokens.digest("x"))).code == wire.NOT_BOUND
    finally:
        c.close()
    assert rows() == []


@pytest.mark.parametrize("value", ["", "ABC", "a" * 63, None, 7])
def test_a_hash_that_isnt_one_is_refused(rig, sessions, value):
    s = sessions().bind()
    e = refused(lambda: s.client.call("mcp_token", {"hash": value}))
    assert e.code == wire.INVALID_PARAMS
    assert rows() == []


def test_a_binding_that_named_no_claim_registers_nothing(rig, sessions):
    s = sessions().bind(claim=None)
    assert refused(s.register).code == wire.INVALID_PARAMS
    assert rows() == []


def test_a_bind_naming_a_claim_that_isnt_one_is_refused(rig, sessions):
    s = sessions()
    with pytest.raises(DaemonError) as raised:
        s.bind(claim="../../elsewhere")
    assert raised.value.code == wire.INVALID_PARAMS
    assert PLANNER not in rig.daemon.bindings


# =========================================================================== the release

def test_a_release_clears_the_binding_in_the_store_and_in_memory(rig, sessions):
    s = sessions().bind()
    s.register()
    other = sessions(name="reviewer").bind()
    other.register()
    s.drop(rig)
    assert row_of(s.hash)["binding_id"] is None
    assert row_of(other.hash)["binding_id"] == live_binding_id(rig, "reviewer@proj-a")
    assert rig.daemon.mcp_tokens.verify(s.token).bound is False
    history = {r["id"]: r for r in rig.daemon.binding_history.intervals()}
    assert all(r["released_at"] for r in history.values()
               if r["qualified"] == PLANNER)


def test_the_release_and_the_clearing_share_one_transaction(tmp_path):
    """A release whose transaction fails leaves both the interval and the row bound."""
    history = bindings.BindingHistory()
    assert history.open()
    try:
        registry = tokens.Registry(history, proc=View())
        row_id = history.bind(qualified=PLANNER, name="planner", scope="proj-a",
                              tier="chosen", adapter="codex")
        h = tokens.digest(tokens.mint())
        registry.register(h, claim="k-1", identity=PLANNER, adapter="codex",
                          binding_id=row_id)
        history._conn.execute("CREATE TRIGGER no_release BEFORE UPDATE OF binding_id ON "
                              "mcp_tokens BEGIN SELECT RAISE(ABORT, 'held'); END")
        history.release(row_id)
        (released,) = history._conn.execute(
            "SELECT released_at FROM bindings WHERE id = ?", (row_id,)).fetchone()
        assert released is None
        history._conn.execute("DROP TRIGGER no_release")
        history.release(row_id)
        assert history._conn.execute("SELECT binding_id FROM mcp_tokens").fetchone()[0] \
            is None
    finally:
        history.close()


def test_the_boot_fence_clears_every_binding_and_keeps_every_row(tmp_path):
    """A daemon that died with live bindings: the next one's fence clears them all."""
    history = bindings.BindingHistory()
    assert history.open()
    view = View()
    root = view.spawn()
    claimfile.write("k-1", {"roots": [Root(root, f"s{root}").as_dict()]})
    registry = tokens.Registry(history, proc=view)
    row_id = history.bind(qualified=PLANNER, name="planner", scope="proj-a",
                          tier="chosen", adapter="codex")
    token = tokens.mint()
    registry.register(tokens.digest(token), claim="k-1", identity=PLANNER,
                      adapter="codex", binding_id=row_id)
    history.close()                                   # no release: the daemon died

    history = bindings.BindingHistory()
    assert history.open()
    try:
        assert history.fence() == 1
        registry = tokens.Registry(history, proc=view)
        assert registry.load() == 1
        row = registry.verify(token)
        assert row.identity == PLANNER and row.binding_id is None
        assert row_of(tokens.digest(token))["revoked_at"] is None
    finally:
        history.close()


# =========================================================================== revocation

def test_a_bind_of_the_name_under_another_claim_revokes_name_lost(rig, sessions):
    a = sessions().bind()
    a.register()
    a.drop(rig)
    b = sessions().bind()
    assert row_of(a.hash)["revoked_why"] == "name-lost"
    assert rig.daemon.mcp_tokens.verify(a.token) is None
    b.register()
    assert rig.daemon.mcp_tokens.verify(b.token).claim_key == b.key


def test_a_bind_naming_no_claim_revokes_name_lost(rig, sessions):
    a = sessions().bind()
    a.register()
    a.drop(rig)
    sessions().bind(claim=None)
    assert row_of(a.hash)["revoked_why"] == "name-lost"


def test_a_rebind_under_the_same_claim_keeps_the_token(rig, sessions):
    a = sessions().bind()
    a.register()
    a.drop(rig)
    a.bind()
    assert row_of(a.hash)["revoked_at"] is None


def test_a_refused_bind_revokes_nothing(rig, sessions):
    a = sessions().bind()
    a.register()
    b = sessions()
    with pytest.raises(DaemonError) as raised:
        b.bind()
    assert raised.value.code == wire.NAME_HELD
    assert row_of(a.hash)["revoked_at"] is None


def test_another_names_bind_revokes_nothing(rig, sessions):
    a = sessions().bind()
    a.register()
    sessions(name="reviewer").bind()
    assert row_of(a.hash)["revoked_at"] is None


@pytest.mark.parametrize("how", ["file-gone", "root-gone", "pid-reused"])
def test_a_release_of_an_ended_session_revokes_session_ended(rig, sessions, view, how):
    s = sessions().bind()
    s.register()
    if how == "file-gone":
        s.end()
    elif how == "root-gone":
        del view.table[s.root]
    else:
        view.table[s.root] = (1, "another-start")
    s.drop(rig)
    assert wait_until(lambda: row_of(s.hash)["revoked_why"] == "session-ended")
    assert rig.daemon.mcp_tokens.verify(s.token) is None


@pytest.mark.parametrize("how", ["live", "stat-refused", "unparsed-file", "no-roots",
                                 "no-claim-dir"])
def test_a_release_without_positive_evidence_revokes_nothing(rig, sessions, view, how,
                                                             monkeypatch, tmp_path):
    s = sessions().bind()
    s.register()
    if how == "stat-refused":
        view.broken.add(s.root)
    elif how == "unparsed-file":
        claimfile.file_for(s.key).write_text("{not json")
    elif how == "no-roots":
        claimfile.write(s.key, {"key": s.key, "roots": []})
    elif how == "no-claim-dir":
        # No claim directory at all (a daemon whose runtime root isn't the sessions'):
        # no file is proved gone.
        monkeypatch.setattr(claimfile, "file_for",
                            lambda key: tmp_path / "no-such-dir" / f"{key}.json")
    s.drop(rig)
    assert row_of(s.hash)["revoked_at"] is None
    row = rig.daemon.mcp_tokens.verify(s.token)
    assert row is not None and row.identity == PLANNER


def test_a_call_with_an_ended_sessions_token_revokes_it(rig, sessions):
    s = sessions().bind()
    s.register()
    s.end()
    assert rig.daemon.mcp_tokens.verify(s.token) is None
    assert row_of(s.hash)["revoked_why"] == "session-ended"


def test_a_call_whose_evidence_cant_be_read_goes_on(rig, sessions, view):
    s = sessions().bind()
    s.register()
    view.broken.add(s.root)
    assert rig.daemon.mcp_tokens.verify(s.token).identity == PLANNER


def test_unknown_and_revoked_tokens_map_to_nothing(rig, sessions):
    assert rig.daemon.mcp_tokens.verify(tokens.mint()) is None
    assert rig.daemon.mcp_tokens.verify("") is None
    assert rig.daemon.mcp_tokens.verify(None) is None
    s = sessions().bind()
    s.register()
    rig.daemon.mcp_tokens.revoke_all("port-taken")
    assert rig.daemon.mcp_tokens.verify(s.token) is None


def test_the_boot_revokes_ended_sessions_and_keeps_live_ones(rig, sessions, view):
    gone, kept = sessions().bind(), sessions(name="reviewer").bind()
    gone.register()
    kept.register()
    gone.drop(rig)
    kept.drop(rig)
    gone.end()                         # after its release, so only the boot can see it
    assert row_of(gone.hash)["revoked_at"] is None
    restart(rig, view)
    assert row_of(gone.hash)["revoked_why"] == "session-ended"
    assert row_of(kept.hash)["revoked_at"] is None
    assert [r.identity for r in rig.daemon.mcp_tokens.active()] == ["reviewer@proj-a"]


def test_port_taken_revokes_every_active_row(rig, sessions):
    a, b = sessions().bind(), sessions(name="reviewer").bind()
    a.register()
    b.register()
    assert rig.daemon.mcp_tokens.revoke_all("port-taken") == 2
    assert {r["revoked_why"] for r in rows()} == {"port-taken"}
    assert rig.daemon.mcp_tokens.active() == []


def test_uninstall_revokes_every_active_row_in_the_store(rig, sessions):
    a = sessions().bind()
    a.register()
    rig.daemon.mcp_tokens.revoke_all("port-taken")
    b = sessions(name="reviewer").bind()
    b.register()
    conn = bindings.connect()
    try:
        assert tokens.revoke_store(conn) == 1
    finally:
        conn.close()
    assert row_of(a.hash)["revoked_why"] == "port-taken"
    assert row_of(b.hash)["revoked_why"] == "uninstall"


def test_a_revocation_the_store_cant_record_still_holds_in_memory(rig, sessions, caplog):
    s = sessions().bind()
    s.register()
    conn = bindings.connect()
    try:
        conn.execute("CREATE TRIGGER no_revoke BEFORE UPDATE OF revoked_at ON mcp_tokens "
                     "BEGIN SELECT RAISE(ABORT, 'held'); END")
    finally:
        conn.close()
    assert rig.daemon.mcp_tokens.revoke_all("port-taken") == 1
    assert rig.daemon.mcp_tokens.verify(s.token) is None
    assert "didn't record" in caplog.text


def test_an_unknown_cause_is_refused(rig):
    with pytest.raises(ValueError):
        rig.daemon.mcp_tokens.revoke_all("bored")


# =========================================================================== the value

def test_no_column_or_log_line_holds_a_tokens_value(rig, sessions, view, caplog):
    caplog.set_level(logging.DEBUG)
    a = sessions().bind()
    a.register()
    b = sessions(name="reviewer").bind()
    refused(lambda: b.register(a.hash))
    a.drop(rig)
    a.bind()
    a.register()
    rig.daemon.mcp_tokens.verify(a.token)
    a.end()
    rig.daemon.mcp_tokens.verify(a.token)
    b.register()
    rig.daemon.mcp_tokens.revoke_all("port-taken")
    refused(b.register)
    conn = sqlite3.connect(comms_db())
    try:
        cells = [str(v) for r in conn.execute("SELECT * FROM mcp_tokens") for v in r]
    finally:
        conn.close()
    assert len(cells) == 16
    for token in (a.token, b.token):
        assert token not in caplog.text
        assert not any(token in cell for cell in cells)
    assert "mcp-token" in caplog.text or "tool token" in caplog.text


# =========================================================================== the evidence

def _proc_dir(tmp_path, entries: dict):
    base = tmp_path / "proc"
    base.mkdir()
    for pid, start in entries.items():
        (base / str(pid)).mkdir()
        if start is not None:
            (base / str(pid) / "stat").write_text(
                f"{pid} (x y) S 1 " + " ".join(["0"] * 17) + f" {start}\n")
    return ProcView(root=str(base))


def test_the_proc_view_proves_a_root_ended_only_when_it_can(tmp_path):
    view = _proc_dir(tmp_path, {100: "77", 101: None})
    assert not tokens.root_ended(Root(100, "77"), view)          # alive
    assert tokens.root_ended(Root(100, "76"), view)              # pid reused
    assert tokens.root_ended(Root(102, "5"), view)               # no such pid
    assert not tokens.root_ended(Root(101, "5"), view)           # there, unreadable


def test_a_view_that_raises_proves_nothing():
    class Raising(ProcView):
        def stat(self, pid):
            raise OSError("anything")

    assert not tokens.root_ended(Root(5, "1"), Raising())


def test_a_claim_ends_only_when_every_root_has_ended(rig):
    view = View()
    a, b = view.spawn(), view.spawn()
    claimfile.write("k-2", {"roots": [Root(a, f"s{a}").as_dict(),
                                      Root(b, f"s{b}").as_dict()]})
    assert not tokens.claim_ended("k-2", view)
    del view.table[a]
    assert not tokens.claim_ended("k-2", view)
    del view.table[b]
    assert tokens.claim_ended("k-2", view)
    assert not tokens.claim_ended("../k-2", view)
