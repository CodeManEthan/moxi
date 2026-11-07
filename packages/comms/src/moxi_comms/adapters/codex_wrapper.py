"""`moxi comms codex` (and `moxi-comms-codex`) -- the Codex wrapper: one command starts a
Codex session with a comms identity, through turns' Codex host.

    moxi comms codex --name NAME [--scope SCOPE | --global] [--director NAME]...
                     [--model M] [--sandbox MODE | --permissions PROFILE]
                     [--option KEY=VALUE]... [--options-file F]
                     [--resume THREAD_ID] [--no-window] [--dry-run]

    moxi-comms-codex IDENTITY [--config PATH] [--window] [--dry-run]

The settings flags are turns' Codex host grammar (`moxi-turns-codex-host`), so a launch
reads the same with or without comms. The second form is for an existing roster: it reads
the row IDENTITY names in the `[codex]` table of comms' config file (or of the file
`--config` names, checked whole) and turns the row into the same flags, so there is one
launch path. A roster launch runs with no window unless it asks for one.

A launch starts a new thread unless it is told to resume one. `--resume THREAD_ID`, in
the `--name` form only, goes to the host with the other settings, so the host resumes
that thread (`thread/resume`) instead of starting one, and the adapter reports the
launch as a resume of it, not as a new session. comms trusts the caller's id, as its
Claude wrapper trusts `--resume U`: it keeps no history of a name's threads to check it
against. A thread Codex can't resume fails in turns' host, loudly: a launch with its
window ends when the window gives up, and one run with `--no-window` keeps retrying while
the name stays bound, as a failed first start does. A roster launch always starts a new
thread.

What the wrapper does, in order:

  1. builds the host's line: `--cwd` (the current directory, or the row's), the settings
     flags, then what comms adds (below), and checks it whole with turns' own parser, so
     a flag the host would refuse refuses the launch before anything starts (exit 2).
     Before it adds comms' flags it asks the daemon whether it serves comms' tools, since
     the answer decides the network default;
  2. when the daemon serves the tools, mints the session's token and stamps it with the
     URL the daemon answered (`MOXI_COMMS_MCP_TOKEN`, `MOXI_COMMS_MCP_URL`) into the
     environment the adapter and the host get, so the host's launch additions carry
     comms' MCP server, and layer one names the tools. When it doesn't, the session
     starts without them, and the wrapper says so. When the agent's shell can't reach
     comms, it stamps `MOXI_COMMS_SHELL_REACH=off` beside them;
  3. makes the session's runtime directory (0700), which holds layer one; the claim
     socket is beside it in comms' runtime directory (`paths.claim_socket`);
  4. forks the session's adapter (`codex.CodexAdapter`). The adapter binds the name with
     the daemon and listens on the claim socket, `MOXI_COMMS_CLAIM_SOCKET`, with this
     process as the claim's root, then says so. The wrapper waits for its word, at most
     `READY_WAIT` seconds, and says once that it is waiting when two seconds pass with no
     word. A bind that isn't answered in that time ends the launch with `TEMPFAIL` (75):
     the host never starts under a name the daemon hasn't bound, and the adapter, finding
     nobody waiting for its word, lets the name go;
  5. writes layer one, the thread's developer instructions, to a 0600 file in that
     directory;
  6. execs `moxi-turns-codex-host` (found by `moxi.paths.script`, never on PATH) with
     the line and `--instructions-file`. The host keeps this process's pid, so it is the
     session's process in the terminal, and the adapter finds its session with
     `turns.wait_ready(pid=<that pid>)`.

The host is this path's one caller of turns' launch additions: the wrapper calls none,
and neither does a launcher that goes through it. The host also checks every load
answer of a thread that names a sandbox against the sandbox it asked for. The host's
environment is this process's, with the identity stamp and the claim socket over it and
comms' own variables a launch didn't stamp scrubbed first; it gets no lifeline, so it
ends with its window, its endpoint or a signal.

What comms adds to the host's settings, so the agent can use comms from its shell:

  * multi-agent off, `--option agents.enabled=false`, always: a comms identity is one
    agent among comms' agents, not the lead of a team of its own;
  * with no sandbox and no permission profile named, `--sandbox workspace-write`, a
    roster row's default;
  * on a sandbox (not a permission profile), the `workspace-write` settings: network
    access, unless the launch or its row sets it, off when the launch carries comms'
    tools (the agent sends through them; Codex's MCP client runs outside the shell's
    sandbox) and on when it doesn't (the agent's shell reaches the daemon socket only
    with it), and the directory the agent's own `comms` writes the record into added to
    the writable roots.

These give the thread parameters a roster row's `launch_options()` gives.

Exit codes are `moxi.exit`'s. A bad flag, a bad roster row or a config file that doesn't
load is `USAGE`; a name the daemon refuses (held, reserved, retired) is `REFUSED`, with
the daemon's words; a daemon that isn't running, or a bind it doesn't answer in
`READY_WAIT`, is `TEMPFAIL`; all of them before Codex starts. A host that can't start is `ERROR`. Once the host runs, its exit status is the
launch's.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from moxi import exit as moxi_exit

from .. import directors as directors_mod
from ..claimhold import CLAIM_SOCKET_ENV
from ..config import ConfigError
from ..context import BARE_COMMS, own_comms
from ..mcp.additions import SHELL_REACH_ENV, SHELL_REACH_OFF, TOKEN_ENV
from ..paths import claim_socket, codex_threads_dir, log_dir
from ..scoping import GLOBAL, NAME_RE, qualify
from . import codex_roster
from .harness import plan_comms, plan_head
from .session import stamp_tools, tools_endpoint, tools_off
from .word import BOUND, FAILED, await_word, exit_of, teller, word_for

PROG = "moxi-comms-codex"
#: turns' Codex host, the console script the wrapper execs.
HOST = "moxi-turns-codex-host"
#: How long the wrapper waits for the adapter's word. A launch whose bind isn't answered
#: in this time ends with `moxi.exit.TEMPFAIL` and never starts the host.
READY_WAIT = 15.0
#: The option comms always sets: Codex's multi-agent mode off.
MULTI_AGENT_OFF = "agents.enabled=false"
#: The sandbox a launch gets when it names neither a sandbox nor a profile.
DEFAULT_SANDBOX = codex_roster.DEFAULT_SANDBOX
#: Layer one's file, in the session's runtime directory.
INSTRUCTIONS_NAME = "instructions.md"

log = logging.getLogger("comms.codex.wrapper")


class Refused(Exception):
    """The launch is refused before anything starts: `code` is its exit code."""

    def __init__(self, text: str, code: int = moxi_exit.USAGE):
        super().__init__(text)
        self.code = code


# --------------------------------------------------------------------------- the flags

_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")


def toml_key(part: str) -> str:
    """One part of a dotted key: bare when TOML allows it, else a quoted string."""
    return part if _BARE_KEY.fullmatch(part) else _toml_string(part)


def _toml_string(text: str) -> str:
    # A JSON string is a TOML basic string but for DEL, which TOML wants escaped.
    return json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007f")


def toml_value(value) -> str:
    """`value` written as a TOML value, the form `--option KEY=VALUE` reads."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{toml_key(str(k))} = {toml_value(v)}"
                               for k, v in value.items()) + "}"
    raise ValueError(f"{value!r} has no TOML form")


def option(key: list, value) -> list[str]:
    """One `--option KEY=VALUE`, KEY a dotted key of `key`'s parts."""
    return ["--option", ".".join(toml_key(str(p)) for p in key) + "=" + toml_value(value)]


def row_flags(agent: codex_roster.CodexAgent) -> list[str]:
    """A roster row's settings as host flags: what `launch_options()` gives for the row,
    less the record's directory, which comms adds to every launch (`comms_flags`)."""
    flags: list[str] = []
    if agent.permissions:
        flags += ["--permissions", agent.permissions]
        if agent.profile:
            flags += option(["permissions", agent.permissions], dict(agent.profile))
    else:
        flags += ["--sandbox", agent.sandbox or DEFAULT_SANDBOX]
        flags += option(["sandbox_workspace_write", "writable_roots"],
                        [str(r) for r in agent.writable_roots])
        if agent.network_access is not None:
            # A row that names none leaves it to the launch (`comms_flags`).
            flags += option(["sandbox_workspace_write", "network_access"],
                            bool(agent.network_access))
    if agent.model:
        flags += ["--model", agent.model]
    return flags


def comms_flags(options: dict, added_root=None, *, network: bool = True) -> list[str]:
    """What comms adds to a launch whose host flags give `options` (the thread
    parameters `turns.codex.launch_args` reads from them). See the module's docstring.
    `network` is the network access a sandbox gets when the flags name none: off for a
    launch that carries comms' tools, on for one that doesn't."""
    flags: list[str] = []
    sandbox = options.get("sandbox")
    if sandbox is None and options.get("permissions") is None:
        sandbox = DEFAULT_SANDBOX
        flags += ["--sandbox", sandbox]
    if sandbox is not None:
        settings = (options.get("config") or {}).get("sandbox_workspace_write")
        settings = settings if isinstance(settings, dict) else {}
        if "network_access" not in settings:
            flags += option(["sandbox_workspace_write", "network_access"], bool(network))
        roots = settings.get("writable_roots")
        roots = [str(r) for r in roots] if isinstance(roots, list) else []
        if added_root is not None:
            known = {os.path.realpath(r) for r in roots}
            if os.path.realpath(str(added_root)) not in known:
                flags += option(["sandbox_workspace_write", "writable_roots"],
                                [*roots, str(added_root)])
    return flags + ["--option", MULTI_AGENT_OFF]


def shell_reach_off(options: dict) -> bool:
    """Whether the agent's shell can't reach comms under the thread parameters
    `options`: a `read-only` sandbox, or `workspace-write` with network access off. Any
    other sandbox, and a permission profile (whose network the wrapper doesn't read),
    say nothing."""
    if options.get("permissions"):
        return False
    sandbox = options.get("sandbox")
    if sandbox == "read-only":
        return True
    if sandbox == "workspace-write":
        settings = (options.get("config") or {}).get("sandbox_workspace_write")
        settings = settings if isinstance(settings, dict) else {}
        return not settings.get("network_access")
    return False


def launch_args(argv) -> dict:
    """turns' reading of the host's flags (`turns.codex.launch_args`). Raises `Refused`
    for a flag the host refuses."""
    import moxi_turns

    try:
        return moxi_turns.codex.launch_args(argv)
    except ValueError as e:
        raise Refused(f"the host refuses this launch's settings: {e}") from None


# --------------------------------------------------------------------------- the launch

def sessions_dir(env=None) -> Path:
    """RUNTIME/comms/comms-sessions, which the Claude wrapper's sessions share."""
    from .session import sessions_dir as shared

    return shared(env)


@dataclass
class Launch:
    """Everything one Codex launch needs, written down in one place."""

    agent: codex_roster.CodexAgent
    #: Where the roster form's row came from, or "the command line".
    source: str
    cwd: str
    #: The host's flags after `--cwd`, comms' additions included, without
    #: `--instructions-file`.
    flags: list
    #: The thread parameters those flags give, as the host reads them.
    options: dict
    window: bool
    write_root: object = None
    write_root_reason: str = ""
    comms_path: str | None = None
    key: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    #: The URL comms' tools answer at, as the daemon said, or None when it serves none.
    tools_url: str | None = None
    #: Why the session gets no comms tools, when `tools_url` is None.
    tools_why: str = ""
    #: The thread the host resumes, as turns' parser read `--resume`, or None: the host
    #: starts a new thread.
    resume: str | None = None

    @property
    def dir(self) -> Path:
        return sessions_dir() / f"codex-{self.key}"

    @property
    def claim_socket(self) -> Path:
        """The adapter's claim socket, named for the session's directory."""
        return claim_socket(self.dir.name)

    @property
    def instructions(self) -> Path:
        return self.dir / INSTRUCTIONS_NAME

    def log_path(self) -> Path:
        """Where the session's adapter writes its log."""
        return log_dir() / f"codex-{self.key}.log"

    def host_argv(self, host) -> list[str]:
        return [str(host), "--cwd", self.cwd, *self.flags,
                "--instructions-file", str(self.instructions)]

    @property
    def shell_reach_off(self) -> bool:
        """Whether the agent's shell can't reach comms (`shell_reach_off`)."""
        return shell_reach_off(self.options)

    def env(self, base=None) -> dict:
        """The host's environment: `base` (this process's) with comms' variables the
        launch didn't stamp scrubbed, the identity stamp and the claim socket over it,
        `MOXI_COMMS_SHELL_REACH=off` when the agent's shell can't reach comms, and no
        lifeline. The tools' token and URL are stamped at the fork (`start`)."""
        from moxi import proc as moxi_proc

        env = self.agent.env(os.environ if base is None else base)
        env[CLAIM_SOCKET_ENV] = str(self.claim_socket)
        if self.shell_reach_off:
            env[SHELL_REACH_ENV] = SHELL_REACH_OFF
        env.pop(moxi_proc.LIFELINE_ENV, None)
        return env

    def write(self) -> Path:
        """Make the session's runtime directory (0700) and return it."""
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir.parent, 0o700)
        os.chmod(self.dir, 0o700)
        return self.dir

    def write_instructions(self, text: str) -> Path:
        """Write layer one to its 0600 file in the session's directory."""
        from moxi import files as moxi_files

        moxi_files.atomic_write(self.instructions, text, mode=0o600)
        return self.instructions


def settings_flags(args) -> list[str]:
    """The `--name` form's settings flags, as given."""
    flags: list[str] = []
    if args.model:
        flags += ["--model", args.model]
    if args.sandbox:
        flags += ["--sandbox", args.sandbox]
    if args.permissions:
        flags += ["--permissions", args.permissions]
    for item in args.option:
        flags += ["--option", item]
    if args.options_file:
        flags += ["--options-file", args.options_file]
    if args.resume is not None:
        flags += ["--resume", args.resume]
    return flags


def _named_agent(args, cwd: str) -> codex_roster.CodexAgent:
    """The `--name` form's identity as a row: the name, its scope and its directors."""
    from ..config import load

    if not NAME_RE.match(args.name):
        raise Refused(f"--name {args.name!r} is not a name. A comms identity is "
                      f"{NAME_RE.pattern} -- letters, digits, dot, underscore, hyphen. "
                      "The scope half goes in --scope or --global, never in the name.")
    if args.scope and args.global_:
        raise Refused("--scope and --global are one choice; name one")
    if args.scope is not None and not NAME_RE.match(args.scope):
        raise Refused(f"--scope {args.scope!r} is not a scope")
    try:
        load()                  # a bad config file fails here, before Codex starts
        directors = directors_mod.declare(args.director)
    except (ConfigError, directors_mod.BadDirector) as e:
        raise Refused(str(e)) from None
    scope = args.scope or GLOBAL
    qualified = qualify(args.name, scope)
    return codex_roster.CodexAgent(
        qualified=qualified, name=args.name, scope=scope, cwd=cwd,
        thread_file=codex_threads_dir() / f"{qualified}.json", sandbox=None,
        directors=list(directors))


def plan(args, *, cwd: str | None = None, endpoint=None) -> Launch:
    """The launch `args` asks for: its row, its host flags and their thread parameters.
    Raises `Refused`, with nothing started and nothing written.

    `endpoint()` answers whether the daemon serves comms' tools, `(url, why)` as
    `session.tools_endpoint` does; it is asked once the launch's own settings have
    passed, since its answer is the network default. None asks nothing and reads as no
    tools."""
    if args.identity is not None:
        if args.name is not None:
            raise Refused("name the launch once: a roster IDENTITY or --name, not both")
        if args.resume is not None:
            raise Refused("--resume goes with --name: a roster launch starts a new thread")
        given = [flag for flag, value in (
            ("--scope", args.scope), ("--global", args.global_ or None),
            ("--director", args.director or None), ("--model", args.model),
            ("--sandbox", args.sandbox), ("--permissions", args.permissions),
            ("--option", args.option or None), ("--options-file", args.options_file),
            ("--no-window", args.no_window or None)) if value is not None]
        if given:
            raise Refused(f"{', '.join(given)}: a roster launch takes its identity and "
                          "settings from its row; pass --name for a launch with its own")
        try:
            roster = codex_roster.load(path=args.config)
            agent = roster.require(args.identity)
        except ConfigError as e:
            raise Refused(str(e)) from None
        source, window, settings = roster.source, bool(args.window), row_flags(agent)
        cwd = str(agent.cwd)
    else:
        if args.name is None:
            raise Refused("pass --name <name>, or a roster IDENTITY. A claim is a name: "
                          "it is what the record shows and what `moxi comms send` speaks "
                          "as.")
        if args.config is not None:
            raise Refused("--config names a roster; it goes with a roster IDENTITY")
        cwd = os.getcwd() if cwd is None else cwd
        agent = _named_agent(args, cwd)
        source, window, settings = "the command line", not args.no_window, \
            settings_flags(args)
    asked = launch_args(["--cwd", cwd, *settings])["options"]
    write_root, reason = codex_roster.record_write_root(agent.env(), cwd)
    tools_url, tools_why = endpoint() if endpoint is not None else (None, "")
    # One fact, the stamped pair, decides the default: a launch that carries comms' tools
    # starts with the network off.
    flags = settings + comms_flags(asked, write_root, network=tools_url is None)
    if not window:
        flags.append("--no-window")
    read = launch_args(["--cwd", cwd, *flags])
    return Launch(agent=agent, source=source, cwd=cwd, flags=flags,
                  options=read["options"], window=window, write_root=write_root,
                  write_root_reason=reason, comms_path=own_comms(), tools_url=tools_url,
                  tools_why=tools_why, resume=read["resume"])


def reach_note(launch: Launch) -> str | None:
    """What a launch whose agent can't reach comms from its shell, and has no tools,
    costs; None otherwise."""
    if launch.tools_url is not None or not launch.shell_reach_off:
        return None
    return ("this agent's shell can't reach the daemon socket (its sandbox has the "
            "network off) and it has no comms tools, so it cannot send or run `moxi "
            "comms reply`")


def print_plan(launch: Launch, host_line: list, out=None) -> None:
    out = out or sys.stdout

    def say(text):
        print(text, file=out)

    agent, options = launch.agent, launch.options
    thread = (f"resumes thread {launch.resume}" if launch.resume is not None
              else "a new thread at this launch")
    plan_head(agent, SimpleNamespace(source=launch.source), say,
              record=f"  thread     {codex_threads_dir() / (agent.qualified + '.json')} "
                     f"({thread})")
    settings = (options.get("config") or {}).get("sandbox_workspace_write") or {}
    if options.get("permissions"):
        say(f"  sandbox    profile {options['permissions']} (approvals never by turns)")
    else:
        network = "on" if settings.get("network_access") else "OFF"
        say(f"  sandbox    {options.get('sandbox')} (network {network}, approvals never "
            "by turns)")
        if launch.shell_reach_off and launch.tools_url is not None:
            say(f"  shell      can't reach comms; the agent sends through comms' tools "
                f"({SHELL_REACH_ENV}={SHELL_REACH_OFF})")
        if options.get("sandbox") == "workspace-write":
            for root in settings.get("writable_roots") or []:
                say(f"  writable   {root}")
            if launch.write_root is None:
                say(f"  writable   (nothing added: {launch.write_root_reason})")
    if options.get("model"):
        say(f"  model      {options['model']}")
    plan_comms(launch.comms_path, say)
    if launch.tools_url is not None:
        say(f"  tools      {launch.tools_url} (token in {TOKEN_ENV}, minted at launch)")
    say(f"  claim      {launch.claim_socket}")
    say(f"  adapter    log {launch.log_path()}")
    say("  launch     " + " ".join(host_line))


# --------------------------------------------------------------------------- main

def build_parser(prog: str = PROG) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog=prog, allow_abbrev=False,
        description="start a Codex session with a comms identity, through turns' Codex "
                    "host")
    ap.add_argument("identity", nargs="?",
                    help="a roster identity (e.g. planner@proj-a) whose row gives the "
                         "launch's settings, in place of --name and the settings flags")
    ap.add_argument("--name", help="claim this chosen (or standing) name")
    ap.add_argument("--scope", help="a project scope, only to keep apart two agents of one "
                                    "name in different projects (default: global)")
    ap.add_argument("--global", dest="global_", action="store_true")
    ap.add_argument("--director", action="append", default=[],
                    help="declare a director for THIS session (repeatable): messages "
                         "from that identity, verified, render as directives")
    host = ap.add_argument_group("settings", "turns' Codex host's own flags")
    host.add_argument("--model", help="the model the thread runs")
    choice = host.add_mutually_exclusive_group()
    choice.add_argument("--sandbox", help="the sandbox mode")
    choice.add_argument("--permissions", metavar="PROFILE",
                        help="a named permission profile, in place of a sandbox mode")
    host.add_argument("--option", action="append", default=[], metavar="KEY=VALUE",
                      help="one Codex config override (KEY a TOML dotted key, VALUE TOML)")
    host.add_argument("--options-file", metavar="F",
                      help="a JSON object of thread parameters")
    host.add_argument("--resume", metavar="THREAD_ID",
                      help="resume this Codex thread instead of starting a new one "
                           "(with --name)")
    host.add_argument("--no-window", action="store_true",
                      help="run no Codex window in this terminal")
    roster = ap.add_argument_group("a roster launch")
    roster.add_argument("--config", metavar="PATH",
                        help="a comms config file whose [codex] table is the roster, in "
                             "place of the box's config file's; checked whole")
    roster.add_argument("--window", action="store_true",
                        help="open the Codex window in this terminal")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and the host's line; start nothing")
    return ap


def main(argv=None, prog: str = PROG) -> int:
    args = build_parser(prog).parse_args(argv)
    try:
        launch = plan(args, endpoint=tools_endpoint)
    except Refused as e:
        print(f"{prog}: {e}", file=sys.stderr)
        return e.code
    from moxi import paths as moxi_paths

    try:
        host = moxi_paths.script(HOST)
    except FileNotFoundError:
        host = None
    print_plan(launch, launch.host_argv(host or HOST))
    if launch.comms_path is None:
        print(f"{prog}: this environment has no `moxi` script; layer one names a bare "
              f"`{BARE_COMMS}`, which the agent's PATH decides", file=sys.stderr)
    if launch.tools_url is None:
        print(f"{prog}: {tools_off(launch.tools_why)}", file=sys.stderr)
    note = reach_note(launch)
    if note is not None:
        print(f"{prog}: {note}", file=sys.stderr)
    if args.dry_run:
        return moxi_exit.OK
    refusal = _cannot_start(host, launch)
    if refusal is not None:
        print(f"{prog}: {refusal}", file=sys.stderr)
        return refusal.code
    sys.stdout.flush()
    sys.stderr.flush()
    return start(launch, host, prog=prog)   # pragma: no cover - replaces this process


def _cannot_start(host, launch: Launch) -> Refused | None:
    """Why the host can't start here, or None."""
    import moxi_turns

    if host is None:
        return Refused(f"turns' Codex host ({HOST}) is not installed in this environment",
                       moxi_exit.ERROR)
    if not moxi_turns.codex_host_available():
        return Refused("turns' Codex host can't start: moxi-turns needs its codex extra",
                       moxi_exit.ERROR)
    if launch.window:
        from .terminal import in_terminal_foreground, terminal_fd

        if not in_terminal_foreground(terminal_fd()):
            return Refused("the Codex window needs the foreground of a terminal; run this "
                           "there, or pass --no-window")
    return None


# --------------------------------------------------------------------------- the fork

def start(launch: Launch, host, *, prog: str = PROG, ready_wait: float = READY_WAIT,
          exec_=os.execve, roster=None) -> int:
    """Fork the session's adapter, wait for its word, write layer one and exec the host.

    As the Claude wrapper does: this process takes a pidfd on itself first (it becomes
    the host at the exec, keeping its pid), and the adapter is forked twice over, in a
    session of its own, so it is never the host's child. A refusal the adapter reports
    ends the launch here, with its exit code. No word in `ready_wait` ends it too, with
    `TEMPFAIL`, and the host never starts; the wrapper says once that it is waiting when
    the word hasn't come after `word.NOTICE_AFTER`. `roster` stands in for the daemon's
    roster in layer one."""
    from ..context import layer_one_form
    from .codex import fetch_roster, layer_one_text
    from .terminal import pidfd_open   # needs termios: not at the top

    env = launch.env()
    # comms' tools: minted and stamped before the adapter forks, so the adapter and the
    # host it execs (turns' one caller of the launch additions here) both see the pair.
    stamp_tools(env, launch.tools_url)
    launch.write()
    harness_pid = os.getpid()
    handle = pidfd_open(harness_pid)
    r, w = os.pipe()
    child = os.fork()
    if child == 0:                                         # pragma: no cover - forked
        code = 70
        try:
            os.close(r)
            os.setsid()
            if os.fork() == 0:
                code = 70
                try:
                    code = _adapter_process(launch, env, harness_pid, handle, w)
                finally:
                    os._exit(code)
            code = 0
        finally:
            os._exit(code)
    os.close(w)
    os.waitpid(child, 0)
    os.close(handle)
    who = launch.agent.qualified
    word = await_word(r, ready_wait, notice=lambda: print(
        f"{prog}: waiting for the comms daemon to bind {who} (up to {ready_wait:g}s)",
        file=sys.stderr, flush=True))
    os.close(r)
    if word is None:
        print(f"{prog}: {who} was not launched: the comms daemon did not answer in "
              f"{ready_wait:g}s; try again, or run moxi comms doctor (its log: "
              f"{launch.log_path()})", file=sys.stderr)
        return moxi_exit.TEMPFAIL
    if word[0] != BOUND:
        code, why = word
        print(f"{prog}: {launch.agent.qualified} was not launched: "
              f"{why or 'the adapter ended before it bound'} (its log: "
              f"{launch.log_path()})", file=sys.stderr)
        _remove(launch)
        return exit_of(code)
    info = roster if roster is not None else fetch_roster()
    # A launch carrying comms' tools gets layer one's tools form: the tools first, the
    # CLI last as the fallback, or not at all when the shell can't reach comms.
    launch.write_instructions(layer_one_text(info, launch.comms_path or BARE_COMMS,
                                             directors=launch.agent.directors,
                                             form=layer_one_form(env)))
    argv = launch.host_argv(host)
    return exec_(str(host), argv, env)


class NobodyWaits(RuntimeError):
    """The wrapper stopped waiting for the adapter's word: no host will start."""


def serve(adapter, handle: int, *, say=None) -> int:
    """The adapter process's life: bind, say so, find the host's session and deliver
    until the session or the host ends. `say(code, reason)` gives the wrapper its word:
    `BOUND` once the bind has been answered and the claim socket listens, or why the
    bind failed. A host that ended before its session was ready ends the adapter
    quietly: the host said why on its own terminal.

    `handle` is a handle on the wrapper's own process, which becomes the host at its
    exec. A wrapper that gave up waiting has ended, so this adapter serves nobody: it
    ends without binding when the wrapper is gone before the bind starts, and when its
    `BOUND` can't be delivered (`say` returns False) it lets go of the name and ends
    with `moxi.exit.TEMPFAIL`, before it looks for a session or delivers anything. A
    `say` that returns nothing counts as delivered.

    A launch that carries comms' tools has the session's token in this process's
    environment: the adapter keeps its hash (the value leaves the environment) and
    registers it after every bind, the first before `BOUND` is said. The form of layer
    one the wrapper wrote is read first, while the token is still there to show it: the
    thread record hashes that form, and the reply reminder follows it."""
    from ..context import layer_one_form
    from .binding import ToolToken
    from .claude_adapter import ended, wait_for_exit
    from .codex import HostGone

    say = say or (lambda code, reason="": None)
    if ended(handle):
        log.info("the wrapper of %s ended before the bind; nothing to serve",
                 adapter.agent.qualified)
        return moxi_exit.TEMPFAIL

    def bound():
        if say(BOUND) is False:
            raise NobodyWaits(f"the wrapper of {adapter.agent.qualified} stopped waiting "
                              "before the bind was answered; letting the name go")

    if adapter.tool_token is None:
        adapter.layer_form = layer_one_form(os.environ)
        adapter.tool_token = ToolToken.from_environ()
    stopping = threading.Event()
    adapter.exit_hook = stopping.set
    adapter._on_bound = bound
    adapter._alive = lambda: not ended(handle)
    try:
        try:
            adapter.start()
        except HostGone as e:
            log.info("%s", e)
            return 0
        except NobodyWaits as e:
            log.warning("%s", e)
            return moxi_exit.TEMPFAIL
        except Exception as e:                              # noqa: BLE001
            log.warning("the launch of %s failed: %s", adapter.agent.qualified, e)
            word = word_for(e)
            say(word, str(e))
            return exit_of(word)
        log.info("adapter for codex host pid %s is up (pid %d)", adapter.harness_pid,
                 os.getpid())
        wait_for_exit(handle, stopping)
        log.info("the codex session of %s has ended: %s", adapter.agent.qualified,
                 adapter.gone_reason or "its host exited")
        return 0
    finally:
        adapter.stop()


def _adapter_process(launch: Launch, env: dict, harness_pid: int, handle: int,
                     word_fd: int) -> int:                 # pragma: no cover - forked
    """The forked adapter: the session's environment, standard streams to its log, then
    `serve` until the session has ended."""
    from .codex import CodexAdapter

    os.environ.clear()
    os.environ.update(env)
    log_path = launch.log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    out = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.dup2(out, 1)
    os.dup2(out, 2)
    os.close(null)
    os.close(out)
    sys.stdout = os.fdopen(1, "w", buffering=1)
    sys.stderr = os.fdopen(2, "w", buffering=1)
    logging.basicConfig(level="INFO", stream=sys.stderr, force=True,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    say = teller(word_fd)
    adapter = CodexAdapter(launch.agent, harness_pid=harness_pid,
                           claim_socket=launch.claim_socket, comms_path=launch.comms_path,
                           wait_when_held=False, resume=launch.resume)
    try:
        return serve(adapter, handle, say=say)
    finally:
        say(FAILED, "the adapter ended")
        _remove(launch)


def _remove(launch: Launch) -> None:
    """The session's runtime directory, and its claim socket if no adapter serves it."""
    from ..claimhold import remove_socket

    shutil.rmtree(launch.dir, ignore_errors=True)
    remove_socket(launch.claim_socket)


__all__ = ["HOST", "Launch", "MULTI_AGENT_OFF", "NobodyWaits", "PROG", "READY_WAIT",
           "Refused", "build_parser", "comms_flags", "launch_args", "main", "option",
           "plan", "print_plan", "reach_note", "row_flags", "serve", "shell_reach_off",
           "start", "toml_key", "toml_value"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
