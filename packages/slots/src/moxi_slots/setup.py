"""slots' `SETUP`, run at every `moxi install slots`: record tmux's absolute path; and its
`TEARDOWN`, run before an uninstall removes anything.

The rule: no slots process looks tmux up on a PATH at run time; the server, every client
call and the doctor use the one absolute path recorded here.

What the `tmux` setting holds decides what is looked up:

- a bare name (the default `tmux`): looked up on the installing user's PATH;
- an absolute path this step wrote before: its file name is looked up again, so a moved
  binary (a Homebrew upgrade moves its Cellar) is found again;
- an absolute path the user set: kept as it is.

The path is written as found on PATH, never its symlink target, so Homebrew's stable link
stays valid across upgrades. Then `<tmux> -V` must be 3.2 or newer.

With tmux absent, not runnable, or below 3.2, the step prints what it found and exits 3,
writing nothing; the install then writes no unit. On a system slots does not run on
(native Windows) the step says so and exits 0: the package installs there, and the
core's writer skips the daemon.

The teardown refuses (`moxi.modules.Refused`, naming the slots) while any slot is
occupied or not idle, since the uninstall's unit removal stops the server and that ends
every session in every slot. The uninstall then removes nothing. With no server
answering, nothing runs in a slot and the teardown accepts.
"""

from __future__ import annotations

import os
import shutil

from moxi import exit as codes

from . import support, tmux
from .places import MODULE
from .settings import REINSTALL, SETUP_BY

#: How tmux is installed, by system.
INSTALL = {support.MACOS: "brew install tmux (or port install tmux)",
           support.LINUX: "your package manager: apt install tmux, dnf install tmux, ...",
           support.WSL2: "your distribution's package: apt install tmux, dnf install "
                         "tmux, ..."}


def install_hint(kind: str | None = None) -> str:
    """How to install tmux here."""
    return INSTALL.get(support.system() if kind is None else kind, "your package manager")


def setup_wrote() -> bool:
    """Whether the `tmux` setting's current value was written by this step: the last
    change of the key in the core's settings history is this step's."""
    from moxi import config

    try:
        changes = config.history(MODULE, "tmux", tail=1)
    except Exception:
        return False
    return bool(changes) and changes[-1].get("by") == SETUP_BY \
        and changes[-1].get("op") == "set"


def resolve(value: str, path_env: str | None) -> tuple[str | None, str]:
    """The absolute path to record for setting `value`, and where it came from; None
    with the reason when there is none."""
    if os.path.isabs(value) and not setup_wrote():
        if os.path.isfile(value) and os.access(value, os.X_OK):
            return value, "kept as set"
        return None, (f"the tmux setting names {value}, which is not an executable file; "
                      f"set another with `moxi config set {MODULE} tmux PATH`, or "
                      f"`moxi config unset {MODULE} tmux` to look tmux up on PATH")
    name = os.path.basename(value) if os.path.isabs(value) else value
    found = shutil.which(name, path=path_env)
    if found is None:
        return None, (f"{name} is not on PATH; install tmux ({install_hint()}), then run "
                      f"`{REINSTALL}` again")
    return os.path.abspath(found), "found on PATH"


def setup() -> int:
    """Record tmux's absolute path; 0 when recorded, 3 when tmux is missing or too old."""
    from moxi import config

    from .settings import current

    kind = support.system()
    if not support.system_ok(kind):
        print(f"{MODULE}: {support.unsupported_words(kind)}; nothing to set up")
        return codes.OK
    try:
        value = current()["tmux"]
    except config.ConfigError as exc:
        print(f"{MODULE}: {exc}")
        return codes.ERROR
    path, how = resolve(value, os.environ.get("PATH"))
    if path is None:
        print(f"{MODULE}: {how}")
        return codes.REFUSED
    found = tmux.version(path)
    if not found.ok:
        print(f"{MODULE}: {path} is {found.text}; slots needs tmux {tmux.FLOOR_TEXT} or "
              f"newer (the first with -D). Install a newer tmux ({install_hint(kind)}), "
              f"then run `{REINSTALL}` again")
        return codes.REFUSED
    if path != value:
        config.set(MODULE, "tmux", path, by=SETUP_BY)
    print(f"{MODULE}: tmux is {path} ({found.text}, {how})")
    return codes.OK


def teardown() -> int:
    """Accept the uninstall (0) unless a slot is occupied or not idle; then raise
    `moxi.modules.Refused` naming them. A server that can't be read is refused too."""
    from moxi.modules import Refused

    from .errors import SlotsError
    from .settings import NotSetUp
    from .slots import busy

    if not support.system_ok():
        return codes.OK
    try:
        if tmux.ping() is None:
            return codes.OK
        lines = busy()
    except NotSetUp:
        return codes.OK
    except SlotsError as exc:
        raise Refused(f"the slots could not be read ({exc}), so the server is left "
                      "running; try again, or remove the slots first") from None
    if lines:
        raise Refused("; ".join(lines) + ". Uninstalling stops the server, which ends "
                      "every slot's session. End the launches through their sessions; for a "
                      "slot that is only not idle, close what you opened there or `moxi "
                      "slots remove N --force` it")
    return codes.OK


__all__ = ["INSTALL", "install_hint", "setup_wrote", "resolve", "setup", "teardown"]
