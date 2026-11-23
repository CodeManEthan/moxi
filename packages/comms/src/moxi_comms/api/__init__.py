"""comms' interface 2: the module other code reaches (`moxi_comms.api`).

Everything named here is public and pinned by comms' own tests; anything else in
`moxi_comms` is private. A Moxi module reaches only this module (the contract check
refuses the rest); `moxi_comms.adapter` is the other public surface, for adapters built
outside comms.

**Sending.** There is one send path, and `moxi comms send` renders what it returns.

* `send(sender, to, text, *, posture, ...)` sends one message and returns a
  `SendResult`. `check(...)`, with the same arguments, runs every check and posts
  nothing (`moxi comms send --check`).
* `send_as(module, name, to, text, *, expires, interrupt)` sends under a send-only name
  reserved for `module`, from that module's own code, outside every session. Given a
  name a program registered, it is refused `not-reserved`.
* `send_under(name, to, text, *, expires, interrupt)` sends under a send-only name a
  program registered (`moxi comms send-only add`), outside every session, as `moxi
  comms send --as` does. Refused `not-registered` for a name no program registered,
  `module-reserved` for a module's, and `credential` when the token can't be read.
* `Sender`: who sends. `Sender.session()` (this process's stamps and its session's claim
  socket), `Sender.socket(path)`, `Sender.claim(server)` (a `ClaimServer` this process
  holds) or `Sender.operator(user_id)` (the operator's lane).
* `SendResult`: `outcome` (one of `OUTCOMES`), `exit_code` (the verb's number for it),
  `message_id`, `recipients` (one `Recipient` per resolved name, its `delivery` one of
  `DELIVERIES`), `refusal` (a `Refusal`, `code` and `text`, when nothing was posted),
  `notes`, and `lines()`, the verb's text. Branch on the data, never on the text.
* `send`, `check`, `send_as`, `send_under` and `delete` each close the record they opened
  before they return, so no connection to it outlives the call.

**The other surfaces.**

* `ClaimServer`, for a binding held by a module's own process.
* Reads: `attributed(message_id)`, `session(identity)`, `session_of(identity)`,
  `roster()` (a `Roster`: `bound`, `standing`, and `waiting`, one `Waiting` per name
  with mail queued), `owner(session_id)`, `roster_rows()` (one `RosterRow` per address: the
  roster joined with each live name's session, its cache (a `CacheFact`), whom it
  awaits and, for the connector's binding, comms' words for it in `via`). A send-only
  entry of either carries `holder`: `module`, `program`, or None for a name an older
  comms kept,
  `identity_state(name, scope=None)`,
  `caller_identity(env=None)`, `record_dir()`, `daemon_socket()`.
* Names: `GLOBAL`, `qualify`, `split_tag`, `NAME_RE`.
* Directors: `policy_directors()`, the names the box's `[policy] directors` sets that
  hold a standing row now, qualified (empty when comms' config or its directory can't be
  read), and `effective(declared, *, own, use_policy)`, the one merge of a launch's own
  directors with the box's policy directors, for an adapter kind built outside comms
  (it loads the box's comms config; a comms caller may pass its own second): it returns the whole list and the part only the policy gave, which the
  adapter's holder writes into its claim as `directors` and `policy_directors`.
  `Agent`, `Owner` and `RosterRow` carry both lists, read from the claim files.
* `CODEX_RESUME`: True when the Codex wrapper, `moxi comms codex --name N`, takes
  `--resume THREAD_ID`; a comms without it has no resume.
* `ENDS_ON_BIND_TIMEOUT`: a frozenset of harness names, `{"claude", "codex"}`: the
  harnesses whose comms wrapper ends the launch with exit 75 (`moxi.exit.TEMPFAIL`) when
  its bind isn't answered in the wrapper's wait, and never starts the harness without
  the name. A comms without it has wrappers that start the harness anyway.
* `HEADLESS`: a frozenset of harness names, `{"codex"}`: the harnesses whose comms
  wrapper takes `--headless`, `--exit WHEN` and `--brief`/`--brief-file`, a session with
  no window that holds its name and ends by its exit rule. A comms without it has no
  headless launch.
* `parse_exit(values, *, harness)`: the one parser of a headless launch's exit rule (the
  `--exit` values, in order); raises `ValueError` with the wrapper's words for a value
  the wrapper would refuse, and for a harness `HEADLESS` doesn't name. Its `cold_gap` says whether the rule warns that mail sent
  once the cache is cold waits for the name's next holder.
* `BIND_WAIT`: the longest a wrapper the launcher starts (Claude's or Codex's) waits for
  its bind, in seconds, read from the wrappers' own waits.
* `DaemonError`, `DaemonOffline`: what a read raises when the daemon refuses or is down.
* `moxi_comms.api.tokens`: the token store the box's own consumers read.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from moxi import modules as _modules

from .. import cache as _cache
from .. import claimfile as _claimfile
from .. import config as _config
from .. import directors as _directors
from .. import directory as _directory
from .. import kinds as _kinds
from .. import machine_line as _machine_line
from .. import paths as _paths
from .. import reserved as _reserved_mod
from .. import sending as _sending
from .. import wire as _wire
from ..adapters import codex_wrapper as _codex_wrapper
from ..adapters import session as _claude_wrapper
from ..adapters.headless import HEADLESS, parse_exit
from ..claimhold import ClaimServer
from ..client import DaemonError, DaemonOffline
from ..constants import (BACKEND_ENV, CLAIM_SOCKET_ENV, COMMAND_ENV, DIRECTORS_ENV,
                         GLOBAL_ENV, MOCK_BACKEND_ENV, NAME_ENV, OPERATOR_ENV,
                         POLICY_DIRECTORS_ENV, PROJECT_ENV, SCOPE_ENV, SECRET_PREFIX_ENV, SHELL_REACH_ENV,
                         SHIM_SOCKET_ENV, TOKEN_ENV, TOKEN_STORE_ENV, URL_ENV)
from ..scoping import GLOBAL, NAME_RE, is_auto_name, qualify, split_tag
from ..sending import (DELIVERIES, EXIT_CODES, OUTCOMES, DeleteResult, Recipient, Refusal,
                       Sender, SendResult)

#: The interface this module is.
INTERFACE = 2

#: The Codex wrapper takes `--resume THREAD_ID` (with `--name`) and reports a resume.
CODEX_RESUME = True

#: The harnesses whose comms wrapper ends the launch, exit 75, when its bind isn't
#: answered in its wait: neither starts its harness under a name it doesn't hold.
ENDS_ON_BIND_TIMEOUT = frozenset({"claude", "codex"})

#: The longest the Claude and Codex wrappers wait for their bind, in seconds.
BIND_WAIT = max(_claude_wrapper.READY_WAIT, _codex_wrapper.READY_WAIT)

#: A read's default wait for the daemon, in seconds.
READ_DEADLINE = 10.0

#: What `posture` may be: a new send states one; a reply may leave it None.
POSTURES = (_machine_line.POSTURE_NONE, _machine_line.POSTURE_OPTIONAL,
            _machine_line.POSTURE_ONCE, _machine_line.POSTURE_OPEN)


# --------------------------------------------------------------------------- sending

def send(sender: Sender, to, text: str, *, posture, reply_to=None, new: bool = False,
         audience=None, held: bool = False, cap=None, no_cap: bool = False,
         timeout_s: float = 30.0, standing: bool = False,
         new_name: bool = False, interrupt: bool = False,
         expires=None) -> SendResult:
    """Send `text` to `to` as `sender`, with every check `moxi comms send` makes.

    `to` is a name, a comma-separated list of names, or a sequence of names. With `to`
    None and `reply_to` a message id, this is `moxi comms reply`: the message goes to that
    message's author (or, for your own message, to its recipients). With both, it is
    `send --reply-to`. `posture` is `none`, `optional`, `once` or `open`, or None (a
    reply, or the operator). `cap` is a whole number of replies. `standing` and
    `new_name` are the verb's `--standing` and `--new-name`. `interrupt` is
    `--interrupt`. `expires` is `--expires`: a duration (`30m`, `6h`, `3d`), `never`, a
    whole number of seconds, or None for the config's default. `timeout_s` is seconds,
    passed to every daemon call, to the claim socket and to the record's busy timeout.

    The result says whether it posted (`message_id`), and a post either commits or does
    not. Raises only on arguments the verb's parser would refuse (`ValueError`,
    `TypeError`)."""
    return _run(sender, to, text, posture=posture, reply_to=reply_to, new=new,
                audience=audience, held=held, cap=cap, no_cap=no_cap, deadline=timeout_s,
                standing=standing, new_name=new_name, interrupt=interrupt,
                expires=expires, check=False)


def check(sender: Sender, to, text: str, *, posture, reply_to=None, new: bool = False,
          audience=None, held: bool = False, cap=None, no_cap: bool = False,
          timeout_s: float = 30.0, standing: bool = False,
          new_name: bool = False, interrupt: bool = False,
          expires=None) -> SendResult:
    """`send`'s every check, stopping before the post: outcome `would-send`, with the
    recipients and notes the post would have, or the refusal the send would give.
    Posts, nudges and records nothing."""
    return _run(sender, to, text, posture=posture, reply_to=reply_to, new=new,
                audience=audience, held=held, cap=cap, no_cap=no_cap, deadline=timeout_s,
                standing=standing, new_name=new_name, interrupt=interrupt,
                expires=expires, check=True)


def _run(sender, to, text, *, posture, reply_to, new, audience, held, cap, no_cap,
         deadline, standing, new_name, interrupt, expires, check) -> SendResult:
    if not isinstance(sender, Sender):
        raise TypeError(f"sender must be an api.Sender, not {type(sender).__name__}")
    if expires is not None:
        _sending.expiry_seconds(expires)          # a bad duration raises ValueError
    if posture is not None and posture not in POSTURES:
        raise ValueError(f"posture must be one of {', '.join(POSTURES)} or None, "
                         f"not {posture!r}")
    args = SimpleNamespace(text=str(text), posture=posture, cap=cap, no_cap=bool(no_cap),
                           audience=audience, new=bool(new), held=bool(held),
                           standing=bool(standing), new_name=bool(new_name),
                           interrupt=bool(interrupt), expires=expires,
                           check=check, reply_to=None, message_id=None)
    tags = None
    if to is None:
        if reply_to is None:
            raise ValueError("send needs `to`, or `reply_to` alone for a reply to a "
                             "message's author")
        args.message_id = str(reply_to)
    else:
        names = to.split(",") if isinstance(to, str) else [str(t) for t in to]
        tags = [t.strip() for t in names if t.strip()]
        if not tags:
            raise ValueError("send needs `to` to name at least one recipient")
        args.reply_to = None if reply_to is None else str(reply_to)
    ctx = _sending.Context(env=sender.sender_env(os.environ), deadline=deadline)
    try:
        return _sending.run(ctx, args, tags=tags)
    finally:
        ctx.close()


def send_as(module: str, name: str, to, text: str, *, expires=None,
            interrupt: bool = False, timeout_s: float = 30.0) -> SendResult:
    """Send `text` to `to` under `name`, a send-only name reserved for `module`, as that
    module's own code: its token is read through this process's credential chain and
    presented to the daemon, which refuses a sender inside a live session. The posture is
    always `[no reply]`, since nobody can mail a reserved name. A refusal is the result's
    `outcome` and `refusal` (`credential` when the token can't be read: run `moxi
    doctor`; `not-reserved` for a name a program registered), as for `send`; it raises
    only for arguments `send` would refuse."""
    return _send_only("send_as", str(module), name, to, text, expires=expires,
                      interrupt=interrupt, timeout_s=timeout_s)


def send_under(name: str, to, text: str, *, expires=None, interrupt: bool = False,
               timeout_s: float = 30.0) -> SendResult:
    """Send `text` to `to` under `name`, a send-only name a program registered with
    `moxi comms send-only add`: its token is read through this process's credential
    chain and presented to the daemon, which refuses a sender inside a live session (so
    does this call, before it reads the token). The posture is always `[no reply]`. A
    refusal is the result's `outcome` and `refusal`: `not-registered` for a name no
    program registered, `module-reserved` for a module's name (`send_as` sends under
    those), `credential` when the token can't be read (`moxi comms send-only renew
    NAME`, from this environment); it raises only for arguments `send` would refuse."""
    return _send_only("send_under", None, name, to, text, expires=expires,
                      interrupt=interrupt, timeout_s=timeout_s)


def _send_only(verb: str, module, name, to, text, *, expires, interrupt: bool,
               timeout_s: float) -> SendResult:
    """`send_as` (with `module`) and `send_under` (without): one send under a send-only
    name, with no reply."""
    if expires is not None:
        _sending.expiry_seconds(expires)          # a bad duration raises ValueError
    names = to.split(",") if isinstance(to, str) else [str(t) for t in to]
    tags = [t.strip() for t in names if t.strip()]
    if not tags:
        raise ValueError(f"{verb} needs `to` to name at least one recipient")
    args = SimpleNamespace(text=str(text), posture=_machine_line.POSTURE_NONE, cap=None,
                           no_cap=False, audience=None, new=False, held=False,
                           standing=False, new_name=False, interrupt=bool(interrupt),
                           expires=expires, check=False, reply_to=None, message_id=None)
    ctx = _sending.Context(env=dict(os.environ), deadline=timeout_s)
    try:
        return _sending.run_reserved(ctx, args, module=module, name=str(name), tags=tags)
    finally:
        ctx.close()


def delete(sender: Sender, message_id, *, timeout_s: float = 30.0) -> DeleteResult:
    """Withdraw `message_id`, which `sender` sent, from every recipient that hasn't had
    it in a digest (`moxi comms remove`). The `DeleteResult` names who it was withdrawn
    from, who had it already and whose copy expired first."""
    if not isinstance(sender, Sender):
        raise TypeError(f"sender must be an api.Sender, not {type(sender).__name__}")
    ctx = _sending.Context(env=sender.sender_env(os.environ), deadline=timeout_s)
    try:
        return _sending.delete(ctx, message_id)
    finally:
        ctx.close()


# --------------------------------------------------------------------------- reads

def _daemon(deadline: float):
    """A connection to the daemon once it has answered with this comms' protocol."""
    return _sending.Context(deadline=deadline).daemon()


def attributed(message_id, *, timeout_s: float = READ_DEADLINE) -> dict | None:
    """One message with its author as the daemon attributes it, or None when the record
    holds no such id: `message_id`, `room`, `author` (`name`, `tier`, `attribution`),
    `recipients`, `resolved`, `text`, `timestamp`. Raises `DaemonOffline` or
    `DaemonError`."""
    client = _daemon(timeout_s)
    try:
        return client.attributed_message(message_id)
    finally:
        client.close()


def session(identity: str, *, timeout_s: float = READ_DEADLINE) -> dict:
    """Which live session sits behind one bound name: the daemon's `session` answer
    (`bound`, `adapter`, `session_id`, `bound_at`, the cache read, ...). An unbound name
    answers `bound` False. Raises `DaemonOffline` or `DaemonError`."""
    client = _daemon(timeout_s)
    try:
        answer = client.call("session", {"identity": str(identity)})
    finally:
        client.close()
    return {k: v for k, v in answer.items() if k not in ("id", "ok")}


def session_of(identity: str, *, timeout_s: float = READ_DEADLINE) -> str | None:
    """The live session id behind the bound name `identity`, as its adapter reports it;
    None when the name isn't bound (a reserved name never is) or its binding has
    reported no session id yet. Raises what `session` raises."""
    answer = session(identity, timeout_s=timeout_s)
    sid = answer.get("session_id")
    return sid if answer.get("bound") and isinstance(sid, str) and sid else None


def caller_identity(env=None) -> str | None:
    """The qualified identity the calling process's own session holds, or None.

    It is proven by the claim holder, never read from a name variable: the claim socket
    `env` names (else this process's environment) answers `claim` only to a process
    inside its session, checked on the connecting process, so an environment copied from
    another session proves nothing. None for no socket named, a path outside comms'
    runtime directory, an adapter that doesn't answer, a caller outside the session, and
    a claim that isn't bound or carries a refusal. It never returns the token, raises
    nothing for those cases, and asks the daemon nothing."""
    from ..claimfile import BOUND
    from ..claimhold import AdapterMismatch, ClaimUnreachable, call, socket_from_env
    from ..paths import OutsideRuntime

    try:
        path = socket_from_env(env)
    except OutsideRuntime:
        return None
    if not path:
        return None
    try:
        answer = call(path, "claim", timeout=READ_DEADLINE)
    except (AdapterMismatch, ClaimUnreachable):
        return None
    if (not answer.get("ok") or answer.get("state") != BOUND
            or answer.get("line") is not None):
        return None
    qualified = (answer.get("claim") or {}).get("qualified")
    return qualified if isinstance(qualified, str) and qualified else None


@dataclass(frozen=True)
class Agent:
    """A bound name: its binding as the daemon holds it, the directors its session runs
    with, and the part of them only the box's `[policy] directors` gave."""

    qualified: str
    name: str
    scope: str
    tier: str | None
    adapter: str | None
    directors: tuple = ()
    policy_directors: tuple = ()


@dataclass(frozen=True)
class Standing:
    """A standing identity: a directory row (`kind` `agent`), or a send-only name (`kind`
    `send-only`: active, or kept from an older comms). `module` names the module the name
    is reserved for, or declared by when it is in conflict; None for a name no module
    owns. `holder` says who holds a send-only name: `module`, `program` (a name a program
    registered, with no module), or None (one an older comms kept)."""

    qualified: str
    name: str
    scope: str
    kind: str
    module: str | None = None
    holder: str | None = None


@dataclass(frozen=True)
class Waiting:
    """Mail queued for one name: how many messages, and the oldest one's time (epoch
    seconds). One queue holds mail for a name that is offline, cold or flagged alike, and
    no reason is kept, so a bound name can have mail waiting too."""

    qualified: str
    count: int
    oldest: float


@dataclass(frozen=True)
class Roster:
    """Who can be messaged: the bound names, the standing rows, and the names with mail
    waiting."""

    bound: tuple = ()
    standing: tuple = ()
    waiting: tuple = ()


@dataclass(frozen=True)
class Owner:
    """Which identity owns one session id now (`identity` None when no live binding
    does), its adapter kind, its directors (and the policy part of them), and whom it
    awaits."""

    session_id: str
    identity: str | None
    adapter: str | None = None
    directors: tuple = ()
    awaiting: tuple = ()
    policy_directors: tuple = ()


def _declared_directors() -> dict:
    """qualified -> (the directors its live claim holds, the policy part of them). Claim
    files that can't be read declare none: the daemon's answer stands without them."""
    out = {}
    try:
        records = _claimfile.live_records()
    except OSError:
        return out
    for _key, record in records:
        qualified = _claimfile.held_identity(record)
        if qualified:
            claim = record.get("claim") or {}
            out.setdefault(qualified, (tuple(claim.get("directors") or ()),
                                       tuple(claim.get("policy_directors") or ())))
    return out


def policy_directors() -> tuple:
    """The names the box's `[policy] directors` sets that hold a standing row now,
    qualified, in the setting's order: the policy directors that can direct. Empty when
    comms' config or its directory can't be read (`moxi comms doctor` says why)."""
    try:
        names = _config.load().policy.directors
    except _config.ConfigError:
        return ()
    held = _directory.provisioned(names)
    return tuple(name for name in names if name in held) if held else ()


def effective(declared, config=None, *, own: str, use_policy: bool) -> tuple[list, list]:
    """A launch's directors and the part of them only the box's policy gave
    (`directors.effective`): `declared` then the policy directors not already there, when
    `use_policy`, without `own`, the launching session's qualified identity.

    `config` is left out by a caller outside comms: the box's comms config is then
    loaded when `use_policy`, as comms' own wrappers load it. A comms caller holding one
    already passes it. Raises `moxi.config.ConfigError` when that config can't be read,
    and `ValueError` for a declared name that is not a name."""
    if config is None and use_policy:
        config = _config.load()
    return _directors.effective(declared, config, own=own, use_policy=use_policy)


def roster(*, timeout_s: float = READ_DEADLINE) -> Roster:
    """The bound agents with their directors (the launch-declared ones and the policy
    ones, `policy_directors` saying which), the standing names (the
    directory's standing rows and the reserved send-only names) and the mail waiting per
    name, as the daemon's `agents` answer lists them. Raises `DaemonOffline` or
    `DaemonError`."""
    client = _daemon(timeout_s)
    try:
        answer = client.call("agents")
    finally:
        client.close()
    directors = _declared_directors()
    bound = tuple(Agent(qualified=b["qualified"], name=b.get("name"), scope=b.get("scope"),
                        tier=b.get("tier"), adapter=b.get("adapter"),
                        directors=directors.get(b["qualified"], ((), ()))[0],
                        policy_directors=directors.get(b["qualified"], ((), ()))[1])
                  for b in answer.get("bindings") or ())
    standing = tuple(Standing(qualified=s["qualified"], name=s.get("name"),
                              scope=s.get("scope"), kind=s.get("kind"),
                              module=s.get("module"), holder=s.get("holder"))
                     for s in answer.get("standing") or ())
    waiting = tuple(Waiting(qualified=q, count=w.get("count"), oldest=w.get("oldest"))
                    for q, w in (answer.get("waiting") or {}).items())
    return Roster(bound=bound, standing=standing, waiting=waiting)


#: The kind a reserved name reads as in `roster().standing`.
SEND_ONLY = _reserved_mod.SEND_ONLY


def _reserved_rows(conn) -> dict:
    """qualified -> (the module each send-only name is reserved for, or None when no
    module owns it; its state; its holder), for every row not freed."""
    return {row["name"]: (row["module"], row["state"], row["holder"])
            for row in conn.execute("SELECT name, module, state, holder FROM reserved "
                                    "WHERE freed_at IS NULL")}


def _reserved(qualified: str) -> tuple[bool, str | None, str | None]:
    """Whether `qualified` is a send-only name (a `reserved` row not freed: active, in
    conflict or unowned, each of which the daemon's bind refuses), its module, and its
    holder. A store that can't be read holds none."""
    import sqlite3

    from ..store.open import Unmoved

    try:
        conn = _directory.connect(_paths.comms_db(), read_only=True)
    except (sqlite3.Error, Unmoved, OSError):
        return False, None, None
    try:
        rows = _reserved_rows(conn)
    except sqlite3.Error:
        return False, None, None
    finally:
        conn.close()
    module, _state, holder = rows.get(qualified, (None, None, None))
    return (qualified in rows), module, holder


def owner(session_id: str, *, timeout_s: float = READ_DEADLINE) -> Owner:
    """Which identity owns `session_id` now, with its directors (launch-declared and
    policy, `policy_directors` saying which) and whom it awaits. Raises `DaemonOffline`, or `DaemonError` (an empty id among them)."""
    client = _daemon(timeout_s)
    try:
        answer = client.call("owner", {"session_id": str(session_id)})
    finally:
        client.close()
    identity = answer.get("owner")
    held, policy = _declared_directors().get(identity, ((), ())) if identity else ((), ())
    return Owner(session_id=answer.get("session_id", session_id), identity=identity,
                 adapter=answer.get("adapter"), directors=held,
                 awaiting=tuple(answer.get("awaiting") or ()), policy_directors=policy)


@dataclass(frozen=True)
class CacheFact:
    """A live name's cache as the daemon's `session` answer reads it, the read its gate
    delivers by: `kind` is `warm` or `cold`, and `until` is when it goes cold (warm) or
    went cold (cold), in epoch seconds rounded to the whole second: the read's time less
    the cache's age plus the lifetime the read used."""

    kind: str
    until: float


@dataclass(frozen=True)
class RosterRow:
    """One address on the roster: a name a live session holds (`live`), or a standing
    name nobody holds now.

    `kind`, `module` and `holder` are the name's standing row's (`agent`, or `send-only`
    with the module it is reserved for and who holds it), None with no standing row. `tier`, `adapter` and
    `directors` are the binding's, so a standing row has none. `session_id`, `bound_at`
    (the daemon's clock, epoch seconds), `awaiting` (whom that session waits on) and
    `cache` (a `CacheFact`) are a live row's, empty when the daemon couldn't say. `cache`
    is None too for any read but `warm` or `cold`, and for one with no age or lifetime,
    so it is None for every row on a box without the cache module. `policy_directors` is
    the part of a live row's `directors` only the box's `[policy] directors` gave. `via`
    is comms' words
    for what a live row's binding is when its adapter kind says so: `Claude's apps,
    through the connector` for the connector's kind while the connector module is
    installed; None for every other row."""

    qualified: str
    name: str
    scope: str | None
    live: bool
    kind: str | None
    module: str | None
    tier: str | None
    adapter: str | None
    directors: tuple = ()
    session_id: str | None = None
    bound_at: float | None = None
    awaiting: tuple = ()
    cache: CacheFact | None = None
    holder: str | None = None
    via: str | None = None
    policy_directors: tuple = ()


#: The module that hosts comms' tools in the Claude apps, which bind as the connector kind.
_CONNECTOR_MODULE = "connector"


def _via(adapter: str | None) -> str | None:
    """comms' words for a binding of kind `adapter`: the connector's words for its kind
    while the connector module can be imported here, else None."""
    if adapter != _kinds.CONNECTOR_KIND or not _modules.available(_CONNECTOR_MODULE):
        return None
    return _kinds.CONNECTOR_VIA


def roster_rows(*, timeout_s: float = READ_DEADLINE, with_roster: bool = False):
    """Who can be messaged, one row per address: `roster()` joined with each bound
    name's `session()` and its session's `owner()`. `moxi comms agents --json` prints
    these rows, and comms' page shows them. With `with_roster` it returns `(rows,
    roster)`, the `Roster` the rows were joined from, so a caller that needs its
    `waiting` too asks the daemon for `agents` once.

    A bound name is a live row, carrying its standing row's `kind` and `module` when it
    has one, and `via` when its adapter kind is the connector's; a standing name nobody
    holds is a standing row. Live rows come first, then
    each group by `qualified`. `timeout_s` goes to every daemon call.

    Raises `DaemonOffline` the first time any call does, making no further call, and
    `DaemonError` when `roster()` does. A `DaemonError` on one name's `session()` or
    `owner()` leaves that row's `session_id`, `bound_at`, `awaiting` and `cache`
    empty."""
    got = roster(timeout_s=timeout_s)
    standing: dict = {}
    for row in got.standing:
        standing.setdefault(row.qualified, row)
    rows = []
    for agent in got.bound:
        kept = standing.pop(agent.qualified, None)
        try:
            session_id, bound_at, awaiting, cache = _live_fields(agent.qualified,
                                                                 timeout_s)
        except DaemonError:
            session_id, bound_at, awaiting, cache = None, None, (), None
        rows.append(RosterRow(
            qualified=agent.qualified, name=agent.name, scope=agent.scope, live=True,
            kind=kept.kind if kept else None,
            module=getattr(kept, "module", None) if kept else None,
            tier=agent.tier, adapter=agent.adapter, directors=tuple(agent.directors),
            session_id=session_id, bound_at=bound_at, awaiting=awaiting, cache=cache,
            holder=getattr(kept, "holder", None) if kept else None,
            via=_via(agent.adapter),
            policy_directors=tuple(getattr(agent, "policy_directors", ()) or ())))
    for row in standing.values():
        rows.append(RosterRow(
            qualified=row.qualified, name=row.name, scope=row.scope, live=False,
            kind=row.kind, module=getattr(row, "module", None), tier=None, adapter=None,
            holder=getattr(row, "holder", None)))
    rows = tuple(sorted(rows, key=lambda r: (not r.live, r.qualified)))
    return (rows, got) if with_roster else rows


def _finite(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _cache_fact(held: dict, read_at: float) -> CacheFact | None:
    """The `CacheFact` of one `session()` answer read at `read_at`, or None."""
    kind, age, ttl = held.get("cache_kind"), held.get("age_s"), held.get("cache_ttl")
    if kind not in (_cache.WARM, _cache.COLD) or not (_finite(age) and _finite(ttl)):
        return None
    until = read_at - age + ttl
    return CacheFact(kind=kind, until=float(round(until))) if _finite(until) else None


def _live_fields(qualified: str, deadline: float) -> tuple:
    """(session_id, bound_at, awaiting, cache) of one bound name, from `session()` and,
    when it names a session, `owner()`."""
    held = session(qualified, timeout_s=deadline)
    read_at = time.time()
    if not held.get("bound"):
        return None, None, (), None
    at = held.get("bound_at")
    bound_at = float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) \
        else None
    cache = _cache_fact(held, read_at)
    sid = held.get("session_id")
    if not (isinstance(sid, str) and sid):
        return None, bound_at, (), cache
    return sid, bound_at, tuple(owner(sid, timeout_s=deadline).awaiting), cache


#: What `identity_state` says of a name.
IDENTITY_STATES = ("free", "held", "standing", "reserved", "retired", "offline")


@dataclass(frozen=True)
class Holder:
    """The live binding that holds a name: its adapter kind and its session's id (None
    while the binding has reported none)."""

    adapter: str | None
    session_id: str | None

    def __str__(self) -> str:
        if self.session_id:
            return f"{self.adapter or 'a'} session {self.session_id}"
        return f"a {self.adapter} session" if self.adapter else "a live session"


@dataclass(frozen=True)
class IdentityState:
    """Whether a name is free for a launch to take, and if not, why.

    `state` is one of `IDENTITY_STATES`: `free`; `held` by a live binding (`holder`, a
    `Holder`); `standing` (a standing identity's directory row, not bound now);
    `reserved` as a send-only name (`module`, None for a name no module owns, and
    `registered` True for a name a program registered); `retired`; or `offline`, when
    the daemon can't be asked (`reason` says why). Branch on `state`, never on text."""

    qualified: str
    state: str
    holder: Holder | None = None
    module: str | None = None
    reason: str | None = None
    registered: bool = False


def identity_state(name: str, scope: str | None = None, *,
                   timeout_s: float = READ_DEADLINE) -> IdentityState:
    """Whether `name` (in `scope`, else the global identity) is free for a launch to
    claim, from the daemon's `resolve` and `session` answers and the store's reserved
    names. `name` may be qualified (`name@scope`) when `scope` is None. Raises
    `ValueError` for what is not a name a launch can claim, and `DaemonError` for an
    answer it can't read as a state."""
    name, scope = _launch_name(name, scope)
    qualified = qualify(name, scope)
    try:
        client = _daemon(timeout_s)
    except DaemonOffline as exc:
        return IdentityState(qualified, "offline", reason=str(exc))
    try:
        try:
            answer = client.call("resolve", {"name": qualified})
        except DaemonError as exc:
            if exc.code == _wire.NAME_RETIRED:
                return IdentityState(qualified, "retired", reason=str(exc))
            raise
        if answer.get("bound"):
            held = client.call("session", {"identity": qualified})
            return IdentityState(qualified, "held", holder=Holder(
                adapter=held.get("adapter"), session_id=held.get("session_id")))
    except DaemonOffline as exc:
        return IdentityState(qualified, "offline", reason=str(exc))
    finally:
        client.close()
    reserved, module, holder = _reserved(qualified)
    if reserved:
        return IdentityState(qualified, "reserved", module=module,
                             registered=holder == _reserved_mod.PROGRAM_HOLDER)
    if answer.get("standing_row"):
        return IdentityState(qualified, "standing")
    return IdentityState(qualified, "free")


def _launch_name(name, scope) -> tuple[str, str]:
    """(name, scope) of what a launch would claim, checked as the wrappers check it."""
    name = str(name)
    if "@" in name:
        if scope is not None:
            raise ValueError(f"{name!r} names its scope; pass the bare name with scope=")
        name, scope = split_tag(name)
    scope = GLOBAL if scope in (None, "") else str(scope)
    if not NAME_RE.match(name) or (scope != GLOBAL and not NAME_RE.match(scope)):
        raise ValueError(f"{qualify(name, scope)!r} is not a name: a comms identity is "
                         f"{NAME_RE.pattern}")
    if is_auto_name(name):
        raise ValueError(f"{name!r} is an agent-N name, which the daemon mints and no "
                         "launch claims")
    return name, scope


def record_dir() -> Path:
    """The directory that holds the box's record file (`[local] record`, else comms.db
    under comms' state directory)."""
    record = _config.load().local.record
    return (Path(record).expanduser() if record else _paths.comms_db()).parent


def daemon_socket() -> Path:
    """The comms daemon's socket path."""
    return _paths.daemon_socket()


__all__ = [
    "INTERFACE", "send", "send_as", "send_under", "check", "delete", "DeleteResult", "Sender", "SendResult",
    "Recipient", "Refusal", "CODEX_RESUME", "ENDS_ON_BIND_TIMEOUT", "BIND_WAIT",
    "HEADLESS", "parse_exit", "OUTCOMES", "DELIVERIES", "EXIT_CODES", "POSTURES", "ClaimServer", "attributed",
    "session", "session_of", "caller_identity", "roster", "owner", "Roster", "Agent",
    "Standing", "Waiting", "Owner", "roster_rows", "RosterRow", "CacheFact",
    "identity_state", "IdentityState", "Holder", "IDENTITY_STATES", "record_dir",
    "daemon_socket", "GLOBAL", "qualify", "split_tag", "NAME_RE", "policy_directors",
    "effective", "POLICY_DIRECTORS_ENV",
    "DaemonError", "DaemonOffline", "BACKEND_ENV", "CLAIM_SOCKET_ENV", "COMMAND_ENV",
    "DIRECTORS_ENV", "GLOBAL_ENV", "MOCK_BACKEND_ENV", "NAME_ENV", "OPERATOR_ENV",
    "PROJECT_ENV", "SCOPE_ENV", "SECRET_PREFIX_ENV", "SHELL_REACH_ENV",
    "SHIM_SOCKET_ENV", "TOKEN_ENV", "TOKEN_STORE_ENV", "URL_ENV",
]
