"""memory's optional links, comms, the launcher and notes, each reached through
`moxi.modules.load` at the moment it is needed.

A link is in one of three states:

- `READY`: the module is installed and offers the interface memory speaks; `api` is its
  API module.
- `NONE`: the module is not installed, or is installed without that interface. memory
  runs as it does without it: no identities without comms, no launch kinds without the
  launcher, and a recall that reads past sessions only without notes.
- `FAILING`: the module is installed and offers the interface, but loading it failed.
  A caller treats it as a link it can't ask this time, not as a missing one.

Each call asks `moxi.modules` again, which keeps what it loaded for the process, so a
long-running caller sees a module installed after it started. Standard library at
import.
"""

from __future__ import annotations

from typing import Any, NamedTuple

READY = "ready"
NONE = "none"
FAILING = "failing"
#: How a `NONE` link's reason says the module is installed without the interface.
NO_INTERFACE = "doesn't offer interface"

#: The comms interface memory speaks: `caller_identity`.
COMMS = "comms"
COMMS_INTERFACE = 2
#: The comms interface memory's recall sender speaks: scoped senders (`send_scoped`,
#: `scoped_state`, `scoped_sender`) and `owner`.
COMMS_SCOPED_INTERFACE = 3
#: The launcher interface memory speaks: `launch_kind` and `launch_kinds`.
LAUNCHER = "launcher"
LAUNCHER_INTERFACE = 1
#: The launcher interface the slot route speaks: `fork` and `exit_session`.
LAUNCHER_FORK_INTERFACE = 3
#: The notes interface memory speaks: `notes_dir`, `root` and `NOTES_DIR_ENV`.
NOTES = "notes"
NOTES_INTERFACE = 1


class Link(NamedTuple):
    """One optional link: its state, its API module when `READY`, and why when not."""

    state: str
    api: Any = None
    reason: str = ""

    @property
    def ready(self) -> bool:
        return self.state == READY


def load(name: str, interface: int) -> Link:
    """The link to module `name` speaking `interface`. Never raises."""
    try:
        from moxi import modules

        if not modules.available(name):
            return Link(NONE, reason=f"{name} is not installed")
        found = modules.load(name)
        if found.state == modules.ABSENT:
            return Link(NONE, reason=found.reason or f"{name} is not installed")
        if found.state != modules.PRESENT:
            return Link(FAILING, reason=found.reason)
        if interface not in found.interfaces:
            return Link(NONE, reason=f"{name} {found.version} {NO_INTERFACE} "
                                     f"{interface}")
        found = modules.load(name, interface=interface)
        if not found.ok or found.api is None:
            return Link(FAILING, reason=found.reason or f"{name} has no API module")
        return Link(READY, found.api)
    except Exception as exc:  # noqa: BLE001 - a link that can't load is one we can't ask
        return Link(FAILING, reason=f"loading {name} failed: {exc}")


def comms() -> Link:
    """The link to comms, for a session's identity."""
    return load(COMMS, COMMS_INTERFACE)


def comms_scoped() -> Link:
    """The link to comms with scoped senders, for a recall's way back by comms."""
    return load(COMMS, COMMS_SCOPED_INTERFACE)


def launcher() -> Link:
    """The link to the launcher, for a session's launch kind."""
    return load(LAUNCHER, LAUNCHER_INTERFACE)


def launcher_forks() -> Link:
    """The link to the launcher that forks a session and exits one, for the slot route
    and a slot fork's discard."""
    return load(LAUNCHER, LAUNCHER_FORK_INTERFACE)


def notes() -> Link:
    """The link to notes, for the notes a recall reads."""
    return load(NOTES, NOTES_INTERFACE)


__all__ = ["READY", "NONE", "FAILING", "NO_INTERFACE", "COMMS", "COMMS_INTERFACE",
           "COMMS_SCOPED_INTERFACE", "LAUNCHER", "LAUNCHER_INTERFACE",
           "LAUNCHER_FORK_INTERFACE", "NOTES", "NOTES_INTERFACE", "Link", "load", "comms",
           "comms_scoped", "launcher", "launcher_forks", "notes"]
