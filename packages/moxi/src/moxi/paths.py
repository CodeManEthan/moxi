"""Where a Moxi module keeps its files: five roots, and one directory per root per module.

`Layout(module)` resolves them for one module `M`, whose variables take the prefix
`MOXI_<M>_`. For each root the first of these that is set wins (an empty variable counts
as unset):

1. the root's own variable, `MOXI_<M>_<KIND>_HOME` (`MOXI_<M>_RUNTIME_DIR` for runtime),
   which names the root: the module's directory is `<value>/M`;
2. `MOXI_<M>_HOME`, one home for all five of the module's roots: `<value>/<kind>/M`;
3. `MOXI_HOME`, one home for every module: `<value>/<kind>/M`;
4. the platform's default:

    kind     Linux and macOS                             Windows
    data     $XDG_DATA_HOME or ~/.local/share, /moxi/M    <Local>\\moxi\\data\\M
    state    $XDG_STATE_HOME or ~/.local/state, /moxi/M   <Local>\\moxi\\state\\M
    cache    $XDG_CACHE_HOME or ~/.cache, /moxi/M         <Local>\\moxi\\cache\\M
    config   $XDG_CONFIG_HOME or ~/.config, /moxi/M       <Roaming>\\moxi\\config\\M
    runtime  $XDG_RUNTIME_DIR/moxi/M, else               <Local>\\moxi\\runtime\\M
             <state root>/runtime/M

`<Roaming>` and `<Local>` are the user's roaming and local application data folders
(`%APPDATA%` and `%LOCALAPPDATA%`). Only config roams: data holds machine paths and
SQLite files that must not follow a profile to another machine.

The state and cache directories of a module that already has them under a host folder,
`<root>/<label>/M` (the layout before this one), are read where they are until they are
moved: `state_dir` and `cache_dir` return the old directory while the new one does not
exist.

`core()` is the core's own layout, for the name `moxi`: it reads only `MOXI_HOME` and the
defaults. The core keeps its device id and its install lock there.

Every method takes an optional `env` mapping and reads `os.environ` without one.

A module writes only under its own five directories. `script(name)` is where one of this
environment's own console scripts lives, and `python()` is this environment's Python, for
a module that writes the path of its own entry point into a hook, a unit file or a
launcher's argv: neither is ever looked up on PATH, where another program of the same
name may stand first.

This module is imported on hook paths, so it imports nothing heavy at module level.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from . import names

_WINDOWS = sys.platform == "win32"

#: The file name of a module's config file, in its config directory.
CONFIG_FILE = "config.toml"

#: The suffixes, after a module's prefix, of every variable that moves its roots.
LOCATORS = ("HOME", "DATA_HOME", "STATE_HOME", "CACHE_HOME", "CONFIG_HOME", "RUNTIME_DIR")

#: The one home for every module's roots.
HOME = "MOXI_HOME"

#: The device label shared by every module that names none of its own.
DEVICE = "MOXI_DEVICE"

#: The longest socket path, in bytes, that `socket_path` returns under a runtime directory.
#: macOS allows 104 bytes in a socket address and Linux 108.
SOCKET_MAX = 100

# Where a socket path too long for its runtime directory goes instead, as
# `<_SHORT_ROOT>/moxi-<uid>/`: `/tmp`, not `$TMPDIR`, which on macOS is long itself.
_SHORT_ROOT = "/tmp"

# The XDG variable and its default for each root kind but runtime.
_XDG = {
    "data": ("XDG_DATA_HOME", "~/.local/share"),
    "state": ("XDG_STATE_HOME", "~/.local/state"),
    "cache": ("XDG_CACHE_HOME", "~/.cache"),
    "config": ("XDG_CONFIG_HOME", "~/.config"),
}


class Layout:
    """The roots and directories of one module, under one environment prefix.

    An empty `prefix` reads no variable of the module's own: that is the core's layout,
    `core()`.
    """

    def __init__(self, module: str, prefix: str | None = None):
        self.module = names.check_module_name(module)
        self.prefix = names.env_prefix(module) if prefix is None else prefix
        self.namespace = names.NAMESPACE

    def __repr__(self) -> str:
        return f"Layout({self.module!r}, {self.prefix!r})"

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, Layout)
                and (self.module, self.prefix) == (other.module, other.prefix))

    def __hash__(self) -> int:
        return hash((self.module, self.prefix))

    def locators(self) -> tuple[str, ...]:
        """The names of every variable that moves this module's roots."""
        own = tuple(f"{self.prefix}{suffix}" for suffix in LOCATORS) if self.prefix else ()
        return (*own, HOME)

    def device(self, env: Mapping[str, str] | None = None) -> str:
        """This device's label for the module (`device_label`)."""
        return _label(self.prefix, _environ(env))

    # -- the roots ---------------------------------------------------------------------

    def _own(self, environ: Mapping[str, str], suffix: str) -> str | None:
        return environ.get(f"{self.prefix}{suffix}") or None if self.prefix else None

    def _root(self, kind: str, env: Mapping[str, str] | None) -> Path:
        environ = _environ(env)
        if per_root := self._own(environ, "RUNTIME_DIR" if kind == "runtime"
                                 else f"{kind.upper()}_HOME"):
            return Path(per_root)
        if home := self._own(environ, "HOME") or environ.get(HOME):
            return Path(home) / kind
        if _WINDOWS:
            roaming, local = _windows_folders()
            return Path(roaming if kind == "config" else local) / self.namespace / kind
        if kind == "runtime":
            if xdg := environ.get("XDG_RUNTIME_DIR"):
                return Path(xdg) / self.namespace
            # No login runtime directory (some containers): under the state root instead.
            return self._root("state", environ) / "runtime"
        variable, default = _XDG[kind]
        xdg = environ.get(variable) or _expanduser(default, environ)
        return Path(xdg) / self.namespace

    def data_root(self, env: Mapping[str, str] | None = None) -> Path:
        return self._root("data", env)

    def state_root(self, env: Mapping[str, str] | None = None) -> Path:
        return self._root("state", env)

    def cache_root(self, env: Mapping[str, str] | None = None) -> Path:
        return self._root("cache", env)

    def config_root(self, env: Mapping[str, str] | None = None) -> Path:
        return self._root("config", env)

    def runtime_root(self, env: Mapping[str, str] | None = None) -> Path:
        return self._root("runtime", env)

    # -- the module's directories ------------------------------------------------------

    def data_dir(self, env: Mapping[str, str] | None = None) -> Path:
        return self.data_root(env) / self.module

    def state_dir(self, env: Mapping[str, str] | None = None) -> Path:
        """The module's state directory, or its old one under a host folder until moved."""
        environ = _environ(env)
        return self._in_place(self.state_root(environ), environ)

    def cache_dir(self, env: Mapping[str, str] | None = None) -> Path:
        """The module's cache directory, or its old one under a host folder until moved."""
        environ = _environ(env)
        return self._in_place(self.cache_root(environ), environ)

    def config_dir(self, env: Mapping[str, str] | None = None) -> Path:
        return self.config_root(env) / self.module

    def runtime_dir(self, env: Mapping[str, str] | None = None, *,
                    create: bool = False) -> Path:
        """The module's runtime directory, where its sockets and page servers live.

        With `create=True` it is made private (`moxi.files.private_dir`): on POSIX 0700
        and refused when another user owns it; on Windows with an access list that grants
        only this user, read back to check. A process that serves a socket or a page
        creates its own runtime directory this way.
        """
        path = self.runtime_root(env) / self.module
        if create:
            from . import files

            files.private_dir(path)
        return path

    def config_file(self, env: Mapping[str, str] | None = None) -> Path:
        """The module's one config file."""
        return self.config_dir(env) / CONFIG_FILE

    def _in_place(self, root: Path, environ: Mapping[str, str]) -> Path:
        new = root / self.module
        if os.path.exists(new):
            return new
        for label in _legacy_labels(self.prefix, environ):
            old = root / label / self.module
            if os.path.isdir(old):
                return old
        return new


def core() -> Layout:
    """The core's own layout: the name `moxi`, reading only `MOXI_HOME` and the defaults.

    `core().state_dir()` is `~/.local/state/moxi/moxi/` on Linux by default.
    """
    return Layout(names.HARNESS, prefix="")


def core_lock(env: Mapping[str, str] | None = None) -> Path:
    """The core lock's file, `install.lock` in the core's state directory."""
    return core().state_dir(env) / "install.lock"


# -- the device -------------------------------------------------------------------------

#: The file in the core's state directory that holds this device's id.
DEVICE_ID = "device-id"


def device_id(env: Mapping[str, str] | None = None) -> str:
    """This device's id: a UUID (32 hex digits) written once in the core's state directory.

    Two first runs at once, in two processes or two threads of one, agree on one id: the
    file is written under a lock, and only when it is still missing once the lock is held.
    """
    path = core().state_dir(env) / DEVICE_ID
    if found := _read_id(path):
        return found
    import uuid

    from . import files

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # This process's threads queue for the lock rather than being refused by it.
    with files._gated_lock(path.with_name(f"{DEVICE_ID}.lock")):
        if found := _read_id(path):
            return found
        fresh = uuid.uuid4().hex
        files.atomic_write(path, f"{fresh}\n")
    return fresh


def device_label(module: str | None = None, env: Mapping[str, str] | None = None) -> str:
    """This device's label: `MOXI_<M>_DEVICE` when `module` is given and that is set, else
    `MOXI_DEVICE`, else `device_id()`. The variables name the device only; neither moves
    a root."""
    prefix = names.env_prefix(module) if module is not None else ""
    return _label(prefix, _environ(env))


def _label(prefix: str, environ: Mapping[str, str]) -> str:
    own = environ.get(f"{prefix}DEVICE") if prefix else None
    return own or environ.get(DEVICE) or device_id(environ)


def _read_id(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return text or None


def _legacy_labels(prefix: str, environ: Mapping[str, str]) -> list[str]:
    """The host-folder names the old layout used: the module's device variable, then the
    host name."""
    labels = []
    if prefix and (own := environ.get(f"{prefix}DEVICE")):
        labels.append(own)
    labels.append(_host_name())
    return [label for label in labels
            if label and label not in (".", "..") and "/" not in label and "\\" not in label]


def _host_name() -> str:
    if _WINDOWS:
        import platform

        return platform.node()
    return os.uname().nodename


# -- sockets and this environment's programs --------------------------------------------

def socket_path(module: str, name: str) -> str:
    """The path of module `module`'s Unix socket `name`, under `SOCKET_MAX` bytes.

    `<runtime_dir>/<name>.sock` when that is short enough, else `/tmp/moxi-<uid>/<h>.sock`
    with `h` the first 16 hex digits of the long path's SHA-256; `/tmp/moxi-<uid>` is made
    private and refused when another user owns it. Not `$TMPDIR`, which on macOS is long
    itself. The runtime directory is not created here. `socket_path(module, "page")` is a
    page server's socket. Windows has no such sockets (its endpoints are named pipes), so
    there it raises `NotImplementedError`.
    """
    if _WINDOWS:
        raise NotImplementedError("Windows has no Unix socket paths; its endpoints are "
                                  "named pipes")
    if not name or name in (".", "..") or "/" in name or "\0" in name:
        raise ValueError(f"{name!r} is not a socket name")
    path = os.path.join(_layout(module).runtime_dir(), f"{name}.sock")
    encoded = os.fsencode(path)
    if len(encoded) < SOCKET_MAX:
        return path
    import hashlib

    from . import files

    short = files.private_dir(os.path.join(_SHORT_ROOT, f"moxi-{os.getuid()}"))
    return os.path.join(short, f"{hashlib.sha256(encoded).hexdigest()[:16]}.sock")


def script(name: str) -> Path:
    """The path of console script `name` in this Python environment's scripts directory
    (`name.exe` on Windows), never one found on PATH.

    Raises `FileNotFoundError` when the environment holds no such script. Only the scripts
    directory is looked in: not PATH, and not the working directory, which Windows'
    own search tries first.
    """
    scripts = _scripts()
    for candidate in _script_names(name):
        path = scripts / candidate
        if path.is_file() and os.access(path, os.X_OK):
            return path
    raise FileNotFoundError(f"{name} is not installed in this environment "
                            f"(looked in {scripts})")


def _script_names(name: str) -> list[str]:
    """The file names script `name` may have: `name` itself on POSIX; on Windows `name`
    with each `PATHEXT` extension, or `name` alone when it already ends in one."""
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\0" in name:
        return []
    if not _WINDOWS:
        return [name]
    extensions = [e for e in os.environ.get("PATHEXT", "").split(os.pathsep) if e]
    extensions = extensions or [".COM", ".EXE", ".BAT", ".CMD"]
    if any(name.lower().endswith(e.lower()) for e in extensions):
        return [name]
    return [name + e.lower() for e in extensions]


def python() -> Path:
    """This environment's Python: `python` (`python.exe` on Windows) in its scripts
    directory.

    It is the environment's own path, not the interpreter it resolves to, so it stays put
    when the environment is upgraded (`uv tool upgrade`); hooks and units write it. An
    interpreter whose scripts directory holds no `python` (a system Python with only
    `python3`) gets `sys.executable`.
    """
    path = _scripts() / ("python.exe" if _WINDOWS else "python")
    return path if path.is_file() else Path(sys.executable)


def _scripts() -> Path:
    import sysconfig

    return Path(sysconfig.get_path("scripts"))


# -- helpers ----------------------------------------------------------------------------

def _layout(module: str) -> Layout:
    """`core()` for the core's own name, else the module's layout."""
    return core() if module == names.HARNESS else Layout(module)


def _environ(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _expanduser(path: str, environ: Mapping[str, str]) -> str:
    """`os.path.expanduser`, reading HOME from `environ` when it is a mapping of its own.

    Only a bare `~` or a `~/` start is this user's home; `~user/...` is another user's,
    found the way `os.path.expanduser` finds it.
    """
    if environ is os.environ or "HOME" not in environ:
        return os.path.expanduser(path)
    seps = ("/", "\\") if _WINDOWS else ("/",)
    if path == "~" or path.startswith(tuple("~" + sep for sep in seps)):
        return environ["HOME"] + path[1:]
    return os.path.expanduser(path)


def _windows_folders() -> tuple[str, str]:
    """The user's roaming and local application data folders (Windows only)."""
    import platformdirs

    return (platformdirs.user_data_dir(roaming=True),
            platformdirs.user_data_dir(roaming=False))


__all__ = ["CONFIG_FILE", "LOCATORS", "HOME", "DEVICE", "DEVICE_ID", "SOCKET_MAX", "Layout",
           "core", "core_lock", "device_id", "device_label", "socket_path", "script",
           "python"]
