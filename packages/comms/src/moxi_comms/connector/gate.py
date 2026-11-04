"""comms' mint gate for the connector's sign-in codes: who may mint one.

The connector mints a sign-in code only when a loaded provider declares a gate and no gate
refuses. comms' gate has two checks, each answering a refusal text or None:

* `peer(peer)` runs on the control socket's peer before every mint. It is the walk the
  daemon makes before a send under a reserved name: the peer's pid, walked up its
  ancestry against every live claim's roots, strictly. A peer inside a session is
  refused, and so is a peer the walk can't place (a process gone, an ancestry that can't
  be read), never read as outside. This check is the gate.
* `shell(env)` runs in `moxi connector code` before it asks the socket: the shell must be
  the operator's lane, as `moxi comms cache` tells it. No claim socket and no session
  name, and `MOXI_COMMS_OPERATOR` set. It gives an honest refusal early; a process can
  call the socket directly, so it is not a gate.

What the gate does not stop, plainly: a process of this user outside every session's
process tree (one an agent started with `setsid` or `systemd-run --user`, a timer, a
harness comms doesn't know) passes the walk. That is the boundary comms already keeps for
the operator's lane and for reserved names; `docs/security.md` says so.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from .. import claimfile, claimhold, membership
from ..sender import OPERATOR_VAR, OperatorRefused, operator_from_env

log = logging.getLogger("comms.connector.gate")

#: The socket's refusal: the peer is inside a session, or the walk couldn't tell.
PEER_REFUSED = ("This process is inside an agent session, or the connector couldn't tell "
                "whether it is. Run moxi connector code in your own terminal.")
#: The verb's refusal of a shell inside a session. `{name}` is the session's name.
SHELL_IN_SESSION = ("This shell is inside the agent session {name} (MOXI_COMMS_NAME or a "
                    "claim socket is set). Run moxi connector code in your own terminal, "
                    "outside every agent session.")
#: The verb's refusal of a shell with no operator stamp.
SHELL_NO_OPERATOR = (f"This shell carries no operator stamp: {OPERATOR_VAR} is not set. "
                     "Your own terminal sets it; comms' configuration doc says how.")

NAME_VAR = "MOXI_COMMS_NAME"


def peer(peer, *, view: membership.ProcView | None = None, records=None,
         alive: Callable | None = None) -> str | None:
    """The socket's check of one control-socket `Peer` (its `pid` and `proc`, a
    `moxi.proc.ProcId`). None lets the mint through; anything else refuses it.

    `view`, `records` (the live claim records, `(key, record)` pairs) and `alive` (a
    `ProcId`'s liveness) are the process table's readers, for a test; each defaults to
    what the daemon's own walk reads."""
    pid = getattr(peer, "pid", None)
    ident = getattr(peer, "proc", None)
    if not pid or ident is None:
        return PEER_REFUSED
    if alive is None:
        from moxi import proc as moxi_proc

        alive = moxi_proc.alive
    try:
        if not alive(ident):
            return PEER_REFUSED
        view = view or membership.DEFAULT_PROC
        live = claimfile.live_records(view) if records is None else list(records)
        inside = membership.sessions_of(pid, claimfile.membership_table(live), view,
                                        strict=True)
    except membership.Unplaceable as e:
        log.info("a sign-in code was refused: process %s's ancestry stops at %s, which "
                 "can't be read", pid, e.pid)
        return PEER_REFUSED
    except Exception:  # noqa: BLE001 - a walk that fails never reads as outside
        log.exception("a sign-in code was refused: process %s couldn't be placed", pid)
        return PEER_REFUSED
    if inside:
        log.info("a sign-in code was refused: process %s is inside a session", pid)
        return PEER_REFUSED
    return None


def shell(env: Mapping[str, str], *, overlay: Callable | None = None) -> str | None:
    """The verb's check of its own environment `env`. None when it is the operator's
    lane; else the refusal naming the check that failed. `overlay` reads the shell's
    claim socket (`claimhold.claim_overlay`), for a test."""
    env = dict(env)
    seen = (overlay or claimhold.claim_overlay)(env)
    if env.get(claimhold.CLAIM_SOCKET_ENV) or env.get(NAME_VAR) or seen.get(NAME_VAR):
        return SHELL_IN_SESSION.format(name=_session_name(env, seen))
    try:
        operator = operator_from_env(seen)
    except OperatorRefused:
        return SHELL_IN_SESSION.format(name=_session_name(env, seen))
    if operator is None:
        return SHELL_NO_OPERATOR
    return None


def _session_name(env: Mapping[str, str], seen: Mapping[str, str]) -> str:
    """The session's name as the shell's claim socket answers it, else as its stamp
    spells it, else a placeholder that says the claim socket names it."""
    name = (seen.get(NAME_VAR) or env.get(NAME_VAR) or "").strip()
    return name or "that holds this shell's claim socket"


__all__ = ["PEER_REFUSED", "SHELL_IN_SESSION", "SHELL_NO_OPERATOR", "peer", "shell"]
