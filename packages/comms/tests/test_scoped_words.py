"""The words modules declare for scoped senders: the entry-point group, the reconcile that
mints each word's token, a word two modules declare, and what the doctor says."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from moxi import modules
from moxi_comms import reserved, scoped, secrets
from moxi_comms.paths import comms_db
from moxi_comms.tokens import TokenStore

DIGEST = {"pager": {"digest": "the nightly digest a session gets"}}


@pytest.fixture
def store(profile):
    return TokenStore(profile / "tokens")


@pytest.fixture
def chain(store):
    return secrets.build(["env", "store"], store=store)


def word_row(word):
    conn = sqlite3.connect(comms_db())
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM scoped WHERE word = ?", (word,)).fetchone()
    finally:
        conn.close()


def token_of(store, module, word):
    return store.read_secret(reserved.owner(module), scoped.secret_id(word))


def entry(word, value, dist="moxi-pager"):
    def load():
        if isinstance(value, Exception):
            raise value
        return value
    return SimpleNamespace(name=word, load=load,
                           dist=SimpleNamespace(metadata={"Name": dist}))


def found(name, state=modules.PRESENT, dist=None):
    return SimpleNamespace(name=name, state=state, distribution=dist or f"moxi-{name}")


# --------------------------------------------------------------------------- the group

def test_the_group_gives_each_word_to_the_module_whose_distribution_carries_it():
    got = scoped.load(entries=[entry("digest", "the nightly digest"),
                               entry("recall", "recalls", dist="moxi_memory")],
                      found=[found("pager"), found("memory")])
    assert got.words == {"pager": {"digest": "the nightly digest"},
                         "memory": {"recall": "recalls"}}
    assert got.broken == [] and got.kept == set()


def test_a_word_of_a_distribution_that_is_no_module_is_left_out():
    got = scoped.load(entries=[entry("digest", "x", dist="some-tool")],
                      found=[found("pager")])
    assert got.words == {}
    (b,) = got.broken
    assert (b.word, b.module) == ("digest", None)
    assert "some-tool is no installed Moxi module" in b.reason


@pytest.mark.parametrize("word,value,why", [
    ("Digest", "x", "is not a word"),
    ("night-digest", "x", "is not a word"),
    ("digest", {"text": "x"}, "not a line of text"),
    ("digest", "two\nlines", "not one line of text"),
    ("digest", "   ", "not one line of text"),
    ("digest", "x" * 201, "over 200 characters"),
])
def test_a_declaration_that_doesnt_hold_is_left_out(word, value, why):
    got = scoped.load(entries=[entry(word, value)], found=[found("pager")])
    assert got.words == {}
    (b,) = got.broken
    assert why in b.reason and b.module == "pager"


def test_a_declaration_that_doesnt_load_keeps_its_word():
    got = scoped.load(entries=[entry("digest", ImportError("gone"))],
                      found=[found("pager", state=modules.BROKEN)])
    assert got.words == {} and got.kept == {"digest"}


def test_the_gone_module_declares_nothing():
    got = scoped.load("pager", entries=[entry("digest", "x")], found=[found("pager")])
    assert got.words == {} and got.broken == []


def test_comms_reads_the_real_group_without_failing():
    got = scoped.load()
    assert isinstance(got.words, dict)


# --------------------------------------------------------------------------- the reconcile

def test_a_declared_word_is_active_with_a_token_for_its_module(chain, store):
    result = reserved.reconcile(declared={}, words=DIGEST, chain=chain)
    assert result.words == [("digest", "pager")]
    assert "pager: the scoped sender word digest declared" in result.lines
    r = word_row("digest")
    assert (r["state"], r["module"], r["declared_by"], r["freed_at"]) == \
        ("active", "pager", "pager", None)
    assert r["description"] == "the nightly digest a session gets"
    token = token_of(store, "pager", "digest")
    assert token and r["token_hash"] == reserved.token_hash(token)


def test_a_second_reconcile_changes_nothing(chain, store):
    reserved.reconcile(declared={}, words=DIGEST, chain=chain)
    before = (word_row("digest")["token_hash"], token_of(store, "pager", "digest"))
    result = reserved.reconcile(declared={}, words=DIGEST, chain=chain)
    assert result.lines == [] and result.words == []
    assert (word_row("digest")["token_hash"], token_of(store, "pager", "digest")) == before


def test_a_word_no_module_declares_now_is_freed_and_its_token_removed(chain, store):
    reserved.reconcile(declared={}, words=DIGEST, chain=chain)
    result = reserved.reconcile(declared={}, words={}, chain=chain)
    assert result.words_freed == [("digest", "pager")]
    r = word_row("digest")
    assert r["freed_at"] is not None and r["token_hash"] is None
    assert token_of(store, "pager", "digest") is None


def test_an_uninstall_frees_the_gone_modules_words(chain, store):
    reserved.reconcile(declared={}, words=DIGEST, chain=chain)
    result = reserved.reconcile("pager", declared={}, words=DIGEST, chain=chain)
    assert result.words_freed == [("digest", "pager")]
    assert token_of(store, "pager", "digest") is None


def test_a_word_two_modules_declare_is_in_conflict_for_both(chain, store):
    reserved.reconcile(declared={}, words=DIGEST, chain=chain)
    both = {**DIGEST, "memory": {"digest": "another digest"}}
    result = reserved.reconcile(declared={}, words=both, chain=chain)
    assert result.word_conflicts == [("digest", None, "memory and pager each declare it")]
    r = word_row("digest")
    assert (r["state"], r["module"], r["declared_by"], r["token_hash"]) == \
        ("conflict", None, "memory pager", None)
    assert token_of(store, "pager", "digest") is None
    # Once one of them stops declaring it, the other's word is active.
    reserved.reconcile(declared={}, words={"memory": {"digest": "another digest"}},
                       chain=chain)
    r = word_row("digest")
    assert (r["state"], r["module"]) == ("active", "memory")
    assert token_of(store, "memory", "digest")


def test_a_word_whose_declaration_doesnt_load_keeps_its_row(chain, monkeypatch):
    reserved.reconcile(declared={}, words=DIGEST, chain=chain)
    monkeypatch.setattr(scoped, "load", lambda gone=None: scoped.Declared(kept={"digest"}))
    result = reserved.reconcile(declared={})
    assert result.words_freed == []
    assert word_row("digest")["state"] == "active"


def test_comms_uninstall_frees_every_word(chain, store):
    reserved.reconcile(declared={}, words=DIGEST, chain=chain)
    result = reserved.release_all(chain=chain)
    assert ("digest", "pager") in result.words_freed
    assert word_row("digest")["freed_at"] is not None
    assert token_of(store, "pager", "digest") is None


def test_renew_word_replaces_the_token(chain, store):
    reserved.reconcile(declared={}, words=DIGEST, chain=chain)
    old = token_of(store, "pager", "digest")
    reserved.renew_word("digest", chain=chain)
    new = token_of(store, "pager", "digest")
    assert new != old and word_row("digest")["token_hash"] == reserved.token_hash(new)
    with pytest.raises(KeyError):
        reserved.renew_word("nothing", chain=chain)


# --------------------------------------------------------------------------- the doctor

def test_a_reconcile_names_a_word_in_conflict_and_a_bad_declaration(chain):
    both = {**DIGEST, "memory": {"digest": "x"}, "notes": {"Bad": "x"}}
    result = reserved.reconcile(declared={}, words=both, chain=chain)
    assert ("digest", None, "memory and pager each declare it") in result.word_conflicts
    assert any(w == "Bad" for w, _m, _why in result.word_conflicts)


def test_the_doctor_rows_say_the_word_conflict(chain, monkeypatch):
    both = {**DIGEST, "memory": {"digest": "x"}}
    monkeypatch.setattr(scoped, "load", lambda gone=None: scoped.from_mapping(both))
    rows = reserved.doctor_rows(declared={}, chain=chain)
    texts = [r.detail for r in rows if r.name == "scoped"]
    assert "a module: the scoped sender word digest isn't declared: memory and pager " \
           "each declare it" in texts


def test_the_doctor_says_a_word_token_this_process_cant_read(chain, store, monkeypatch):
    monkeypatch.setattr(scoped, "load", lambda gone=None: scoped.from_mapping(DIGEST))
    reserved.reconcile(declared={}, chain=chain)
    store.delete_secret(reserved.owner("pager"), scoped.secret_id("digest"))
    rows = [r for r in reserved.doctor_rows(declared={}, chain=chain)
            if r.name == "scoped"]
    (row,) = rows
    assert "can't read pager's token for the scoped sender word digest" in row.detail
    assert row.fix == "moxi comms reserved renew --word digest"
