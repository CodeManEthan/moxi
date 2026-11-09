"""The config's check: the schema, then the rules of `show`, the bands and `dir.parts`."""

import pytest
from statusline_kit import config_file

from moxi import config
from moxi_statusline.settings import check, schema, show_problem


def test_the_defaults_pass():
    check("")
    settings = config.check_text("statusline", schema(), "")
    assert list(settings["show"]) == ["dir", "branch", "model", "effort", "context",
                                      "usage", "cache"]
    assert settings["separator"] == " │ "
    assert settings["color"] is True
    assert (settings["dir.parts"], settings["context.warn_at"],
            settings["context.alert_at"]) == (2, 50, 80)


@pytest.mark.parametrize("text", [
    'show = ["dir", "row", "model"]',
    'show = ["model", "usage", "cache", "anything9"]',
    'show = []',
    '[context]\nwarn_at = 80\nalert_at = 80',
    '[dir]\nparts = 1',
])
def test_what_it_accepts(text):
    check(text)


@pytest.mark.parametrize(("text", "key", "words"), [
    ('show = ["dir", "Not-A-Name"]', "show", "neither a base field nor a module name"),
    ('show = ["dir", "with space"]', "show", "neither a base field nor a module name"),
    ('show = ["dir", "row", "model", "row", "cost"]', "show", "2 'row' items"),
    ('show = ["row", "dir"]', "show", "'row' first"),
    ('show = ["dir", "row"]', "show", "'row' last"),
    ('show = ["row"]', "show", "'row' first"),
    ('[context]\nwarn_at = 81\nalert_at = 80', "context.warn_at", "above context.alert_at"),
    ('[dir]\nparts = 0', "dir.parts", "is 0"),
    ('[dir]\nparts = -2', "dir.parts", "is -2"),
])
def test_what_it_refuses(text, key, words):
    with pytest.raises(config.ConfigError) as caught:
        check(text)
    assert caught.value.key == key
    assert words in caught.value.problem


def test_the_schema_refuses_before_the_rules():
    with pytest.raises(config.ConfigError) as caught:
        check('show = "dir"')
    assert caught.value.key == "show"
    with pytest.raises(config.ConfigError):
        check("unknown = 1")


def test_show_problem_says_the_fix():
    problem, fix = show_problem(["dir", "row", "row", "model"])
    assert "at most two rows" in problem
    assert fix == "keep one 'row'"
    assert show_problem(["dir", "row", "model"]) is None


def test_moxi_config_set_runs_the_check():
    """The installed announcement names the check, so the core's setter refuses a change
    the check refuses and writes nothing."""
    with pytest.raises(config.ConfigError) as caught:
        config.set("statusline", "show", ["row", "dir"], by="test")
    assert "'row' first" in caught.value.problem
    assert not config_file().exists()
    config.set("statusline", "show", ["dir", "row", "model"], by="test")
    assert config_file().exists()


def test_two_keys_changed_together_pass_where_one_alone_is_refused():
    with pytest.raises(config.ConfigError):
        config.set("statusline", "context.warn_at", 90, by="test")
    config.set_many("statusline", [("context.warn_at", 90), ("context.alert_at", 95)],
                    by="test")
