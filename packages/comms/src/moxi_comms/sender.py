"""The send path's identity and the operator's credentialled posting lane.

A 429 is retried honoring Retry-After, bounded (finite attempts, then stop).
Exhaustion is a loud failure at the verb -- the one place a message could actually be
lost, so it never fails quietly.

Logs carry ids only -- never bodies or credentials.

THE OPERATOR'S LANE lives here too. It is a second lane, not a second identity scheme:
`MOXI_COMMS_OPERATOR=<user_id>` is the trigger, a claimed session is never the operator, and
the post presents the `operator` holder's own token exactly as an agent's post presents
its live binding token. `post_as_user` is what the operator reaches.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from . import constants, directory, secrets, tokens
from .gateway import RateLimited
from .provision import NAMES
from .scoping import GLOBAL, qualify

log = logging.getLogger("comms.send")

#: The operator lane's trigger. A stamp the launcher never passes to a
#: session: it is typed in the operator's own terminal and nowhere else.
OPERATOR_VAR = "MOXI_COMMS_OPERATOR"

#: The stamps that say a session is CLAIMED. Either one beside the trigger is a refusal.
CLAIM_VARS = ("MOXI_COMMS_NAME",)


class SendFailed(Exception):
    """Loud. The message was NOT posted."""


class OperatorRefused(Exception):
    """The operator's stamp and an agent's claim in one environment.

    Letting `MOXI_COMMS_NAME=operator` in any shell resolve to `post_as_user` would reopen
    the fabricated-identity class. The lane needs a credential AND a claimed session is
    ineligible for it: a shell holding both stamps is a shell whose identity nobody can
    name, so neither is preferred and the send is refused.

    Raised rather than answered with `None` for the same reason `MalformedStamp` is: the
    verb has to tell "there is no operator here" from "there are two senders here" and
    say the true thing about each. `detail` is machinery-facing (it names the variables);
    the agent-facing refusal is the CLI's, and it carries no seat vocabulary.
    """

    def __init__(self, message: str, *, detail: str):
        super().__init__(message)
        self.detail = detail


class MalformedStamp(Exception):
    """The launch stamp exists and is BROKEN -- which is not the same as absent.

    Raised rather than answered with `None` so the verb can tell the two apart and say
    the true thing about each. `detail` is machinery-facing (it names the variable and
    the value); the agent-facing refusal is the CLI's, and it carries no seat vocabulary.
    """

    def __init__(self, message: str, *, detail: str):
        super().__init__(message)
        self.detail = detail


@dataclass
class SenderIdentity:
    """Who the verb posts as."""
    qualified: str
    scope: str
    name: str | None = None

    @classmethod
    def for_agent(cls, name: str, scope: str) -> "SenderIdentity":
        return cls(qualified=qualify(name, scope), scope=scope, name=name)


def operator_from_env(env) -> str | None:
    """The operator's user id when this shell IS the operator's lane, else None.

    The terminal is the operator's lane, and `MOXI_COMMS_OPERATOR=<user_id>` is how a
    terminal says so. The launcher never passes it to a session, so nothing an agent
    runs inherits it.

    A CLAIMED session is never the operator. `MOXI_COMMS_NAME` beside the trigger raises
    `OperatorRefused`, with the variable in the log and in `detail`.
    """
    user_id = (env.get(OPERATOR_VAR) or "").strip()
    if not user_id:
        return None
    claimed = [var for var in CLAIM_VARS if env.get(var)]
    if claimed:
        log.warning("%s is set beside %s; a claimed session is never the operator and the "
                    "verb refuses rather than pick one of them",
                    OPERATOR_VAR, ", ".join(claimed))
        raise OperatorRefused(
            "this process carries the operator's stamp and an agent's claim at once",
            detail=f"{OPERATOR_VAR} is set beside {', '.join(claimed)}")
    return user_id


def operator_credential(db, room: str = NAMES, *, store=None, env=None):
    """The operator's lane resolved the way an agent's is: the room, then the credential.

    Returns `(token, channel_id)` for the verb's posting step.

    The configured resolver chain supplies the token.  Its store step still uses the
    operator holder, while an environment step can answer first when the chain declares
    it.  The token goes to the one caller that presents it and nowhere else.
    """
    room_row = directory.room_by_name(db, room)
    if room_row is None:
        raise SendFailed(f"{room} is not provisioned")
    holder = tokens.owner_for_role(tokens.ROLE_OPERATOR)
    chain = secrets.from_config(env=env, store=store)
    token = chain.get(tokens.OPERATOR_SECRET_ID, owner=holder)["value"]
    return token, room_row["channel_id"]


def post_as_operator(gateway, user_id: str, channel_id: str, content: str, *, token: str,
                     attempts: int = constants.RETRY_ATTEMPTS, sleep=time.sleep):
    """The operator's post: the same message, a different door, and a credential.

    `content` is whatever the verb composed, machine line and all -- the operator types
    the same line every other sender does, and the verb's length cap applies to this lane
    by the same seam fact (`max_content_length`), because the cap is a property of a
    MESSAGE and not of the posting route.

    The token rides down as a keyword and the backend decides what it is worth: the local
    gateway compares it against `meta('operator_token_hash')`, while the mock accepts
    and ignores it so the seam is one shape on both. The bounded
    retry is the posting policy for this lane.
    """
    return _bounded(lambda: gateway.post_as_user(user_id, channel_id, content, token=token),
                    as_=f"operator {user_id}", attempts=attempts, sleep=sleep)


def _bounded(send, *, as_: str, attempts: int, sleep):
    """The bounded retry, once, for both lanes: the transport states the fact, the verb
    owns the
    policy, and exhaustion is LOUD in the same words whichever door was used."""
    last = None
    for attempt in range(1, attempts + 1):
        try:
            msg = send()
            log.info("posted message_id=%s channel=%s as=%s attempt=%d",
                     msg.id, msg.channel_id, as_, attempt)
            return msg
        except RateLimited as e:
            last = e
            log.warning("429 on attempt %d/%d; retry_after=%ss", attempt, attempts,
                        e.retry_after)
            if attempt >= attempts:
                break
            sleep(min(e.retry_after, constants.RETRY_MAX_SLEEP))
    raise SendFailed(
        f"SEND FAILED: rate limited on all {attempts} attempts "
        f"(last Retry-After {getattr(last, 'retry_after', '?')}s). The message was NOT "
        "posted and is NOT in the record. Nothing will retry it for you.")


def sender_from_env(env, project: str) -> SenderIdentity | None:
    """The identity a launch stamp claims. **Nothing is sent as it.** A send reads its
    identity from the session's claim socket (`cli._claim_sender`); with no socket the
    send verb reads this only to name, in its refusal, the identity the stamp claims,
    because only a live session holding a name sends under it. `moxi comms cache` reads it to
    gate its caller when there is no claim socket.

    The harness stamps the claim into the environment at claim time.

    `MOXI_COMMS_NAME` with `MOXI_COMMS_SCOPE` or `MOXI_COMMS_GLOBAL` is the claim. No
    name means no identity. Identity comes from the claim-stamped environment, and a
    process with no claim is refused at the verb.
    """
    name = env.get("MOXI_COMMS_NAME")
    if not name:
        return None
    # A stamp with no `MOXI_COMMS_SCOPE` is the global identity: `project` is the session's
    # project, never its scope.
    scope = GLOBAL if env.get("MOXI_COMMS_GLOBAL") else (env.get("MOXI_COMMS_SCOPE") or GLOBAL)
    return SenderIdentity.for_agent(name, scope)
