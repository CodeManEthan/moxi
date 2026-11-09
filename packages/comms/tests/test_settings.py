"""comms' one config file: the schema, `config.load`, the overrides and the refusals."""

from __future__ import annotations

import os

import pytest

from conftest import write_config
from moxi import config as moxi_config

from moxi_comms import config, constants, record, local_record, module, secrets, tokens


def test_the_schema_parses_and_its_overrides_are_spelled_with_the_prefix():
    schema = config.schema()
    schema.check_env("comms")
    assert {k.env for k in schema.live() if k.env} == {
        "MOXI_COMMS_BACKEND", "MOXI_COMMS_LOCAL_RECORD", "MOXI_COMMS_LOCAL_RETAIN_DAYS",
        "MOXI_COMMS_TOKEN_STORE", "MOXI_COMMS_DELIVERY_EXPIRY"}
    assert not set(module.INTERNAL_ENV) & {k.env for k in schema.live()}


def test_the_retired_keys_are_declared():
    retired = {k.name for k in config.schema().keys.values() if k.retired}
    assert retired == {"claude.channel", "policy.never_held", "policy.next_turn_refused",
                       "provision.slots", "connector.wait_default", "connector.wait_max"}
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
    assert cfg.credentials.chain == ("env", "keychain", "store") == secrets.DEFAULT_CHAIN
    assert (cfg.credentials.store, cfg.credentials.roles) == ("", ())
    assert cfg.policy.cache_callers == ("operator",)
    assert cfg.policy.labels == {}
    assert cfg.provision.operator == "operator"
    assert cfg.block is False
    assert cfg.codex == cfg.opencode == {"defaults": {}, "agents": {}}
    assert not hasattr(cfg, "claude")


def test_one_owner_of_the_retention():
    assert config.LOCAL_RETAIN_DAYS == local_record.RETAIN_DAYS == \
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

[credentials]
chain = ["store"]
roles = ["calendar", "pager"]

[policy]
cache_callers = ["operator", "herald"]
labels = { "console@acme" = "the build console's send-only identity" }

[provision]
operator = "alice"

[delivery]
expiry = "6h"

[codex.defaults]
sandbox = "workspace-write"

[codex.agents."planner@proj-a"]
model = "gpt-x"
cwd = "/src/proj-a"

[opencode.agents.reviewer]
model = "acme/coder"
cwd = "/src/x"
"""


def test_the_file_sets_every_table(monkeypatch):
    monkeypatch.delenv("MOXI_COMMS_DELIVERY_EXPIRY")
    path = write_config(None, EVERY_TABLE)
    cfg = config.load()
    assert cfg.source == str(path) == str(config.config_path())
    assert cfg.backend == "mock" and cfg.block is True
    assert cfg.local.record == os.path.expanduser("~/rec.db")
    assert cfg.local.retain_days == 7 and cfg.threads.reply_cap == 3
    assert cfg.credentials.chain == ("store",)
    assert cfg.credentials.roles == ("calendar", "pager")
    assert cfg.policy.cache_callers == ("operator", "herald")
    assert cfg.policy.labels == {"console@acme": "the build console's send-only identity"}
    assert cfg.provision.operator == "alice"
    assert (cfg.delivery.expiry, cfg.delivery.expiry_seconds) == ("6h", 21600)
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
    monkeypatch.setenv("MOXI_COMMS_BACKEND", "local")
    monkeypatch.setenv("MOXI_COMMS_LOCAL_RETAIN_DAYS", "0")
    monkeypatch.setenv("MOXI_COMMS_LOCAL_RECORD", str(tmp_path / "r.db"))
    monkeypatch.setenv("MOXI_COMMS_TOKEN_STORE", str(tmp_path / "store"))
    cfg = config.load()
    assert cfg.backend == "local" and cfg.sources["backend"] == "env"
    assert cfg.local.retain_days == 0
    assert cfg.local.record == str(tmp_path / "r.db")
    assert cfg.credentials.store == str(tmp_path / "store")


def test_a_given_environment_is_read_instead_of_the_process(tmp_path):
    env = {"MOXI_COMMS_CONFIG_HOME": str(tmp_path / "cfg"), "HOME": str(tmp_path)}
    write_config(env, "[threads]\nreply_cap = 5\n")
    assert config.config_path(env) == tmp_path / "cfg" / "comms" / "config.toml"
    assert config.load(env=env).threads.reply_cap == 5
    assert config.load().threads.reply_cap == constants.DEFAULT_REPLY_CAP


#: Files comms' loader refuses, each with the key the refusal names.
BAD_FILES = [
    ('backend = "remote"\n', "backend"),
    ("[threads]\nreply_cap = 0\n", "threads.reply_cap"),
    ("[threads]\nreply_cap = true\n", "threads.reply_cap"),
    ("[local]\nretain_days = -1\n", "local.retain_days"),
    ('[credentials]\nchain = ["env", "vault"]\n', "credentials.chain"),
    ("[credentials]\nchain = []\n", "credentials.chain"),
    ('[credentials]\nroles = ["operator"]\n', "credentials.roles"),
    ('[credentials]\nroles = ["../up"]\n', "credentials.roles"),
    ('[policy]\ncache_callers = [""]\n', "policy.cache_callers"),
    ('[policy]\nlabels = { "x" = 1 }\n', "policy.labels"),
    ("[provision]\nslots = 0\n", "provision.slots"),
    ("[replay]\nmessages = 0\n", "replay.messages"),
    ("[replay]\nhours = -1\n", "replay.hours"),
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


def test_the_replay_keys_are_read_and_default_to_comms_0_2s():
    assert (config.load().replay.messages, config.load().replay.hours) == (50, 24)
    write_config(None, "[replay]\nmessages = 7\nhours = 2\n")
    assert (config.load().replay.messages, config.load().replay.hours) == (7, 2)


def test_a_retired_key_says_what_to_do():
    write_config(None, '[claude]\nchannel = "plugin"\n')
    with pytest.raises(config.ConfigError, match="turns' plugin delivers"):
        config.load()


@pytest.mark.parametrize("text, old, new", [
    ("[connector]\nwait_default = 20\n", "connector.wait_default", "connector.wait_default_s"),
    ("[connector]\nwait_max = 60\n", "connector.wait_max", "connector.wait_max_s"),
])
def test_a_renamed_key_is_refused_naming_the_new_one(text, old, new):
    write_config(None, text)
    with pytest.raises(config.ConfigError) as caught:
        config.load()
    assert caught.value.key == old
    assert caught.value.fix == f"renamed: set {new} instead"


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
    assert record.backend_choice() == record.BackendChoice("local", record.SOURCE_DEFAULT)
    assert record.backend_choice().describe() == "the default"
    write_config(None, 'backend = "mock"\n')
    assert record.backend_choice() == record.BackendChoice("mock", record.SOURCE_CONFIG)
    monkeypatch.setenv("MOXI_COMMS_BACKEND", "local")
    assert record.backend_choice().source == record.SOURCE_ENV
    assert record.backend_choice("mock").source == record.SOURCE_FLAG
    assert record.default_backend() == record.LOCAL


def test_an_unknown_backend_is_a_record_error(monkeypatch):
    monkeypatch.setenv("MOXI_COMMS_BACKEND", "discord")
    with pytest.raises(record.RecordError):
        record.backend_choice()
    with pytest.raises(record.RecordError):
        record.backend_choice("discord")


def test_the_token_store_location_comes_from_the_config(monkeypatch, tmp_path):
    from moxi_comms import paths

    assert tokens.TokenStore().root == paths.tokens_dir()
    write_config(None, f'[credentials]\nstore = "{tmp_path / "a"}"\n')
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
    write_config(None, '[credentials]\nroles = ["calendar"]\n')
    owner = tokens.owner_for_role("calendar")
    assert owner.parts == ("calendar",) and owner.role == "calendar"
    assert tokens.owner_of_holder("comms/calendar") == owner
    assert tokens.owner_for_role("tunnel").parts == ()
    assert tokens.owner_for_role(tokens.ROLE_OPERATOR).parts == ("operator",)


def test_the_chain_reads_the_configured_store(tmp_path):
    store = tmp_path / "store"
    write_config(None, f'[credentials]\nchain = ["store"]\nstore = "{store}"\n')
    owner = tokens.owner_for_role(tokens.ROLE_OPERATOR)
    tokens.TokenStore(store).write_secret(owner, tokens.OPERATOR_SECRET_ID, "tok")
    chain = secrets.from_config()
    assert chain.names == ("store",)
    assert chain.get(tokens.OPERATOR_SECRET_ID, owner=owner)["value"] == "tok"


def test_the_running_reader_refuses_a_bad_start_and_keeps_the_last_good_at_use():
    """`config.Config`, the daemon's reader: a bad file refuses `start()`; after a
    good start, `current()` keeps the last good settings while the file is in error,
    reports once per change of the file, and follows the file again once it loads."""
    write_config(None, "[threads]\nreply_cap = [\n")
    with pytest.raises(config.ConfigError):
        config.Config(report=lambda exc: None).start()

    reports = []
    write_config(None, "block = true\n[threads]\nreply_cap = 7\n")
    reader = config.Config(report=reports.append)
    assert reader.start().block is True
    write_config(None, "block = false\n[threads]\nreply_cap = [\n")
    assert reader.use("block") is True and reader.use("threads").reply_cap == 7
    assert reader.current().block is True
    assert len(reports) == 1 and isinstance(reports[0], config.ConfigError)
    # A new bad edit is a change of the file: reported once more.
    write_config(None, '[codex.agents.planner]\nslot = 1\napi_token = "x"\n')
    assert reader.use("block") is True
    assert len(reports) == 2 and "looks like a secret" in str(reports[1])
    write_config(None, "block = false\n")
    assert reader.use("block") is False and reader.use("threads").reply_cap == constants.DEFAULT_REPLY_CAP
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
    named.write_text("block = true\n[threads]\nreply_cap = 9\n")
    cfg = config.load_file(named)
    assert cfg.block is True and cfg.threads.reply_cap == 9
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
    monkeypatch.setenv("MOXI_COMMS_BACKEND", "bogus")
    with pytest.raises(config.ConfigError):
        config.load()
    assert config.check_text('backend = "local"\n') is None
    monkeypatch.setenv("MOXI_COMMS_BACKEND", "mock")
    with pytest.raises(config.ConfigError):
        config.check_text('backend = "remote"\n')


#: A codex row whose launch may warn about it, and an opencode row: neither
#: names its own state file, so a launch builds both defaults from the environment.
WARNED_ROWS = ('[codex.agents."p@x"]\ncwd = "/src"\nnetwork_access = false\n'
               '[opencode.agents."q@x"]\ncwd = "/src"\n')


class _NoEnvironment(dict):
    """A process environment any read of which fails the test."""

    def _read(self, *args):
        raise AssertionError(f"the environment was read: {args!r}")

    __getitem__ = get = __contains__ = __iter__ = __len__ = keys = items = _read
    values = copy = _read


def test_the_text_entry_reads_nothing_but_the_text(monkeypatch):
    # A path's `~` is the user's home, the one thing the core's reader reads to type it.
    text = EVERY_TABLE.replace('"~/rec.db"', '"/var/rec.db"')
    config.schema()
    monkeypatch.setattr(os, "environ", _NoEnvironment())
    assert config.check_text(text) is None
    assert config.check_text(WARNED_ROWS) is None


def test_the_text_entry_logs_no_roster_warning(caplog, capsys):
    from moxi_comms.adapters import codex_roster

    with caplog.at_level("WARNING"):
        assert config.check_text(WARNED_ROWS) is None
    assert caplog.records == [] and capsys.readouterr().err == ""
    write_config(None, WARNED_ROWS)
    with caplog.at_level("WARNING"):
        roster = codex_roster.load()
    # A launch says what a row with the network off costs, when it carries no comms
    # tools (`codex_wrapper.reach_note`); the load says nothing.
    assert caplog.records == []
    assert roster.agents["p@x"].thread_file.name == "p@x.json"


def test_the_text_entry_types_the_values_as_the_loader_does(monkeypatch):
    seen = []
    typed = config._typed

    def spy(settings, path):
        seen.append((dict(settings.values), dict(settings.sources)))
        return typed(settings, path)

    monkeypatch.setattr(config, "_typed", spy)
    # The test environment sets the expiry's override; the text entry reads no environment.
    monkeypatch.delenv("MOXI_COMMS_DELIVERY_EXPIRY")
    write_config(None, EVERY_TABLE)
    config.load()
    config.check_text(EVERY_TABLE)
    assert len(seen) == 2 and seen[0] == seen[1]


def test_the_text_entry_leaves_no_cache_entry():
    checked, cached = dict(config._checked), dict(moxi_config._cache)
    config.check_text(EVERY_TABLE)
    with pytest.raises(config.ConfigError):
        config.check_text('[credentials]\nchain = []\n')
    assert (config._checked, moxi_config._cache) == (checked, cached)


@pytest.fixture
def core_state(monkeypatch, tmp_path):
    """The core's own roots (the settings history) under the test's tmp."""
    monkeypatch.setenv("MOXI_HOME", str(tmp_path / "core"))


@pytest.mark.parametrize("argv, value, key, words", [
    (("codex.agents", '{"p@x" = {api_token = "sk-secret"}}'),
     {"p@x": {"api_token": "sk-secret"}}, "codex.agents.p@x.api_token",
     "looks like a secret"),
    (("credentials.chain", '["env", "vault"]'), ["env", "vault"], "credentials.chain",
     "not a resolver"),
    (("credentials.chain", "[]"), [], "credentials.chain", "is empty"),
    (("credentials.roles", "--add", "reader"), None, "credentials.roles",
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


def test_config_set_and_check_print_no_roster_warning(core_state, capsys, caplog):
    from moxi import cli

    path = write_config(None, WARNED_ROWS)
    with caplog.at_level("WARNING"):
        assert cli.main(["config", "set", "comms", "block", "false"]) == 0
        assert cli.main(["config", "comms", "check"]) == 0
    assert capsys.readouterr().err == "" and caplog.records == []
    assert "network_access = false" in path.read_text()


def test_config_set_and_check_pass_a_good_file_under_a_bad_variable(core_state, capsys,
                                                                    monkeypatch):
    from moxi import cli

    path = write_config(None, "block = true\n")
    monkeypatch.setenv("MOXI_COMMS_BACKEND", "bogus")
    assert cli.main(["config", "set", "comms", "credentials.roles", "--add", "pager"]) == 0
    assert cli.main(["config", "unset", "comms", "block"]) == 0
    assert cli.main(["config", "comms", "check"]) == 0
    assert capsys.readouterr().err == ""
    assert config.check_text(path.read_text()) is None
    assert "pager" in path.read_text() and "block" not in path.read_text()
