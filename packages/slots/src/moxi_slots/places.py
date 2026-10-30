"""Where slots keeps its files, each read from `moxi.paths` at the call, so a process that
changes `MOXI_HOME` reaches another server and another registry.

Runtime directory (0700, made by the server wrapper):
    tmux.sock      the server's socket (or a hashed path, see `moxi.paths.socket_path`)
    server.conf    the config the wrapper writes before it starts tmux
    server.pid     the pid the wrapper wrote before it became tmux
    server.lock    held while a caller starts the server on demand
    launch/        one private file per launch, read and emptied by the trampoline

State directory:
    slots.json     the registry
    slots.lock     the module lock around every change
    mismatches/    the regions of screens that did not match, the last 20
    server.log     the output of a server started on demand
"""

from __future__ import annotations

from pathlib import Path

from moxi import paths

MODULE = "slots"
#: The socket's name, as `moxi.paths.socket_path` takes it.
SOCKET_NAME = "tmux"


def layout() -> paths.Layout:
    return paths.Layout(MODULE)


def runtime_dir(*, create: bool = False) -> Path:
    """The runtime directory; `create=True` makes it 0700 and checks its owner."""
    return layout().runtime_dir(create=create)


def state_dir() -> Path:
    return layout().state_dir()


def socket() -> str:
    """The one socket every tmux call names with `-S`."""
    return paths.socket_path(MODULE, SOCKET_NAME)


def server_conf() -> Path:
    return runtime_dir() / "server.conf"


def server_pid() -> Path:
    return runtime_dir() / "server.pid"


def server_lock() -> Path:
    return runtime_dir() / "server.lock"


def launch_dir() -> Path:
    return runtime_dir() / "launch"


def registry() -> Path:
    return state_dir() / "slots.json"


def module_lock() -> Path:
    return state_dir() / "slots.lock"


def mismatches_dir() -> Path:
    return state_dir() / "mismatches"


def server_log() -> Path:
    return state_dir() / "server.log"


__all__ = ["MODULE", "SOCKET_NAME", "layout", "runtime_dir", "state_dir", "socket",
           "server_conf", "server_pid", "server_lock", "launch_dir", "registry",
           "module_lock", "mismatches_dir", "server_log"]
