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

from moxi import files, names, paths

from .. import catalog, store

#: The first line of the identity block.
HEADER = "--- LAUNCH IDENTITY (stamped at launch) ---"
_WIDTH = len("start posture: ")

#: The launch's stamp: the request id of the launch that started the session.
REQUEST = "MOXI_LAUNCHER_REQUEST"
#: turns' grant of session commands to the one launch it is stamped into.
TURNS_COMMANDS = "MOXI_TURNS_COMMANDS"
#: The commands every launch with turns grants; `commands(values)` adds those its
#: recycle rows need.
COMMANDS = ("exit",)
#: usage's context mark, set from the `warn_at` row.
USAGE_WARN_AT = "MOXI_USAGE_WARN_AT"
#: usage's crossing mark for recycle at limit, set from `limit_at` when
#: `recycle_at_limit` is not off.
USAGE_CROSSING_AT = "MOXI_USAGE_CROSSING_AT"
#: The launch's keep-warm and recycle-on-expiry options, read by cache's timer.
KEEP_WARM = "MOXI_LAUNCHER_KEEP_WARM"
RECYCLE_ON_EXPIRY = "MOXI_LAUNCHER_RECYCLE_ON_EXPIRY"
#: The run id of a relaunch whose first turn carries its handoff.
RELAUNCH = "MOXI_LAUNCHER_RELAUNCH"
#: The identity block's line on a keep-warm launch.
KEEP_WARM_LINE = ('on; open a hold with "moxi launcher keep-warm hold <name>" for each '
                  "piece of work you hand out, and release it when the work is done")
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

#: Codex's own location, configuration and sign-in names, as Codex's environment
#: variable reference documents them, with the API key its built-in OpenAI provider
#: reads. A launch never scrubs them.
CODEX_KEEP = frozenset({
    # Where its state lives.
    "CODEX_HOME", "CODEX_SQLITE_HOME",
    # Sign-in.
    "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_API_KEY",
    "OPENAI_FEDERATION_RULE_ID", "OPENAI_IDENTITY_TOKEN_FILE",
    "OPENAI_WORKLOAD_IDENTITY_CONTEXT",
    # How it connects.
    "CODEX_CA_CERTIFICATE",
})

#: The two classes of the keep-lists' names and the provider names, for a replay.
RECORDED, CREDENTIAL = "recorded", "credential"

#: Each name of the keep-lists (`CLAUDE_KEEP`, `CODEX_KEEP`), and each provider name a
#: recorded switch needs that the scrub never touches, classed once. A name is a
#: `credential` when its value can grant access or route traffic: a token, a key, a
#: secret, a password, or a proxy, endpoint or base URL. Every other name (a directory or
#: path, a user, shell or time-zone name, a region, profile or project name, an on/off
#: switch, a device label) is `recorded`. A launch records a recorded name's value and a
#: replay starts with it; a credential's value is never recorded, and reaches a replay
#: only when the user lists it in `relaunch.pass_env` or `relaunch.director_pass_env`.
ENV_CLASS = MappingProxyType({
    # Claude Code: where the configuration lives.
    "CLAUDE_CONFIG_DIR": RECORDED, "CLAUDE_CODE_PLUGIN_CACHE_DIR": RECORDED,
    "CLAUDE_CODE_PLUGIN_SEED_DIR": RECORDED, "CLAUDE_CODE_TMPDIR": RECORDED,
    # Claude Code: sign-in and the model provider.
    "CLAUDE_CODE_OAUTH_TOKEN": CREDENTIAL, "CLAUDE_CODE_OAUTH_REFRESH_TOKEN": CREDENTIAL,
    "CLAUDE_CODE_OAUTH_SCOPES": RECORDED, "CLAUDE_CODE_API_KEY_HELPER_TTL_MS": RECORDED,
    "CLAUDE_CODE_USE_BEDROCK": RECORDED, "CLAUDE_CODE_USE_VERTEX": RECORDED,
    "CLAUDE_CODE_USE_FOUNDRY": RECORDED, "CLAUDE_CODE_USE_MANTLE": RECORDED,
    "CLAUDE_CODE_USE_ANTHROPIC_AWS": RECORDED,
    "CLAUDE_CODE_SKIP_BEDROCK_AUTH": RECORDED, "CLAUDE_CODE_SKIP_VERTEX_AUTH": RECORDED,
    "CLAUDE_CODE_SKIP_FOUNDRY_AUTH": RECORDED, "CLAUDE_CODE_SKIP_MANTLE_AUTH": RECORDED,
    "CLAUDE_CODE_SKIP_ANTHROPIC_AWS_AUTH": RECORDED,
    "CLAUDE_CODE_SKIP_AWS_CRED_CACHE": RECORDED,
    "CLAUDE_CODE_AWS_CHAIN_RESOLVE_TIMEOUT_MS": RECORDED,
    "CLAUDE_CODE_DISABLE_AUTH_REFRESH_LOCK": RECORDED,
    "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST": RECORDED,
    # Claude Code: how it connects. The certificate and key are paths to files; the
    # passphrase opens the key.
    "CLAUDE_CODE_CLIENT_CERT": RECORDED, "CLAUDE_CODE_CLIENT_KEY": RECORDED,
    "CLAUDE_CODE_CLIENT_KEY_PASSPHRASE": CREDENTIAL, "CLAUDE_CODE_CERT_STORE": RECORDED,
    "CLAUDE_CODE_PROXY_RESOLVES_HOSTS": RECORDED,
    # Claude Code: security and traffic choices, its commands' wrappers, its shell.
    "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": RECORDED, "CLAUDE_CODE_SCRIPT_CAPS": RECORDED,
    "CLAUDE_CODE_MCP_ALLOWLIST_ENV": RECORDED,
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": RECORDED,
    "CLAUDE_CODE_SHELL_PREFIX": RECORDED, "CLAUDE_CODE_PROCESS_WRAPPER": RECORDED,
    "CLAUDE_CODE_SHELL": RECORDED, "CLAUDE_CODE_GIT_BASH_PATH": RECORDED,
    # Codex: where its state lives, sign-in, how it connects. The identity token file is
    # a path; the workload identity context may carry the identity itself.
    "CODEX_HOME": RECORDED, "CODEX_SQLITE_HOME": RECORDED,
    "CODEX_API_KEY": CREDENTIAL, "CODEX_ACCESS_TOKEN": CREDENTIAL,
    "OPENAI_API_KEY": CREDENTIAL, "OPENAI_FEDERATION_RULE_ID": RECORDED,
    "OPENAI_IDENTITY_TOKEN_FILE": RECORDED, "OPENAI_WORKLOAD_IDENTITY_CONTEXT": CREDENTIAL,
    "CODEX_CA_CERTIFICATE": RECORDED,
    # The providers the switches above pick: their region, profile, project and resource
    # names, and their keys, tokens and endpoints.
    "AWS_REGION": RECORDED, "AWS_DEFAULT_REGION": RECORDED, "AWS_PROFILE": RECORDED,
    "ANTHROPIC_SMALL_FAST_MODEL_AWS_REGION": RECORDED,
    "CLOUD_ML_REGION": RECORDED, "ANTHROPIC_VERTEX_PROJECT_ID": RECORDED,
    "GOOGLE_CLOUD_PROJECT": RECORDED, "ANTHROPIC_FOUNDRY_RESOURCE": RECORDED,
    "AWS_ACCESS_KEY_ID": CREDENTIAL, "AWS_SECRET_ACCESS_KEY": CREDENTIAL,
    "AWS_SESSION_TOKEN": CREDENTIAL, "AWS_BEARER_TOKEN_BEDROCK": CREDENTIAL,
    "ANTHROPIC_BEDROCK_BASE_URL": CREDENTIAL, "ANTHROPIC_VERTEX_BASE_URL": CREDENTIAL,
    "ANTHROPIC_FOUNDRY_BASE_URL": CREDENTIAL, "ANTHROPIC_FOUNDRY_API_KEY": CREDENTIAL,
    "ANTHROPIC_API_KEY": CREDENTIAL, "ANTHROPIC_AUTH_TOKEN": CREDENTIAL,
    "ANTHROPIC_BASE_URL": CREDENTIAL,
})
#: Provider names classed recorded by their start: Vertex's region for one model.
RECORDED_PREFIXES = ("VERTEX_REGION_",)
#: The switches that send Claude Code to another provider, whose sign-in then comes from
#: that provider's names.
PROVIDER_SWITCHES = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                     "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_MANTLE",
                     "CLAUDE_CODE_USE_ANTHROPIC_AWS")

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
    its Codex wrapper's per-launch settings and its headless flags, and
    `comms_codex_resume` whether that wrapper resumes a thread it is told to (read only
    for a resume). `windows` picks the command line limit.

    A replay of a stored launch sets the rest. `posture` is the start posture the
    identity block shows in place of the values' own (a relaunch's), which the values,
    and so the new row's `values_json`, never carry; None keeps the values' posture.
    `resume` says the session id is one the launch resumes, not one minted for it.
    `relaunched` marks a relaunch, whose handoff header says so. `extra_stamps` are
    stamps the replay adds to the option rows' own (`RELAUNCH`).
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
    comms_codex_resume: bool = False
    comms_headless: bool = False
    windows: bool = os.name == "nt"
    posture: str | None = None
    resume: bool = False
    relaunched: bool = False
    extra_stamps: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def posture_of(self, values: Mapping[str, object]) -> str:
        """The start posture the launch shows: the replay's, else the values' own."""
        if self.posture is not None:
            return self.posture
        return str(values.get("posture") or "")

    def options(self, values: Mapping[str, object]) -> dict[str, str]:
        """The stamps beyond the request's own: the option rows', then the replay's."""
        return {**option_stamps(values), **self.extra_stamps}


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
                   agent_role: str | None = None, keep_warm: bool = False) -> str:
    """The identity block a launched session starts with. A line appears only when it
    carries a value; `keep warm:` only on a keep-warm launch."""
    lines = [("session id:", session_id), ("project cwd:", cwd),
             ("project key:", project_key),
             ("identity:", f"{identity}@{scope}"
              if identity and scope and "@" not in identity else identity),
             ("handoff dir:", handoff_dir), ("start posture:", posture),
             ("agent role:", agent_role),
             ("keep warm:", KEEP_WARM_LINE if keep_warm else None)]
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
               handoff_text: str | None = None, posture: str | None = None,
               relaunched: bool = False) -> str | None:
    """The text of a launch's first turn, or None when it owes none: the first turn's own
    text (one header line, then the handoff's text and the brief), with the posture line
    second when `posture` is given. The header begins every first turn, so the text is
    never read as a flag or a subcommand; a relaunch's says it was relaunched."""
    from .. import first

    text = first.text(actor=actor, brief=brief, handoff=handoff or None,
                      handoff_text=(handoff_text or "") if handoff else None,
                      relaunched=relaunched)
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


_PLAIN_NAME = re.compile(r"[A-Za-z0-9._-]+")


def plain_name(name: str) -> bool:
    """Whether `name` is one plain file name: letters, digits, `.`, `_` and `-`, and not
    `.` or `..`."""
    return bool(_PLAIN_NAME.fullmatch(name)) and name not in (".", "..")


def scratch_dir(request_id: str) -> Path:
    """Where a scratch launch works: a folder named by its request id under
    `scratch_root()`, always as an absolute path (a slot launch is refused a relative
    one). Only the path: the launch makes the folder. Raises ValueError for a request id
    that is not a plain name, which would put the folder outside `scratch_root()`."""
    if not plain_name(request_id):
        raise ValueError(f"request id {request_id!r} is not a plain name")
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


def commands(values: Mapping[str, object]) -> tuple[str, ...]:
    """The session commands a launch grants through turns: `exit`, then `clear` or
    `compact` for `recycle_on_expiry`, and `compact` for `recycle_at_limit`."""
    granted = list(COMMANDS)
    expiry = values.get("recycle_on_expiry")
    if expiry in ("clear", "compact"):
        granted.append(str(expiry))
    if values.get("recycle_at_limit") in ("relaunch", "compact") and "compact" not in granted:
        granted.append("compact")
    return tuple(granted)


def option_stamps(values: Mapping[str, object]) -> dict[str, str]:
    """The stamps a launch's option rows set: keep-warm on, recycle on expiry's value,
    and the crossing mark when it recycles at its limit. A row at `off` stamps nothing."""
    env: dict[str, str] = {}
    if values.get("keep_warm") == "on":
        env[KEEP_WARM] = "on"
    if values.get("recycle_on_expiry") in ("clear", "compact"):
        env[RECYCLE_ON_EXPIRY] = str(values["recycle_on_expiry"])
    if values.get("recycle_at_limit") in ("relaunch", "compact") and values.get("limit_at"):
        env[USAGE_CROSSING_AT] = str(values["limit_at"])
    return env


def stamps(request_id: str, *, turns: bool = False, additions: Additions | None = None,
           warn_at: str | None = None, notes_dir: str | None = None,
           granted: Sequence[str] = COMMANDS,
           options: Mapping[str, str] | None = None) -> dict[str, str]:
    """The variables a launch stamps into its session's environment. `granted` is the
    commands turns grants (`commands(values)`), and must be the ones turns' launch
    additions were asked with, whose own render of the grant lands over this one;
    `options` the option rows' stamps and any a replay adds."""
    env: dict[str, str] = {}
    if turns:
        env[TURNS_COMMANDS] = ",".join(granted)
    if additions is not None:
        env.update(additions.env)
    if warn_at:
        env[USAGE_WARN_AT] = str(warn_at)
    if notes_dir:
        env[NOTES_DIR] = str(notes_dir)
    env.update(options or {})
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


#: The short list of names a launch records from its child's environment
#: (`launches.base_env_json`): the ones slots' server keeps, and `TMPDIR`, the private
#: temporary directory the harness and its tools write to. Besides these names, every
#: name starting with one of `BASE_PREFIXES`. A row made before the base was recorded
#: takes these alone from the replaying process (and on Windows `WINDOWS_BASE_NAMES`).
BASE_NAMES = ("HOME", "USER", "LOGNAME", "SHELL", "PATH", "LANG", "TZ", "TMPDIR")
BASE_PREFIXES = ("LC_", "XDG_")
#: The names a launch on Windows records besides those: the system's own locations and
#: names, which a process there needs to start (`SYSTEMROOT` above all), and the user's
#: profile, data and temp folders. Paths and plain names, never a secret.
WINDOWS_BASE_NAMES = (
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "OS",
    "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "COMPUTERNAME",
    "USERPROFILE", "USERNAME", "USERDOMAIN", "HOMEDRIVE", "HOMEPATH",
    "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "PUBLIC", "ALLUSERSPROFILE", "PROGRAMDATA",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
    "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "COMMONPROGRAMW6432",
)
#: The caller's terminal names, which a resume into the caller's own terminal adds.
TERMINAL_NAMES = ("TERM", "COLORTERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION")

#: The core's locators: its home and device label, and each module's per-root and device
#: names (`MOXI_<M>_HOME`, `MOXI_<M>_STATE_HOME` and the other roots, `MOXI_<M>_DEVICE`),
#: built from the core's own constants.
_HARNESS = names.HARNESS.upper() + "_"
_LOCATOR = re.compile(re.escape(_HARNESS) + "[A-Z0-9_]+_(?:" + "|".join(
    re.escape(n) for n in (*paths.LOCATORS, paths.DEVICE.removeprefix(_HARNESS))) + ")")


def locator(name: str) -> bool:
    """Whether `name` is one of the core's locators."""
    return name in (paths.HOME, paths.DEVICE) or bool(_LOCATOR.fullmatch(name))


def env_class(name: str) -> str | None:
    """`name`'s class for a replay (`RECORDED` or `CREDENTIAL`, from `ENV_CLASS` and
    `RECORDED_PREFIXES`), or None for a name of neither."""
    if (found := ENV_CLASS.get(name)) is not None:
        return found
    return RECORDED if name.startswith(RECORDED_PREFIXES) else None


def _short(name: str, windows: bool) -> bool:
    return (name in BASE_NAMES or name.startswith(BASE_PREFIXES)
            or (windows and name in WINDOWS_BASE_NAMES))


def base_env(environ: Mapping[str, str], *,
             windows: bool = os.name == "nt") -> dict[str, str | None]:
    """What a launch records of its child's environment `environ`: the value of each name
    of the recorded class that is set (the short list, `BASE_NAMES` and `BASE_PREFIXES`;
    the core's locators; the names `ENV_CLASS` classes recorded; on Windows
    `WINDOWS_BASE_NAMES`), and each credential that is set with None in place of its
    value, which is never recorded. Each is kept under the name as `environ` spells it.
    Windows reads names without case, so there they are matched so too."""
    out: dict[str, str | None] = {}
    for name, value in environ.items():
        key = name.upper() if windows else name
        kind = env_class(key)
        if kind == CREDENTIAL:
            out[name] = None
        elif kind == RECORDED or _short(key, windows) or locator(key):
            out[name] = value
    return out


def short_env(environ: Mapping[str, str], *,
              windows: bool = os.name == "nt") -> dict[str, str]:
    """The short list alone (`BASE_NAMES`, `BASE_PREFIXES`) of `environ`, and on Windows
    the names a process there needs to start (`WINDOWS_BASE_NAMES`): the base of a replay
    of a row made before the base was recorded."""
    norm = str.upper if windows else (lambda name: name)
    return {name: value for name, value in environ.items() if _short(norm(name), windows)}


def replay_env(base: Mapping[str, str | None], environ: Mapping[str, str],
               pass_env: Sequence[str] = (), *,
               terminal: bool = False) -> dict[str, str]:
    """The environment a replay's child is built from, before the scrub and the stamps:
    `base` (the row's recorded values; a credential recorded as set has no value and
    adds nothing), then each name `pass_env` lists that `environ`, the replaying
    process's environment, holds; for a resume into the caller's own terminal
    (`terminal`), the caller's terminal names it holds (`TERMINAL_NAMES`). Nothing else of
    `environ` comes in."""
    env = {name: value for name, value in base.items() if value is not None}
    if terminal:
        env.update({name: environ[name] for name in TERMINAL_NAMES if name in environ})
    env.update({name: environ[name] for name in pass_env if name in environ})
    return env


def no_credentials(base: Mapping[str, str | None], passed: Sequence[str]) -> bool:
    """Whether a replay starts with no sign-in it may need: the recorded base shows a
    provider switch on, or a credential that was set at launch, and `passed` (the names
    the replay's pass list carried) is empty."""
    if passed:
        return False
    switched = any((base.get(name) or "").strip().lower() not in ("", "0", "false")
                   for name in PROVIDER_SWITCHES)
    return switched or any(value is None for value in base.values())


#: The start posture of a self relaunch, which names its handoff.
RELAUNCHED = "relaunched (handoff: {path})"


# -- sizes ------------------------------------------------------------------------------

def encode(value: object) -> str:
    """`value` as the launch log stores it."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def check_sizes(argv: Sequence[str], *, values: Mapping[str, object],
                brief: str | None = None, env: Mapping[str, str] | None = None,
                prompt: str | None = None, windows: bool = os.name == "nt") -> None:
    """Refuse a launch past a limit: `store.TooLarge` for a brief, `values_json` or
    `argv_json` past the launch log's cap, `TooLong` for a command line past the
    system's. A `values_json` refusal names its largest value too, which is the one to
    shorten (an inline brief or instructions, most often)."""
    for name, text in (("brief", brief), ("values_json", encode(dict(values))),
                       ("argv_json", encode(list(argv)))):
        if text is not None and (size := len(text.encode("utf-8"))) > store.CAP:
            largest = None
            if name == "values_json" and values:
                largest = max(((key, len(encode(value).encode("utf-8")))
                               for key, value in values.items()), key=lambda kv: kv[1])
            raise store.TooLarge(name, size, largest)
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


__all__ = ["BASE_NAMES", "BASE_PREFIXES", "CODEX_KEEP", "COMMANDS", "CREDENTIAL",
           "ENV_CLASS", "HEADER", "KEEP_WARM", "KEEP_WARM_LINE", "NOTES_DIR",
           "PROVIDER_SWITCHES", "RECORDED", "RECORDED_PREFIXES", "RECYCLE_ON_EXPIRY",
           "RELAUNCH", "RELAUNCHED", "REQUEST", "SCRUB_NAMES", "SCRUB_PREFIXES",
           "TERMINAL_NAMES", "TURNS_COMMANDS", "USAGE_CROSSING_AT", "USAGE_WARN_AT",
           "WINDOWS_BASE_NAMES", "WINDOWS_LINE", "WORD", "Additions", "Composition",
           "Context", "NoBrief", "NotYet", "PromptFile", "TooLong", "TurnsAdditions",
           "Unreadable", "agent_role", "base_env", "check_sizes", "commands", "encode",
           "env_class", "environment", "first_turn", "identity_block", "locator",
           "no_credentials", "option_stamps", "prompt_path", "replay_env", "scratch_dir",
           "scratch_root", "scratch_root_problem", "scrub", "seat_text", "short_env",
           "stamps", "write_prompt"]
