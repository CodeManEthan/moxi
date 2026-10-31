"""The claim holder every adapter carries, its claim socket, and its ops.

Every adapter process is its session's claim holder: the Claude shim, moxi-comms-codex and
moxi-comms-opencode. It holds at most one CLAIM (a comms identity it may register). It writes
what it holds to its claim file (`claimfile`) before it answers anything, and serves a
local claim socket whose path it stamps into everything it spawns as
`MOXI_COMMS_CLAIM_SOCKET`.

**The token rule.** A claim file records which identity and state the adapter holds, but
never its posting token. While the claim is bound, the live token stays only in the
holder's memory and is handed only to a send inside that session. Every transition away
from bound clears it.

**Who may call each op.** What a process may send as is answered only inside the
session that holds it (`claim`); what the session holds is answered to any local caller
and can never be sent as (`state`). There is no other op.

`moxi comms whoami` is here too (`whoami`), with the read of a session whose adapter is gone
(`dead_adapter_answer`).
"""

from __future__ import annotations

import itertools
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from moxi import ipc

from . import claimfile, wire
from .claimfile import BOUND, HOLDS, OFFLINE, STANDBY, WAITING
from .client import DaemonClient, DaemonError, DaemonOffline, DaemonTimeout
from .membership import DEFAULT_PROC, SELF, TREE, ProcView, Root, root_of, sessions_of

log = logging.getLogger("comms.claim")


class ClaimUnreachable(OSError):
    """Nothing answers at a claim socket: its adapter is gone, or never served there."""


class AdapterMismatch(Exception):
    """The claim socket's adapter doesn't speak this comms' wire: a comms 0.2 adapter
    (its answer isn't JSON-RPC), or one whose wire version this comms doesn't share. Its
    text says so, and that relaunching the session is the cure."""


#: What a send inside a session launched before the upgrade says, whose adapter still
#: speaks comms 0.2's claim socket.
OLD_ADAPTER = ("this session's comms adapter is from comms 0.2 and speaks the old wire; "
               "relaunch the session. Nothing was sent.")

#: The one variable every adapter stamps into what it spawns.
CLAIM_SOCKET_ENV = "MOXI_COMMS_CLAIM_SOCKET"

#: The claim socket's methods, beside the hello: `claim` answers only inside the session,
#: `state` any local caller.
CLAIM_OPS = ("claim",)
STATE_OPS = ("state",)
OPS = CLAIM_OPS + STATE_OPS

#: The threads one claim socket's methods run on.
WORKERS = 4

#: The fields a current claim file may carry. Rebuilding at compatibility boundaries
#: drops fields written by older releases without teaching live code their names.
CLAIM_KEYS = ("name", "scope", "global", "qualified", "tier", "directors")

#: The refusal any caller outside the socket's own session gets for `claim`.
NOT_INSIDE = ("this process is not inside the session that holds this socket's identity")


# --------------------------------------------------------------------------- lines

def send_line(record: dict | None) -> str | None:
    """Why a process inside this session cannot send, or None when it holds a claim
    that names an identity (`claimfile.held_identity`). The same line `moxi comms whoami`
    prints. A claim with no name holds no comms identity, in every state."""
    if record is None:
        return "this session holds no comms identity"
    state = record.get("state")
    if claimfile.held_identity(record):
        return None
    if state == WAITING:
        claim = record.get("claim") or {}
        name = claim.get('qualified') or 'this name'
        why = record.get("waiting_why")
        if why == "":
            return f"{name} is not bound to comms yet"
        if isinstance(why, str):
            return f"comms refused {name}: {why}"
        return f"{name} is held by another session"
    if record.get("lost"):
        return f"{record['lost']} is held by another session"
    return "this session holds no comms identity"


def _current_claim(claim: dict) -> dict:
    """Copy only fields understood by this release."""
    return {key: claim[key] for key in CLAIM_KEYS if key in claim}


# --------------------------------------------------------------------------- holder

class ClaimHolder:
    """One adapter's claim and file, and the ops on them. The adapter reports what its
    binding is (`bound`, `went_offline`, `name_refused`, `note_session`); the holder
    writes it to the claim file and answers the socket from it."""

    def __init__(self, *, kind: str, key: str, socket_path, lock=None, roots=(),
                 keying: str = "session", proc=None, launch_session=None):
        self.kind = kind
        self.key = key
        self.socket_path = Path(socket_path)
        self.lock = lock or threading.RLock()
        self.proc: ProcView = proc or DEFAULT_PROC
        self.keying = keying
        self.roots: list = list(roots)
        # the record
        self.state = OFFLINE
        self.claim: dict | None = None
        self.hold = 0
        self.launch_session = launch_session
        #: The port's running harness session: its id; "" when the holder knows it
        #: runs none; None while it is not known (a port that has not noted one yet). A record with no key at
        #: all, or null, reads as not known.
        self.running_session: str | None = None
        self.lost: str | None = None
        self.ever_held = False
        #: Why a `waiting` claim waits, when it is not that another session holds its
        #: name (`send_line`): "" while it has been neither bound nor refused, else the
        #: daemon's words for a refusal that names no holder. None (null, or no key):
        #: another session holds the name. Only a `ClaimServer` sets it.
        self.waiting_why: str | None = None
        #: The live binding's posting credential. It is deliberately outside `claim`
        #: and `record()`, so neither the claim file nor a state answer can carry it.
        self.token: str | None = None
        #: One register in flight: the adapter's supervisor's.
        self.register_lock = threading.Lock()
        self._host: wire.Host | None = None
        self._stop = threading.Event()
        #: Closed with its file removed (`close()`): `save` writes nothing from then on,
        #: so a late event (a harness's reader thread running `went_offline` after its
        #: `stop()`) cannot put back the file. A shim's `close(remove_file=False)` keeps
        #: its file, and its writes.
        self._removed = False

    # ------------------------------------------------------------------ the record
    def record(self) -> dict:
        return {"kind": self.kind, "key": self.key, "keying": self.keying,
                "socket": str(self.socket_path), "state": self.state,
                "claim": self.claim, "hold": self.hold,
                "roots": [r.as_dict() for r in self.roots],
                "launch_session": self.launch_session,
                "running_session": self.running_session,
                "lost": self.lost,
                "ever_held": self.ever_held, "waiting_why": self.waiting_why}

    def save(self) -> None:
        with self.lock:
            if self._removed:
                return
            claimfile.write(self.key, self.record())

    def load(self, record: dict) -> None:
        """Come up in a recorded state (a respawned shim inside its session). A state
        this holder does not know (an old file's `passing`) comes up `standby`."""
        state = record.get("state")
        claim = record.get("claim")
        self.claim = _current_claim(claim) if isinstance(claim, dict) and claim else None
        self.hold = int(record.get("hold") or 0)
        self.running_session = record.get("running_session")
        self.lost = record.get("lost")
        self.ever_held = bool(record.get("ever_held"))
        self.token = None
        if record.get("launch_session"):
            self.launch_session = record["launch_session"]
        # A held claim comes back offline: its connection went with the old process.
        self.state = OFFLINE if state in HOLDS else (state if state in claimfile.STATES
                                                    else STANDBY)

    def start_launch(self, claim: dict) -> None:
        """A launch with no file of its own: `offline` with the launch claim."""
        with self.lock:
            self.claim = dict(claim)
            self.state = OFFLINE
            self.hold = 1
            self.save()

    # ------------------------------------------------------------------ adapter events
    @property
    def holds(self) -> bool:
        return self.state in HOLDS and self.claim is not None

    def may_register(self) -> bool:
        """Whether the adapter's supervisor may register now: an offline or waiting
        claim."""
        return self.claim is not None and self.state in (OFFLINE, WAITING)

    def register_claim(self) -> dict:
        """The claim for the adapter's next register."""
        with self.lock:
            return dict(self.claim or {})

    def bound(self, resp: dict) -> None:
        """A register of the claim was accepted."""
        with self.lock:
            if self.claim is None:
                return
            ident = resp.get("identity") or {}
            claim = dict(self.claim)
            claim.update(qualified=ident.get("qualified", claim.get("qualified")),
                         tier=ident.get("tier"),
                         name=ident.get("name", claim.get("name")),
                         scope=ident.get("scope", claim.get("scope")))
            self.claim = _current_claim(claim)
            self.state = BOUND
            self.token = resp.get("token")
            if self.lost == self.claim.get("qualified"):
                self.lost = None           # a name lost and bound again; another stays said
            self.ever_held = True
            self.save()

    def went_offline(self) -> None:
        with self.lock:
            self.token = None
            if self.state == BOUND:
                self.state = OFFLINE
                self.save()

    def name_refused(self, holder: str | None = None) -> None:
        """A register refused because a different connection holds the name. A claim
        this adapter has held leaves it (`standby` with `lost`); a launch claim never
        held keeps waiting."""
        with self.lock:
            self.token = None
            if self.claim is None:
                return
            if not self.ever_held:
                self.state = WAITING
                self.save()
                return
            self.lost = holder or self.claim.get("qualified")
            self.claim = None
            self.state = STANDBY
            self.save()

    def note_session(self, session_id: str | None) -> None:
        """The port's running harness session."""
        with self.lock:
            if session_id != self.running_session:
                self.running_session = session_id
                self.save()

    def hold_mark(self) -> tuple | None:
        """(qualified, hold) for an injection made now, or None with no claim."""
        with self.lock:
            if not self.holds:
                return None
            return self.claim.get("qualified"), self.hold

    def may_advance(self, mark) -> bool:
        """A cursor moves only for the name its digest was injected under, in the
        same hold, while the adapter still holds it."""
        with self.lock:
            return (mark is not None and self.holds
                    and (self.claim.get("qualified"), self.hold) == tuple(mark))

    # ------------------------------------------------------------------ reads
    def claim_answer(self) -> dict:
        with self.lock:
            claim = dict(self.claim) if self.claim else None
            answer = {"ok": True, "state": self.state, "claim": claim,
                      "hold": self.hold, "launch_session": self.launch_session,
                      "kind": self.kind, "key": self.key,
                      "line": send_line(self.record())}
            if self.state == BOUND and self.token is not None:
                answer["token"] = self.token
            return answer

    def state_answer(self) -> dict:
        with self.lock:
            return {"ok": True, "state": self.state, "kind": self.kind,
                    "key": self.key,
                    "claim": (self.claim or {}).get("qualified") if self.holds else None,
                    "claim_name": (self.claim or {}).get("qualified"),
                    "lost": self.lost}

    # ------------------------------------------------------------------ membership
    def caller_sessions(self, pid) -> list:
        if pid is None:
            return []
        records = claimfile.live_records(self.proc)
        # This holder's own file is live by construction while it serves.
        if not any(k == self.key for k, _r in records):
            records.append((self.key, self.record()))
        return sessions_of(pid, claimfile.membership_table(records), self.proc)

    # ------------------------------------------------------------------ the socket
    def listen(self) -> None:
        """Serve the claim socket, JSON-RPC 2.0 through `moxi.ipc`, on a loop of its own
        thread (harness adapters, the shim, ClaimServer). Raises
        `moxi.ipc.AlreadyServing` when another live holder serves the path."""
        host = wire.Host(str(self.socket_path), self._call, lambda conn: None,
                         capabilities=(), workers=WORKERS, name="comms-claim")
        host.start()
        host.open()
        self._host = host

    def close(self, *, remove_file: bool = True) -> None:
        # Set under the lock every write takes: one that took it first has written
        # before the file is removed, and one after it writes nothing.
        with self.lock:
            self._stop.set()
            self._removed = self._removed or remove_file
            self.token = None
        host, self._host = self._host, None
        if host is not None:
            host.close()
            # The lock file beside the socket is this process's alone (its path carries
            # the holder's key), so it goes with the socket.
            Path(ipc_lock_path(str(self.socket_path))).unlink(missing_ok=True)
        if remove_file:
            claimfile.remove(self.key)

    def _call(self, conn, method: str, params: object):
        return self.dispatch(method, conn.peer.pid)

    def dispatch(self, method: str, pid=None) -> dict:
        """One method from a caller whose pid is `pid` (None when it can't be read):
        its answer. `claim` answers only a caller inside this holder's session; any other
        caller gets `ok` false with `not_inside`."""
        if method not in OPS:
            raise ipc.RpcError(wire.METHOD_NOT_FOUND,
                               f"method not found: {method}; the claim socket answers "
                               + ", ".join(OPS))
        try:
            if method == "state":
                return self.state_answer()
            if self.key not in self.caller_sessions(pid):
                return {"ok": False, "error": NOT_INSIDE, "not_inside": True}
            return self.claim_answer()
        except Exception as e:                                   # noqa: BLE001
            log.exception("claim method %s failed", method)
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def ipc_lock_path(address: str) -> str:
    """The lock file `moxi.ipc.serve` holds beside a socket while it serves it."""
    return address + ".lock"


# --------------------------------------------------------------------------- client

def call(path, method: str, timeout: float = 15.0) -> dict:
    """Ask the claim socket at `path` `method`, after a hello: its answer.

    Raises `ClaimUnreachable` when nothing answers there, and `AdapterMismatch` when the
    adapter there doesn't speak this comms' wire (a comms 0.2 adapter, or another wire
    version)."""
    try:
        client = ipc.connect(str(path), timeout=timeout)
    except (ipc.IpcError, OSError) as e:
        raise ClaimUnreachable(f"no claim socket answers at {path}: {e}") from None
    try:
        client.hello(wire.VERSIONS)
        answer = client.call(method, {}, timeout=timeout)
    except ipc.NotRpc:
        raise AdapterMismatch(OLD_ADAPTER) from None
    except ipc.RpcError as e:
        if e.code == wire.VERSION_REFUSED:
            raise AdapterMismatch(_version_text(e.data)) from None
        raise ClaimUnreachable(f"the claim socket at {path} refused {method}: "
                               f"{e.message}") from None
    except (ipc.IpcError, OSError) as e:
        raise ClaimUnreachable(f"the claim socket at {path} did not answer: {e}") from None
    finally:
        client.close()
    return answer if isinstance(answer, dict) else {}


def _version_text(data) -> str:
    """The claim socket's version refusal, in the words a send says."""
    theirs = data.get("server") if isinstance(data, dict) else None
    ours = f"{wire.VERSIONS[0]}-{wire.VERSIONS[1]}"
    if isinstance(theirs, list) and len(theirs) == 2:
        return (f"this session's comms adapter speaks comms wire {theirs[0]}-{theirs[1]} "
                f"and this comms speaks {ours}; relaunch the session. Nothing was sent.")
    return (f"this session's comms adapter refused this comms' wire {ours}; relaunch the "
            "session. Nothing was sent.")


def socket_from_env(env=None) -> Path | None:
    """The claim socket `env` (else this process) names, or None when it names none.

    A path that is not one of this comms' claim sockets raises `paths.OutsideRuntime`
    (`checked_socket`), naming the variable and the directory: a socket stamped by
    another copy of comms, or by hand, is never asked who this process is. The
    directory is this process's own; a stamp cannot move it.
    """
    value = (env if env is not None else os.environ).get(CLAIM_SOCKET_ENV)
    return checked_socket(value) if value else None


def checked_socket(path, env=None) -> Path:
    """`path` when it is one of this comms' claim sockets, else raise
    `paths.OutsideRuntime` naming `MOXI_COMMS_CLAIM_SOCKET` and the directory.

    One of comms' own is a path inside RUNTIME/comms (`env`'s, else this process's), or
    a short path the core gave a holder whose path there was too long
    (`paths.claim_socket`): in the core's short socket directory, and recorded as its
    socket by the holder's claim file in RUNTIME/comms. A short path no claim file of
    this comms records is another program's, and is refused.
    """
    from .paths import OutsideRuntime, refuse_outside_runtime, runtime_dir, short_socket_dir

    try:
        return refuse_outside_runtime(path, CLAIM_SOCKET_ENV, env)
    except OutsideRuntime:
        short = short_socket_dir()
        if (short is not None
                and os.path.realpath(Path(path).parent) == os.path.realpath(short)
                and record_at(path, env) is not None):
            return Path(path)
        root = runtime_dir(env)
        raise OutsideRuntime(
            f"{CLAIM_SOCKET_ENV} names {path}, which is outside comms' runtime directory "
            f"{root} and is no claim socket of this comms; comms uses only its own "
            f"sockets. Start the session from `moxi comms` or unset "
            f"{CLAIM_SOCKET_ENV}.") from None


def record_at(path, env=None) -> tuple | None:
    """(key, record) of the claim file in RUNTIME/comms that records `path` as its
    socket, or None when none does."""
    want = str(path)
    for key, record in claimfile.all_records(env):
        if record.get("socket") == want:
            return key, record
    return None


def remove_socket(path) -> None:
    """Remove the claim socket at `path` and its lock file once no holder serves it:
    what a holder that ended without closing left behind. A live holder's are left."""
    try:
        verdict = ipc.sweep(str(path))
    except NotImplementedError:
        return
    if verdict != "live":
        Path(ipc_lock_path(str(path))).unlink(missing_ok=True)


# --------------------------------------------------------------------------- whoami

def whoami(env=None, say=print, proc=None) -> int:
    """`moxi comms whoami`: what this session holds, `<qualified> (<state>)`,
    or the line a send would refuse with; then `session <id>` when the claim records
    its launch session. `OK` when it holds an identity; `REFUSED` when it holds none, the
    code a send from the same process exits with; `ERROR` for an adapter that speaks
    another comms wire, as for a send."""
    from moxi import exit as moxi_exit

    from .paths import OutsideRuntime

    env = env if env is not None else os.environ
    try:
        path = socket_from_env(env)
    except OutsideRuntime as e:
        say(f"{e} Nothing was asked.")
        return moxi_exit.REFUSED
    if not path:
        say("this process has no claim socket: it is inside no comms session and holds "
            "no comms identity")
        return moxi_exit.REFUSED
    try:
        answer = call(path, "claim")
    except AdapterMismatch as e:
        say(str(e).replace(" Nothing was sent.", ""))
        return moxi_exit.ERROR
    except ClaimUnreachable:
        answer = dead_adapter_answer(path, proc=proc)
        if answer is None:
            say("this session's comms adapter is gone and it holds no comms identity")
            return moxi_exit.REFUSED
    if not answer.get("ok"):
        say(answer.get("error"))
        return moxi_exit.REFUSED
    claim = answer.get("claim") or {}
    line = answer.get("line")
    if line is None:
        say(f"{claim.get('qualified')} ({answer.get('state')})")
    else:
        say(line)
    if answer.get("launch_session"):
        say(f"session {answer['launch_session']}")
    return moxi_exit.OK if line is None else moxi_exit.REFUSED


def dead_adapter_answer(path, proc=None, pid=None) -> dict | None:
    """The claim file of a session whose adapter is gone, read for a caller inside that
    session: an answer shaped like `claim`'s, or None when there is no live file whose
    session the caller is inside."""
    proc = proc or DEFAULT_PROC
    pid = os.getpid() if pid is None else pid
    records = claimfile.all_records()
    mine = [(k, r) for k, r in records if r.get("socket") == str(path)]
    if not mine:
        return None
    key, record = mine[0]
    if not claimfile.tree_root_alive(record, proc):
        return None
    live = [(k, r) for k, r in records if claimfile.is_live(r, proc)]
    if key not in sessions_of(pid, claimfile.membership_table(live), proc):
        return None
    state = record.get("state")
    claim = record.get("claim") if claimfile.held_identity(record) else None
    return {"ok": True, "state": OFFLINE if claim else state, "claim": claim,
            "launch_session": record.get("launch_session"), "kind": record.get("kind"),
            "key": key, "line": send_line(record) if not claim else None,
            "dead_adapter": True}


# --------------------------------------------------------------------------- ClaimServer

_GATE = ("import os,sys\n"
         "fd=int(sys.argv[1]); os.read(fd,1); os.close(fd)\n"
         "os.execvp(sys.argv[2], sys.argv[2:])\n")


#: Numbers each `ClaimServer`'s socket in this process.
_SERVER_SOCKETS = itertools.count(1)


class ClaimServer:
    """A claim socket for a binding held by a module's own process (its own loop). Its
    roots are the holder, reach `self`, and any child roots the
    holder adds for this binding. It answers `claim` inside its session and `state` to
    any local caller.

    The holder reports what its binding is (`set_bound`, `set_offline`, `set_waiting`),
    or gives the server its own daemon arm (`arm=True`): it registers at `start`, goes
    `offline` and registers again across a daemon restart, holds nothing after a
    refusal (logged once), and closes the binding at `stop`.
    """

    def __init__(self, name: str, scope: str, *, kind: str,
                 directors=(), socket_path=None, arm: bool = False, daemon_socket=None,
                 proc=None, global_: bool = False, backoff=None):
        from .paths import claim_socket
        from .scoping import GLOBAL, qualify

        self.proc = proc or DEFAULT_PROC
        me = os.getpid()
        start = self.proc.start_time(me) or "0"
        global_ = global_ or scope == GLOBAL
        self.claim = {"name": name, "scope": GLOBAL if global_ else scope,
                      "global": global_, "qualified": qualify(name, GLOBAL if global_
                                                              else scope),
                      "tier": None, "directors": list(directors)}
        key = f"srv-{me}-{start}-{self.claim['qualified']}"
        # Numbered per server: two servers for one name in one process (one waiting on
        # the other) each serve a socket of their own.
        path = socket_path or claim_socket(f"{key}-{next(_SERVER_SOCKETS)}")
        self.holder = ClaimHolder(kind=kind, key=key, socket_path=path, proc=self.proc,
                                  keying="server", roots=[Root(me, start, SELF)])
        self.holder.claim = dict(self.claim)
        self.holder.state = WAITING
        self.holder.waiting_why = ""            # not bound yet, and nothing refused it
        self.arm = arm
        self.daemon_socket = daemon_socket
        self.kind = kind
        self._client: DaemonClient | None = None
        self._stop = threading.Event()
        self._backoff = backoff
        self.refusal: str | None = None
        self._children: dict = {}

    @property
    def socket_path(self) -> Path:
        return self.holder.socket_path

    def env(self) -> dict:
        return {CLAIM_SOCKET_ENV: str(self.socket_path)}

    # holder-reported state. Each is a no-op once `stop()` has run: the file went with
    # the socket, and a late report would write it back as a dead `srv-` file.
    def set_bound(self, tier=None, *, token) -> None:
        with self.holder.lock:
            if self._stop.is_set():
                return
            self.holder.claim.update(tier=tier)
            self.holder.state = BOUND
            self.holder.token = token
            self.holder.ever_held = True
            self.holder.save()

    def set_offline(self) -> None:
        with self.holder.lock:
            if self._stop.is_set():
                return
            self.holder.token = None
            self.holder.state = OFFLINE if self.holder.ever_held else WAITING
            self.holder.save()

    def set_waiting(self, refusal: str | None = None) -> None:
        """The claim holds nothing. `refusal`: the daemon's words when it refused the
        register for a reason other than another session holding the name, which the
        send verb then says; without them it says the name is held elsewhere."""
        with self.holder.lock:
            if self._stop.is_set():
                return
            self.holder.token = None
            self.holder.state = WAITING
            self.holder.waiting_why = refusal
            self.holder.save()

    # roots. After `stop()` neither writes, as the reports above: a `spawn` racing a
    # service's stop gets no root, so its child meets no socket and its send fails.
    def add_root(self, pid: int, reach: str = TREE) -> Root | None:
        root = root_of(pid, reach, self.proc)
        if root is None:
            return None
        with self.holder.lock:
            if self._stop.is_set():
                return None
            self.holder.roots.append(root)
            self.holder.save()
        return root

    def drop_root(self, pid: int) -> None:
        with self.holder.lock:
            if self._stop.is_set():
                return
            self.holder.roots = [r for r in self.holder.roots
                                 if not (r.pid == pid and r.reach == TREE)]
            self.holder.save()

    def spawn(self, argv, *, env=None, **popen) -> subprocess.Popen:
        """Start a child that is a `tree` root of this binding, written to the claim
        file before the child's program runs, and dropped at its exit. The child's
        environment carries this binding's claim socket."""
        child_env = dict(env if env is not None else os.environ)
        child_env[CLAIM_SOCKET_ENV] = str(self.socket_path)
        r, w = os.pipe()
        try:
            proc = subprocess.Popen([sys.executable, "-c", _GATE, str(r), *argv],
                                    env=child_env, pass_fds=(r,), **popen)
        finally:
            os.close(r)
        try:
            self.add_root(proc.pid, TREE)
        finally:
            os.write(w, b"x")
            os.close(w)

        def reap():
            proc.wait()
            self.drop_root(proc.pid)
        threading.Thread(target=reap, daemon=True).start()
        return proc

    # lifecycle
    def start(self) -> "ClaimServer":
        self.holder.save()
        self.holder.listen()
        if self.arm:
            self._register_once()
            threading.Thread(target=self._supervise, daemon=True).start()
        return self

    def stop(self) -> None:
        # Set under the holder's lock: a report that takes the lock after this sees the
        # stop, and one that took it first has written before the file is removed.
        with self.holder.lock:
            self._stop.set()
            self.holder.token = None
            client = self._client
        if client is not None:
            client.close()
        self.holder.close()

    def _register_once(self) -> bool:
        c = self.holder.claim
        client = DaemonClient(self.daemon_socket, on_close=self._on_close)
        try:
            client.connect()
            resp = self._register(client, c)
        except (DaemonOffline, DaemonTimeout):
            client.close()
            self.set_offline()
            return False
        except DaemonError as e:
            client.close()
            if self.refusal != str(e):
                log.warning("the daemon refused %s: %s", c["qualified"], e)
            self.refusal = str(e)
            held = (e.data.get("holder") or {}).get("qualified") == c["qualified"]
            self.set_waiting(None if held else self.refusal)
            return False
        # The arm reads `bound` only while the connection its register was answered on
        # is open, and a drop at any point after the answer sends it back to register.
        # The reader thread marks the client closed BEFORE it runs `_on_close`, and
        # `_on_close` takes this lock, so the check and `set_bound` under it make the
        # ordering total: either the close landed first and this declines the bind, or
        # `_on_close` runs after it and reads the binding offline. Unchecked, a close
        # between the answer and `set_bound` was overwritten with `bound` and the
        # supervisor slept on a binding the daemon no longer held (the Claude shim's
        # `_connect_once` guards it the same way).
        with self.holder.lock:
            if self._stop.is_set():
                # A bind must not outlive `stop()`, as the Claude shim declines it: the
                # daemon would hold it until the process exits.
                client.close()
                return False
            if not client.connected:
                client.close()
                self.set_offline()
                return False
            self._client = client
            self.refusal = None
            self.set_bound((resp.get("identity") or {}).get("tier"),
                           token=resp["token"])
        return True

    def _register(self, client, c) -> dict:
        return client.bind(name=c["name"], scope=c["scope"],
                           global_=c.get("global", False), project=c["scope"],
                           adapter=self.kind)

    def _on_close(self) -> None:
        if not self._stop.is_set():
            self.set_offline()

    def _supervise(self) -> None:
        from .client import backoff_delays
        delays = self._backoff or backoff_delays()
        while not self._stop.is_set():
            if self.holder.state in HOLDS and self.holder.state == BOUND:
                self._stop.wait(0.2)
                continue
            if self._register_once():
                delays = self._backoff or backoff_delays()
                continue
            self._stop.wait(next(delays))


__all__ = ["AdapterMismatch", "CLAIM_OPS", "CLAIM_SOCKET_ENV", "ClaimUnreachable",
           "STAMP_VARS", "claim_overlay", "ClaimHolder", "ClaimServer", "NOT_INSIDE",
           "OLD_ADAPTER", "OPS", "STATE_OPS", "call", "checked_socket",
           "dead_adapter_answer", "record_at", "remove_socket", "send_line",
           "socket_from_env", "whoami"]


STAMP_VARS = ("MOXI_COMMS_NAME", "MOXI_COMMS_SCOPE", "MOXI_COMMS_GLOBAL")


def claim_overlay(env) -> dict:
    """`env` as a reader of the launch stamp should see it: with a claim socket, the
    stamp's identity variables are replaced by what this session's claim holds now (or
    removed when it holds nothing, or the caller is not inside it), so `whois`,
    `explain`, `cache` and the roster hook read the claim, never a stale stamp. With no
    socket, `env` unchanged. A socket outside comms' runtime directory is asked
    nothing, and the stamp is removed: this session holds nothing comms can read."""
    from .paths import OutsideRuntime

    if not env.get(CLAIM_SOCKET_ENV):
        return env
    out = {k: v for k, v in env.items() if k not in STAMP_VARS}
    try:
        path = socket_from_env(env)
    except OutsideRuntime as e:
        log.warning("%s", e)
        return out
    try:
        answer = call(path, "claim", timeout=5.0)
    except AdapterMismatch:
        return out
    except ClaimUnreachable:
        answer = dead_adapter_answer(path)
    if not answer or not answer.get("ok") or answer.get("line") is not None:
        return out
    claim = answer.get("claim") or {}
    if not claim.get("name"):
        return out
    out["MOXI_COMMS_NAME"] = claim["name"]
    if claim.get("global"):
        out["MOXI_COMMS_GLOBAL"] = "1"
    out["MOXI_COMMS_SCOPE"] = claim.get("scope") or ""
    return out
