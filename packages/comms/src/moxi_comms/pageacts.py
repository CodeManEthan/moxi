"""The page's acts, in the page server's process: `check` and `send` as the operator.

Each act runs the send verb's own path (`api.check`, `api.send`) as the operator's lane,
`api.Sender.operator(<his user id>)`, with the posture always given, `new_name` set (the
page offers only names the roster or the record holds) and `ACT_TIMEOUT_S` for every
daemon call. The verb reads his credential from its chain in this process for the one
call and keeps nothing; no answer carries it.

**His user id**: this process's `MOXI_COMMS_OPERATOR` when it is set and not blank, as
the terminal's send takes it; else the directory's one operator row, read on a
read-only connection that is closed before the act goes on. With neither (no row, two or
more, or no `comms.db`), the act refuses with the code `operator-unknown` and posts
nothing. A store that can't be read refuses with `record-unreadable`.

**The answers** carry the verb's outcome and its refusal's code, never its text: the page
words each from the outcome and the code. A request the verb's own parser refuses (a
blank `to`) is `refused` with the code `request-shape`. Any other failure is logged once
per class with its traceback but not its text, and answers the code `page-error`:
`failed` for a check, `unknown` for a send, since a send that broke may have posted.

Inside an agent's session the verb refuses the operator's lane, so `check` and `send`
answer `operator-in-session`, or `operator-and-claim` when this process also carries an
agent's claim.

Like `page`, this module is imported only by the page server and loopback's export.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import traceback
from collections.abc import Callable
from typing import Any

from . import api
from .constants import OPERATOR_ENV
from .paths import comms_db
from .sending import OUTCOME_FAILED, OUTCOME_REFUSED, OUTCOME_UNKNOWN
from .store import open_read

#: Seconds each daemon call of an act may wait, passed to the verb.
ACT_TIMEOUT_S = 10.0

#: The codes the page server answers itself, beside the verb's.
OPERATOR_UNKNOWN = "operator-unknown"
RECORD_UNREADABLE = "record-unreadable"
REQUEST_SHAPE = "request-shape"
PAGE_ERROR = "page-error"

log = logging.getLogger("moxi.comms.page")

_logged: set[type] = set()
_logged_lock = threading.Lock()


def _log_once(err: BaseException, what: str) -> None:
    # The class and the traceback's frames, not the exception's text, which could hold
    # what the verb read.
    with _logged_lock:
        first = type(err) not in _logged
        _logged.add(type(err))
    if first:
        frames = "".join(traceback.format_tb(err.__traceback__))
        log.error("%s (%s)\n%s", what, type(err).__name__, frames)


def operator_id(env=None) -> str | None:
    """The operator's user id the acts send as: `MOXI_COMMS_OPERATOR` in `env` (this
    process's environment by default) when set and not blank, else the directory's one
    operator row; None with neither."""
    env = os.environ if env is None else env
    found = (env.get(OPERATOR_ENV) or "").strip()
    if found:
        return found
    target = comms_db()
    try:
        conn = open_read(target, check_step=True)
    except sqlite3.OperationalError:
        if not target.exists():
            return None
        raise
    try:
        rows = conn.execute("SELECT user_id FROM operators").fetchall()
    finally:
        conn.close()
    return str(rows[0][0]) if len(rows) == 1 else None


def refused(code: str, outcome: str = OUTCOME_REFUSED) -> dict[str, Any]:
    """An answer that posted nothing, with `code`."""
    return {"outcome": outcome, "message_id": None, "recipients": [],
            "refusal": {"code": code}}


def answered(result: api.SendResult) -> dict[str, Any]:
    """`result` as `page.SENT`: the outcome, the message id, each recipient's delivery and
    the refusal's code. The verb's text, its refusal's and each recipient's note, is
    left out."""
    return {
        "outcome": result.outcome,
        "message_id": None if result.message_id is None else str(result.message_id),
        "recipients": [{"qualified": r.qualified, "delivery": r.delivery,
                        "cold_at": None if r.cold_at is None else float(r.cold_at)}
                       for r in result.recipients],
        "refusal": None if result.refusal is None else {"code": result.refusal.code},
    }


def _act(call: Any, verb: Callable[..., api.SendResult], broke: str) -> dict[str, Any]:
    # One check or send as the operator. `broke` is the outcome of a failure past the
    # verb's own answers.
    body = call.body
    try:
        user = operator_id()
    except Exception as exc:
        _log_once(exc, "reading the operator's user id failed")
        return refused(RECORD_UNREADABLE, OUTCOME_FAILED)
    if user is None:
        return refused(OPERATOR_UNKNOWN)
    try:
        result = verb(api.Sender.operator(user), [body["to"]], body["text"],
                      posture=body["posture"], reply_to=body.get("reply_to"),
                      held=body["held"], new_name=True, timeout_s=ACT_TIMEOUT_S)
    except (ValueError, TypeError):
        return refused(REQUEST_SHAPE)
    except Exception as exc:
        _log_once(exc, "the page's send box failed")
        return refused(PAGE_ERROR, broke)
    return answered(result)


def check(call: Any) -> dict[str, Any]:
    """`POST /api/check`: every check `send` makes, as the operator, posting nothing:
    `would-send` with each recipient's delivery, or what a send would come to."""
    return _act(call, api.check, OUTCOME_FAILED)


def send(call: Any) -> dict[str, Any]:
    """`POST /api/send`: the message posted as the operator: `sent` with its id and each
    recipient's delivery, or why nothing was posted."""
    return _act(call, api.send, OUTCOME_UNKNOWN)


__all__ = ["ACT_TIMEOUT_S", "OPERATOR_UNKNOWN", "PAGE_ERROR", "RECORD_UNREADABLE",
           "REQUEST_SHAPE", "answered", "check", "operator_id", "refused", "send"]
