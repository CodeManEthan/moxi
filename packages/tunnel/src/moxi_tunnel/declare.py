"""The targets modules declare, read from the extension groups `moxi.tunnel` and
`moxi.tunnel.targets`.

A module offers a target as an entry named `<module>.<name>` whose value is a dict:

    [project.entry-points."moxi.tunnel"]
    "ui.gateway" = "moxi_ui.tunnel:GATEWAY"

    GATEWAY = {"reach": "own-devices",          # or "public"
               "port_setting": "gateway.port",   # an int key in this module's SETTINGS
               "doc": "the UI on your phone"}

A declaration in `moxi.tunnel.targets` may also name two callables, each as
`"<python module>:<callable>"`:

- `upstream() -> str` gives what the provider forwards to: `unix:<absolute path>` or
  `http://127.0.0.1:<port>`, or raises `moxi_tunnel.NotReady(text)`. Tunnel calls it
  before every apply of the target, and refuses the act with the text when it raises.
- `retired() -> list` gives origins, `{"host", "port"}` each, the module keeps a copy of.
  Tunnel adds them to its record's retired origins before it places the target.

These two live in their own group because a tunnel that predates them reads only
`moxi.tunnel`, and would apply the target as a plain loopback port. This version refuses
as bad a `moxi.tunnel` declaration that names `upstream`, `retired` or `lifetime`, a
`lifetime` in either group (it can't keep an entry that lives only with its owner's
process), and a target declared in both groups.

The core reads an entry's offering module as its name up to the first `.`, and so does
tunnel: the module and the target name come from splitting the entry name there, and the
target is named `<module>/<name>`. Keys of the dict tunnel doesn't know are ignored, so a
later version can add some.

A bad declaration is refused (exit 3) and shown by the doctor as a `fail` row. An entry is
bad when its name has no `.` or an empty part, when its module part is not the module of
the distribution that declares it, when its value can't be loaded or is not a dict, when
`reach` is not `own-devices` or `public`, when `doc` is given and is not a string, and when
`port_setting` is not an `int` key of the module's settings.

The loopback port is a setting, read through `moxi.config` each time a target is applied
(`loopback_port`), so a port the user moves shows as `moved` and is followed on the same
address. Two targets may not hold one loopback port: the second add is refused
(`same_port`), and `clashes` names every recorded pair whose settings hold one port now.
"""

from __future__ import annotations

import importlib
import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from moxi import config, modules, names

from .errors import NotReady, Refused

#: The extension group targets are declared in, read by every version of tunnel.
GROUP = "moxi.tunnel"
#: The group a declaration that names `upstream` or `retired` is declared in.
TARGETS_GROUP = "moxi.tunnel.targets"
GROUPS = (GROUP, TARGETS_GROUP)
REACHES = ("own-devices", "public")
#: The declaration keys only `moxi.tunnel.targets` may hold.
NEW_KEYS = ("upstream", "retired", "lifetime")
_CALLABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*")
_HTTP = re.compile(r"http://127\.0\.0\.1:([0-9]{1,5})")


@dataclass(frozen=True)
class Target:
    """One declared target: `<module>/<name>`, its reach and its port setting."""

    module: str
    name: str
    reach: str
    port_setting: str
    doc: str
    distribution: str
    #: `"<python module>:<callable>"` giving the upstream, or None for the port setting.
    upstream: str | None = None
    #: `"<python module>:<callable>"` giving the module's copy of retired origins, or None.
    retired: str | None = None

    @property
    def key(self) -> str:
        """The target's name, `<module>/<name>`."""
        return f"{self.module}/{self.name}"


@dataclass(frozen=True)
class Bad:
    """A declaration tunnel refuses: the entry's name, its distribution and why."""

    entry: str
    distribution: str
    reason: str
    group: str = GROUP

    @property
    def fix(self) -> str:
        return f"fix the {self.group} entry {self.entry!r} in {self.distribution}, or " \
               "uninstall it"


@dataclass(frozen=True)
class Declarations:
    """What one read of the group gave: the targets by `<module>/<name>`, and the bad
    declarations."""

    targets: Mapping[str, Target]
    bad: tuple[Bad, ...]


def _dist_module(distribution: str) -> str | None:
    """The module a distribution named `moxi-<module>` holds, else None."""
    normal = re.sub(r"[-_.]+", "-", distribution).lower()
    prefix = f"{names.NAMESPACE}-"
    if not normal.startswith(prefix):
        return None
    module = normal[len(prefix):]
    return module if names.is_module_name(module) else None


def read() -> Declarations:
    """Every target declared in `moxi.tunnel` and `moxi.tunnel.targets` now, and every bad
    declaration. A target declared in both groups is bad, in each."""
    targets: dict[str, Target] = {}
    bad: list[Bad] = []
    groups: dict[str, list[str]] = {}
    for group in GROUPS:
        for ext in modules.extensions(group):
            groups.setdefault(ext.name, []).append(group)
    for group in GROUPS:
        for ext in modules.extensions(group):
            if len(groups[ext.name]) > 1:
                bad.append(Bad(ext.name, ext.distribution, f"it is declared in both {GROUP} "
                               f"and {TARGETS_GROUP}", group))
                continue
            got = _one(ext, group)
            if isinstance(got, Bad):
                bad.append(got)
            else:
                targets[got.key] = got
    return Declarations(targets=dict(sorted(targets.items())), bad=tuple(bad))


def _one(ext, group: str = GROUP) -> Target | Bad:
    def refuse(reason: str) -> Bad:
        return Bad(ext.name, ext.distribution, reason, group)

    module, dot, name = ext.name.partition(".")
    if not dot or not module or not name:
        return refuse("its name is not <module>.<name>")
    if "/" in name or any(ch.isspace() for ch in name):
        return refuse("its target name holds a / or a space")
    owner = _dist_module(ext.distribution)
    if module != owner:
        return refuse(f"its module part {module!r} is not the module of {ext.distribution}")
    try:
        value = ext.load()
    except modules.ExtensionError as exc:
        return refuse(exc.reason)
    if not isinstance(value, dict):
        return refuse(f"its value is a {type(value).__name__}, not a dict")
    reach = value.get("reach")
    if reach not in REACHES:
        return refuse(f"its reach is {reach!r}, not own-devices or public")
    doc = value.get("doc", "")
    if not isinstance(doc, str):
        return refuse("its doc is not a string")
    setting = value.get("port_setting")
    if not isinstance(setting, str):
        return refuse("its port_setting is not the name of a setting")
    problem = _int_key(module, setting)
    if problem:
        return refuse(problem)
    if "lifetime" in value:
        return refuse("it names lifetime, which this version of tunnel can't keep")
    if group == GROUP:
        named = [key for key in NEW_KEYS if key in value]
        if named:
            return refuse(f"it names {', '.join(named)} in {GROUP}, which tunnels before "
                          f"0.2 would ignore; declare it in {TARGETS_GROUP}")
    calls = {}
    for key in ("upstream", "retired"):
        spec = value.get(key)
        if spec is not None and not (isinstance(spec, str) and _CALLABLE.fullmatch(spec)):
            return refuse(f"its {key} is not \"<python module>:<callable>\"")
        calls[key] = spec
    return Target(module=module, name=name, reach=reach, port_setting=setting, doc=doc,
                  distribution=ext.distribution, **calls)


def _schema(module: str) -> config.Schema:
    found = modules.load(module)
    if not found.ok:
        raise Refused(f"the module {module} is {found.state}: {found.reason}",
                      f"moxi install {module}")
    schema = config.schema_for(found)
    if schema is None:
        raise Refused(f"the module {module} declares no settings")
    return schema


def _int_key(module: str, setting: str) -> str | None:
    try:
        schema = _schema(module)
    except Refused as exc:
        return f"its port_setting {setting} can't be checked: {exc.reason}"
    except config.ConfigError as exc:
        return f"its port_setting {setting} can't be checked: {exc}"
    key = schema.keys.get(setting)
    if key is None or key.retired is not None or key.type != "int":
        return f"its port_setting {setting} is not an int setting of {module}"
    return None


def loopback_port(target: Target) -> int:
    """The target's loopback port, read from its setting now. Raises `Refused` (exit 3)
    when the setting can't be read or holds no port from 1 to 65535."""
    try:
        port = config.load(target.module, _schema(target.module))[target.port_setting]
    except config.ConfigError as exc:
        raise Refused(f"{target.key}'s port setting {target.module} {target.port_setting} "
                      f"can't be read: {exc.problem}", exc.fix) from None
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
        raise Refused(f"{target.key}'s port setting {target.module} {target.port_setting} "
                      f"is {port!r}, not a port from 1 to 65535",
                      f"moxi config set {target.module} {target.port_setting} <port>")
    return port


def _call(target: Target, key: str, spec: str):
    """Call the callable `spec` names for `target`'s `key`. `NotReady` goes to the caller;
    any other failure is `Refused`."""
    module, _, attr = spec.partition(":")
    try:
        fn = getattr(importlib.import_module(module), attr)
        return fn()
    except NotReady:
        raise
    except Exception as exc:  # noqa: BLE001 - a module's callable never breaks tunnel
        raise Refused(f"{target.key}'s {key} {spec} failed: {type(exc).__name__}: {exc}",
                      f"moxi doctor {target.module}") from None


def upstream(target: Target) -> tuple[str, int | str]:
    """What `target` forwards to now, as the record's `(form, target)`: `("unix", path)` or
    `("http", port)`. A target with no `upstream` reads its port setting. Raises `NotReady`
    with the callable's text, or `Refused` (exit 3) for a value tunnel can't apply."""
    if target.upstream is None:
        return "http", loopback_port(target)
    value = _call(target, "upstream", target.upstream)
    if isinstance(value, str):
        if value.startswith("unix:"):
            path = value.removeprefix("unix:")
            if sys.platform == "win32":
                raise Refused(f"{target.key}'s upstream is a Unix socket, which the "
                              "providers forward to only on Linux, macOS and BSD",
                              f"moxi doctor {target.module}")
            if path.startswith("/") and not any(ord(c) < 32 or ord(c) == 127 for c in path):
                return "unix", path
        match = _HTTP.fullmatch(value)
        if match and 0 < int(match[1]) < 65536:
            return "http", int(match[1])
    raise Refused(f"{target.key}'s upstream gave {value!r}, not unix:<absolute path> or "
                  "http://127.0.0.1:<port>", f"moxi doctor {target.module}")


def retired(target: Target) -> list[tuple[str, int]]:
    """The origins `target`'s `retired` callable gives, `(host, port)` each; `[]` with no
    callable. Raises `Refused` (exit 3) when it fails or gives something else."""
    if target.retired is None:
        return []
    value = _call(target, "retired", target.retired)
    out = []
    try:
        for item in value:
            host, port = ((item["host"], item["port"]) if isinstance(item, Mapping)
                          else tuple(item))
            if not (isinstance(host, str) and isinstance(port, int)
                    and not isinstance(port, bool) and 0 < port < 65536):
                raise ValueError(item)
            out.append((host.lower().rstrip("."), port))
    except (TypeError, ValueError, KeyError):
        raise Refused(f"{target.key}'s retired gave something that is not a list of "
                      "origins", f"moxi doctor {target.module}") from None
    return out


def resolve(text: str, keys: Iterable[str]) -> str:
    """The target `text` names among `keys`: `<module>/<name>`, or the bare name when only
    one module has it. Raises `Refused` (exit 3) naming the choices otherwise."""
    keys = sorted(set(keys))
    if "/" in text:
        if text in keys:
            return text
        raise Refused(f"no target {text}", _choices(keys))
    hits = [key for key in keys if key.split("/", 1)[1] == text]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise Refused(f"no target {text}", _choices(keys))
    raise Refused(f"{len(hits)} modules declare a target {text}: {', '.join(hits)}",
                  "name it as <module>/<name>")


def _choices(keys: list[str]) -> str:
    return ("the targets are " + ", ".join(keys)) if keys else "no module declares a target"


def same_port(port: int, ports: Mapping[str, int], *, exclude: str | None = None) -> list[str]:
    """The targets among `ports` (target -> loopback port now) that hold `port`, besides
    `exclude`: an add of `exclude` is refused while this is not empty."""
    return sorted(key for key, held in ports.items() if held == port and key != exclude)


def clashes(ports: Mapping[str, int]) -> dict[str, list[str]]:
    """For each recorded target (target -> loopback port now) whose port another holds,
    the others that hold it. Every target of a clashing pair is a key."""
    by_port: dict[int, list[str]] = {}
    for key, port in ports.items():
        by_port.setdefault(port, []).append(key)
    out = {}
    for holders in by_port.values():
        if len(holders) > 1:
            for key in holders:
                out[key] = sorted(h for h in holders if h != key)
    return dict(sorted(out.items()))


__all__ = ["GROUP", "TARGETS_GROUP", "GROUPS", "Target", "Bad", "Declarations", "read",
           "loopback_port", "upstream", "retired", "resolve", "same_port", "clashes"]
