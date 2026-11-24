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
    assert read["subconscious.trigger"] == "user"
    assert read["subconscious.scope"] == "project-first"
    assert read["subconscious.limit_s"] == 60
    assert read["subconscious.max_tools"] == 8
    assert read["subconscious.max_rounds"] == 4
    assert read["subconscious.max_points"] == 5
    assert read["subconscious.prefetch_hits"] == 8
    assert read["subconscious.on_demand_per_prompt"] == 2
    assert read["subconscious.fork_commands"] == [
        "moxi memory search", "moxi sessions read", "grep", "cat", "head", "tail", "ls",
        "wc", "cut"]


def test_every_key_is_read_at_its_use_and_none_from_the_env():
    schema = settings.schema()
    keys = [key for key in schema.keys.values()]
    assert {key.name for key in keys} == {
        "harnesses", "index.include_subagents", "index.tail_chars", "redact.sets",
        "reminder.enabled", "ingest.timer.enabled", "subconscious.trigger",
        "subconscious.scope", "subconscious.limit_s", "subconscious.max_tools",
        "subconscious.max_rounds", "subconscious.max_points", "subconscious.prefetch_hits",
        "subconscious.on_demand_per_prompt", "subconscious.fork_commands"}
    assert all(key.read == "use" and key.env is None for key in keys)
    assert set(module.INTERNAL_ENV) == set(module.SESSION_ENV) == {
        "MOXI_MEMORY_SUBCONSCIOUS", "MOXI_MEMORY_SUBCONSCIOUS_ROUTE"}


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
    ("[subconscious]\nlimit_s = 0\n", "subconscious.limit_s"),
    ("[subconscious]\nlimit_s = 3601\n", "subconscious.limit_s"),
    ("[subconscious]\nmax_tools = 0\n", "subconscious.max_tools"),
    ("[subconscious]\nmax_rounds = 0\n", "subconscious.max_rounds"),
    ("[subconscious]\nmax_points = 0\n", "subconscious.max_points"),
    ("[subconscious]\nprefetch_hits = -1\n", "subconscious.prefetch_hits"),
    ("[subconscious]\non_demand_per_prompt = -1\n", "subconscious.on_demand_per_prompt"),
    ('[subconscious]\nfork_commands = ["grep", "  "]\n', "subconscious.fork_commands"),
    ('[subconscious]\ntrigger = "comms"\n', "subconscious.trigger"),
    ('[subconscious]\nscope = "near"\n', "subconscious.scope"),
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


@pytest.mark.parametrize("limit_s", [1, 60, 3600])
def test_the_check_takes_a_limit_from_1_to_3600_seconds(limit_s):
    settings.check_text(f"[subconscious]\nlimit_s = {limit_s}\n")


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
    assert set(module.OPTIONAL) == {"comms", "launcher", "connector", "secrets",
                                     "notes"}
