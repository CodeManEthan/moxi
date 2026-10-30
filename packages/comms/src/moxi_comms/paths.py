"""comms' locations: its own five directories under Moxi's roots (`moxi.paths`).

    DATA/comms      ~/.local/share/moxi/comms/            MOXI_COMMS_DATA_HOME
    STATE/comms     ~/.local/state/moxi/comms/            MOXI_COMMS_STATE_HOME
    CACHE/comms     ~/.cache/moxi/comms/                  MOXI_COMMS_CACHE_HOME
    CONFIG/comms    ~/.config/moxi/comms/                 MOXI_COMMS_CONFIG_HOME
    RUNTIME/comms   $XDG_RUNTIME_DIR/moxi/comms/          MOXI_COMMS_RUNTIME_DIR

`MOXI_COMMS_HOME` collapses all five under one directory, and `MOXI_HOME` all of every
module's (`moxi.paths.Layout`); `MOXI_COMMS_DEVICE` labels the device and moves no root.
comms writes only under these five; its sockets live in RUNTIME/comms, which
`runtime_root()` names. Only comms-specific subpaths live here.

Every function takes an optional `env` mapping and reads the process environment
without one.

`refuse_outside_runtime` is the guard on a socket path handed in from the environment:
a claim or daemon socket outside RUNTIME/comms is refused, naming the variable and the
directory, so a stamp inherited from another program's session cannot point comms at
that program's sockets.
"""

from __future__ import annotations

import os
from pathlib import Path

from moxi.paths import Layout

from .release import MODULE

#: comms' layout: the core's roots, comms' prefix `MOXI_COMMS_`.
LAYOUT = Layout(MODULE)


def data_dir(env=None) -> Path:
    """DATA/comms."""
    return LAYOUT.data_dir(env)


def state_dir(env=None) -> Path:
    """STATE/comms, this machine's own."""
    return LAYOUT.state_dir(env)


def cache_dir(env=None) -> Path:
    """CACHE/comms, this machine's own."""
    return LAYOUT.cache_dir(env)


def config_dir(env=None) -> Path:
    """CONFIG/comms, which holds `config.toml`."""
    return LAYOUT.config_dir(env)


def runtime_dir(env=None) -> Path:
    """RUNTIME/comms: every socket comms makes, and nothing that outlives a reboot."""
    return LAYOUT.runtime_dir(env)


def runtime_root(env=None) -> Path:
    """comms' runtime root, RUNTIME/comms (`runtime_dir`): the directory every comms
    socket is made in, and the one `refuse_outside_runtime` holds a socket path to."""
    return LAYOUT.runtime_dir(env)


class OutsideRuntime(ValueError):
    """A socket path from the environment that is not under RUNTIME/comms."""


def refuse_outside_runtime(path, variable: str, env=None) -> Path:
    """`path` when it lies under RUNTIME/comms, else raise `OutsideRuntime` naming
    `variable` and the directory.

    The check reads both paths with their links resolved, so a link inside RUNTIME/comms
    that points elsewhere is refused too.
    """
    root = runtime_dir(env)
    given = Path(path)
    real = Path(os.path.realpath(given))
    real_root = Path(os.path.realpath(root))
    if real != real_root and real_root in real.parents:
        return given
    raise OutsideRuntime(
        f"{variable} names {given}, which is outside comms' runtime directory {root}; "
        f"comms uses only sockets under {root}. Start the session from `moxi comms` "
        f"or unset {variable}.")


def comms_db(env=None) -> Path:
    """STATE/comms/comms.db -- comms' one store (`store/`): the record, the delivery
    books, the binding history, the directory and the reserved names.

    STATE and deliberately not DATA: DATA is the sync surface, and a live database is
    never continuous-file-synced. Open it only through `store.open_write` or
    `store.open_read`.
    """
    return state_dir(env) / "comms.db"


def directory_db(env=None) -> Path:
    """DATA/comms/directory.db -- comms 0.2's directory, which comms.db replaces. Read
    only by the move of 0.2's files into comms.db."""
    return data_dir(env) / "directory.db"


def bindings_db(env=None) -> Path:
    """DATA/comms/bindings.db -- comms 0.2's binding history, which comms.db replaces.
    Read only by the move of 0.2's files into comms.db."""
    return data_dir(env) / "bindings.db"


def cards_dir(env=None) -> Path:
    """DATA/comms/cards -- holder-authored peer-facing agent cards, one per identity.

    DATA and deliberately not STATE: a card is durable user data and device-agnostic --
    who someone IS does not depend on which box is asking. It is not derived (nothing can regenerate a card; only
    its holder can write one), so it is not CACHE; it is content, not config; and it
    outlives a login, so it is not RUNTIME. comms creates nothing here: the reader is
    read-only and the first holder to write a card makes the directory.
    """
    return data_dir(env) / "cards"


def cursors_dir(env=None) -> Path:
    """STATE/comms/cursors: comms 0.2's cursor files, which the move into comms.db
    reads. Nothing writes there now."""
    return state_dir(env) / "cursors"


def tokens_dir(env=None) -> Path:
    """STATE/comms/tokens -- the per-holder token store.

    STATE and deliberately not DATA: a stored credential is a DEVICE credential, and DATA
    is THE sync surface, so a token there would ride a future sync to boxes it was never
    minted for. CONFIG holds no tokens at all. `[secrets] store` in config.toml (or
    `MOXI_COMMS_TOKEN_STORE`) overrides this path -- `MOXI_COMMS_HOME` collapses every
    root, so an alternate-home recipe needs a way to still name the real store.
    """
    return state_dir(env) / "tokens"


def record_db(env=None) -> Path:
    """STATE/comms/record.db -- comms 0.2's local record, which comms.db replaces. Read
    only by the move of 0.2's files into comms.db."""
    return state_dir(env) / "record.db"


def daemon_socket(env=None) -> Path:
    """The daemon's socket, `moxi.ipc.address("comms", "daemon")`: on POSIX
    RUNTIME/comms/daemon.sock, or the core's short hashed path when that is too long.
    With `env` given, RUNTIME/comms/daemon.sock under that environment's roots."""
    if env is None:
        from moxi import ipc

        return Path(ipc.address(MODULE, "daemon"))
    return runtime_dir(env) / "daemon.sock"


def old_daemon_socket(env=None) -> Path:
    """RUNTIME/comms/moxi-comms-daemon.sock -- comms 0.2's daemon socket. This build
    never binds, removes or renames it; the move of 0.2's files only connects to it, to
    see that no 0.2 daemon still answers there."""
    return runtime_dir(env) / "moxi-comms-daemon.sock"


def codex_threads_dir(env=None) -> Path:
    """STATE/comms/codex -- thread-id <-> identity: the thread of each identity's
    current launch."""
    return state_dir(env) / "codex"


def opencode_runtime_dir(env=None) -> Path:
    """RUNTIME/comms/opencode -- what an `opencode` wrapper writes for its own server's launch:
    the layer-one instructions file the server reads by absolute path."""
    return runtime_dir(env) / "opencode"


def opencode_sessions_dir(env=None) -> Path:
    """STATE/comms/opencode -- session-id <-> identity for the `opencode` adapter, and
    the wrapper's log and status lines.

    The same category as `codex_threads_dir` (the adapter writes it, the daemon never
    does). It holds the session of the identity's current launch: each launch creates
    a new session and never loads one an earlier launch recorded. The status lines
    carry across launches.
    """
    return state_dir(env) / "opencode"


def log_dir(env=None) -> Path:
    """STATE/comms/logs -- the daemon's and the wrappers' logs."""
    return state_dir(env) / "logs"
