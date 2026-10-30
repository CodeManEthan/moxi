"""Strays: Moxi modules installed outside the environment `moxi` runs in.

Only the core declares the `moxi` script, so a module installed on its own (`uv tool
install moxi-cache`, `pip install --user moxi-cache`) lands where this `moxi` can't see
it. A stray is a `moxi-<m>` distribution (a `moxi_<m>-<version>.dist-info` directory)
found in:

1. uv's tool directory: `UV_TOOL_DIR`; else the parent of `sys.prefix` when this is a uv
   tool environment; else uv's default (`$XDG_DATA_HOME/uv/tools` or
   `~/.local/share/uv/tools`, `%APPDATA%\\uv\\data\\tools` on Windows). Each other tool
   there is read;
2. the user site directory;
3. the environment of every other `moxi` on PATH whose real path is not this
   environment's own script (uv's link to it resolves to the same file).

The scan is stats and directory listings. Its answer is cached in
`STATE/moxi/strays.json` under a key made of the tool directory's and the user site's
modification times, a hash of PATH and this environment's prefix; an unchanged key uses
the cached answer. The same file records which strays were already told, so each is told
once.
"""

from __future__ import annotations

import os
import sys

#: The cache and told-record file, in the core's state directory.
RECORD = "strays.json"


class Stray:
    """One module distribution outside this environment."""

    __slots__ = ("module", "distribution", "version", "where", "kind", "tool")

    def __init__(self, module: str, version: str, where: str, kind: str,
                 tool: str | None = None):
        self.module = module
        self.distribution = f"moxi-{module}"
        self.version = version
        #: The environment (or user site directory) that holds it.
        self.where = where
        #: "tool" (a uv tool), "user" (the user site) or "path" (beside another moxi).
        self.kind = kind
        #: The uv tool's name, for a "tool" stray or a "path" one in a uv tool.
        self.tool = tool

    @property
    def key(self) -> str:
        return f"{self.distribution}@{self.where}"

    def __eq__(self, other):
        if other.__class__ is not self.__class__:
            return NotImplemented
        return self.as_json() == other.as_json()

    def __hash__(self):
        return hash(self.key)

    def __repr__(self):
        return f"Stray({self.distribution} {self.version} {self.kind} at {self.where})"

    def as_json(self) -> dict:
        return {"module": self.module, "version": self.version, "where": self.where,
                "kind": self.kind, "tool": self.tool}

    @classmethod
    def from_json(cls, data: dict) -> Stray:
        return cls(data["module"], data["version"], data["where"], data["kind"],
                   data.get("tool"))

    def place(self) -> str:
        """Where it is, in words."""
        if self.kind == "tool":
            return f'uv tool "{self.tool}"'
        if self.kind == "user":
            return f"the user site {self.where}"
        return f"the environment {self.where}"

    def remove_command(self) -> str:
        """The command that removes the other copy."""
        if self.tool:
            return f"uv tool uninstall {self.tool}"
        if self.kind == "user":
            return f"python -m pip uninstall {self.distribution}"
        python = os.path.join(self.where, "Scripts" if _windows() else "bin", "python")
        return f"{python} -m pip uninstall {self.distribution}"

    def line(self) -> str:
        """The one line a command prints the first time it finds this stray."""
        return (f"moxi: {self.distribution} is installed outside this moxi "
                f"({self.place()}), where moxi can't see it. Install it here: moxi install "
                f"{self.module}. Then remove the other: {self.remove_command()}")

    def absent_note(self) -> str:
        """What an absent-module message adds when this copy exists."""
        return (f"a copy is installed at {self.where}; `moxi install {self.module}` puts "
                f"it where moxi sees it")


def _windows() -> bool:
    return sys.platform == "win32"


# -- where to look ---------------------------------------------------------------------

def tool_dir(environ=None) -> str:
    """uv's tool directory, as uv itself picks it."""
    environ = os.environ if environ is None else environ
    given = environ.get("UV_TOOL_DIR")
    if given:
        return os.path.abspath(os.path.expanduser(given))
    if os.path.isfile(os.path.join(sys.prefix, "uv-receipt.toml")):
        return os.path.dirname(os.path.abspath(sys.prefix))
    if _windows():
        appdata = environ.get("APPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(appdata, "uv", "data", "tools")
    data = environ.get("XDG_DATA_HOME") or os.path.join(
        environ.get("HOME") or os.path.expanduser("~"), ".local", "share")
    return os.path.join(data, "uv", "tools")


def _user_site() -> str | None:
    """The user site directory, or None when Python has none."""
    import site

    try:
        return site.getusersitepackages()
    except Exception:  # noqa: BLE001 - an odd build without a user site
        return None


def _own_site() -> str:
    """The site directory this `moxi` is imported from."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _own_script() -> str | None:
    """The real path of this environment's `moxi` script, when it has one."""
    import sysconfig

    scripts = sysconfig.get_path("scripts")
    for name in ("moxi.exe", "moxi") if _windows() else ("moxi",):
        path = os.path.join(scripts, name)
        if os.path.isfile(path):
            return os.path.realpath(path)
    return None


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _site_dirs(env_root: str) -> list[str]:
    """The site-packages directories of the environment rooted at `env_root`."""
    import glob

    if _windows():
        found = [os.path.join(env_root, "Lib", "site-packages")]
    else:
        found = glob.glob(os.path.join(glob.escape(env_root), "lib", "python*",
                                       "site-packages"))
    return sorted(p for p in found if os.path.isdir(p))


def _modules_in(site_dir: str) -> list[tuple[str, str]]:
    """Each `(module, version)` whose `moxi_<module>-<version>.dist-info` is in `site_dir`."""
    from . import names

    out = []
    try:
        entries = sorted(os.listdir(site_dir))
    except OSError:
        return out
    for entry in entries:
        lowered = entry.lower()
        if not (lowered.startswith("moxi_") and lowered.endswith(".dist-info")):
            continue
        stem = entry[: -len(".dist-info")]
        dist, _, version = stem.partition("-")
        module = dist[len("moxi_"):].lower()
        if names.is_module_name(module) and os.path.isdir(os.path.join(site_dir, entry)):
            out.append((module, version))
    return out


def scan(environ=None) -> list[Stray]:
    """Every stray, found afresh (no cache)."""
    environ = os.environ if environ is None else environ
    own_site = _own_site()
    seen_sites: list[str] = [own_site]
    found: list[Stray] = []

    def take(site_dir: str, where: str, kind: str, tool: str | None) -> None:
        if any(_same(site_dir, s) for s in seen_sites):
            return
        seen_sites.append(site_dir)
        for module, version in _modules_in(site_dir):
            found.append(Stray(module, version, where, kind, tool))

    tools = tool_dir(environ)
    try:
        names_ = sorted(os.listdir(tools))
    except OSError:
        names_ = []
    for name in names_:
        root = os.path.join(tools, name)
        if not os.path.isdir(root) or _same(root, sys.prefix):
            continue
        for site_dir in _site_dirs(root):
            take(site_dir, root, "tool", name)

    user = _user_site()
    if user and os.path.isdir(user):
        take(user, user, "user", None)

    own_script = _own_script()
    exe = "moxi.exe" if _windows() else "moxi"
    for entry in (environ.get("PATH") or "").split(os.pathsep):
        if not entry:
            continue
        candidate = os.path.join(entry, exe)
        if not os.path.isfile(candidate):
            continue
        real = os.path.realpath(candidate)
        if own_script is not None and _same(real, own_script):
            continue
        root = os.path.dirname(os.path.dirname(real))
        if _same(root, sys.prefix):
            continue
        tool = None
        if os.path.isfile(os.path.join(root, "uv-receipt.toml")):
            tool = os.path.basename(root)
        for site_dir in _site_dirs(root):
            take(site_dir, root, "path", tool)
    return found


# -- the cache and the told record ------------------------------------------------------

def _mtime(path: str | None) -> int | None:
    if not path:
        return None
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def cache_key(environ=None) -> list:
    """What the cached answer depends on."""
    import hashlib

    environ = os.environ if environ is None else environ
    path_hash = hashlib.sha256((environ.get("PATH") or "").encode("utf-8",
                                                                 "surrogatepass")).hexdigest()
    return [_mtime(tool_dir(environ)), _mtime(_user_site()), path_hash[:16],
            os.path.abspath(sys.prefix)]


def record_path(environ=None):
    from . import paths

    return paths.core().state_dir(environ) / RECORD


def _read(path) -> dict:
    import json

    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def find(environ=None, *, write: bool = True) -> list[Stray]:
    """The strays, from the cache when its key is unchanged, else scanned (and, with
    `write`, cached)."""
    path = record_path(environ)
    key = cache_key(environ)
    held = _read(path)
    if held.get("key") == key and isinstance(held.get("strays"), list):
        try:
            return [Stray.from_json(s) for s in held["strays"]]
        except (KeyError, TypeError):
            pass
    strays = scan(environ)
    if write:
        _write(path, key=key, strays=strays)
    return strays


def _write(path, *, key=None, strays=None, told=None) -> None:
    import json

    from . import files

    def change(text):
        try:
            data = json.loads(text) if text else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if key is not None:
            data["key"] = key
            data["strays"] = [s.as_json() for s in strays]
        if told is not None:
            data["told"] = sorted(told)
        return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")

    files.update(path, change)


def tell(stream=None, environ=None) -> list[Stray]:
    """Print one line for each stray not told before, and record it as told. Returns the
    strays told now. A stray that goes away is dropped from the record, so it is told
    again if it comes back."""
    stream = sys.stderr if stream is None else stream
    strays = find(environ)
    path = record_path(environ)
    held = _read(path)
    told_before = set(held.get("told") or ()) if isinstance(held.get("told"), list) else set()
    now = {s.key for s in strays}
    new = [s for s in strays if s.key not in told_before]
    for stray in new:
        print(stray.line(), file=stream)
    if new or (told_before - now):
        _write(path, told=(told_before & now) | {s.key for s in new})
    return new


def copy_of(module: str, environ=None) -> Stray | None:
    """A stray copy of `module`, for an absent-module message. Writes nothing."""
    for stray in find(environ, write=False):
        if stray.module == module:
            return stray
    return None


__all__ = ["Stray", "scan", "find", "tell", "copy_of", "tool_dir", "cache_key",
           "record_path", "RECORD"]
