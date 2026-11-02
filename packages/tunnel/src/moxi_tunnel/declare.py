"""The targets modules declare, read from the extension group `moxi.tunnel`.

A module offers a target as an entry named `<module>.<name>` whose value is a dict:

    [project.entry-points."moxi.tunnel"]
    "ui.gateway" = "moxi_ui.tunnel:GATEWAY"

    GATEWAY = {"reach": "own-devices",          # or "public"
               "port_setting": "gateway.port",   # an int key in this module's SETTINGS
               "doc": "the UI on your phone"}

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

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from moxi import config, modules, names

from .errors import Refused

#: The extension group targets are declared in.
GROUP = "moxi.tunnel"
REACHES = ("own-devices", "public")


@dataclass(frozen=True)
class Target:
    """One declared target: `<module>/<name>`, its reach and its port setting."""

    module: str
    name: str
    reach: str
    port_setting: str
    doc: str
    distribution: str

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

    @property
    def fix(self) -> str:
        return f"fix the moxi.tunnel entry {self.entry!r} in {self.distribution}, or " \
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
    """Every target declared in `moxi.tunnel` now, and every bad declaration."""
    targets: dict[str, Target] = {}
    bad: list[Bad] = []
    for ext in modules.extensions(GROUP):
        got = _one(ext)
        if isinstance(got, Bad):
            bad.append(got)
        else:
            targets[got.key] = got
    return Declarations(targets=dict(sorted(targets.items())), bad=tuple(bad))


def _one(ext) -> Target | Bad:
    def refuse(reason: str) -> Bad:
        return Bad(ext.name, ext.distribution, reason)

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
    return Target(module=module, name=name, reach=reach, port_setting=setting, doc=doc,
                  distribution=ext.distribution)


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


__all__ = ["GROUP", "Target", "Bad", "Declarations", "read", "loopback_port", "resolve",
           "same_port", "clashes"]
