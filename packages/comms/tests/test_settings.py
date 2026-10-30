"""comms' one config file: the schema, `config.load`, the overrides and the refusals."""

from __future__ import annotations

import os

import pytest

from conftest import write_config
from moxi import config as moxi_config

from moxi_comms import config, constants, gateway, local_gateway, module, secrets, tokens


def test_the_schema_parses_and_its_overrides_are_spelled_with_the_prefix():
    schema = config.schema()
    schema.check_env("comms")
    assert {k.env for k in schema.live() if k.env} == {
        "MOXI_COMMS_GATEWAY", "MOXI_COMMS_LOCAL_RECORD", "MOXI_COMMS_LOCAL_RETAIN_DAYS",
        "MOXI_COMMS_TOKEN_STORE"}
    assert not set(module.INTERNAL_ENV) & {k.env for k in schema.live()}


def test_the_retired_keys_are_declared():
    retired = {k.name for k in config.schema().keys.values() if k.retired}
    assert retired == {"claude.channel", "policy.never_held", "policy.next_turn_refused",
                       "provision.slots"}
    assert not retired & {k.name for k in config.schema().live()}


def test_cache_required_is_declared_off_by_default_and_read_at_start():
    key = config.schema().keys["cache.required"]
    assert (key.type, key.default, key.read, key.env) == ("bool", False, "start", None)
    assert config.load().cache.required is False


def test_the_defaults_with_no_file():
    cfg = config.load()
    assert cfg.source == "defaults"
    assert cfg.backend == "local"
    assert (cfg.local.record, cfg.local.retain_days) == ("", 90)
    assert cfg.threads.reply_cap == constants.DEFAULT_REPLY_CAP
    assert cfg.secrets.chain == ("env", "store") == secrets.DEFAULT_CHAIN
    assert (cfg.secrets.store, cfg.secrets.roles) == ("", ())
    assert cfg.policy.cache_callers == ("operator",)
    assert cfg.policy.labels == {}
    assert cfg.provision.operator == "operator"
    assert (cfg.replay.messages, cfg.replay.seconds) == \
        (constants.REPLAY_N, constants.REPLAY_T)
    assert cfg.block is False
    assert cfg.codex == cfg.opencode == {"defaults": {}, "agents": {}}
    assert not hasattr(cfg, "claude")


def test_one_owner_of_the_retention():
    assert config.LOCAL_RETAIN_DAYS == local_gateway.RETAIN_DAYS == \
        config.schema().keys["local.retain_days"].default


#: A file that sets every table.
EVERY_TABLE = """
backend = "mock"
block = true

[local]
record = "~/rec.db"
retain_days = 7

[threads]
reply_cap = 3

[secrets]
chain = ["store"]
roles = ["calendar", "pager"]

[policy]
cache_callers = ["operator", "herald"]
labels = { "console@acme" = "the build console's send-only identity" }

[provision]
operator = "alice"

[replay]
messages = 10
hours = 2

[codex.defaults]
sandbox = "workspace-write"

[codex.agents."planner@proj-a"]
model = "gpt-x"
cwd = "/src/proj-a"

[opencode.agents.reviewer]
model = "acme/coder"
cwd = "/src/x"
"""


def test_the_file_sets_every_table():
    path = write_config(None, EVERY_TABLE)
    cfg = config.load()
    assert cfg.source == str(path) == str(config.config_path())
    assert cfg.backend == "mock" and cfg.block is True
    assert cfg.local.record == os.path.expanduser("~/rec.db")
    assert cfg.local.retain_days == 7 and cfg.threads.reply_cap == 3
    assert cfg.secrets.chain == ("store",)
    assert cfg.secrets.roles == ("calendar", "pager")
    assert cfg.policy.cache_callers == ("operator", "herald")
    assert cfg.policy.labels == {"console@acme": "the build console's send-only identity"}
    assert cfg.provision.operator == "alice"
    assert (cfg.replay.messages, cfg.replay.seconds) == (10, 7200)
    assert cfg.codex["defaults"] == {"sandbox": "workspace-write"}
    assert cfg.codex["agents"]["planner@proj-a"] == {"model": "gpt-x",
                                                     "cwd": "/src/proj-a"}
    assert cfg.opencode["agents"]["reviewer"]["model"] == "acme/coder"
    assert cfg.sources["backend"] == "file" and cfg.sources["block"] == "file"


def test_block_is_read_at_each_use():
    assert config.load().block is False
    write_config(None, "block = true\n")
    assert config.load().block is True
    write_config(None, "block = false\n")
    assert config.load().block is False


def test_an_env_override_wins_over_the_file(monkeypatch, tmp_path):
    write_config(None, 'backend = "mock"\n[local]\nretain_days = 7\n')
    monkeypatch.setenv("MOXI_COMMS_GATEWAY", "local")
    monkeypatch.setenv("MOXI_COMMS_LOCAL_RETAIN_DAYS", "0")
    monkeypatch.setenv("MOXI_COMMS_LOCAL_RECORD", str(tmp_path / "r.db"))
    monkeypatch.setenv("MOXI_COMMS_TOKEN_STORE", str(tmp_path / "store"))
    cfg = config.load()
    assert cfg.backend == "local" and cfg.sources["backend"] == "env"
    assert cfg.local.retain_days == 0
    assert cfg.local.record == str(tmp_path / "r.db")
    assert cfg.secrets.store == str(tmp_path / "store")


def test_a_given_environment_is_read_instead_of_the_process(tmp_path):
    env = {"MOXI_COMMS_CONFIG_HOME": str(tmp_path / "cfg"), "HOME": str(tmp_path)}
    write_config(env, "[replay]\nmessages = 5\n")
    assert config.config_path(env) == tmp_path / "cfg" / "comms" / "config.toml"
    assert config.load(env=env).replay.messages == 5
    assert config.load().replay.messages == 50


#: Files comms' loader refuses, each with the key the refusal names.
BAD_FILES = [
    ('backend = "remote"\n', "backend"),
    ("[threads]\nreply_cap = 0\n", "threads.reply_cap"),
    ("[threads]\nreply_cap = true\n", "threads.reply_cap"),
    ("[local]\nretain_days = -1\n", "local.retain_days"),
    ('[secrets]\nchain = ["env", "vault"]\n', "secrets.chain"),
    ("[secrets]\nchain = []\n", "secrets.chain"),
    ('[secrets]\nroles = ["operator"]\n', "secrets.roles"),
    ('[secrets]\nroles = ["../up"]\n', "secrets.roles"),
    ('[policy]\ncache_callers = [""]\n', "policy.cache_callers"),
    ('[policy]\nlabels = { "x" = 1 }\n', "policy.labels"),
    ("[provision]\nslots = 0\n", "provision.slots"),
    ("[replay]\nmessages = 0\n", "replay.messages"),
    ('[codex.agents]\nplanner = 3\n', "codex.agents"),
    ('[claude]\nchannel = "server"\n', "claude.channel"),
    ('[policy]\nnever_held = []\n', "policy.never_held"),
    ("[discord]\nguild = 1\n", "discord"),
    ('[codex.agents.planner]\nslot = 1\napi_token = "x"\n',
     "codex.agents.planner.api_token"),
]


@pytest.mark.parametrize("text, key", BAD_FILES)
def test_a_bad_file_is_refused_naming_the_key(text, key):
    write_config(None, text)
    with pytest.raises(config.ConfigError) as caught:
        config.load()
    assert caught.value.key == key
    assert isinstance(caught.value, moxi_config.ConfigError)
    assert caught.value.fix


def test_a_retired_key_says_what_to_do():
    write_config(None, '[claude]\nchannel = "plugin"\n')
    with pytest.raises(config.ConfigError, match="turns' plugin delivers"):
        config.load()


def test_the_retired_provision_slots_key_says_to_remove_it():
    write_config(None, "[provision]\nslots = 2\n")
    with pytest.raises(config.ConfigError) as caught:
        config.load()
    assert caught.value.key == "provision.slots"
    assert caught.value.fix == "remove it: comms has no slots"


def test_a_token_shaped_key_is_refused_anywhere_in_the_file():
    write_config(None, '[codex.agents.planner]\nslot = 1\napi_token = "x"\n')
    with pytest.raises(config.ConfigError, match="looks like a secret") as caught:
        config.load()
    assert caught.value.key == "codex.agents.planner.api_token"


def test_malformed_toml_is_refused():
    write_config(None, "backend = \n")
    with pytest.raises(config.ConfigError):
        config.load()


def test_the_roster_parsers_can_raise_the_same_error_with_one_message():
    error = config.ConfigError("planner is not in the codex roster")
    assert str(error) == "planner is not in the codex roster"
    assert isinstance(error, moxi_config.ConfigError)


# ------------------------------------------------------------------ the readers

def test_the_backend_choice_names_its_source(monkeypatch):
    assert gateway.backend_choice() == gateway.BackendChoice("local", gateway.SOURCE_DEFAULT)
    assert gateway.backend_choice().describe() == "the default"
    write_config(None, 'backend = "mock"\n')
    assert gateway.backend_choice() == gateway.BackendChoice("mock", gateway.SOURCE_CONFIG)
    monkeypatch.setenv("MOXI_COMMS_GATEWAY", "local")
    assert gateway.backend_choice().source == gateway.SOURCE_ENV
    assert gateway.backend_choice("mock").source == gateway.SOURCE_FLAG
    assert gateway.default_backend() == gateway.LOCAL


def test_an_unknown_backend_is_a_gateway_error(monkeypatch):
    monkeypatch.setenv("MOXI_COMMS_GATEWAY", "discord")
    with pytest.raises(gateway.GatewayError):
        gateway.backend_choice()
    with pytest.raises(gateway.GatewayError):
        gateway.backend_choice("discord")


def test_the_token_store_location_comes_from_the_config(monkeypatch, tmp_path):
    from moxi_comms import paths

    assert tokens.TokenStore().root == paths.tokens_dir()
    write_config(None, f'[secrets]\nstore = "{tmp_path / "a"}"\n')
    assert tokens.TokenStore().root == tmp_path / "a"
    monkeypatch.setenv(tokens.STORE_ENV_VAR, str(tmp_path / "b"))
    assert tokens.TokenStore().root == tmp_path / "b"
    assert tokens.TokenStore(tmp_path / "c").root == tmp_path / "c"
    # A caller's injected env names the override, never the roots.
    assert tokens.TokenStore(env={"MOXI_COMMS_TOKEN_STORE": str(tmp_path / "d")}).root \
        == tmp_path / "d"
    monkeypatch.delenv(tokens.STORE_ENV_VAR)
    assert tokens.TokenStore(env={"MOXI_COMMS_PROJECT": "p"}).root == tmp_path / "a"


def test_a_role_the_box_lists_keys_under_itself_and_an_unlisted_one_is_unknown():
    assert tokens.owner_for_role("calendar").parts == ()
    assert tokens.owner_of_holder("comms/calendar") is None
    write_config(None, '[secrets]\nroles = ["calendar"]\n')
    owner = tokens.owner_for_role("calendar")
    assert owner.parts == ("calendar",) and owner.role == "calendar"
    assert tokens.owner_of_holder("comms/calendar") == owner
    assert tokens.owner_for_role("tunnel").parts == ()
    assert tokens.owner_for_role(tokens.ROLE_OPERATOR).parts == ("operator",)


def test_the_chain_reads_the_configured_store(tmp_path):
    store = tmp_path / "store"
    write_config(None, f'[secrets]\nchain = ["store"]\nstore = "{store}"\n')
    owner = tokens.owner_for_role(tokens.ROLE_OPERATOR)
    tokens.TokenStore(store).write_secret(owner, tokens.OPERATOR_SECRET_ID, "tok")
    chain = secrets.from_config()
    assert chain.names == ("store",)
    assert chain.get(tokens.OPERATOR_SECRET_ID, owner=owner)["value"] == "tok"


def test_the_running_reader_refuses_a_bad_start_and_keeps_the_last_good_at_use():
    """`config.Config`, the daemon's reader: a bad file refuses `start()`; after a
    good start, `current()` keeps the last good settings while the file is in error,
    reports once per change of the file, and follows the file again once it loads."""
    write_config(None, "[replay]\nmessages = [\n")
    with pytest.raises(config.ConfigError):
        config.Config(report=lambda exc: None).start()

    reports = []
    write_config(None, "block = true\n[replay]\nmessages = 7\n")
    reader = config.Config(report=reports.append)
    assert reader.start().block is True
    write_config(None, "block = false\n[replay]\nmessages = [\n")
    assert reader.use("block") is True and reader.use("replay").messages == 7
    assert reader.current().block is True
    assert len(reports) == 1 and isinstance(reports[0], config.ConfigError)
    # A new bad edit is a change of the file: reported once more.
    write_config(None, '[codex.agents.planner]\nslot = 1\napi_token = "x"\n')
    assert reader.use("block") is True
    assert len(reports) == 2 and "looks like a secret" in str(reports[1])
    write_config(None, "block = false\n")
    assert reader.use("block") is False and reader.use("replay").messages == 50
    assert len(reports) == 2


def test_the_running_reader_is_moxi_configs():
    assert issubclass(config.Config, moxi_config.Config)
    with pytest.raises(RuntimeError):
        config.Config().current()


def test_a_named_file_is_read_in_place(tmp_path, monkeypatch):
    import tempfile

    def no_temp_root(*args, **kwargs):
        raise AssertionError("load_file made a temporary config root")

    monkeypatch.setattr(tempfile, "TemporaryDirectory", no_temp_root)
    named = tmp_path / "launch.toml"
    named.write_text("block = true\n[replay]\nmessages = 9\n")
    cfg = config.load_file(named)
    assert cfg.block is True and cfg.replay.messages == 9
    assert cfg.source == str(named)
    assert config.load().block is False


def test_a_named_relative_file_is_read_from_the_working_directory(tmp_path, monkeypatch):
    (tmp_path / "launch.toml").write_text("block = true\n")
    monkeypatch.chdir(tmp_path)
    assert config.load_file("launch.toml").source == str(tmp_path / "launch.toml")


@pytest.mark.parametrize("text, key", [("block = 3\n", "block"),
                                       ('[codex.agents.p]\napi_token = "x"\n',
                                        "codex.agents.p.api_token")])
def test_a_named_file_s_errors_name_that_file(tmp_path, text, key):
    named = tmp_path / "launch.toml"
    named.write_text(text)
    with pytest.raises(config.ConfigError) as caught:
        config.load_file(named)
    assert caught.value.file == str(named) and caught.value.key == key


def test_a_named_file_that_does_not_exist_is_refused(tmp_path):
    with pytest.raises(config.ConfigError, match="is not a file"):
        config.load_file(tmp_path / "missing.toml")


# -- the text entry: the announcement's CONFIG_CHECK ---------------------------------------

def test_the_announcement_names_the_whole_loader_over_the_text():
    from moxi import modules

    assert module.CONFIG_CHECK == "moxi_comms.config:check_text"
    assert modules.load("comms").config_check == module.CONFIG_CHECK


@pytest.mark.parametrize("text", ["", EVERY_TABLE])
def test_the_text_entry_passes_what_the_loader_passes(text):
    write_config(None, text)
    config.load()
    assert config.check_text(text) is None


@pytest.mark.parametrize("text, key", BAD_FILES + [("backend = \n", None)])
def test_the_text_entry_refuses_every_file_the_loader_refuses(text, key):
    with pytest.raises(config.ConfigError) as caught:
        config.check_text(text)
    assert (caught.value.key, caught.value.file) == (key, None)
    write_config(None, text)
    with pytest.raises(config.ConfigError) as loaded:
        config.load()
    assert (loaded.value.key, loaded.value.problem) == (key, caught.value.problem)


#: Files the schema passes and a roster parser refuses, with the words of the refusal.
BAD_ROWS = [
    ('[codex.agents."p@x"]\nslot = 1\ncwd = "/src"\n', "slot is retired"),
    ('[codex.agents."p@x"]\ncwd = "/src"\ncolour = "red"\n', "unknown key(s) colour"),
    ('[codex.agents."Bad Name"]\ncwd = "/src"\n', "is not a valid name"),
    ('[codex.agents."agent-3@x"]\ncwd = "/src"\n', "agent-N namespace is reserved"),
    ('[codex.agents."p@x"]\ncwd = "/src"\nsandbox = "open"\n', "is not one of"),
    ('[codex.agents."p@x"]\ncwd = "/src"\nsandbox = "read-only"\npermissions = "a"\n',
     "cannot be combined"),
    ('[codex.agents."p@x"]\ncwd = "/src"\ndirectors = "main"\n',
     "directors must be a list"),
    ('[codex.defaults]\nslot = 1\n', "slot is retired"),
    ('[opencode.agents."p@x"]\nslot = 1\ncwd = "/src"\n', "slot is retired"),
    ('[opencode.agents."p@x"]\ncwd = "/src"\nmodel = "nomodel"\n',
     "must be a \"provider/model\" id"),
    ('[opencode.agents."p@x"]\ncwd = "/src"\ndirectors = ["bad name"]\n', None),
]


@pytest.mark.parametrize("text, words", BAD_ROWS)
def test_the_text_entry_refuses_every_row_its_roster_refuses(text, words):
    from moxi_comms.adapters import codex_roster, opencode_roster

    with pytest.raises(config.ConfigError) as caught:
        config.check_text(text)
    assert caught.value.file is None and caught.value.fix
    if words is not None:
        assert words in caught.value.problem
    write_config(None, text)
    config.load()
    roster = codex_roster if text.startswith("[codex") else opencode_roster
    with pytest.raises(config.ConfigError) as loaded:
        roster.load()
    assert loaded.value.problem.endswith(caught.value.problem)


def test_the_text_entry_reads_no_environment(monkeypatch):
    write_config(None, 'backend = "local"\n')
    monkeypatch.setenv("MOXI_COMMS_GATEWAY", "bogus")
    with pytest.raises(config.ConfigError):
        config.load()
    assert config.check_text('backend = "local"\n') is None
    monkeypatch.setenv("MOXI_COMMS_GATEWAY", "mock")
    with pytest.raises(config.ConfigError):
        config.check_text('backend = "remote"\n')


def test_the_text_entry_leaves_no_cache_entry():
    checked, cached = dict(config._checked), dict(moxi_config._cache)
    config.check_text(EVERY_TABLE)
    with pytest.raises(config.ConfigError):
        config.check_text('[secrets]\nchain = []\n')
    assert (config._checked, moxi_config._cache) == (checked, cached)


@pytest.fixture
def core_state(monkeypatch, tmp_path):
    """The core's own roots (the settings history) under the test's tmp."""
    monkeypatch.setenv("MOXI_HOME", str(tmp_path / "core"))


@pytest.mark.parametrize("argv, value, key, words", [
    (("codex.agents", '{"p@x" = {api_token = "sk-secret"}}'),
     {"p@x": {"api_token": "sk-secret"}}, "codex.agents.p@x.api_token",
     "looks like a secret"),
    (("secrets.chain", '["env", "vault"]'), ["env", "vault"], "secrets.chain",
     "not a resolver"),
    (("secrets.chain", "[]"), [], "secrets.chain", "is empty"),
    (("secrets.roles", "--add", "reader"), None, "secrets.roles",
     "one of comms' own roles"),
])
def test_config_set_refuses_what_comms_refuses(core_state, capsys, argv, value, key,
                                               words):
    from moxi import cli

    path = write_config(None, "block = true\n")
    assert cli.main(["config", "set", "comms", *argv]) == 2
    out, err = capsys.readouterr()
    assert out == "" and f"{path}: key {key!r}: " in err and words in err
    assert path.read_text() == "block = true\n"
    assert moxi_config.history("comms") == []
    with pytest.raises(config.ConfigError) as caught:
        if value is None:
            moxi_config.add_item("comms", argv[0], argv[-1], by="ui")
        else:
            moxi_config.set("comms", argv[0], value, by="ui")
    assert caught.value.file == str(path) and caught.value.key == key
    assert path.read_text() == "block = true\n"


@pytest.mark.parametrize("key", ["codex.agents", "opencode.agents"])
def test_config_set_refuses_a_row_its_roster_refuses(core_state, capsys, key):
    from moxi import cli

    path = write_config(None, "block = true\n")
    assert cli.main(["config", "set", "comms", key, '{"p@x" = {slot = 1}}']) == 2
    out, err = capsys.readouterr()
    assert out == "" and f"{path}: " in err and "slot is retired" in err
    assert path.read_text() == "block = true\n"
    assert moxi_config.history("comms") == []


def test_config_set_and_check_pass_a_good_file_under_a_bad_variable(core_state, capsys,
                                                                    monkeypatch):
    from moxi import cli

    path = write_config(None, "block = true\n")
    monkeypatch.setenv("MOXI_COMMS_GATEWAY", "bogus")
    assert cli.main(["config", "set", "comms", "secrets.roles", "--add", "pager"]) == 0
    assert cli.main(["config", "unset", "comms", "block"]) == 0
    assert cli.main(["config", "comms", "check"]) == 0
    assert capsys.readouterr().err == ""
    assert config.check_text(path.read_text()) is None
    assert "pager" in path.read_text() and "block" not in path.read_text()
