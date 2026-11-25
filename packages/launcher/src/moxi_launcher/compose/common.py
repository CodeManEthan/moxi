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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from moxi import files, modules, names, paths

from .. import catalog, store
from ..catalog import TAKES_POLICY
from ..codex_trust import Trust
from ..listeners import RELAUNCH_ENV, REQUEST_ENV

#: The first line of the identity block.
HEADER = "--- LAUNCH IDENTITY (stamped at launch) ---"
_WIDTH = len("start posture: ")

#: The commands every launch with turns grants; `commands(values)` adds those its
#: recycle rows need.
COMMANDS = ("exit",)
#: The launch's keep-warm and recycle-on-expiry options, read by cache's timer.
KEEP_WARM_ENV = "MOXI_LAUNCHER_KEEP_WARM"
RECYCLE_ON_EXPIRY_ENV = "MOXI_LAUNCHER_RECYCLE_ON_EXPIRY"
#: The identity block's line on a keep-warm launch.
KEEP_WARM_LINE = ("on; subagents of yours and agents you direct at work keep you warm; "
                  'for other work you hand out, open a hold with "moxi launcher keep-warm '
                  'hold <name>" and release it when the work is done')
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


def _stamp(env: dict[str, str], module: str, interface: int, name: str, value: str) -> None:
    """Stamp `value` under the environment name an optional module exports as `name`.
    Nothing is stamped when the module can't be loaded here: only it reads the variable,
    and a replayed row may still carry a value for a module removed since."""
    found = modules.load(module, interface=interface)
    if found.ok and found.api is not None:
        env[getattr(found.api, name)] = value

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
    `agent` role was asked for and did not resolve. `user_settings` are the settings the
    launch's own `--settings` gives, read only for a launch that carries the bypass
    warning's skip key (`skip_key_owed`). `codex_trust` is how a Codex launch settles its
    folder's trust (`codex_trust.Trust`), None to pass only what the `trust_cwd` row
    gives. `runs_dir` holds the system prompt file and the
    settings run file. `moxi` is the command comms' wrappers run under; `host` turns' Codex host, None
    when it is not installed. `comms_codex` and `comms_headless` say whether comms takes
    its Codex wrapper's per-launch settings and its headless flags, and
    `comms_codex_resume` whether that wrapper resumes a thread it is told to (read only
    for a resume). `comms_has_policy` says whether comms has policy directors, so its
    wrappers take `--no-policy-directors` (`presets.comms_has_policy`). `is_windows` picks
    the command line limit.

    A replay of a stored launch sets the rest. `posture` is the start posture the
    identity block shows in place of the values' own (a relaunch's), which the values,
    and so the new row's `values_json`, never carry; None keeps the values' posture.
    `resume` says the session id is one the launch resumes, not one minted for it.
    `fork_of` is the session id a fork launch forks: the harness starts the launch's
    session from a copy of that session's conversation.
    `relaunched` marks a relaunch, whose handoff header says so. `extra_stamps` are
    stamps the replay adds to the option rows' own (`RELAUNCH_ENV`).
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
    user_settings: Mapping[str, object] | None = None
    codex_trust: Trust | None = None
    runs_dir: str = ""
    moxi: str = "moxi"
    host: str | None = None
    comms_codex: bool = False
    comms_codex_resume: bool = False
    comms_headless: bool = False
    comms_has_policy: bool = False
    windows: bool = os.name == "nt"
    posture: str | None = None
    resume: bool = False
    fork_of: str | None = None
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
class SettingsFile:
    """The settings file a Claude launch passes with `--settings`: where it goes and what it
    holds."""

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
    `not_applied`. `prompt_file` is Claude's system prompt file, and `settings_file` the
    settings file a bypass slot launch passes, each written before the start. `warnings`
    are one line each.
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
    settings_file: SettingsFile | None = None
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


def agent_role(role: str | None, *, found: bool | None) -> str | None:
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


# -- the bypass warning's skip key ------------------------------------------------------

#: The suffix of a Claude launch's settings run file, after its request id.
SETTINGS_SUFFIX = ".settings.json"
#: The Claude Code setting that skips its bypass-permissions warning.
SKIP_BYPASS_WARNING = "skipDangerousModePermissionPrompt"
#: The permission mode whose slot launches carry it.
BYPASS = "bypassPermissions"


def settings_path(runs_dir: str | os.PathLike, request_id: str) -> str:
    """Where a launch's settings run file goes: `<runs>/<request id>.settings.json`."""
    return str(Path(runs_dir) / f"{request_id}{SETTINGS_SUFFIX}")


def skip_key_owed(harness: str, values: Mapping[str, object]) -> bool:
    """Whether a launch carries the bypass warning's skip key: a Claude launch into a slot
    whose permission mode, the one its argv passes, is `bypassPermissions`. A terminal
    launch shows the warning to the person who typed it, and a headless one shows none."""
    return (harness == "claude" and values.get("target") == "slot"
            and values.get("permission_mode") == BYPASS)


def settings_value(extra: Iterable[object]) -> str | None:
    """The value of the `--settings` the launch's extra flags pass, or None."""
    tokens = [str(token) for token in extra]
    for i, token in enumerate(tokens):
        if token.startswith(catalog.SETTINGS_FLAG + "="):
            return token.split("=", 1)[1]
        if token == catalog.SETTINGS_FLAG and i + 1 < len(tokens):
            return tokens[i + 1]
    return None


def without_settings(extra: Sequence[str]) -> list[str]:
    """`extra` with its `--settings` and that flag's value taken out."""
    kept: list[str] = []
    skip = False
    for token in extra:
        if skip:
            skip = False
        elif token == catalog.SETTINGS_FLAG:
            skip = True
        elif not token.startswith(catalog.SETTINGS_FLAG + "="):
            kept.append(token)
    return kept


def read_settings(value: str, cwd: str) -> dict[str, object]:
    """The settings a `--settings` value gives: the JSON object it spells (a value starting
    with `{`), or the one in the file it names (relative to `cwd`), which is read and never
    changed. `Unreadable` when the string or the file is not a JSON object."""
    if value.lstrip().startswith("{"):
        try:
            given = json.loads(value)
        except ValueError as exc:
            raise Unreadable(catalog.EXTRA, f"--settings {value} is not JSON: {exc}",
                             "pass a JSON object or a settings file") from exc
        return given
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path(cwd) / path
    try:
        given = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise Unreadable(catalog.EXTRA, f"the --settings file {path} can't be read as "
                         f"JSON: {exc}", "fix the file, or leave --settings out") from exc
    if not isinstance(given, dict):
        raise Unreadable(catalog.EXTRA, f"the --settings file {path} is not a JSON object",
                         "fix the file, or leave --settings out")
    return given


def settings_file(runs_dir: str, request_id: str,
                  user_settings: Mapping[str, object] | None) -> SettingsFile:
    """The settings run file of a launch that carries the skip key: the user's own
    settings, when the launch passes some, with the key added."""
    merged = {**dict(user_settings or {}), SKIP_BYPASS_WARNING: True}
    return SettingsFile(settings_path(runs_dir, request_id),
                        json.dumps(merged, indent=2, sort_keys=True) + "\n")


def write_settings(settings: SettingsFile) -> None:
    """Write a launch's settings run file, readable by its owner only, its folder 0700."""
    files.private_dir(Path(settings.path).parent)
    files.atomic_write(settings.path, settings.text, mode=0o600)


# -- the opening prompt -----------------------------------------------------------------

def first_turn(by: str, *, brief: str | None = None, handoff: str | None = None,
               handoff_text: str | None = None, posture: str | None = None,
               relaunched: bool = False) -> str | None:
    """The text of a launch's first turn, or None when it owes none: the first turn's own
    text (one header line, then the handoff's text and the brief), with the posture line
    second when `posture` is given. The header begins every first turn, so the text is
    never read as a flag or a subcommand; a relaunch's says it was relaunched."""
    from .. import first

    text = first.text(by=by, brief=brief, handoff=handoff or None,
                      handoff_text=(handoff_text or "") if handoff else None,
                      relaunched=relaunched)
    if text is None or not posture:
        return text
    header, _, rest = text.partition("\n")
    return f"{header}\nstart posture: {posture}" + (f"\n{rest}" if rest else "")


#: The folder this user's scratch parent is made in, where there is a uid. Fixed: `TMPDIR`
#: is never read, since one under a folder notes keeps notes for would give every scratch
#: folder a notes home.
SCRATCH_TMP = Path("/tmp")


def scratch_root() -> Path:
    """The folder this user's scratch launches go under: `/tmp/scratch-<uid>`, by the
    numeric uid, one per user, so no other user owns it, and none can move a launch's
    folder out of it and put their own in its place. Where there is no uid (Windows) it is
    `scratch-<user name>` in the user's own temporary folder, `TEMP` or `TMP`."""
    if hasattr(os, "getuid"):
        return SCRATCH_TMP / f"scratch-{os.getuid()}"
    try:
        who = getpass.getuser()
    except Exception:  # noqa: BLE001  (no name to be had: one folder for all)
        who = "user"
    who = re.sub(r"[^A-Za-z0-9._-]", "_", who) or "user"
    temp = (os.environ.get("TEMP") or os.environ.get("TMP")
            or str(Path.home() / "AppData" / "Local" / "Temp"))
    return Path(os.path.abspath(temp)) / f"scratch-{who}"


_PLAIN_NAME = re.compile(r"[A-Za-z0-9._-]+")


def plain_name(name: str) -> bool:
    """Whether `name` is one plain file name: letters, digits, `.`, `_` and `-`, and not
    `.` or `..`."""
    return bool(_PLAIN_NAME.fullmatch(name)) and name not in (".", "..")


_FOLD_UNSAFE = re.compile(r"[^a-z0-9-]+")


def scratch_name(session_name: str) -> str:
    """The name of a scratch launch's folder: the session name folded to lowercase
    letters, digits and single dashes, at most 63 characters, or `launch` when nothing is
    left, whatever the name (`Foo_Bar` works in `foo-bar`). A folded name holds no `.`,
    `..` or separator, so the folder stays under `scratch_root()`."""
    folded = re.sub(r"-{2,}", "-", _FOLD_UNSAFE.sub("-", session_name.lower())).strip("-")
    return folded[:63].strip("-") or "launch"


def scratch_dir(session_name: str) -> Path:
    """Where a scratch launch works: a folder named by its session name, folded
    (`scratch_name`), under `scratch_root()`, always as an absolute path (a slot launch is
    refused a relative one). Only the path: the launch makes the folder, and a later
    launch under the same name works in the same one."""
    return scratch_root() / scratch_name(session_name)


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
    except OSError as exc:
        return f"{root} can't be read: {exc}"
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

def scrub(env: Mapping[str, str], session_env: frozenset[str] | set[str] = frozenset(),
          *, is_windows: bool = os.name == "nt") -> dict[str, str]:
    """A copy of `environ` without any session's stamps.

    Removes every `CLAUDE*` name but the user's configuration and sign-in
    (`CLAUDE_KEEP`), `AI_AGENT`, and each name in `session_env`, the modules'
    `SESSION_ENV` keys, where a key ending in `_` is a prefix.
    """
    norm = str.upper if is_windows else (lambda name: name)
    claude = tuple(norm(p) for p in SCRUB_PREFIXES)
    keep = {norm(n) for n in CLAUDE_KEEP}
    prefixes = tuple(norm(n) for n in session_env if n.endswith("_"))
    names = {norm(n) for n in (*SCRUB_NAMES, *(n for n in session_env
                                                if not n.endswith("_")))}

    def scrubbed(name: str) -> bool:
        name = norm(name)
        return (name in names or name.startswith(prefixes)
                or (name.startswith(claude) and name not in keep))

    return {name: value for name, value in env.items() if not scrubbed(name)}


def headless_words(values: Mapping[str, object], brief: str) -> list[str]:
    """The flags of comms' wrapper for a headless launch: `--headless`, one `--exit` per
    `headless_exit` value, in order, and `--brief` with the launch's first turn."""
    words = ["--headless"]
    for value in values.get("headless_exit") or ():
        words += ["--exit", str(value)]
    return words + ["--brief", brief]


def director_words(values: Mapping[str, object], ctx: Context) -> list[str]:
    """The director flags of comms' wrapper: one `--director` per `director` value, in
    order, then `--no-policy-directors` when the launch's `takes_policy_directors` is
    false. A comms whose wrappers don't take that flag (`Context.comms_has_policy` false)
    has no policy directors to opt out of, and such a launch is refused (`NotYet`)
    rather than composed with a flag its wrapper refuses."""
    words: list[str] = []
    for director in values.get("director") or ():
        words += ["--director", str(director)]
    if values.get(TAKES_POLICY) is False:
        if not ctx.comms_has_policy:
            raise NotYet(TAKES_POLICY, "takes_policy_directors = false needs comms' "
                         "policy directors, which this comms does not have",
                         "upgrade moxi-comms, or leave takes_policy_directors out")
        words.append("--no-policy-directors")
    return words


def commands(values: Mapping[str, object]) -> tuple[str, ...]:
    """The session commands a launch grants through turns: `exit`, then `clear` or
    `compact` exactly when `recycle_on_expiry` is that value. `recycle_at_limit` grants
    nothing: a relaunch exits through `exit`."""
    granted = list(COMMANDS)
    expiry = values.get("recycle_on_expiry")
    if expiry in ("clear", "compact"):
        granted.append(str(expiry))
    return tuple(granted)


def option_stamps(values: Mapping[str, object]) -> dict[str, str]:
    """The stamps a launch's option rows set: keep-warm on, recycle on expiry's value,
    and the crossing mark (`limit_mark`) for a `relaunch` launch only where it is told it
    may recycle itself (`selfrecycle.missing` with `told`): its arm would be accepted and
    it warns at a mark.
    The subconscious row stamps its mode under memory's variable. A row at `off` stamps
    nothing."""
    env: dict[str, str] = {}
    if values.get("keep_warm") == "on":
        env[KEEP_WARM_ENV] = "on"
    if values.get("recycle_on_expiry") in ("clear", "compact"):
        env[RECYCLE_ON_EXPIRY_ENV] = str(values["recycle_on_expiry"])
    if relaunch_holds(values):
        _stamp(env, "usage", 1, "CROSSING_AT_ENV", limit_mark(values))
    if values.get("subconscious") in ("automatic", "on-demand"):
        _stamp(env, "memory", 1, "SUBCONSCIOUS_ENV", str(values["subconscious"]))
    return env


def relaunch_holds(values: Mapping[str, object]) -> bool:
    """Whether a launch of `values` (its `harness`, `target`, `cwd` and the rest, as the
    composer has them) is set to `recycle_at_limit = relaunch`, can recycle itself and is
    told it may at its warn-at mark, by `selfrecycle.missing` with `told`; a launch not yet
    placed in a slot counts as in one when its target is a slot."""
    from .. import selfrecycle

    if values.get("recycle_at_limit") != "relaunch":
        return False
    target = values.get("target")
    return selfrecycle.missing(
        harness=str(values.get("harness") or "claude"), target=str(target or ""),
        in_slot=target == "slot", cwd=str(values.get("cwd") or "") or None,
        values=values, source=None, told=True) is None


def limit_mark(values: Mapping[str, object]) -> str:
    """The mark a launch that relaunches itself is stamped to cross at, read when the
    launch is composed: its `warn_at`, the mark usage warns it at, so the relaunch line
    rides on that warning. `relaunch_holds` has made sure it is a mark."""
    return str(values.get("warn_at") or "")


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
        _stamp(env, "turns", 2, "COMMANDS_ENV", ",".join(granted))
    if additions is not None:
        env.update(additions.env)
    if warn_at:
        _stamp(env, "usage", 1, "WARN_AT_ENV", str(warn_at))
    if notes_dir:
        _stamp(env, "notes", 1, "NOTES_DIR_ENV", str(notes_dir))
    env.update(options or {})
    env[REQUEST_ENV] = request_id
    return env


def child_env(caller_env: Mapping[str, str], composition: Composition,
                session_env: frozenset[str] | set[str] = frozenset(), *,
                is_windows: bool = os.name == "nt") -> dict[str, str]:
    """The whole environment the child gets: `environ` scrubbed, then the launch's
    stamps."""
    env = scrub(caller_env, session_env, is_windows=is_windows)
    env.update(composition.env)
    return env


#: The short list of names a launch records from its child's environment
#: (`launches.base_env_json`): the ones slots' server keeps, `TMPDIR`, the private
#: temporary directory the harness and its tools write to, and `CALLER_FIRST_NAMES`.
#: Besides these names, every name starting with one of `BASE_PREFIXES`. A row made
#: before the base was recorded takes these alone from the replaying process (and on
#: Windows `WINDOWS_BASE_NAMES`).
BASE_NAMES = ("HOME", "USER", "LOGNAME", "SHELL", "PATH", "LANG", "TZ", "TMPDIR",
              "DBUS_SESSION_BUS_ADDRESS")
#: The recorded names a replay takes from the replaying process when it holds them, and
#: from the record only when it doesn't: the user's session bus, which can move at each
#: login. On Linux the system keychain is reached over it, so a session without it can't
#: read a secret kept there. Elsewhere the name is absent and adds nothing.
CALLER_FIRST_NAMES = ("DBUS_SESSION_BUS_ADDRESS",)
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
    re.escape(n) for n in (*paths.LOCATORS, paths.DEVICE_ENV.removeprefix(_HARNESS))) + ")")


def locator(name: str) -> bool:
    """Whether `name` is one of the core's locators."""
    return name in (paths.HOME_ENV, paths.DEVICE_ENV) or bool(_LOCATOR.fullmatch(name))


def env_class(name: str) -> str | None:
    """`name`'s class for a replay (`RECORDED` or `CREDENTIAL`, from `ENV_CLASS` and
    `RECORDED_PREFIXES`), or None for a name of neither."""
    if (found := ENV_CLASS.get(name)) is not None:
        return found
    return RECORDED if name.startswith(RECORDED_PREFIXES) else None


def _short(name: str, *, is_windows: bool) -> bool:
    return (name in BASE_NAMES or name.startswith(BASE_PREFIXES)
            or (is_windows and name in WINDOWS_BASE_NAMES))


def base_env(env: Mapping[str, str], *,
             is_windows: bool = os.name == "nt") -> dict[str, str | None]:
    """What a launch records of its child's environment `environ`: the value of each name
    of the recorded class that is set (the short list, `BASE_NAMES` and `BASE_PREFIXES`;
    the core's locators; the names `ENV_CLASS` classes recorded; on Windows
    `WINDOWS_BASE_NAMES`), and each credential that is set with None in place of its
    value, which is never recorded. Each is kept under the name as `environ` spells it.
    Windows reads names without case, so there they are matched so too."""
    out: dict[str, str | None] = {}
    for name, value in env.items():
        key = name.upper() if is_windows else name
        kind = env_class(key)
        if kind == CREDENTIAL:
            out[name] = None
        elif kind == RECORDED or _short(key, is_windows=is_windows) or locator(key):
            out[name] = value
    return out


def short_env(env: Mapping[str, str], *,
              is_windows: bool = os.name == "nt") -> dict[str, str]:
    """The short list alone (`BASE_NAMES`, `BASE_PREFIXES`) of `environ`, and on Windows
    the names a process there needs to start (`WINDOWS_BASE_NAMES`): the base of a replay
    of a row made before the base was recorded."""
    norm = str.upper if is_windows else (lambda name: name)
    return {name: value for name, value in env.items() if _short(norm(name), is_windows=is_windows)}


def replay_env(base: Mapping[str, str | None], caller_env: Mapping[str, str],
               pass_env: Sequence[str] = (), *,
               terminal: bool = False) -> dict[str, str]:
    """The environment a replay's child is built from, before the scrub and the stamps:
    `base` (the row's recorded values; a credential recorded as set has no value and
    adds nothing), with each of `CALLER_FIRST_NAMES` that `environ`, the replaying
    process's environment, holds taken from it instead; then each name `pass_env` lists
    that `environ` holds; for a resume into the caller's own terminal (`terminal`), the
    caller's terminal names it holds (`TERMINAL_NAMES`). Nothing else of `environ` comes
    in."""
    env = {name: value for name, value in base.items() if value is not None}
    env.update({name: caller_env[name] for name in CALLER_FIRST_NAMES
                if caller_env.get(name)})
    if terminal:
        env.update({name: caller_env[name] for name in TERMINAL_NAMES if name in caller_env})
    env.update({name: caller_env[name] for name in pass_env if name in caller_env})
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
                prompt: str | None = None, is_windows: bool = os.name == "nt") -> None:
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
    if is_windows:
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


__all__ = ["BASE_NAMES", "BASE_PREFIXES", "CALLER_FIRST_NAMES", "CODEX_KEEP", "COMMANDS",
           "CREDENTIAL", "ENV_CLASS", "HEADER", "KEEP_WARM_ENV", "KEEP_WARM_LINE",
           "PROVIDER_SWITCHES", "RECORDED", "RECORDED_PREFIXES", "RECYCLE_ON_EXPIRY_ENV",
           "RELAUNCH_ENV", "RELAUNCHED", "REQUEST_ENV", "SCRUB_NAMES", "SCRUB_PREFIXES",
           "TERMINAL_NAMES",
           "WINDOWS_BASE_NAMES", "WINDOWS_LINE", "WORD", "Additions", "Composition",
           "BYPASS", "Context", "NoBrief", "NotYet", "PromptFile", "SETTINGS_SUFFIX",
           "SKIP_BYPASS_WARNING", "SettingsFile", "TooLong", "TurnsAdditions",
           "Unreadable", "agent_role", "base_env", "check_sizes", "commands",
           "director_words", "encode",
           "env_class", "child_env", "first_turn", "headless_words", "identity_block",
           "limit_mark", "locator", "no_credentials", "option_stamps",
           "prompt_path", "read_settings", "relaunch_holds",
           "replay_env", "scratch_dir", "scratch_name", "scratch_root",
           "scratch_root_problem", "scrub", "seat_text", "settings_file", "settings_path",
           "settings_value", "short_env",
           "skip_key_owed", "stamps", "without_settings", "write_prompt", "write_settings"]
