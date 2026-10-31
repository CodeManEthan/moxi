"""Reserved send-only names: reserving and freeing them, the `moxi.installs` listener,
conflicts, adoption of an older comms' send-only rows, and the token in the credential
chain."""

from __future__ import annotations

import sqlite3

import pytest

from moxi.store import transaction
from moxi_comms import reserved, secrets
from moxi_comms.paths import comms_db
from moxi_comms.store import open_write
from moxi_comms.tokens import TokenStore

LAUNCHER = {"launcher": ["console"]}


@pytest.fixture
def store(tmp_path):
    return TokenStore(tmp_path / "tokens")


@pytest.fixture
def chain(store):
    return secrets.build(["env", "store"], store=store)


def row(name):
    conn = sqlite3.connect(comms_db())
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM reserved WHERE name = ?", (name,)).fetchone()
    finally:
        conn.close()


def intervals(name):
    conn = sqlite3.connect(comms_db())
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM bindings WHERE qualified = ? ORDER BY id",
                            (name,)).fetchall()
    finally:
        conn.close()


def write(sql, params=()):
    conn = open_write()
    try:
        with transaction(conn) as c:
            c.execute(sql, params)
    finally:
        conn.close()


def token_of(store, module, name):
    return store.read_secret(reserved.owner(module), reserved.secret_id(name))


def test_a_declared_name_is_reserved_with_a_token_in_the_chain(chain, store):
    result = reserved.reconcile(declared=LAUNCHER, chain=chain)
    assert result.reserved == [("console", "launcher")]
    assert result.lines == ["launcher: console reserved"]
    r = row("console")
    assert (r["state"], r["module"], r["freed_at"]) == ("active", "launcher", None)
    token = token_of(store, "launcher", "console")
    assert token and r["token_hash"] == reserved.token_hash(token)
    (interval,) = intervals("console")
    assert (interval["kind"], interval["released_at"]) == ("reserved", None)
    assert interval["bound_at"] == r["reserved_at"]


def test_a_second_reconcile_changes_nothing(chain, store):
    reserved.reconcile(declared=LAUNCHER, chain=chain)
    before = (row("console")["token_hash"], token_of(store, "launcher", "console"))
    result = reserved.reconcile(declared=LAUNCHER, chain=chain)
    assert result.lines == []
    assert (row("console")["token_hash"], token_of(store, "launcher", "console")) == before
    assert len(intervals("console")) == 1


def test_uninstall_frees_the_name_while_the_package_is_still_present(chain, store,
                                                                     monkeypatch):
    # The core calls the listener after the module's teardown and before its package
    # goes: the module still reads as installed.
    monkeypatch.setattr(reserved, "declared_names",
                        lambda gone=None: {m: n for m, n in LAUNCHER.items() if m != gone})
    monkeypatch.setattr(reserved, "_chain", lambda c, env: chain)
    reserved.on_install("launcher")
    assert row("console")["state"] == "active"
    reserved.on_uninstall("launcher")
    r = row("console")
    assert r["freed_at"] is not None and r["token_hash"] is None
    assert token_of(store, "launcher", "console") is None
    assert intervals("console")[-1]["released_at"] == r["freed_at"]
    assert reserved.lookup(sqlite3.connect(comms_db()), "console") is None


def test_a_name_no_longer_declared_is_freed_and_reserved_again_with_a_fresh_token(
        chain, store):
    reserved.reconcile(declared=LAUNCHER, chain=chain)
    first = token_of(store, "launcher", "console")
    assert reserved.reconcile(declared={}, chain=chain).freed == [("console", "launcher")]
    reserved.reconcile(declared=LAUNCHER, chain=chain)
    second = token_of(store, "launcher", "console")
    assert second and second != first
    assert row("console")["token_hash"] == reserved.token_hash(second)
    assert [i["released_at"] is None for i in intervals("console")] == [False, True]


def test_comms_own_uninstall_leaves_no_reserved_token(chain, store, monkeypatch):
    reserved.reconcile(declared={"launcher": ["console"], "pager": ["pager"]}, chain=chain)
    monkeypatch.setattr(reserved, "_chain", lambda c, env: chain)
    reserved.on_uninstall("comms")
    for module, name in (("launcher", "console"), ("pager", "pager")):
        assert token_of(store, module, name) is None
        assert row(name)["freed_at"] is not None
    assert not [k for o in store.owners_present()
                for k in store.secret_ids(reserved.owner(o))
                if k.startswith(reserved.SECRET_PREFIX)]


def test_a_standing_identity_is_a_conflict_not_a_reservation(chain, capsys):
    write("INSERT INTO identities(name, scope, created_at) VALUES ('console', 'global', "
          "'2026-01-01T00:00:00Z')")
    result = reserved.reconcile(declared=LAUNCHER, chain=chain)
    assert result.conflicts == [("console", "launcher", "it is a standing identity")]
    assert result.lines == ["launcher: console isn't reserved: it is a standing identity"]
    r = row("console")
    assert (r["state"], r["token_hash"], r["note"]) == ("conflict", None,
                                                        "it is a standing identity")


def test_a_live_binding_is_a_conflict_and_the_next_reconcile_reserves_it(chain):
    write("INSERT INTO bindings(qualified, name, scope, tier, adapter, bound_at) VALUES "
          "('console', 'console', 'global', 'chosen', 'claude-shim', "
          "'2026-01-01T00:00:00Z')")
    assert reserved.reconcile(declared=LAUNCHER, chain=chain).conflicts == [
        ("console", "launcher", "a live session holds it")]
    write("UPDATE bindings SET released_at = '2026-01-01T00:01:00Z'")
    assert reserved.reconcile(declared=LAUNCHER, chain=chain).reserved == [
        ("console", "launcher")]
    assert row("console")["state"] == "active"


def test_two_modules_declaring_one_name_is_a_conflict(chain):
    result = reserved.reconcile(declared={"a": ["console"], "b": ["console"]}, chain=chain)
    assert result.conflicts == [("console", None, "a and b each declare it")]
    assert row("console")["state"] == "conflict"
    # One of them gone: the other gets it.
    assert reserved.reconcile(declared={"a": ["console"], "b": ["console"]}, gone="b",
                              chain=chain).reserved == [("console", "a")]


def test_a_second_declarer_of_a_reserved_name_leaves_it_with_its_owner(chain):
    reserved.reconcile(declared={"a": ["console"]}, chain=chain)
    result = reserved.reconcile(declared={"a": ["console"], "b": ["console"]}, chain=chain)
    assert result.conflicts == [("console", "b", "a has it")]
    assert (row("console")["state"], row("console")["module"]) == ("active", "a")


@pytest.mark.parametrize("name", ["console@proj-a", "two words", "agent-3"])
def test_a_scoped_or_malformed_declaration_is_refused(chain, name):
    result = reserved.reconcile(declared={"launcher": [name]}, chain=chain)
    assert [c[0] for c in result.conflicts] == [name]
    assert row(name)["state"] == "conflict"
    assert result.reserved == []


@pytest.mark.parametrize("name", ["operator", "auto"])
def test_a_name_the_daemon_keeps_is_never_reserved_and_gets_no_row(chain, store, name):
    result = reserved.reconcile(declared={"pager": [name]}, chain=chain)
    assert [c[:2] for c in result.conflicts] == [(name, "pager")]
    assert result.lines == [f"pager: {name} isn't reserved: {result.conflicts[0][2]}"]
    assert result.reserved == []
    assert row(name) is None
    assert token_of(store, "pager", name) is None


def test_an_unowned_row_is_adopted_by_the_first_module_that_declares_it(chain, store):
    write("INSERT INTO reserved(name, state, note) VALUES ('console', 'unowned', 'old')")
    assert reserved.reconcile(declared={}, chain=chain).lines == []
    assert row("console")["state"] == "unowned"
    result = reserved.reconcile(declared=LAUNCHER, chain=chain)
    assert result.adopted == [("console", "launcher")]
    r = row("console")
    assert (r["state"], r["module"], r["note"]) == ("active", "launcher", None)
    assert r["token_hash"] == reserved.token_hash(token_of(store, "launcher", "console"))


def test_a_token_the_chain_cant_read_is_reported_and_never_minted_again(chain, store):
    reserved.reconcile(declared=LAUNCHER, chain=chain)
    digest = row("console")["token_hash"]
    store.delete_secret(reserved.owner("launcher"), reserved.secret_id("console"))
    rows = reserved.doctor_rows(declared=LAUNCHER, chain=chain)
    assert [(r.state, r.detail, r.fix) for r in rows] == [(
        "warn", "comms can't read the token for console from this process's environment",
        "moxi comms reserved renew console")]
    assert row("console")["token_hash"] == digest
    reserved.renew("console", chain=chain)
    token = token_of(store, "launcher", "console")
    assert row("console")["token_hash"] == reserved.token_hash(token)
    assert reserved.token_rows(chain=chain) == []


def test_a_chain_write_that_fails_commits_nothing(store):
    chain = secrets.build(["env"])                  # nothing in it can be written
    result = reserved.reconcile(declared=LAUNCHER, chain=chain)
    assert [f[:2] for f in result.failed] == [("console", "launcher")]
    assert row("console") is None


def test_the_boot_fence_leaves_a_reserved_interval_open(chain):
    from moxi_comms.bindings import BindingHistory

    reserved.reconcile(declared=LAUNCHER, chain=chain)
    history = BindingHistory()
    history.open()
    try:
        assert history.fence() == 0
    finally:
        history.close()
    assert intervals("console")[0]["released_at"] is None


def test_the_listener_is_declared_in_the_core_group():
    from importlib.metadata import entry_points

    (ep,) = [e for e in entry_points(group="moxi.installs") if e.name == "comms"]
    module = ep.load()
    assert module.on_install is reserved.on_install
    assert module.on_uninstall is reserved.on_uninstall


def test_reserved_renew_on_the_cli_mints_into_this_process_chain(capsys):
    from moxi import exit as moxi_exit
    from moxi_comms import cli

    store = TokenStore()                    # the configured store, under the tmp home
    reserved.reconcile(declared=LAUNCHER, chain=secrets.from_config())
    old = token_of(store, "launcher", "console")
    write("UPDATE reserved SET token_hash = 'stale' WHERE name = 'console'")
    assert cli.main(["reserved", "renew", "console"]) == moxi_exit.OK
    new = token_of(store, "launcher", "console")
    assert new != old and row("console")["token_hash"] == reserved.token_hash(new)
    assert "a new token for launcher" in capsys.readouterr().out
    assert cli.main(["reserved", "renew", "pager"]) == moxi_exit.REFUSED


def test_the_token_rests_in_the_keychain_when_there_is_one(store):
    from test_secrets import FakeKeychain

    fake = FakeKeychain()
    chain = secrets.build(["env", "keychain", "store"], store=store, keychain=fake)
    reserved.reconcile(declared=LAUNCHER, chain=chain)
    ((_service, user), token), = fake.kept.items()
    assert user == "launcher:comms/reserved/console"
    assert row("console")["token_hash"] == reserved.token_hash(token)
    assert token_of(store, "launcher", "console") is None
    reserved.reconcile(declared={}, chain=chain)
    assert fake.kept == {}
