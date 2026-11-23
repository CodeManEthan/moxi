"""What the lander tells the leads through comms, and the record of every message.

**Through comms, when it is installed.** comms is an optional module of rounds. The
lander runs outside every session, so it sends with comms' `send_as` under `SENDER`,
the send-only name rounds reserves (`module.RESERVED_NAMES`), posture none. Without
comms nothing is sent and nothing is recorded: `moxi rounds next` and `moxi rounds
queue` are then the only places the news shows.

**Only to recorded names.** rounds names no seat: a message goes to a name recorded on
the build (its builder, from its commission), on the round or the project's record (the
round's lead, `leads.of`), or in the rounds config (the project's `director`). Each
name gets its own send, so a name comms refuses does not stop the others. A builder is
sent to only while it can receive (`Teller.can_receive`): comms reads its name `held`
by a running session.

**The record** is one `told` event in the repo's queue file per try (`Teller.tell`):
`key` (the message's own, so it is sent once), `to`, `about`, `text`, and `delivery`,
what became of it:

- one of comms' deliveries (`live`, `unbound`, `waiting`, ...): the message was posted;
  `unbound` means no session holds the name and comms keeps the message for its next;
- `offline`: comms' daemon could not be reached, or could not read the recipient's
  cache. Nothing was sent; the lander tries it again at each pass (`retry`) for
  `UNSENT_S` after its first try, then records it `unsent` and stops trying. A message
  whose reason the lander reads again before each try (the owner's state, for a red
  spell's notice) and finds gone is recorded `unsent` too. While one is still tried
  the lander does not exit (`trying`);
- `refused`: comms refused it (`why` holds its code and words, `never-bound` for a name
  comms has never seen bound); `error`: the send raised. Neither is tried again and
  both read as no delivery;
- `nobody`: there was no name to send to (`why`: no director set, or no lead recorded).
  Recorded whether or not comms is installed, so `queue` and `next` can say so.

`undelivered(queue)` gives the newest record of each send of a message no name
received, which `queue` shows: a message is its `about`, `text` and `member`, so a send
refused beside another of the same text that reached its name is not counted. Old logs
read the same way.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from . import queue_file
from .errors import UnfencedTest

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
#: The identity state comms reads for a name a running session holds.
HELD = "held"
#: Load comms when the teller is made (the default of `comms`).
LOAD = object()


def comms_api() -> Any | None:
    """comms' interface 2, None when comms is not installed or does not load."""
    from moxi import modules

    if not modules.available("comms"):
        return None
    found = modules.load("comms", interface=2)
    return found.api if found.ok else None


def _newest(queue: queue_file.Queue) -> list[dict]:
    """The newest record of each send (each `key`), oldest first."""
    newest: dict[str, dict] = {}
    for one in queue.told:
        newest[str(one.get("key"))] = one
    return list(newest.values())


def message_of(record: dict) -> tuple:
    """The message a `told` record is a send of: its `about`, `text` and `member`."""
    return (record.get("about"), record.get("text"), tuple(record.get("member") or ()))


def undelivered(queue: queue_file.Queue) -> list[dict]:
    """The newest record of each send of a message no name received, oldest first: a
    message any of whose sends reached a name is left out."""
    newest = _newest(queue)
    reached = {message_of(one) for one in newest
               if one.get("delivery") not in NOT_DELIVERED}
    return [one for one in newest if one.get("delivery") in NOT_DELIVERED
            and message_of(one) not in reached]


def trying(queue: queue_file.Queue) -> list[dict]:
    """The sends still being tried: those whose newest record is `offline`."""
    return [one for one in _newest(queue) if one.get("delivery") == OFFLINE]


class Teller:
    """Sends the lander's messages for `repo` and records each. `comms` is comms' API,
    None for none installed, `LOAD` to load it; `clock` gives the time now."""

    def __init__(self, repo: str, *, env=None, comms: Any = LOAD,
                 clock: Callable[[], float] = time.time,
                 say: Callable[[str], None] = print):
        self.repo, self.env, self.clock, self.say = repo, env, clock, say
        self.comms = comms_api() if comms is LOAD else comms

    def can_receive(self, name: str) -> bool:
        """Whether comms reads `name` held by a running session; a name comms does not
        take, a read that raises, or no comms reads as no."""
        if self.comms is None:
            return False
        try:
            return str(self.comms.identity_state(name).state) == HELD
        except UnfencedTest:
            raise
        except Exception:  # noqa: BLE001  (a read that raises reads as no)
            return False

    def record_of(self, key: str) -> dict | None:
        """The newest record of the send `key`, None when there is none."""
        return next((one for one in reversed(queue_file.read(self.repo, self.env).told)
                     if one.get("key") == key), None)

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
        except UnfencedTest:
            raise
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

    def retry(self, *, reread: Callable[[dict, dict], str | None] | None = None) -> None:
        """Try again each message comms could not take, until `UNSENT_S` after its first
        try; past that it is recorded `unsent` and no longer tried. `reread(record,
        fields)`, before each try, reads again what the message depends on: it updates
        the `fields` the try is recorded with, and gives why the message is no longer
        owed (recorded `unsent`, not tried again), or None to try it."""
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
            gone = None if reread is None else reread(one, fields)
            if gone is not None:
                self.record(one["key"], one.get("to"), one.get("about"), one.get("text"),
                            UNSENT, why=gone, **fields)
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
           "NOBODY", "NOT_DELIVERED", "HELD", "LOAD", "comms_api", "message_of",
           "undelivered", "trying", "Teller"]
