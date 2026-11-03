"""The one outcome table of a confirmed act's answer, as the gateway's commit uses it.

`client.js`'s `confirm()` maps an answer at the desk by `outcomeOf` in
`web/src/confirm-model.ts`; this is the same table in Python, so an act through the gateway
gets the outcome the same answer gets at the desk. A test drives both copies from one set
of vectors (`web/test/outcome-vectors.json`), so the two stay in step.

- the module's 202 is `accepted` with its status path (a 202 without one is `unknown`);
- the module's 4xx is `refused` with its status and its text;
- a 503 is `refused`: the module's own is sent before any handler runs, and the shell's
  `stopping` means the act was never sent;
- the shell's 502 `start` or `unreachable` is `refused`: the act was never sent;
- the shell's 502 `upstream` or 504, a 500 or any other 5xx, no answer at all (status 0),
  and any answer the table doesn't name are `unknown`, since the module may have acted.
"""

from __future__ import annotations

from typing import Any

__all__ = ["MAY_HAVE_ACTED", "NOT_SENT", "SIGNED_OUT", "outcome_of"]

#: The text of a refusal that reached no module.
NOT_SENT = "the module didn't get the act"
#: The text of an outcome nobody can know from the answer.
MAY_HAVE_ACTED = "the module may have acted; check its page"
#: The text of a refusal because the caller holds no session key.
SIGNED_OUT = "this browser is signed out of Moxi"


def _said(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    for name in ("refused", "conflict", "error"):
        value = data.get(name)
        if isinstance(value, str) and value:
            return value
    return None


def _refusal_text(code: int, data: Any) -> str:
    text = _said(data)
    if text is not None:
        return text
    if code == 403:
        return SIGNED_OUT
    if code == 404:
        return "the module has no such act now"
    if code == 413:
        return "the request was too large for the module"
    return f"the module refused the act ({code})"


def _proxy_error(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    error = data.get("error")
    return error if isinstance(error, str) else None


def outcome_of(status: int, data: Any) -> dict[str, Any]:
    """The outcome of one answer: `status` is the HTTP status, 0 when no answer came, and
    `data` the parsed JSON body or None. Gives `{"outcome": "accepted", "status"}`,
    `{"outcome": "refused", "code", "text"}` or `{"outcome": "unknown", "text"}`."""
    if status == 202:
        path = data.get("status") if isinstance(data, dict) else None
        if isinstance(path, str):
            return {"outcome": "accepted", "status": path}
        return {"outcome": "unknown", "text": MAY_HAVE_ACTED}
    if 400 <= status < 500:
        return {"outcome": "refused", "code": status, "text": _refusal_text(status, data)}
    if status == 503:
        if _proxy_error(data) == "stopping":
            return {"outcome": "refused", "code": status, "text": NOT_SENT}
        return {"outcome": "refused", "code": status,
                "text": "the module was too busy to take the act; try again"}
    if status == 502 and _proxy_error(data) in ("start", "unreachable"):
        return {"outcome": "refused", "code": status, "text": NOT_SENT}
    return {"outcome": "unknown", "text": MAY_HAVE_ACTED}
