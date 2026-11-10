"""repo's config, read through `moxi.config` at each use.

Keys: `git_hooks.mode`, the mode a repo gets when it pins none (`auto`, `config` or
`shim`); `commit_rules.everywhere`, whether `git-hooks add` and `refresh` register the
commit-rules piece in each repo they touch; `commit_proof.everywhere`, the same for the
commit-proof piece; `commit_rules.tag_patterns`, the tag patterns the commit-rules piece
starts with in a repo where it is first registered. Every value scoped to one repo lives
in that repo's git config instead.
"""

from __future__ import annotations

from moxi.config import ConfigError, Schema

MODULE = "repo"
SCHEMA_RESOURCE = "settings.toml"
TAG_PATTERNS = "commit_rules.tag_patterns"

_schema: Schema | None = None


def schema() -> Schema:
    """The module's schema, parsed once per process."""
    global _schema
    if _schema is None:
        _schema = Schema.from_resource(__package__, SCHEMA_RESOURCE)
    return _schema


def load():
    """repo's settings as they stand; raises `moxi.config.ConfigError`."""
    from moxi import config

    settings = config.load(MODULE, schema())
    _check_patterns(settings)
    return settings


def check_text(text: str) -> None:
    """The module's whole loader over a config text: the schema's check, then each
    `commit_rules.tag_patterns` entry must compile as a regular expression, which the
    schema can't say. Raises `ConfigError` when it refuses."""
    from moxi import config

    _check_patterns(config.check_text(MODULE, schema(), text))


def _check_patterns(settings) -> None:
    from moxi_repo.commit_rules import pattern_problem

    for pattern in settings[TAG_PATTERNS]:
        why = pattern_problem(pattern)
        if why is not None:
            raise ConfigError(settings.file, TAG_PATTERNS, f"holds {why}",
                              "correct the pattern or remove it")
