"""A program's send-only name: registering it, renewing its token and removing it, each
straight against comms.db and the credential chain, with the daemon down."""

from __future__ import annotations

import sqlite3

import pytest

from moxi.store import transaction
from moxi_comms import reserved, secrets
from moxi_comms.paths import comms_db
from moxi_comms.store import open_write
from moxi_comms.tokens import TokenStore


@pytest.fixture
def store(tmp_path):
    return TokenStore(tmp_path / "tokens")


@pytest.fixture
def chain(store):
    return secrets.build(["env", "store"], env={}, store=store)


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
        return conn.execute("SELECT * FROM bindings WHERE qualified = ? AND kind = "
                            "'reserved' ORDER BY id", (name,)).fetchall()
    finally:
        conn.close()


def write(sql, params=()):
    conn = open_write()
    try:
        with transaction(conn) as c:
            c.execute(sql, params)
    finally:
        conn.close()


def token(store, name="ticker"):
    return store.read_secret(reserved.program_owner(), reserved.program_secret_id(name))


def snapshot():
    conn = sqlite3.connect(comms_db())
    try:
        return (conn.execute("SELECT * FROM reserved ORDER BY name").fetchall(),
                conn.execute("SELECT * FROM bindings ORDER BY id").fetchall())
    finally:
        conn.close()


# --------------------------------------------------------------------------- add

def test_add_mints_a_token_and_writes_a_programs_row_with_its_interval(chain, store):
    result = reserved.register("ticker", chain=chain)
    assert result.lines == ["ticker registered (token in the store step)"]
    r = row("ticker")
    assert (r["state"], r["holder"], r["module"], r["freed_at"]) == (
        "active", "program", None, None)
    assert r["token_hash"] == reserved.token_hash(token(store))
    assert chain.get("comms/send-only/ticker",
                     owner=reserved.program_owner())["value"] == token(store)
    (interval,) = intervals("ticker")
    assert (interval["released_at"], interval["bound_at"]) == (None, r["reserved_at"])


def test_add_again_mints_nothing(chain, store):
    reserved.register("ticker", chain=chain)
    before = (row("ticker")["token_hash"], token(store))
    result = reserved.register("ticker", chain=chain)
    assert result.lines == ["ticker is already registered"] and not result.unreadable
    assert (row("ticker")["token_hash"], token(store)) == before
    assert len(intervals("ticker")) == 1


def test_add_again_names_renew_when_this_process_cant_read_the_token(chain, store):
    reserved.register("ticker", chain=chain)
    store.delete_secret(reserved.program_owner(), reserved.program_secret_id("ticker"))
    result = reserved.register("ticker", chain=chain)
    assert result.lines[0] == "ticker is already registered"
    assert "moxi comms send-only renew ticker" in result.lines[1]
    assert [n for n, _why in result.unreadable] == ["ticker"]
    assert row("ticker")["token_hash"] is not None


def module_rows():
    reserved.reconcile(declared={"launcher": ["console"], "pager": ["busy"],
                                 "herald": ["busy"]},
                       chain=secrets.build(["store"], store=TokenStore()))


@pytest.mark.parametrize("name, words", [
    ("ticker@proj-a", "names a scope"),
    ("agent-3", "agent-N names are the daemon's"),
    ("operator", "the operator's name"),
    ("auto", "keeps it"),
    ("console", "console is reserved for launcher"),
    ("busy", "busy is declared by more than one module"),
    ("old", "old is retired"),
    ("planner", "it is a standing identity"),
    ("worker", "a live session holds it"),
])
def test_add_refuses_a_name_someone_has_and_writes_nothing(chain, name, words):
    module_rows()
    write("INSERT INTO identities(name, scope, status, created_at) VALUES "
          "('old', 'global', 'retired', 't'), ('planner', 'global', 'active', 't')")
    write("INSERT INTO bindings(qualified, name, scope, tier, adapter, bound_at) VALUES "
          "('worker', 'worker', 'global', 'chosen', 'claude', 't')")
    before = snapshot()
    with pytest.raises(reserved.Refused, match=words):
        reserved.register(name, chain=chain)
    assert snapshot() == before


def past(*, bound=False, waiting=0):
    if bound:
        write("INSERT INTO bindings(qualified, name, scope, tier, adapter, bound_at, "
              "released_at) VALUES ('ticker', 'ticker', 'global', 'chosen', 'claude', "
              "'2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z')")
    for i in range(waiting):
        write("INSERT INTO queue(name, message_id, queued_at) VALUES ('ticker', ?, "
              "'2026-01-01T00:00:00Z')", (i + 1,))


def test_add_refuses_a_name_a_session_held_before_unless_taken(chain, store):
    past(bound=True)
    before = snapshot()
    with pytest.raises(reserved.Refused, match=r"a session has held it before .*--take"):
        reserved.register("ticker", chain=chain)
    assert snapshot() == before and token(store) is None
    result = reserved.register("ticker", take=True, chain=chain)
    assert result.lines == ["ticker registered (token in the store step)"]
    assert row("ticker")["holder"] == "program"


def test_add_refuses_a_name_with_mail_waiting_saying_how_much_unless_taken(chain):
    past(waiting=2)
    with pytest.raises(reserved.Refused, match="2 message"):
        reserved.register("ticker", chain=chain)
    result = reserved.register("ticker", take=True, chain=chain)
    assert result.lines[-1] == "2 message(s) waiting for ticker will be dropped"
    (interval,) = intervals("ticker")
    assert interval["bound_at"] >= "2026-01-01T00:00:00Z"


def test_add_adopts_an_unowned_row_without_take_even_with_session_history(chain, store):
    write("INSERT INTO reserved(name, state, note) VALUES ('ticker', 'unowned', 'kept')")
    past(bound=True)
    result = reserved.register("ticker", chain=chain)
    assert result.lines == ["ticker registered (adopted from an older comms; token in the "
                            "store step)"]
    r = row("ticker")
    assert (r["state"], r["holder"], r["note"]) == ("active", "program", None)
    assert r["token_hash"] == reserved.token_hash(token(store))


def test_add_says_which_step_shadows_the_new_token(store):
    """A token left in the environment is read before the one just minted: `add` says
    which variable holds it."""
    env = {secrets.env_key("comms/send-only/ticker"): "an-old-token"}
    chain = secrets.build(["env", "store"], env=env, store=store)
    result = reserved.register("ticker", chain=chain)
    assert result.unreadable
    assert ("this process can't send under ticker: the environment variable "
            "MOXI_COMMS_SECRET_COMMS_SEND_ONLY_TICKER holds an older token") in result.lines[-1]


def test_a_chain_write_that_fails_registers_nothing(chain, store):
    open_write().close()
    before = snapshot()
    result = reserved.register("ticker", chain=secrets.build(["env"], env={}))
    assert result.failed and "isn't registered" in result.lines[0]
    assert snapshot() == before


# --------------------------------------------------------------------------- renew

def test_renew_replaces_the_hash_and_the_old_token_in_every_step(chain, store):
    from test_secrets import FakeKeychain

    keychain = FakeKeychain()
    both = secrets.build(["keychain", "store"], store=store, keychain=keychain)
    reserved.register("ticker", chain=chain)            # into the store
    old = token(store)
    keychain.set_password(secrets.KEYCHAIN_SERVICE,
                          "send-only:comms/send-only/ticker", old)
    result = reserved.renew_program("ticker", chain=both)
    ((_key, new),) = keychain.kept.items()
    assert new != old and token(store) is None
    assert row("ticker")["token_hash"] == reserved.token_hash(new)
    assert result.lines == ["ticker: a new token, in the keychain step; the old one is "
                            "refused from now on"]


def test_renew_names_a_step_it_couldnt_reach(chain, store):
    from test_secrets import FakeKeychain

    class Unusable(FakeKeychain):
        priority = 0

    reserved.register("ticker", chain=chain)
    result = reserved.renew_program("ticker", chain=secrets.build(
        ["keychain", "store"], store=store, keychain=Unusable()))
    assert ("ticker: the keychain step couldn't be reached from here; a copy of the old "
            "token there stays, and is refused") in result.lines


def test_renew_says_when_an_env_variable_still_shadows_the_new_token(store):
    env = {secrets.env_key("comms/send-only/ticker"): "an-old-token"}
    reserved.register("ticker", chain=secrets.build(["store"], store=store))
    result = reserved.renew_program("ticker", chain=secrets.build(["env", "store"], env=env,
                                                                 store=store))
    assert [n for n, _why in result.unreadable] == ["ticker"]
    assert "MOXI_COMMS_SECRET_COMMS_SEND_ONLY_TICKER holds an older token" in result.lines[-1]


@pytest.mark.parametrize("verb", [reserved.renew_program, reserved.remove_program])
def test_renew_and_remove_refuse_a_modules_name_naming_the_module(chain, verb):
    module_rows()
    before = snapshot()
    with pytest.raises(reserved.Refused, match="console is reserved for launcher"):
        verb("console", chain=chain)
    assert snapshot() == before


def test_renew_refuses_a_name_no_program_registered(chain):
    with pytest.raises(reserved.Refused, match="moxi comms send-only add ticker"):
        reserved.renew_program("ticker", chain=chain)
    write("INSERT INTO reserved(name, state) VALUES ('herald', 'unowned')")
    with pytest.raises(reserved.Refused, match=r"\(an older comms kept it\)"):
        reserved.renew_program("herald", chain=chain)


# --------------------------------------------------------------------------- remove

def test_remove_frees_the_name_closes_its_interval_and_removes_its_token(chain, store):
    reserved.register("ticker", chain=chain)
    result = reserved.remove_program("ticker", chain=chain)
    assert result.lines == ["ticker removed: it is an ordinary name again"]
    r = row("ticker")
    assert r["freed_at"] is not None and r["token_hash"] is None
    assert intervals("ticker")[-1]["released_at"] == r["freed_at"]
    assert token(store) is None
    conn = sqlite3.connect(comms_db())
    conn.row_factory = sqlite3.Row
    assert reserved.lookup(conn, "ticker") is None
    conn.close()


def test_remove_frees_an_unowned_row(chain):
    write("INSERT INTO reserved(name, state) VALUES ('herald', 'unowned')")
    reserved.remove_program("herald", chain=chain)
    assert row("herald")["freed_at"] is not None


def test_remove_says_where_a_module_name_goes(chain):
    module_rows()
    with pytest.raises(reserved.Refused, match="it goes when launcher is uninstalled"):
        reserved.remove_program("console", chain=chain)


def test_a_removed_name_can_be_registered_again_with_a_fresh_token(chain, store):
    reserved.register("ticker", chain=chain)
    first = token(store)
    reserved.remove_program("ticker", chain=chain)
    reserved.register("ticker", chain=chain)
    assert token(store) not in (None, first)
    assert [i["released_at"] is None for i in intervals("ticker")] == [False, True]


# --------------------------------------------------------------------------- the verbs

def cli_run(argv, capsys):
    from moxi_comms import cli

    code = cli.main(argv)
    return code, capsys.readouterr().out


def test_the_verbs_register_renew_and_remove_on_the_command_line(capsys):
    from moxi import exit as moxi_exit

    configured = TokenStore()                       # the configured store, under tmp
    code, out = cli_run(["send-only", "add", "ticker"], capsys)
    assert code == moxi_exit.OK, out
    assert out.startswith("ticker registered (token in the ")
    first = token(configured)
    assert row("ticker")["token_hash"] == reserved.token_hash(first)
    code, out = cli_run(["send-only", "add", "ticker"], capsys)
    assert (code, out) == (moxi_exit.OK, "ticker is already registered\n")
    assert token(configured) == first
    code, out = cli_run(["send-only", "renew", "ticker"], capsys)
    assert code == moxi_exit.OK and "ticker: a new token, in the " in out, out
    assert token(configured) != first
    code, out = cli_run(["send-only", "remove", "ticker"], capsys)
    assert code == moxi_exit.OK and "ticker removed: it is an ordinary name again" in out
    code, out = cli_run(["send-only", "remove", "ticker"], capsys)
    assert code == moxi_exit.REFUSED
    assert out == "ticker isn't a registered send-only name; nothing was changed\n"


def test_add_again_exits_refused_when_this_process_cant_read_the_token(capsys):
    """A program that runs `add` at every start tells `fine` from `renew needed` by the
    exit code."""
    from moxi import exit as moxi_exit

    configured = TokenStore()
    assert cli_run(["send-only", "add", "ticker"], capsys)[0] == moxi_exit.OK
    configured.delete_secret(reserved.program_owner(), reserved.program_secret_id("ticker"))
    code, out = cli_run(["send-only", "add", "ticker"], capsys)
    assert code == moxi_exit.REFUSED
    assert out.splitlines()[0] == "ticker is already registered"
    assert "moxi comms send-only renew ticker" in out.splitlines()[1]
    assert row("ticker")["holder"] == reserved.PROGRAM_HOLDER


def test_a_refused_verb_says_why_and_changes_nothing(capsys):
    from moxi import exit as moxi_exit

    reserved.reconcile(declared={"launcher": ["console"]}, chain=secrets.from_config())
    before = snapshot()
    for argv, words in [
            (["send-only", "add", "console"], "console is reserved for launcher: a program "
                                              "can't register it"),
            (["send-only", "add", "ticker@proj"], "ticker@proj can't be registered"),
            (["send-only", "renew", "console"], "console is reserved for launcher: moxi "
                                                "comms reserved renew console"),
            (["send-only", "remove", "console"], "it goes when launcher is uninstalled")]:
        code, out = cli_run(argv, capsys)
        assert code == moxi_exit.REFUSED, (argv, out)
        assert words in out and out.rstrip().endswith("; nothing was changed"), (argv, out)
    assert snapshot() == before


def test_a_held_lock_is_a_try_again_and_a_failed_write_an_error(monkeypatch, capsys):
    from moxi import exit as moxi_exit
    from moxi.files import LockTimeout

    def held(*args, **kwargs):
        raise LockTimeout("reserved.lock", 4242)

    monkeypatch.setattr(reserved, "register", held)
    code, out = cli_run(["send-only", "add", "ticker"], capsys)
    assert code == moxi_exit.TEMPFAIL and "Try again in a moment" in out

    def failed(*args, **kwargs):
        return reserved.Result(failed=[("ticker", None, "no")],
                               lines=["ticker isn't registered: its token couldn't be "
                                      "written. no"])

    monkeypatch.setattr(reserved, "register", failed)
    code, out = cli_run(["send-only", "add", "ticker"], capsys)
    assert code == moxi_exit.ERROR and "couldn't be written" in out
