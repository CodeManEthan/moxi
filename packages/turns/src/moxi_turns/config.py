"""turns' settings, read through `moxi.config` from `CONFIG/turns/config.toml`.

The schema is `settings.toml` in this package (the announcement's `SETTINGS`). One more
input sits outside the file: `MOXI_TURNS_COMMANDS`, a launcher's grant of session commands
to one launch, read from the environment the endpoint inherits from its session.

The grant has two forms. Bound, `<session id>:<commands>`, rendered when the launch names
the id it starts its harness under: an endpoint honours it only when that id is one its
session has had, its current id or an earlier one (a `/clear` moves a session to a new
id and keeps the old one), so a harness started by hand inside the session, which
inherits the variable under an id of its own, gets nothing from it. Bare, `<commands>`,
for a launch with no id to bind (an unnamed resume, a Codex or OpenCode launch, a grant
stamped by hand): honoured as it stands. `<commands>` is a comma list of session
commands. A bound grant with no id, or with a second `:`, is malformed and counts as
absent, with a log line.

turns refuses nothing its schema passes, so it declares no `CONFIG_CHECK`: a name in
`commands.allow` that is not a session command grants nothing (the doctor warns of it),
and a start-list entry that names no installed module or readable file adds nothing (the
doctor names it).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping

from moxi import config as moxi_config

from .results import COMMANDS

log = logging.getLogger("moxi.turns.config")

MODULE = "turns"
#: A launcher stamps `<session id>:clear,exit` (bound) or `clear,exit` (bare), with any of
#: the session commands, into one launch's environment.
COMMANDS_ENV = "MOXI_TURNS_COMMANDS"
#: Between a bound grant's session id and its commands.
BOUND = ":"
#: The stop handler's wait.
STOP_WAIT = "stop_handler.wait_ms"


def schema() -> moxi_config.Schema:
    return moxi_config.Schema.from_resource(__package__, "settings.toml")


def reader(env: Mapping[str, str] | None = None) -> moxi_config.Config:
    """A started-on-demand `moxi.config.Config` for turns."""
    return moxi_config.Config(MODULE, schema(), env=env)


def load(env: Mapping[str, str] | None = None) -> moxi_config.Settings:
    """turns' settings now. Raises `moxi.config.ConfigError`."""
    return reader(env).start()


def grant(commands: Iterable[str], session_id: str | None = None) -> str:
    """The value of `MOXI_TURNS_COMMANDS` granting `commands`, in `COMMANDS`' order: bound
    to `session_id` when one is given, bare otherwise. Raises `ValueError` for a name that
    is not a session command, or an id that could not be read back (empty, or holding
    `:`, `,` or white space)."""
    names = set()
    for name in commands:
        if name not in COMMANDS:
            raise ValueError(f"{name!r} is not a session command; use {', '.join(COMMANDS)}")
        names.add(name)
    listed = ",".join(c for c in COMMANDS if c in names)
    if session_id is None:
        return listed
    if not session_id or any(ch in session_id for ch in BOUND + ",") \
            or any(ch.isspace() for ch in session_id):
        raise ValueError(f"session id {session_id!r} cannot bind a command grant: it is "
                         "empty or holds ':', ',' or white space")
    return f"{session_id}{BOUND}{listed}"


def granted(stamp: str, session_ids: Iterable[str | None] = ()) -> frozenset[str]:
    """The session commands a `MOXI_TURNS_COMMANDS` value grants to an endpoint whose
    session has had `session_ids` (its current id and the earlier ones). A bound grant
    whose id is not among them grants nothing, as does a malformed one (logged); a bare
    one grants its list. Names that are not session commands are dropped."""
    listed = stamp
    if BOUND in stamp:
        bound_id, _, listed = stamp.partition(BOUND)
        bound_id = bound_id.strip()
        if not bound_id or BOUND in listed:
            log.warning("%s is malformed (%r): it grants nothing", COMMANDS_ENV, stamp)
            return frozenset()
        if bound_id not in {i for i in session_ids if i}:
            log.info("%s is bound to a session id this endpoint has not had: it grants "
                     "nothing here", COMMANDS_ENV)
            return frozenset()
    return frozenset(n for n in (part.strip() for part in listed.split(",")) if n in COMMANDS)


def allowed(settings_allow: object, env: Mapping[str, str],
            session_ids: Iterable[str | None] = ()) -> frozenset[str]:
    """The session commands consented to: the config's `commands.allow` and the launch's
    grant together. `session_ids` are the ids the endpoint's session has had, its current
    one and the earlier ones; a grant bound to another id counts as absent, so only
    `commands.allow` applies. Names that are not session commands are dropped."""
    names: set[str] = set()
    if isinstance(settings_allow, (list, tuple)):
        names.update(n for n in settings_allow if isinstance(n, str) and n in COMMANDS)
    names.update(granted(env.get(COMMANDS_ENV, ""), session_ids))
    return frozenset(names)


def unknown_commands(settings_allow: Iterable[object]) -> list[str]:
    """The names in `commands.allow` that are not session commands, which grant nothing."""
    return [str(n) for n in settings_allow if n not in COMMANDS]


__all__ = ["MODULE", "COMMANDS_ENV", "BOUND", "STOP_WAIT", "schema", "reader", "load", "grant",
           "granted", "allowed", "unknown_commands"]
