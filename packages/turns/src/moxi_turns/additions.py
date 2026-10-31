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
  an endpoint with no user-scope install. The command is the one turns' config and this
  Python environment give, read from this process's own environment as turns' setup
  reads it, not from `env`. A launch writes nothing into turns' package: launches in
  parallel share no write, and a read-only install launches.
- every harness: `MOXI_TURNS_COMMANDS`, the session commands the launch grants, when
  `commands` is not empty.

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
with a note and a doctor row; it is never half applied. Hooks are not a contribution: a
module that wants session events registers a listener.

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
- `mcp_servers` (`{name: spec}`) waits for a later turns: a contribution naming any
  server is skipped with a note.
"""

from __future__ import annotations

import logging
import os
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


@dataclass(frozen=True)
class AdditionsRequest:
    """What a contributor is asked about: the launch about to start. `env` is read-only."""

    harness: str
    cwd: str
    env: Mapping[str, str]
    session_id: str | None = None


@dataclass(frozen=True)
class Contribution:
    """What one module adds to a launch."""

    mcp_servers: Mapping[str, object] = field(default_factory=dict)
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


@dataclass(frozen=True)
class _Rendered:
    """One contribution for one harness: what it adds, or why it is skipped. `serious`
    is a skip that is the module's fault (a doctor `fail`), not one that waits on turns."""

    argv: tuple[str, ...] = ()
    permission: Mapping[str, object] | None = None
    skipped: str | None = None
    serious: bool = False


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
                     commands: Iterable[str] = ()) -> LaunchAdditions:
    """What a launch of `harness` adds: argv words, environment variables, harness
    options and notes. `cwd` and `env` are the launch's as composed so far; `session_id`
    is the id the launcher minted, when it has one; `commands` the session commands this
    launch grants. `env` goes to the contributors only; turns' own additions read no
    variable from it.

    For Claude, `env` of the result always holds `MOXI_TURNS_ENDPOINT`, the endpoint
    command the runner spawns. Nothing is written for it.

    Raises `ValueError` for a harness or a command turns does not know, and
    `moxi_turns.TurnsError` when Claude's plugin folder is missing, turns' config cannot
    be read, or there is no endpoint command. A contribution that fails is skipped with a
    note; it never fails the launch.
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
        from .config import COMMANDS_ENV

        out_env[COMMANDS_ENV] = ",".join(c for c in COMMANDS if c in granted)

    request = AdditionsRequest(harness, os.fspath(cwd),
                               MappingProxyType({str(k): str(v) for k, v in env.items()}),
                               session_id)
    group = load()
    for b in group.broken:
        notes.append(f"additions of {b.module} are off: {b.reason}")
    permission: dict[str, object] = {}
    for contributor in group.contributors:
        rendered = _ask(contributor, request)
        if rendered.skipped:
            notes.append(f"additions of {contributor.module} skipped: {rendered.skipped}")
            continue
        argv += rendered.argv
        if rendered.permission:
            _add_permission(permission, rendered.permission)
    if permission:
        options["permission"] = permission
    return LaunchAdditions(harness, tuple(argv), out_env, options, tuple(notes))


def _ask(contributor: Contributor, request: AdditionsRequest) -> _Rendered:
    try:
        answer = contributor.function(request)
    except Exception as exc:  # noqa: BLE001 - a contributor's failure is its own
        return _Rendered(skipped=f"it raised {_describe(exc)}", serious=True)
    if answer is None:
        return _Rendered()
    return render(answer, request.harness)


def render(answer: object, harness: str) -> _Rendered:
    """One contribution, rendered for `harness`, or the reason it is skipped whole."""
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
    if servers:
        named = ", ".join(sorted(map(str, servers)))
        return _Rendered(skipped=f"it names MCP servers ({named}), which this turns does "
                                 "not add yet")
    rules = tuple(permissions.get(harness) or ())
    if not rules:
        return _Rendered()
    if harness == "claude":
        return _Rendered(argv=tuple(f"--allowedTools={rule}" for rule in rules))
    if harness == "opencode":
        fragment: dict[str, object] = {}
        for rule in rules:
            _add_permission(fragment, _opencode_rule(rule))
        return _Rendered(permission=fragment)
    return _Rendered(skipped=f"it names {len(rules)} Codex tool rule(s), and turns knows "
                             "no way to give Codex a tool rule for one launch")


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
    reason that waits on turns (MCP servers, Codex rules) is `warn`; else `ok`, naming
    the harnesses it adds to."""
    from moxi import doctor as moxi_doctor

    from .doctor import MODULE

    group = load()
    out = []
    for b in group.broken:
        out.append(moxi_doctor.Row(MODULE, f"additions {b.module}", "fail", b.reason,
                                   "reinstall or fix that module"))
    cwd = os.getcwd()
    env = MappingProxyType(dict(os.environ))
    for contributor in group.contributors:
        adds, skips, serious = [], [], False
        for harness in HARNESSES:
            rendered = _ask(contributor, AdditionsRequest(harness, cwd, env))
            if rendered.skipped:
                skips.append(f"{harness}: {rendered.skipped}")
                serious = serious or rendered.serious
            elif rendered.argv or rendered.permission:
                adds.append(harness)
        if skips:
            out.append(moxi_doctor.Row(
                MODULE, f"additions {contributor.module}", "fail" if serious else "warn",
                "; ".join(skips) + (f"; adds to {', '.join(adds)}" if adds else "")))
        else:
            out.append(moxi_doctor.Row(
                MODULE, f"additions {contributor.module}", "ok",
                f"adds to {', '.join(adds)}" if adds else "adds nothing here"))
    return out


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


__all__ = ["GROUP", "HARNESSES", "AdditionsRequest", "Contribution", "LaunchAdditions",
           "launch_additions", "load", "render", "for_host", "merge_options", "rows"]
