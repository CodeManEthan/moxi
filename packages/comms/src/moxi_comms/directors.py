"""Recipient-declared director authority.

Authority is granted by the RECIPIENT's side, never claimed by the sender: at launch, and
through its machine's own comms config. The declaration surface is `moxi-comms-claude
--director <name>` (repeatable), the codex and opencode rosters' `directors` lists, and the
box's `[policy] directors`, which every launch takes after its own (`effective`) unless it
opts out (`--no-policy-directors`, `takes_policy_directors = false`). A sender can no more
assert authority over its reader than it can assert a routing tag it is entitled to, and
for the same reason: tags are spoofable, names are cooperative labels, and nothing builds
authorization on tag contents.

It is INVISIBLE ON THE WIRE. No record, payload, daemon or protocol change: two things
exist and nothing else does.

  * the DECLARATION -- qualified ONCE, at launch, and stamped into the session
    environment as `MOXI_COMMS_DIRECTORS` (comma-separated, qualified) beside the identity
    stamp `MOXI_COMMS_NAME` / `MOXI_COMMS_SCOPE`, with the part only the box's policy
    gave in `MOXI_COMMS_POLICY_DIRECTORS`;
  * the RENDERING -- `standing_of()`, which both adapters' frame line asks about every
    delivered payload before it writes the line.

**Qualification happens exactly once.** `moxi-comms-shim --director` and `$MOXI_COMMS_DIRECTORS`
carry already-qualified names and are never re-qualified: a global identity renders bare
(`herald`, not `herald@global`), so a second pass cannot tell it from a bare name and
would quietly turn it into something else. One spelling per identity, decided at the
wrapper and the roster: a bare director is the global identity, `name@scope` is that
scope's.

Directive standing requires VERIFIED attribution. A
declared director whose message is not verified degrades to information standing and
the frame line says why (it renders `(unverified)` and the reason; the wire value is
still `labeled`) -- standing-tier directors are the recommended shape because they stay
verified through replay. A POLICY director (one only the box's setting gave) needs the
standing tier too: anyone may bind an unheld name, so the box's setting grants nothing
until the name is provisioned. Operator-tier messages are ALWAYS directives, declared or
not. No transitivity: one hop, fixed at launch, dead with the session.

Truth in labeling: this is authority CALIBRATION, not access control. A
model can ignore its director or obey a stranger; what this fixes is the default.
"""

from __future__ import annotations

from dataclasses import dataclass

from .attribution import TIER_OPERATOR, TIER_STANDING, VERIFIED
from .constants import DIRECTORS_ENV, POLICY_DIRECTORS_ENV
from .scoping import NAME_RE, identity_of, split_tag

SEPARATOR = ","

DIRECTIVE, INFORMATION = "directive", "information"

_DIRECTOR_NOTE = "your director"
#: The one place a deviation still needs a sentence rather than a mark: `(unverified)`
#: says what the frame knows, and this says what it COSTS -- the authority the recipient
#: granted does not apply to a message whose sender was not proven.
_DEGRADED_NOTE = ("your declared director, but this message's attribution is "
                  "unverified — information standing")
#: A policy director (one this box's `[policy] directors` names, not the launch) whose
#: message is verified but whose name holds no standing row: anyone may bind an unheld
#: name, so the box's setting grants nothing until the name is provisioned.
_UNPROVISIONED_NOTE = "policy director, not provisioned: information standing"
_POLICY_DEGRADED_NOTE = ("policy director, but this message's attribution is "
                         "unverified — information standing")


class BadDirector(ValueError):
    """A director name that is not a name. Refused where it was typed, never dropped."""


# --------------------------------------------------------------------- the declaration

def qualify_director(tag) -> str:
    """Qualify ONE declared director by the one rule every name reads by.

    A bare name is the global identity, `name@scope` is explicit, and a global identity
    renders bare (`name@global` -> `name`). The declaring session's scope plays no part.
    An `agent-N` is global and unscoped wherever it is written, exactly as the daemon
    resolves it -- so `agent-7@proj-a` normalises to `agent-7` rather than to an address
    that could never match.
    """
    if not isinstance(tag, str):
        raise BadDirector(f"director {tag!r} is not a name")
    name, at = split_tag(tag.strip())
    if not NAME_RE.match(name or ""):
        raise BadDirector(f"director {tag!r} is not a name "
                          "(expected `name` or `name@scope`)")
    if at is not None and not NAME_RE.match(at):
        raise BadDirector(f"director {tag!r} has no usable scope after '@'")
    return identity_of(tag.strip())


def declare(tags) -> list[str]:
    """The launch-time declaration: qualified, de-duplicated, order preserved."""
    declared: list[str] = []
    for tag in tags or ():
        qualified = qualify_director(tag)
        if qualified not in declared:
            declared.append(qualified)
    return declared


def effective(declared, config, *, own: str, use_policy: bool) -> tuple[list[str], list[str]]:
    """The directors one launch runs with, and which of them only the box's setting gave.

    Returns `(full, policy)`: `full` is the launch-declared names (`declared`, qualified
    here as `declare` qualifies them), then each of `config.policy.directors` not already
    present, when `use_policy`; `policy` is the names that came only from the setting.
    Order is kept and names are de-duplicated. The session's own qualified identity
    `own` is left out of the policy part, so a policy director's own session doesn't
    direct itself; a name the launch declared stays whatever it is.

    A wrapper calls this once, where it builds its declared list, so the stamp, the
    claim, the prints and every render carry both lists from then on.
    """
    full = declare(declared)
    policy: list[str] = []
    if use_policy:
        for name in getattr(getattr(config, "policy", None), "directors", ()) or ():
            if name not in full and name != own:
                full.append(name)
                policy.append(name)
    return full, policy


def stamp(directors, policy=()) -> dict:
    """The env stamps, beside the identity stamp: the full list, and the part of it only
    the box's setting gave. An empty list stamps nothing."""
    directors, policy = list(directors or ()), list(policy or ())
    out = {DIRECTORS_ENV: SEPARATOR.join(directors)} if directors else {}
    if policy:
        out[POLICY_DIRECTORS_ENV] = SEPARATOR.join(policy)
    return out


def _split(raw) -> tuple:
    return tuple(part.strip() for part in (raw or "").split(SEPARATOR) if part.strip())


def from_env(env=None) -> tuple:
    """Read the stamp back. Already qualified -- never re-qualified (see the module doc)."""
    import os

    env = os.environ if env is None else env
    return _split(env.get(DIRECTORS_ENV))


def policy_from_env(env=None) -> tuple:
    """Read the policy stamp back: the names in the full stamp that only the box's
    setting gave. Already qualified."""
    import os

    env = os.environ if env is None else env
    return _split(env.get(POLICY_DIRECTORS_ENV))


def launch_declared(directors, policy=()) -> tuple:
    """The full list without its policy part: the names the launch itself declared."""
    policy = set(policy or ())
    return tuple(name for name in directors or () if name not in policy)


def validated(tags) -> tuple:
    """Shape-check an already-qualified list (the adapter's own flag / env stamp)."""
    out: list[str] = []
    for tag in tags or ():
        if not isinstance(tag, str) or not tag.strip():
            raise BadDirector(f"director {tag!r} is not a name")
        name, at = split_tag(tag.strip())
        if not NAME_RE.match(name or "") or (at is not None and not NAME_RE.match(at)):
            raise BadDirector(f"director {tag!r} is not a qualified name "
                              "(expected `name` or `name@scope`)")
        if tag.strip() not in out:
            out.append(tag.strip())
    return tuple(out)


def describe(directors, policy=(), held=None, *, opted_out: bool = False) -> str | None:
    """The launch prints' directors line, after `directors  `, or None when there is
    nothing to say. Each policy name is marked `(policy)`, or `(policy, not provisioned)`
    when `held` (the names holding a standing row now, `directory.provisioned`) is known
    and lacks it. `opted_out` says the launch takes no policy directors."""
    policy = set(policy or ())
    names = []
    for name in directors or ():
        if name not in policy:
            names.append(name)
        elif held is not None and name not in held:
            names.append(f"{name} (policy, not provisioned)")
        else:
            names.append(f"{name} (policy)")
    if not names and not opted_out:
        return None
    text = (", ".join(names) + "; verified messages from these render as directives"
            if names else "none")
    return f"{text}; policy directors: opted out" if opted_out else text


# ----------------------------------------------------------------------- the rendering

@dataclass(frozen=True)
class Standing:
    """How one delivered message presents: the frame line's lead, and why."""

    kind: str                 # directive | information
    lead: str                 # the words before the author's name
    note: str | None          # the short factual reason, or nothing to say

    @property
    def directive(self) -> bool:
        return self.kind == DIRECTIVE


def standing_of(author: dict, directors=(), *, policy=()) -> Standing:
    """What standing this author has WITH THIS RECIPIENT. The whole authority check.

    Operator first, and unconditionally: the operator's word is a directive whether or
    not anything was declared. Then the declaration, gated on verified attribution --
    an unverified director keeps information standing and says so.

    `policy` is the part of `directors` that only the box's `[policy] directors` gave
    (`effective`'s second list). Such a name is a directive only when the message is
    verified AND its author is at the standing tier, which only provisioning writes; the
    tier is read from the message, so provisioning the name later counts at once. A name
    in `policy` is never also launch-declared (`effective` keeps the two apart), so a
    name both declared and set takes the launch's rule.
    """
    author = author or {}
    if author.get("tier") == TIER_OPERATOR:
        return Standing(DIRECTIVE, "directive from", None)
    name = author.get("name")
    if name in set(policy or ()):
        if author.get("attribution") != VERIFIED:
            return Standing(INFORMATION, "from", _POLICY_DEGRADED_NOTE)
        if author.get("tier") != TIER_STANDING:
            return Standing(INFORMATION, "from", _UNPROVISIONED_NOTE)
        return Standing(DIRECTIVE, "directive from", _DIRECTOR_NOTE)
    if name in set(directors or ()):
        if author.get("attribution") == VERIFIED:
            return Standing(DIRECTIVE, "directive from", _DIRECTOR_NOTE)
        return Standing(INFORMATION, "from", _DEGRADED_NOTE)
    return Standing(INFORMATION, "from", None)


__all__ = ["DIRECTORS_ENV", "POLICY_DIRECTORS_ENV", "SEPARATOR", "DIRECTIVE", "INFORMATION",
           "BadDirector", "Standing", "declare", "describe", "effective", "from_env", "launch_declared",
           "policy_from_env", "qualify_director", "stamp", "standing_of", "validated"]
