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
  reserved for `module`, from that module's own code, outside every session.
* `Sender`: who sends. `Sender.session()` (this process's stamps and its session's claim
  socket), `Sender.socket(path)`, `Sender.claim(server)` (a `ClaimServer` this process
  holds) or `Sender.operator(user_id)` (the operator's lane).
* `SendResult`: `outcome` (one of `OUTCOMES`), `exit_code` (the verb's number for it),
  `message_id`, `recipients` (one `Recipient` per resolved name, its `delivery` one of
  `DELIVERIES`), `refusal` (a `Refusal`, `code` and `text`, when nothing was posted),
  `notes`, and `lines()`, the verb's text. Branch on the data, never on the text.

**The other surfaces.**

* `ClaimServer`, for a binding held by a module's own process.
* Reads: `attributed(message_id)`, `session(identity)`, `session_of(identity)`,
  `roster()`, `owner(session_id)`, `identity_state(name, scope=None)`,
  `caller_identity(env=None)`, `record_dir()`, `daemon_socket()`.
* Names: `GLOBAL`, `qualify`, `split_tag`, `NAME_RE`.
* `replay()`: the reader catch-up bound in force, `(messages, seconds)`.
* `DaemonError`, `DaemonOffline`: what a read raises when the daemon refuses or is down.
* `moxi_comms.api.tokens`: the token store the box's own consumers read.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from .. import claimfile as _claimfile
from .. import config as _config
from .. import directory as _directory
from .. import machine_line as _machine_line
from .. import paths as _paths
from .. import reserved as _reserved_mod
from .. import sending as _sending
from .. import wire as _wire
from ..claimhold import ClaimServer
from ..client import DaemonError, DaemonOffline
from ..scoping import GLOBAL, NAME_RE, is_auto_name, qualify, split_tag
from ..sending import (DELIVERIES, EXIT_CODES, OUTCOMES, DeleteResult, Recipient, Refusal,
                       Sender, SendResult)

#: The interface this module is.
INTERFACE = 2

#: A read's default wait for the daemon, in seconds.
READ_DEADLINE = 10.0

#: What `posture` may be: a new send states one; a reply may leave it None.
POSTURES = (_machine_line.POSTURE_NONE, _machine_line.POSTURE_OPTIONAL,
            _machine_line.POSTURE_ONCE, _machine_line.POSTURE_OPEN)


# --------------------------------------------------------------------------- sending

def send(sender: Sender, to, text: str, *, posture, reply_to=None, new: bool = False,
         audience=None, held: bool = False, cap=None, no_cap: bool = False,
         deadline: float = 30.0, standing: bool = False,
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
    whole number of seconds, or None for the config's default. `deadline` is seconds,
    passed to every daemon call, to the claim socket and to the record's busy timeout.

    The result says whether it posted (`message_id`), and a post either commits or does
    not. Raises only on arguments the verb's parser would refuse (`ValueError`,
    `TypeError`)."""
    return _run(sender, to, text, posture=posture, reply_to=reply_to, new=new,
                audience=audience, held=held, cap=cap, no_cap=no_cap, deadline=deadline,
                standing=standing, new_name=new_name, interrupt=interrupt,
                expires=expires, check=False)


def check(sender: Sender, to, text: str, *, posture, reply_to=None, new: bool = False,
          audience=None, held: bool = False, cap=None, no_cap: bool = False,
          deadline: float = 30.0, standing: bool = False,
          new_name: bool = False, interrupt: bool = False,
          expires=None) -> SendResult:
    """`send`'s every check, stopping before the post: outcome `would-send`, with the
    recipients and notes the post would have, or the refusal the send would give.
    Posts, nudges and records nothing."""
    return _run(sender, to, text, posture=posture, reply_to=reply_to, new=new,
                audience=audience, held=held, cap=cap, no_cap=no_cap, deadline=deadline,
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
    ctx = _sending.Context(env=sender.environ(os.environ), deadline=deadline)
    return _sending.run(ctx, args, tags=tags)


def send_as(module: str, name: str, to, text: str, *, expires=None,
            interrupt: bool = False, deadline: float = 30.0) -> SendResult:
    """Send `text` to `to` under `name`, a send-only name reserved for `module`, as that
    module's own code: its token is read through this process's credential chain and
    presented to the daemon, which refuses a sender inside a live session. The posture is
    always `[no reply]`, since nobody can mail a reserved name. A refusal is the result's
    `outcome` and `refusal` (`credential` when the token can't be read: run `moxi
    doctor`), as for `send`; it raises only for arguments `send` would refuse."""
    if expires is not None:
        _sending.expiry_seconds(expires)          # a bad duration raises ValueError
    names = to.split(",") if isinstance(to, str) else [str(t) for t in to]
    tags = [t.strip() for t in names if t.strip()]
    if not tags:
        raise ValueError("send_as needs `to` to name at least one recipient")
    args = SimpleNamespace(text=str(text), posture=_machine_line.POSTURE_NONE, cap=None,
                           no_cap=False, audience=None, new=False, held=False,
                           standing=False, new_name=False, interrupt=bool(interrupt),
                           expires=expires, check=False, reply_to=None, message_id=None)
    ctx = _sending.Context(env=dict(os.environ), deadline=deadline)
    return _sending.run_reserved(ctx, args, module=str(module), name=str(name), tags=tags)


def delete(sender: Sender, message_id, *, deadline: float = 30.0) -> DeleteResult:
    """Withdraw `message_id`, which `sender` sent, from every recipient that hasn't had
    it in a digest (`moxi comms delete`). The `DeleteResult` names who it was withdrawn
    from, who had it already and whose copy expired first."""
    if not isinstance(sender, Sender):
        raise TypeError(f"sender must be an api.Sender, not {type(sender).__name__}")
    ctx = _sending.Context(env=sender.environ(os.environ), deadline=deadline)
    return _sending.delete(ctx, message_id)


# --------------------------------------------------------------------------- reads

def _daemon(deadline: float):
    """A connection to the daemon once it has answered with this comms' protocol."""
    return _sending.Context(deadline=deadline).daemon()


def attributed(message_id, *, deadline: float = READ_DEADLINE) -> dict | None:
    """One message with its author as the daemon attributes it, or None when the record
    holds no such id: `message_id`, `room`, `author` (`name`, `tier`, `attribution`),
    `recipients`, `resolved`, `text`, `timestamp`. Raises `DaemonOffline` or
    `DaemonError`."""
    client = _daemon(deadline)
    try:
        return client.attributed_message(message_id)
    finally:
        client.close()


def session(identity: str, *, deadline: float = READ_DEADLINE) -> dict:
    """Which live session sits behind one bound name: the daemon's `session` answer
    (`bound`, `adapter`, `session_id`, `bound_at`, the cache read, ...). An unbound name
    answers `bound` False. Raises `DaemonOffline` or `DaemonError`."""
    client = _daemon(deadline)
    try:
        answer = client.call("session", {"identity": str(identity)})
    finally:
        client.close()
    return {k: v for k, v in answer.items() if k not in ("id", "ok")}


def session_of(identity: str, *, deadline: float = READ_DEADLINE) -> str | None:
    """The live session id behind the bound name `identity`, as its adapter reports it;
    None when the name isn't bound (a reserved name never is) or its binding has
    reported no session id yet. Raises what `session` raises."""
    answer = session(identity, deadline=deadline)
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
    """A bound name: its binding as the daemon holds it, and the directors its session
    declared at launch."""

    qualified: str
    name: str
    scope: str
    tier: str | None
    adapter: str | None
    directors: tuple = ()


@dataclass(frozen=True)
class Standing:
    """A standing identity: a directory row (`kind` `agent`), or a reserved name (`kind`
    `send-only`: active, or kept from an older comms). `module` names the module the name
    is reserved for, or declared by when it is in conflict; None for a name no module
    owns."""

    qualified: str
    name: str
    scope: str
    kind: str
    module: str | None = None


@dataclass(frozen=True)
class Roster:
    """Who can be messaged: the bound names, and the standing rows."""

    bound: tuple = ()
    standing: tuple = ()


@dataclass(frozen=True)
class Owner:
    """Which identity owns one session id now (`identity` None when no live binding
    does), its adapter kind, the directors it declared, and whom it awaits."""

    session_id: str
    identity: str | None
    adapter: str | None = None
    directors: tuple = ()
    awaiting: tuple = ()


def _declared_directors() -> dict:
    """qualified -> the directors its live claim declares."""
    out = {}
    for _key, record in _claimfile.live_records():
        qualified = _claimfile.held_identity(record)
        if qualified:
            claim = record.get("claim") or {}
            out.setdefault(qualified, tuple(claim.get("directors") or ()))
    return out


def roster(*, deadline: float = READ_DEADLINE) -> Roster:
    """The bound agents with their declared directors, and the standing names: the
    directory's standing rows and the reserved send-only names, as the daemon's `agents`
    answer lists them. Raises `DaemonOffline` or `DaemonError`."""
    client = _daemon(deadline)
    try:
        answer = client.call("agents")
    finally:
        client.close()
    directors = _declared_directors()
    bound = tuple(Agent(qualified=b["qualified"], name=b.get("name"), scope=b.get("scope"),
                        tier=b.get("tier"), adapter=b.get("adapter"),
                        directors=directors.get(b["qualified"], ()))
                  for b in answer.get("bindings") or ())
    standing = tuple(Standing(qualified=s["qualified"], name=s.get("name"),
                              scope=s.get("scope"), kind=s.get("kind"),
                              module=s.get("module"))
                     for s in answer.get("standing") or ())
    return Roster(bound=bound, standing=standing)


#: The kind a reserved name reads as in `roster().standing`.
SEND_ONLY = _reserved_mod.SEND_ONLY


def _reserved_rows(conn) -> dict:
    """qualified -> (the module each reserved name is reserved for, or None when no
    module owns it; its state), for every row not freed."""
    return {row["name"]: (row["module"], row["state"]) for row in conn.execute(
        "SELECT name, module, state FROM reserved WHERE freed_at IS NULL")}


def _reserved(qualified: str) -> tuple[bool, str | None]:
    """Whether `qualified` is a reserved name (a `reserved` row not freed: active, in
    conflict or unowned, each of which the daemon's bind refuses), and its module. A
    store that can't be read holds none."""
    import sqlite3

    from ..store.open import Unmoved

    try:
        conn = _directory.connect(_paths.comms_db(), read_only=True)
    except (sqlite3.Error, Unmoved, OSError):
        return False, None
    try:
        rows = _reserved_rows(conn)
    except sqlite3.Error:
        return False, None
    finally:
        conn.close()
    return (qualified in rows), rows.get(qualified, (None,))[0]


def owner(session_id: str, *, deadline: float = READ_DEADLINE) -> Owner:
    """Which identity owns `session_id` now, with its declared directors and whom it
    awaits. Raises `DaemonOffline`, or `DaemonError` (an empty id among them)."""
    client = _daemon(deadline)
    try:
        answer = client.call("owner", {"session_id": str(session_id)})
    finally:
        client.close()
    identity = answer.get("owner")
    return Owner(session_id=answer.get("session_id", session_id), identity=identity,
                 adapter=answer.get("adapter"),
                 directors=_declared_directors().get(identity, ()) if identity else (),
                 awaiting=tuple(answer.get("awaiting") or ()))


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
    `reserved` for a module (`module`, None for a name no module owns); `retired`; or
    `offline`, when the daemon can't be asked (`reason` says why). Branch on `state`,
    never on text."""

    qualified: str
    state: str
    holder: Holder | None = None
    module: str | None = None
    reason: str | None = None


def identity_state(name: str, scope: str | None = None, *,
                   deadline: float = READ_DEADLINE) -> IdentityState:
    """Whether `name` (in `scope`, else the global identity) is free for a launch to
    claim, from the daemon's `resolve` and `session` answers and the store's reserved
    names. `name` may be qualified (`name@scope`) when `scope` is None. Raises
    `ValueError` for what is not a name a launch can claim, and `DaemonError` for an
    answer it can't read as a state."""
    name, scope = _launch_name(name, scope)
    qualified = qualify(name, scope)
    try:
        client = _daemon(deadline)
    except DaemonOffline as e:
        return IdentityState(qualified, "offline", reason=str(e))
    try:
        try:
            answer = client.call("resolve", {"name": qualified})
        except DaemonError as e:
            if e.code == _wire.NAME_RETIRED:
                return IdentityState(qualified, "retired", reason=str(e))
            raise
        if answer.get("bound"):
            held = client.call("session", {"identity": qualified})
            return IdentityState(qualified, "held", holder=Holder(
                adapter=held.get("adapter"), session_id=held.get("session_id")))
    except DaemonOffline as e:
        return IdentityState(qualified, "offline", reason=str(e))
    finally:
        client.close()
    reserved, module = _reserved(qualified)
    if reserved:
        return IdentityState(qualified, "reserved", module=module)
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


def replay() -> tuple[int, int]:
    """The reader catch-up bound in force: `(messages, seconds)`, from the config file's
    `[replay] messages` and `hours`."""
    bound = _config.load().replay
    return bound.messages, bound.hours * 3600


__all__ = [
    "INTERFACE", "send", "send_as", "check", "delete", "DeleteResult", "Sender", "SendResult",
    "Recipient", "Refusal",
    "OUTCOMES", "DELIVERIES", "EXIT_CODES", "POSTURES", "ClaimServer", "attributed",
    "session", "session_of", "caller_identity", "roster", "owner", "Roster", "Agent",
    "Standing", "Owner",
    "identity_state", "IdentityState", "Holder", "IDENTITY_STATES", "record_dir",
    "daemon_socket", "GLOBAL", "qualify", "split_tag", "NAME_RE", "replay",
    "DaemonError", "DaemonOffline",
]
