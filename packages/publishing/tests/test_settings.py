"""publishing's config: the defaults, and what the loader refuses."""

from __future__ import annotations

import pytest
from moxi.config import ConfigError

from moxi_publishing import rules, settings


def test_the_defaults():
    values = settings.load()
    assert tuple(values[settings.RULES]) == rules.NAMES
    assert values[settings.WORD_PATTERN] == r"^\S+$"
    assert list(values[settings.DROP_TRAILERS]) == ["Claude-Session", "Commit-Check"]
    assert values[settings.MAIN] == "main"
    assert values[settings.WORD_REQUIRED] is False


@pytest.mark.parametrize("text", [
    'provenance.rules = ["no-such-rule"]\n',
    'push.word_pattern = "("\n',
])
def test_the_loader_refuses(text):
    with pytest.raises(ConfigError):
        settings.check_text(text)
