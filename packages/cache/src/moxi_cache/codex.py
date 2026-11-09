"""The Codex clock: the end of a thread's last request, read from its rollout.

**THE EVIDENCE RULE.** A Codex cache reads `warm` or `fresh` only from a whole-history
rollout recognised line by line, in the format of the Codex version the reader was checked
against. Anything it does not recognise is `unknown`. Recognition is a whitelist, never a
deny-list.

* **Finding it.** sessions' `find` lists every file under `<cache_source>/*/*/*/` whose
  name parses by Codex's own rule as `rollout-<timestamp>-<thread>[_<rollout id>].jsonl[.zst]`
  for this thread, at every read. A root that holds none (`miss`): no rollout. A root it
  cannot list: `unknown`. One `.jsonl`: read. One `.jsonl.zst`: Codex compressed it after
  7 idle days, so `cold` with no age. More than one: the thread's history is split (a
  revert, or one rollout in two states), `unknown`.
* **The whole-history check, on line 1.** Line 1's records are one `session_meta` marker,
  its `id` and `session_id` are the thread id, its `history_mode` is `legacy` or
  `paginated`, and it has no payload key off sessions' `META_KEYS` (`unlisted_keys` is
  empty). A key that refers to history kept elsewhere (`history_base`, `forked_from_id`,
  ...) is not on that list, and neither is any key not seen.
* **Reading it.** Backwards through sessions' `records_back` in 64 KB blocks, to the
  earliest record the verdict needs, and line 1 (`read_head=True`), with a 64 MB limit. The
  read asks for every kind of record, because the evidence rule must see every line. A
  `malformed` record, or an `other` record sessions' lists do not recognise, is `unknown`.
  The one exception is a last line with no newline that does not decode: Codex is writing
  it, and it is skipped.
* **The last request** is the newest request from a `token_usage_record` whose `thread_id`
  and `session_id` are both the thread id: the thread's own cache key. Its time, the end
  of the response, is the clock's stamp. A guardian review or an internal child uses
  another key and never moves the clock.
* **The two pairs.** The last model is the `model` of the record's own turn's
  `turn_context`; the last provider is the latest `thread_settings_applied` of this thread
  at or before the record, else line 1's `model_provider`. Both are read from the settings
  markers the read covered, not from the request's own model and provider: a backward
  read sees no settings line older than what it covered, so line 1's provider is used only
  when the read reached line 1. The next model and provider are the latest of each in the
  whole file. Pairs that differ read `cold`; equal pairs are looked up in the lifetime
  table.
* **`fresh`** only for a thread the launcher's own start attempt created after a read
  that found no record (`turned` false), with no rollout yet or a whole-history rollout of
  fresh kinds alone, made by a Codex version the reader was checked against; or, with no
  thread id at all, on the caller's word that its attempt starts one (`new_session`).

**Drift.** On a read whose last pair is on a measured row, the scan also judges the
thread's newest turns: a turn whose first request found the cache cold (cached tokens
under half of the previous request's input) at a gap the row still counted warm lowers
that model's lifetime (`judge_drift`). The read reports the observation on the `Clock`; a
learning drift store writes it before the read returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from moxi_sessions.api import claude as transcripts
from moxi_sessions.api import codex as rollouts
from moxi_sessions.api import find, records_back

from . import clock as _clock
from . import table as _table
from .clock import (COLD, FRESH, MISSING, NO_CACHE, WARM, Clock, bad_id, free_reason,
                    future_stamp, no_row, pair_text, span, unknown)
from .drift import DriftView

HARNESS = "codex"

#: Drift: the smallest previous prompt an observation is made from, and the share of it a
#: request must read from the cache to count as warm.
DRIFT_MIN_INPUT = 16_384
DRIFT_SHARE = 0.5
#: A cold landing sets the lifetime to this share of the gap, rounded down to the minute.
DRIFT_LOWER_TO = 0.8

#: Why a malformed record is skipped when it is the last line, unterminated: one that does
#: not decode is the line Codex is still writing.
BEING_WRITTEN = "undecodable"

# The reasons.
NO_SESSION_YET = "no thread id reported yet"
NO_ROOT = "no Codex sessions directory was named for this thread"
SPLIT = "this thread's history is split across more than one rollout file"
COMPRESSED = "its rollout has been idle at least 7 days"
NOT_WHOLE = "this rollout is not the thread's whole history"
UNRECOGNISED = "the rollout has a line the reader does not recognise"
NOT_OWN = "the rollout has requests, none under this thread's own key"
OLDER_CODEX = ("this rollout was written by a Codex older than 0.153.0, whose lines are not "
               "read as a clock")
NO_REQUEST = "this thread shows no request, but nothing confirms it has made none"
NO_PAIR = "which model the next request will use cannot be told"
CHANGED = ("the model or provider changed since the last request, so the next request "
           "starts a cache of its own")
OTHER_THREAD = "the rollout has a settings line of another thread"
NO_STAMP = "the rollout's last request has no readable time"
NO_DRIFT = ("no cache drift record was given, so the measured lifetime cannot be read")


# --------------------------------------------------------------------------- records

def _turn(rec):
    """The turn a record names, or None."""
    return rec.turn_id if isinstance(rec.turn_id, str) else None


def _ts(rec) -> float | None:
    t = rec.time
    if isinstance(t, (int, float)) and not isinstance(t, bool):
        return float(t)
    return None


def _iso(t: float | None) -> str | None:
    if t is None:
        return None
    moment = datetime.fromtimestamp(t, timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _marker(rec, *names) -> bool:
    return rec.kind == "marker" and rec.marker in names


def _is_tsa(rec) -> bool:
    return _marker(rec, "settings") and rec.raw_kind == "event_msg/thread_settings_applied"


def _is_context(rec) -> bool:
    return _marker(rec, "settings") and rec.raw_kind == "turn_context"


def _is_record(rec) -> bool:
    """A request from a `token_usage_record`. An older Codex's request, read from a
    `token_count` event, is not one."""
    return rec.kind == "request" and rec.raw_kind == "token_usage_record"


def _own(rec, thread_id: str) -> bool:
    return (_is_record(rec) and rec.extra.get("thread_id") == thread_id
            and rec.extra.get("session_id") == thread_id)


def _is_start(rec) -> bool:
    return _marker(rec, "turn_start")


def _is_token_count(rec) -> bool:
    return rec.raw_kind == "event_msg/token_count"


def _breaks_continuation(rec) -> bool:
    """A line after which the next request may read a low cache for a reason other than
    expiry: a compaction, a rollback, an aborted turn."""
    return _marker(rec, "compaction", "rollback", "turn_aborted")


def _unrecognised(rec) -> bool:
    return (rec.kind == "malformed"
            or (rec.kind == "other" and not rec.recognised))


@dataclass
class Scan:
    """What one read covered: line 1's marker, and the covered records oldest first."""
    meta: object = None
    lines: list = field(default_factory=list)
    reached_start: bool = False
    hit_limit: bool = False


# --------------------------------------------------------------------------- the read

def read(thread_id, *, cache_source, turned, new_session, now, drift=MISSING,
         bound_at=None, judged=None, limit=None) -> Clock:
    """One Codex thread's clock. `drift` is the lowerings in force (a `DriftView`, a
    mapping, or MISSING, which reads a measured row `unknown`); `judged` the newest turn id
    already judged for drift on this thread, or None. Raises nothing the caller does not
    catch."""
    try:
        table = _table.load_table()
    except _table.TableError as e:
        return unknown(_clock.TABLE_NOT_LOADED, f"{_table.NOT_LOADED} ({e})")
    if not thread_id:
        if new_session:
            return Clock(FRESH)          # no thread yet: the launcher's own attempt starts it
        return unknown(_clock.NO_SESSION_YET, NO_SESSION_YET)
    if not isinstance(thread_id, str) or not transcripts.SAFE_ID_RE.match(thread_id):
        return bad_id("thread id", thread_id)
    if not cache_source:
        return unknown(_clock.NO_SOURCE, NO_ROOT)
    root = Path(cache_source)
    found = find(HARNESS, thread_id, root=root)
    if not found.found:
        if found.probes and found.probes[0].outcome == transcripts.MISS:
            if not turned:
                return Clock(FRESH)      # the launcher's own new thread: no rollout yet
            return unknown(_clock.NO_RECORD, f"no rollout for thread {thread_id} under "
                                             f"{root}, and nothing shows it has made no "
                                             "request")
        return unknown(_clock.SOURCE_UNREADABLE,
                       f"the Codex sessions directory {root} is missing or unreadable")
    hits = found.hits
    if len(hits) > 1:
        return unknown(_clock.SPLIT, SPLIT)
    if hits[0].compressed:
        return Clock(COLD, reason=COMPRESSED, code=_clock.COMPRESSED)
    limit = _clock.CLOCK_SCAN_LIMIT if limit is None else limit
    try:
        with records_back(hits[0].source, read_head=True, block=_clock.CLOCK_BLOCK,
                          limit=limit) as back:
            return _read_open(back, thread_id, table=table, turned=turned, now=now,
                              drift=drift, bound_at=bound_at, judged=judged)
    except OSError as e:
        cause = e.__cause__ if isinstance(e.__cause__, OSError) else e
        return unknown(_clock.UNREADABLE,
                       f"the rollout could not be read ({cause.strerror or cause})")


def _whole_history(first, thread_id: str):
    """Line 1's `session_meta` marker when it shows the thread's whole history, else
    None."""
    if not first or len(first) != 1:
        return None
    meta = first[0]
    if not _marker(meta, "session_meta"):
        return None
    if meta.fields.get("unlisted_keys"):
        return None
    if meta.extra.get("id") != thread_id or meta.extra.get("session_id") != thread_id:
        return None
    if meta.fields.get("history_mode") not in rollouts.HISTORY_MODES:
        return None
    return meta


class _Need:
    """When the backwards scan has covered every record the verdict needs: the last own
    record, its turn's `turn_context`, and a settings line at or before it (else the scan
    runs to line 1). With drift on, also the previous own record of the oldest turn to
    judge, that record's turn start, and a settings line at or before it."""

    def __init__(self, thread_id, judged):
        self.thread_id = thread_id
        self.judged = judged
        self.record = None
        self.context = False
        self.settings_before = False
        self.drift = False
        self.newest_turn = None
        self.boundary_seen = False
        self.prev_record = None
        self.prev_start = False
        self.prev_settings = False

    def feed(self, rec) -> None:
        if self.record is None:
            if _own(rec, self.thread_id):
                self.record = rec
            return
        if _is_context(rec) and _turn(rec) == _turn(self.record):
            self.context = True
        if _is_tsa(rec):
            self.settings_before = True
        if self.drift:
            self.feed_drift(rec)

    def feed_drift(self, rec) -> None:
        turn = _turn(rec)
        if self.prev_record is None:
            if not self.boundary_seen:
                if self.judged is None:
                    if _is_start(rec) and turn == self.newest_turn:
                        self.boundary_seen = True
                    return
                if turn is not None and turn == self.judged:
                    self.boundary_seen = True
            if self.boundary_seen and _own(rec, self.thread_id):
                self.prev_record = rec
            return
        if _is_start(rec) and turn == _turn(self.prev_record):
            self.prev_start = True
        if _is_tsa(rec):
            self.prev_settings = True

    def done(self) -> bool:
        if self.record is None or not self.context or not self.settings_before:
            return False
        if not self.drift:
            return True
        return self.prev_record is not None and self.prev_start and self.prev_settings


def _limit_mb() -> int:
    return _clock.CLOCK_SCAN_LIMIT // (1024 * 1024)


def _read_open(back, thread_id, *, table, turned, now, drift, bound_at, judged) -> Clock:
    meta = _whole_history(back.first, thread_id)
    if meta is None:
        return unknown(_clock.NOT_WHOLE, NOT_WHOLE)
    need = _Need(thread_id, judged)
    newest_first: list = []
    measured_checked = False
    for rec in back:
        if _unrecognised(rec):
            if rec.kind == "malformed" and rec.unterminated and rec.why == BEING_WRITTEN:
                continue                 # the line Codex is writing as the read runs
            return unknown(_clock.UNRECOGNISED, UNRECOGNISED)
        if _marker(rec, "session_meta"):
            return unknown(_clock.NOT_WHOLE, NOT_WHOLE)
        if _is_tsa(rec) and rec.extra.get("thread_id") != thread_id:
            return unknown(_clock.OTHER_THREAD, OTHER_THREAD)
        newest_first.append(rec)
        need.feed(rec)
        if (need.record is not None and need.context and need.settings_before
                and not measured_checked):
            measured_checked = True
            need.newest_turn = _turn(need.record)
            if _last_pair_measured(newest_first, meta, thread_id, table):
                need.drift = True
                _refeed_for_drift(need, newest_first)
        if need.done():
            break
    reached_start = back.ended == "start"
    lines = list(reversed(newest_first))
    scan = Scan(meta=meta, lines=lines, reached_start=reached_start,
                hit_limit=back.ended == "limit")
    if need.record is None:
        if not reached_start:
            return unknown(_clock.SCAN_LIMIT, f"no request found in the last {_limit_mb()} MB "
                                              "of the rollout")
        return _no_record(scan, thread_id, turned)
    if not (need.context and need.settings_before) and not reached_start:
        return unknown(_clock.SCAN_LIMIT, f"the rollout's last {_limit_mb()} MB end before "
                                          "the evidence the reader needs")
    return _clock_from(scan, need.record, thread_id, table=table, now=now, drift=drift,
                       bound_at=bound_at, judged=judged)


def _refeed_for_drift(need: _Need, newest_first: list) -> None:
    """Drift was switched on mid-scan: replay the records already read past the record."""
    past = False
    for rec in newest_first:
        if past:
            need.feed_drift(rec)
        elif rec is need.record:
            past = True


def _no_record(scan: Scan, thread_id, turned) -> Clock:
    """The whole file read and no record of the thread's own."""
    if any(_is_record(rec) for rec in scan.lines):
        return unknown(_clock.NOT_OWN, NOT_OWN)
    if any(_is_token_count(rec) for rec in scan.lines):
        return unknown(_clock.OLDER_HARNESS, OLDER_CODEX)
    if turned:
        return unknown(_clock.NO_REQUEST, NO_REQUEST)
    # The Codex versions sessions' mapping was checked against are the ones whose new
    # threads read as `fresh`.
    if scan.meta.fields.get("cli_version") not in rollouts.CHECKED_VERSIONS:
        return unknown(_clock.NO_REQUEST, NO_REQUEST)
    if all(_fresh_kind(rec, thread_id) for rec in scan.lines):
        return Clock(FRESH)
    return unknown(_clock.NO_REQUEST, NO_REQUEST)


#: The lines a new thread writes before its first response, by sessions' `raw_kind`, that
#: are fresh whatever they hold.
FRESH_KINDS = ("turn_context", "world_state", "event_msg/task_started",
               "event_msg/user_message")


def _fresh_kind(rec, thread_id) -> bool:
    """A record of a line a new thread writes before its first response."""
    if rec.raw_kind in FRESH_KINDS:
        return True
    if _is_tsa(rec):
        return rec.extra.get("thread_id") == thread_id
    if rec.raw_kind == "event_msg/item_completed":
        item = rec.extra.get("item")
        return isinstance(item, dict) and item.get("type") == "UserMessage"
    if rec.kind == "message" and rec.raw_kind == "response_item/message":
        return rec.role in ("user", "developer")
    return False


def _text(value):
    return value if isinstance(value, str) and value else None


def _pair_at(lines: list, index: int, meta, whole: bool) -> tuple:
    """The (provider, model) the record at `lines[index]` used, by the last-pair rule: the
    model of its own turn's `turn_context`, the provider of the latest settings line at
    or before it, else line 1's. A half that cannot be read is None; so is line 1's
    provider when the covered lines do not reach back to it."""
    record = lines[index]
    turn = _turn(record)
    model = None
    for rec in reversed(lines[:index]):
        if _is_context(rec) and _turn(rec) == turn:
            model = rec.fields.get("model")
            break
    provider = None
    for rec in reversed(lines[:index]):
        if _is_tsa(rec):
            provider = rec.fields.get("provider")
            break
    else:
        provider = meta.fields.get("model_provider") if whole else None
    return _text(provider), _text(model)


def _next_pair(lines: list, meta, whole: bool) -> tuple:
    """The pair the thread's next request will use: the model of the latest
    `turn_context` or settings line, the provider of the latest settings line, else line
    1's."""
    model = provider = None
    for rec in reversed(lines):
        if model is None and (_is_context(rec) or _is_tsa(rec)):
            model = rec.fields.get("model")
            if _text(model) is None:
                return None, None
        if provider is None and _is_tsa(rec):
            provider = rec.fields.get("provider")
            if _text(provider) is None:
                return None, None
        if model is not None and provider is not None:
            break
    if provider is None and whole:
        provider = meta.fields.get("model_provider")
    return _text(provider), _text(model)


def _last_pair_measured(newest_first: list, meta, thread_id, table) -> bool:
    lines = list(reversed(newest_first))
    index = next(i for i in range(len(lines) - 1, -1, -1) if _own(lines[i], thread_id))
    provider, model = _pair_at(lines, index, meta, False)
    if provider is None or model is None:
        return False
    row = table.lookup(HARNESS, provider, model)
    return row is not None and row.measured


def _clock_from(scan: Scan, record, thread_id, *, table, now, drift, bound_at,
                judged) -> Clock:
    lines = scan.lines
    index = next(i for i in range(len(lines) - 1, -1, -1) if lines[i] is record)
    whole = scan.reached_start
    next_provider, next_model = _next_pair(lines, scan.meta, whole)
    if next_provider is None or next_model is None:
        return unknown(_clock.NO_PAIR, NO_PAIR)
    if table.is_free(HARNESS, next_provider, next_model):
        return Clock(NO_CACHE, reason=free_reason(next_provider, next_model),
                     key=f"{HARNESS} {pair_text(next_provider, next_model)} (free)",
                     code=_clock.FREE)
    last_provider, last_model = _pair_at(lines, index, scan.meta, whole)
    if last_provider is None or last_model is None:
        return unknown(_clock.NO_PAIR, NO_PAIR)
    ts = _ts(record)
    if ts is None:
        return unknown(_clock.NO_STAMP, NO_STAMP)
    skew = future_stamp(ts, now)
    if skew:
        return unknown(_clock.FUTURE_STAMP, skew)
    age = max(0.0, now - ts)
    if (last_provider, last_model) != (next_provider, next_model):
        return Clock(COLD, age_s=age, reason=CHANGED, code=_clock.CHANGED)
    row = table.lookup(HARNESS, last_provider, last_model)
    key = f"{HARNESS} {pair_text(last_provider, last_model)}"
    if row is None:
        # A model with no lifetime row has no clock to count by.
        return Clock(NO_CACHE, age_s=age, reason=no_row(last_provider, last_model),
                     code=_clock.NO_ROW)
    if row.free:
        return Clock(NO_CACHE, reason=free_reason(last_provider, last_model),
                     key=f"{key} (free)", code=_clock.FREE)
    if not row.measured:
        return _verdict(age, row.seconds, key=key, basis="documented",
                        provider=last_provider, model=last_model)
    # A measured row: the lifetime in force after drift.
    if drift is MISSING:
        return unknown(_clock.NO_DRIFT_RECORD, NO_DRIFT)
    state = DriftView.of(drift)
    entry_key = (HARNESS, last_provider, last_model)
    blocked = state.blocked_reason(entry_key)
    if blocked:
        return unknown(_clock.DRIFT_HELD, blocked)
    lifetime, lowered_at = state.lifetime(entry_key, row)
    observed = judge_drift(lines, scan.meta, thread_id, row=row,
                           pair=(last_provider, last_model), lifetime=lifetime,
                           bound_at=bound_at, judged=judged, whole=whole)
    if observed.lowered is not None:
        lifetime = observed.lowered["lifetime"]
        lowered_at = observed.lowered["at_iso"]
    clock = _verdict(age, lifetime, key=_measured_key(key, row, lifetime, lowered_at),
                     basis="measured", provider=last_provider, model=last_model,
                     lowered_from=row.seconds if lifetime < row.seconds else None)
    clock.lowering = observed.lowered
    clock.notes = observed.notes
    clock.judged = (thread_id, observed.newest) if observed.newest else None
    return clock


def _measured_key(key, row, lifetime, lowered_at) -> str:
    if lifetime < row.seconds:
        when = f" on {lowered_at[:10]}" if isinstance(lowered_at, str) and lowered_at else ""
        return f"{key} (measured on this box, lowered from {span(row.seconds)}{when})"
    return f"{key} (measured on this box)"


def _verdict(age, lifetime, *, key, basis, provider, model, lowered_from=None) -> Clock:
    kind = COLD if age >= lifetime else WARM
    return Clock(kind, age_s=age, ttl_s=lifetime, key=key, basis=basis,
                 provider=provider, model=model, lowered_from=lowered_from)


# --------------------------------------------------------------------------- drift

@dataclass
class Judged:
    lowered: dict | None = None
    notes: list = field(default_factory=list)
    newest: str | None = None


def lower_to(gap: float, floor: int) -> int:
    """80% of the gap, rounded down to the minute, never below the floor."""
    return max(int(floor), int(gap * DRIFT_LOWER_TO) // 60 * 60)


def judge_drift(lines: list, meta, thread_id, *, row, pair, lifetime, bound_at,
                judged, whole: bool) -> Judged:
    """The drift scan over the covered records: each turn newer than `judged` (the newest
    turn alone when None), oldest first, against the lifetime in force."""
    out = Judged()
    starts = [i for i, rec in enumerate(lines) if _is_start(rec)]
    own = [i for i, rec in enumerate(lines) if _own(rec, thread_id)]
    if not own:
        return out
    # turn id -> index of its first own record
    first_of: dict = {}
    for i in own:
        first_of.setdefault(_turn(lines[i]), i)
    turns = []                           # (start index, first record index), oldest first
    for s in starts:
        r = first_of.get(_turn(lines[s]))
        if r is not None and r > s:
            turns.append((s, r))
    if not turns:
        return out
    out.newest = _turn(lines[turns[-1][1]])
    if judged is None:
        todo = turns[-1:]
    else:
        newer = []
        for s, r in reversed(turns):
            if _turn(lines[s]) == judged:
                break
            newer.append((s, r))
        todo = list(reversed(newer))
    in_force = lifetime
    for s, r in todo:
        obs = _observe(lines, meta, thread_id, s, r, row=row, pair=pair,
                       bound_at=bound_at, whole=whole)
        if obs is None:
            continue
        gap, prev, rec = obs
        if gap < row.floor:
            out.notes.append(
                f"cache clock: {pair[1]} on codex found its cache cold {span(gap)} after "
                f"the request before, under the documented {span(row.floor)}; not counted "
                f"(thread {thread_id}, cached {rec.cache_read_input_tokens} of "
                f"{prev.input_tokens} tokens)")
            continue
        if gap >= in_force:
            continue
        new = lower_to(gap, row.floor)
        if new >= in_force:
            continue
        in_force = new
        out.lowered = {
            "harness": HARNESS, "provider": pair[0], "model": pair[1], "lifetime": new,
            "from": lifetime if out.lowered is None else out.lowered["from"],
            "gap": gap, "thread": thread_id, "response_id": rec.request_id,
            "at": _ts(rec), "at_iso": _iso(_ts(rec)), "prev_at": _ts(prev),
            "prev_at_iso": _iso(_ts(prev)),
            "cached": rec.cache_read_input_tokens, "input": prev.input_tokens,
            "row_seconds": row.seconds, "row_checked": row.checked}
    return out


def _count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _observe(lines, meta, thread_id, s, r, *, row, pair, bound_at, whole):
    """(gap, R_prev, R) when turn (start `s`, first record `r`) is a cold landing at a gap
    that may be counted; None otherwise."""
    if bound_at is None:
        return None                       # no registration time: no observation at all
    prev = None
    for i in range(s - 1, -1, -1):
        if _own(lines[i], thread_id):
            prev = i
            break
    if prev is None:
        return None
    rec, prev_rec, start = lines[r], lines[prev], lines[s]
    if None in (_ts(rec), _ts(prev_rec), _ts(start)):
        return None
    if _pair_at(lines, r, meta, whole) != tuple(pair):
        return None
    if _pair_at(lines, prev, meta, whole) != tuple(pair):
        return None
    prev_turn = _turn(prev_rec)
    prev_start = None
    for i in range(prev, -1, -1):
        if _is_start(lines[i]) and _turn(lines[i]) == prev_turn:
            prev_start = i
            break
    if prev_start is None:
        return None
    for each in lines[prev_start:r]:
        if _breaks_continuation(each):
            return None
    for each in lines[prev + 1:s]:
        if _is_tsa(each):
            return None                   # a resume, or a settings change
    if _ts(prev_rec) < bound_at:
        return None                       # the launcher registered after R_prev: a resume
    inp, cached = prev_rec.input_tokens, rec.cache_read_input_tokens
    if not _count(inp) or not _count(cached):
        return None
    if inp < DRIFT_MIN_INPUT:
        return None
    if cached >= inp * DRIFT_SHARE:
        return None                       # warm
    gap = _ts(start) - _ts(prev_rec)
    return gap, prev_rec, rec


__all__ = ["HARNESS", "read", "judge_drift", "lower_to"]
