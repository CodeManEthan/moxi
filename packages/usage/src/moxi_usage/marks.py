"""The warn-at marks as a caller outside the session sees them, and the sweep of their files.

The listener (`moxi_usage.mark`) runs in each session's endpoint and keeps its state
there; on disk it keeps only each session's fired purposes. This module reads those:

* `mark(session_id, *, harness=None)`: the marks in force for the session's notice and
  crossing, their thresholds for the session's model, its current context, and each
  purpose's state (`armed`, `fired` with when, or `off`), as a `MarkState`; the fired
  notices of its agents in `agents`. The marks in force are read from the live session's
  own environment through turns when turns is installed and the session is live, else
  from what the session's file recorded at its last firing, else from config
  (`marks.default` for the notice; the crossing is off). `marks_from` says which.
* `fired()`: every session's file of fired purposes (`Fired`).
* `sweep(*, now)`: delete fired-mark files older than `marks.max_age_days` and the
  ceiling's agent markers older than `ceiling.max_age_days`, for the doctor's `marks` row.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from moxi_sessions.api import UNKNOWN

from . import mark as listener
from .thresholds import MAIN

#: Where the marks in force were read from.
FROM_SESSION = "session"
FROM_RECORD = "record"
FROM_CONFIG = "config"
#: A purpose's states.
ARMED, FIRED, OFF = "armed", "fired", "off"
#: The ceiling's markers, under usage's state directory.
CEILING_DIR = "ceiling"
_DAY = 86400.0


@dataclass(frozen=True)
class PurposeState:
    """One purpose of the main session: its mark (`off` for none), the mark's threshold
    in tokens for the session's model (`UNKNOWN` with `reason` when it can't be said),
    and its state, `fired_at` set when fired."""

    purpose: str
    mark: str
    state: str
    threshold: Any = UNKNOWN
    fired_at: float | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"purpose": self.purpose, "mark": self.mark,
                               "state": self.state}
        if self.threshold is UNKNOWN:
            if self.mark != OFF:
                out["unknown"] = ["threshold"]
        else:
            out["threshold"] = self.threshold
        if self.fired_at is not None:
            out["fired_at"] = self.fired_at
        if self.reason is not None:
            out["reason"] = self.reason
        return out


@dataclass(frozen=True)
class MarkState:
    """A session's marks: `purposes` (the notice, then the crossing), the context they
    are measured against (`tokens` and `model`, `UNKNOWN` when it can't be read, with
    `reason`), and `agents`, each agent's fired purposes by agent id."""

    session_id: str
    harness: str | None
    marks_from: str
    purposes: tuple[PurposeState, ...]
    tokens: Any = UNKNOWN
    model: Any = UNKNOWN
    agents: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    path: str | None = None
    reason: str | None = None

    def purpose(self, name: str) -> PurposeState:
        return next(p for p in self.purposes if p.purpose == name)

    def lines(self) -> list[str]:
        """The text form: one line for the context, one per purpose, one per agent."""
        if self.tokens is UNKNOWN:
            out = [f"context unknown: {self.reason}"]
        else:
            model = self.model if self.model is not UNKNOWN else "model unknown"
            out = [f"context {self.tokens:,} ({model})"]
        for p in self.purposes:
            if p.mark == OFF:
                out.append(f"{p.purpose}: off")
                continue
            limit = (f"{p.threshold:,}" if p.threshold is not UNKNOWN
                     else f"threshold unknown: {p.reason}")
            state = p.state if p.fired_at is None else f"fired {_utc(p.fired_at)}"
            out.append(f"{p.purpose}: {p.mark} at {limit}, {state}")
        for agent_id, purposes in sorted(self.agents.items()):
            for name, at in sorted(purposes.items()):
                out.append(f"agent {agent_id}: {name} fired {_utc(at)}")
        out.append(f"marks from {self.marks_from}")
        return out

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"session_id": self.session_id, "harness": self.harness,
                               "marks_from": self.marks_from,
                               "purposes": [p.as_dict() for p in self.purposes],
                               "agents": {k: dict(v) for k, v in self.agents.items()},
                               "path": self.path}
        unknown = []
        for name in ("tokens", "model"):
            value = getattr(self, name)
            if value is UNKNOWN:
                unknown.append(name)
            else:
                out[name] = value
        if self.reason is not None:
            out["reason"] = self.reason
        if unknown:
            out["unknown"] = unknown
        return out


@dataclass(frozen=True)
class Fired:
    """One session's file of fired purposes: `readers` maps `main` or an agent id to its
    fired purposes, each `{mark, fired_at, tokens, threshold, model}`."""

    path: str
    harness: str | None
    session_id: str | None
    updated_at: float | None
    marks: Mapping[str, str]
    readers: Mapping[str, Mapping[str, Mapping[str, Any]]]

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "harness": self.harness, "session_id": self.session_id,
                "updated_at": self.updated_at, "marks": dict(self.marks),
                "readers": {k: dict(v) for k, v in self.readers.items()}}


# --- one session ------------------------------------------------------------------------

def _live_marks(session_id: str) -> dict[str, str | None] | None:
    """The session's own stamps, read from its live endpoint through turns; None when
    turns is absent, the session is not live, or its endpoint can't say."""
    from moxi import modules

    if not modules.available("turns"):
        return None
    found = modules.load("turns")
    if not found.ok or found.api is None:
        return None
    try:
        session = found.api.find(session_id=session_id)
        if session is None:
            return None
        with session:
            return session.environ([listener.WARN_AT, listener.CROSSING_AT])
    except Exception:  # noqa: BLE001 - an endpoint that can't say leaves the record
        return None


def _record(session_id: str, harness: str | None,
            env: Mapping[str, str] | None) -> tuple[dict | None, str | None, str | None]:
    """The session's fired-purposes file: (document, path, harness)."""
    for name in ((harness,) if harness else ("claude", "codex", "opencode")):
        path = listener.state_file(name, session_id, env)
        if path is None:
            continue
        doc = listener.read_record(path)
        if doc is not None:
            return doc, str(path), name
    return None, None, None


def _in_force(session_id: str, doc: dict | None,
              live: dict | None) -> tuple[str, str, str]:
    """(notice mark, crossing mark, where they came from), each mark `off` for none."""
    if live is not None:
        notice, crossing = listener.selected(live)
        return notice or OFF, crossing or OFF, FROM_SESSION
    marks = doc.get("marks") if isinstance(doc, dict) else None
    if isinstance(marks, dict):
        notice, crossing = marks.get(listener.NOTICE), marks.get(listener.CROSSING)
        return (notice if notice in listener.MARKS else OFF,
                crossing if crossing in listener.MARKS else OFF, FROM_RECORD)
    return listener.config_default() or OFF, OFF, FROM_CONFIG


def mark(session_id: str, *, harness: str | None = None,
         env: Mapping[str, str] | None = None, live: bool = True) -> MarkState:
    """The main session's marks in force, their thresholds, its current context and each
    purpose's state; its agents' fired purposes. `live=False` skips asking turns for the
    session's own stamps. Raises `moxi.config.ConfigError` when a config can't be read."""
    from .context import context
    from .thresholds import threshold

    ctx = context(session_id, harness=harness, env=env)
    held_by = ctx.harness or harness
    doc, path, file_harness = _record(session_id, held_by, env)
    held_by = held_by or file_harness
    notice, crossing, marks_from = _in_force(
        session_id, doc, _live_marks(session_id) if live else None)
    readers = doc.get("readers") if isinstance(doc, dict) else None
    readers = readers if isinstance(readers, dict) else {}
    main = readers.get(listener.MAIN_READER)
    main = main if isinstance(main, dict) else {}
    model = ctx.model
    purposes = []
    for name, wanted in ((listener.NOTICE, notice), (listener.CROSSING, crossing)):
        if wanted == OFF:
            purposes.append(PurposeState(name, OFF, OFF))
            continue
        found = threshold(model if model is not UNKNOWN else None, wanted, MAIN, env=env)
        limit = found.tokens if found.tokens is not None else UNKNOWN
        rec = main.get(name)
        fired_at = (rec.get("fired_at") if isinstance(rec, dict) and rec.get("mark") == wanted
                    else None)
        fired_at = float(fired_at) if isinstance(fired_at, (int, float)) else None
        purposes.append(PurposeState(name, wanted, FIRED if fired_at is not None else ARMED,
                                     limit, fired_at, found.reason))
    agents = {}
    for key, value in readers.items():
        if key == listener.MAIN_READER or not isinstance(value, dict):
            continue
        times = {name: float(rec["fired_at"]) for name, rec in value.items()
                 if isinstance(rec, dict) and isinstance(rec.get("fired_at"), (int, float))}
        if times:
            agents[key] = times
    return MarkState(session_id, held_by, marks_from, tuple(purposes), tokens=ctx.tokens,
                     model=model, agents=agents, path=path,
                     reason=ctx.reason if ctx.tokens is UNKNOWN else None)


# --- every session ----------------------------------------------------------------------

def fired(env: Mapping[str, str] | None = None) -> list[Fired]:
    """Every session's file of fired purposes, newest first."""
    directory = listener.state_dir(env)
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith(".json"))
    except OSError:
        return []
    out = []
    for name in names:
        path = directory / name
        doc = listener.read_record(path)
        if doc is None:
            continue
        readers = doc.get("readers") if isinstance(doc.get("readers"), dict) else {}
        marks = doc.get("marks") if isinstance(doc.get("marks"), dict) else {}
        updated = doc.get("updated_at")
        out.append(Fired(str(path), doc.get("harness"), doc.get("session_id"),
                         float(updated) if isinstance(updated, (int, float)) else None,
                         marks, readers))
    out.sort(key=lambda f: -(f.updated_at or 0.0))
    return out


@dataclass(frozen=True)
class Swept:
    """What one sweep deleted, and the fired-mark files it kept."""

    marks: int = 0
    ceiling: int = 0
    kept: int = 0
    problems: tuple[str, ...] = ()


def sweep(*, now: float, marks_days: float, ceiling_days: float,
          env: Mapping[str, str] | None = None) -> Swept:
    """Delete the fired-mark files (and their lock files) last changed more than
    `marks_days` days before `now`, and each agent's ceiling markers,
    `ceiling/<session>/<agent>/`, last changed more than `ceiling_days` before it; a
    session directory left empty goes too. Never raises: a file that can't be deleted is
    named in `problems`."""
    problems: list[str] = []
    swept_marks = kept = swept_ceiling = 0
    directory = listener.state_dir(env)
    try:
        names = os.listdir(directory)
    except FileNotFoundError:
        names = []
    except OSError as exc:
        names = []
        problems.append(f"{directory}: {exc}")
    oldest = now - marks_days * _DAY
    for name in names:
        path = os.path.join(directory, name)
        try:
            st = os.lstat(path)
            if st.st_mtime < oldest:
                os.remove(path)
                swept_marks += name.endswith(".json")
            elif name.endswith(".json"):
                kept += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            problems.append(f"{path}: {exc}")
    from .ceiling import sweep as sweep_ceiling

    swept_ceiling, more = sweep_ceiling(now=now, max_age_days=ceiling_days, env=env)
    problems.extend(more)
    return Swept(swept_marks, swept_ceiling, kept, tuple(problems))


def _utc(epoch: float) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(epoch, datetime.UTC).strftime("%Y-%m-%d %H:%MZ")


__all__ = ["MarkState", "PurposeState", "Fired", "Swept", "mark", "fired", "sweep",
           "FROM_SESSION", "FROM_RECORD", "FROM_CONFIG", "ARMED", "FIRED", "OFF"]
