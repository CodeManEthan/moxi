"""The functions behind notes' turns listeners: the start line and the snapshot trigger.

turns imports this module in a listener thread of the session's endpoint, at a function's
first ask. It imports nothing of repo: a pass runs in a process of its own.

`start(event)` answers a `start` with "Notes directory: <path>", or None when the session
has no notes directory. The session's folder is `event.cwd` when the event carries one
that names an existing folder by an absolute path, else `PWD` from the session's
environment under the same test, else this process's working directory. The override is
`MOXI_NOTES_DIR` from the session's environment, never this process's own.

`fire(event)` runs at every `turn` and `gone`. It does nothing while `snapshot.session_end` is
off or the notes root holds no `.git`. A `turn` is skipped when another `turn` started a
pass less than a minute ago (the runtime file `fired` keeps the time); a `gone` is never
skipped, since it is the last chance to see the session's edits. Then it starts
`moxi-notes-snapshot --settle` detached and returns: no turn and no session end waits on a
pass.
"""

from __future__ import annotations

import os

#: The variable the launcher sets for one launch, read from the session's environment.
OVERRIDE = "MOXI_NOTES_DIR"
#: The start line's words before the path.
LINE = "Notes directory: "
#: A `turn` within this many seconds of the last fired one starts no pass.
DEBOUNCE = 60.0
#: The runtime file whose time is the last fired `turn`.
FIRED = "fired"
#: The console script a fired pass runs.
SCRIPT = "moxi-notes-snapshot"


def _folder(value) -> str | None:
    if not isinstance(value, str) or not value or "\0" in value:
        return None
    if not os.path.isabs(value) or not os.path.isdir(value):
        return None
    return os.path.normpath(value)


def session_path(event) -> str:
    """The session's folder: `event.cwd`, then `PWD` from its environment, then this
    process's working directory."""
    env = getattr(event, "env", None) or {}
    return (_folder(getattr(event, "cwd", None)) or _folder(env.get("PWD"))
            or os.getcwd())


def start(event) -> str | None:
    """The start line for `event`'s session, or None."""
    from .resolve import notes_dir

    env = getattr(event, "env", None) or {}
    found = notes_dir(session_path(event), override=env.get(OVERRIDE) or None)
    if found.dir is None:
        return None
    line = f"{LINE}{found.dir}"
    if found.warning:
        line += f" ({found.warning})"
    return line


def fire(event) -> None:
    """Start one detached snapshot pass for a `turn` or `gone` event."""
    import time

    from . import settings

    places = settings.read()
    if not places.settings["snapshot.session_end"]:
        return None
    if not os.path.lexists(places.root / ".git"):
        return None
    if getattr(event, "event", None) == "turn":
        from moxi import files, paths

        marker = paths.Layout(settings.MODULE).runtime_dir(create=True) / FIRED
        try:
            age = time.time() - marker.stat().st_mtime
        except OSError:
            age = None
        if age is not None and 0 <= age < DEBOUNCE:
            return None
        files.atomic_write(marker, b"")
    _spawn(places.root)
    return None


def _spawn(root) -> int:
    import moxi.paths
    import moxi.proc

    return moxi.proc.spawn([str(moxi.paths.script(SCRIPT)), "--settle"], detached=True,
                           output=os.devnull, cwd=str(root))


__all__ = ["OVERRIDE", "LINE", "DEBOUNCE", "session_path", "start", "fire"]
