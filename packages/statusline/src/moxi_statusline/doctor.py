"""statusline's doctor rows, which `moxi doctor` shows for the module.

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
    out.append(bytecode_row(items))
    out.append(sample_row())
    out.append(show_row(items))
    out.append(names_row())
    return out


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


def show_row(items: list[str]) -> Row:
    missing = []
    for item in items:
        found = modules.announced(item)
        if found.state != modules.PRESENT:
            missing.append(f"{item} (not installed)")
        elif not found.segment:
            missing.append(f"{item} (no status line segment)")
    if not missing:
        return Row(MODULE, "show", "ok", "every module item in show has a segment")
    return Row(MODULE, "show", "warn",
               f"these items show nothing: {', '.join(missing)}",
               "moxi config statusline set show ...")


def names_row() -> Row:
    clash = sorted({entry.name for entry in modules.installed() if entry.name in RESERVED})
    if not clash:
        return Row(MODULE, "module names", "ok", "no installed module is named like a "
                   "base field")
    return Row(MODULE, "module names", "warn",
               f"installed modules named like a base field, which the line shows in their "
               f"place: {', '.join(clash)}")
