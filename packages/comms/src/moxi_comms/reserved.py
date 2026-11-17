"""Send-only names: names nobody mails and no session binds, which only their holder
sends under.

There are two kinds, told apart by the row's `holder`:

  * a module's: a module declares the names it needs in its announcement's
    `RESERVED_NAMES`, and sends under them from its own code (`api.send_as`);
  * a program's: any program or service of the user registers a name with `moxi comms
    send-only add NAME` (`register`), and sends under it with its token.

A name is bare and global (`console`, never `console@proj`), and belongs to the install.
comms keeps one row per name in comms.db's `reserved` table:

  * `active`: held by its holder, with the hash of a token that rests in the credential
    chain: for a module's name under the secret id `comms/reserved/<name>`, held by the
    module; for a program's under `comms/send-only/<name>`, held by `send-only`. A
    program's row has no `module`;
  * `conflict`: declared by a module, but not reserved (the name is a standing identity,
    a live binding holds it, another module declares it too, or it isn't a bare name),
    with a `note` saying why; the next reconcile tries again;
  * `unowned`: a send-only name an older comms kept, with no holder; nothing can send
    under it until a module declares it or a program registers it, and the first that
    does adopts it.

A row with `freed_at` set is no longer reserved: the name is ordinary again. No session
binds a name whose row isn't freed, and nobody mails an `active` or `unowned` one.

`reconcile(gone=None)` is the one writer. It reads the declared names of every installed
module except `gone`, and brings the table in line: a declared name with no row is
reserved (a fresh token, a row, and a `bindings` interval of kind `reserved`), a row whose
module no longer declares it is freed (its interval closed, its token removed). A module
that is installed but reads as broken keeps its names: only an absent module, or the one
being uninstalled, loses them. It never
replaces a token it can't read: it mints only for a row that has no hash. `renew(name)`
is the explicit repair. It never writes a program's row: a module that declares a name a
program registered gets a conflict line, and the row stays the program's.

`register`, `renew_program` and `remove_program` are a program's three: the `moxi comms
send-only` verbs. Each takes the same lock and opens the store the same way as
`reconcile`, so each works while the daemon is down. A process inside a session, or
one comms can't place, may only register a name nobody uses: `session_guard` refuses it
`add --take`, `renew` and `remove` before anything is touched.

It runs from the core's `moxi.installs` group (`on_install`, `on_uninstall`), at the
daemon's start, and in comms' doctor checks. `on_uninstall(module)` reconciles with the
module treated as gone, whatever its package's state: the core calls it after the
module's teardown and before its package is removed, while it still reads as installed.
comms' own uninstall frees every name it reserved or a program registered, and removes
every token it minted.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets as _secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from . import paths as _paths
from .bindings import stamp
from .release import MODULE
from .scoping import AUTO_WORD, GLOBAL, NAME_RE, OPERATOR_NAME, is_auto_name

log = logging.getLogger("comms.reserved")

ACTIVE, CONFLICT, UNOWNED = "active", "conflict", "unowned"
#: Who holds a row's name (`reserved.holder`): a module, or a program. An unowned row has
#: none.
MODULE_HOLDER, PROGRAM_HOLDER = "module", "program"
#: The kind a reserved name reads as among the standing names (`agents`' `standing`,
#: `api.roster().standing`).
SEND_ONLY = "send-only"

#: The secret id a reserved name's token rests under, for its owning module.
SECRET_PREFIX = "comms/reserved/"
#: The secret id a program's send-only name's token rests under, for `PROGRAM_OWNER`.
PROGRAM_SECRET_PREFIX = "comms/send-only/"
#: The credential chain's holder of every program's send-only token.
PROGRAM_OWNER = "send-only"
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


def program_secret_id(name: str) -> str:
    """The credential chain's id for the token of `name`, a program's send-only name."""
    return PROGRAM_SECRET_PREFIX + name


def program_owner():
    """The credential chain's holder of a program's send-only token."""
    from .tokens import TokenOwner

    return TokenOwner((PROGRAM_OWNER,))


def credential(name: str, module: str | None, holder: str | None) -> tuple:
    """(secret id, holder) of the token that sends under `name`: a program's, or the
    module's."""
    if holder == PROGRAM_HOLDER:
        return program_secret_id(name), program_owner()
    return secret_id(name), owner(module)


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
    #: (name, why) for each name a program holds whose token this process doesn't read
    #: back as the one comms holds the hash of.
    unreadable: list = field(default_factory=list)


class Refused(Exception):
    """A `send-only` verb's refusal, with nothing written. `str()` is the verb's words."""


# --------------------------------------------------------------------------- reads

#: What `declared_names` gives for a module whose announcement can't be read: the names
#: it declares aren't known, so the names reserved for it stay as they are.
UNKNOWN = None


def declared_names(gone: str | None = None) -> dict:
    """module -> the names it declares, for every installed module but `gone`. A `broken`
    module is still installed, and its names aren't known: it maps to `UNKNOWN`, and
    keeps what is reserved for it. Only an absent module, or `gone`, declares nothing."""
    from moxi import modules

    out = {}
    for found in modules.scan():
        if found.name == gone:
            continue
        if found.state == modules.BROKEN:
            out[found.name] = UNKNOWN
            continue
        if found.state != modules.PRESENT:
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
    return conn.execute("SELECT name, module, token_hash, state, note, holder FROM "
                        "reserved WHERE name = ? AND freed_at IS NULL",
                        (qualified,)).fetchone()


def held_by_program(row) -> bool:
    """Is `row` a name a program registered?"""
    return row is not None and row["holder"] == PROGRAM_HOLDER


def module_words(row) -> str:
    """Whom a module's row of `name` is held for, in a sentence's words: `console is
    reserved for launcher`, or, for a conflict row, who declares it and why it isn't
    reserved."""
    name = row["name"]
    if row["state"] == ACTIVE:
        return f"{name} is reserved for {row['module']}"
    who = row["module"] or "more than one module"
    return f"{name} is declared by {who} ({row['note'] or 'in conflict'})"


def unmailable(conn, qualified: str) -> bool:
    """Does `qualified` name a reserved name nobody mails: an `active` or `unowned` row?
    A `conflict` row isn't reserved, so it is mailed as the name it is."""
    row = lookup(conn, qualified)
    return row is not None and row["state"] in (ACTIVE, UNOWNED)


def bind_refusal(row) -> str:
    """The daemon's words refusing a bind of the reserved name in `row`."""
    name = row["name"]
    if row["state"] == ACTIVE and held_by_program(row):
        return f"{name} is a send-only name a program registered: no session binds it"
    if row["state"] == ACTIVE:
        return f"{name} is reserved for {row['module']}: no session binds it"
    if row["state"] == UNOWNED:
        return (f"{name} is a send-only name an older comms kept: no session binds it "
                "until a module declares it or a program registers it")
    return (f"{name} is declared as a reserved name and not yet reserved "
            f"({row['note'] or 'in conflict'}): no session binds it until the conflict "
            "is settled")


#: Names the daemon keeps for itself and the operator. A module that declares one gets a
#: conflict line and no row: a `conflict` row refuses binds, and the operator's own
#: session binds his name.
_KEPT = (OPERATOR_NAME, AUTO_WORD)


def _bad_name(name: str) -> str | None:
    """Why `name` can't be reserved, or None when it can."""
    if "@" in name:
        return "a reserved name is bare and global, and this one names a scope"
    if not NAME_RE.match(name):
        return "it isn't a comms name"
    if is_auto_name(name):
        return "agent-N names are the daemon's"
    if name == OPERATOR_NAME:
        return "it is the operator's name, and the daemon keeps it for him"
    if name == AUTO_WORD:
        return "a bind of auto asks the daemon for an agent-N name, so the daemon keeps it"
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
    `gone` declares. `declared` (module -> names, or `UNKNOWN` for a module whose names
    can't be read) stands in for the installed modules; `chain` for the credential chain
    the config names. Raises `StoreRefused` with
    nothing written when comms.db can't be opened for writing."""
    if declared is None:
        declared = declared_names(gone)
    else:
        declared = {m: n if n is UNKNOWN else tuple(n)
                    for m, n in declared.items() if m != gone}
    with _Held(target, env) as conn:
        return _reconcile(conn, declared, _chain(chain, env), clock)


def release_all(*, chain=None, env=None, target=None, clock=time.time) -> Result:
    """comms' own uninstall: free every `active` row, a module's or a program's, and
    remove every token comms minted, so no token outlives the code that checks it, and
    revoke every session's token for comms' tools. Conflict and unowned rows stay as
    they are; the rows stay in comms.db until a purge takes it.

    A token can outlive its row's hash: a freed name whose token couldn't be removed, a
    mint whose commit failed. So after the frees, the token of every name any row or
    installed module names goes too, and every send-only token in the file store,
    a module's or a program's, whatever names it."""
    from .secrets import SecretError

    chain = _chain(chain, env)
    result = Result()
    with _Held(target, env) as conn:
        found = rows(conn)
        for name, row in found.items():
            if row["state"] == ACTIVE and row["freed_at"] is None:
                _free(conn, chain, name, row["module"], clock, result,
                      holder=row["holder"])
        _revoke_tool_tokens(conn, clock, result)
    done = set(result.freed)
    for name, module in sorted(_minted(chain, found) - done):
        try:
            if chain.delete(secret_id(name), owner=owner(module)):
                result.lines.append(f"{module}: {name}'s leftover token removed")
        except SecretError as exc:
            result.lines.append(f"{module}: {name}'s token couldn't be removed: {exc}")
            log.warning("%s's token for %s couldn't be removed: %s", name, module, exc)
    freed = {name for name, _module in done}
    for name in sorted(_registered(chain, found) - freed):
        try:
            if chain.delete(program_secret_id(name), owner=program_owner()):
                result.lines.append(f"send-only: {name}'s leftover token removed")
        except SecretError as exc:
            result.lines.append(f"send-only: {name}'s token couldn't be removed: {exc}")
            log.warning("%s's send-only token couldn't be removed: %s", name, exc)
    return result


def _registered(chain, found: dict) -> set:
    """Every name a program's send-only token may rest under: each program's row, freed
    or not, and each `comms/send-only/` key the file store holds."""
    names = {name for name, row in found.items() if row["holder"] == PROGRAM_HOLDER}
    for step in getattr(chain, "steps", ()):
        store = getattr(step, "store", None)
        if store is None:
            continue
        try:
            names |= {key[len(PROGRAM_SECRET_PREFIX):]
                      for key in store.secret_ids(program_owner())
                      if key.startswith(PROGRAM_SECRET_PREFIX)}
        except Exception as exc:                                   # noqa: BLE001
            log.warning("the token store couldn't be listed: %s", exc)
    return names


def _revoke_tool_tokens(conn, clock, result: Result) -> None:
    """comms' uninstall revokes every session's token for comms' tools, in one
    transaction: a revoked token never comes back, so nothing a session still holds
    sends through a reinstalled comms."""
    from moxi.store import transaction

    from .mcp import tokens

    with transaction(conn) as c:
        revoked = tokens.revoke_store(c, tokens.UNINSTALL, at=clock())
    if revoked:
        result.lines.append(f"comms: {revoked} session token(s) for comms' tools "
                            "revoked")


def _minted(chain, found: dict) -> set:
    """(name, module) for every reserved-name token comms may have minted: each row's,
    each installed module's declared names, and each one the file store holds."""
    pairs = {(name, row["module"]) for name, row in found.items()
             if row["module"] and row["holder"] != PROGRAM_HOLDER}
    try:
        declared = declared_names()
    except Exception as exc:                                       # noqa: BLE001
        log.warning("the installed modules couldn't be read: %s", exc)
        declared = {}
    pairs |= {(name, module) for module, names in declared.items() if names
              for name in names}
    from .tokens import TokenOwner

    for step in getattr(chain, "steps", ()):
        store = getattr(step, "store", None)
        if store is None:
            continue
        try:
            for holder in store.owners_present():
                for key in store.secret_ids(TokenOwner(tuple(holder.split("/")))):
                    if key.startswith(SECRET_PREFIX) and "/" not in holder:
                        pairs.add((key[len(SECRET_PREFIX):], holder))
        except Exception as exc:                                   # noqa: BLE001
            log.warning("the token store couldn't be listed: %s", exc)
    return pairs


def renew(name: str, *, chain=None, env=None, target=None, clock=time.time) -> Result:
    """Mint a fresh token for the reserved name `name` into the first writable step of
    this process's chain, and commit its hash: the one repair for a token a context
    can't read. Raises `KeyError` when `name` isn't reserved for a module, and
    `Refused` when a program registered it (`renew_program` renews that one)."""
    chain = _chain(chain, env)
    result = Result()
    with _Held(target, env) as conn:
        row = lookup(conn, name)
        if held_by_program(row):
            raise Refused(f"{name} is registered by a program, not reserved for a module: "
                          f"moxi comms send-only renew {name}")
        if row is None or row["state"] != ACTIVE:
            raise KeyError(name)
        _mint(conn, chain, name, row["module"], clock, result, renew=True)
    if not result.failed:
        trace_send(row["module"], name, credential=False, sent=True)
    return result


# --------------------------------------------------------------------------- a program's names

#: Where `session_place` finds this process: inside a live session, or somewhere its
#: ancestry can't be read far enough to tell.
INSIDE, UNPLACED = "inside", "unplaced"


def session_place(proc=None) -> str | None:
    """Where this process stands against the live sessions, walked as the daemon walks a
    sender under a send-only name: `INSIDE` a live session, `UNPLACED` when its ancestry
    can't be read up to a process with no parent, or None outside every session. Reads
    the claim files only, so it answers with the daemon down."""
    from . import claimfile, membership

    try:
        live = claimfile.live_records(proc)
        found = membership.sessions_of(os.getpid(), claimfile.membership_table(live),
                                       proc, strict=True)
    except membership.Unplaceable:
        return UNPLACED
    return INSIDE if found else None


def session_guard(verb: str, name: str, *, proc=None) -> None:
    """The one check `send-only add --take`, `renew` and `remove` make first, before any
    row or token is touched: a process inside a live session, or one comms can't place,
    may only register a name nobody uses (plain `add`). Taking a name over, replacing
    its token and freeing it are for the user's own shell. Raises `Refused`."""
    place = session_place(proc)
    if place is None:
        return
    where = ("from inside an agent's session" if place == INSIDE else
             "from a process comms can't place outside every session")
    raise Refused(f"moxi comms send-only {verb} {name} is refused {where}: a process "
                  "inside a session may only register a name nobody uses (plain add). "
                  "Run it from your own shell")


def register(name: str, *, take: bool = False, chain=None, env=None, target=None,
             clock=time.time) -> Result:
    """`moxi comms send-only add NAME`: register `name` as a program's send-only name. A
    fresh token goes into the first writable step of this process's chain for
    `PROGRAM_OWNER`, and its hash into an `active` row whose holder is `program`, with a
    `bindings` interval of kind `reserved`, so mail queued for the name before now is
    dropped. An `unowned` row is adopted.

    On a name a program holds already it mints nothing: the result's `unreadable` names
    it when this process can't read its token back. Raises `Refused`, with nothing
    written, for a name comms can't register, a module's name, a retired name, a
    standing identity or a name a live session holds, and, unless `take`, a name a
    session held before or one with mail waiting. With `take`, it is refused first from
    inside a session (`session_guard`)."""
    from moxi.store import transaction

    from . import directory
    from .secrets import SecretError

    if take:
        session_guard("add --take", name)
    why = _bad_name(name)
    if why:
        raise Refused(f"{name} can't be registered: {why}")
    chain = _chain(chain, env)
    result = Result()
    with _Held(target, env) as conn:
        row = lookup(conn, name)
        if held_by_program(row):
            result.lines.append(f"{name} is already registered")
            why = _unreadable(chain, name, row["token_hash"])
            if why:
                result.unreadable.append((name, why))
                result.lines.append(f"this process can't send under {name}: {why}. Run "
                                    f"moxi comms send-only renew {name} from the "
                                    "environment that sends under it")
            return result
        if row is not None and row["state"] != UNOWNED:
            raise Refused(f"{module_words(row)}: a program can't register it")
        adopt = row is not None
        if directory.retired(conn, name, GLOBAL):
            raise Refused(f"{name} is retired: a retired name means nobody, and a send-only "
                          "name would bring it back")
        why = _blocked(conn, name)
        if why:
            raise Refused(f"{name} can't be registered: {why}")
        waiting = conn.execute("SELECT COUNT(*) FROM queue WHERE name = ? AND "
                               "state = 'waiting'", (name,)).fetchone()[0]
        # An adopted row's name was send-only already: the move of comms 0.2's files may
        # have carried its old send-only bindings as `session` rows.
        if not adopt and not take:
            past = conn.execute("SELECT COUNT(*) FROM bindings WHERE qualified = ? AND "
                                "kind = 'session'", (name,)).fetchone()[0]
            bits = ([f"a session has held it before ({past} binding(s))"] if past else []) \
                + ([f"{waiting} message(s) wait for it, which registering drops for good"]
                   if waiting else [])
            if bits:
                raise Refused(f"{name} has a past: {'; '.join(bits)}. To register it "
                              "anyway, run add --take from your own shell, outside "
                              "every session")
        token = _secrets.token_urlsafe(32)
        try:
            step = chain.put(program_secret_id(name), token, owner=program_owner())
        except SecretError as exc:
            result.failed.append((name, None, str(exc)))
            result.lines.append(f"{name} isn't registered: its token couldn't be written. "
                                f"{exc}")
            return result
        with transaction(conn) as c:
            _commit_active(c, name, None, PROGRAM_HOLDER, token_hash(token),
                           stamp(clock()))
    result.reserved.append((name, None))
    kept = "adopted from an older comms; " if adopt else ""
    result.lines.append(f"{name} registered ({kept}token in the {step} step)")
    if waiting:
        result.lines.append(f"{waiting} message(s) waiting for {name} will be dropped")
    _read_back(chain, name, token, result)
    log.info("registered %s for a program (token in the %s step)", name, step)
    return result


def renew_program(name: str, *, chain=None, env=None, target=None,
                  clock=time.time) -> Result:
    """`moxi comms send-only renew NAME`: replace the token of `name`, a program's
    send-only name. The old token is removed from every step of this process's chain
    that holds it first, so a stale copy in an earlier step can't shadow the new one;
    then a fresh one goes into the first writable step and its hash is committed. A step
    that can't be reached from here, or can't be written (the environment), keeps any
    copy it has: the result's lines say so, and `unreadable` names the name when this
    process doesn't read the new token back. Raises `Refused`, with nothing written,
    when `name` isn't a program's, and from inside a session (`session_guard`)."""
    from moxi.store import transaction

    from .secrets import SecretError

    session_guard("renew", name)
    chain = _chain(chain, env)
    result = Result()
    with _Held(target, env) as conn:
        row = lookup(conn, name)
        if row is None or row["state"] == UNOWNED:
            raise Refused(_not_registered(name, row))
        if not held_by_program(row):
            if row["state"] == ACTIVE:
                raise Refused(f"{module_words(row)}: moxi comms reserved renew {name}")
            raise Refused(f"{module_words(row)}: no token sends under it")
        unreached = _remove_everywhere(chain, name)
        token = _secrets.token_urlsafe(32)
        try:
            step = chain.put(program_secret_id(name), token, owner=program_owner())
        except SecretError as exc:
            result.failed.append((name, None, str(exc)))
            result.lines.append(f"{name}: the new token couldn't be written, so it has "
                                f"none this process reads. {exc}")
            return result
        with transaction(conn) as c:
            c.execute("UPDATE reserved SET token_hash = ? WHERE name = ?",
                      (token_hash(token), name))
    result.lines.append(f"{name}: a new token, in the {step} step; the old one is refused "
                        "from now on")
    for where in unreached:
        result.lines.append(f"{name}: {where} couldn't be reached from here; a copy of "
                            "the old token there stays, and is refused")
    _read_back(chain, name, token, result)
    if not result.unreadable:
        trace_send(None, name, credential=False, sent=True, holder=PROGRAM_HOLDER)
    return result


def remove_program(name: str, *, chain=None, env=None, target=None,
                   clock=time.time) -> Result:
    """`moxi comms send-only remove NAME`: free `name`, a program's send-only name or an
    `unowned` one. The row is freed and its hash cleared, its interval closed, and its
    token removed from every step of this process's chain that holds it. The name is
    ordinary again: a session may bind it, as someone new. Raises `Refused`, with
    nothing written, for a module's name or a name with no row, and from inside a
    session (`session_guard`)."""
    from moxi.store import transaction

    session_guard("remove", name)
    chain = _chain(chain, env)
    result = Result()
    with _Held(target, env) as conn:
        row = lookup(conn, name)
        if row is None:
            raise Refused(f"{name} isn't a registered send-only name")
        if row["state"] != UNOWNED and not held_by_program(row):
            gone = (f"it goes when {row['module']} is uninstalled" if row["module"] else
                    "it goes when no module declares it")
            raise Refused(f"{module_words(row)}: {gone}")
        with transaction(conn) as c:
            _free_row(c, name, stamp(clock()))
    result.freed.append((name, None))
    result.lines.append(f"{name} removed: it is an ordinary name again")
    for where in _remove_everywhere(chain, name):
        result.lines.append(f"{name}: {where} couldn't be reached from here; a copy of "
                            "its token there stays, and sends nothing")
    trace_send(None, name, credential=False, sent=True, holder=PROGRAM_HOLDER)
    return result


def _not_registered(name: str, row) -> str:
    """The words for a name no program holds: none registered it, or an older comms
    kept it."""
    kept = " (an older comms kept it)" if row is not None and row["state"] == UNOWNED else ""
    return (f"{name} isn't a registered send-only name{kept}: moxi comms send-only add "
            f"{name}")


def _remove_everywhere(chain, name: str) -> list[str]:
    """Remove the token of `name`, a program's send-only name, from every step of
    `chain` that holds it. Returns the steps that couldn't be reached, in a sentence's
    words: a keychain with no usable backend, and a step that broke."""
    from .secrets import STEP_KEYCHAIN, SecretStepFailed

    sid, holder = program_secret_id(name), program_owner()
    unreached = []
    for step in getattr(chain, "steps", (chain,)):
        try:
            if step.delete(sid, owner=holder):
                continue
        except SecretStepFailed as exc:
            unreached.append(f"the {step.name} step ({exc})")
            continue
        if step.name == STEP_KEYCHAIN and step.backend() is None:
            unreached.append(f"the {step.name} step")
    return unreached


def _first_held(chain, name: str):
    """(the first step of `chain` that holds a token for `name`, a program's send-only
    name, and the value it holds), or (None, None). Raises `SecretError` when a step
    before it breaks."""
    sid, holder = program_secret_id(name), program_owner()
    for step in getattr(chain, "steps", (chain,)):
        found = step.get(sid, owner=holder)
        if found is not None:
            return step, found["value"]
    return None, None


def _shadow(step, name: str) -> str:
    """Where a step holds a stale token for `name`, a program's send-only name, in the
    words that say how to remove it."""
    from .secrets import KEYCHAIN_SERVICE, STEP_ENV, STEP_KEYCHAIN, env_key

    sid = program_secret_id(name)
    if step.name == STEP_ENV:
        return f"the environment variable {env_key(sid)}"
    if step.name == STEP_KEYCHAIN:
        return (f"the keychain (service {KEYCHAIN_SERVICE}, user "
                f"{program_owner()}:{sid})")
    return step.describe(sid)


def _read_back(chain, name: str, token: str, result: Result) -> None:
    """Read `name`'s token back through `chain`, as a send would. When the value read
    isn't `token`, say which step shadows it, and add the name to `unreadable`."""
    from .secrets import SecretError

    try:
        step, value = _first_held(chain, name)
    except SecretError as exc:
        why = f"its token can't be read back: {exc}"
    else:
        if value == token:
            return
        why = (f"{_shadow(step, name)} holds an older token, which this process reads "
               "first: remove it there" if step is not None else
               "no step of this process's credential chain reads its token back")
    result.unreadable.append((name, why))
    result.lines.append(f"this process can't send under {name}: {why}")


def _unreadable(chain, name: str, digest) -> str | None:
    """Why this process can't send under `name`, a program's send-only name whose hash
    is `digest`, or None when it can."""
    from .secrets import SecretError

    try:
        step, value = _first_held(chain, name)
    except SecretError as exc:
        return f"its token can't be read: {exc}"
    if step is None:
        return "no step of its credential chain holds the token"
    if digest is None or token_hash(value) != digest:
        return f"{_shadow(step, name)} holds a stale token"
    return None


class _Held:
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
            self._lock = _files.lock(target.parent / LOCK, timeout_s=60.0)
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
    table = rows(conn)
    # A name a program registered is the program's until it removes it: no path below
    # writes its row, and each module that declares it gets a conflict line instead.
    programs = {name for name, row in table.items()
                if row["freed_at"] is None and row["holder"] == PROGRAM_HOLDER}
    for module in sorted(declared):
        if declared[module] is UNKNOWN:
            # A module that is installed but can't be read keeps every name reserved
            # for it, as though it declared them still.
            for name, row in sorted(table.items()):
                if (row["freed_at"] is None and row["state"] == ACTIVE
                        and row["module"] == module):
                    wanted.setdefault(name, []).append(module)
            continue
        for name in declared[module]:
            why = _bad_name(str(name))
            if why:
                bad.append((str(name), module, why))
            else:
                wanted.setdefault(name, []).append(module)

    # Frees first: a row whose module no longer declares it (or is gone), and a
    # conflict row nobody declares now.
    for name, row in table.items():
        if row["freed_at"] is not None or name in programs:
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
                  keep=name in _KEPT or name in programs or (
                      row is not None and row["freed_at"] is None
                      and row["state"] == UNOWNED), clock=clock)

    for name in sorted(wanted):
        modules_ = wanted[name]
        if name in programs:
            for module in modules_:
                _conflict(conn, result, name, module, "a program registered it",
                          keep=True, clock=clock)
            continue
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
    """Record that `name` couldn't be reserved for `module`, and say so. With `keep`, the
    table isn't written: an unowned row stays unowned (it still refuses binds and mail),
    and a name the daemon keeps gets no row."""
    result.conflicts.append((name, module, why))
    who = module or "a module"
    result.lines.append(f"{who}: {name} isn't reserved: {why}")
    if keep:
        return
    from moxi.store import transaction

    with transaction(conn) as c:
        c.execute("INSERT INTO reserved(name, module, token_hash, state, reserved_at, "
                  "freed_at, note, holder) VALUES (?, ?, NULL, 'conflict', NULL, NULL, ?, "
                  "?) ON CONFLICT(name) DO UPDATE SET module = excluded.module, "
                  "token_hash = NULL, state = 'conflict', reserved_at = NULL, "
                  "freed_at = NULL, note = excluded.note, holder = excluded.holder",
                  (name, module, why, MODULE_HOLDER))


def _mint(conn, chain, name: str, module: str, clock, result: Result, *,
          adopt: bool = False, renew: bool = False) -> None:
    """A fresh token for `name`, written to the chain for `module`, then its hash
    committed. A write that fails commits nothing."""
    from moxi.store import transaction

    from .secrets import SecretError

    token = _secrets.token_urlsafe(32)
    try:
        step = chain.put(secret_id(name), token, owner=owner(module))
    except SecretError as exc:
        result.failed.append((name, module, str(exc)))
        result.lines.append(f"{module}: {name} isn't reserved: its token couldn't be "
                            f"written. {exc}")
        return
    at = stamp(clock())
    with transaction(conn) as c:
        if renew:
            c.execute("UPDATE reserved SET token_hash = ? WHERE name = ?",
                      (token_hash(token), name))
        else:
            _commit_active(c, name, module, MODULE_HOLDER, token_hash(token), at)
    if renew:
        result.lines.append(f"{name}: a new token for {module}, in the {step} step")
    elif adopt:
        result.adopted.append((name, module))
        result.lines.append(f"{module}: {name} reserved (adopted from an older comms)")
    else:
        result.reserved.append((name, module))
        result.lines.append(f"{module}: {name} reserved")
    log.info("reserved %s for %s (token in the %s step)", name, module, step)


def _commit_active(c, name: str, module, holder: str, digest: str, at: str) -> None:
    """In the open transaction `c`: `name`'s row active for its holder with the token
    hash `digest`, and its `bindings` interval of kind `reserved` opened unless one is
    open already."""
    c.execute("INSERT INTO reserved(name, module, token_hash, state, reserved_at, "
              "freed_at, note, holder) VALUES (?, ?, ?, 'active', ?, NULL, NULL, ?) "
              "ON CONFLICT(name) DO UPDATE SET module = excluded.module, "
              "token_hash = excluded.token_hash, state = 'active', "
              "reserved_at = excluded.reserved_at, freed_at = NULL, note = NULL, "
              "holder = excluded.holder",
              (name, module, digest, at, holder))
    open_ = c.execute("SELECT 1 FROM bindings WHERE qualified = ? AND kind = ? "
                      "AND released_at IS NULL", (name, BINDING_KIND)).fetchone()
    if open_ is None:
        c.execute("INSERT INTO bindings(qualified, name, scope, tier, adapter, "
                  "bound_at, kind) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (name, name, GLOBAL, BINDING_TIER, BINDING_ADAPTER, at, BINDING_KIND))


def _free(conn, chain, name: str, module, clock, result: Result, *,
          holder: str | None = MODULE_HOLDER) -> None:
    """Free `name`: the row freed and its interval closed, then its token removed."""
    from moxi.store import transaction

    from .secrets import SecretError

    at = stamp(clock())
    with transaction(conn) as c:
        _free_row(c, name, at)
    result.freed.append((name, module))
    who = "send-only" if holder == PROGRAM_HOLDER else module
    result.lines.append(f"{who}: {name} freed")
    sid, holder_owner = credential(name, module, holder)
    try:
        chain.delete(sid, owner=holder_owner)
    except SecretError as exc:
        # The row is freed and its hash gone, so the token no longer sends anything.
        result.lines.append(f"{who}: {name}'s token couldn't be removed: {exc}")
        log.warning("freed %s, but its token couldn't be removed: %s", name, exc)


def _free_row(c, name: str, at: str) -> None:
    """In the open transaction `c`: `name`'s row freed, its hash cleared, and its
    `reserved` interval closed."""
    c.execute("UPDATE reserved SET freed_at = ?, token_hash = NULL WHERE name = ?",
              (at, name))
    c.execute("UPDATE bindings SET released_at = ? WHERE qualified = ? AND kind = ? "
              "AND released_at IS NULL", (at, name, BINDING_KIND))


# --------------------------------------------------------------------------- the trace

def record_key(name: str) -> str:
    """The key of the doctor record a send under `name` leaves when its process can't
    use the token."""
    return f"reserved-token-{name}"


def trace_send(module: str | None, name: str, *, credential: bool, sent: bool,
               holder: str | None = MODULE_HOLDER) -> None:
    """Leave or clear the doctor's record of a send under `name` for `module`, or, with
    `holder` `program`, for the program that registered it.

    The process that sends (a module's service, a timer, a program) may read the
    credential chain from a different context than the shell the doctor runs in, so the
    doctor can't see the miss by reading the chain itself. A send refused for its
    credential records which process found no usable token, and the record stays until
    a send under `name` works again, a renew mints a new one, or a program's name is
    removed. Never raises: the send's own result is the answer."""
    import sys

    from moxi import doctor

    try:
        if credential:
            prog = os.path.basename(sys.argv[0]) if sys.argv and sys.argv[0] else "python"
            where = f"the environment of {prog} (pid {os.getpid()})"
            if holder == PROGRAM_HOLDER:
                doctor.record(MODULE, f"comms can't read the token for {name} from "
                              f"{where}, where a program sends under it; renew it from "
                              "that environment, not from a shell that reads the chain "
                              "differently", key=record_key(name),
                              fix=f"moxi comms send-only renew {name}, run where {name} "
                                  "sends from")
                return
            doctor.record(MODULE, f"comms can't read the token for {name} from {where}, "
                          f"where {module} sends under it; renew it from that "
                          "environment if the shell you run this in reads it fine",
                          key=record_key(name), fix=f"moxi comms reserved renew {name}")
        elif sent:
            doctor.clear(MODULE, record_key(name))
    except Exception as exc:                                       # noqa: BLE001
        log.warning("the doctor's record for %s couldn't be written: %s", name, exc)


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
    except StoreRefused as exc:
        return [f"comms: reserved names weren't changed: {exc.reason}"]


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
    except StoreRefused as exc:
        return [Row(MODULE, "reserved", "warn", f"reserved names weren't checked: "
                    f"{exc.reason}")]
    out = [Row(MODULE, "reserved", "warn", f"{module or 'a module'}: {name} isn't "
               f"reserved: {why}", "moxi doctor runs again after the cause is gone")
           for name, module, why in result.conflicts]
    out += [Row(MODULE, "reserved", "fail", f"{module}: {name} isn't reserved: {why}",
                "check the credential chain, then run moxi doctor")
            for name, module, why in result.failed]
    out += token_rows(env=env, chain=chain)
    return out


def token_rows(*, env=None, chain=None, context: str = "this process's") -> list:
    """A row for each active send-only name whose token this process's chain can't
    read, or reads with a hash that doesn't match. A program's name is sent under from
    the program's own environment, so its row says to renew there, and a stale copy is
    named by the step that holds it: removing that copy is the cure, not a renew from
    here."""
    from moxi.doctor import Row

    from .secrets import SecretError
    from .store import StoreRefused
    from .store.open import open_read

    try:
        conn = open_read(env=env)
    except (StoreRefused, sqlite3.Error):
        return []
    try:
        active = [(r[0], r[1], r[2], r[3]) for r in conn.execute(
            "SELECT name, module, token_hash, holder FROM reserved WHERE state = 'active' "
            "AND freed_at IS NULL ORDER BY name")]
    finally:
        conn.close()
    chain = _chain(chain, env)
    out = []
    for name, module, digest, holder in active:
        if holder == PROGRAM_HOLDER:
            out += _program_token_rows(chain, name, digest, context)
            continue
        try:
            got = chain.get(secret_id(name), owner=owner(module))["value"]
        except SecretError:
            got = None
        if got is None or token_hash(got) != digest:
            out.append(Row(MODULE, "reserved", "warn",
                           f"comms can't read the token for {name} from {context} "
                           "environment", f"moxi comms reserved renew {name}"))
    return out


def _program_token_rows(chain, name: str, digest, context: str) -> list:
    """The doctor's row for `name`, a program's send-only name, when this process can't
    read its token, or reads a stale copy."""
    from moxi.doctor import Row

    from .secrets import SecretError

    try:
        step, value = _first_held(chain, name)
    except SecretError as exc:
        return [Row(MODULE, "reserved", "warn", f"comms can't read the token for {name}: "
                    f"{exc}", "check the credential chain, then run moxi doctor")]
    if step is None:
        return [Row(MODULE, "reserved", "warn",
                    f"comms can't read the token for {name} from {context} environment; "
                    "a program registered it, and sends under it from its own",
                    f"moxi comms send-only renew {name}, run where {name} sends from, "
                    "if that environment can't read it either")]
    if digest is not None and token_hash(value) == digest:
        return []
    return [Row(MODULE, "reserved", "warn",
                f"{_shadow(step, name)} holds a stale token for {name}, which {context} "
                "environment reads first",
                f"remove that stale copy; renew {name} only where it sends from")]


__all__ = ["ACTIVE", "CONFLICT", "UNOWNED", "UNKNOWN", "MODULE_HOLDER", "PROGRAM_HOLDER",
           "PROGRAM_OWNER", "PROGRAM_SECRET_PREFIX", "SECRET_PREFIX", "Refused", "Result",
           "INSIDE", "UNPLACED", "credential", "declared_names", "doctor_rows", "held_by_program", "lookup",
           "module_words", "on_install", "on_uninstall", "owner", "program_owner",
           "program_secret_id", "reconcile", "record_key", "register", "release_all",
           "remove_program", "renew", "renew_program", "rows", "secret_id", "session_guard", "session_place", "token_hash",
           "token_rows", "trace_send"]
