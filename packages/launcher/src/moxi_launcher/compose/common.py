"""What both composers share: the shapes they take and give, the identity block and the
system prompt file, the opening prompt, the child's environment, and the size limits.

Everything here but `write_prompt` is pure: values in, text, argv and environment out.

**The child's environment is scrubbed, then stamped.** From a copy of the caller's
environment every `CLAUDE*` name but the user's own Claude configuration and sign-in
(`CLAUDE_KEEP`), `AI_AGENT`, and every name and prefix the installed modules declare in
`SESSION_ENV` (`moxi.modules.session_env()`) is removed, so a launch made from inside a
session carries none of that session's stamps. Then the launch's own
stamps go in: `MOXI_LAUNCHER_REQUEST` always, `MOXI_TURNS_COMMANDS=exit` when turns is
installed, the launch additions' environment, usage's warning mark and notes' directory
when those rows are set. The stamps alone are what a launch records and `preview` shows;
the whole environment goes to the child only.

**A value is never cut to fit.** A command line past the system's limit (on Windows the
whole line, elsewhere one word), or a brief, `values_json` or `argv_json` past the launch
log's cap, is refused naming what is too long.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from moxi import files

from .. import catalog, store

#: The first line of the identity block.
HEADER = "--- LAUNCH IDENTITY (stamped at launch) ---"
_WIDTH = len("start posture: ")

#: The launch's stamp: the request id of the launch that started the session.
REQUEST = "MOXI_LAUNCHER_REQUEST"
#: turns' grant of session commands to the one launch it is stamped into.
TURNS_COMMANDS = "MOXI_TURNS_COMMANDS"
#: The commands part 1 grants.
COMMANDS = ("exit",)
#: usage's context mark, set from the `warn_at` row.
USAGE_WARN_AT = "MOXI_USAGE_WARN_AT"
#: notes' directory, set from the `notes_dir` row.
NOTES_DIR = "MOXI_NOTES_DIR"
#: What every scrub removes besides the modules' own session stamps: every `CLAUDE*`
#: name but those in `CLAUDE_KEEP`, and the agent marker. A Claude Code session sets
#: `CLAUDE*` names in what it starts, and new ones come with new versions, so the scrub
#: drops the prefix and keeps only a named list.
SCRUB_PREFIXES = ("CLAUDE",)
SCRUB_NAMES = ("AI_AGENT",)
#: The `CLAUDE*` names the scrub keeps: the user's own Claude Code configuration and
#: sign-in, as Claude Code's environment variable reference documents them. A session
#: never sets these in what it starts; without them the launched Claude would read
#: another configuration folder, sign in another way or not at all, or lose a security
#: choice the user made.
CLAUDE_KEEP = frozenset({
    # Where the configuration lives.
    "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_PLUGIN_CACHE_DIR", "CLAUDE_CODE_PLUGIN_SEED_DIR",
    "CLAUDE_CODE_TMPDIR",
    # Sign-in and the model provider.
    "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
    "CLAUDE_CODE_OAUTH_SCOPES", "CLAUDE_CODE_API_KEY_HELPER_TTL_MS",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_MANTLE", "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_SKIP_BEDROCK_AUTH", "CLAUDE_CODE_SKIP_VERTEX_AUTH",
    "CLAUDE_CODE_SKIP_FOUNDRY_AUTH", "CLAUDE_CODE_SKIP_MANTLE_AUTH",
    "CLAUDE_CODE_SKIP_ANTHROPIC_AWS_AUTH", "CLAUDE_CODE_SKIP_AWS_CRED_CACHE",
    "CLAUDE_CODE_AWS_CHAIN_RESOLVE_TIMEOUT_MS", "CLAUDE_CODE_DISABLE_AUTH_REFRESH_LOCK",
    "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
    # How it connects: client certificates, trusted CAs, the proxy.
    "CLAUDE_CODE_CLIENT_CERT", "CLAUDE_CODE_CLIENT_KEY",
    "CLAUDE_CODE_CLIENT_KEY_PASSPHRASE", "CLAUDE_CODE_CERT_STORE",
    "CLAUDE_CODE_PROXY_RESOLVES_HOSTS",
    # Security and traffic choices, and the wrappers its commands run under.
    "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", "CLAUDE_CODE_SCRIPT_CAPS",
    "CLAUDE_CODE_MCP_ALLOWLIST_ENV", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
    "CLAUDE_CODE_SHELL_PREFIX", "CLAUDE_CODE_PROCESS_WRAPPER",
    # The shell it runs.
    "CLAUDE_CODE_SHELL", "CLAUDE_CODE_GIT_BASH_PATH",
})

#: The longest command line Windows takes, whole, in characters, its terminator included.
WINDOWS_LINE = 32_767
#: The longest single word (argument or environment entry) Linux and macOS take, in bytes,
#: its terminator included.
WORD = 128 * 1024


# -- refusals -------------------------------------------------------------------------

class NotYet(catalog.Refused):
    """A launch whose shape needs a flag another module does not take yet."""

    code = "not-yet"


class NoBrief(catalog.Refused):
    """A headless launch with nothing to run."""

    code = "no-brief"


class TooLong(catalog.Refused):
    """A command line past the system's limit. It is refused, never cut."""

    code = "too-long"

    def __init__(self, what: str, size: int, limit: int, unit: str):
        self.size, self.limit = size, limit
        super().__init__(None, f"{what} is {size} {unit}, past the system's limit of "
                               f"{limit}; it is refused, not cut",
                         f"make {what} shorter")


class Unreadable(catalog.Refused):
    """A file a launch names that can't be read."""

    code = "unreadable"


class TurnsAdditions(catalog.Refused):
    """turns could not give a launch its additions (an unknown harness or command, or
    turns' own file that it could not write)."""

    code = "turns-additions"


# -- the shapes -----------------------------------------------------------------------

@dataclass(frozen=True)
class Additions:
    """What turns' launch additions gave one launch: words for the harness's command line,
    variables for its environment, and one note per contributor turns skipped."""

    argv: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Context:
    """Everything a composer reads besides the resolved values.

    `request_id` and `session_id` are the request's (the session id is Claude's, minted
    when the caller gave none). `mode` is the mode the resolver chose, None to let the
    composer work it out. `modules` is the set of installed modules the launch may
    use. `chosen` names the rows whose value was chosen for this launch, typed or from the
    named preset, for the modes rule. `brief`, `handoff_text` and `instructions` are the
    texts the launch read: the brief (inline or from its file), the handoff file's text and
    the seat's instructions (inline or from their file). `handoff_dir` is the folder the
    identity block names, None without a handoff key; `role_found` is False when the
    `agent` role was asked for and did not resolve. `runs_dir` holds the system prompt
    file. `moxi` is the command comms' wrappers run under; `host` turns' Codex host, None
    when it is not installed. `comms_codex` and `comms_headless` say whether comms takes
    its Codex wrapper's per-launch settings and its headless flags. `windows` picks the
    command line limit.
    """

    request_id: str
    session_id: str | None = None
    mode: str | None = None
    actor: str = ""
    modules: frozenset[str] = frozenset()
    chosen: frozenset[str] = frozenset()
    project_key: str | None = None
    preset_kind: str | None = None
    preset_name: str | None = None
    brief: str | None = None
    handoff_text: str | None = None
    instructions: str | None = None
    handoff_dir: str | None = None
    role_found: bool | None = None
    additions: Additions | None = None
    runs_dir: str = ""
    moxi: str = "moxi"
    host: str | None = None
    comms_codex: bool = False
    comms_headless: bool = False
    windows: bool = os.name == "nt"


@dataclass(frozen=True)
class PromptFile:
    """The system prompt file a Claude launch appends: where it goes and what it holds."""

    path: str
    text: str


@dataclass(frozen=True)
class Composition:
    """One composed launch.

    `argv` is what the target starts; `env` the stamped variables only (the launch records
    these and `preview` shows them), which `environment` lays over the scrubbed caller's
    environment for the child. `prompt` is the opening prompt on the argv, if any; `first`
    the text owed as the first turn once the session is ready, when it is not on the argv.
    `brief` is the brief's text as the launch read it (inline or from its file), which the
    launch log keeps for the sender. `values` are the launch's values as the launch
    records them (`values_json`): the resolved values with the `mode` and the rows
    `not_applied`. `prompt_file` is Claude's system prompt file, written before the
    start. `warnings` are one line each.
    """

    harness: str
    mode: str
    target: str
    argv: tuple[str, ...]
    env: Mapping[str, str]
    values: Mapping[str, object]
    prompt: str | None = None
    first: str | None = None
    brief: str | None = None
    prompt_file: PromptFile | None = None
    not_applied: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


# -- values -----------------------------------------------------------------------------

def with_defaults(harness: str, values: Mapping[str, object]) -> dict[str, object]:
    """`values` over the shipped default of every row a user may set for a `harness`
    launch, so a value the resolver left out reads as its default."""
    defaults = {row.name: row.default for row in (*catalog.launcher(),
                                                  *catalog.load(harness))
                if row.settable}
    return {**defaults, **values}


def recorded(harness: str, values: Mapping[str, object], mode: str,
             not_applied: Sequence[str]) -> dict[str, object]:
    """The values a launch records (`values_json`): every value but the rows the
    launcher owns, with the launch's `mode` and the rows `not_applied`."""
    rows = catalog.load(harness).rows
    kept = {name: value for name, value in values.items()
            if not (name in rows and rows[name].owned and name not in catalog.launcher())}
    return {**kept, "mode": mode, "not_applied": list(not_applied)}


def cwd_of(values: Mapping[str, object]) -> str:
    """The launch's directory, which the resolver has made absolute."""
    cwd = values.get("cwd")
    if not cwd or not os.path.isabs(str(cwd)):
        raise ValueError(f"a launch needs its directory as an absolute path, not {cwd!r}")
    return str(cwd)


# -- the identity block and the system prompt file --------------------------------------

def identity_block(*, session_id: str | None, cwd: str, posture: str,
                   project_key: str | None = None, identity: str | None = None,
                   scope: str | None = None, handoff_dir: str | Path | None = None,
                   agent_role: str | None = None) -> str:
    """The identity block a launched session starts with. A line appears only when it
    carries a value."""
    lines = [("session id:", session_id), ("project cwd:", cwd),
             ("project key:", project_key),
             ("identity:", f"{identity}@{scope}"
              if identity and scope and "@" not in identity else identity),
             ("handoff dir:", handoff_dir), ("start posture:", posture),
             ("agent role:", agent_role)]
    return "\n".join([HEADER, *(f"{label:<{_WIDTH}}{value}" for label, value in lines
                                if value)])


def seat_text(block: str, instructions: str | None) -> str:
    """The identity block, then the seat's instructions when there are any."""
    if instructions and instructions.strip():
        return f"{block}\n\n{instructions.strip()}"
    return block


def agent_role(role: str | None, found: bool | None) -> str | None:
    """The block's agent role line: the role, marked when it was asked for and did not
    resolve."""
    if not role:
        return None
    return role if found is not False else f"{role}, requested, did not resolve"


def prompt_path(runs_dir: str | os.PathLike, request_id: str) -> str:
    """Where a launch's system prompt file goes: `<runs>/<request id>.prompt.md`."""
    return str(Path(runs_dir) / f"{request_id}.prompt.md")


def write_prompt(prompt_file: PromptFile) -> None:
    """Write a launch's system prompt file, readable by its owner only, its folder 0700."""
    files.private_dir(Path(prompt_file.path).parent)
    files.atomic_write(prompt_file.path, prompt_file.text, mode=0o600)


# -- the opening prompt -----------------------------------------------------------------

def first_turn(actor: str, *, brief: str | None = None, handoff: str | None = None,
               handoff_text: str | None = None, posture: str | None = None) -> str | None:
    """The text of a launch's first turn, or None when it owes none: the first turn's own
    text (one header line, then the handoff's text and the brief), with the posture line
    second when `posture` is given. The header begins every first turn, so the text is
    never read as a flag or a subcommand."""
    from .. import first

    text = first.text(actor=actor, brief=brief, handoff=handoff or None,
                      handoff_text=(handoff_text or "") if handoff else None)
    if text is None or not posture:
        return text
    header, _, rest = text.partition("\n")
    return f"{header}\nstart posture: {posture}" + (f"\n{rest}" if rest else "")


def scratch_root() -> Path:
    """The folder this user's scratch launches go under: `moxi-scratch-<uid>` in the
    system's temporary folder (the user's name where there is no uid), always absolute.
    One per user, so no other user owns it, and none can move a launch's folder out of it
    and put their own in its place."""
    if hasattr(os, "getuid"):
        who = str(os.getuid())
    else:
        try:
            who = getpass.getuser()
        except Exception:  # noqa: BLE001  (no name to be had: one folder for all)
            who = "user"
        who = re.sub(r"[^A-Za-z0-9._-]", "_", who) or "user"
    return Path(os.path.abspath(tempfile.gettempdir())) / f"moxi-scratch-{who}"


def scratch_dir(request_id: str) -> Path:
    """Where a scratch launch works: a folder named by its request id under
    `scratch_root()`, always as an absolute path (a slot launch is refused a relative
    one). Only the path: the launch makes the folder."""
    return scratch_root() / request_id


def scratch_root_problem(root: Path | None = None) -> str | None:
    """Why this user's scratch launches can't go under `root` (`scratch_root()` when
    None), or None when they can. A missing folder is fine: the launch makes it, 0700.
    One that is a link, not a folder, owned by another user, or open to other users is
    not. On Windows the temporary folder is the user's own, and making the folder checks
    its access list, so nothing is read here."""
    root = scratch_root() if root is None else root
    if os.name == "nt":
        return None
    try:
        st = os.lstat(root)
    except FileNotFoundError:
        return None
    except OSError as error:
        return f"{root} can't be read: {error}"
    if stat.S_ISLNK(st.st_mode):
        return f"{root} is a symbolic link"
    if not stat.S_ISDIR(st.st_mode):
        return f"{root} is not a directory"
    if st.st_uid != os.getuid():
        return f"{root} is owned by another user (uid {st.st_uid})"
    if st.st_mode & 0o077:
        return (f"{root} lets other users in (mode {stat.S_IMODE(st.st_mode):o}); only "
                "its owner may use it")
    return None


# -- the child's environment ------------------------------------------------------------

def scrub(environ: Mapping[str, str], session_env: frozenset[str] | set[str] = frozenset(),
          *, windows: bool = os.name == "nt") -> dict[str, str]:
    """A copy of `environ` without any session's stamps.

    Removes every `CLAUDE*` name but the user's configuration and sign-in
    (`CLAUDE_KEEP`), `AI_AGENT`, and each name in `session_env`, the modules'
    `SESSION_ENV` keys, where a key ending in `_` is a prefix.
    """
    norm = str.upper if windows else (lambda name: name)
    claude = tuple(norm(p) for p in SCRUB_PREFIXES)
    keep = {norm(n) for n in CLAUDE_KEEP}
    prefixes = tuple(norm(n) for n in session_env if n.endswith("_"))
    names = {norm(n) for n in (*SCRUB_NAMES, *(n for n in session_env
                                                if not n.endswith("_")))}

    def scrubbed(name: str) -> bool:
        name = norm(name)
        return (name in names or name.startswith(prefixes)
                or (name.startswith(claude) and name not in keep))

    return {name: value for name, value in environ.items() if not scrubbed(name)}


def stamps(request_id: str, *, turns: bool = False, additions: Additions | None = None,
           warn_at: str | None = None, notes_dir: str | None = None) -> dict[str, str]:
    """The variables a launch stamps into its session's environment."""
    env: dict[str, str] = {}
    if turns:
        env[TURNS_COMMANDS] = ",".join(COMMANDS)
    if additions is not None:
        env.update(additions.env)
    if warn_at:
        env[USAGE_WARN_AT] = str(warn_at)
    if notes_dir:
        env[NOTES_DIR] = str(notes_dir)
    env[REQUEST] = request_id
    return env


def environment(environ: Mapping[str, str], composition: Composition,
                session_env: frozenset[str] | set[str] = frozenset(), *,
                windows: bool = os.name == "nt") -> dict[str, str]:
    """The whole environment the child gets: `environ` scrubbed, then the launch's
    stamps."""
    env = scrub(environ, session_env, windows=windows)
    env.update(composition.env)
    return env


# -- sizes ------------------------------------------------------------------------------

def encode(value: object) -> str:
    """`value` as the launch log stores it."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def check_sizes(argv: Sequence[str], *, values: Mapping[str, object],
                brief: str | None = None, env: Mapping[str, str] | None = None,
                prompt: str | None = None, windows: bool = os.name == "nt") -> None:
    """Refuse a launch past a limit: `store.TooLarge` for a brief, `values_json` or
    `argv_json` past the launch log's cap, `TooLong` for a command line past the
    system's."""
    for name, text in (("brief", brief), ("values_json", encode(dict(values))),
                       ("argv_json", encode(list(argv)))):
        if text is not None and (size := len(text.encode("utf-8"))) > store.CAP:
            raise store.TooLarge(name, size)
    if windows:
        line = subprocess.list2cmdline(list(argv))
        if len(line) + 1 > WINDOWS_LINE:
            raise TooLong("the command line", len(line) + 1, WINDOWS_LINE, "characters")
        return
    words = [(word, "the opening prompt" if prompt is not None and word == prompt
              else f"argument {i}") for i, word in enumerate(argv)]
    words += [(f"{name}={value}", f"variable {name}") for name, value in (env or {}).items()]
    for word, what in words:
        if (size := len(word.encode("utf-8", "surrogateescape")) + 1) > WORD:
            raise TooLong(what, size, WORD, "bytes")


__all__ = ["COMMANDS", "HEADER", "NOTES_DIR", "REQUEST", "SCRUB_NAMES", "SCRUB_PREFIXES",
           "TURNS_COMMANDS", "USAGE_WARN_AT", "WINDOWS_LINE", "WORD", "Additions",
           "Composition", "Context", "NoBrief", "NotYet", "PromptFile", "TooLong",
           "TurnsAdditions", "Unreadable", "agent_role", "check_sizes", "encode",
           "environment", "first_turn", "identity_block", "prompt_path",
           "scratch_dir", "scratch_root", "scratch_root_problem", "scrub", "seat_text",
           "stamps", "write_prompt"]
