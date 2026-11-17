"""statusline's doctor rows, which `moxi doctor` shows for the module.

- `command`: the command of Claude Code's `statusLine` resolves to an executable. Moxi's
  line must name a render that is there (a `fail` when it isn't: an upgrade or a moved
  environment left the setting behind; `moxi statusline use` points it at this one). A
  user's own line is checked by its first word, a path or a command on PATH, and a `warn`
  when it doesn't resolve. No line, or a settings file that can't be read, is said and
  is no failure of statusline's.
- `bytecode`: each module a render of the configured line loads (`RENDER_IMPORTS`, and
  each shown segment's announcement and closure) has a current `.pyc`. Without one, every
  render compiles it again in memory. A `warn`; `moxi install statusline` compiles them.
- `sample render`: one render of the sample input by the real command, timed against the
  module's `BUDGETS` `hook_ms`, a multiple of the start time of `python -I -c pass` here.
  A `warn` above it.
- `show`: each module item of `show` has a segment behind it: the module is present and
  announces a `SEGMENT`. A `warn` naming the items that show nothing.
- `module names`: no installed module is named like a base field, which the line would
  show in its place. A `warn` naming each.
"""

from moxi import modules
from moxi.config import ConfigError
from moxi.doctor import Row

from . import module as announcement
from .fields import RESERVED

MODULE = "statusline"
INSTALL = "moxi install statusline"


def rows() -> list[Row]:
    """Every row, in the order above."""
    from . import settings

    out = []
    try:
        show = list(settings.load()["show"])
    except ConfigError as exc:
        show = list(settings.schema().keys["show"].default)
        out.append(Row(MODULE, "config", "warn",
                       f"{exc}; the rows below read the default show",
                       "moxi config statusline check"))
    items = [item for item in show if item not in RESERVED]
    out.insert(0, command_row())
    out.append(bytecode_row(items))
    out.append(sample_row())
    out.append(show_row(items))
    out.append(names_row())
    return out


def command_row() -> Row:
    import os
    import shlex

    from moxi import paths
    from moxi.harnessfile import Refused

    from . import switch

    try:
        doc = switch.read()
    except Refused as exc:
        return Row(MODULE, "command", "warn", f"{exc}; its status line can't be checked")
    current = doc.data.get(switch.KEY)
    if current is None:
        return Row(MODULE, "command", "ok", "Claude Code sets no status line",
                   "moxi statusline use")
    command = switch.command_of(current)
    if command is None:
        return Row(MODULE, "command", "warn",
                   f"Claude Code's statusLine in {doc.path} has no command")
    path = switch.moxis_path(current)
    if path is not None:
        if not (os.path.isfile(path) and os.access(path, os.X_OK)):
            return Row(MODULE, "command", "fail",
                       f"Claude Code's status line runs {path}, which is not an "
                       f"executable here", "moxi statusline use")
        try:
            here = switch.render_path()
        except FileNotFoundError:
            here = None
        if here is not None and os.path.realpath(here) != os.path.realpath(path):
            return Row(MODULE, "command", "warn",
                       f"Claude Code's status line runs another environment's render, "
                       f"{path}", "moxi statusline use")
        return Row(MODULE, "command", "ok", f"Moxi's line, {path}")
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    first = words[0] if words else command
    if os.sep in first or (os.altsep and os.altsep in first):
        found = os.path.expanduser(first)
        found = found if os.path.isfile(found) and os.access(found, os.X_OK) else None
    else:
        found = paths.which(first)
    if found is None:
        return Row(MODULE, "command", "warn",
                   f"Claude Code's status line is your own, and its command's first word, "
                   f"{first}, doesn't resolve to an executable")
    return Row(MODULE, "command", "ok", f"your own line; {first} resolves to {found}")


def bytecode_row(items: list[str]) -> Row:
    from .compile import current, render_sources, segment_sources
    from .render import RENDER_IMPORTS

    sources = render_sources(RENDER_IMPORTS)
    for item in items:
        for path in segment_sources(item):
            sources.setdefault(path, path)
    stale = [name for name, path in sources.items() if not current(path)]
    if not stale:
        return Row(MODULE, "bytecode", "ok",
                   f"current for the {len(sources)} modules a render loads")
    shown = ", ".join(stale[:5]) + (f" and {len(stale) - 5} more" if len(stale) > 5 else "")
    return Row(MODULE, "bytecode", "warn",
               f"no current .pyc for {len(stale)} of the {len(sources)} modules a render "
               f"loads ({shown}), so each render compiles them again", INSTALL)


def sample_row(floor=None) -> Row:
    """One render of the sample input, timed against the budget. `floor` is the start
    time of `python -I -c pass` in ms (default: the doctor's measure)."""
    from . import sample
    from .preview import PreviewError, render

    try:
        done = render(sample.payload())
    except PreviewError as exc:
        return Row(MODULE, "sample render", "fail", str(exc), INSTALL)
    if floor is None:
        from moxi.doctor import floor_ms

        floor = floor_ms()
    multiple = announcement.BUDGETS["hook_ms"]
    budget = multiple * floor
    detail = (f"{done.ms:.1f} ms; the budget is {budget:.1f} ms ({multiple:g} times the "
              f"{floor:.1f} ms start of python -I -c pass)")
    if done.ms > budget:
        return Row(MODULE, "sample render", "warn",
                   detail + "; a busy machine runs it slower, so check again when it is "
                   "quiet", "moxi statusline preview")
    return Row(MODULE, "sample render", "ok", detail)


def nothing_behind(items: list[str]) -> list[str]:
    """Each module item of `items` that shows nothing, with why: `cache (not
    installed)`, `alpha (no status line segment)`."""
    missing = []
    for item in items:
        if item in RESERVED:
            continue
        found = modules.announced(item)
        if found.state != modules.PRESENT:
            missing.append(f"{item} (not installed)")
        elif not found.segment:
            missing.append(f"{item} (no status line segment)")
    return missing


def clashes() -> list[str]:
    """The installed modules named like a base field."""
    return sorted({entry.name for entry in modules.installed() if entry.name in RESERVED})


def show_row(items: list[str]) -> Row:
    missing = nothing_behind(items)
    if not missing:
        return Row(MODULE, "show", "ok", "every module item in show has a segment")
    return Row(MODULE, "show", "warn",
               f"these items show nothing: {', '.join(missing)}",
               "moxi config statusline set show ...")


def names_row() -> Row:
    clash = clashes()
    if not clash:
        return Row(MODULE, "module names", "ok", "no installed module is named like a "
                   "base field")
    return Row(MODULE, "module names", "warn",
               f"installed modules named like a base field, which the line shows in their "
               f"place: {', '.join(clash)}")
