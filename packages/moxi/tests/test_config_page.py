"""A key's page fields: how the settings page shows it, checked when the schema is read."""

import copy
import json
import pickle

import pytest

from moxi import cli, config
from moxi.config import Schema, SchemaError

PAGE = """
[port]
type = "int"
default = 8460
doc = "The port the page listens on."
read = "start"
label = "Port"
line = "The page's port on this machine."
common = true
group = "Address"
unit = "port number"

[look]
type = "enum"
values = ["plain", "neon"]
default = "plain"
doc = "The page's look."
read = "use"
choices = { plain = "Plain", neon = "Midnight neon" }
pick = true

[guard]
type = "bool"
default = true
doc = "Refuse a risky act."
read = "use"
hurts = { when = "off", says = "turns this guard off", confirm = "Anything goes." }

[trust]
type = "bool"
default = false
doc = "Answer the trust question."
read = "use"
hurts = { when = "on", says = "answers for you", confirm = "Every folder is trusted.", rest = true }

[carry]
type = "list"
items = "str"
default = []
doc = "Names carried into sessions."
read = "start"
hurts = { when = "add", says = "carries a name", confirm = "Sessions see it." }

[gate]
type = "bool"
default = false
doc = "Serve the gate."
read = "use"
terminal = { on = "moxi demo gate enable", off = "moxi demo gate disable" }

[template]
type = "string"
default = "{name}"
doc = "The name template."
read = "use"
elsewhere = "The template is edited on Home."
"""


def key_spec(**fields) -> str:
    lines = ['[a]', 'doc = "d"', 'read = "use"']
    for name, value in fields.items():
        lines.append(f"{name} = {value}")
    return "\n".join(lines) + "\n"


BOOL = {"type": '"bool"', "default": "false"}
INT = {"type": '"int"', "default": "1"}
STRING = {"type": '"string"', "default": '"x"'}
ENUM = {"type": '"enum"', "values": '["a", "b"]', "default": '"a"'}
LIST = {"type": '"list"', "default": "[]"}


def test_each_page_field_is_read_onto_the_key():
    keys = Schema.parse(PAGE).keys
    port = keys["port"]
    assert (port.label, port.line, port.common, port.group, port.unit) == (
        "Port", "The page's port on this machine.", True, "Address", "port number")
    assert keys["look"].choices == {"plain": "Plain", "neon": "Midnight neon"}
    assert keys["look"].pick is True
    assert keys["guard"].hurts == {"when": "off", "says": "turns this guard off",
                                   "confirm": "Anything goes.", "rest": False}
    assert keys["trust"].hurts["rest"] is True
    assert keys["carry"].hurts["when"] == "add"
    assert keys["gate"].terminal == {"on": "moxi demo gate enable",
                                     "off": "moxi demo gate disable"}
    assert keys["template"].elsewhere == "The template is edited on Home."


def test_a_key_without_page_fields_holds_none():
    key = Schema.parse(key_spec(**INT)).keys["a"]
    assert all(getattr(key, field) is None for field in config.PAGE_FIELDS)


def test_a_key_built_by_keyword_takes_no_page_field():
    key = config.Key(name="a", value_type="int", default=1, doc="d", read="use",
                     values=None, env=None, retired=None, needs=None, items=None)
    assert all(getattr(key, field) is None for field in config.PAGE_FIELDS)
    assert key == config.Key("a", "int", 1, "d", "use")


def test_a_key_with_page_fields_copies_and_pickles():
    key = Schema.parse(PAGE).keys["guard"]
    assert copy.deepcopy(key) == key == pickle.loads(pickle.dumps(key))
    assert copy.copy(key).hurts == key.hurts


def test_page_fields_change_nothing_the_key_reads():
    plain = Schema.parse(key_spec(**INT)).keys["a"]
    shown = Schema.parse(key_spec(**INT, label='"A"', unit='"s"', common="true")).keys["a"]
    assert [getattr(plain, f) for f in plain.__slots__ if f not in config.PAGE_FIELDS] == [
        getattr(shown, f) for f in shown.__slots__ if f not in config.PAGE_FIELDS]


@pytest.mark.parametrize("fields, words", [
    ({**INT, "label": '"two\\nlines"'}, "has label 'two\\nlines', which is not one line"),
    ({**INT, "label": '""'}, "has label '', which is not one line"),
    ({**INT, "label": "3"}, "has label 3, which is not one line"),
    ({**INT, "label": '"' + "x" * 61 + '"'}, "label of 61 characters, more than 60"),
    ({**INT, "line": '"' + "x" * 161 + '"'}, "line of 161 characters, more than 160"),
    ({**INT, "group": '"' + "x" * 41 + '"'}, "group of 41 characters, more than 40"),
    ({**INT, "unit": '"' + "x" * 21 + '"'}, "unit of 21 characters, more than 20"),
    ({**STRING, "unit": '"s"'}, "has unit but is a string"),
    ({**INT, "unit": '"one two three"'}, "more than two words"),
    ({**STRING, "elsewhere": '"a\\nb"'}, "has elsewhere 'a\\nb', which is not one line"),
    ({**INT, "common": '"yes"'}, "has common 'yes', not a bool"),
    ({**ENUM, "pick": "1"}, "has pick 1, not a bool"),
    ({**BOOL, "pick": "true"}, "has pick but is a bool"),
    ({**ENUM, "choices": '{ c = "C" }'}, "has choices for 'c', which is not one of its values"),
    ({**ENUM, "choices": "{}"}, "choices that are not a table"),
    ({**ENUM, "choices": '{ a = "" }'}, "has choices '' for 'a', which is not one line"),
    ({**STRING, "choices": '{ x = "X" }'}, "has choices but is a string"),
    ({**BOOL, "hurts": '{ when = "add", says = "s", confirm = "c" }'},
     "has hurts.when 'add'"),
    ({**LIST, "hurts": '{ when = "off", says = "s", confirm = "c" }'},
     "has hurts.when 'off'"),
    ({**INT, "hurts": '{ when = "on", says = "s", confirm = "c" }'},
     "has hurts but is an int"),
    ({**BOOL, "hurts": '{ when = "on", confirm = "c" }'},
     "has hurts.says None, which is not one line"),
    ({**BOOL, "hurts": '{ when = "on", says = "s", confirm = "a\\nb" }'},
     "has hurts.confirm 'a\\nb', which is not one line"),
    ({**BOOL, "hurts": '{ when = "on", says = "s", confirm = "c", why = "w" }'},
     "unknown hurts field(s) why"),
    ({**BOOL, "hurts": '{ when = "on", says = "s", confirm = "c", rest = "yes" }'},
     "hurts.rest 'yes', not a bool"),
    ({**LIST, "hurts": '{ when = "add", says = "s", confirm = "c", rest = true }'},
     "has hurts.rest but is a list"),
    ({**BOOL, "hurts": '"off"'}, "hurts that is not a table"),
    ({**INT, "terminal": '{ on = "x on", off = "x off" }'}, "has terminal but is an int"),
    ({**BOOL, "terminal": '{ on = "x on" }'}, "terminal that is not a table of on and off"),
    ({**BOOL, "terminal": '{ on = "x on", off = "" }'},
     "has terminal.off '', which is not one line"),
])
def test_a_malformed_page_field_is_refused(fields, words):
    with pytest.raises(SchemaError) as err:
        Schema.parse(key_spec(**fields), source="pkg/settings.toml")
    assert words in err.value.problem
    assert err.value.key == "a" and "pkg/settings.toml" in str(err.value)


def test_a_retired_key_carries_no_page_field():
    with pytest.raises(SchemaError) as err:
        Schema.parse('[a]\nretired = "remove it"\nlabel = "A"\ncommon = true\n')
    assert err.value.problem == "is retired but has label, common"


def common_keys(count: int) -> str:
    return "".join(f'[k{i}]\ntype = "int"\ndefault = 1\ndoc = "d"\nread = "use"\n'
                   f"common = true\n" for i in range(count))


def test_a_schema_marks_at_most_seven_keys_common():
    assert config.COMMON_MAX == 7
    assert sum(1 for key in Schema.parse(common_keys(7)).live() if key.common) == 7
    with pytest.raises(SchemaError) as err:
        Schema.parse(common_keys(8) + '[plain]\ntype = "int"\ndefault = 1\ndoc = "d"\n'
                     'read = "use"\ncommon = false\n', source="pkg/settings.toml")
    assert err.value.problem.startswith("marks 8 keys common (k0, k1,")
    assert err.value.problem.endswith("more than 7")
    assert err.value.key is None


def test_the_cores_own_schema_passes_with_its_page_fields():
    config._core.clear()
    keys = config._core_schema().keys
    assert [name for name, key in keys.items() if key.common] == ["skills.harnesses"]
    assert keys["skills.harnesses"].label == "Skills are placed for"
    assert keys["skills.form"].label == "How skills are placed"
    assert keys["skills.form"].choices == {"auto": "auto", "link": "link", "copy": "copy"}
    assert keys["harnesses"].label == "Harness folders"
    assert all(key.label and key.line for key in keys.values())


# -- moxi config <module> show --json ------------------------------------------------------

@pytest.fixture
def paged(site, tmp_path, monkeypatch):
    """Module `demo`, whose schema is `PAGE` with a key that has no page field."""
    site.write("moxi_demo/settings.toml", PAGE + key_spec(**INT).replace("[a]", "[plain]"))
    site.module("demo", SETTINGS="'settings.toml'")
    monkeypatch.setenv("MOXI_DEMO_CONFIG_HOME", str(tmp_path / "config"))
    return site


def show_json(capsys, module="demo") -> dict:
    capsys.readouterr()
    assert cli.main(["config", module, "show", "--json"]) == 0
    return {key["name"]: key for key in json.loads(capsys.readouterr().out)}


PAGE_NULL = {"label": None, "line": None, "common": False, "group": None, "unit": None,
             "choices": None, "pick": False, "hurts": None, "terminal": None,
             "elsewhere": None}


def test_show_json_carries_each_page_field(paged, capsys):
    keys = show_json(capsys)
    port = keys["port"]
    assert {field: port[field] for field in PAGE_NULL} == {
        **PAGE_NULL, "label": "Port", "line": "The page's port on this machine.",
        "common": True, "group": "Address", "unit": "port number"}
    assert port["value"] == 8460 and port["source"] == "default"
    assert keys["look"]["choices"] == [{"value": "plain", "words": "Plain"},
                                       {"value": "neon", "words": "Midnight neon"}]
    assert keys["look"]["pick"] is True
    assert keys["guard"]["hurts"] == {"when": "off", "says": "turns this guard off",
                                      "confirm": "Anything goes.", "rest": False}
    assert keys["trust"]["hurts"]["rest"] is True
    assert keys["carry"]["hurts"]["when"] == "add"
    assert keys["gate"]["terminal"] == {"on": "moxi demo gate enable",
                                        "off": "moxi demo gate disable"}
    assert keys["template"]["elsewhere"] == "The template is edited on Home."


def test_show_json_gives_null_for_each_page_field_not_given(paged, capsys):
    plain = show_json(capsys)["plain"]
    assert {field: plain[field] for field in PAGE_NULL} == PAGE_NULL


def test_show_json_lists_choices_in_the_enums_order(site, tmp_path, monkeypatch, capsys):
    site.write("moxi_demo/settings.toml", key_spec(
        type='"enum"', values='["a", "b", "c"]', default='"a"',
        choices='{ c = "Sea", a = "Ay" }'))
    site.module("demo", SETTINGS="'settings.toml'")
    monkeypatch.setenv("MOXI_DEMO_CONFIG_HOME", str(tmp_path / "config"))
    assert show_json(capsys)["a"]["choices"] == [{"value": "a", "words": "Ay"},
                                                 {"value": "c", "words": "Sea"}]


def test_show_json_of_the_core_carries_its_page_fields(capsys):
    keys = show_json(capsys, "moxi")
    assert list(keys) == ["skills.form", "skills.harnesses", "harnesses"]
    assert keys["skills.harnesses"]["common"] is True
    assert keys["skills.form"]["choices"] == [{"value": v, "words": v}
                                              for v in ("auto", "link", "copy")]
    assert keys["harnesses"]["line"] == "An empty folder means the harness's built-in one."
