"""The credential chain: one way to turn a secret id into a value, three places to look.

The operator lane resolves its posting credential through this chain, and a module that
sends under a reserved name reads its token through it. The chain is declared in
config.toml:

    [credentials]
    chain = ["env", "keychain", "store"]

and the interface is three methods:

    Chain.get(secret_id, *, owner=None) -> {"value": ..., "note": ...}
    Chain.put(secret_id, value, *, owner) -> the step that took it
    Chain.delete(secret_id, *, owner) -> the steps that held it

THE STEPS, in the order the file declares them:

  1. `env` -- `MOXI_COMMS_SECRET_<KEY>`, for comms' own credentials. The KEY is the
     secret id upper-cased with every run of non-alphanumerics folded to one underscore
     (`env_key` below), so `comms/operator/token` is
     `MOXI_COMMS_SECRET_COMMS_OPERATOR_TOKEN`
     and a role credential already named like a variable (`ANTHROPIC_API_KEY`) keeps its
     own spelling under the prefix. It is read, never written.
  2. `keychain` -- the user's keychain through `keyring` (the macOS Keychain, Windows
     Credential Manager, the Secret Service), imported inside the step so no hook path
     pays for it. A box with no usable keychain backend (a headless Linux box without a
     Secret Service) misses here; it doesn't fail.
  3. `store` -- the token store's declared read, `TokenStore.read_secret(owner,
     secret_id)`, with the `owner` that rides down from the caller. A store hit has no
     note: the store is a flat key/value map.

A write goes to the first step that can be written: the keychain when it has a backend,
else the store.

A MISS IS NOT A FAILURE AND A FAILURE IS NOT A MISS. A step that does not hold the
secret falls through to the next one. A step that BREAKS -- a store file the rest of the
box can read, a keychain backend that raises -- is loud and names the step, because "the
credential is not here" and "I could not tell whether the credential is here" are
different sentences and only one of them is safe to answer with the next step's answer.
When every step misses, one error names the steps tried and the holder, in the caller's
language (the send verb prints it).
"""

from __future__ import annotations

import logging
import os
import re

from .record import RecordError

log = logging.getLogger("comms.secrets")

#: The env step's prefix. `MOXI_COMMS_SECRET_<KEY>`.
ENV_PREFIX = "MOXI_COMMS_SECRET_"

STEP_ENV = "env"
STEP_KEYCHAIN = "keychain"
STEP_STORE = "store"

#: The steps, in their default order. A name outside this tuple is refused at config
#: load (`config.load`), not at resolve time: a typo in the chain must fail the box's
#: first read of its own config, not the first send of the day.
STEPS = (STEP_ENV, STEP_KEYCHAIN, STEP_STORE)

#: The keychain service every comms secret is kept under; the user name is
#: `<holder>:<secret id>`.
KEYCHAIN_SERVICE = "moxi-comms"

#: What a box whose config sets no `[credentials] chain` gets (settings.toml's default).
DEFAULT_CHAIN = STEPS


def default_chain() -> tuple:
    """The chain for a box whose config names none."""
    return DEFAULT_CHAIN


class SecretError(RecordError):
    """A chain refusal. A `RecordError`, so the send verb's exit 5 is unchanged."""


class SecretNotFound(SecretError):
    """Every step missed. Names the steps tried and the holder."""


class SecretStepFailed(SecretError):
    """One step BROKE. Named, and never quietly treated as a miss."""


def env_key(secret_id: str) -> str:
    """The env step's variable name for one secret id, stated once and used everywhere.

    Every run of characters that is not a letter or a digit folds to one underscore and
    the rest upper-cases: `comms/operator/token` becomes
    `MOXI_COMMS_SECRET_COMMS_OPERATOR_TOKEN`,
    `ANTHROPIC_API_KEY` -> `MOXI_COMMS_SECRET_ANTHROPIC_API_KEY`. Two ids that differ only in
    punctuation therefore share a variable, which is why this is the step for comms'
    own credentials with named keys.
    """
    folded = re.sub(r"[^A-Za-z0-9]+", "_", str(secret_id)).strip("_")
    return ENV_PREFIX + folded.upper()


def _holder(owner) -> str:
    """WHO the value was asked for, for an error sentence. Never a path, never a value."""
    return str(owner) if owner is not None else "this process's own holder"


class Resolver:
    """One secret id in, `{"value", "note"}` out, or a miss.

    `get` returns None for a MISS (this resolver does not hold it) and raises
    `SecretStepFailed` when it could not tell. `name` is what the chain's error calls it
    and `describe` is the phrase that reads inside a sentence.
    """

    name = ""

    def describe(self, secret_id: str) -> str:
        return self.name

    def get(self, secret_id: str, *, owner=None) -> dict | None:
        raise NotImplementedError

    def put(self, secret_id: str, value: str, *, owner) -> bool:
        """Write `value` under `secret_id` for `owner`: True when this step took it,
        False when this step can't be written (the chain then tries the next one).
        Raises `SecretStepFailed` when the write broke."""
        return False

    def delete(self, secret_id: str, *, owner) -> bool:
        """Remove `secret_id` for `owner` from this step: True when it held it. Raises
        `SecretStepFailed` when the step broke."""
        return False


class EnvResolver(Resolver):
    """Step 1: `MOXI_COMMS_SECRET_<KEY>` out of the injected environment. No note."""

    name = STEP_ENV

    def __init__(self, env=None):
        self.env = env if env is not None else os.environ

    def describe(self, secret_id: str) -> str:
        return f"the environment ({env_key(secret_id)})"

    def get(self, secret_id: str, *, owner=None) -> dict | None:
        value = (self.env.get(env_key(secret_id)) or "").strip()
        if not value:
            return None
        log.info("secret %s: from the environment", secret_id)   # never the value
        return {"value": value, "note": ""}


class KeychainResolver(Resolver):
    """Step 2: the user's keychain, through `keyring`.

    `backend` is a keyring backend to use instead of the one `keyring` picks, for a
    test. A keychain with no usable backend (keyring's null or fail backend, a chainer
    with nothing in it, or `keyring` not importable) is a miss, and can't be written: the
    chain moves on to the store. A backend that raises is a failure naming this step.
    """

    name = STEP_KEYCHAIN

    def __init__(self, backend=None):
        self._backend = backend

    def describe(self, secret_id: str) -> str:
        return "the keychain"

    def backend(self):
        """The keyring backend this step uses, or None when there is no usable one."""
        backend = self._backend
        if backend is None:
            try:
                import keyring

                backend = keyring.get_keyring()
            except Exception:                                    # noqa: BLE001
                return None
        try:
            priority = backend.priority
        except Exception:                                        # noqa: BLE001
            return None
        return backend if priority and priority > 0 else None

    @staticmethod
    def _user(secret_id: str, owner) -> str:
        return f"{owner}:{secret_id}" if owner is not None else str(secret_id)

    def _failed(self, what: str, secret_id: str, e: Exception) -> SecretStepFailed:
        return SecretStepFailed(f"cannot {what} {secret_id}: the keychain step failed "
                                f"({type(e).__name__}: {e}).")

    def get(self, secret_id: str, *, owner=None) -> dict | None:
        backend = self.backend()
        if backend is None:
            return None
        try:
            value = backend.get_password(KEYCHAIN_SERVICE, self._user(secret_id, owner))
        except Exception as e:                                   # noqa: BLE001
            raise self._failed("resolve", secret_id, e) from None
        if not value or not value.strip():
            return None
        log.info("secret %s: from the keychain", secret_id)     # never the value
        return {"value": value.strip(), "note": ""}

    def put(self, secret_id: str, value: str, *, owner) -> bool:
        backend = self.backend()
        if backend is None:
            return False
        try:
            backend.set_password(KEYCHAIN_SERVICE, self._user(secret_id, owner), value)
        except Exception as e:                                   # noqa: BLE001
            raise self._failed("write", secret_id, e) from None
        return True

    def delete(self, secret_id: str, *, owner) -> bool:
        backend = self.backend()
        if backend is None:
            return False
        user = self._user(secret_id, owner)
        try:
            if backend.get_password(KEYCHAIN_SERVICE, user) is None:
                return False
            backend.delete_password(KEYCHAIN_SERVICE, user)
        except Exception as e:                                   # noqa: BLE001
            raise self._failed("remove", secret_id, e) from None
        return True


class StoreResolver(Resolver):
    """Step 2: the custody store's declared read, keyed by the holder that is spending.

    `store` is a `TokenStore`, a path, or None for the configured store; it is a
    CONSTRUCTOR argument, so a test's chain is as fake-able as a test's store.
    """

    name = STEP_STORE

    def __init__(self, store=None, *, env=None):
        from .tokens import TokenStore

        self.env = env if env is not None else os.environ
        self.store = (store if isinstance(store, TokenStore)
                      else TokenStore(store, env=self.env))

    def describe(self, secret_id: str) -> str:
        return "the holder's token store"

    def get(self, secret_id: str, *, owner=None) -> dict | None:
        from .tokens import TokenStoreError

        if owner is None:
            return None                      # a store lookup is keyed by holder
        try:
            value = self.store.read_secret(owner, secret_id)
        except TokenStoreError as e:
            # A store that refuses (0600 wider than the rule) is not a holder without the
            # key: it is a store that could not be read, and falling through would answer
            # the wrong question. The store's refusals are already caller-shaped.
            raise SecretStepFailed(
                f"cannot resolve the posting credential {secret_id}: the token store "
                f"step failed. {e}") from None
        if not value:
            return None
        log.info("secret %s: resolved from the token store", secret_id)   # never which
        return {"value": value, "note": ""}

    def put(self, secret_id: str, value: str, *, owner) -> bool:
        from .tokens import TokenStoreError

        try:
            self.store.write_secret(owner, secret_id, value)
        except TokenStoreError as e:
            raise SecretStepFailed(f"cannot write {secret_id}: the token store step "
                                   f"failed. {e}") from None
        except OSError as e:
            raise SecretStepFailed(f"cannot write {secret_id}: the token store step "
                                   f"failed ({e.strerror or e}).") from None
        return True

    def delete(self, secret_id: str, *, owner) -> bool:
        from .tokens import TokenStoreError

        try:
            return self.store.delete_secret(owner, secret_id)
        except (TokenStoreError, OSError) as e:
            raise SecretStepFailed(f"cannot remove {secret_id}: the token store step "
                                   f"failed. {e}") from None


class Chain(Resolver):
    """The steps in declared order. The only thing the backends hold."""

    name = "chain"

    def __init__(self, steps):
        self.steps = tuple(steps)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(step.name for step in self.steps)

    def __repr__(self) -> str:
        return f"Chain({', '.join(self.names)})"

    def get(self, secret_id: str, *, owner=None) -> dict:
        tried = []
        for step in self.steps:
            tried.append(step.describe(secret_id))
            found = step.get(secret_id, owner=owner)
            if found is not None:
                return found
        raise SecretNotFound(_not_found(secret_id, owner, tried,
                                        STEP_STORE in self.names))

    def put(self, secret_id: str, value: str, *, owner) -> str:
        """Write `value` into the first step that takes it, and answer that step's name.
        Raises `SecretStepFailed` when that step broke, and `SecretError` when no step
        can be written."""
        for step in self.steps:
            if step.put(secret_id, value, owner=owner):
                log.info("secret %s: written to the %s step", secret_id, step.name)
                return step.name
        raise SecretError(f"cannot write {secret_id}: no step of the credential chain "
                          f"({', '.join(self.names) or 'none'}) can be written.")

    def delete(self, secret_id: str, *, owner) -> list[str]:
        """Remove `secret_id` for `owner` from every step that holds it; the names of
        those steps. A step that breaks raises `SecretStepFailed` after the others ran."""
        removed, failed = [], None
        for step in self.steps:
            try:
                if step.delete(secret_id, owner=owner):
                    removed.append(step.name)
            except SecretStepFailed as e:
                failed = failed or e
        if failed is not None:
            raise failed
        return removed


def _not_found(secret_id, owner, tried, has_store) -> str:
    """The one miss sentence, in the caller's language. It names the steps and the
    holder and nothing else: not a path, not a key, and never a value."""
    where = ", then ".join(tried) if tried else "nothing (the chain is empty)"
    if has_store:
        tail = "Provisioning writes it into the store; ask the operator."
    else:
        tail = "Nothing was attempted beyond those steps."
    return (f"cannot resolve the posting credential {secret_id}: tried {where}, and none "
            f"of them holds it for {_holder(owner)}. {tail}")


# --------------------------------------------------------------------------- building

def build(names=None, *, env=None, store=None, keychain=None) -> Chain:
    """A chain from step NAMES. The one constructor; every backend gets its chain here.

    `store` and `keychain` (a keyring backend) are the injection points a test uses. An unknown name raises `SecretError` --
    `config.load` refuses it first, so this is the second fence.
    """
    names = tuple(names) if names is not None else default_chain()
    steps = []
    for name in names:
        if name == STEP_ENV:
            steps.append(EnvResolver(env=env))
        elif name == STEP_KEYCHAIN:
            steps.append(KeychainResolver(keychain))
        elif name == STEP_STORE:
            steps.append(StoreResolver(store, env=env))
        else:
            raise SecretError(
                f"unknown secret resolver {name!r}: the chain's steps are "
                f"{', '.join(STEPS)} (config.toml [credentials] chain).")
    return Chain(steps)


def from_config(cfg=None, *, env=None, store=None) -> Chain:
    """The box's declared chain. `cfg` is a loaded `CommsConfig`, or None to load."""
    if cfg is None:
        from . import config

        cfg = config.load(env=env)
    if store is None and cfg.credentials.store:
        store = cfg.credentials.store
    return build(cfg.credentials.chain, env=env, store=store)


__all__ = ["Resolver", "EnvResolver", "KeychainResolver", "StoreResolver", "Chain",
           "SecretError", "SecretNotFound", "SecretStepFailed", "env_key", "build",
           "from_config", "ENV_PREFIX", "STEPS", "DEFAULT_CHAIN", "default_chain",
           "STEP_ENV", "STEP_KEYCHAIN", "STEP_STORE", "KEYCHAIN_SERVICE"]
