"""A launch's comms identity, as comms' `identity_state` reports it.

`check(name, scope, comms)` is the live check a launch with an identity meets before
anything is written: None when the name is free to take, else the refusal. comms says
one of these of a name:

| state | what it is | the launch |
|---|---|---|
| `free` | no one holds it | goes on |
| `standing` | a standing identity's own name, not bound now | goes on: its session binds it |
| `offline` | comms' daemon can't be asked | refused as a try-again: the wrapper would stop |
| `held` | a live session holds it | refused, naming the holder; not refused for a relaunch's check when the holder is the session it replaces |
| `reserved` | a send-only name | refused, naming the module or a program |
| `retired` | a scoped name comms no longer binds | refused, with comms' words |

comms raises `ValueError` for what is not a name a launch can claim (refused with its
words), and `DaemonError` for an answer it can't read as a state (a try-again refusal
naming the comms daemon). A comms without `identity_state` is not asked.
"""

from __future__ import annotations

from typing import Any

from moxi import exit as exits

from .catalog import Refused

#: The states a launch is refused in: the name is someone else's, or not anyone's to take.
TAKEN = ("held", "reserved", "retired")
#: The holder a relaunch's check names when the name's binding has reported no session id.
NO_SESSION_YET = "a binding that has reported no session yet"


class IdentityTaken(Refused):
    """The comms name a launch asks for is held, reserved or retired. `holder` is who
    holds it, `module` the module it is reserved for, `registered` whether a program
    registered it as a send-only name, `reason` comms' words."""

    code = "identity-taken"

    def __init__(self, name: str, state: str, holder: str | None = None, *,
                 module: str | None = None, reason: str | None = None,
                 registered: bool = False):
        self.name, self.state, self.holder = name, state, holder
        self.module, self.reason, self.registered = module, reason, registered
        if state == "held":
            hint = "pick another identity, or wait until that session ends"
        elif state == "reserved":
            hint = "pick another identity: no session binds a send-only name"
        elif state == "retired":
            hint = "launch it under its bare name with no scope, or pick another identity"
        else:
            hint = "pick another identity, or wait until it is free"
        super().__init__("identity", words(name, state, holder, module=module,
                                           reason=reason, registered=registered), hint)


class IdentityRefused(Refused):
    """comms refuses the name itself: it is not one a launch can claim."""

    code = "identity-refused"

    def __init__(self, name: str, said: str):
        self.name = name
        super().__init__("identity", f"comms refuses identity {name}: {said}",
                         "pick another identity")


class CommsUnanswered(Refused):
    """comms' daemon answered the identity check with something comms could not read."""

    code = "try-again"
    exit_code = exits.TEMPFAIL

    def __init__(self, name: str, said: str):
        self.name = name
        super().__init__("identity", f"the comms daemon gave no answer on identity {name} "
                                     f"that could be read: {said}",
                         "try again; if it goes on, moxi doctor shows what is wrong with "
                         "comms")


class CommsOffline(Refused):
    """comms can't reach its daemon to ask about the name, so its wrapper could not bind
    it either."""

    code = "try-again"
    exit_code = exits.TEMPFAIL

    def __init__(self, name: str, said: str | None):
        self.name = name
        super().__init__("identity", f"the comms daemon can't be asked about identity "
                                     f"{name}" + (f": {said}" if said else ""),
                         "try again once the comms daemon runs; moxi doctor shows what is "
                         "wrong with comms")


def words(name: str, state: str, holder: str | None = None, *,
          module: str | None = None, reason: str | None = None,
          registered: bool = False) -> str:
    """One state of a name, in a few words."""
    if state == "held":
        return f"identity {name} is held by {holder or 'a live session'}"
    if state == "reserved" and registered:
        return f"identity {name} is a send-only name a program registered"
    if state == "reserved":
        return (f"identity {name} is reserved for {module}" if module else
                f"identity {name} is reserved, and no module owns it")
    if state == "retired":
        return f"identity {name} is retired" + (f"; comms says: {reason}" if reason else "")
    if state == "offline":
        return "the comms daemon can't be asked" + (f": {reason}" if reason else "")
    by = f" by {holder}" if holder else ""
    return f"identity {name} is {state}{by}"


def shown(name: str, scope: str | None) -> str:
    """The name as a refusal shows it, with its scope."""
    return f"{name}@{scope}" if scope and "@" not in name else name


def ask(name: str, scope: str | None, comms: Any) -> Any:
    """comms' state of the name, or None when this comms has no identity check. Raises
    what `identity_state` raises."""
    call = getattr(comms, "identity_state", None) if comms is not None else None
    return None if call is None else call(name, scope=scope)


def check(name: str, scope: str | None, comms: Any, *,
          replacing: str | None = None) -> Refused | None:
    """The refusal a launch claiming `name` in `scope` meets, from `comms` (comms' API, or
    None), or None when the launch goes on. See the module's text. `replacing` is the
    session a relaunch takes over: the name held by it is not refused."""
    if comms is None or getattr(comms, "identity_state", None) is None:
        return None
    named = shown(name, scope)
    try:
        state = ask(name, scope, comms)
    except ValueError as exc:
        return IdentityRefused(named, str(exc))
    except daemon_errors(comms) as exc:
        return CommsUnanswered(named, str(exc))
    if state.state == "offline":
        return CommsOffline(getattr(state, "qualified", None) or named,
                            getattr(state, "reason", None))
    if state.state not in TAKEN:
        return None
    holder = getattr(state, "holder", None)
    if state.state == "held" and replacing:
        held_by = getattr(holder, "session_id", None)
        if held_by == replacing:
            return None
        if not held_by:
            taken = IdentityTaken(getattr(state, "qualified", None) or named, "held",
                                  NO_SESSION_YET)
            taken.hint = ("arm the relaunch again once the session's comms binding has "
                          "reported its session")
            return taken
    return IdentityTaken(getattr(state, "qualified", None) or named, state.state,
                         str(holder) if holder else None,
                         module=getattr(state, "module", None),
                         reason=getattr(state, "reason", None),
                         registered=bool(getattr(state, "registered", False)))


def daemon_errors(comms: Any) -> tuple[type[BaseException], ...]:
    """comms' `DaemonError`, as an `except` clause takes it (empty when comms has none)."""
    error = getattr(comms, "DaemonError", None)
    return (error,) if isinstance(error, type) and issubclass(error, BaseException) else ()


__all__ = ["NO_SESSION_YET", "TAKEN", "CommsOffline", "CommsUnanswered", "IdentityRefused",
           "IdentityTaken", "ask", "check", "daemon_errors", "shown", "words"]
