"""What `moxi statusline status` reports: Claude Code's line, the line kept to put back,
the items of `show` and which show nothing, modules named like a base field, and a
project's own `statusLine` in the current folder, which Claude Code uses there.

It reads; it writes nothing.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from moxi.config import ConfigError

from . import switch


def report(folder: str | os.PathLike | None = None,
           env: Mapping[str, str] | None = None) -> dict:
    """The status as plain data: `switch.state`'s keys, then `show` (the config's items),
    `config_error` (why the config can't be read, the default items then shown),
    `nothing_behind`, `clashes` and `project` (`{path, command}` for each project file
    in `folder`, default the working folder, that sets its own line)."""
    from . import doctor, settings

    out = switch.state(env)
    try:
        show = list(settings.load()["show"])
        out["config_error"] = None
    except ConfigError as exc:
        show = list(settings.schema().keys["show"].default)
        out["config_error"] = str(exc)
    out["show"] = show
    out["nothing_behind"] = doctor.nothing_behind(show)
    out["clashes"] = doctor.clashes()
    folder = os.getcwd() if folder is None else folder
    out["project"] = [{"path": path, "command": command}
                      for path, command in switch.project_lines(folder, env)]
    return out


def lines(data: dict) -> list[str]:
    """`report`'s data as the lines the verb prints."""
    out = []
    if data["error"] is not None:
        out.append(f"Claude Code's status line: unknown, {data['error']}")
    elif data["moxis"]:
        where = ("this environment's render" if data["here"]
                 else "another environment's render; moxi statusline use points it here")
        out.append(f"Claude Code's status line: Moxi's, {where}")
        out.append(f"  command: {data['command']}")
    elif data["command"] is not None:
        out.append("Claude Code's status line: your own")
        out.append(f"  command: {data['command']}")
    elif data["set"]:
        out.append("Claude Code's status line: set, with no command")
    else:
        out.append("Claude Code's status line: none")
    out.append(f"  settings: {data['settings']}")
    saved = data["saved"]
    if data["saved_error"] is not None:
        out.append(f"Saved line: unknown, {data['saved_error']}")
    elif saved is None:
        out.append("Saved line: none kept")
    else:
        kept = switch.Saved(saved["had"], saved.get(switch.KEY), saved.get("saved_at"))
        when = f" (kept {kept.at})" if kept.at else ""
        out.append(f"Saved line: {kept.describe()}{when}")
    if data["config_error"] is not None:
        out.append(f"Config: {data['config_error']}; the default items are shown")
    out.append(f"Shows: {', '.join(data['show'])}")
    if data["nothing_behind"]:
        out.append(f"Shows nothing: {', '.join(data['nothing_behind'])}")
    if data["clashes"]:
        out.append(f"Modules named like a base field, shown as the field: "
                   f"{', '.join(data['clashes'])}")
    for entry in data["project"]:
        runs = f" ({entry['command']})" if entry["command"] else ""
        out.append(f"This folder's {entry['path']} sets its own status line{runs}, which "
                   f"Claude Code uses here")
    return out
