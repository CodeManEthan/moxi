"""The record INTERFACE -- the seam the daemon, the sender, the CLI and provision use.

There are two implementations, and nothing above this line knows which one it holds:

  * `local_record.LocalRecord` -- the box's own SQLite record under STATE, written
                                  directly by every comms process and subscribed to by
                                  the daemon.
  * `mock_record.MockRecord`   -- in-process (and optionally JSON-file-backed) record;
                                  the tests' double.

The interface, in full:

    channels     create_channel(name) -> channel_id        (idempotent by name)
                 channel_by_name(name) -> channel_id | None
                 channel_name(channel_id) -> name | None
    principals   register_principal(name) -> principal_id
                 deny_send(principal, channel_id) / can_send(principal, channel_id)
    posting      post_as_user(user_id, channel_id, content, *, token=None)
                                                           -- operator lane; the local
                                                              backend checks the token
                 post_as_identity(qualified, channel_id, content)
                                                           -- daemon-stamped agent post
                 post_as_reserved(name, channel_id, content)
                                                           -- a module's post under a
                                                              name it reserved, after
                                                              the daemon checked its token
                 max_content_length -> int   (attribute, not a method)
                 history_bound -> int | None (attribute: the most messages an
                                              unbounded `history(after=)` returns, or
                                              None when it returns them all)
    the record   history(channel_id, after=, limit=) -> [Message]   (ascending by id)
                 get_message(message_id) -> Message | None
                 max_message_id(channel_id=None) -> str | None
                 since(channel_id, timestamp) -> [Message]  (the daemon's boot read of
                                                           its sent index)
    events       subscribe(from_id=) -> Subscription / unsubscribe(sub) / close()

Events on a Subscription are exactly two shapes, and the second one is load-bearing:

    {"t": "MESSAGE_CREATE", "d": <Message.to_dict()>}
    {"t": "READY", "resumed": bool}          # resumed=False => the daemon MUST rescan

`max_content_length` is the seam DECLARING a transport fact, and nothing more: how long
one post's content may be. Every implementation declares it and enforces it. The send
verb checks it first and refuses an over-long message with nothing posted; comms does
not split.

`history_bound` is the same kind of declaration about the record: how many messages an
unbounded forward read (`history(channel, after=id)`) can return before the backend
stops on its own. None means the whole range comes back (the local record and the
mock), and both declare None. The send verb reads it to know whether an open thread's members read
may have been cut short; a record that declares nothing counts as bounded.

`Message` carries NO reply field. Correlation rides the machine line's `re:<message_id>`
token, and a reader gets it by parsing the record's own line zero. A transport-level reply
field here would be a second source of truth for the same fact.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field

#: A message's `author_kind`, who posted it: an agent the daemon checked against its live
#: binding, the operator through the operator's lane, or a module under a name it
#: reserved, with that name's token.
AUTHOR_IDENTITY, AUTHOR_OPERATOR, AUTHOR_RESERVED = "identity", "operator", "reserved"
#: The operator's kind as older stores spell it: comms 0.2's record, and comms.db before
#: its schema's second step. The 0.2 move and that step rewrite it to `operator`, and
#: nothing writes it any more. Attribution still reads it, for a store a read-only open
#: finds before any writer has stepped it.
AUTHOR_OPERATOR_OLD = "user"

# The one cap on one message, in characters. Declared here so the local record and the
# mock name ONE number.
MAX_CONTENT_LENGTH = 16_000


class RecordError(Exception):
    pass


class RateLimited(RecordError):
    """429. `retry_after` is seconds, surfaced, never swallowed here --
    the send verb owns the retry policy."""

    def __init__(self, retry_after: float, *, scope: str = "unknown", global_: bool = False):
        super().__init__(f"429 rate limited, retry after {retry_after}s")
        self.retry_after = retry_after
        self.scope = scope
        self.global_ = global_


class Forbidden(RecordError):
    """403 -- e.g. a principal whose Send was channel-denied."""


class Fatal(RecordError):
    """A failure that retrying cannot fix (4004 auth failed, 4014 disallowed intents).

    Surfaced loudly and never retry-looped: a retry loop against a fatal close code
    looks connected while nothing arrives.
    """


@dataclass
class Message:
    id: str
    channel_id: str
    author_id: str            # webhook id, operator user id, or qualified identity
    author_kind: str          # 'identity' | 'operator' | 'reserved'; an older row may
                              # say 'webhook'
    author_display: str       # webhook override, user display, or qualified identity
    content: str
    timestamp: str
    #: When its author withdrew it (`moxi comms remove`), the record's own time
    #: spelling; None while it stands. A withdrawn message stays in the record.
    withdrawn_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        # tolerate a record dict from an older store: `reply_to` used to ride the
        # transport, and now lives in the machine line only.
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class Subscription:
    """One record session's event queue, as seen by the daemon."""

    def __init__(self, last_id: str = "0"):
        self._q: list = []
        self._cv = threading.Condition()
        self.last_id = last_id
        self.closed = False

    def next_event(self, timeout: float = 1.0):
        deadline = time.monotonic() + timeout
        with self._cv:
            while not self._q and not self.closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cv.wait(remaining)
            return self._q.pop(0) if self._q else None

    def close(self) -> None:
        with self._cv:
            self.closed = True
            self._cv.notify_all()

    def _push(self, event: dict) -> None:
        with self._cv:
            self._q.append(event)
            self._cv.notify_all()


@dataclass
class _State:
    """Mock record state (lives here so the mock and the interface stay one file apart)."""

    seq: int = 0
    channels: dict = field(default_factory=dict)
    denied: dict = field(default_factory=dict)
    users: dict = field(default_factory=dict)
    messages: list = field(default_factory=list)
    #: The record's meta keys (the operator's token hash among them).
    meta: dict = field(default_factory=dict)

    def replace_with(self, raw: dict) -> None:
        for k, v in raw.items():
            setattr(self, k, v)


# --------------------------------------------------------------------------- selection

MOCK = "mock"
LOCAL = "local"
BACKENDS = (LOCAL, MOCK)


#: Where a backend choice came from, most local first.
SOURCE_FLAG = "flag"
SOURCE_ENV = "environment"
SOURCE_CONFIG = "config"
SOURCE_DEFAULT = "default"


@dataclass(frozen=True)
class BackendChoice:
    """Which backend and where the choice came from."""

    name: str
    source: str

    def describe(self) -> str:
        """The source in words, for `moxi comms doctor`."""
        if self.source == SOURCE_FLAG:
            return "the --backend flag"
        if self.source == SOURCE_ENV:
            return "MOXI_COMMS_BACKEND"
        if self.source == SOURCE_CONFIG:
            return "the config file"
        return "the default"


def default_backend() -> str:
    """The backend when nothing names one: `local`, the box's own record, so an install
    that names nothing gets a working backend (config.toml's `backend` default)."""
    from . import config

    return config.schema().keys["backend"].default


def backend_choice(explicit: str | None = None, env=None) -> BackendChoice:
    """Which backend a run uses, and why: the flag, then config.toml's `backend` with
    its override `MOXI_COMMS_BACKEND`, then the default (`local`).

    `mock` is never a default: a test or a user picks it by name.
    """
    import os

    from . import config

    env = env if env is not None else os.environ
    if explicit:
        chosen, source = explicit, SOURCE_FLAG
    else:
        try:
            cfg = config.load(env=env)
        except config.ConfigError as e:
            raise RecordError(str(e)) from None
        chosen = cfg.backend
        source = {"env": SOURCE_ENV, "file": SOURCE_CONFIG}.get(
            cfg.sources.get("backend"), SOURCE_DEFAULT)
    if chosen not in BACKENDS:
        raise RecordError(f"unknown record backend {chosen!r}; expected one of "
                           f"{', '.join(BACKENDS)}")
    return BackendChoice(chosen, source)


def backend_name(explicit: str | None = None, env=None) -> str:
    """The name half of `backend_choice`: the flag, `MOXI_COMMS_BACKEND`, the config
    file, then `local`."""
    return backend_choice(explicit, env).name


def open_record(explicit: str | None = None, *, role: str = "reader", env=None,
                 busy_timeout: float | None = None):
    """Open the record this run is configured for. `role` is accepted from every caller
    and means nothing to either backend. `busy_timeout` is the local record's wait for a
    writer's lock, in seconds (None: its own default); the mock has no lock."""
    import os

    env = env if env is not None else os.environ
    name = backend_name(explicit, env=env)
    if name == MOCK:
        from .mock_record import MockRecord
        store = env.get("MOXI_COMMS_MOCK_BACKEND")
        if not store:
            raise RecordError(
                "the mock record needs a store: set MOXI_COMMS_MOCK_BACKEND=<store.json>")
        return MockRecord(store)
    from . import config
    cfg = config.load(env=env)
    from .local_record import LocalRecord
    local = cfg.local
    return LocalRecord(local.record or None, retain_days=local.retain_days,
                        busy_timeout=busy_timeout)


__all__ = ["AUTHOR_IDENTITY", "AUTHOR_OPERATOR", "AUTHOR_RESERVED", "AUTHOR_OPERATOR_OLD",
           "MAX_CONTENT_LENGTH", "RecordError", "RateLimited", "Forbidden", "Fatal",
           "Message", "Subscription", "MOCK", "LOCAL", "BACKENDS",
           "BackendChoice", "SOURCE_FLAG", "SOURCE_ENV", "SOURCE_CONFIG", "SOURCE_DEFAULT",
           "backend_choice", "backend_name", "default_backend", "open_record"]
