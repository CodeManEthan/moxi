"""The opencode roster: the `[opencode]` table of comms' config file, NON-SECRET ids only.

Same law as the codex roster: **which OpenCode servers exist is provisioner-configured,
never register-triggered.** `moxi-comms-opencode` runs a server only for an identity named
here; a `register` on the daemon socket never causes a spawn.

    CONFIG/comms/config.toml

    [opencode.defaults]                # every row takes these unless it names its own
    model     = ["opencode/minimax-m3.1-flash-free", "opencode/glm-5.3-flash-free"]

    [opencode.agents."coder@proj-a"]
    cwd       = "/home/me/src/proj-a"  # best at a git root (see below)
    directors = ["main"]               # THIS agent's declared directors (authority)
    # model   = "opencode/minimax-m3.1-flash-free"   # the launch default; a list names
    #                                                # the fallbacks after it, in order
    # binary  = "/home/me/.opencode/bin/opencode"
    # session_file = ".../opencode/coder@proj-a.json"

**The model is a launch default, and every model named here must be a free one.** The
wrapper sets OpenCode's `model` and `small_model` to the first model in `model` that
the server offers for free, and never changes it after that: comms' posts name no
model, and a model the person picks in the window is theirs. Comms never names a paid
model and never adds a credential, a key or a provider entry; the OpenCode sign-in and
environment the person starts the wrapper with are theirs and are used as they are.

**A row that names no `model` gets `DEFAULT_MODELS`, in order.** Before it starts the
server, the wrapper has OpenCode refresh its model list
(`opencode.OpenCodeAdapter.refresh_models`), so a model retired since OpenCode last
fetched the list is not offered and the next one is chosen.

**The session loads the person's own configuration.** The server runs with the
environment the wrapper was started in, so it reads the person's OpenCode config and
sign-in, their `~/.claude/CLAUDE.md` and their skills, as OpenCode does when they run
it themselves. With no git root above `cwd`, OpenCode treats an ancestor `.opencode/`
directory as project config and writes into it; a `cwd` at a git root bounds that.

The roster lives by the config file's refusal rule (`config.refuse_secrets`): a
token-shaped key is a hard stop. `[opencode.defaults]` holds any row key but
`session_file`, which names one identity's own things.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..config import ConfigError
from ..paths import opencode_sessions_dir
from . import roster_rows

#: The launch defaults when a row names none, in order: the free OpenCode Zen models
#: whose provider keeps no data and trains on none. Source: https://opencode.ai/docs/zen/,
#: read 2026-10-20, where each "follows a zero-retention policy and does not use your
#: data for model training"; MiniMax M3.1 Flash Free is free "for a limited time", so a
#: launch whose model list no longer offers it takes the next. Terms and the free list
#: change; a row's `model` overrides the whole list.
DEFAULT_MODELS = ("opencode/minimax-m3.1-flash-free", "opencode/glm-5.3-flash-free",
                  "opencode/deepseek-v4.1-flash-free")
#: The first of them, for the callers that name one.
DEFAULT_MODEL = DEFAULT_MODELS[0]

#: The name the wrapper looks for on PATH, then where OpenCode's installer puts it
#: (only an interactive shell adds that directory to PATH).
BINARY = "opencode"
INSTALL_DIR = Path("~/.opencode/bin")

_ENTRY_KEYS = {"cwd", "directors", "model", "binary", "session_file"}
#: Keys that name one identity's own things, so `[opencode.defaults]` may not hold them.
_OWN_KEYS = {"session_file"}
#: What `[opencode.defaults]` may hold: the launch model alone. Every other key is set
#: on each row, `directors` above all, since it is one recipient's authority grant.
_DEFAULT_KEYS = {"model"}


@dataclass
class OpenCodeAgent:
    """One roster row: everything the wrapper needs about one identity."""

    qualified: str
    name: str
    scope: str
    cwd: str
    session_file: Path | None       # None only in a roster built to check rows
    models: list = field(default_factory=lambda: list(DEFAULT_MODELS))
    binary: str | None = None
    directors: list = field(default_factory=list)   # qualified at load, one spelling

    def env(self, base=None) -> dict:
        """Identity stamped into the server's environment AT SPAWN, scrubbed first.

        OpenCode runs a session's tools in the server's own process, so the agent's
        shell inherits exactly this, and `moxi comms send` there speaks as this identity
        through the wrapper's claim socket stamped beside it."""
        return roster_rows.stamped_env(self, base)


class Roster(roster_rows.Roster):
    MISSING = ("{qualified!r} is not in the opencode roster ({source}). Which "
               "OpenCode servers exist is provisioner-configured: registering an "
               "identity never starts one. Add an [opencode.agents.\"{qualified}\"] "
               "section to comms' config file. Known: {known}.")


def resolve_binary(name: str | None, path: str | None = None) -> str | None:
    """The opencode binary, as an absolute path, or None when nothing resolves: the
    row's `binary`, else `opencode` on PATH, else the installer's directory."""
    if name and os.sep in name:
        candidate = Path(name).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.absolute())
        return None
    found = shutil.which(name or BINARY, path=path)
    if found:
        return str(Path(found).absolute())
    if name:
        return None
    installed = INSTALL_DIR.expanduser() / BINARY
    if installed.is_file() and os.access(installed, os.X_OK):
        return str(installed)
    return None


def session_path(qualified: str) -> Path:
    return opencode_sessions_dir() / f"{qualified}.json"


def load(env=None, path=None) -> Roster:
    """The roster from comms' config file (`config.load(env).opencode`), or from the comms
    config file `path` when a launch names one (`--config`). A file with no `[opencode]`
    table is an empty roster; a bad row is refused, loudly."""
    cfg = roster_rows.read_config(env, path)
    return from_table(cfg.opencode, cfg.source)


def from_table(table: dict, source: str = "defaults", *, check: bool = False) -> Roster:
    """The roster from the raw `[opencode]` table, `{"defaults": {...}, "agents":
    {...}}`. `source` is the file the table came from, which every refusal names.

    `check=True` checks the rows and nothing more, for a check of config text: it reads
    no environment, so a row with no `session_file` of its own gets None in place of its
    default under STATE. The roster it returns is not one to launch from."""
    defaults = dict((table or {}).get("defaults") or {})
    where = roster_rows.check_defaults("opencode", source, defaults, _ENTRY_KEYS,
                                       _DEFAULT_KEYS)
    if "model" in defaults and not _models_ok(defaults["model"]):
        raise ConfigError(f"{where}: model must be a \"provider/model\" id or a list of "
                          f"them, not {defaults['model']!r}")
    agents = {}
    for qualified, section in ((table or {}).get("agents") or {}).items():
        if isinstance(section, dict):
            section = {**{k: v for k, v in defaults.items() if k not in section},
                       **section}
        agent = _entry(source, qualified, section, check=check)
        agents[agent.qualified] = agent
    return Roster(agents=agents, source=source)


def _models_ok(model) -> bool:
    """A `model` value: one "provider/model" id, or a non-empty list of them."""
    models = [model] if isinstance(model, str) else model
    return (isinstance(models, list) and bool(models)
            and all(isinstance(m, str) and "/" in m for m in models))


def _entry(path, qualified: str, section, *, check: bool = False) -> OpenCodeAgent:
    head = roster_rows.parse_head(
        "opencode", path, qualified, section, _ENTRY_KEYS,
        stamped=": the server's environment is stamped at spawn, before any register "
                "could answer.",
        works_in="the OpenCode session")
    where, qualified = head.where, head.qualified

    model = section.get("model", list(DEFAULT_MODELS))
    models = [model] if isinstance(model, str) else model
    if not _models_ok(model):
        raise ConfigError(f"{where}: model must be a \"provider/model\" id or a list of "
                          f"them, not {model!r}")

    declared = roster_rows.parse_directors(where, section)
    binary = section.get("binary")
    if binary is not None and not isinstance(binary, str):
        raise ConfigError(f"{where}: binary must be a path, not {binary!r}")
    return OpenCodeAgent(
        qualified=qualified, name=head.name, scope=head.scope,
        cwd=head.cwd, session_file=(Path(section["session_file"]) if section.get("session_file")
                      else None if check else session_path(qualified)),
        models=list(models), binary=binary, directors=declared)


__all__ = ["BINARY", "DEFAULT_MODEL", "DEFAULT_MODELS", "INSTALL_DIR", "OpenCodeAgent", "Roster",
           "ConfigError", "from_table", "load", "resolve_binary", "session_path"]
