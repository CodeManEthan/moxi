"""publishing's config, read through `moxi.config` at each use.

Keys: `provenance.rules`, the provenance rules that run; `push.word_pattern`, the shape a
push word must match in full; `defaults.drop_trailers`, `defaults.main` and
`defaults.word_required`, which seed a repo's piece settings when `enable` first
registers a piece there; and `defaults.refused_trailers`, the keys clean history adds to
the repo's commit-rules piece. Every value scoped to one repo lives in that repo's git
config.
"""

from __future__ import annotations

from moxi.config import ConfigError, Schema

MODULE = "publishing"
SCHEMA_RESOURCE = "settings.toml"
RULES = "provenance.rules"
WORD_PATTERN = "push.word_pattern"
DROP_TRAILERS = "defaults.drop_trailers"
REFUSED_TRAILERS = "defaults.refused_trailers"
MAIN = "defaults.main"
WORD_REQUIRED = "defaults.word_required"

_schema: Schema | None = None


def schema() -> Schema:
    """The module's schema, parsed once per process."""
    global _schema
    if _schema is None:
        _schema = Schema.from_resource(__package__, SCHEMA_RESOURCE)
    return _schema


def load():
    """publishing's settings as they stand; raises `moxi.config.ConfigError`."""
    from moxi import config

    settings = config.load(MODULE, schema())
    _check(settings)
    return settings


def check_text(text: str) -> None:
    """The module's whole loader over a config text: the schema's check, then what the
    schema can't say: every name in `provenance.rules` is a rule, and `push.word_pattern`
    compiles. Raises `ConfigError` when it refuses."""
    from moxi import config

    _check(config.check_text(MODULE, schema(), text))


def _check(settings) -> None:
    import re

    from moxi_publishing.rules import NAMES

    for name in settings[RULES]:
        if name not in NAMES:
            raise ConfigError(settings.file, RULES, f"holds {name!r}, which is no rule",
                              f"use only these: {', '.join(NAMES)}")
    try:
        re.compile(settings[WORD_PATTERN])
    except re.error as exc:
        raise ConfigError(settings.file, WORD_PATTERN,
                          f"is not a regular expression ({exc})",
                          "correct the pattern") from None
