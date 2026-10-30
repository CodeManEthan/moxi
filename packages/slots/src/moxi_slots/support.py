"""Whether slots runs here: `supported()`, and which system this is.

slots runs on Linux, macOS and WSL2 (which is Linux). On native Windows the package
installs and its announcement and command load, but `supported()` is false, every
command says where slots runs and exits 3, and the core's writer skips the daemon. A
launcher that finds `supported()` false treats slots as absent: it still launches
in-terminal and headless.

WSL2 is told apart from other Linux by `platform.uname().release`, never by reading
`/proc`.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass

#: Where slots runs, said the same way everywhere.
WHERE = "slots runs on Linux, macOS and WSL2"
#: What a launcher still does where slots does not run.
WITHOUT = "the launcher still launches in-terminal and headless"

LINUX = "linux"
WSL2 = "wsl2"
MACOS = "macos"
WINDOWS = "windows"


@dataclass(frozen=True)
class Support:
    """`ok` is whether slots can be used here; `why` says why not ("" when it can)."""

    ok: bool
    why: str = ""


def system(platform_name: str | None = None, release: str | None = None) -> str:
    """`linux`, `wsl2`, `macos`, `windows`, or the platform's own name for any other."""
    name = sys.platform if platform_name is None else platform_name
    if name.startswith("linux"):
        release = platform.uname().release if release is None else release
        return WSL2 if "microsoft" in release.lower() else LINUX
    if name == "darwin":
        return MACOS
    if name in ("win32", "cygwin"):
        return WINDOWS
    return name


def system_ok(kind: str | None = None) -> bool:
    """Whether slots runs on this kind of system at all."""
    return (system() if kind is None else kind) in (LINUX, WSL2, MACOS)


def label(kind: str) -> str:
    return {LINUX: "Linux", WSL2: "WSL2", MACOS: "macOS", WINDOWS: "native Windows"}.get(
        kind, kind)


def supported() -> Support:
    """Whether slots can be used here: a system it runs on, and a recorded tmux.

    It runs no program: the tmux check is that the recorded path names an executable
    file (`SETUP` checked its version when it recorded it).
    """
    kind = system()
    if not system_ok(kind):
        return Support(False, f"{WHERE}, not {label(kind)}; {WITHOUT}")
    from .settings import NotSetUp, tmux_path

    try:
        tmux_path()
    except NotSetUp as exc:
        return Support(False, str(exc))
    return Support(True)


def unsupported_words(kind: str | None = None) -> str:
    """The sentence every command prints on a system slots does not run on."""
    return f"{WHERE}, not {label(system() if kind is None else kind)}; {WITHOUT}"


__all__ = ["WHERE", "WITHOUT", "LINUX", "WSL2", "MACOS", "WINDOWS", "Support", "system",
           "system_ok", "label", "supported", "unsupported_words"]
