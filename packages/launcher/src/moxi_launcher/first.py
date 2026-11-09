"""The first turn: the brief or handoff a launch sends to its session once it is ready.

A launch with a brief, a handoff or both owes its session one first turn. When turns can
reach the session (Claude in `interactive` mode, Codex under turns' host), the sender waits
for turns to say the session is ready and hands turns the text as a plain delivery. In
every other case the text went on the harness's command line as its opening prompt, and
nothing is sent here.

Who sends: a slot launch calls `send` itself, then returns. A launch in this terminal is
about to become the harness, so before it does it starts the detached deliverer
(`start_deliverer`), the process `moxi launcher deliver REQUEST_ID --pid PID` that runs
`deliver`, writes the outcome to the launch log and exits.

**The sender waits on the session, not on the row.** Readiness is turns' own answer, so it
does not depend on when the ready recorder's write lands.

**The first turn is sent at most once.** Before it hands turns the text the sender claims
the send by moving the row to `sending`; a sender whose claim matches no row (a second
deliverer for the launch, or a row that already failed) sends nothing. The text goes with a
`ref` unique to the launch, and the sender then only waits on that one delivery: it never
sends the text again, whatever comes back.

What comes back decides the row:

- turns says the delivery started: `first-sent`.
- turns says it was refused: `failed`, with turns' reason. turns reports a refusal only
  when it received one.
- turns says it was lost, or gives no answer in time, or the call fails some other way:
  the brief may or may not have gone in. The row is `first-sent` with reason
  `unconfirmed`, never `failed`, and the caller is told the brief is unconfirmed. No
  missing answer reads as a refusal.
- the session is not ready in time: `failed`, `no-ready`, and nothing was sent.

A sender that dies after its claim leaves the row `sending`, which `log` shows as stale
and nothing retries: a brief may be lost, and the row shows it, but it is never sent twice.
"""

from __future__ import annotations

import getpass
import importlib
import logging
import os
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from moxi import exit as exits
from moxi import modules
from moxi import paths
from moxi import proc
from moxi import store as core_store
from moxi.paths import Layout

from . import store

log = logging.getLogger("moxi.launcher.first")

MODULE = "launcher"
#: The turns interface the sender speaks: `wait_ready`, `Session.deliver(text, ref=)` and
#: the `Delivery` it returns.
TURNS_INTERFACE = 2
#: A test seam: the import name of a module that stands in for turns' API, so a test (and a
#: detached deliverer a test starts) reaches a fake turns without one installed.
TURNS_SEAM = "MOXI_LAUNCHER_TURNS"
#: comms' identity stamp; the actor named in the header when the caller has one.
COMMS_NAME = "MOXI_COMMS_NAME"

#: The first line of a first turn that carries a brief only.
BRIEF_HEADER = "Brief from launcher, launched by {actor}"
#: The first line of a first turn that carries a handoff, with or without a brief.
HANDOFF_HEADER = "Handoff from launcher, launched by {actor}: {path}"
#: The first line of a self relaunch's first turn, which carries its handoff alone.
RELAUNCH_HEADER = "Handoff from launcher, relaunched by {actor}: {path}"
_HEADER_STARTS = ("Brief from launcher, launched by ", "Handoff from launcher, launched by ",
                  "Handoff from launcher, relaunched by ")

#: The reason written with `first-sent` when turns could not say whether the text went in.
UNCONFIRMED = "unconfirmed"
#: The reason written with `failed` when the session was not ready in time.
NO_READY = "no-ready"
#: The reason written with `failed` when the handoff could not be read at send time.
HANDOFF_UNREADABLE = "handoff-unreadable"
#: The reason written with `failed` when turns cannot be loaded at send time.
NO_TURNS = "no-turns"
#: The `events` kind of the first turn's outcome.
EVENT = "first-turn"

#: The states a sender may start from: those it may still claim the send from.
_CLAIMABLE = store.LEAVES["sending"]


class HandoffUnreadable(Exception):
    """The handoff file named for a launch can't be read; the launch is refused."""

    exit_code = exits.REFUSED

    def __init__(self, path: str, why: str):
        self.path = path
        self.why = why
        super().__init__(f"the handoff {path} can't be read: {why}")


# -- the text ---------------------------------------------------------------------------


def actor(env: Mapping[str, str] | None = None) -> str:
    """Who launched, as the header names them: the caller's comms identity when its
    environment holds comms' stamp, else the OS user name."""
    environ = os.environ if env is None else env
    name = environ.get(COMMS_NAME, "").strip()
    if name:
        return name
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001  (no user name known to the OS)
        return "unknown"


def text(*, actor: str, brief: str | None = None, handoff: str | os.PathLike | None = None,
         handoff_text: str | None = None, relaunched: bool = False) -> str | None:
    """The first turn a launch sends, or None when it has neither brief nor handoff.

    It starts with one header line naming the launcher and `actor`. With a handoff the
    header names its path and the handoff's text follows; a brief comes after the handoff
    when there are both. `relaunched` marks a relaunch's handoff, whose header says the
    session was relaunched by `actor`. `handoff_text` is the handoff's text when the
    caller has read it already; otherwise the file is read here, any readable file, and
    one that can't be read raises `HandoffUnreadable`.
    """
    brief = brief if brief and brief.strip() else None
    if handoff is None and brief is None:
        return None
    if handoff is None:
        return BRIEF_HEADER.format(actor=actor) + "\n\n" + brief
    path = os.fspath(handoff)
    if handoff_text is None:
        handoff_text = read_handoff(path)
    header = RELAUNCH_HEADER if relaunched else HANDOFF_HEADER
    parts = [header.format(actor=actor, path=path), handoff_text.strip("\n")]
    if brief is not None:
        parts.append(brief)
    return "\n\n".join(parts)


def read_handoff(path: str | os.PathLike) -> str:
    """The text of the handoff at `path`; `HandoffUnreadable` when it can't be read."""
    try:
        return Path(path).expanduser().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise HandoffUnreadable(os.fspath(path), _why(error)) from error


def on_argv(argv: Sequence[str]) -> bool:
    """Whether a launch's argv carries its first turn as the opening prompt. Such a launch
    has had its first turn, and nothing more is sent."""
    return any(isinstance(word, str) and word.startswith(_HEADER_STARTS) for word in argv)


def ref(request_id: str) -> str:
    """The `ref` the first turn of launch `request_id` goes with, unique to the launch."""
    return f"launcher:{request_id}"


# -- turns --------------------------------------------------------------------------------


def turns_api(*, quiet: bool = False) -> Any | None:
    """turns' API, or None when turns is not installed or does not speak the interface the
    sender needs. The composer asks the same question, so composing and sending agree on
    whether a launch goes through turns. `quiet` leaves out the warning logged when an
    older turns is installed.

    When `MOXI_LAUNCHER_TURNS` names a module, that module stands in for turns' API: the
    seam a test uses to give the launcher a fake turns.
    """
    seam = os.environ.get(TURNS_SEAM)
    if seam:
        return importlib.import_module(seam)
    if not modules.available("turns"):
        return None
    found = modules.load("turns", interface=TURNS_INTERFACE)
    if not found.ok:
        if not quiet:
            log.warning("turns is installed but can't be used for the first turn: %s",
                        found.reason)
        return None
    return found.api


# -- sending ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    """What a sender did for one launch.

    `outcome` is one of:

    - `sent`: turns said the first turn started; the row is `first-sent`.
    - `unconfirmed`: the text was handed to turns, which could not say whether it went in
      (it was lost, or no answer came in time); the row is `first-sent`, reason
      `unconfirmed`. It was not sent again.
    - `refused`: turns refused it; the row is `failed` with turns' reason.
    - `no-ready`: the session was not ready in time; the row is `failed`, `no-ready`, and
      nothing was sent.
    - `failed`: nothing was sent and the row is `failed` (`reason` says why).
    - `not-owed`: the launch has no first turn for a sender, having none or having had it
      on its argv; nothing was written.
    - `not-claimed`: another sender has the send, or the row has already ended; nothing
      was sent or written.
    - `unknown-launch`: the launch log holds no such launch.

    `state` and `reason` are what this sender wrote to the row (None when it wrote no
    state). `detail` carries turns' own words, `session_id` the session the text went to.
    """

    request_id: str
    outcome: str
    state: str | None = None
    reason: str | None = None
    detail: str = ""
    session_id: str | None = None
    delivery: str | None = None

    @property
    def unconfirmed(self) -> bool:
        return self.outcome == "unconfirmed"

    @property
    def exit_code(self) -> int:
        return _EXIT[self.outcome]

    def line(self) -> str:
        """One line for the caller."""
        rid = self.request_id
        tail = f" ({self.detail})" if self.detail else ""
        return {
            "sent": f"first turn of {rid} delivered",
            "unconfirmed": f"the brief of {rid} is unconfirmed: turns could not say whether "
                           f"it went in{tail}; it was not sent again",
            "refused": f"the first turn of {rid} was refused: {self.reason}{tail}",
            "no-ready": f"the first turn of {rid} was not sent: the session was not ready "
                        f"in time{tail}",
            "failed": f"the first turn of {rid} was not sent: {self.reason}{tail}",
            "not-owed": f"launch {rid} owes no first turn to send{tail}",
            "not-claimed": f"the first turn of {rid} is not this sender's to send{tail}",
            "unknown-launch": f"no launch {rid} in the launch log",
        }[self.outcome]


_EXIT = {"sent": exits.OK, "unconfirmed": exits.OK, "refused": exits.REFUSED,
         "no-ready": exits.ERROR, "failed": exits.ERROR, "not-owed": exits.OK,
         "not-claimed": exits.OK, "unknown-launch": exits.REFUSED}


def send(conn: sqlite3.Connection, request_id: str, *, pid: int | None = None,
         turns: Any | None = None, ready_wait_s: float | None = None,
         first_wait_s: float | None = None) -> Outcome:
    """Send launch `request_id`'s first turn through turns, at most once, and write the
    outcome to its row.

    The sender waits for turns to say the session is ready, up to `ready_wait_s`
    (`ready.wait_s`): by the minted session id for Claude, by `pid` for Codex under turns'
    host (`pid` defaults to the row's). For Codex it records the session id turns names.
    It then claims the send (`sending`), hands turns the text with the launch's `ref`, and
    waits for the delivery to start, up to `first_wait_s` (`first.wait_s`). `turns`
    defaults to `turns_api()`.

    Every state write is forward-only, so a write that comes after a later one changes
    nothing. Raises `store.Busy` when the launch log's write lock is held too long.
    """
    row = store.get(conn, request_id)
    if row is None:
        return Outcome(request_id, "unknown-launch")
    if not row.brief and not row.handoff_path:
        return Outcome(request_id, "not-owed", detail="no brief and no handoff")
    if on_argv(row.argv):
        return Outcome(request_id, "not-owed", detail="it was the opening prompt")
    if row.state not in _CLAIMABLE:
        return Outcome(request_id, "not-claimed", detail=f"the launch is {row.state}")
    try:
        body = text(actor=row.actor, brief=row.brief, handoff=row.handoff_path,
                    relaunched=bool(row.relaunch_of))
    except HandoffUnreadable as error:
        return _fail(conn, row, HANDOFF_UNREADABLE, error.why, leaving=_CLAIMABLE)
    turns = turns_api() if turns is None else turns
    if turns is None:
        return _fail(conn, row, NO_TURNS, "turns can't be loaded", leaving=_CLAIMABLE)
    ready_wait_s, first_wait_s = _waits(ready_wait_s, first_wait_s)

    session = _wait_ready(turns, row, pid, ready_wait_s)
    if isinstance(session, str):
        return _fail(conn, row, NO_READY, session, leaving=_CLAIMABLE, outcome="no-ready")
    session_id = getattr(session, "session_id", None)
    if row.harness == "codex" and session_id:
        store.learn_session(conn, request_id, session_id, "sender")

    if not store.set_state(conn, request_id, "sending"):
        now = store.get(conn, request_id)
        return Outcome(request_id, "not-claimed", session_id=session_id,
                       detail=f"the launch is {now.state if now else 'gone'}")

    the_ref = ref(request_id)
    delivery = None
    try:
        delivery = session.deliver(body, ref=the_ref)
        value = delivery.wait_started(first_wait_s)
    except TimeoutError:
        return _unconfirmed(conn, row, session_id, delivery, the_ref,
                            f"not started within {first_wait_s:g} s")
    except Exception as error:  # noqa: BLE001  (any failure here leaves the outcome unknown)
        return _unconfirmed(conn, row, session_id, delivery, the_ref, _why(error))
    return _settled(conn, row, session_id, delivery, the_ref, value)


def _waits(ready_wait_s: float | None, first_wait_s: float | None) -> tuple[float, float]:
    if ready_wait_s is None or first_wait_s is None:
        from . import settings

        current = settings.current()
        ready_wait_s = current["ready.wait_s"] if ready_wait_s is None else ready_wait_s
        first_wait_s = current["first.wait_s"] if first_wait_s is None else first_wait_s
    return float(ready_wait_s), float(first_wait_s)


def _wait_ready(turns: Any, row: store.LaunchRow, pid: int | None,
                timeout: float) -> Any:
    """The ready `Session`, or a string saying why there is none."""
    if row.harness == "codex":
        pid = row.pid if pid is None else pid
        if not pid:
            return "no process to find the Codex host by"
        where = {"pid": pid}
    else:
        if not row.session_id:
            return "no session id to find the session by"
        where = {"session_id": row.session_id}
    try:
        return turns.wait_ready(**where, timeout_s=timeout)
    except TimeoutError:
        return f"not ready within {timeout:g} s"
    except Exception as error:  # noqa: BLE001  (turns could not find the session)
        return _why(error)


def _settled(conn, row, session_id, delivery, the_ref, value) -> Outcome:
    kind = getattr(value, "KIND", None)
    if kind == "started":
        _event(conn, row, session_id, "started", delivery, the_ref,
               turn_id=getattr(value, "turn_id", None))
        store.set_state(conn, row.request_id, "first-sent")
        return Outcome(row.request_id, "sent", "first-sent", session_id=session_id,
                       delivery=_delivery_id(delivery, value))
    if kind == "refused":
        reason = str(getattr(value, "reason", "") or "refused")
        _event(conn, row, session_id, "refused", delivery, the_ref, reason=reason)
        store.set_state(conn, row.request_id, "failed", reason=reason, leaving=("sending",))
        return Outcome(row.request_id, "refused", "failed", reason, session_id=session_id,
                       delivery=_delivery_id(delivery, value))
    if kind == "lost":
        why = f"lost: {getattr(value, 'reason', '')}".rstrip(": ")
        return _unconfirmed(conn, row, session_id, delivery, the_ref, why, value=value)
    return _unconfirmed(conn, row, session_id, delivery, the_ref,
                        f"an answer turns does not give: {value!r}", value=value)


def _unconfirmed(conn, row, session_id, delivery, the_ref, why: str, *,
                 value: Any = None) -> Outcome:
    _event(conn, row, session_id, UNCONFIRMED, delivery, the_ref, why=why)
    store.set_state(conn, row.request_id, "first-sent", reason=UNCONFIRMED)
    log.warning("first turn of %s unconfirmed: %s; not sent again", row.request_id, why)
    return Outcome(row.request_id, "unconfirmed", "first-sent", UNCONFIRMED, why,
                   session_id=session_id, delivery=_delivery_id(delivery, value))


def _fail(conn, row, reason: str, why: str, *, leaving: Sequence[str],
          outcome: str = "failed") -> Outcome:
    store.add_event(conn, EVENT, {"outcome": outcome, "reason": reason, "why": why},
                    request_id=row.request_id, session_id=row.session_id)
    store.set_state(conn, row.request_id, "failed", reason=reason, leaving=leaving)
    return Outcome(row.request_id, outcome, "failed", reason, why)


def _event(conn, row, session_id, outcome: str, delivery, the_ref, **more: Any) -> None:
    detail = {"outcome": outcome, "ref": the_ref,
              "delivery": getattr(delivery, "id", None), **more}
    store.add_event(conn, EVENT, detail, request_id=row.request_id,
                    session_id=session_id or row.session_id)


def _delivery_id(delivery: Any, value: Any) -> str | None:
    return getattr(value, "delivery", None) or getattr(delivery, "id", None)


def _why(error: BaseException) -> str:
    words = str(error).strip()
    return f"{type(error).__name__}: {words}" if words else type(error).__name__


# -- the detached deliverer ----------------------------------------------------------------


def run_log(request_id: str, env: Mapping[str, str] | None = None) -> Path:
    """A launch's run log, `STATE/runs/<request id>.log`, where the deliverer writes."""
    return Layout(MODULE).state_dir(env) / "runs" / f"{request_id}.log"


def deliverer_argv(request_id: str, pid: int) -> list[str]:
    """The detached deliverer's command: `moxi launcher deliver REQUEST_ID --pid PID`,
    with `moxi` from this environment's scripts."""
    return [str(paths.script("moxi")), "launcher", "deliver", request_id, "--pid", str(pid)]


def start_deliverer(request_id: str, *, pid: int | None = None,
                    env: Mapping[str, str] | None = None) -> int:
    """Start the detached deliverer for launch `request_id` and return its pid at once.

    For a launch in this terminal: called just before the launcher becomes the harness,
    with `pid` its own pid (the default), which the harness keeps. The deliverer runs in a
    session of its own (a detached process group on Windows), stdin closed, its output
    appended to the launch's run log, and outlives this process. `env` is its whole
    environment (default: this process's).
    """
    pid = os.getpid() if pid is None else pid
    return proc.spawn(deliverer_argv(request_id, pid), detached=True,
                      output=run_log(request_id, env), env=env)


def deliver(request_id: str, *, pid: int | None = None, out: TextIO | None = None,
            turns: Any | None = None) -> int:
    """`moxi launcher deliver REQUEST_ID --pid PID`: send the launch's first turn, write
    the outcome to its row, print one line and return the exit code.

    A launch with a comms identity first waits for comms' wrapper to bind its name
    (`targets.settle_bind`), and one whose wrapper ended before that, or never bound the
    name, ends `failed` with the cause and sends nothing; so this process is started for
    such a launch even when no first turn is owed. That wait runs its full length, which
    outlasts the wrapper's own: a shorter `ready.wait_s` doesn't cut it, since a wrapper
    still inside its own wait is not stuck. The ready wait counts from the bind, so a
    deliverer never runs longer than the bind wait plus `ready.wait_s` plus `first.wait_s`.

    0 when the first turn was sent, is unconfirmed, or was not this deliverer's to send;
    1 when the session was not ready or nothing could be sent, or comms' wrapper failed;
    3 when turns refused it, comms refused the name, or there is no such launch; 75 when
    the launch log stayed busy or comms' daemon was not running.
    """
    out = sys.stdout if out is None else out
    try:
        conn = store.connect(create=False)
    except core_store.StoreTooNew:
        raise
    except core_store.StoreError:
        outcome = Outcome(request_id, "unknown-launch")
        print(outcome.line(), file=out, flush=True)
        return outcome.exit_code
    from . import targets  # not at the top: targets imports this module

    try:
        ready_wait_s, first_wait_s = _waits(None, None)
        ended = targets.settle_bind(conn, request_id, pid=pid)
        if ended is not None:
            print(ended.line, file=out, flush=True)
            return ended.exit_code
        outcome = send(conn, request_id, pid=pid, turns=turns, ready_wait_s=ready_wait_s,
                       first_wait_s=first_wait_s)
    except store.Busy as error:
        print(f"the launch log is busy: {error}", file=out, flush=True)
        return exits.TEMPFAIL
    finally:
        conn.close()
    print(outcome.line(), file=out, flush=True)
    return outcome.exit_code


__all__ = ["BRIEF_HEADER", "EVENT", "HANDOFF_HEADER", "HANDOFF_UNREADABLE", "NO_READY",
           "NO_TURNS", "RELAUNCH_HEADER", "TURNS_INTERFACE", "TURNS_SEAM", "UNCONFIRMED", "HandoffUnreadable",
           "Outcome", "actor", "deliver", "deliverer_argv", "on_argv", "read_handoff", "ref",
           "run_log", "send", "start_deliverer", "text", "turns_api"]
