"""What a launch adds to its harness: `launch_additions`, and what other modules contribute.

A launch about to start asks turns, once, what to add to the harness it starts:

    adds = turns.launch_additions("claude", cwd=cwd, env=env, session_id=sid,
                                  commands=("exit", "compact"))
    argv += adds.argv            # words for the harness's command line
    env.update(adds.env)         # variables for its environment
    adds.options                 # harness settings, for turns' own hosts
    adds.notes                   # one line per contribution skipped, and why, to log

It runs before the session exists, so it is a library call, not a method on the socket.
Each launch path has exactly one caller. For Claude that is the launcher's composer, or
comms' Claude wrapper when the launch goes through it. For Codex and OpenCode it is turns'
own host: `turns.codex.launch` and `turns.opencode.launch` call it once themselves, unless
their caller passed `additions=`, which then replaces that call.

What turns itself adds, per harness:

- Claude: `--plugin-dir` for the runner and for the gate, and `MOXI_TURNS_ENDPOINT`, the
  absolute endpoint command the runner spawns, so a launch loads both plugins and gets
  an endpoint with no user-scope install. The folders are the copy turns' setup placed
  in its data dir when that copy is current and this version of turns placed it, else
  the package's own. The command is the one turns' config and this Python environment
  give, read from this process's own environment as turns' setup reads it, not from
  `env`. A launch writes nothing: launches in parallel share no write, and a read-only
  install with no setup run launches.
- every harness: `MOXI_TURNS_COMMANDS`, the session commands the launch grants, when
  `commands` is not empty. With a `session_id` it is bound to that id,
  `<session id>:<commands>`, and an endpoint honours it only while its session's id is
  that one or was (a `/clear` keeps the launch's grant); a harness started by hand
  inside the session inherits it and gets nothing from it. Without one it is the bare
  comma list, honoured wherever it reaches. So a launcher passes as `session_id` only
  the id it gives the harness itself (Claude's `--session-id`).

**Contributions.** A module adds to a launch by declaring an entry point in the group
`moxi.turns.additions`, named by the module, whose target is a function:

    [project.entry-points."moxi.turns.additions"]
    mymod = "moxi_mymod.launch:additions"

    def additions(request):          # request: harness, cwd, env, session_id
        return {"tool_permissions": {"claude": ("Bash(mymod status:*)",),
                                     "opencode": ("bash mymod status*",)}}

It returns a `Contribution` (or a mapping with its fields), or None to add nothing. The
doctor also calls it, with no session id, to show what each module would add; so it has
no side effects and answers quickly. A contribution is applied whole or skipped whole,
with a note and a doctor row; it is never half applied. The one exception is an MCP
server that cannot go into this launch (below): that server alone is left out, with a
note and a doctor row, and the rest of the contribution goes in. Hooks are not a
contribution: a module that wants session events registers a listener.

- `tool_permissions` maps a harness to rules in that harness's own permission syntax,
  which turns passes as given. Module tool permissions go in per launch and are never
  written to the user's settings file.
  - Claude: one `--allowedTools=<rule>` word per rule, in Claude Code's rule syntax
    (`Bash(git diff:*)`, `mcp__name__tool`). The joined form keeps a positional word
    after it from being read as another rule.
  - OpenCode: a `permission` fragment in `options`. A rule is a permission name alone
    (`edit`), which allows it whole, or a name, a space and a pattern (`bash git diff*`),
    which allows that pattern.
  - Codex: turns knows no per-launch rule form for Codex, so a contribution with Codex
    rules is skipped for a Codex launch, with a note.
  A rule that is not a non-empty string, or a harness turns does not know, refuses the
  contribution.
  OpenCode names an MCP server's tool `<server>_<tool>`, hyphens kept, and Claude
  `mcp__<server>__<tool>`; a rule for one keys on that name.
- `mcp_servers` maps a server name to `McpServer(url, token_env)` (or a mapping with
  those keys): an HTTP MCP server at `url`, sent `Authorization: Bearer <token>` with the
  token taken from the variable `token_env`, or no header when `token_env` is None. A
  name is lowercase letters, digits and hyphens; a URL is http or https. Per harness:
  - Claude: one `--mcp-config=<json>` word holding every contribution's servers,
    `{"mcpServers": {name: {"type": "http", "url": ..., "headers": {"Authorization":
    "Bearer ${VAR}"}}}}`. Claude expands `${VAR}` from its own environment. The joined
    form, because `--mcp-config <json>` takes a positional prompt right after it as a
    second config. No `--strict-mcp-config`: the user's own servers stay beside these.
  - Codex: `options["config"]["mcp_servers"][name] = {"url": ..., "bearer_token_env_var":
    VAR, "default_tools_approval_mode": "approve"}`, the thread config the host sends.
    The host answers no approval request, so a tool held for one would hang. For each
    server with a `token_env`, also `options["config"]["shell_environment_policy"]
    ["filters"][VAR] = "exclude"` and, once for the launch, `options["config"]
    ["features"]["shell_snapshot"] = False`. The exclude keeps the variable out of the
    environment of every shell the model runs; the snapshot goes off because Codex 0.153
    writes a login shell's whole `export` list, taken from the app-server's environment
    before any filter, to a file under `CODEX_HOME` and sources it before each command.
    With both, the value is in the shell's environment and in no file Codex writes; the
    app-server's own environment still holds it, since its MCP client reads it there, and
    a process of the same user can read that environment. `filters` replaces a lower
    layer's legacy `exclude` and `include_only` arrays, so a user `config.toml` that sets
    those loses them in such a session; the doctor warns. A caller's option for either
    key goes over the rendered one (`for_host`).
  - OpenCode: `options["mcp"][name] = {"type": "remote", "url": ..., "enabled": True,
    "headers": {"Authorization": "Bearer {env:VAR}"}}`, which OpenCode substitutes in the
    config it reads at start (`OPENCODE_CONFIG_CONTENT`).
  On Claude and OpenCode turns knows no setting that keeps one variable out of the
  model's shell, so their shells inherit it.
  **turns never reads a token's value and never writes one into argv, a file or another
  variable.** It checks only that `token_env` is a key of the launch's `env` and renders
  a reference to it. A server whose variable is absent is left out, never rendered with
  an empty token: Claude would send the reference itself and get a 401. So is a server
  with a bad name or URL, and one whose name an earlier module's server already has. A
  server named for a module in the group belongs to that module: another module's server
  of that name is left out wherever it sorts, so no module can take `comms`'s server.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

log = logging.getLogger("moxi.turns.additions")

#: The entry-point group.
GROUP = "moxi.turns.additions"
#: The harnesses a launch can start.
HARNESSES = ("claude", "codex", "opencode")
#: The fields a contribution may carry.
FIELDS = ("mcp_servers", "tool_permissions")
#: What an MCP server's name may be.
SERVER_NAME = re.compile(r"[a-z0-9-]+")
#: What a token variable's name may be: one Claude's `${...}` and OpenCode's `{env:...}`
#: both take whole.
VARIABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: Characters a server's URL may not hold: whitespace, and what Claude (`${VAR}`) or
#: OpenCode (`{env:VAR}`) would read as a reference to a variable.
_URL_REFUSED = re.compile(r"[\s${}]")
#: Whether the installed OpenCode substitutes `{env:VAR}` in the config it reads at start.
#: 1.18.34 does. Were it found not to, the OpenCode form is left out with a note: turns
#: never reads the value to write it in instead.
OPENCODE_SUBSTITUTES_ENV = True


@dataclass(frozen=True)
class AdditionsRequest:
    """What a contributor is asked about: the launch about to start. `env` is read-only."""

    harness: str
    cwd: str
    env: Mapping[str, str]
    session_id: str | None = None


@dataclass(frozen=True)
class McpServer:
    """An HTTP MCP server a contribution adds: its URL, and the name of the variable in
    the launch's environment that holds its bearer token (None: no token). turns renders
    a reference to the variable and never reads it."""

    url: str
    token_env: str | None = None


@dataclass(frozen=True)
class Contribution:
    """What one module adds to a launch."""

    mcp_servers: Mapping[str, McpServer] = field(default_factory=dict)
    tool_permissions: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class LaunchAdditions:
    """What a launch adds to its harness. `options` is in the harness's own form."""

    harness: str
    argv: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    options: Mapping[str, object] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Contributor:
    """One module's entry in the group, loaded."""

    module: str
    function: object


@dataclass(frozen=True)
class Broken:
    """An entry that could not be loaded, and why (a note, and a doctor row)."""

    module: str
    reason: str


@dataclass(frozen=True)
class Group:
    contributors: tuple[Contributor, ...] = ()
    broken: tuple[Broken, ...] = ()

    def modules(self) -> frozenset[str]:
        """Every module in the group, a broken entry's included: the server names no other
        module may take."""
        return frozenset([*(c.module for c in self.contributors),
                          *(b.module for b in self.broken)])


@dataclass(frozen=True)
class _Rendered:
    """One contribution for one harness: what it adds, or why it is skipped. `serious`
    is a skip that is the module's fault (a doctor `fail`), not one that waits on turns.

    `servers` holds its MCP servers by name, each in the harness's own form, which the
    launch places once every contribution is in (`_place`): Claude's in one word, the
    others' in `options`. `left_out` says why each server that could not go in did not.
    `tokened` names the servers that send a token when they go in, whether or not this
    launch's environment holds the variable (the doctor's Codex check)."""

    argv: tuple[str, ...] = ()
    permission: Mapping[str, object] | None = None
    servers: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    left_out: tuple[str, ...] = ()
    tokened: tuple[str, ...] = ()
    skipped: str | None = None
    serious: bool = False

    @property
    def adds(self) -> bool:
        return bool(self.argv or self.permission or self.servers)


# -- the group -----------------------------------------------------------------------------


def load(entries: Iterable | None = None) -> Group:
    """Read the group from this environment (or from `entries`, each with `.name` and
    `.load()`, for tests). Never raises."""
    from moxi import names

    if entries is None:
        from moxi import modules

        entries = modules.extensions(GROUP)
    by_name: dict[str, list] = {}
    for ep in entries:
        by_name.setdefault(ep.name, []).append(ep)
    contributors: list[Contributor] = []
    broken: list[Broken] = []
    for name in sorted(by_name):
        claims = by_name[name]
        if len(claims) > 1:
            broken.append(Broken(name, f"{len(claims)} distributions register additions "
                                       f"named {name!r}; uninstall all but one"))
            continue
        if not names.is_module_name(name):
            broken.append(Broken(name, f"{name!r} is not a Moxi module name"))
            continue
        try:
            function = claims[0].load()
        except Exception as exc:  # noqa: BLE001 - every failure is the entry's
            broken.append(Broken(name, f"its entry does not load: {_describe(exc)}"))
            continue
        if not callable(function):
            broken.append(Broken(name, f"its entry is a {type(function).__name__}, not a "
                                       "function"))
            continue
        contributors.append(Contributor(name, function))
    return Group(tuple(contributors), tuple(broken))


# -- the call ------------------------------------------------------------------------------


def launch_additions(harness: str, *, cwd, env: Mapping[str, str],
                     session_id: str | None = None,
                     commands: Iterable[str] = (), refuse=None) -> LaunchAdditions:
    """What a launch of `harness` adds: argv words, environment variables, harness
    options and notes. `cwd` and `env` are the launch's as composed so far; `session_id`
    is the id the launcher minted and gives the harness, when it has one; `commands` the
    session commands this launch grants, bound to `session_id` when there is one. `env`
    goes to the contributors only; turns' own additions read no variable from it.

    For Claude, `env` of the result always holds `MOXI_TURNS_ENDPOINT`, the endpoint
    command the runner spawns. Nothing is written for it. The contributions' MCP servers
    go last: one `--mcp-config=` word on Claude, `options` on Codex and OpenCode. Of
    `env`, the renderer asks only whether it holds each server's token variable.

    Raises `ValueError` for a harness or a command turns does not know, or a session id
    a grant cannot be bound to (empty, or holding `:`, `,` or white space), and
    `moxi_turns.TurnsError` when Claude's plugin folder is missing, turns' config cannot
    be read, or there is no endpoint command. A contribution that fails is skipped with a
    note; it never fails the launch.

    `refuse`, when given, is the caller's own check of one module's contribution:
    `refuse(fragment) -> str | None`, `fragment` a `LaunchAdditions` holding only that
    module's argv and options as this harness takes them. A reason, or a raise, skips that
    module alone, with a note naming it, so a caller that can't take one module's keys
    still gets the others'.
    """
    from .results import COMMANDS

    if harness not in HARNESSES:
        raise ValueError(f"harness {harness!r} is not one of {', '.join(HARNESSES)}")
    if isinstance(commands, str):
        raise TypeError("commands is a sequence of command names, not a string")
    granted = set()
    for name in commands:
        if name not in COMMANDS:
            raise ValueError(f"{name!r} is not a session command; use "
                             f"{', '.join(COMMANDS)}")
        granted.add(name)

    argv: list[str] = []
    out_env: dict[str, str] = {}
    options: dict[str, object] = {}
    notes: list[str] = []
    if harness == "claude":
        from .claude_setup import ENDPOINT_ENV, claude_plugin_dirs, launch_endpoint

        runner, gate = claude_plugin_dirs()
        argv += ["--plugin-dir", str(runner), "--plugin-dir", str(gate)]
        out_env[ENDPOINT_ENV] = launch_endpoint()
    if granted:
        from .config import COMMANDS_ENV, grant

        out_env[COMMANDS_ENV] = grant(granted, session_id or None)

    request = AdditionsRequest(harness, os.fspath(cwd),
                               MappingProxyType({str(k): str(v) for k, v in env.items()}),
                               session_id)
    group = load()
    for b in group.broken:
        notes.append(f"additions of {b.module} are off: {b.reason}")
    permission: dict[str, object] = {}
    servers: dict[str, Mapping[str, object]] = {}
    owner: dict[str, str] = {}
    reserved = group.modules()
    for contributor in group.contributors:
        rendered = _ask(contributor, request)
        if rendered.skipped is None and refuse is not None:
            rendered = _checked(rendered, harness, refuse)
        if rendered.skipped:
            notes.append(f"additions of {contributor.module} skipped: {rendered.skipped}")
            continue
        notes += [f"additions of {contributor.module}: {why}"
                  for why in _left_out(rendered, harness, owner, contributor.module,
                                       reserved)]
        argv += rendered.argv
        if rendered.permission:
            _add_permission(permission, rendered.permission)
        for name, entry in rendered.servers.items():
            if _taken(name, contributor.module, owner, reserved) is None:
                owner[name] = contributor.module
                servers[name] = entry
    if permission:
        options["permission"] = permission
    server_argv, server_options = _place(harness, servers)
    argv += server_argv
    options = merge_options(options, server_options)
    return LaunchAdditions(harness, tuple(argv), out_env, options, tuple(notes))


def _checked(rendered: _Rendered, harness: str, refuse) -> _Rendered:
    """`rendered`, or a skip when the caller's `refuse` turns its fragment down."""
    server_argv, server_options = _place(harness, rendered.servers)
    options = {"permission": dict(rendered.permission)} if rendered.permission else {}
    fragment = LaunchAdditions(harness, (*rendered.argv, *server_argv),
                               options=merge_options(options, server_options))
    try:
        why = refuse(fragment)
    except Exception as exc:  # noqa: BLE001 - the caller's check failing skips the module
        why = f"the launcher's check raised {_describe(exc)}"
    return rendered if why is None else _Rendered(skipped=f"the launcher refuses it: {why}")


def _ask(contributor: Contributor, request: AdditionsRequest) -> _Rendered:
    try:
        answer = contributor.function(request)
    except Exception as exc:  # noqa: BLE001 - a contributor's failure is its own
        return _Rendered(skipped=f"it raised {_describe(exc)}", serious=True)
    if answer is None:
        return _Rendered()
    return render(answer, request.harness, request.env)


def render(answer: object, harness: str, env: Mapping[str, str] | None = None
           ) -> _Rendered:
    """One contribution, rendered for `harness`, or the reason it is skipped whole. `env`
    is the launch's environment; of it, only which names it holds is asked (an MCP
    server's token variable), never a value. Without it no token variable is there."""
    if isinstance(answer, Contribution):
        servers, permissions = answer.mcp_servers, answer.tool_permissions
    elif isinstance(answer, Mapping):
        unknown = sorted(str(k) for k in answer if k not in FIELDS)
        if unknown:
            return _Rendered(skipped=f"it names {', '.join(unknown)}, which this turns "
                                     f"does not take ({', '.join(FIELDS)})", serious=True)
        servers = answer.get("mcp_servers") or {}
        permissions = answer.get("tool_permissions") or {}
    else:
        return _Rendered(skipped=f"it returned a {type(answer).__name__}, not a "
                                 "Contribution", serious=True)
    if not isinstance(servers, Mapping) or not isinstance(permissions, Mapping):
        return _Rendered(skipped="mcp_servers and tool_permissions are each a mapping",
                         serious=True)
    problem = _check_permissions(permissions)
    if problem:
        return _Rendered(skipped=problem, serious=True)
    specs = _server_specs(servers)
    if isinstance(specs, str):
        return _Rendered(skipped=specs, serious=True)
    rendered, left_out = _render_servers(specs, harness, env if env is not None else {})
    tokened = tuple(name for name in sorted(specs) if specs[name].token_env is not None
                    and _server_problem(name, specs[name], harness) is None)
    rules = tuple(permissions.get(harness) or ())
    if not rules:
        return _Rendered(servers=rendered, left_out=left_out, tokened=tokened)
    if harness == "claude":
        return _Rendered(argv=tuple(f"--allowedTools={rule}" for rule in rules),
                         servers=rendered, left_out=left_out, tokened=tokened)
    if harness == "opencode":
        fragment: dict[str, object] = {}
        for rule in rules:
            _add_permission(fragment, _opencode_rule(rule))
        return _Rendered(permission=fragment, servers=rendered, left_out=left_out,
                         tokened=tokened)
    return _Rendered(skipped=f"it names {len(rules)} Codex tool rule(s), and turns knows "
                             "no way to give Codex a tool rule for one launch")


# -- MCP servers ---------------------------------------------------------------------------


def _server_specs(servers: Mapping) -> dict[str, McpServer] | str:
    """Each server as an `McpServer`, or what is wrong with the contribution's shape."""
    out: dict[str, McpServer] = {}
    for name, spec in servers.items():
        if isinstance(spec, Mapping):
            unknown = sorted(str(k) for k in spec if k not in ("url", "token_env"))
            if unknown or "url" not in spec:
                return (f"its MCP server {name!r} is given as a mapping with "
                        f"{', '.join(map(str, spec)) or 'no keys'}; use url and token_env")
            spec = McpServer(spec["url"], spec.get("token_env"))
        elif not isinstance(spec, McpServer):
            return (f"its MCP server {name!r} is a {type(spec).__name__}, not an "
                    "McpServer")
        if not isinstance(spec.url, str):
            return f"its MCP server {name!r} has a url that is not a string"
        if spec.token_env is not None and not isinstance(spec.token_env, str):
            return f"its MCP server {name!r} has a token_env that is not a string or None"
        out[str(name)] = spec
    return out


def _render_servers(specs: Mapping[str, McpServer], harness: str, env
                    ) -> tuple[dict[str, dict[str, object]], tuple[str, ...]]:
    """The servers that can go into this launch, each in `harness`'s form, and why each
    other one is left out. Of `env`, asks only whether it holds a token variable."""
    rendered: dict[str, dict[str, object]] = {}
    left_out: list[str] = []
    for name in sorted(specs):
        spec = specs[name]
        why = _server_problem(name, spec, harness)
        if why is None and spec.token_env is not None and spec.token_env not in env:
            why = f"{spec.token_env} is not in the launch's environment"
        if why is not None:
            left_out.append(f"MCP server {name!r} left out: {why}")
            continue
        rendered[name] = _entry(harness, spec)
    return rendered, tuple(left_out)


def _server_problem(name: str, spec: McpServer, harness: str) -> str | None:
    if not SERVER_NAME.fullmatch(name):
        return "a server's name is lowercase letters, digits and hyphens"
    if _URL_REFUSED.search(spec.url):
        return "its URL holds whitespace, '$', '{' or '}'"
    from urllib.parse import urlsplit

    try:
        url = urlsplit(spec.url)
        host = url.hostname
    except ValueError as exc:
        return f"its URL does not parse: {exc}"
    if url.scheme not in ("http", "https") or not host:
        return "its URL is not an http or https URL with a host"
    if spec.token_env is not None and not VARIABLE_NAME.fullmatch(spec.token_env):
        return f"its token_env {spec.token_env!r} is not a variable name"
    if (harness == "opencode" and spec.token_env is not None
            and not OPENCODE_SUBSTITUTES_ENV):
        return ("this OpenCode does not substitute {env:...} in its config, and turns "
                "never writes a token's value")
    return None


def _entry(harness: str, spec: McpServer) -> dict[str, object]:
    """One server in `harness`'s form. A token is a reference to its variable only."""
    var = spec.token_env
    if harness == "claude":
        entry: dict[str, object] = {"type": "http", "url": spec.url}
        if var is not None:
            entry["headers"] = {"Authorization": f"Bearer ${{{var}}}"}
        return entry
    if harness == "codex":
        entry = {"url": spec.url}
        if var is not None:
            entry["bearer_token_env_var"] = var
        entry["default_tools_approval_mode"] = "approve"
        return entry
    entry = {"type": "remote", "url": spec.url, "enabled": True}
    if var is not None:
        entry["headers"] = {"Authorization": f"Bearer {{env:{var}}}"}
    return entry


def _left_out(rendered: _Rendered, harness: str, owner: Mapping[str, str], module: str,
              reserved: frozenset[str]) -> list[str]:
    """Why each of `module`'s servers stays out of this launch: its own reasons, and a name
    that is taken (`_taken`)."""
    taken = (_taken(name, module, owner, reserved) for name in rendered.servers)
    return list(rendered.left_out) + [why for why in taken if why is not None]


def _taken(name: str, module: str, owner: Mapping[str, str],
           reserved: frozenset[str]) -> str | None:
    """Why `module` can't have a server named `name`, or None: the name is another
    module's own (one in `reserved`), or an earlier module's server (`owner`) has it."""
    if name in reserved and name != module:
        return f"MCP server {name!r} left out: the name belongs to the module {name}"
    if name in owner:
        return f"MCP server {name!r} left out: {owner[name]} already adds a server of that name"
    return None


def _place(harness: str, servers: Mapping[str, Mapping[str, object]]
           ) -> tuple[tuple[str, ...], dict[str, object]]:
    """Every contribution's servers, where `harness` takes them: argv for Claude (one
    word), options for Codex and OpenCode."""
    if not servers:
        return (), {}
    if harness == "claude":
        config = json.dumps({"mcpServers": dict(servers)}, separators=(",", ":"))
        return (f"--mcp-config={config}",), {}
    if harness == "codex":
        config: dict[str, object] = {"mcp_servers": dict(servers)}
        tokens = sorted({entry["bearer_token_env_var"] for entry in servers.values()
                         if "bearer_token_env_var" in entry})
        if tokens:
            # The token variable stays in the app-server, whose MCP client reads it, and
            # out of every shell the model runs and of the snapshot file Codex writes.
            config["shell_environment_policy"] = {
                "filters": {var: "exclude" for var in tokens}}
            config["features"] = {"shell_snapshot": False}
        return (), {"config": config}
    return (), {"mcp": dict(servers)}


def _check_permissions(permissions: Mapping) -> str | None:
    for harness, rules in permissions.items():
        if harness not in HARNESSES:
            return (f"its tool permissions name the harness {harness!r}; use "
                    f"{', '.join(HARNESSES)}")
        if isinstance(rules, str) or not isinstance(rules, (tuple, list)):
            return (f"its {harness} tool permissions are a {type(rules).__name__}, not a "
                    "sequence of rules")
        for rule in rules:
            if not isinstance(rule, str) or not rule.strip():
                return f"its {harness} tool permissions hold {rule!r}, not a non-empty rule"
    return None


def _opencode_rule(rule: str) -> dict[str, object]:
    name, _, pattern = rule.strip().partition(" ")
    pattern = pattern.strip()
    return {name: {pattern: "allow"} if pattern else "allow"}


def _add_permission(into: dict[str, object], fragment: Mapping[str, object]) -> None:
    """Join a `permission` fragment of allows into `into`. A name allowed whole stays
    allowed whole."""
    for name, value in fragment.items():
        have = into.get(name)
        if have == "allow" or value == "allow":
            into[name] = "allow"
        elif isinstance(have, dict) and isinstance(value, Mapping):
            have.update(value)
        else:
            into[name] = dict(value) if isinstance(value, Mapping) else value


# -- the hosts -----------------------------------------------------------------------------


def for_host(harness: str, additions: LaunchAdditions | None, *, cwd, env, options
             ) -> tuple[dict[str, str], dict[str, object]]:
    """What turns' Codex or OpenCode host launches with: the caller's `env` and `options`
    with the launch's additions applied. With `additions` None the host is the one caller
    and asks once; with it given, nothing is asked.

    The additions' variables go over the caller's. Their options go under the caller's:
    a mapping in both is joined, and where both set one value the caller's stands.
    Raises `ValueError` for additions made for another harness or carrying argv, which
    the hosts build themselves; the host still refuses any key it owns.
    """
    if options is not None and not isinstance(options, Mapping):
        raise TypeError("options must be a mapping")
    caller_env = {str(k): str(v) for k, v in (env or {}).items()}
    if additions is None:
        additions = launch_additions(harness, cwd=cwd, env={**os.environ, **caller_env})
        for note in additions.notes:
            log.warning("%s launch: %s", harness, note)
    elif not isinstance(additions, LaunchAdditions):
        raise TypeError(f"additions is a LaunchAdditions, not a "
                        f"{type(additions).__name__}")
    if additions.harness != harness:
        raise ValueError(f"the additions were made for {additions.harness}, not {harness}")
    if additions.argv:
        raise ValueError(f"the {harness} host builds its own command line; additions "
                         f"carry argv {list(additions.argv)!r}")
    merged_env = {**caller_env, **{str(k): str(v) for k, v in additions.env.items()}}
    merged_options = merge_options(additions.options, options or {})
    return merged_env, merged_options


def merge_options(under: Mapping, over: Mapping) -> dict:
    """`over` laid on `under`: mappings in both are joined, and otherwise `over`'s value
    stands. Neither is changed."""
    out = {k: (merge_options(v, {}) if isinstance(v, Mapping) else v)
           for k, v in under.items()}
    for key, value in over.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = merge_options(out[key], value)
        else:
            out[key] = merge_options(value, {}) if isinstance(value, Mapping) else value
    return out


# -- the doctor ----------------------------------------------------------------------------


def rows() -> list:
    """The doctor's `additions <module>` rows: an entry that does not load, or a
    contribution that raises or is malformed, is `fail`; one skipped on a harness for a
    reason that waits on turns (Codex rules) is `warn`; else `ok`, naming the harnesses
    it adds to. A module with an MCP server left out on a harness (its token variable is
    not in this environment, a bad name or URL, a name an earlier module's server has,
    or a form this harness cannot take) also gets an `additions <module>: mcp_servers`
    `warn` row, and so does one with a token server on Codex while the user's Codex
    `config.toml` sets `shell_environment_policy.exclude` or `include_only`, which the
    rendered `filters` replace in such a session. The doctor asks each contributor about
    a launch with its own directory and environment."""
    from moxi import doctor as moxi_doctor

    from .doctor import MODULE

    group = load()
    out = []
    for b in group.broken:
        out.append(moxi_doctor.Row(MODULE, f"additions {b.module}", "fail", b.reason,
                                   "reinstall or fix that module"))
    cwd = os.getcwd()
    env = MappingProxyType(dict(os.environ))
    owners: dict[str, dict[str, str]] = {harness: {} for harness in HARNESSES}
    reserved = group.modules()
    legacy = codex_legacy_policy(env)
    for contributor in group.contributors:
        adds, skips, serious = [], [], False
        left_out: dict[str, list[str]] = {}
        for harness in HARNESSES:
            rendered = _ask(contributor, AdditionsRequest(harness, cwd, env))
            if rendered.skipped:
                skips.append(f"{harness}: {rendered.skipped}")
                serious = serious or rendered.serious
                continue
            owner = owners[harness]
            for why in _left_out(rendered, harness, owner, contributor.module, reserved):
                left_out.setdefault(why, []).append(harness)
            if harness == "codex" and legacy and rendered.tokened:
                left_out.setdefault(_legacy_why(legacy), []).append(harness)
            for name in rendered.servers:
                if _taken(name, contributor.module, owner, reserved) is None:
                    owner[name] = contributor.module
            if rendered.adds:
                adds.append(harness)
        if skips:
            out.append(moxi_doctor.Row(
                MODULE, f"additions {contributor.module}", "fail" if serious else "warn",
                "; ".join(skips) + (f"; adds to {', '.join(adds)}" if adds else "")))
        else:
            out.append(moxi_doctor.Row(
                MODULE, f"additions {contributor.module}", "ok",
                f"adds to {', '.join(adds)}" if adds else "adds nothing here"))
        if left_out:
            out.append(moxi_doctor.Row(
                MODULE, f"additions {contributor.module}: mcp_servers", "warn",
                "; ".join(f"{', '.join(harnesses)}: {why}"
                          for why, harnesses in left_out.items()),
                f"a launch through {contributor.module} stamps a server's token variable; "
                f"a bad name or URL is {contributor.module}'s to fix"
                + ("; write Codex's shell_environment_policy exclude and include_only as "
                   "filters" if legacy and _legacy_why(legacy) in left_out else "")))
    return out


#: The legacy forms of Codex's `shell_environment_policy` that a layer's `filters`
#: replaces in Codex 0.153's config merge.
CODEX_LEGACY_POLICY = ("exclude", "include_only")


def codex_legacy_policy(env: Mapping[str, str]) -> tuple[str, ...]:
    """Which of `CODEX_LEGACY_POLICY` the user's Codex `config.toml` sets in its
    `shell_environment_policy` table (`CODEX_HOME`, else `~/.codex`). A file that is
    missing or does not read gives none: the doctor warns only on what it can see."""
    import tomllib
    from pathlib import Path

    home = env.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    try:
        with open(Path(home) / "config.toml", "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, ValueError):
        return ()
    policy = data.get("shell_environment_policy")
    if not isinstance(policy, Mapping):
        return ()
    return tuple(key for key in CODEX_LEGACY_POLICY if key in policy)


def _legacy_why(legacy: tuple[str, ...]) -> str:
    keys = " and ".join(f"shell_environment_policy.{key}" for key in legacy)
    return (f"the user's Codex config.toml sets {keys}, which the token server's "
            "filters replace in a session that carries it")


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


__all__ = ["GROUP", "HARNESSES", "AdditionsRequest", "Contribution", "McpServer",
           "LaunchAdditions", "launch_additions", "load", "render", "for_host",
           "merge_options", "rows"]
