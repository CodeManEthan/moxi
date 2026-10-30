"""The OpenCode clock: the start of a session's last request, read from `opencode.db`.

The launcher asks OpenCode where its database is (`opencode db path`, under the server's
environment) and passes it as `cache_source`. cache reads the session through sessions
(`records`), whose reader opens the database read only, with a short busy timeout and an
authorizer that lets it read the `session`, `message` and `part` tables and nothing else.
The database also holds accounts and credentials, which cache has no reason to read.

**The order of the read.** The next pair first: the pair OpenCode will use for a prompt
that names no model and no agent. It is the default agent's configured model as the
launcher reports it (`agent_model`); else the session row's `model`; else the latest user
message's `model`; else not known. Each step reads only what it needs: the session row
needs only the `session` table, and the messages are read only when a step needs them. A
next pair on a free row of the lifetime table is `no-cache` at any age, whatever the last
request was: a cold landing on a free model costs nothing. A next pair that cannot be read
is `unknown`.

Otherwise the whole session is read. On every read: every message is one the reader knows
(`role` `user` or `assistant`), and no revert is pending. The last request is the latest
step of a qualifying assistant message (this session's own, not a compaction, not a
summary). Its stamp is the latest time known to be at or before the request's start: the
message's creation for its first step, the end of the step before it for a later one. So
the clock errs old. The last pair is that message's `providerID` and `modelID`. Pairs that
differ read `cold`; equal pairs are looked up in the lifetime table, `"*"` matching any
model of the provider.

**`fresh`** only for a session the launcher's own start attempt created after a read that
found no session, until its first turn ends (`turned` false), whose rows are all on the
fresh whitelist (user messages with text parts only), made by an OpenCode version the
reader was checked against, with zero token and cost totals on its session row; or, with
no session id at all, on the caller's word that its attempt creates one (`new_session`).
The versions and the totals are sessions' (its OpenCode reader's `CHECKED_VERSIONS` and
`TOTALS`): they are facts of OpenCode's database, so they have one home.
"""

from __future__ import annotations

from moxi_sessions.api import Source, Unreadable, known, records
from moxi_sessions.api import claude as transcripts
from moxi_sessions.api import opencode as reader

from . import clock as _clock
from . import table as _table
from .clock import (COLD, FRESH, MISSING, NO_CACHE, WARM, Clock, bad_id, free_reason,
                    future_stamp, no_row, pair_text, unknown)

HARNESS = "opencode"

#: The record kinds the clock reads. Tool calls and results are left out: a message
#: names the kinds of its parts, so a tool part still keeps a session from `fresh`.
KINDS = frozenset({"message", "request", "malformed", "other"})

NO_SESSION_YET = "no session id reported yet"
NO_SOURCE = "no OpenCode database was named for this session"
NO_PAIR = "which model the next request will use cannot be told"
NO_AGENT = ("which model the next request will use cannot be told: the launcher could not "
            "name the session's default agent")
UNSHAPED_MESSAGE = "a message in this session is not in a shape the reader knows"
REVERT = "this session has a revert pending"
UNSHAPED_ROWS = "this session's rows are not in a shape the reader knows"
NO_REQUEST = "this session shows no request, but nothing confirms it has made none"
CHANGED = ("the model or provider changed since the last request, so the next request "
           "starts a cache of its own")


class Unread(Exception):
    """A read that cannot go on: `unknown`, with a code and the reason."""

    def __init__(self, reason: str, code: str = _clock.UNRECOGNISED):
        super().__init__(reason)
        self.code = code


def _parse_model(value) -> tuple | None:
    """`"provider/model"` -> (provider, model), else None."""
    if not isinstance(value, str) or "/" not in value:
        return None
    provider, _, model = value.partition("/")
    return (provider, model) if provider and model else None


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _unread(error: Unreadable, session_id, path) -> Unread:
    """sessions' failed read as the clock's `unknown`: a database with no row for the
    session is `no-record`, one whose tables or columns are not the reader's is
    `unrecognised` (the sign of an OpenCode upgrade), one that opened but could not be
    read (locked, or not a database) is `unreadable`, and one missing or that cannot be
    opened is `source-unreadable`."""
    if isinstance(error, reader.MissingSession):
        return Unread(f"no session {session_id} in {path}", _clock.NO_RECORD)
    if isinstance(error, reader.UnknownSchema):
        return Unread(error.reason, _clock.UNRECOGNISED)
    if isinstance(error, reader.QueryFailed):
        return Unread(f"the OpenCode database could not be read ({error.error})",
                      _clock.UNREADABLE)
    return Unread(error.reason, _clock.SOURCE_UNREADABLE)


class _Session:
    """One read of the session through sessions, opened on first use: the session row
    first, the records only when a step needs them."""

    def __init__(self, path, session_id):
        self.path = path
        self.session_id = session_id
        self.read = None
        self._row = None
        self._records = None
        self.failed: Unread | None = None

    def close(self):
        if self.read is not None:
            self.read.close()

    def row(self):
        """The session row (sessions' `SessionRow`). Raises Unread."""
        if self._row is not None:
            return self._row
        if self.failed is not None:
            raise self.failed
        try:
            self._row = self._open()
        except Unread as e:
            self.failed = e
            raise
        return self._row

    def _open(self):
        if not self.path:
            raise Unread(NO_SOURCE, _clock.NO_SOURCE)
        if not self.session_id:
            raise Unread(NO_SESSION_YET, _clock.NO_SESSION_YET)
        try:
            self.read = records(Source(HARNESS, str(self.path), self.session_id),
                                kinds=KINDS)
            return self.read.header.session_row
        except Unreadable as e:
            raise _unread(e, self.session_id, self.path) from e

    def records(self) -> list:
        """Every record the clock reads, in the session's order. Raises Unread."""
        if self._records is None:
            self.row()
            try:
                self._records = list(self.read)
            except Unreadable as e:
                # The read is over once it fails: every later ask fails the same way.
                self.failed = _unread(e, self.session_id, self.path)
                raise self.failed from e
        return self._records

    def messages(self) -> list:
        """Every message of the session as (its message record, its requests), oldest
        first. Raises Unread when a message is not one the reader knows: a row that is
        not a JSON object, or has a role other than user or assistant."""
        out = []
        for record in self.records():
            if record.at.get("table") == "message":
                if record.kind != "message":
                    raise Unread(UNSHAPED_MESSAGE)
                out.append((record, []))
            elif record.kind == "request" and out:
                out[-1][1].append(record)
        return out

    def no_revert(self):
        if self.row().revert_pending:
            raise Unread(REVERT, _clock.REVERT)


def next_pair(session: _Session, agent_model):
    """(provider, model) of the next request, by the first step that applies; raises
    Unread when it cannot be known."""
    if agent_model is MISSING or agent_model is None:
        raise Unread(NO_AGENT, _clock.NO_PAIR)
    if not isinstance(agent_model, dict) or not isinstance(agent_model.get("agent"), str) \
            or not agent_model.get("agent"):
        raise Unread(NO_AGENT, _clock.NO_PAIR)
    configured = agent_model.get("model")
    if configured is not None:
        pair = _parse_model(configured)
        if pair is None:
            raise Unread(f"{NO_PAIR} (the default agent's model {configured!r} does not "
                         "parse)", _clock.NO_PAIR)
        return pair
    value = session.row().model
    if value is not None:
        if (not isinstance(value, dict) or not isinstance(value.get("providerID"), str)
                or not isinstance(value.get("id"), str) or not value["providerID"]
                or not value["id"]):
            raise Unread(f"{NO_PAIR} (the session's model does not parse)", _clock.NO_PAIR)
        return value["providerID"], value["id"]
    messages = session.messages()
    session.no_revert()
    for message, _requests in reversed(messages):
        # A user message that names no model has no known model.
        if message.role != "user" or not known(message.model):
            continue
        model = message.model
        if (not isinstance(model, dict) or not isinstance(model.get("providerID"), str)
                or not isinstance(model.get("modelID"), str) or not model["providerID"]
                or not model["modelID"]):
            raise Unread(f"{NO_PAIR} (the latest user message's model does not parse)",
                         _clock.NO_PAIR)
        return model["providerID"], model["modelID"]
    raise Unread(NO_PAIR, _clock.NO_PAIR)


def read(session_id, *, cache_source, turned, new_session, agent_model=MISSING,
         now) -> Clock:
    """One OpenCode session's clock."""
    try:
        table = _table.load_table()
    except _table.TableError as e:
        return unknown(_clock.TABLE_NOT_LOADED, f"{_table.NOT_LOADED} ({e})")
    if session_id and (not isinstance(session_id, str)
                       or not transcripts.SAFE_ID_RE.match(session_id)):
        return bad_id("session id", session_id)
    session = _Session(cache_source, session_id)
    try:
        return _read(session, session_id, table=table, cache_source=cache_source,
                     turned=turned, new_session=new_session, agent_model=agent_model,
                     now=now)
    except Unread as e:
        return unknown(e.code, str(e))
    finally:
        session.close()


def _read(session: _Session, session_id, *, table, cache_source, turned, new_session,
          agent_model, now) -> Clock:
    # The next pair first: on a free row nothing else is read or checked.
    try:
        upcoming = next_pair(session, agent_model)
        unknown_next = None
    except Unread as e:
        upcoming, unknown_next = None, e
    if upcoming is not None and table.is_free(HARNESS, *upcoming):
        return Clock(NO_CACHE, reason=free_reason(*upcoming),
                     key=f"{HARNESS} {pair_text(*upcoming)} (free)", code=_clock.FREE)
    if not session_id:
        if new_session:
            return Clock(FRESH)          # the launcher's own attempt creates the session
        return unknown(_clock.NO_SESSION_YET, NO_SESSION_YET)
    if not cache_source:
        return unknown(_clock.NO_SOURCE, NO_SOURCE)
    row = session.row()
    messages = session.messages()
    session.no_revert()
    last = _last_request(messages)
    if last is None:
        return _no_request(row, session.records(), turned)
    ts, request = last
    provider, model = request.provider, request.model
    if unknown_next is not None:
        return unknown(unknown_next.code, str(unknown_next))
    if not (isinstance(provider, str) and provider and isinstance(model, str) and model):
        return unknown(_clock.NO_PAIR, NO_PAIR)
    skew = future_stamp(ts, now)
    if skew:
        return unknown(_clock.FUTURE_STAMP, skew)
    age = max(0.0, now - ts)
    if (provider, model) != tuple(upcoming):
        return Clock(COLD, age_seconds=age, reason=CHANGED, code=_clock.CHANGED)
    row_ = table.lookup(HARNESS, provider, model)
    key = f"{HARNESS} {pair_text(provider, model)}"
    if row_ is None:
        # A model with no lifetime row has no clock to count by.
        return Clock(NO_CACHE, age_seconds=age, reason=no_row(provider, model),
                     code=_clock.NO_ROW)
    if row_.free:
        return Clock(NO_CACHE, reason=free_reason(provider, model), key=f"{key} (free)",
                     code=_clock.FREE)
    kind = COLD if age >= row_.seconds else WARM
    return Clock(kind, age_seconds=age, ttl_seconds=row_.seconds, key=key,
                 basis="documented", provider=provider, model=model)


def _qualifies(message) -> bool:
    return (message.role == "assistant" and message.extra.get("agent") != "compaction"
            and message.flags.get("summary") is not True)


def _last_request(messages):
    """(stamp in epoch seconds, the request) for the session's last request, or None when
    the session has no assistant message. Raises Unread when it has assistant messages
    and none with a step the reader can use.

    A step is a request with a start time (a `step-start` part). The stamp of a message's
    first step is the message's creation; a later step's is the end of the newest step
    finished before it in that message, or the message's creation when none was."""
    assistants = [m for m in messages if m[0].role == "assistant"]
    if not assistants:
        return None
    for message, requests in reversed(assistants):
        if not _qualifies(message):
            continue
        steps = [i for i, r in enumerate(requests) if known(r.start_time)]
        if not steps:
            continue
        last = requests[steps[-1]]
        stamp = last.extra.get("message_time")
        if steps[-1] != steps[0]:
            for earlier in reversed(requests[:steps[-1]]):
                if earlier.final:
                    stamp = earlier.time
                    break
        if not _is_number(stamp):
            raise Unread("the session's last request has no readable time", _clock.NO_STAMP)
        return float(stamp), last
    raise Unread(UNSHAPED_ROWS)


def _fresh_record(record) -> bool:
    """A record of a session that has made no request: a user message whose parts are
    all text."""
    return (record.kind == "message" and record.role == "user" and not record.reasoning
            and all(kind == "text" for kind in record.content_kinds))


def _no_request(row, session_records, turned) -> Clock:
    """No assistant message: `fresh` only on every witness of a new session."""
    if turned:
        return unknown(_clock.NO_REQUEST, NO_REQUEST)
    if row.version not in reader.CHECKED_VERSIONS:
        return unknown(_clock.NO_REQUEST, NO_REQUEST)
    if any(getattr(row, k) not in (0, 0.0) for k in reader.TOTALS):
        return unknown(_clock.NO_REQUEST, NO_REQUEST)
    if not all(_fresh_record(r) for r in session_records):
        return unknown(_clock.NO_REQUEST, NO_REQUEST)
    return Clock(FRESH)


__all__ = ["HARNESS", "read", "next_pair", "Unread"]
