"""The `moxi` command: the umbrella every module's commands run under.

    moxi <module> <verb> ...     runs the module's own command, in this process
    moxi modules [--json]        what this environment holds
    moxi modules --available     the first-party modules `moxi install` knows by name
    moxi install <module> ...    installs a module, sets it up, offers its extras
    moxi shortcut [<module>]     the opt-in bare command for a module
    moxi config <module> ...     a module's settings and its config file
    moxi config set ...          changes one of them (`moxi config unset` removes one)
    moxi paths <module> [--json] where a module keeps its files
    moxi check [PATH]            the module contract check
    moxi help [<verb>|<module>]  help
    moxi version                 versions

The verbs are reserved: no module may take one as its name. A module owns only what
follows its name: `moxi comms send ...` calls the `CLI` target comms' announcement names
as `main(argv, prog="moxi comms")` and exits with what it returns.

Only the core declares the console script `moxi = "moxi.cli:main"`: `uv tool install
moxi` puts `moxi` on PATH, and `moxi install <module>` adds a module to the environment it
runs in. A Moxi module installed anywhere else is a stray, which moxi can't see; every
command but `help`, `version` and `run` tells each new one once (`moxi.strays`).
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping

from . import names

USAGE = """\
usage: moxi <module> <verb> ...
       moxi <command> ...

commands:
  modules [--json]                   list the Moxi modules this environment holds
  modules --available [--refresh]    list the first-party modules, installed or not
  install <module> [options]         install a module and offer its optional extras
  shortcut [<module>] [--remove]     put a bare <module> command on PATH, or take it off
  config <module> [show [--json]|path|check|init]
                                     a module's settings and its config file
  config set|unset <module> <key> ...
                                     change one of a module's settings
  paths <module> [--json]            where a module keeps its files, and its stores
  check [--staged] [PATH]            check a module repo against the module contract
  help [<command>|<module>]          this help, or a command's or a module's
  version                            the versions of moxi and its modules
"""

#: How a shortcut file marks itself; `moxi shortcut --remove` removes only a file
#: carrying the marker of the module named. A Windows `.cmd` shortcut carries
#: `CMD_SHORTCUT_MARKER`.
SHORTCUT_MARKER = "# moxi shortcut: "
CMD_SHORTCUT_MARKER = "rem moxi shortcut: "


def main(argv: list[str] | None = None) -> int:
    """The `moxi` console script. Returns the exit code.

    Run as the console script (no `argv` given), every command but `help`, `version` and
    `run` first tells each stray not told before (`moxi.strays`). A caller passing `argv`
    in process runs the command alone.
    """
    console = argv is None
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if console:
            _tell_strays(argv)
        return _main(argv)
    except KeyboardInterrupt:
        print("moxi: interrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # Output piped into a reader that went away (`moxi modules | head`).
        try:
            sys.stdout = open(os.devnull, "w")  # noqa: SIM115
        except OSError:
            pass
        return 1


def _main(argv: list[str]) -> int:
    if not argv:
        sys.stderr.write(USAGE)
        return 2
    first, rest = argv[0], argv[1:]
    if first in ("-h", "--help"):
        sys.stdout.write(USAGE)
        return 0
    if first == "--version":
        return _version([])
    verb = _VERBS.get(first)
    if verb is not None:
        return _exit_code(verb, rest)
    if first in names.VERBS:
        return _not_yet(first)
    if first.startswith("-"):
        _error(f"unknown option {first!r}")
        sys.stderr.write(USAGE)
        return 2
    return dispatch(first, rest)


#: The first words that skip the stray check.
_NO_STRAY_CHECK = frozenset({"run", "help", "version", "-h", "--help", "--version"})


def _tell_strays(argv: list[str]) -> None:
    """One stderr line per stray not told before. Never fails the command."""
    if not argv or argv[0] in _NO_STRAY_CHECK:
        return
    try:
        from . import strays

        strays.tell()
    except Exception:  # noqa: BLE001 - a broken check must not stop the command
        return


def _stray_note(name: str) -> str | None:
    """The absent-module message's line about a stray copy of `name`, if there is one."""
    try:
        from . import strays

        stray = strays.copy_of(name)
    except Exception:  # noqa: BLE001
        return None
    return stray.absent_note() if stray is not None else None


def _exit_code(func, argv: list[str]) -> int:
    """Run a verb, turning argparse's SystemExit into an exit code."""
    try:
        code = func(argv)
    except SystemExit as exc:
        return _system_exit_code(exc)
    return 0 if code is None else int(code)


def _system_exit_code(exc: SystemExit) -> int:
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    print(code, file=sys.stderr)
    return 1


def _error(message: str) -> None:
    print(f"moxi: {message}", file=sys.stderr)


def _not_yet(verb: str) -> int:
    """A verb the core keeps for a command this version does not have."""
    _error(f"`moxi {verb}` is not in this version of moxi")
    return 2


def _parser(verb: str, description: str):
    import argparse

    return argparse.ArgumentParser(prog=f"moxi {verb}", description=description)


# -- dispatch to a module ---------------------------------------------------------------

def dispatch(name: str, argv: list[str]) -> int:
    """Run module `name`'s command with `argv`, in this process. Returns its exit code.

    An absent or broken module, or one with no command, prints why and returns 2.
    """
    from . import modules

    if not names.is_module_name(name):
        _error(f"{name!r} is neither a moxi command nor a module name")
        sys.stderr.write(USAGE)
        return 2
    found = modules.load(name)
    if found.state != modules.PRESENT:
        _error(found.reason)
        print(install_hint(name), file=sys.stderr)
        if found.state == modules.ABSENT:
            note = _stray_note(name)
            if note:
                print(note, file=sys.stderr)
        return 2
    if found.cli is None:
        print(f"{name}: {found.summary or '(no summary)'}", file=sys.stderr)
        print(f"{name} has no command of its own; other modules call it.", file=sys.stderr)
        users = users_of(name)
        if users:
            print(f"used here by: {', '.join(users)}", file=sys.stderr)
        else:
            print("no installed module uses it.", file=sys.stderr)
        return 2
    target, _, attr = found.cli.partition(":")
    try:
        import importlib

        func = getattr(importlib.import_module(target), attr)
    except Exception as exc:  # noqa: BLE001 - a broken module is reported, not raised
        _error(f"{name}'s command {found.cli} cannot be loaded: "
               f"{type(exc).__name__}: {exc}")
        print(install_hint(name), file=sys.stderr)
        return 2
    try:
        code = func(argv, prog=f"moxi {name}")
    except SystemExit as exc:
        return _system_exit_code(exc)
    # What the command returns means what `sys.exit` would make of it.
    return _system_exit_code(SystemExit(code))


def install_hint(name: str) -> str:
    """The one command that installs module `name`, which a feature missing it names.

    When installed modules declare an extra named by `name` that installs it (see
    `speakers_of`), the line goes through the first of them (`moxi install comms --with cache`) and says which range each speaks,
    so the depending module's range applies. Otherwise it is `moxi install <name>`.
    """
    speakers = speakers_of(name)
    if not speakers:
        return f"install it: moxi install {name}"
    first = speakers[0]
    ranges = "; ".join(f"{s.module} speaks {s.requirement}" for s in speakers)
    return f"install it: moxi install {first.module} --with {name}   ({ranges})"


# -- what the environment holds ----------------------------------------------------------

def normalize(name: str) -> str:
    """A distribution or extra name in its normalized form (PEP 503, PEP 685)."""
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def _distribution(name: str):
    from importlib.metadata import PackageNotFoundError, distribution

    try:
        return distribution(name)
    except PackageNotFoundError:
        return None


def _applies(req, extra: str) -> bool:
    """True when `req` is installed here with extra `extra` asked for ("" for none)."""
    if req.marker is None:
        return not extra
    try:
        return req.marker.evaluate({"extra": extra})
    except (ValueError, KeyError):
        return False


def requirements(dist_name: str, extra: str | None = None) -> list:
    """The requirements a distribution's metadata declares that apply in this environment,
    each a vendored `packaging` Requirement.

    With no `extra`, the ones it always installs; with one, the ones that extra adds. Every
    marker is evaluated here, `extra == ...` included. A line that does not parse, or whose
    marker cannot be evaluated, is skipped.
    """
    from ._vendor.packaging.requirements import Requirement

    dist = _distribution(dist_name)
    if dist is None:
        return []
    wanted = normalize(extra) if extra else ""
    out = []
    for line in dist.requires or ():
        try:
            req = Requirement(line)
        except ValueError:
            continue
        if not _applies(req, wanted):
            continue
        if wanted and _applies(req, ""):
            continue
        out.append(req)
    return out


_BOUND_ORDER = (">=", ">", "==", "===", "~=", "!=", "<=", "<")


def requirement_text(req) -> str:
    """A requirement as `moxi` shows it: normalized name, sorted extras, then the lower
    bounds before the upper ones (`moxi-cache>=0.1,<0.2`), or the URL. No marker."""
    name = normalize(req.name)
    extras = sorted(normalize(e) for e in req.extras)
    shown = f"{name}[{','.join(extras)}]" if extras else name
    if req.url:
        return f"{shown} @ {req.url}"
    specs = sorted(req.specifier, key=lambda s: (_BOUND_ORDER.index(s.operator), s.version))
    return shown + ",".join(str(s) for s in specs)


def provided_extras(dist_name: str) -> list[str]:
    dist = _distribution(dist_name)
    if dist is None:
        return []
    return [normalize(e) for e in dist.metadata.get_all("Provides-Extra") or ()]


def extra_installed(dist_name: str, extra: str, _seen: set | None = None) -> bool:
    """True when every requirement extra `extra` of `dist_name` adds is installed.

    A requirement is installed when its distribution is, and every extra it names is in
    turn. Version ranges are not checked; a requirement whose marker excludes this
    environment is not asked for.
    """
    seen = set() if _seen is None else _seen
    key = (normalize(dist_name), normalize(extra))
    if key in seen:
        return True
    seen.add(key)
    for req in requirements(dist_name, key[1]):
        if _distribution(req.name) is None:
            return False
        if not all(extra_installed(req.name, e, seen) for e in req.extras):
            return False
    return True


class Speaker:
    """An installed module that declares an extra named by another module."""

    __slots__ = ("module", "distribution", "requirement")

    def __init__(self, module: str, distribution: str, requirement: str):
        self.module = module
        self.distribution = distribution
        self.requirement = requirement


def speakers_of(name: str) -> list[Speaker]:
    """Installed modules declaring an extra that installs module `name`, with the
    requirement it adds."""
    from . import modules

    # Only an extra named by a module and installing that module's distribution routes
    # it. A feature extra that happens to share the name (comms' `codex`, which adds
    # moxi-turns[codex]) or a third party's extra says nothing about module `name`, so
    # the hint and `moxi install` would go through the wrong distribution.
    wanted = normalize(names.distribution(name))
    out = []
    for entry in modules.installed():
        if entry.name == name or not entry.distribution:
            continue
        if normalize(name) not in provided_extras(entry.distribution):
            continue
        own = [r for r in requirements(entry.distribution, name)
               if normalize(r.name) == wanted]
        if not own:
            continue
        out.append(Speaker(entry.name, entry.distribution,
                           ", ".join(requirement_text(r) for r in own)))
    return out


def _module_of_distribution() -> dict[str, str]:
    from . import modules

    return {normalize(e.distribution): e.name for e in modules.installed() if e.distribution}


def required_modules(dist_name: str, by_dist: dict[str, str] | None = None) -> list[str]:
    """The installed Moxi modules a distribution requires outright (not through an extra)."""
    by_dist = _module_of_distribution() if by_dist is None else by_dist
    out = []
    for req in requirements(dist_name):
        module = by_dist.get(normalize(req.name))
        if module is not None and module not in out:
            out.append(module)
    return out


def users_of(name: str) -> list[str]:
    """Installed modules that require module `name`, or take it through an installed
    extra."""
    from . import modules

    wanted = normalize(names.distribution(name))
    out = []
    for entry in modules.installed():
        if entry.name == name or not entry.distribution:
            continue
        dist = entry.distribution
        if any(normalize(r.name) == wanted for r in requirements(dist)):
            out.append(entry.name)
            continue
        for extra in provided_extras(dist):
            if (any(normalize(r.name) == wanted for r in requirements(dist, extra))
                    and extra_installed(dist, extra)):
                out.append(f"{entry.name} (through its extra {extra})")
                break
    return out


# -- moxi modules -------------------------------------------------------------------------

def describe() -> dict:
    """Everything `moxi modules --json` prints: one object for this environment.

    It reads every module by the full scan, so a name two distributions claim, or a module
    whose distribution is not named for it, shows as broken. Each module's object carries
    every field of its `Found` but the imported API module, under the field's name;
    `optional` is the richer list of its extras.
    """
    from . import modules

    shortcut_dir = _shortcut_dir()
    by_dist = _module_of_distribution()
    listed = []
    for found in modules.scan():
        dist = found.distribution or ""
        optional = []
        for extra, sentence in found.optional.items():
            optional.append({
                "extra": extra,
                "sentence": sentence,
                "installed": bool(dist) and extra_installed(dist, extra),
                "add": f"moxi install {found.name} --with {extra}",
            })
        item = _found_json(found)
        item.update({
            "distribution": dist,
            "version": found.version or "",
            "shortcut": shortcut_dir is not None and _is_shortcut(
                _shortcut_path(shortcut_dir, found.name), found.name),
            "requires": required_modules(dist, by_dist) if dist else [],
            "optional": optional,
        })
        listed.append(item)
    return {
        "moxi": _version_of("moxi"),
        "python": sys.executable,
        "prefix": sys.prefix,
        "modules": listed,
    }


def _found_json(found) -> dict:
    """A `Found`'s fields as JSON values: tables as objects, sets and tuples as lists."""
    def plain(value):
        if isinstance(value, Mapping):
            return {k: plain(v) for k, v in value.items()}
        if isinstance(value, frozenset):
            return sorted(value)
        if isinstance(value, (list, tuple)):
            return [plain(v) for v in value]
        return value

    return {name: plain(getattr(found, name)) for name in found._fields if name != "api"}


def _modules(argv: list[str]) -> int:
    parser = _parser("modules", "List the Moxi modules this Python environment holds.")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    parser.add_argument("--available", action="store_true",
                        help="list the first-party modules moxi install knows by name")
    parser.add_argument("--refresh", action="store_true",
                        help="with --available: fetch the published catalogue first (the "
                             "one command that uses the network)")
    args = parser.parse_args(argv)
    if args.refresh and not args.available:
        parser.error("--refresh goes with --available")
    if args.available:
        return _available(refresh=args.refresh, as_json=args.json)
    info = describe()
    if args.json:
        import json

        print(json.dumps(info, indent=2))
        return 0
    if not info["modules"]:
        print("No Moxi module is installed in this environment.")
        print("Install one with: moxi install <module>")
        return 0
    for item in info["modules"]:
        head = f"{item['name']}  {item['distribution']} {item['version']}  {item['state']}"
        if item["cli"]:
            head += f"  shortcut {'on' if item['shortcut'] else 'off'}"
        print(head)
        if item["reason"]:
            print(f"    {item['reason']}")
        if item["summary"]:
            print(f"    {item['summary']}")
        elif item["state"] == "present":
            print("    (no summary)")
        for opt in item["optional"]:
            mark = "installed" if opt["installed"] else "not installed"
            line = f"    [{opt['extra']}] {mark}: {opt['sentence']}"
            if not opt["installed"]:
                line += f"  ({opt['add']})"
            print(line)
    return 0


def _available(*, refresh: bool, as_json: bool) -> int:
    """`moxi modules --available`: the catalogue, each row installed or not."""
    from . import catalogue, modules

    try:
        if refresh:
            try:
                listed, said = catalogue.refresh()
            except catalogue.CatalogueError as exc:
                listed = catalogue.load()
                said = f"refresh failed: {exc}; the catalogue in effect stays"
            print(f"moxi: {said}", file=sys.stderr)
        else:
            listed = catalogue.load()
    except catalogue.CatalogueError as exc:
        _error(f"the catalogue cannot be read: {exc}")
        return 1
    states = {found.name: found for found in modules.scan()}
    rows = []
    for entry in listed.entries:
        found = states.get(entry.name)
        rows.append({"name": entry.name, "distribution": entry.distribution,
                     "summary": entry.summary,
                     "state": found.state if found is not None else modules.ABSENT,
                     "version": (found.version or "") if found is not None else "",
                     "install": f"moxi install {entry.name}"})
    if as_json:
        import json

        print(json.dumps({"version": listed.version, "source": listed.source,
                          "modules": rows}, indent=2))
        return 0
    for row in rows:
        if row["state"] == modules.ABSENT:
            mark = f"not installed  ({row['install']})"
        else:
            mark = f"installed {row['version']}".rstrip()
            if row["state"] != modules.PRESENT:
                mark += f" ({row['state']})"
        print(f"{row['name']}  {row['distribution']}  {mark}")
        print(f"    {row['summary']}")
    return 0


# -- moxi shortcut ------------------------------------------------------------------------

def _shortcut_dir() -> str | None:
    """The directory of the `moxi` on PATH, where shortcuts are written."""
    import shutil

    found = shutil.which("moxi")
    return os.path.dirname(found) if found else None


def _is_shortcut(path: str, module: str | None = None) -> bool:
    """True when `path` is a shortcut file (for `module`, when given)."""
    marked = _shortcut_module(path)
    return marked is not None and (module is None or marked == module)


def _shortcut_module(path: str) -> str | None:
    try:
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as fh:
            head = fh.read(256)
    except OSError:
        return None
    for line in head.decode("utf-8", "replace").splitlines()[:3]:
        for marker in (SHORTCUT_MARKER, CMD_SHORTCUT_MARKER):
            if line.startswith(marker):
                return line[len(marker):].strip() or None
    return None


def _on_windows() -> bool:
    return sys.platform == "win32"


def _shortcut_path(where: str, module: str) -> str:
    """Where module `module`'s shortcut goes in `where`: `<module>.cmd` on Windows."""
    return os.path.join(where, f"{module}.cmd" if _on_windows() else module)


def shortcut_text(module: str, *, windows: bool | None = None) -> str:
    """The content of module `module`'s shortcut file: a `/bin/sh` script, or on Windows
    a `.cmd` file (with CRLF line ends)."""
    if windows if windows is not None else _on_windows():
        return f"@echo off\r\n{CMD_SHORTCUT_MARKER}{module}\r\nmoxi {module} %*\r\n"
    return f'#!/bin/sh\n{SHORTCUT_MARKER}{module}\nexec moxi {module} "$@"\n'


def _shortcut(argv: list[str]) -> int:
    parser = _parser("shortcut", "Put a bare <module> command on PATH that runs "
                                 "`moxi <module>`, or take it off. With no module, "
                                 "list the shortcuts that are on.")
    parser.add_argument("module", nargs="?")
    parser.add_argument("--remove", action="store_true", help="take the shortcut off")
    args = parser.parse_args(argv)
    import shutil

    where = _shortcut_dir()
    if args.module is None:
        if args.remove:
            parser.error("--remove needs a module")
        if where is None:
            _error("moxi is not on PATH, so no shortcut can be on")
            return 1
        on = []
        try:
            entries = sorted(os.listdir(where))
        except OSError as exc:
            _error(f"cannot read {where}: {exc}")
            return 1
        for entry in entries:
            path = os.path.join(where, entry)
            module = _shortcut_module(path)
            if module is not None:
                on.append((module, path))
        if not on:
            print(f"No shortcut is on (they would be in {where}).")
        for module, path in on:
            print(f"{module}  {path}")
        return 0

    module = args.module
    if not names.is_module_name(module):
        _error(f"{module!r} is not a module name")
        return 2
    if where is None:
        _error("moxi is not on PATH; a shortcut is written beside it, so put the "
               "directory holding moxi on PATH first")
        return 1
    path = _shortcut_path(where, module)

    if args.remove:
        if not os.path.lexists(path):
            _error(f"no shortcut for {module} at {path}")
            return 1
        if not _is_shortcut(path, module):
            _error(f"{path} is not moxi's shortcut for {module}; leaving it alone")
            return 1
        os.unlink(path)
        print(f"removed the shortcut {path}")
        return 0

    from . import modules

    found = modules.load(module)
    if found.state != modules.PRESENT:
        _error(found.reason)
        print(install_hint(module), file=sys.stderr)
        return 1
    if found.cli is None:
        _error(f"{module} has no command of its own, so it takes no shortcut")
        return 1
    clash = shutil.which(module)
    if clash is not None:
        if _is_shortcut(clash, module):
            print(f"the shortcut for {module} is already on: {clash}")
            return 0
        _error(f"{module} is already on PATH at {clash}; refusing to shadow it. "
               f"Run it as `moxi {module}`.")
        return 1
    if os.path.lexists(path):
        _error(f"{path} exists; refusing to overwrite it")
        return 1
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                     0o755)
        with os.fdopen(fd, "wb") as fh:
            fh.write(shortcut_text(module).encode("utf-8"))
    except OSError as exc:
        _error(f"cannot write {path}: {exc}")
        return 1
    print(f"wrote the shortcut {path}: `{module} ...` now runs `moxi {module} ...`")
    return 0


# -- moxi config --------------------------------------------------------------------------

_CONFIG_USAGE = """\
usage: moxi config <module> [show [--json]|path|check|init]
       moxi config set <module> <key> <value> [--by WHO]
       moxi config set <module> <key> (--add VALUE | --remove VALUE) [--by WHO]
       moxi config unset <module> <key> [--by WHO]
"""


def _config(argv: list[str]) -> int:
    if argv and argv[0] in ("set", "unset"):
        return _config_change(argv[0], argv[1:])
    parser = _parser("config", "A module's settings and its config file. `moxi config "
                     "set` and `moxi config unset` change one key; `moxi` names the "
                     "core's own settings.")
    parser.usage = _CONFIG_USAGE.removeprefix("usage: ")
    parser.add_argument("module")
    parser.add_argument("action", nargs="?", default="show",
                        choices=("show", "path", "check", "init"),
                        help="show (the default): every key with its value, source, doc, "
                             "when it is read and who last set it; path: the file's path; "
                             "check: exit non-zero when the file cannot be read or the "
                             "module refuses it; init: "
                             "write the file with every key commented out at its default")
    parser.add_argument("--json", action="store_true",
                        help="with show: print the keys as one JSON list")
    args = parser.parse_args(argv)
    if args.json and args.action != "show":
        parser.error("--json goes with show")
    from . import config, modules
    from .paths import _layout

    module = args.module
    if not names.is_module_name(module):
        _error(f"{module!r} is not a module name")
        return 2
    if module == names.HARNESS:
        found = None
    else:
        found = modules.load(module)
        if found.state != modules.PRESENT:
            _error(found.reason)
            print(install_hint(module), file=sys.stderr)
            return 2
    path = _layout(module).config_file()
    if args.action == "path":
        print(path)
        return 0
    try:
        schema = config._core_schema() if found is None else config.schema_for(found)
    except config.ConfigError as exc:
        _error(f"{module}'s settings schema cannot be read: {exc}")
        return 1
    if schema is None:
        if args.json:
            print("[]")
            return 0
        print(f"{module} has no settings.")
        return 0
    if args.action == "init":
        if os.path.lexists(path):
            _error(f"{path} exists; refusing to overwrite it")
            return 1
        try:
            schema.check_env(module)
        except config.ConfigError as exc:
            _error(str(exc))
            return 1
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(config_template(module, schema))
        except OSError as exc:
            _error(f"cannot write {path}: {exc}")
            return 1
        print(f"wrote {path}")
        return 0
    if args.action == "check":
        try:
            config._check_config(module, schema, None if found is None
                                 else found.config_check)
        except config.ConfigError as exc:
            _error(str(exc))
            return 1
        state = "" if path.exists() else " (no file: every key at its default)"
        print(f"ok: {path}{state}")
        return 0
    if args.json:
        return _config_json(module, found, schema)
    try:
        settings = config.load(module, schema)
    except config.ConfigError as exc:
        _error(str(exc))
        return 1
    last = _last_set(config.history(module, tail=None), schema.keys)
    print(f"# {path}{'' if path.exists() else '  (no file: every key at its default)'}")
    for key in schema.live():
        value = settings[key.name]
        shown = "(none)" if value is None else _toml_value(
            str(value) if key.type == "path" else value)
        print(f"{key.name} = {shown}    [{settings.source(key.name)}]")
        extra = f"read at {key.read}"
        if key.env:
            extra += f"; env {key.env}"
        print(f"    {key.doc} ({extra})")
        record = last.get(key.name)
        if record is not None:
            print(f"    last {_DONE.get(record.get('op'), 'changed')} by "
                  f"{record.get('by')} at {record.get('at')}")
    return 0


_DONE = {"set": "set", "unset": "unset", "add": "added to", "remove": "removed from"}


def _config_json(module: str, found, schema) -> int:
    """`moxi config <module> show --json`: one JSON list, one object per live key.

    Each object holds the key's schema fields (`name`, `type`, `default`, `doc`,
    `values`, `read`, `env`, `needs`, `items`), its `value` and its `source` (`default`,
    `file` or `env`). A switch table also holds `switch_table: true` and `entries`, one
    `{name, value, source, named_by}` per entry in the file and per entry the module's
    units, wraps and questions name as a switch (see `_switch_names`). When the file
    cannot be read the output is one `{"error", "fix"}` object instead, and the exit 1.
    """
    import json

    from . import config

    try:
        settings = config.load(module, schema)
    except config.ConfigError as exc:
        print(json.dumps({"error": exc.problem, "fix": exc.fix}, indent=2))
        return 1
    names: list = []
    out = []
    for key in schema.live():
        item = {field: _plain_json(getattr(key, field))
                for field in ("name", "type", "default", "doc", "values", "read", "env",
                              "needs", "items")}
        item["value"] = _plain_json(settings[key.name])
        item["source"] = settings.source(key.name)
        if config.is_switch_table(key):
            if not names:
                names.append(_switch_names(module, found))
            named, unavailable = names[0]
            item["switch_table"] = True
            item["entries"] = _switch_entries(key.name, settings, named,
                                              unavailable is None)
            if unavailable is not None:
                item["declarations"] = "unavailable"
                item["reason"] = unavailable
        out.append(item)
    print(json.dumps(out, indent=2))
    return 0


def _plain_json(value):
    from collections.abc import Mapping as _Mapping
    from pathlib import PurePath

    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, _Mapping):
        return {str(k): _plain_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain_json(v) for v in value]
    return value


def _switch_names(module: str, found) -> tuple[dict[str, list[str]], str | None]:
    """What names each switch of `module`: `{switch: ["daemon:<n>", "timer:<n>",
    "wrap:<ref>", "question"]}`, from its merged declarations (the read `plan` makes, with
    its time limit), its wraps and its `QUESTIONS`; and the reason when the declarations
    could not be read (None when they were), in which case the table is empty."""
    named: dict[str, list[str]] = {}
    if found is None:
        return named, None
    from . import units

    try:
        decl = units.declarations(module)
    except units.DeclarationError as exc:
        return {}, f"UNITS_FROM failed: {exc.text}"
    for kind, table in (("daemon", decl.daemons), ("timer", decl.timers)):
        for name, entry in table.items():
            if entry.get("switch"):
                named.setdefault(entry["switch"], []).append(f"{kind}:{name}")
    try:
        wrapped = units.wraps(module)
    except OSError:
        wrapped = {}
    for ref, wrap in sorted(wrapped.items()):
        named.setdefault(wrap.key, []).append(f"wrap:{ref}")
    for key in found.questions:
        named.setdefault(key, []).append("question")
    return named, None


def _switch_entries(table: str, settings, named: dict[str, list[str]],
                    with_named: bool) -> list[dict]:
    """A switch table's entries: each in the file, then each a switch names that the file
    lacks (false, from the default); by entry name."""
    in_file = {}
    if settings.source(table) == "file":
        in_file = dict(settings[table] or {})
    by_entry: dict[str, list[str]] = {}
    if with_named:
        for switch, by in named.items():
            head, dot, entry = switch.rpartition(".")
            if dot and head == table and entry:
                by_entry.setdefault(entry, []).extend(by)
    entries = []
    for entry in sorted(set(in_file) | set(by_entry)):
        there = entry in in_file
        entries.append({"name": entry, "value": bool(in_file[entry]) if there else False,
                        "source": "file" if there else "default",
                        "named_by": by_entry.get(entry, [])})
    return entries


def _last_set(records: list[dict], keys) -> dict[str, dict]:
    """The newest change of each key in `keys`, a switch table's entries counting as
    changes of their table. `records` is oldest first."""
    last: dict[str, dict] = {}
    for record in records:
        key = record.get("key")
        if isinstance(key, str):
            for name in (key, key.rpartition(".")[0]):
                if name in keys:
                    last[name] = record
    return last


def _config_change(op: str, argv: list[str]) -> int:
    """`moxi config set` and `moxi config unset`."""
    if op == "set":
        description = ("Set one key of a module's config file. The value is read by the "
                       "key's type; a list or a table is one TOML value. --add and "
                       "--remove change one item of a list.")
    else:
        description = "Remove one key from a module's config file, so it reads its default."
    parser = _parser(f"config {op}", description)
    parser.add_argument("module", help="the module, or moxi for the core's own settings")
    parser.add_argument("key", help="the setting's dotted name")
    if op == "set":
        parser.add_argument("value", nargs="?", help="the new value")
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--add", metavar="VALUE", help="add one item to a list")
        group.add_argument("--remove", metavar="VALUE",
                           help="remove one item from a list")
    parser.add_argument("--by", default="cli", metavar="WHO",
                        help="who makes the change, for the history (default: cli)")
    args = parser.parse_args(argv)
    if op == "set":
        given = [v is not None for v in (args.value, args.add, args.remove)]
        if sum(given) != 1:
            parser.error("give exactly one of a value, --add VALUE or --remove VALUE")
        if args.add is not None:
            op, value = "add", args.add
        elif args.remove is not None:
            op, value = "remove", args.remove
        else:
            value = args.value
    else:
        value = None
    from . import config

    try:
        changed = config._change(args.module, args.key, op, value, by=args.by, text=True)
    except config.ConfigError as exc:
        _error(str(exc))
        return exc.exit_code
    if not changed and op == "unset":
        print(f"{args.module}: {args.key} is not set in the file; nothing changed")
    elif not changed:
        print(f"{args.module}: {args.key} already holds that item; nothing changed")
    elif op in ("add", "remove"):
        done = "added" if op == "add" else "removed"
        print(f"{args.module}: {args.key}: one item {done} (by {args.by})")
    else:
        print(f"{args.module}: {args.key} {op} (by {args.by})")
    if op in ("set", "unset"):
        applied = config.apply_units(args.module, args.key)
        if any(not a.ok for a in applied):
            return 1
    return 0


def config_template(module: str, schema) -> str:
    """The text `moxi config <module> init` writes: every live key commented out at its
    default, under its doc. Uncommenting every line gives a file that reads as the
    defaults."""
    lines = [f"# The config file of the Moxi module {module}.",
             "# Every key is shown commented out at its default. Uncomment a key to set it;",
             "# `moxi config " + module + "` shows what is in effect.", ""]
    groups: dict[str, list] = {}
    for key in schema.live():
        group, _, leaf = key.name.rpartition(".")
        groups.setdefault(group, []).append((leaf, key))
    order = sorted(groups, key=lambda g: (g != "", list(groups).index(g)))
    for group in order:
        if group:
            lines.append(f"# [{group}]")
        for leaf, key in groups[group]:
            notes = [f"read at {key.read}"]
            if key.values:
                notes.append("one of " + ", ".join(map(_toml_value, key.values)))
            if key.env:
                notes.append(f"env {key.env} overrides it")
            lines.append(f"# {key.doc} ({'; '.join(notes)})")
            lines.append(f"# {_toml_key(leaf)} = {_toml_value(key.default)}")
        lines.append("")
    return "\n".join(lines)


_BARE = re.compile(r"[A-Za-z0-9_-]+")


def _toml_key(key: str) -> str:
    return key if _BARE.fullmatch(key) else _toml_string(key)


def _toml_string(text: str) -> str:
    out = ['"']
    for ch in text:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _toml_value(value) -> str:
    """A TOML inline value for a schema default."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value:
            return "nan"
        if value in (float("inf"), float("-inf")):
            return "inf" if value > 0 else "-inf"
        return repr(value)
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        return "{ " + ", ".join(f"{_toml_key(str(k))} = {_toml_value(v)}"
                                for k, v in value.items()) + " }"
    raise TypeError(f"cannot write {value!r} as TOML")


# -- moxi check, help, version --------------------------------------------------------------

def _check(argv: list[str]) -> int:
    from . import contract

    return contract.main(argv)


# -- moxi paths ---------------------------------------------------------------------------

def _paths(argv: list[str]) -> int:
    parser = _parser("paths", "Where a module keeps its files: its five directories, its "
                              "config file and each store it declares. `moxi paths moxi` "
                              "gives the core's own.")
    parser.add_argument("module")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    args = parser.parse_args(argv)
    module = args.module
    if not names.is_module_name(module):
        _error(f"{module!r} is not a module name")
        return 2
    try:
        info = _paths_of(module)
    except LookupError as exc:
        _error(str(exc))
        print(install_hint(module), file=sys.stderr)
        return 2
    if args.json:
        import json

        print(json.dumps(info, indent=2))
        return 0
    print(info["module"])
    for kind, path in info["directories"].items():
        print(f"  {kind:<12}{path}")
    print(f"  {'config file':<12}{info['config_file']}")
    for note in info["notes"]:
        print(f"  {note}")
    if info["stores"]:
        print("stores")
    for store in info["stores"]:
        if store["exists"]:
            facts = f"{store['kind']}  {_size_text(store['size'])}  {store['mode']}"
        else:
            facts = f"{store['kind']}  " + ("not created yet" if store["lazy"] else "missing")
        print(f"  {store['name']}  {store['path']}  {facts}")
    return 0


def _paths_of(module: str) -> dict:
    """`moxi paths <module>` as one JSON-ready mapping. `LookupError` when the module is
    not installed here, or broken; `moxi` is the core, which declares no stores."""
    from . import paths

    if module == names.HARNESS:
        layout, stores = paths.core(), {}
    else:
        from . import modules

        found = modules.load(module)
        if found.state != modules.PRESENT:
            raise LookupError(found.reason)
        layout, stores = paths.Layout(module), found.stores
    directories = {"data": layout.data_dir(), "state": layout.state_dir(),
                   "cache": layout.cache_dir(), "config": layout.config_dir(),
                   "runtime": layout.runtime_dir()}
    notes = [f"{kind} is read where it is, under a host folder; a later release of "
             "moxi moves it"
             for kind, root in (("state", layout.state_root()), ("cache", layout.cache_root()))
             if directories[kind] != root / module]
    listed = []
    for name, entry in stores.items():
        path = directories[entry["root"]] / entry["path"]
        listed.append({"name": name, "path": str(path), "root": entry["root"],
                       "kind": entry["kind"], "lazy": bool(entry.get("lazy", False)),
                       **_store_facts(path, entry["kind"])})
    return {"module": module,
            "directories": {kind: str(path) for kind, path in directories.items()},
            "config_file": str(layout.config_file()), "notes": notes, "stores": listed}


def _store_facts(path, kind: str) -> dict:
    """Whether a store exists, its size in bytes (a directory's: every file under it) and
    its permission bits."""
    import stat

    try:
        st = os.stat(path)
    except FileNotFoundError:
        return {"exists": False, "size": None, "mode": None}
    size = st.st_size
    if kind == "dir" and stat.S_ISDIR(st.st_mode):
        size = 0
        for top, _, files in os.walk(path):
            for name in files:
                try:
                    size += os.lstat(os.path.join(top, name)).st_size
                except OSError:
                    pass
    return {"exists": True, "size": size, "mode": f"{stat.S_IMODE(st.st_mode):04o}"}


def _size_text(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    for unit in ("KB", "MB", "GB"):
        size /= 1024
        if size < 1024 or unit == "GB":
            break
    return f"{size:.1f} {unit}"


def _install(argv: list[str]) -> int:
    from . import install

    return install.main(argv)


def _help(argv: list[str]) -> int:
    if not argv:
        sys.stdout.write(USAGE)
        return 0
    topic, rest = argv[0], argv[1:]
    if topic in ("help", "-h", "--help"):
        sys.stdout.write(USAGE)
        return 0
    verb = _VERBS.get(topic)
    if verb is not None:
        return _exit_code(verb, ["--help"])
    if topic in names.VERBS:
        return _not_yet(topic)
    return dispatch(topic, ["--help", *rest])


def _version_of(dist: str) -> str | None:
    found = _distribution(dist)
    return found.version if found is not None else None


def _version(argv: list[str]) -> int:
    parser = _parser("version", "The versions of moxi and the modules it sees.")
    parser.parse_args(argv)
    from . import modules

    print(f"moxi {_version_of('moxi') or 'unknown'}")
    for entry in modules.installed():
        print(f"{entry.name} {entry.version} ({entry.distribution})")
    return 0


_VERBS = {
    "modules": _modules,
    "install": _install,
    "shortcut": _shortcut,
    "config": _config,
    "paths": _paths,
    "check": _check,
    "help": _help,
    "version": _version,
}


__all__ = ["main", "dispatch", "install_hint", "describe", "speakers_of", "users_of",
           "required_modules", "extra_installed", "normalize", "shortcut_text",
           "config_template", "SHORTCUT_MARKER", "CMD_SHORTCUT_MARKER"]
