"""What the lander tells the leads through comms, and the record of every message.

**Through comms, when it is installed.** comms is an optional module of rounds. The
lander runs outside every session, so it sends with comms' `send_as` under `SENDER`,
the send-only name rounds reserves (`module.RESERVED_NAMES`), posture none. Without
comms nothing is sent and nothing is recorded: `moxi rounds next` and `moxi rounds
queue` are then the only places the news shows.

**Only to recorded names.** rounds names no seat: a message goes to a name recorded on
the build (its builder, from its commission), on the round or the project's record (the
round's lead, `leads.of`), or in the rounds config (the project's `director`). Each
name gets its own send, so a name comms refuses does not stop the others.

**The record** is one `told` event in the repo's queue file per try (`Teller.tell`):
`key` (the message's own, so it is sent once), `to`, `about`, `text`, and `delivery`,
what became of it:

- one of comms' deliveries (`live`, `unbound`, `waiting`, ...): the message was posted;
  `unbound` means no session holds the name and comms keeps the message for its next;
- `offline`: comms' daemon could not be reached, or could not read the recipient's
  cache. Nothing was sent; the lander tries it again at each pass (`retry`) for
  `UNSENT_S` after its first try, then records it `unsent` and stops trying. While one
  is still tried the lander does not exit (`trying`);
- `refused`: comms refused it (`why` holds its code and words, `never-bound` for a name
  comms has never seen bound); `error`: the send raised. Neither is tried again and
  both read as no delivery;
- `nobody`: there was no name to send to (`why`: no director set, or no lead recorded).
  Recorded whether or not comms is installed, so `queue` and `next` can say so.

`undelivered(queue)` gives the newest record of each message that did not reach a
name, which `queue` shows.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from . import queue_file

#: The module the send-only name is reserved for, and the name.
MODULE = "rounds"
SENDER = "rounds"
#: How long after its first try a message comms could not take is tried again.
UNSENT_S = 3600.0
#: What became of a message besides comms' own deliveries.
OFFLINE = "offline"
UNSENT = "unsent"
REFUSED = "refused"
ERROR = "error"
NOBODY = "nobody"
#: The deliveries that reached no name.
NOT_DELIVERED = (OFFLINE, UNSENT, REFUSED, ERROR, NOBODY)
#: comms' outcomes after which nothing was sent and a later try may go.
_TRY_AGAIN = ("offline", "cache-unread")
#: Load comms when the teller is made (the default of `comms`).
LOAD = object()


def comms_api() -> Any | None:
    """comms' interface 2, None when comms is not installed or does not load."""
    from moxi import modules

    if not modules.available("comms"):
        return None
    found = modules.load("comms", interface=2)
    return found.api if found.ok else None


def undelivered(queue: queue_file.Queue) -> list[dict]:
    """The newest record of each message that reached no name, oldest first."""
    newest: dict[str, dict] = {}
    for one in queue.told:
        newest[str(one.get("key"))] = one
    return [one for one in newest.values() if one.get("delivery") in NOT_DELIVERED]


def trying(queue: queue_file.Queue) -> list[dict]:
    """The messages still being tried: those whose newest record is `offline`."""
    return [one for one in undelivered(queue) if one.get("delivery") == OFFLINE]


class Teller:
    """Sends the lander's messages for `repo` and records each. `comms` is comms' API,
    None for none installed, `LOAD` to load it; `clock` gives the time now."""

    def __init__(self, repo: str, *, env=None, comms: Any = LOAD,
                 clock: Callable[[], float] = time.time,
                 say: Callable[[str], None] = print):
        self.repo, self.env, self.clock, self.say = repo, env, clock, say
        self.comms = comms_api() if comms is LOAD else comms

    def tell(self, to: str | None, about: str, key: str, text: str, *,
             why_nobody: str = "", **fields) -> dict | None:
        """Send `text` to `to` once, under `key`, and record what became of it. `to`
        None records `nobody` with `why_nobody`. The record, None when the message was
        recorded already or comms is not installed."""
        queue = queue_file.read(self.repo, self.env)
        if any(one.get("key") == key for one in queue.told):
            return None
        if to is None:
            return self.record(key, to, about, text, NOBODY, why=why_nobody, **fields)
        if self.comms is None:
            return None
        return self.send(key, to, about, text, first_s=self.clock(), **fields)

    def send(self, key: str, to: str, about: str, text: str, *, first_s: float,
             **fields) -> dict:
        """One try of a message, recorded."""
        try:
            result = self.comms.send_as(MODULE, SENDER, [to], text)
        except Exception as exc:  # noqa: BLE001  (a send that raises is recorded)
            return self.record(key, to, about, text, ERROR, why=str(exc) or type(exc).__name__,
                               **fields)
        outcome = getattr(result, "outcome", None)
        if outcome == "sent":
            recipients = getattr(result, "recipients", ()) or ()
            delivery = (getattr(recipients[0], "delivery", None) if recipients else None)
            return self.record(key, to, about, text, delivery or "sent",
                               message_id=getattr(result, "message_id", None), **fields)
        refusal = getattr(result, "refusal", None)
        code = getattr(refusal, "code", None)
        words = getattr(refusal, "text", None) or code or str(outcome)
        if outcome in _TRY_AGAIN:
            return self.record(key, to, about, text, OFFLINE, why=words, first_s=first_s,
                               **fields)
        why = f"{code}: {words}" if code and words != code else words
        return self.record(key, to, about, text, REFUSED, why=why, **fields)

    def retry(self) -> None:
        """Try again each message comms could not take, until `UNSENT_S` after its first
        try; past that it is recorded `unsent` and no longer tried."""
        if self.comms is None:
            return
        for one in trying(queue_file.read(self.repo, self.env)):
            first_s = float(one.get("first_s") or 0)
            fields = {name: value for name, value in one.items()
                      if name not in ("event", "at", "key", "to", "about", "text",
                                      "delivery", "why", "first_s", "message_id")}
            if self.clock() - first_s >= UNSENT_S:
                self.record(one["key"], one.get("to"), one.get("about"), one.get("text"),
                            UNSENT, why=f"comms could not take it for {int(UNSENT_S)} s: "
                            f"{one.get('why')}", **fields)
                continue
            self.send(one["key"], one["to"], one.get("about"), one.get("text"),
                      first_s=first_s, **fields)

    def record(self, key: str, to: str | None, about: str, text: str, delivery: str,
               **fields) -> dict:
        written = queue_file.append(self.repo, {
            "event": queue_file.TOLD, "key": key, "to": to, "about": about, "text": text,
            "delivery": delivery,
            **{name: value for name, value in fields.items() if value is not None}},
            self.env)
        if delivery in NOT_DELIVERED:
            self.say(f"lander of {self.repo}: {about} to {to or 'nobody'}: {delivery}"
                     + (f" ({fields['why']})" if fields.get("why") else ""))
        return written


__all__ = ["MODULE", "SENDER", "UNSENT_S", "OFFLINE", "UNSENT", "REFUSED", "ERROR",
           "NOBODY", "NOT_DELIVERED", "LOAD", "comms_api", "undelivered", "trying",
           "Teller"]
