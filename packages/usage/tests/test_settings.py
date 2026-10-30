"""usage's config: the whole schema, and the `[[model]]` rows the schema cannot check."""

import pytest

from moxi.config import ConfigError, check_text
from moxi_usage import settings

#: Every key the schema declares, with its default.
DEFAULTS = {
    "claude.archive_roots": [],
    "model": [],
    "prices.url": "https://raw.githubusercontent.com/BerriAI/litellm/main/"
                  "model_prices_and_context_window.json",
    "prices.max_age_days": 120,
    "index.workers": 0,
    "index.wait_seconds": 30.0,
    "limits.codex_pull_after": 600,
    "limits.history_days": 8,
    "limits.projection_minutes": 60,
    "marks.default": "off",
    "marks.max_age_days": 7,
    "marks.handler_wait_ms": 200,
    "restart_point.days": 21,
    "restart_point.interactive_only": True,
    "restart_point.interactive_originators": ["codex-tui", "Codex Desktop"],
    "restart_point.measure_weekly": False,
    "ceiling.warn_tokens": 0,
    "ceiling.hard_tokens": 0,
    "ceiling.return_tools": ["StructuredOutput"],
    "ceiling.handoff_roots": [],
    "ceiling.work_roots": [],
    "ceiling.allow_commands": [],
    "ceiling.max_age_days": 7,
}


def test_every_key_and_default():
    current = settings.current()
    keys = set(current)
    assert keys == set(DEFAULTS) | {"ceiling.warning_text", "ceiling.deny_text"}
    for key, value in DEFAULTS.items():
        assert current[key] == value, key
    for key in ("ceiling.warning_text", "ceiling.deny_text"):
        assert current[key] and "~" not in current[key] and "/" not in current[key]
    assert {"{growth}", "{current}", "{hard}"} <= {
        part for part in ("{growth}", "{current}", "{hard}")
        if part in current["ceiling.warning_text"]}


ROW = '[[model]]\nid = "m-1"\ninput = 1\n'


def test_a_good_row_reads():
    text = ROW + ('output = 2.5\nwindow = 128000\nmatch = "exact"\nprovider = "openai"\n'
                  'from = 2026-09-01\nto = "2026-11-01T12:00:00Z"\nnote = "n"\n'
                  'tiers.fast = {input = 2, output = 5, speed = "fast"}\n')
    settings.check(text)
    (row,) = settings.overrides(check_text("usage", settings.schema(), text))
    assert row.prices == {"input": 1.0, "output": 2.5} and row.limits == {"window": 128000}
    assert row.dated and row.as_dict()["from"] == "2026-09-01"
    assert row.as_dict()["to"] == "2026-11-01T12:00:00Z"
    assert row.tiers == {"fast": {"input": 2.0, "output": 5.0, "speed": "fast"}}


@pytest.mark.parametrize("text, said", [
    ('model = [1]\n', "not a table"),
    ('[[model]]\ninput = 1\n', "no id"),
    (ROW + 'inptu = 2\n', "unknown keys"),
    (ROW + 'match = "fuzzy"\n', "match"),
    ('[[model]]\nid = "claude-"\nmatch = "prefix"\ninput = 1\n', "no digit"),
    ('[[model]]\nid = "m-1"\ninput = -1\n', "input"),
    ('[[model]]\nid = "m-1"\ninput = "cheap"\n', "input"),
    (ROW + 'window = 0\n', "window"),
    (ROW + 'from = "soon"\n', "from"),
    (ROW + 'from = 2026-11-01\nto = 2026-10-01\n', "earlier"),
    (ROW + 'tiers = {fast = {inptu = 1}}\n', "tier"),
    ('[[model]]\nid = "m-1"\nnote = "only a note"\n', "no fact"),
])
def test_config_check_refuses_what_the_loader_refuses(text, said):
    with pytest.raises(ConfigError, match=said) as caught:
        settings.check(text)
    assert caught.value.key == "model"


def test_config_check_runs_the_schema_first():
    with pytest.raises(ConfigError):
        settings.check('[marks]\ndefault = "3x"\n')
    settings.check('[marks]\ndefault = "1.5x"\n')
