"""The settings: every key and its default, the tables a config file may hold, and the
module's own check of the file."""

from __future__ import annotations

import json

import pytest
from moxi import cli, config

from moxi_rounds import settings

#: Every key with the value it reads when the config file sets none.
DEFAULTS = {
    "flows": {},
    "tiers": ["fix", "small", "full"],
    "models": {},
    "worktree_dir": None,
    "defaults.flow": "full",
    "defaults.dir": "rounds",
    "defaults.ratify": "majors",
    "defaults.trace": False,
    "defaults.check_on": "merged",
    "defaults.idle_days": 14,
    "defaults.dormant_days": 30,
    "defaults.slice_minutes": 45,
    "defaults.sitting_at": 10,
    "projects": {},
}

TYPES = {
    "flows": "table", "tiers": "list", "models": "table", "worktree_dir": "path",
    "defaults.flow": "string", "defaults.dir": "string", "defaults.ratify": "enum",
    "defaults.trace": "bool", "defaults.check_on": "enum", "defaults.idle_days": "int",
    "defaults.dormant_days": "int", "defaults.slice_minutes": "int",
    "defaults.sitting_at": "int", "projects": "table",
}

FLOWS = """\
    [flows.design]
    steps = [ { step = "design", by = "lead", model = "second" },
              { step = "verify-design", by = "checker", recheck_model = "third" } ]

    [flows.fix]
    steps = [ { step = "build", by = "builder", model = "second" },
              { step = "staging", does = "command", before = "finish", argv = ["./stage.sh", "{tip}"], timeout_s = 900 },
              { step = "finish", by = "builder" } ]
"""

PROJECTS = """\
    [projects.proj-a]
    dir = "designs"
    trace = true
    check_on = "tip"
    into = "main"
    check = { command = "make check", timeout_s = 600 }
    finish = [ { do = "merge" }, { do = "push", remote = "origin" } ]
    shared_paths = ["*.lock"]
    keep_branch = true
    brief = ["Run only your own tests."]

    [projects.proj-b]
    sitting_at = 0
"""


def shown() -> dict[str, dict]:
    """What `moxi config rounds show --json` prints, parsed."""
    return {row["name"]: row for row in json.loads(_run(["config", "rounds", "--json"]))}


def _run(argv) -> str:
    import contextlib
    import io

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert cli.main(argv) == 0
    return out.getvalue()


# -- the keys and their defaults ---------------------------------------------------------

def test_the_schema_declares_each_key_with_its_type():
    schema = settings.schema()
    assert {key.name: key.type for key in schema.live()} == TYPES
    assert list(schema.keys) == list(TYPES)


def test_an_empty_config_reads_every_default():
    read = settings.load()
    assert {key: read[key] for key in read} == DEFAULTS
    assert {read.source(key) for key in read} == {config.DEFAULT}


def test_moxi_config_shows_every_key_at_its_default_with_no_file():
    rows = shown()
    assert list(rows) == list(DEFAULTS)
    for name, value in DEFAULTS.items():
        assert rows[name]["value"] == value, name
        assert rows[name]["source"] == "default" and rows[name]["type"] == TYPES[name]
        assert rows[name]["doc"].strip()


def test_moxi_config_prints_one_line_per_key(config_file):
    text = _run(["config", "rounds"])
    assert text.splitlines()[0] == (f"# {config_file}  (no file: every key at its "
                                    "default)")
    for line in ('tiers = ["fix", "small", "full"]    [default]',
                 "worktree_dir = (none)    [default]",
                 'defaults.flow = "full"    [default]',
                 'defaults.dir = "rounds"    [default]',
                 'defaults.ratify = "majors"    [default]',
                 "defaults.trace = false    [default]",
                 'defaults.check_on = "merged"    [default]',
                 "defaults.idle_days = 14    [default]",
                 "defaults.dormant_days = 30    [default]",
                 "defaults.slice_minutes = 45    [default]",
                 "defaults.sitting_at = 10    [default]"):
        assert line in text.splitlines(), line


def test_no_key_has_an_environment_override_and_each_is_read_at_its_use():
    for key in settings.schema().live():
        assert key.env is None and key.read == "use", key.name


def test_the_two_enums_take_the_values_they_name():
    keys = settings.schema().keys
    assert keys["defaults.ratify"].values == ("majors", "majors+minors", "off")
    assert keys["defaults.check_on"].values == ("merged", "tip")


def test_the_three_open_tables_declare_no_item_type():
    keys = settings.schema().keys
    assert [keys[name].items for name in ("flows", "models", "projects")] == [None] * 3
    assert keys["tiers"].items == "str"


# -- what the file may hold --------------------------------------------------------------

def test_the_file_sets_the_plain_keys(write_config, tmp_path):
    write_config(f"""\
        tiers = ["fix", "full"]
        worktree_dir = "{tmp_path / 'trees'}"

        [models]
        second = {{ claude = "model-two", codex = "model-b" }}

        [defaults]
        flow = "small"
        dir = "designs"
        ratify = "majors+minors"
        trace = true
        check_on = "tip"
        idle_days = 7
        dormant_days = 60
        slice_minutes = 30
        sitting_at = 0
    """)
    read = settings.load()
    assert read["tiers"] == ["fix", "full"]
    assert read["worktree_dir"] == tmp_path / "trees"
    assert read["models"] == {"second": {"claude": "model-two", "codex": "model-b"}}
    assert [read[f"defaults.{name}"] for name in (
        "flow", "dir", "ratify", "trace", "check_on", "idle_days", "dormant_days",
        "slice_minutes", "sitting_at")] == ["small", "designs", "majors+minors", True,
                                            "tip", 7, 60, 30, 0]
    assert read.source("defaults.flow") == config.FILE


def test_a_project_table_reads_back_as_it_was_written(write_config):
    write_config(PROJECTS)
    read = settings.load()
    assert read.source("projects") == config.FILE
    assert read["projects"] == {
        "proj-a": {"dir": "designs", "trace": True, "check_on": "tip", "into": "main",
                   "check": {"command": "make check", "timeout_s": 600},
                   "finish": [{"do": "merge"}, {"do": "push", "remote": "origin"}],
                   "shared_paths": ["*.lock"], "keep_branch": True,
                   "brief": ["Run only your own tests."]},
        "proj-b": {"sitting_at": 0},
    }


def test_a_flows_table_reads_back_as_it_was_written(write_config):
    write_config(FLOWS)
    flows = settings.load()["flows"]
    assert list(flows) == ["design", "fix"]
    assert flows["design"]["steps"][1] == {"step": "verify-design", "by": "checker",
                                           "recheck_model": "third"}
    assert flows["fix"]["steps"][1] == {"step": "staging", "does": "command",
                                        "before": "finish",
                                        "argv": ["./stage.sh", "{tip}"], "timeout_s": 900}


def test_moxi_config_shows_the_tables_the_file_holds(write_config):
    write_config(FLOWS + PROJECTS)
    rows = shown()
    assert rows["flows"]["source"] == rows["projects"]["source"] == "file"
    assert rows["projects"]["value"]["proj-b"] == {"sitting_at": 0}
    assert len(rows["flows"]["value"]["fix"]["steps"]) == 3
    assert rows["defaults.sitting_at"]["value"] == 10


def test_two_readings_share_no_table(write_config):
    write_config(PROJECTS)
    first = settings.load()["projects"]
    first["proj-a"]["into"] = "changed"
    assert settings.load()["projects"]["proj-a"]["into"] == "main"


def test_a_table_set_through_moxi_config_reads_back(config_file):
    config.set("rounds", "projects", {"proj-a": {"into": "main",
                                                 "check": {"command": "make check",
                                                           "timeout_s": 600}}}, by="test")
    config.set("rounds", "flows",
               {"design": {"steps": [{"step": "design", "by": "lead"}]}}, by="test")
    read = settings.load()
    assert read["projects"]["proj-a"]["check"] == {"command": "make check",
                                                    "timeout_s": 600}
    assert read["flows"] == {"design": {"steps": [{"step": "design", "by": "lead"}]}}
    assert cli.main(["config", "rounds", "check"]) == 0
    assert config_file.exists()


@pytest.mark.parametrize("text, key, words", [
    ("projects = 3\n", "projects", "is an int, not a table"),
    ("flows = []\n", "flows", "is a list, not a table"),
    ("tiers = [1]\n", "tiers", "item 1 is an int, not a string"),
    ("[defaults]\nratify = \"all\"\n", "defaults.ratify", "which it cannot be"),
    ("[defaults]\nidle_days = \"14\"\n", "defaults.idle_days", "is a string, not an int"),
    ("worktree_dir = \"trees\"\n", "worktree_dir", "is the relative path"),
    ("[defaults]\ninto = \"main\"\n", "defaults.into", "is not a setting of this module"),
    ("round = 1\n", "round", "is not a setting of this module"),
])
def test_the_schema_refuses_a_value_of_the_wrong_shape(write_config, text, key, words):
    path = write_config(text)
    with pytest.raises(config.ConfigError) as caught:
        settings.load()
    assert caught.value.key == key and words in caught.value.problem
    assert caught.value.file == str(path)
    with pytest.raises(config.ConfigError):
        settings.check_text(text)


# -- the module's own check --------------------------------------------------------------

def test_the_modules_check_accepts_a_valid_config():
    import textwrap

    assert settings.check_text("") is None
    assert settings.check_text(textwrap.dedent(FLOWS + PROJECTS)) is None


def test_moxi_config_check_passes_a_file_with_both_tables(write_config, capsys):
    path = write_config(FLOWS + PROJECTS)
    assert cli.main(["config", "rounds", "check"]) == 0
    assert capsys.readouterr().out == f"ok: {path}\n"


def refuse_a_marked_project(read):
    """A stand-in for a rule of the module's own: no project table may hold `refused`."""
    for name, table in read["projects"].items():
        if "refused" in table:
            raise config.ConfigError(read.file, f"projects.{name}.refused",
                                     "is not a key of a project table", "remove it")
    return read


def test_a_table_the_schema_takes_is_still_held_to_the_modules_check(
        write_config, monkeypatch, capsys):
    monkeypatch.setattr(settings, "check", refuse_a_marked_project)
    path = write_config("[projects.proj-a]\ninto = \"main\"\n")
    before = path.read_text()

    with pytest.raises(config.ConfigError) as caught:
        config.set("rounds", "projects", {"proj-a": {"refused": 1}}, by="test")
    assert caught.value.key == "projects.proj-a.refused"
    assert caught.value.file == str(path)
    assert path.read_text() == before and config.history("rounds") == []

    config.set("rounds", "projects", {"proj-a": {"into": "next"}}, by="test")
    assert settings.load()["projects"] == {"proj-a": {"into": "next"}}

    path.write_text("[projects.proj-b]\nrefused = true\n")
    assert cli.main(["config", "rounds", "check"]) == 1
    assert "key 'projects.proj-b.refused'" in capsys.readouterr().err
    with pytest.raises(config.ConfigError):
        settings.load()
