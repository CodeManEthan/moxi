"""The token store: what may rest, where, at what mode, and who may spend it.

The store's door is the declared pair, `read_secret` and `write_secret`. A file
that is not a flat table of strings holds no comms key: a read misses, a write refuses.
"""

from __future__ import annotations

import os
import stat
import tomllib

import pytest

from moxi_comms.paths import tokens_dir
from moxi_comms.tokens import (NOT_A_MAP, OPERATOR_SECRET_ID, STORE_ENV_VAR, TokenStore,
                               TokenStoreError, owner_for_role)

POSTING_KEY = OPERATOR_SECRET_ID


# --------------------------------------------------------------------- the location

def test_the_default_location_is_the_device_keyed_state_root():
    """STATE, not DATA: a stored credential is a device credential and DATA is the sync
    surface."""
    assert TokenStore(env={}).root == tokens_dir()
    assert "state" in str(tokens_dir())


def test_the_explicit_override_beats_the_default_state_path(tmp_path):
    """`MOXI_COMMS_HOME` collapses every root, so an alternate-home recipe needs a
    deliberate way to still name the operator's real store."""
    store = TokenStore(env={STORE_ENV_VAR: str(tmp_path / "elsewhere")})
    assert store.root == tmp_path / "elsewhere"

    store.write_secret(owner_for_role("operator"), POSTING_KEY, "OPERATOR-TOKEN")
    assert (tmp_path / "elsewhere" / "operator").exists()
    assert not tokens_dir().exists()


def test_the_holder_file_names(tmp_path):
    store = TokenStore(tmp_path)
    assert store.path_for(owner_for_role("daemon")) == tmp_path / "daemon"
    assert store.path_for(owner_for_role("reader")) == tmp_path / "daemon"   # one holder
    assert store.path_for(owner_for_role("operator")) == tmp_path / "operator"


def test_a_role_that_is_still_unknown_has_no_store_path_at_all(tmp_path):
    """The role set is closed by construction, not by a list somewhere: an unknown role
    gets an owner with no PARTS, and the refusal happens where the path is computed. The
    message is the generic no-owner one -- there is no role-specific error to extend, and
    a box adding a role names it in `[credentials] roles` (test_roles.py)."""
    store = TokenStore(tmp_path)
    unknown = owner_for_role("stenographer")
    assert unknown.parts == () and unknown.role == "stenographer"
    with pytest.raises(TokenStoreError) as e:
        store.path_for(unknown)
    assert "no owner" in str(e.value)
    assert "stenographer" not in str(e.value)


def test_the_stores_door_is_the_declared_pair():
    assert callable(TokenStore.read_secret) and callable(TokenStore.write_secret)
    assert not hasattr(TokenStore, "read") and not hasattr(TokenStore, "write")


# --------------------------------------------------------------------- permissions

def test_the_store_is_written_0600_in_a_0700_directory(tmp_path):
    store = TokenStore(tmp_path / "tokens")
    path = store.write_secret(owner_for_role("operator"), POSTING_KEY, "OPERATOR-TOKEN")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700


def test_a_loose_file_is_REFUSED_not_warned_about(tmp_path):
    """Decided here: 0600 is the whole mechanism. A store that reads a
    world-readable credential anyway has a comment where its rule was."""
    store = TokenStore(tmp_path)
    owner = owner_for_role("operator")
    path = store.write_secret(owner, POSTING_KEY, "OPERATOR-TOKEN")
    os.chmod(path, 0o644)
    with pytest.raises(TokenStoreError) as e:
        store.read_secret(owner, POSTING_KEY)
    assert "0600" in str(e.value)
    assert str(path) not in str(e.value)      # the path goes to the log, not to an agent


def test_a_loose_store_directory_is_refused_too(tmp_path):
    store = TokenStore(tmp_path / "tokens")
    owner = owner_for_role("operator")
    store.write_secret(owner, POSTING_KEY, "OPERATOR-TOKEN")
    os.chmod(store.root, 0o755)
    with pytest.raises(TokenStoreError):
        store.read_secret(owner, POSTING_KEY)


# --------------------------------------------------------------------- the fences

def test_the_provisioner_role_cannot_even_have_a_file_written_for_it(tmp_path):
    with pytest.raises(TokenStoreError) as e:
        TokenStore(tmp_path).write_secret(owner_for_role("provisioner"), POSTING_KEY,
                                          "MASTER-ISH")
    assert "crown-jewel" in str(e.value)
    assert not any(tmp_path.rglob("*"))


def test_the_provisioner_is_refused_by_the_declared_read_too(tmp_path):
    with pytest.raises(TokenStoreError) as e:
        TokenStore(tmp_path).read_secret(owner_for_role("provisioner"), POSTING_KEY)
    assert "crown-jewel" in str(e.value)
    assert not any(tmp_path.rglob("*"))


# ------------------------------------------------------------ the map and the pair
#
# The holder file is a flat TOML map, and every value goes in and out through
# `read_secret` / `write_secret`.

def test_read_secret_and_write_secret_round_trip_at_0600_in_0700(tmp_path):
    """The declared pair, both halves, with the 0600/0700 modes."""
    store = TokenStore(tmp_path / "tokens")
    owner = owner_for_role("operator")
    path = store.write_secret(owner, POSTING_KEY, "POSTING-TOKEN-0001")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert store.read_secret(owner, POSTING_KEY) == "POSTING-TOKEN-0001"
    assert tomllib.loads(path.read_text()) == {POSTING_KEY: "POSTING-TOKEN-0001"}
    assert store.secret_ids(owner) == [POSTING_KEY]
    assert store.read_secret(owner, "comms/operator/other") is None


def test_one_key_is_written_without_disturbing_the_others(tmp_path):
    store = TokenStore(tmp_path)
    owner = owner_for_role("operator")
    store.write_secret(owner, "ANTHROPIC_API_KEY", "ROLE-CREDENTIAL")
    store.write_secret(owner, POSTING_KEY, "NAMES-TOKEN")
    store.write_secret(owner, POSTING_KEY, "NAMES-TOKEN-ROTATED")
    assert store.read_secret(owner, "ANTHROPIC_API_KEY") == "ROLE-CREDENTIAL"
    assert store.read_secret(owner, POSTING_KEY) == "NAMES-TOKEN-ROTATED"
    assert store.secret_ids(owner) == ["ANTHROPIC_API_KEY", POSTING_KEY]


def test_the_emitter_escapes_a_value_that_looks_like_toml(tmp_path):
    """The stdlib reads TOML and does not write it, so the emitter is ours: a value with
    a quote, a backslash, an `=` and a newline in it comes back byte for byte."""
    nasty = 'a"b\\c=d\ne\tf'
    store = TokenStore(tmp_path)
    owner = owner_for_role("operator")
    store.write_secret(owner, 'weird"key\\here', nasty)
    assert store.read_secret(owner, 'weird"key\\here') == nasty.strip()
    assert tomllib.loads(store.path_for(owner).read_text()) == {
        'weird"key\\here': nasty.strip()}


def test_the_declared_pair_refuses_an_empty_value_and_an_unprintable_key(tmp_path):
    store = TokenStore(tmp_path)
    with pytest.raises(TokenStoreError):
        store.write_secret(owner_for_role("operator"), POSTING_KEY, "   ")
    with pytest.raises(TokenStoreError):
        store.write_secret(owner_for_role("operator"), "two\nlines", "TOK")
    with pytest.raises(TokenStoreError):
        store.read_secret(owner_for_role("operator"), "")
    assert not any(tmp_path.rglob("*"))


def test_a_loose_file_is_refused_by_every_half_of_the_boundary(tmp_path):
    """0600 is the whole mechanism, so the pair and the key-level question refuse
    alike."""
    store = TokenStore(tmp_path)
    owner = owner_for_role("operator")
    path = store.write_secret(owner, POSTING_KEY, "POSTING-TOKEN-0001")
    os.chmod(path, 0o644)
    for call in (lambda: store.read_secret(owner, POSTING_KEY),
                 lambda: store.secret_ids(owner),
                 lambda: store.write_secret(owner, POSTING_KEY, "NEXT")):
        with pytest.raises(TokenStoreError) as e:
            call()
        assert "0600" in str(e.value)
        assert str(path) not in str(e.value)


@pytest.mark.parametrize("bare", ["0.credential.machine:token", "0.abc=.def:ghi",
                                  "aGVsbG8=", "a=b", "x = 3"])
def test_a_file_that_is_not_a_flat_table_of_strings_holds_no_key_and_is_left_alone(
        tmp_path, bare):
    """A bare token (what an older install's paste verb wrote) or any other shape is not
    the map this store writes: it holds no comms key, so a read misses; a write
    refuses it with `NOT_A_MAP`, naming no path, and never overwrites
    it."""
    store = TokenStore(tmp_path)
    owner = owner_for_role("operator")
    path = tmp_path / "operator"
    path.write_text(bare + "\n")
    os.chmod(path, 0o600)
    os.chmod(tmp_path, 0o700)
    assert store.read_secret(owner, POSTING_KEY) is None
    assert store.secret_ids(owner) == []
    with pytest.raises(TokenStoreError) as e:
        store.write_secret(owner, POSTING_KEY, "NEXT")
    assert str(e.value) == NOT_A_MAP and str(path) not in str(e.value)
    assert path.read_text() == bare + "\n"
