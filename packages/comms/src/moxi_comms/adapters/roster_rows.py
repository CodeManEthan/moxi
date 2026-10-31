"""What the codex and opencode rosters share: the roster itself, reading it from comms'
config file, the `[*.defaults]` refusals, the head of every row, its `directors`, and
the identity a row stamps into what its wrapper spawns.

Each harness keeps its own keys, its defaults, its agent dataclass and the words its
refusals use for what it runs; the checks and their order are here once.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import directors as directors_mod
from ..config import ConfigError
from ..scoping import GLOBAL, NAME_RE, is_auto_name, qualify, split_tag


@dataclass
class Roster:
    """A harness's roster rows by qualified name. A harness's own roster sets
    `MISSING`, the refusal of a name it has no row for, formatted with `qualified`,
    `source` and `known`."""

    agents: dict = field(default_factory=dict)
    source: str = "defaults"

    MISSING = "{qualified!r} is not in the roster ({source}). Known: {known}."

    def require(self, qualified: str):
        agent = self.agents.get(qualified)
        if agent is None:
            known = ", ".join(sorted(self.agents)) or "(none)"
            raise ConfigError(self.MISSING.format(qualified=qualified, source=self.source,
                                                  known=known))
        return agent


def read_config(env=None, path=None):
    """comms' config (`config.load(env)`), or the comms config file `path` when a launch
    names one (`--config`)."""
    from .. import config as config_mod

    return (config_mod.load_file(path, env=env) if path is not None
            else config_mod.load(env=env))


def check_defaults(table: str, source: str, defaults: dict, entry_keys,
                   default_keys) -> str:
    """Refuse a `[<table>.defaults]` key no row takes, and one a row keeps for itself.
    Returns the table's `where` for the harness's own checks of its values."""
    where = f"{source}: [{table}.defaults]"
    if "slot" in defaults:
        raise ConfigError(f"{where}: slot is retired; remove it: comms has no slots")
    unknown = sorted(set(defaults) - set(entry_keys))
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(unknown)}; expected "
                          f"{', '.join(sorted(default_keys))}")
    own = sorted(set(defaults) - set(default_keys))
    if own:
        raise ConfigError(f"{where}: {', '.join(own)} name(s) one identity's own; set "
                          f"them on its [{table}.agents.\"<name>\"] row (the defaults "
                          f"hold only {', '.join(sorted(default_keys))})")
    return where


@dataclass
class RowHead:
    """The parts of a row every harness reads the same way."""

    where: str
    qualified: str
    name: str
    scope: str
    cwd: str


def parse_head(table: str, source: str, qualified: str, section, entry_keys, *,
               stamped: str, works_in: str) -> RowHead:
    """A row's table check, its unknown keys, its name and scope, the name check, the
    `agent-N` refusal and `cwd`, in that order. `works_in` names what works in `cwd`.

    `stamped` is accepted while both roster callers migrate together. It has no effect.
    """
    where = f"{source}: [{table}.agents.\"{qualified}\"]"
    if not isinstance(section, dict):
        raise ConfigError(f"{where} is not a table")
    if "slot" in section:
        raise ConfigError(f"{where}: slot is retired; remove it: comms has no slots")
    unknown = sorted(set(section) - set(entry_keys))
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(unknown)}; expected "
                          f"{', '.join(sorted(entry_keys))}")
    name, scope = split_tag(qualified)
    scope = scope or GLOBAL
    # `planner@global` and `planner` are the same identity: a global name renders bare.
    # Normalising here means the roster key can be written either way and the state
    # file, the cursor and the daemon's binding still agree on one string.
    qualified = qualify(name, scope)
    if not NAME_RE.match(name):
        raise ConfigError(f"{where}: {name!r} is not a valid name")
    if is_auto_name(name):
        raise ConfigError(
            f"{where}: the agent-N namespace is reserved for the daemon's auto-namer in "
            "every scope; a roster identity is chosen or standing, never ephemeral")

    cwd = section.get("cwd")
    if not cwd:
        raise ConfigError(f"{where}: no cwd (the directory {works_in} works in)")
    return RowHead(where=where, qualified=qualified, name=name, scope=scope,
                   cwd=str(cwd))


def parse_directors(where: str, section: dict) -> list:
    """The row's `directors`, the recipient's grant, qualified exactly as
    `moxi-comms-claude --director` qualifies at launch: a bare name is the global
    identity, `name@scope` is explicit, a global one renders bare."""
    declared = section.get("directors", [])
    if not isinstance(declared, list):
        raise ConfigError(f"{where}: directors must be a list of names, not "
                          f"{declared!r} -- authority is declared by the RECIPIENT, one "
                          "entry per director, no transitivity")
    try:
        return directors_mod.declare(declared)
    except directors_mod.BadDirector as e:
        raise ConfigError(f"{where}: {e}") from e


def stamped_env(agent, base=None) -> dict:
    """A row's identity stamped into its wrapper's spawn environment, scrubbed first
    (`session.stamped_env`)."""
    from .session import stamped_env as stamp     # one implementation of the stamp

    return stamp(base, name=agent.name, scope=agent.scope,
                 global_=agent.scope == GLOBAL,
                 project=None if agent.scope == GLOBAL else agent.scope,
                 directors=agent.directors)


__all__ = ["Roster", "RowHead", "check_defaults", "parse_directors", "parse_head",
           "read_config", "stamped_env"]
