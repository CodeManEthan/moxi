"""statusline's config: its schema, the rules the schema can't say, and the check that
holds a config file to both.

`check(text)` is the announcement's `CONFIG_CHECK`: the schema's reader over the text,
then `refusal`. `moxi config set` runs it before it writes, so a file it writes always
renders. The render runs `refusal` over the file it reads too, since a file edited by
hand is read without that check.

The rules: each `show` item is a base field, `row`, or spelled as a module name; at most
one `row`, and never first or last, so a line has one or two rows; `context.warn_at` not
above `context.alert_at`; `dir.parts` at least 1. Which modules are installed is not a
rule here: an item whose module is absent shows nothing, and the doctor names it.
"""

import os

from moxi import config, names

from .fields import RESERVED, ROW

MODULE = "statusline"
SCHEMA_FILE = os.path.join(os.path.dirname(__file__), "settings.toml")

_schema: list = []


def schema() -> config.Schema:
    """The module's schema, read once per process from the file beside this module."""
    if not _schema:
        _schema.append(config.Schema.from_path(SCHEMA_FILE))
    return _schema[0]


def check(text: str) -> None:
    """The whole check of a config file's text. Raises `moxi.config.ConfigError`."""
    settings = config.check_text(MODULE, schema(), text)
    problem = refusal(settings)
    if problem is not None:
        raise problem


def load(path: str | None = None) -> config.Settings:
    """The settings from the module's config file, or from `path` in its place, held to
    the same rules. Raises `moxi.config.ConfigError`."""
    settings = config.load(MODULE, schema(), path=path)
    problem = refusal(settings)
    if problem is not None:
        raise problem
    return settings


def refusal(settings: config.Settings) -> config.ConfigError | None:
    """The error for the first rule `settings` breaks, or None."""
    file = settings.file
    found = show_problem(settings["show"])
    if found is not None:
        problem, fix = found
        return config.ConfigError(file, "show", problem, fix)
    warn_at, alert_at = settings["context.warn_at"], settings["context.alert_at"]
    if warn_at > alert_at:
        return config.ConfigError(file, "context.warn_at",
                                  f"is {warn_at}, above context.alert_at ({alert_at})",
                                  "set it at or below context.alert_at")
    if settings["dir.parts"] < 1:
        return config.ConfigError(file, "dir.parts", f"is {settings['dir.parts']}",
                                  "set it to 1 or more")
    return None


def show_problem(items) -> tuple[str, str] | None:
    """`(problem, fix)` for the first rule a list of `show` items breaks, or None."""
    for item in items:
        if item not in RESERVED and not names.is_module_name(item):
            return (f"holds {item!r}, which is neither a base field nor a module name",
                    f"use base fields ({', '.join(RESERVED)}) or installed modules' names")
    rows = [i for i, item in enumerate(items) if item == ROW]
    if len(rows) > 1:
        return (f"holds {len(rows)} {ROW!r} items; a line has at most two rows",
                f"keep one {ROW!r}")
    if rows and rows[0] in (0, len(items) - 1):
        where = "first" if rows[0] == 0 else "last"
        return (f"has {ROW!r} {where}, which would leave a row empty",
                f"put {ROW!r} between two items")
    return None
