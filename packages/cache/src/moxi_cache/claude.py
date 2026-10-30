"""The Claude clock: the end of a session's last request, read from its own transcript.

**The clock** is the session's main conversation: the time of the newest request record
of its transcript whose `sidechain` and `synthetic` are both false. A request is an
`assistant` line with a `usage` dict, one record per request, from its newest line; a
synthetic one is a request Claude Code made up itself (model `<synthetic>`). A subagent
writes its own file and its lines are sidechain, so a subagent's work never moves it.
cache finds the file through sessions (`find`) and reads its requests backwards
(`records_back`), an unterminated last line included, and judges each request itself. The
clock reads young by the length of the last response, because the stamp is written when a
response ends and the lifetime runs from when the request started; a read inside that
margin past the lifetime reads warm. Declared.

**What each ending of the read means.** The newest request that meets the rule is the
clock; one with no readable stamp is `unknown`. A read that ended at the start of the file
with no such request is `fresh`, unless a line on the way would not parse (a `malformed`
record), which is `unknown`. A read that ended at its byte limit with no such request is
`unknown`, never `fresh`: a long tail with no request is not evidence of no request.

**`fresh` needs positive evidence.** The absence of evidence is `unknown`. Three things
are evidence: (1) a transcript found and read to its start, with no request that meets the
rule; (2) the caller's word that its next delivery starts a new session, with no session
id (`new_session`); (3) no transcript, on the session's own word: the caller passed the
root the session itself stated (`projects_root`), that root was read and holds no file for
the id, and no prompt or turn end has been seen in the session (`turned` false). A Claude
session writes no transcript until its first prompt. A missing transcript with anything
less is `unknown`, and its reason names the root searched. For a session that stated no
root, cache reads under sessions' root for it (config `claude.projects`).

**The lifetime** comes from the cache tier the requests wrote. Every request says how much
it wrote for five minutes and how much for an hour (`cache_creation_5m_input_tokens`,
`cache_creation_1h_input_tokens`); a count above zero names that tier. The lifetime is the
tier of the newest request that meets the rule and names a tier, at or before the clock
request: one hour or five minutes, the shorter when a request wrote both. A request that
only read the cache wrote no tier, so when the clock request names none the same backward
read goes on past it, within the same byte limit, to the first request that does. A read
that reaches the start or the limit with no tiered request uses config
`claude.ttl_seconds`, and the `Clock` says which it used (`ttl_source`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from moxi_sessions.api import Source, find, records_back
from moxi_sessions.api import claude as transcripts
from moxi_sessions.api import projects_root as sessions_projects_root

from . import clock as _clock
from .clock import (COLD, FRESH, TTL_CONFIG, TTL_TIER, WARM, Clock, bad_id, future_stamp,
                    unknown)

HARNESS = "claude"

#: The two tiers a request can write, by the request field that counts each, and their
#: lifetimes.
TIER_1H = ("cache_creation_1h_input_tokens", 3600)
TIER_5M = ("cache_creation_5m_input_tokens", 300)

#: The reason a read with no session id is `unknown`.
NO_SESSION_YET = "no session id reported yet"

#: The record kinds the clock reads. A line that will not parse is asked for too: with
#: requests alone the read would pass it by unseen, and a read to the start past one is
#: not evidence of no request.
KINDS = frozenset({"request", "malformed"})


def _count(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def tier_of(request) -> int | None:
    """The lifetime, in seconds, of the cache tier a request wrote; None when it wrote
    none. The shorter when it wrote both."""
    found = [seconds for field, seconds in (TIER_5M, TIER_1H)
             if _count(getattr(request, field, None))]
    return min(found) if found else None


def _meets(record) -> bool:
    """The clock's rule: a request of the main conversation, not one Claude Code made up."""
    return (record.kind == "request" and record.sidechain is not True
            and record.synthetic is not True)


def _stamp(record) -> float | None:
    return record.time if isinstance(record.time, (int, float)) else None


@dataclass(frozen=True)
class Read:
    """What one backward read of a transcript found.

    `outcome` is `clock` (with `ts`, and `tier` when a request at or before the clock
    request named one), `none` (read to the start, no request meets the rule), `limit`
    (the limit stopped the read with no such request), `nostamp` (the newest such request
    has no readable stamp) or `malformed` (read to the start, no such request, and a line
    that would not parse). `tier_ended` says how the tier read ended when the clock
    request named none: `start` or `limit`."""

    outcome: str
    ts: float | None = None
    tier: int | None = None
    tier_ended: str | None = None


def last_request(path, *, limit: int | None = None) -> Read:
    """Read a transcript backwards to its clock request, and past it to the tier. Raises
    OSError on a read that fails."""
    limit = _clock.CLOCK_SCAN_LIMIT if limit is None else limit
    malformed = False
    with records_back(Source(HARNESS, str(path), None, None), kinds=KINDS,
                      block=_clock.CLOCK_BLOCK, limit=limit) as back:
        records = iter(back)
        for record in records:
            if record.kind == "malformed":
                malformed = True
                continue
            if not _meets(record):
                continue
            ts = _stamp(record)
            if ts is None:
                return Read("nostamp")
            tier = tier_of(record)
            if tier is not None:
                return Read("clock", ts, tier)
            for older in records:
                if _meets(older):
                    tier = tier_of(older)
                    if tier is not None:
                        return Read("clock", ts, tier)
            return Read("clock", ts, None, tier_ended=back.ended)
        if back.ended == "limit":
            return Read("limit")
    return Read("malformed") if malformed else Read("none")


def read(session_id, *, now: float, projects_root=None, turned: bool = True,
         new_session: bool = False) -> Clock:
    """One Claude session's clock. Raises nothing the caller does not catch."""
    if not session_id:
        if new_session:
            return Clock(FRESH)          # the caller's next delivery starts a new session
        return unknown(_clock.NO_SESSION_YET, NO_SESSION_YET)
    if not isinstance(session_id, str) or not transcripts.SAFE_ID_RE.match(session_id):
        return bad_id("session id", session_id)
    own = bool(projects_root)
    if own:
        root = Path(projects_root)
    else:
        from moxi.config import ConfigError

        try:
            root = Path(sessions_projects_root(None))
        except ConfigError as e:
            return unknown(_clock.CONFIG_ERROR, f"sessions' config could not be read ({e})")
    located = find(HARNESS, session_id, root=root)
    if not located.found:
        readable = located.probes[0].outcome == transcripts.MISS
        if own and readable and not turned:
            return Clock(FRESH)          # no transcript, on the session's own word
        where = root if readable else f"{root} (missing or unreadable)"
        return unknown(_clock.NO_RECORD, f"no transcript for session {session_id} under "
                                         f"{where}, and nothing shows it has made no "
                                         "request")
    try:
        found = last_request(located.hits[0].source.path)
    except OSError as e:
        return unknown(_clock.UNREADABLE,
                       f"the transcript could not be read ({e.strerror or e})")
    if found.outcome == "none":
        return Clock(FRESH)              # read whole, and no request in it
    if found.outcome == "limit":
        return unknown(_clock.SCAN_LIMIT,
                       f"no request found in the last "
                       f"{_clock.CLOCK_SCAN_LIMIT // (1024 * 1024)} MB of the transcript")
    if found.outcome == "nostamp":
        return unknown(_clock.NO_STAMP, "the transcript's last request has no readable time")
    if found.outcome == "malformed":
        return unknown(_clock.MALFORMED, "the transcript has lines that will not parse and "
                                         "no request that can be read")
    skew = future_stamp(found.ts, now)
    if skew:
        return unknown(_clock.FUTURE_STAMP, skew)
    if found.tier is not None:
        ttl, source = found.tier, TTL_TIER
    else:
        from moxi.config import ConfigError

        from .settings import current

        try:
            ttl, source = current()["claude.ttl_seconds"], TTL_CONFIG
        except ConfigError as e:
            return unknown(_clock.CONFIG_ERROR, f"cache's config could not be read ({e})")
    age = max(0.0, now - found.ts)
    return Clock(COLD if age >= ttl else WARM, age_seconds=age, ttl_seconds=ttl,
                 ttl_source=source)


__all__ = ["HARNESS", "TIER_1H", "TIER_5M", "Read", "tier_of", "last_request", "read"]
