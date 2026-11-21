"""The model a relaunch comes back on.

    choose(row, session_id)      # the pin: the row's model, the transcript's, or none

A launch whose values name a `model` keeps it (`how` `row`), and nothing is read. Any
other launch is pinned to the model its session last used: sessions' reader finds the
session's transcript under the root the launch writes to (`joiner.transcripts_root`),
reads the newest request of each source it finds (a synthetic request passed over), and
takes the newest of those by its time, a request whose time is unknown ranking below any
with one and the later source winning a tie. The model is the request's `model`, else its
`response_model`, as written, date suffix and all (`how` `pinned`).

The id is pinned only when it is one token of a model id's shape (`ID_SHAPE`); no list of
models is asked. Otherwise, and when nothing can be read, the launch runs the harness's
default model, and `how` is `default:` with the reason: `no-sessions` (sessions is not
installed or does not load), `transcript-unreadable` (the session was not found, every
source found is compressed, or none could be read), `no-model-line` (the newest request
names no model) or `bad-id` (the id read is not of a model id's shape).

The pin goes to the relaunch alone: `api.ReplayRequest.model_pin` sets the new launch's
model only when the stored values name none, and the new row's values keep the stored
model, so a resume of the relaunched launch runs the row's own model.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from . import store

#: The model a row's values name.
ROW = "row"
#: The model read from the session's transcript.
PINNED = "pinned"
#: No model: the harness's default; `how` is this, a colon and the reason.
DEFAULT = "default"
NO_SESSIONS = "no-sessions"
TRANSCRIPT_UNREADABLE = "transcript-unreadable"
NO_MODEL_LINE = "no-model-line"
BAD_ID = "bad-id"
#: The model Claude Code names on a request it made up itself.
SYNTHETIC = "<synthetic>"
#: The shape of a model id the pin takes: one token, at most 128 characters.
ID_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}")

#: Stands for sessions' API, looked up when it is needed.
LOOK: Any = object()


@dataclass(frozen=True)
class Pin:
    """The model a relaunch runs: `model` (None runs the default), `how` it was chosen
    (`row`, `pinned`, or `default:<reason>`), and `read`, the id as the transcript gave it
    (None when nothing was read)."""

    model: str | None
    how: str
    read: str | None = None

    @property
    def pinned(self) -> bool:
        return self.how == PINNED

    def detail(self) -> dict[str, Any]:
        """The pin as a run event's detail."""
        return {"model": self.model, "how": self.how, "read": self.read}

    @classmethod
    def of(cls, detail: Mapping[str, Any]) -> Pin:
        """A pin from a run event's detail."""
        model, read = detail.get("model"), detail.get("read")
        return cls(model if isinstance(model, str) and model else None,
                   str(detail.get("how") or f"{DEFAULT}:{NO_MODEL_LINE}"),
                   read if isinstance(read, str) else None)


def default(reason: str, read: str | None = None) -> Pin:
    return Pin(None, f"{DEFAULT}:{reason}", read)


def shaped(model_id: str) -> bool:
    """Whether `model_id` has a model id's shape (`ID_SHAPE`)."""
    return ID_SHAPE.fullmatch(model_id) is not None


def choose(row: store.LaunchRow, session_id: str | None, *, sessions: Any = LOOK,
           env: Mapping[str, str] | None = None) -> Pin:
    """The model a relaunch of `row`'s session `session_id` runs. Never raises for what
    the transcript holds or whether it can be read. `sessions` stands in for sessions'
    interface 2 (None: not installed)."""
    named = row.values.get("model")
    if isinstance(named, str) and named:
        return Pin(named, ROW)
    if sessions is LOOK:
        from . import joiner

        sessions = joiner.sessions_api()
    if sessions is None:
        return default(NO_SESSIONS)
    raw = read_model(sessions, row, session_id, os.environ if env is None else env)
    if isinstance(raw, Pin):
        return raw
    if not shaped(raw):
        return default(BAD_ID, raw)
    return Pin(raw, PINNED, raw)


def read_model(sessions: Any, row: store.LaunchRow, session_id: str | None,
               env: Mapping[str, str]) -> str | Pin:
    """The model the session's newest request names, or the default pin saying why there
    is none."""
    from . import joiner

    if not session_id:
        return default(TRANSCRIPT_UNREADABLE)
    try:
        root = joiner.transcripts_root(sessions, row, env)
        found = sessions.find(row.harness, session_id, root=root)
    except Exception:  # noqa: BLE001  (a find that fails is a transcript not read)
        return default(TRANSCRIPT_UNREADABLE)
    newest: tuple[tuple[int, float], Any] | None = None
    for hit in found.hits:
        if hit.compressed:
            continue
        try:
            request = sessions.last_request(hit.source).request
        except Exception:  # noqa: BLE001  (a source that can't be read is passed over)
            continue
        if request is None:
            newest = newest or ((-1, 0.0), None)
            continue
        rank = _rank(request)
        if newest is None or rank >= newest[0]:
            newest = (rank, request)
    if newest is None:
        return default(TRANSCRIPT_UNREADABLE)
    request = newest[1]
    if request is None:
        return default(NO_MODEL_LINE)
    for value in (getattr(request, "model", None), getattr(request, "response_model", None)):
        if isinstance(value, str) and value and value != SYNTHETIC:
            return value
    return default(NO_MODEL_LINE)


def _rank(request: Any) -> tuple[int, float]:
    """A request's place by its time: any known time above an unknown one."""
    when = getattr(request, "time", None)
    if isinstance(when, bool) or not isinstance(when, (int, float)):
        return (0, 0.0)
    return (1, float(when))


__all__ = ["BAD_ID", "DEFAULT", "ID_SHAPE", "LOOK", "NO_MODEL_LINE", "NO_SESSIONS", "PINNED",
           "ROW", "SYNTHETIC", "TRANSCRIPT_UNREADABLE", "Pin", "choose", "default",
           "read_model", "shaped"]
