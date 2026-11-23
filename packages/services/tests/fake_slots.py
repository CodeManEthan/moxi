"""A stand-in for slots' API, which services' page reads to say what a stop of slots'
server would end.

`install` writes a fake `slots` module into a test's site: an announcement that declares
the `server` daemon and offers interface 1, and an `API` module whose `list_slots` and
`jobs` answer from a `FakeSlots`. So services reaches it as it reaches the real one,
through `moxi.modules.load("slots", interface=1)`, and slots' server is a row. The
records carry the fields services reads of slots' own (`Listing.ok`, `why`, `slots`,
`unregistered`; a slot's `number`, `state`, `idle`, `label`; a job's `name`, `running`),
and a fake can be told to fail as the page must survive: a listing not `ok`, `jobs()`
raising, or a read that doesn't answer until it is let go.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

#: The fake slots module's announcement: its server daemon, and interface 1.
ANNOUNCEMENT = '''\
SUMMARY = "slots"
INTERFACES = frozenset({1})
API = "moxi_slots.api"
DAEMONS = {"server": {"command": "moxi-slots-server"}}
'''

#: The fake API module: each call answers from `fake_slots.FAKE`.
API = '''\
import fake_slots


def list_slots():
    return fake_slots.FAKE.list_slots()


def jobs():
    return fake_slots.FAKE.jobs()
'''


@dataclass(frozen=True)
class Slot:
    """One slot: `state` is `free`, `ended`, `occupied` or `missing`; `idle` False while
    someone is at it; `label` the launch's label while its run pane is there."""

    number: int
    state: str = "free"
    idle: bool = True
    label: str | None = None


@dataclass(frozen=True)
class Job:
    name: str
    running: bool = True


@dataclass(frozen=True)
class Listing:
    ok: bool
    why: str = ""
    slots: tuple = ()
    unregistered: tuple = ()


@dataclass
class FakeSlots:
    """What the fake API answers. `ok` False makes the listing say it couldn't read, with
    `why`; `jobs_raise` is raised by `jobs()`; with `hold` set, `list_slots` waits until
    the event is set, as a server that doesn't answer."""

    slots: list = field(default_factory=list)
    unregistered: list = field(default_factory=list)
    running: list = field(default_factory=list)
    ok: bool = True
    why: str = ""
    jobs_raise: Exception | None = None
    hold: threading.Event | None = None
    #: every call, by name, in order.
    calls: list = field(default_factory=list)

    def list_slots(self) -> Listing:
        self.calls.append("list_slots")
        if self.hold is not None:
            self.hold.wait(30)
        if not self.ok:
            return Listing(ok=False, why=self.why)
        return Listing(ok=True, slots=tuple(self.slots),
                       unregistered=tuple(self.unregistered))

    def jobs(self) -> list[Job]:
        self.calls.append("jobs")
        if self.jobs_raise is not None:
            raise self.jobs_raise
        return list(self.running)


#: The fake the installed API answers from.
FAKE = FakeSlots()


def install(site, fake: FakeSlots) -> FakeSlots:
    """Write the fake slots module into `site` (conftest's `Site`), answering from
    `fake`."""
    global FAKE
    FAKE = fake
    site.module("slots", ANNOUNCEMENT)
    site.write("moxi_slots/api.py", API)
    return fake
