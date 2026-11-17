"""memory's config: the keys and their defaults, the checks the schema can't make, a read
at each use, and the folders memory keeps its files in."""

import pytest
from moxi.config import ConfigError
from moxi.paths import Layout

from moxi_memory import module, settings


def write_config(text):
    path = Layout("memory").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_every_key_has_its_default():
    read = settings.read()
    assert read["harnesses"] == ["claude", "codex", "opencode"]
    assert read["index.include_subagents"] is False
    assert read["index.tail_chars"] == 1000
    assert read["redact.sets"] == ["credentials", "broad"]
    assert read["reminder.enabled"] is True
    assert read["ingest.timer.enabled"] is False


def test_every_key_is_read_at_its_use_and_none_from_the_env():
    schema = settings.schema()
    keys = [key for key in schema.keys.values()]
    assert {key.name for key in keys} == {"harnesses", "index.include_subagents", "index.tail_chars",
                                          "redact.sets", "reminder.enabled", "ingest.timer.enabled"}
    assert all(key.read == "use" and key.env is None for key in keys)
    assert module.INTERNAL_ENV == {}


def test_an_edit_takes_effect_at_the_next_read():
    assert settings.read()["reminder.enabled"] is True
    write_config("[reminder]\nenabled = false\n[index]\ntail_chars = 200\n")
    read = settings.read()
    assert read["reminder.enabled"] is False and read["index.tail_chars"] == 200


@pytest.mark.parametrize("text, key", [
    ('harnesses = ["claude", "gemini"]\n', "harnesses"),
    ("[index]\ntail_chars = 0\n", "index.tail_chars"),
    ("[redact]\nsets = []\n", "redact.sets"),
    ('[redact]\nsets = [" "]\n', "redact.sets"),
])
def test_the_check_refuses_what_memory_cant_use(text, key):
    with pytest.raises(ConfigError) as caught:
        settings.check_text(text)
    assert caught.value.key == key
    write_config(text)
    with pytest.raises(ConfigError):
        settings.read()


def test_the_check_takes_a_good_file():
    settings.check_text('harnesses = ["codex"]\n[reminder]\nenabled = false\n[index]\ninclude_subagents = true\n')


def test_the_dirs_are_the_cores_for_memory():
    layout = Layout("memory")
    assert settings.state_dir() == layout.state_dir()
    assert settings.cache_dir() == layout.cache_dir()
    env = {"MOXI_HOME": "/somewhere"}
    assert str(settings.state_dir(env)) == "/somewhere/state/memory"
    assert str(settings.cache_dir(env)) == "/somewhere/cache/memory"


def test_the_announcement_names_the_schema_and_its_check():
    assert module.SETTINGS == "settings.toml"
    assert module.CONFIG_CHECK == "moxi_memory.settings:check_text"
    assert set(module.OPTIONAL) == {"comms", "launcher"}
