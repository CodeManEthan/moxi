"""Several changes in one write: `moxi.config.set_many`, and `draft_text`, the same step
with no lock and no write."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import textwrap

import pytest

from moxi import config, files
from moxi.config import ConfigChanged, ConfigError

SCHEMA = """
[context.warn_at]
type = "int"
default = 50
doc = "Yellow from this percent."
read = "use"

[context.alert_at]
type = "int"
default = 70
doc = "Red from this percent."
read = "use"

[name]
type = "string"
default = "demo"
doc = "A display name."
read = "use"

[show]
type = "list"
items = "str"
default = ["dir"]
doc = "The items, in order."
read = "use"

[rows]
type = "table"
items = "bool"
default = {}
doc = "A switch per row."
read = "use"
"""

#: The module's own check: alert_at must sit above warn_at, by at most 30, so moving
#: both from 50/70 to 90/95 is refused one key at a time and taken together.
CHECK = '''
import tomllib


def check(text):
    data = tomllib.loads(text)
    context = data.get("context", {})
    warn, alert = context.get("warn_at", 50), context.get("alert_at", 70)
    if not 0 < alert - warn <= 30:
        raise ValueError(f"alert_at {alert} must be above warn_at {warn} by 1 to 30")
    if data.get("name") == "kept":
        from moxi.config import ConfigError
        raise ConfigError(None, "name", "is a name kept back", "pick another name")
'''

COMMENTED = textwrap.dedent("""\
    # my line
    name = "mine"  # shown in the corner

    [context]
    # a yellow band first
    warn_at = 50
    alert_at = 70
""")


@pytest.fixture
def demo(site, tmp_path, monkeypatch):
    """Module `demo` with SCHEMA and CHECK, its config root and the core's state in the
    test's tmp. Returns the config file's path, which does not exist yet."""
    site.write("moxi_demo/settings.toml", SCHEMA)
    site.write("moxi_demo/check.py", CHECK)
    site.module("demo", SETTINGS="'settings.toml'", CONFIG_CHECK="'moxi_demo.check:check'")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MOXI_DEMO_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("MOXI_HOME", str(tmp_path / "core"))
    return tmp_path / "config" / "demo" / "config.toml"


@pytest.fixture
def writes(monkeypatch) -> list:
    """Each path `moxi.files.atomic_write` writes, in order."""
    seen = []
    real = files.atomic_write

    def counted(path, data, **kwargs):
        seen.append(path)
        real(path, data, **kwargs)

    monkeypatch.setattr(files, "atomic_write", counted)
    return seen


def made(path, data: bytes | str = COMMENTED):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    return path


def stamp(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def refused(call, *args, **kwargs) -> ConfigError:
    with pytest.raises(ConfigError) as caught:
        call(*args, **kwargs)
    return caught.value


RAISED = [("context.warn_at", 90), ("context.alert_at", 95)]


# -- set_many ---------------------------------------------------------------------------

def test_two_bound_keys_move_together_where_either_alone_is_refused(demo, writes):
    made(demo)
    for key, value in RAISED:
        refused(config.set_value, "demo", key, value, by="cli")
        refused(config.set_many, "demo", [(key, value)], by="ui")
    assert demo.read_text() == COMMENTED and writes == []
    assert config.set_many("demo", RAISED, by="ui") == []
    assert len(writes) == 1
    text = demo.read_text()
    assert "warn_at = 90" in text and "alert_at = 95" in text
    assert "# a yellow band first" in text and "# shown in the corner" in text


@pytest.mark.parametrize("changes, key, words", [
    # the module's check refuses the whole draft: no one key to name
    ([("context.warn_at", 90), ("name", "x")], None, "alert_at 70 must be above"),
    # one change: the refusal names it, as `set_value`'s does
    ([("context.warn_at", 90)], "context.warn_at", "alert_at 70 must be above"),
    # the module's own ConfigError keeps its key
    ([("name", "kept"), ("context.warn_at", 60)], "name", "kept back"),
    # a value refused before anything is applied
    ([("name", "x"), ("context.warn_at", "high")], "context.warn_at", "not an int"),
    ([("name", "x"), ("nmae", "y")], "nmae", "not a setting of demo"),
    ([("show", "dir")], "show", "cannot be a string"),
])
def test_a_refused_draft_writes_nothing(demo, writes, changes, key, words):
    made(demo)
    exc = refused(config.set_many, "demo", changes, by="ui")
    assert type(exc) is ConfigError and exc.key == key and words in exc.problem
    assert exc.file == str(demo)
    assert demo.read_text() == COMMENTED and writes == []
    assert config.history("demo") == []


def test_a_draft_the_schema_reader_refuses_writes_nothing(demo, writes):
    hand = "bogus = 1\n" + COMMENTED
    made(demo, hand)
    exc = refused(config.set_many, "demo", RAISED, by="ui")
    assert exc.key == "bogus" and "not a setting" in exc.problem
    assert demo.read_text() == hand and writes == []
    assert config.history("demo") == []


def test_a_stale_base_writes_nothing(demo, writes):
    made(demo)
    _, base = config.draft_text("demo", RAISED)
    moved = COMMENTED.replace('"mine"', '"theirs"')
    demo.write_text(moved)
    exc = refused(config.set_many, "demo", RAISED, by="ui", base=base)
    assert isinstance(exc, ConfigChanged) and exc.exit_code == 75
    assert "changed since it was read" in exc.problem and exc.file == str(demo)
    assert demo.read_text() == moved and writes == []
    assert config.history("demo") == []


def test_a_base_that_still_holds_writes(demo):
    made(demo)
    _, base = config.draft_text("demo", [])
    assert base == stamp(COMMENTED.encode())
    config.set_many("demo", RAISED, by="ui", base=base)
    assert "warn_at = 90" in demo.read_text()


def test_a_missing_files_stamp_is_that_of_no_bytes(demo):
    text, base = config.draft_text("demo", [])
    assert (text, base) == ("", stamp(b""))
    config.set_many("demo", [("name", "new")], by="ui", base=base)
    assert demo.read_text() == 'name = "new"\n'


def test_one_history_line_per_key_each_naming_who(demo):
    made(demo)
    config.set_many("demo", RAISED + [("context.warn_at", 91)], by="ui")
    records = config.history("demo")
    assert [(r["module"], r["key"], r["op"], r["by"]) for r in records] == [
        ("demo", "context.warn_at", "set", "ui"),
        ("demo", "context.alert_at", "set", "ui")]
    assert all(set(r) == {"module", "key", "op", "by", "at"} for r in records)
    assert "warn_at = 91" in demo.read_text()


def test_no_changes_write_nothing_and_make_no_file(demo, writes, tmp_path):
    assert config.set_many("demo", [], by="ui") == []
    assert writes == [] and not (tmp_path / "config").exists()
    assert config.history("demo") == []


def test_set_many_needs_a_by(demo):
    exc = refused(config.set_many, "demo", RAISED, by=" ")
    assert "no one named" in exc.problem
    assert not demo.exists()


def test_one_key_writes_the_bytes_set_writes(demo):
    made(demo)
    config.set_value("demo", "rows.backup", "on", by="cli")
    by_set = demo.read_bytes()
    made(demo)
    config.set_many("demo", [("rows.backup", "on")], by="ui")
    assert demo.read_bytes() == by_set


def test_set_many_returns_one_list_of_the_units_each_key_applied(demo, monkeypatch):
    asked = []

    def applied(module, key, **kwargs):
        asked.append(key)
        return [f"unit for {key}"]

    monkeypatch.setattr(config, "apply_units", applied)
    assert config.set_many("demo", RAISED, by="ui") == [
        "unit for context.warn_at", "unit for context.alert_at"]
    assert asked == ["context.warn_at", "context.alert_at"]


HOLDER = textwrap.dedent("""
    import sys, time
    from moxi import files
    with files.lock(sys.argv[1]):
        print("held", flush=True)
        time.sleep(30)
""")


def test_a_held_lock_refuses_with_exit_75_and_is_not_a_changed_file(demo, monkeypatch,
                                                                     writes):
    monkeypatch.setattr(config, "LOCK_TIMEOUT", 0.2)
    made(demo)
    lock = demo.with_name("config.toml.lock")
    proc = subprocess.Popen([sys.executable, "-c", HOLDER, str(lock)],
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "held"
        exc = refused(config.set_many, "demo", RAISED, by="ui")
    finally:
        proc.kill()
        proc.wait()
    assert exc.exit_code == 75 and not isinstance(exc, ConfigChanged)
    assert str(proc.pid) in exc.problem
    assert demo.read_text() == COMMENTED and writes == []


def test_a_refused_value_is_neither_a_changed_file_nor_a_held_lock(demo):
    exc = refused(config.set_many, "demo", [("context.warn_at", 90)], by="ui")
    assert exc.exit_code == 2 and not isinstance(exc, ConfigChanged)


# -- draft_text -------------------------------------------------------------------------

@pytest.mark.parametrize("before", [None, COMMENTED, COMMENTED.replace("\n", "\r\n")],
                         ids=["missing", "commented", "crlf"])
def test_the_draft_is_what_set_many_then_writes_byte_for_byte(demo, before):
    if before is not None:
        made(demo, before)
    changes = RAISED + [("show", ["dir", "row", "model"]), ("rows.backup", True)]
    text, base = config.draft_text("demo", changes)
    assert base == stamp(b"" if before is None else before.encode())
    config.set_many("demo", changes, by="ui", base=base)
    assert demo.read_bytes() == text.encode("utf-8")


@pytest.mark.parametrize("changes", [
    [("context.warn_at", 90), ("name", "x")],
    [("context.warn_at", 90)],
    [("name", "kept"), ("context.warn_at", 60)],
    [("name", "x"), ("context.warn_at", "high")],
    [("nmae", "y")],
])
def test_the_draft_refuses_what_set_many_refuses(demo, changes):
    made(demo)
    drafted = refused(config.draft_text, "demo", changes)
    saved = refused(config.set_many, "demo", changes, by="ui")
    assert (type(drafted), drafted.key, drafted.problem, drafted.exit_code) == (
        type(saved), saved.key, saved.problem, saved.exit_code)


def test_the_draft_over_a_stale_base_is_refused(demo):
    made(demo)
    _, base = config.draft_text("demo", [])
    demo.write_text(COMMENTED + "\n")
    exc = refused(config.draft_text, "demo", RAISED, base=base)
    assert isinstance(exc, ConfigChanged) and exc.exit_code == 75


def test_a_file_that_is_not_toml_is_refused_alike(demo):
    made(demo, "name = \n")
    drafted = refused(config.draft_text, "demo", RAISED)
    saved = refused(config.set_many, "demo", RAISED, by="ui")
    one = refused(config.set_value, "demo", "name", "x", by="cli")
    assert "is not valid TOML" in drafted.problem
    assert drafted.problem == saved.problem == one.problem
    assert drafted.key is saved.key is one.key is None


def test_the_draft_writes_nothing_and_makes_no_file_or_dir(demo, writes, tmp_path):
    text, _ = config.draft_text("demo", RAISED)
    assert "warn_at = 90" in text
    assert writes == [] and not (tmp_path / "config").exists()
    made(demo)
    config.draft_text("demo", RAISED)
    assert demo.read_text() == COMMENTED and writes == []
    assert sorted(p.name for p in demo.parent.iterdir()) == ["config.toml"]
    assert config.history("demo") == []
