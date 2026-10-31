"""Reserved send-only names: names a module declares, which only that module sends under.

A module declares the names it needs in its announcement's `RESERVED_NAMES`. A name is
bare and global (`console`, never `console@proj`), and belongs to the install. comms keeps
one row per name in comms.db's `reserved` table:

  * `active`: reserved for its `module`, with the hash of a token that rests in the
    credential chain for that module, under the secret id `comms/reserved/<name>`;
  * `conflict`: declared, but not reserved (the name is a standing identity, a live
    binding holds it, another module declares it too, or it isn't a bare name), with a
    `note` saying why; the next reconcile tries again;
  * `unowned`: a send-only name an older comms kept; nothing can send under it until a
    module declares it, and the first that does adopts it.

A row with `freed_at` set is no longer reserved: the name is ordinary again. No session
binds a name whose row isn't freed, and nobody mails an `active` or `unowned` one.

`reconcile(gone=None)` is the one writer. It reads the declared names of every installed
module except `gone`, and brings the table in line: a declared name with no row is
reserved (a fresh token, a row, and a `bindings` interval of kind `reserved`), a row whose
module no longer declares it is freed (its interval closed, its token removed). It never
replaces a token it can't read: it mints only for a row that has no hash. `renew(name)`
is the explicit repair.

It runs from the core's `moxi.installs` group (`on_install`, `on_uninstall`), at the
daemon's start, and in comms' doctor checks. `on_uninstall(module)` reconciles with the
module treated as gone, whatever its package's state: the core calls it after the
module's teardown and before its package is removed, while it still reads as installed.
comms' own uninstall frees every name it reserved and removes every token it minted.
"""

from __future__ import annotations

import hashlib
import logging
import secrets as _secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from . import paths as _paths
from .bindings import stamp
from .release import MODULE
from .scoping import GLOBAL, NAME_RE, is_auto_name

log = logging.getLogger("comms.reserved")

ACTIVE, CONFLICT, UNOWNED = "active", "conflict", "unowned"
#: The kind a reserved name reads as among the standing names (`agents`' `standing`,
#: `api.roster().standing`).
SEND_ONLY = "send-only"

#: The secret id a reserved name's token rests under, for its owning module.
SECRET_PREFIX = "comms/reserved/"
#: The `bindings` interval of a reserved name: its kind, tier and adapter columns.
BINDING_KIND = "reserved"
BINDING_TIER = "standing"
BINDING_ADAPTER = "reserved"
#: The lock file one reconcile holds at a time, beside comms.db.
LOCK = "reserved.lock"

# `moxi.files.lock` refuses a second holder in one process, so this process's threads
# queue here first.
_gate = threading.Lock()


def secret_id(name: str) -> str:
    """The credential chain's id for `name`'s token."""
    return SECRET_PREFIX + name


def owner(module: str):
    """The credential chain's holder of a module's tokens: the module, by its name."""
    from .tokens import TokenOwner

    return TokenOwner((module,))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class Result:
    """What one reconcile did and found. `lines` says it, one line each."""

    reserved: list = field(default_factory=list)     # (name, module)
    adopted: list = field(default_factory=list)      # (name, module)
    freed: list = field(default_factory=list)        # (name, module)
    conflicts: list = field(default_factory=list)    # (name, module, why)
    failed: list = field(default_factory=list)       # (name, module, why)
    lines: list = field(default_factory=list)


# --------------------------------------------------------------------------- reads

def declared_names(gone: str | None = None) -> dict:
    """module -> the names it declares, for every installed module but `gone`. A module
    that isn't `present` (absent, or broken) declares nothing."""
    from moxi import modules

    out = {}
    for found in modules.scan():
        if found.state != modules.PRESENT or found.name == gone:
            continue
        names = tuple(found.reserved_names or ())
        if names:
            out[found.name] = names
    return out


def rows(conn) -> dict:
    """name -> the `reserved` row, freed ones included."""
    conn.row_factory = sqlite3.Row
    return {r["name"]: r for r in conn.execute("SELECT * FROM reserved")}


def lookup(conn, qualified: str):
    """The `reserved` row of `qualified` that isn't freed, or None."""
    return conn.execute("SELECT name, module, token_hash, state, note FROM reserved "
                        "WHERE name = ? AND freed_at IS NULL", (qualified,)).fetchone()


def unmailable(conn, qualified: str) -> bool:
    """Does `qualified` name a reserved name nobody mails: an `active` or `unowned` row?
    A `conflict` row isn't reserved, so it is mailed as the name it is."""
    row = lookup(conn, qualified)
    return row is not None and row["state"] in (ACTIVE, UNOWNED)


def bind_refusal(row) -> str:
    """The daemon's words refusing a bind of the reserved name in `row`."""
    name = row["name"]
    if row["state"] == ACTIVE:
        return f"{name} is reserved for {row['module']}: no session binds it"
    if row["state"] == UNOWNED:
        return (f"{name} is a send-only name an older comms kept: no session binds it "
                "until a module declares it")
    return (f"{name} is declared as a reserved name and not yet reserved "
            f"({row['note'] or 'in conflict'}): no session binds it until the conflict "
            "is settled")


def _bad_name(name: str) -> str | None:
    """Why `name` can't be reserved, or None when it can."""
    if "@" in name:
        return "a reserved name is bare and global, and this one names a scope"
    if not NAME_RE.match(name):
        return "it isn't a comms name"
    if is_auto_name(name):
        return "agent-N names are the daemon's"
    return None


def _blocked(conn, name: str) -> str | None:
    """Why `name` can't be reserved now, from the store: a standing identity, or a live
    binding."""
    if conn.execute("SELECT 1 FROM identities WHERE name = ? AND scope = ? AND "
                    "status = 'active'", (name, GLOBAL)).fetchone():
        return "it is a standing identity"
    if conn.execute("SELECT 1 FROM bindings WHERE qualified = ? AND kind = 'session' AND "
                    "released_at IS NULL", (name,)).fetchone():
        return "a live session holds it"
    return None


# --------------------------------------------------------------------------- reconcile

def reconcile(gone: str | None = None, *, declared=None, chain=None, env=None,
              target=None, clock=time.time) -> Result:
    """Bring the `reserved` table in line with the names every installed module but
    `gone` declares. `declared` (module -> names) stands in for the installed modules;
    `chain` for the credential chain the config names. Raises `StoreRefused` with
    nothing written when comms.db can't be opened for writing."""
    if declared is None:
        declared = declared_names(gone)
    else:
        declared = {m: tuple(n) for m, n in declared.items() if m != gone}
    with _held(target, env) as conn:
        return _reconcile(conn, declared, _chain(chain, env), clock)


def release_all(*, chain=None, env=None, target=None, clock=time.time) -> Result:
    """comms' own uninstall: free every `active` row and remove every token comms
    minted, so no token outlives the code that checks it. Conflict and unowned rows stay
    as they are; the rows stay in comms.db until a purge takes it."""
    chain = _chain(chain, env)
    result = Result()
    with _held(target, env) as conn:
        for name, row in rows(conn).items():
            if row["state"] == ACTIVE and row["freed_at"] is None:
                _free(conn, chain, name, row["module"], clock, result)
    return result


def renew(name: str, *, chain=None, env=None, target=None, clock=time.time) -> Result:
    """Mint a fresh token for the reserved name `name` into the first writable step of
    this process's chain, and commit its hash: the one repair for a token a context
    can't read. Raises `KeyError` when `name` isn't reserved for a module."""
    chain = _chain(chain, env)
    result = Result()
    with _held(target, env) as conn:
        row = lookup(conn, name)
        if row is None or row["state"] != ACTIVE:
            raise KeyError(name)
        _mint(conn, chain, name, row["module"], clock, result, renew=True)
    return result


class _held:
    """comms.db open for writing under the reserved names' lock."""

    def __init__(self, target, env):
        self.target, self.env = target, env

    def __enter__(self):
        from moxi import files as _files

        from .store import open_write
        from .store.open import path as store_path

        target = self.target if self.target is not None else store_path(self.env)
        self._gate = _gate
        self._gate.acquire()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._lock = _files.lock(target.parent / LOCK, timeout=60.0)
            self._lock.__enter__()
            try:
                self.conn = open_write(target, env=self.env)
            except BaseException:
                self._lock.__exit__(None, None, None)
                raise
        except BaseException:
            self._gate.release()
            raise
        self.conn.row_factory = sqlite3.Row
        return self.conn

    def __exit__(self, *exc):
        try:
            self.conn.close()
        finally:
            try:
                self._lock.__exit__(*exc)
            finally:
                self._gate.release()
        return False


def _chain(chain, env):
    if chain is not None:
        return chain
    from . import secrets

    return secrets.from_config(env=env)


def _reconcile(conn, declared: dict, chain, clock) -> Result:
    result = Result()
    wanted: dict[str, list[str]] = {}
    bad = []
    for module in sorted(declared):
        for name in declared[module]:
            why = _bad_name(str(name))
            if why:
                bad.append((str(name), module, why))
            else:
                wanted.setdefault(name, []).append(module)
    table = rows(conn)

    # Frees first: a row whose module no longer declares it (or is gone), and a
    # conflict row nobody declares now.
    for name, row in table.items():
        if row["freed_at"] is not None:
            continue
        if row["state"] == ACTIVE and row["module"] not in wanted.get(name, ()):
            _free(conn, chain, name, row["module"], clock, result)
        elif (row["state"] == CONFLICT and name not in wanted
              and not any(b[0] == name for b in bad)):
            _write(conn, "UPDATE reserved SET freed_at = ? WHERE name = ?",
                   (stamp(clock()), name))
    table = rows(conn)

    for name, module, why in bad:
        row = table.get(name)
        _conflict(conn, result, name, module, why,
                  keep=row is not None and row["freed_at"] is None
                  and row["state"] == UNOWNED, clock=clock)

    for name in sorted(wanted):
        modules_ = wanted[name]
        row = table.get(name)
        live = row is not None and row["freed_at"] is None
        if live and row["state"] == ACTIVE and row["module"] in modules_:
            for other in modules_:
                if other != row["module"]:
                    result.conflicts.append((name, other, f"{row['module']} has it"))
                    result.lines.append(f"{other}: {name} isn't reserved for it: it is "
                                        f"reserved for {row['module']}")
            if row["token_hash"] is None:
                _mint(conn, chain, name, row["module"], clock, result)
            continue
        if len(modules_) > 1:
            _conflict(conn, result, name, None,
                      f"{' and '.join(modules_)} each declare it", clock=clock)
            continue
        module = modules_[0]
        why = _blocked(conn, name)
        if why:
            _conflict(conn, result, name, module, why,
                      keep=live and row["state"] == UNOWNED, clock=clock)
            continue
        _mint(conn, chain, name, module, clock, result,
              adopt=live and row["state"] == UNOWNED)
    return result


def _write(conn, sql: str, params=()) -> None:
    from moxi.store import transaction

    with transaction(conn) as c:
        c.execute(sql, params)


def _conflict(conn, result: Result, name: str, module, why: str, *, keep: bool = False,
              clock=time.time) -> None:
    """Record that `name` couldn't be reserved for `module`, and say so. An unowned row
    (`keep`) stays unowned: it still refuses binds and mail."""
    result.conflicts.append((name, module, why))
    who = module or "a module"
    result.lines.append(f"{who}: {name} isn't reserved: {why}")
    if keep:
        return
    from moxi.store import transaction

    with transaction(conn) as c:
        c.execute("INSERT INTO reserved(name, module, token_hash, state, reserved_at, "
                  "freed_at, note) VALUES (?, ?, NULL, 'conflict', NULL, NULL, ?) "
                  "ON CONFLICT(name) DO UPDATE SET module = excluded.module, "
                  "token_hash = NULL, state = 'conflict', reserved_at = NULL, "
                  "freed_at = NULL, note = excluded.note",
                  (name, module, why))


def _mint(conn, chain, name: str, module: str, clock, result: Result, *,
          adopt: bool = False, renew: bool = False) -> None:
    """A fresh token for `name`, written to the chain for `module`, then its hash
    committed. A write that fails commits nothing."""
    from moxi.store import transaction

    from .secrets import SecretError

    token = _secrets.token_urlsafe(32)
    try:
        step = chain.put(secret_id(name), token, owner=owner(module))
    except SecretError as e:
        result.failed.append((name, module, str(e)))
        result.lines.append(f"{module}: {name} isn't reserved: its token couldn't be "
                            f"written. {e}")
        return
    at = stamp(clock())
    with transaction(conn) as c:
        if renew:
            c.execute("UPDATE reserved SET token_hash = ? WHERE name = ?",
                      (token_hash(token), name))
        else:
            c.execute("INSERT INTO reserved(name, module, token_hash, state, reserved_at, "
                      "freed_at, note) VALUES (?, ?, ?, 'active', ?, NULL, NULL) "
                      "ON CONFLICT(name) DO UPDATE SET module = excluded.module, "
                      "token_hash = excluded.token_hash, state = 'active', "
                      "reserved_at = excluded.reserved_at, freed_at = NULL, note = NULL",
                      (name, module, token_hash(token), at))
            open_ = c.execute("SELECT 1 FROM bindings WHERE qualified = ? AND kind = ? "
                              "AND released_at IS NULL", (name, BINDING_KIND)).fetchone()
            if open_ is None:
                c.execute("INSERT INTO bindings(qualified, name, scope, tier, adapter, "
                          "bound_at, kind) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (name, name, GLOBAL, BINDING_TIER, BINDING_ADAPTER, at,
                           BINDING_KIND))
    if renew:
        result.lines.append(f"{name}: a new token for {module}, in the {step} step")
    elif adopt:
        result.adopted.append((name, module))
        result.lines.append(f"{module}: {name} reserved (adopted from an older comms)")
    else:
        result.reserved.append((name, module))
        result.lines.append(f"{module}: {name} reserved")
    log.info("reserved %s for %s (token in the %s step)", name, module, step)


def _free(conn, chain, name: str, module: str, clock, result: Result) -> None:
    """Free `name`: the row freed and its interval closed, then its token removed."""
    from moxi.store import transaction

    from .secrets import SecretError

    at = stamp(clock())
    with transaction(conn) as c:
        c.execute("UPDATE reserved SET freed_at = ?, token_hash = NULL WHERE name = ?",
                  (at, name))
        c.execute("UPDATE bindings SET released_at = ? WHERE qualified = ? AND kind = ? "
                  "AND released_at IS NULL", (at, name, BINDING_KIND))
    result.freed.append((name, module))
    result.lines.append(f"{module}: {name} freed")
    try:
        chain.delete(secret_id(name), owner=owner(module))
    except SecretError as e:
        # The row is freed and its hash gone, so the token no longer sends anything.
        result.lines.append(f"{module}: {name}'s token couldn't be removed: {e}")
        log.warning("freed %s, but its token couldn't be removed: %s", name, e)


# --------------------------------------------------------------------------- the listener

def on_install(module: str, found=None) -> None:
    """The core's `moxi.installs` listener, after `module`'s install settles."""
    _say(_run(lambda: reconcile()))


def on_uninstall(module: str) -> None:
    """The core's `moxi.installs` listener, before `module`'s package is removed: its
    names are freed now. comms' own uninstall frees every name and removes every
    token."""
    if module == MODULE:
        _say(_run(lambda: release_all()))
    else:
        _say(_run(lambda: reconcile(gone=module)))


def _run(call) -> list[str]:
    from .store import StoreRefused

    try:
        return call().lines
    except StoreRefused as e:
        return [f"comms: reserved names weren't changed: {e.reason}"]


def _say(lines) -> None:
    for line in lines:
        print(line)


# --------------------------------------------------------------------------- doctor

def doctor_rows(env=None, *, declared=None, chain=None) -> list:
    """comms' doctor rows about reserved names: reconcile first (which catches a module
    installed before comms, and a store restored from a backup), then one row per name
    that isn't reserved as declared, and per reserved token this process can't read."""
    from moxi.doctor import Row

    from .store import StoreRefused, zero_two_files

    if zero_two_files(env):
        # The doctor never moves comms 0.2's files; the daemon's next start does, and
        # reconciles then.
        return []
    try:
        result = reconcile(declared=declared, chain=chain, env=env)
    except StoreRefused as e:
        return [Row(MODULE, "reserved", "warn", f"reserved names weren't checked: "
                    f"{e.reason}")]
    out = [Row(MODULE, "reserved", "warn", f"{module or 'a module'}: {name} isn't "
               f"reserved: {why}", "moxi doctor runs again after the cause is gone")
           for name, module, why in result.conflicts]
    out += [Row(MODULE, "reserved", "fail", f"{module}: {name} isn't reserved: {why}",
                "check the credential chain, then run moxi doctor")
            for name, module, why in result.failed]
    out += token_rows(env=env, chain=chain)
    return out


def token_rows(*, env=None, chain=None, context: str = "this process's") -> list:
    """A row for each active reserved name whose token this process's chain can't read,
    or reads with a hash that doesn't match."""
    from moxi.doctor import Row

    from .secrets import SecretError
    from .store import StoreRefused
    from .store.open import open_read

    try:
        conn = open_read(env=env)
    except (StoreRefused, sqlite3.Error):
        return []
    try:
        active = [(r[0], r[1], r[2]) for r in conn.execute(
            "SELECT name, module, token_hash FROM reserved WHERE state = 'active' AND "
            "freed_at IS NULL ORDER BY name")]
    finally:
        conn.close()
    chain = _chain(chain, env)
    out = []
    for name, module, digest in active:
        try:
            got = chain.get(secret_id(name), owner=owner(module))["value"]
        except SecretError:
            got = None
        if got is None or token_hash(got) != digest:
            out.append(Row(MODULE, "reserved", "warn",
                           f"comms can't read the token for {name} from {context} "
                           "environment", f"moxi comms reserved renew {name}"))
    return out


__all__ = ["ACTIVE", "CONFLICT", "UNOWNED", "SECRET_PREFIX", "Result", "declared_names",
           "doctor_rows", "lookup", "on_install", "on_uninstall", "owner", "reconcile",
           "release_all", "renew", "rows", "secret_id", "token_hash", "token_rows"]
