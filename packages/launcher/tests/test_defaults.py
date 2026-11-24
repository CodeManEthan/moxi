"""The launch defaults as a table `moxi config` shows and changes (`moxi_launcher.defaults`):
the rows and values offered on this machine, the keys that need a fix, the row's own check,
the lines each row carries, and the writer's key placement over files shaped like a user's
`presets.toml`."""

from __future__ import annotations

import dataclasses
import os
import sys
from types import MappingProxyType

import pytest
from moxi import config
from moxi.config import ConfigError
from moxi.paths import Layout

from moxi_launcher import catalog, defaults, presets
from moxi_launcher.defaults import Table


def only(*names):
    """An `available` answering yes for `names` only."""
    return lambda name: name in names


ALL = lambda name: True  # noqa: E731
NONE = only()


def write(text: str) -> None:
    file = presets.path()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(text.encode("utf-8"))


def text() -> str:
    return presets.path().read_bytes().decode("utf-8")


def rows(available=NONE) -> dict[str, dict]:
    return {item["name"]: item for item in Table(available=available).keys(os.environ)}


def change(name, op, value=None, available=NONE):
    return Table(available=available).change(name, op, value, by="test", env=os.environ)


@pytest.fixture
def conditions(monkeypatch):
    """Set the two conditions a row or a value may name: `cache-timer` and `slots-here`."""
    def hold(*, cache_timer: bool = True, slots_here: bool = True):
        monkeypatch.setattr(catalog, "session_env_readable", lambda: cache_timer)
        monkeypatch.setattr(catalog, "slots_here", lambda: slots_here)
    hold()
    return hold


# -- the rows -----------------------------------------------------------------------------

def test_every_offered_row_is_listed_once_and_the_hook_names_itself(conditions):
    listed = [item["name"] for item in Table(available=ALL).keys(os.environ)]
    assert len(listed) == len(set(listed))
    assert set(listed) == set(defaults.offered_rows(ALL))
    assert defaults.TABLE.title == "Launch defaults"
    assert defaults.TABLE.file(os.environ) == presets.path(os.environ)
    assert "A relaunch or resume keeps what the session launched with" in defaults.LINE
    assert defaults.APPLIES.startswith("Applies to sessions launched after this.")


def test_owned_and_per_seat_rows_are_absent_unless_the_file_sets_them(conditions):
    listed = rows(ALL)
    for name in ("session_id", "resume", "takes_policy_directors", "update_check"):
        assert name not in listed
    write('[defaults]\nsession_id = "x"\ntakes_policy_directors = false\n')
    listed = rows(ALL)
    assert listed["session_id"]["clear_only"] is True
    assert listed["takes_policy_directors"]["clear_only"] is True


def test_a_row_whose_module_is_absent_or_whose_condition_fails_is_left_out(conditions):
    assert "keep_warm" not in rows(only("turns"))
    assert "keep_warm" in rows(only("turns", "cache"))
    conditions(cache_timer=False)
    assert "keep_warm" not in rows(only("turns", "cache"))
    assert "recycle_on_expiry" not in rows(only("turns", "cache"))


def test_a_held_value_for_a_row_not_offered_is_kept_and_not_listed(conditions):
    conditions(cache_timer=False)
    here = only("turns", "cache")
    write('[defaults]\nkeep_warm = "on"\n')
    assert "keep_warm" not in rows(here)
    assert change("effort", "set", "high", here) == (True, [])
    assert presets.read().defaults == {"keep_warm": "on", "effort": "high"}
    with pytest.raises(ConfigError) as caught:
        change("keep_warm", "set", "off", here)
    assert caught.value.problem == ("keep_warm needs cache to read session environments "
                                    "on this system")
    # Not listed, so not cleared here: a hand edit can.
    with pytest.raises(ConfigError) as caught:
        change("keep_warm", "unset", None, here)
    assert "needs cache" in caught.value.problem
    assert presets.read().defaults["keep_warm"] == "on"


def test_relaunch_is_offered_only_where_slots_run(conditions):
    here = only("turns", "usage", "slots")
    assert rows(here)["recycle_at_limit"]["values"] == ["off", "relaunch", "compact"]
    conditions(slots_here=False)
    assert rows(here)["recycle_at_limit"]["values"] == ["off", "compact"]
    assert rows(only("turns", "usage"))["recycle_at_limit"]["values"] == ["off", "compact"]


def test_a_held_relaunch_where_slots_cant_run_is_refused_not_warned(conditions):
    conditions(slots_here=False)
    write('[defaults]\nrecycle_at_limit = "relaunch"\n')
    row = rows(only("turns", "usage", "slots"))["recycle_at_limit"]
    assert row["value"] == "relaunch" and not row.get("clear_only")
    assert row["refused"] == ("relaunch at limit needs slots and a slot launch; launch it "
                              "in a slot (--target slot) where tmux slots run, or recycle "
                              "with compact.")
    assert row["warning"] is None
    with pytest.raises(ConfigError) as caught:
        change("recycle_at_limit", "set", "relaunch", only("turns", "usage", "slots"))
    assert caught.value.problem == "relaunch at limit needs slots and a slot launch"


def test_keys_launches_refuse_are_listed_first_to_clear(conditions):
    write('[defaults]\neffort = "high"\nsession_id = "x"\ntakes_policy_directors = false\n'
          'typo_row = 3\n')
    items = Table(available=ALL).keys(os.environ)
    first = items[:3]
    assert [item["name"] for item in first] == ["session_id", "takes_policy_directors",
                                                "typo_row"]
    for item in first:
        assert item["group"] == defaults.NEEDS_FIX and item["clear_only"] is True
        assert item["source"] == "file" and item["refused"]
    assert [item["type"] for item in first] == ["string", "bool", "int"]
    assert first[0]["refused"] == ("session_id is set by the launcher itself; leave "
                                   "session_id out.")
    assert first[1]["refused"].startswith("takes_policy_directors can't be set in "
                                          "[defaults]")
    assert first[2]["refused"] == ("typo_row is not a launcher row or a row of any "
                                   "harness; moxi launcher catalog lists the rows.")
    # Each clear answers the lines of the keys still held.
    assert change("session_id", "unset", available=ALL) == (
        True, [first[1]["refused"][:-1], first[2]["refused"][:-1]])
    assert change("takes_policy_directors", "unset", available=ALL) == (
        True, [first[2]["refused"][:-1]])
    assert change("typo_row", "unset", available=ALL) == (True, [])
    assert presets.read().defaults == {"effort": "high"}


def test_a_wrong_typed_live_row_keeps_its_control_with_the_refusal(conditions):
    write('[defaults]\neffort = 3\n')
    row = rows()["effort"]
    assert not row.get("clear_only") and row["group"] == "model"
    assert row["values"] == ["low", "medium", "high", "xhigh", "max"]
    assert row["refused"] == ("effort is an int, not a string; set effort to a string "
                              "('low', 'medium', 'high', 'xhigh', 'max').")
    assert change("effort", "set", "low") == (True, [])
    assert rows()["effort"]["refused"] is None


def test_a_name_both_harnesses_have_is_one_row_with_each_doc():
    for name in ("model", "add_dir"):
        claude, codex = catalog.load("claude")[name], catalog.load("codex")[name]
        assert (claude.type, claude.default) == (codex.type, codex.default)
    listed = rows()
    assert listed["add_dir"]["line"] == (
        "Claude and Codex launches both read it. Claude: Another directory the session's "
        "tools may reach; may be given more than once. Codex: Another directory the "
        "session may write to; may be given more than once.")
    assert listed["model"]["line"].startswith(defaults.BOTH + " Claude: The model: ")


def test_the_six_rows_with_no_group_sit_under_start(conditions):
    items = Table(available=ALL).keys(os.environ)
    assert all(item["group"] for item in items)
    assert {item["name"] for item in items if item["group"] == defaults.START} == {
        "cwd", "project", "brief", "brief_file", "target", "slot"}
    groups = [item["group"] for item in items]
    assert groups == sorted(groups, key=defaults.GROUPS.index)


def test_a_row_says_its_label_value_source_and_shipped_default():
    write('[defaults]\npermission_mode = "plan"\n')
    row = rows()["permission_mode"]
    assert (row["label"], row["value"], row["source"], row["default"]) == (
        "Permission mode", "plan", "file", "default")
    assert rows()["effort"]["source"] == "default"


# -- the writer ---------------------------------------------------------------------------

OPERATOR = ('[defaults]\n'
            'permission_mode = "bypassPermissions"  # mine\n'
            'remote_control = true\n'
            '\n'
            '# Seats, one per agent.\n'
            '# The planner first.\n'
            '[seat.planner]\n'
            'harness = "claude"\n'
            'identity = "planner"\n'
            '\n'
            '[project.moxi]\n'
            'harness = "codex"\n')


def test_the_writer_keeps_every_other_byte_of_a_users_file():
    write(OPERATOR)
    assert change("permission_mode", "set", "plan") == (True, [])
    now = OPERATOR.replace('"bypassPermissions"  # mine', '"plan"  # mine')
    assert text() == now
    # A new key goes straight after the last key, above the comment block.
    change("effort", "set", "high")
    now = now.replace('remote_control = true\n', 'remote_control = true\neffort = "high"\n')
    assert text() == now
    change("remote_control", "unset")
    now = now.replace('remote_control = true\n', '')
    assert text() == now
    change("permission_mode", "unset")
    change("effort", "unset")
    now = now.replace('permission_mode = "plan"  # mine\neffort = "high"\n', '')
    assert text() == now and now.startswith("[defaults]\n\n# Seats")
    # The table holds no key: a new one goes straight under its header.
    change("model", "set", "opus")
    assert text() == now.replace("[defaults]\n", '[defaults]\nmodel = "opus"\n', 1)


def test_a_file_without_defaults_gets_the_table_at_its_end():
    write('[seat.a]\nharness = "claude"\n')
    change("effort", "set", "low")
    assert text() == '[seat.a]\nharness = "claude"\n\n[defaults]\neffort = "low"\n'


def test_a_missing_file_is_made(tmp_path):
    assert not presets.path().exists()
    change("effort", "set", "low")
    assert text() == '[defaults]\neffort = "low"\n'
    if sys.platform != "win32":
        assert presets.path().stat().st_mode & 0o777 == 0o600


def test_an_inline_defaults_is_edited_in_place():
    write('defaults = {effort = "low"}\n\n[seat.a]\nharness = "claude"\n')
    change("model", "set", "opus")
    change("effort", "unset")
    assert text() == 'defaults = {model = "opus"}\n\n[seat.a]\nharness = "claude"\n'


def test_values_are_read_as_their_row_types(tmp_path):
    extra_dir = str(tmp_path / "more")
    change("add_dir", "set", f'["{extra_dir.replace(chr(92), chr(92) * 2)}"]')
    change("remote_control", "set", "true")
    assert presets.read().defaults == {"add_dir": [extra_dir], "remote_control": True}
    change("remote_control", "set", "laptop")
    assert presets.read().defaults["remote_control"] == "laptop"
    assert change("remote_control", "set", "laptop") == (False, [])
    assert change("effort", "unset") == (False, [])
    change("instructions_file", "set", "~/agents/planner.md", ALL)
    assert presets.read().defaults["instructions_file"] == "~/agents/planner.md"


# -- the row's own check ------------------------------------------------------------------

@pytest.mark.parametrize(("name", "value", "problem"), [
    ("effort", "huge", "effort cannot be 'huge'"),
    ("add_dir", "not a list", "cannot be 'not a list', which is not one TOML list"),
    ("add_dir", '["relative/path"]', "add_dir is the relative path 'relative/path'"),
    ("takes_policy_directors", "false", "takes_policy_directors can't be set in "
                                        "[defaults]: one line in [defaults] would opt every "
                                        "seat out of comms' policy directors"),
    ("session_id", "x", "session_id is set by the launcher itself"),
    ("nothing_like_it", "x", "nothing_like_it is not a launcher row or a row of any "
                             "harness"),
])
def test_a_value_its_row_refuses_is_not_written(name, value, problem, conditions):
    write(OPERATOR)
    with pytest.raises(ConfigError) as caught:
        change(name, "set", value, ALL)
    assert caught.value.key == name
    assert caught.value.problem.startswith(problem)
    assert text() == OPERATOR


def test_a_key_that_can_only_be_cleared_is_refused_a_value():
    write('[defaults]\nsession_id = "x"\n')
    with pytest.raises(ConfigError) as caught:
        change("session_id", "set", "y")
    assert caught.value.problem == "session_id is set by the launcher itself"


def _launcher_with(monkeypatch, name, **changes):
    real = catalog.launcher()
    rows = dict(real.rows)
    rows[name] = dataclasses.replace(rows[name], **changes)
    fake = dataclasses.replace(real, rows=MappingProxyType(rows))
    monkeypatch.setattr(catalog, "launcher", lambda: fake)
    return rows[name]


def test_a_retired_row_is_listed_to_clear_and_never_set(monkeypatch):
    _launcher_with(monkeypatch, "limit_at", retired="remove limit_at")
    write('[defaults]\nlimit_at = "1x"\n')
    row = rows(ALL)["limit_at"]
    assert row["clear_only"] is True and row["group"] == defaults.NEEDS_FIX
    assert "limit_at is retired; remove limit_at" in (row["refused"] or row["warning"])
    with pytest.raises(ConfigError) as caught:
        change("limit_at", "set", "1x", ALL)
    assert caught.value.problem == "limit_at is retired"
    assert change("limit_at", "unset", available=ALL) == (True, [])


@dataclasses.dataclass(frozen=True)
class _RetiresCompact(catalog.Row):
    """A `recycle_at_limit` whose `compact` is retired, as a catalog that retires a value
    has it."""

    def check(self, value):
        if value == "compact":
            raise catalog.BadValue(self.name, f"{self.name} compact is retired",
                                   "set recycle_at_limit off")
        return super().check(value)


def test_a_retired_value_is_refused_by_the_rows_own_check_alone(monkeypatch, conditions):
    real = catalog.launcher()
    rows_ = dict(real.rows)
    old = rows_["recycle_at_limit"]
    rows_["recycle_at_limit"] = _RetiresCompact(**{f.name: getattr(old, f.name)
                                                   for f in dataclasses.fields(old)})
    monkeypatch.setattr(catalog, "launcher", lambda: dataclasses.replace(
        real, rows=MappingProxyType(rows_)))
    # Even where [defaults]' own check lets a retired value through, the write's call of
    # the row's check refuses it.
    monkeypatch.setattr(presets, "defaults_problems", lambda table, available=None: [])
    with pytest.raises(ConfigError) as caught:
        change("recycle_at_limit", "set", "compact", ALL)
    assert caught.value.problem == "recycle_at_limit compact is retired"
    assert not presets.path().exists()


# -- what a value meets -------------------------------------------------------------------

RECYCLES = only("turns", "usage", "slots")
REFUSED_BARE = ("a launch with no preset is refused: relaunch at limit needs slots and a "
                "slot launch; launch it in a slot (--target slot) where tmux slots run, or "
                "recycle with compact")


def test_a_relaunch_with_no_slot_target_is_written_with_its_warning(conditions):
    changed, warnings = change("recycle_at_limit", "set", "relaunch", RECYCLES)
    assert changed and warnings == [REFUSED_BARE]
    row = rows(RECYCLES)["recycle_at_limit"]
    assert row["warning"] == REFUSED_BARE + "." and row["refused"] is None
    assert presets.SCRATCH_RECYCLE_OFF in row["line"]


@pytest.mark.parametrize("order", [("recycle_at_limit", "target"),
                                   ("target", "recycle_at_limit")])
def test_relaunch_and_a_slot_target_save_in_either_order(order, conditions):
    values = {"recycle_at_limit": "relaunch", "target": "slot"}
    for name in order:
        change(name, "set", values[name], RECYCLES)
    assert presets.read().defaults == values
    row = rows(RECYCLES)["recycle_at_limit"]
    assert row["refused"] is None
    # A Claude launch with no preset relaunches itself; a Codex one can't.
    assert row["warning"] == ("recycle_at_limit relaunch does nothing on a Codex launch "
                              "with no preset: it is a codex launch, and only a Claude "
                              "session can be exited and relaunched; launch the seat on "
                              "Claude.")


def test_clearing_a_slot_target_answers_the_relaunch_line_it_brings_back(conditions):
    change("recycle_at_limit", "set", "relaunch", RECYCLES)
    change("target", "set", "slot", RECYCLES)
    changed, lines = change("target", "unset", None, RECYCLES)
    assert changed and lines == [REFUSED_BARE]
    assert rows(RECYCLES)["recycle_at_limit"]["warning"] == REFUSED_BARE + "."
    # A clear that leaves nothing to say answers no line.
    assert change("recycle_at_limit", "unset", None, RECYCLES) == (True, [])


def test_two_places_are_written_and_both_rows_refused(tmp_path):
    change("project", "set", "moxi")
    changed, warnings = change("cwd", "set", str(tmp_path))
    assert changed and warnings == ["cwd and project each name where the session starts; "
                                    "keep one of them"]
    listed = rows()
    for name in ("cwd", "project"):
        assert listed[name]["refused"] == warnings[0] + "."
        assert not listed[name].get("clear_only")
    change("project", "unset")
    assert rows()["cwd"]["refused"] is None


def test_a_file_with_an_unrelated_bad_preset_is_still_edited():
    write('[defaults]\neffort = "low"\n\n[seat.bad]\npermission_mode = "nope"\n')
    assert change("effort", "set", "high") == (True, [])
    assert text() == '[defaults]\neffort = "high"\n\n[seat.bad]\npermission_mode = "nope"\n'


def test_a_hand_edit_made_since_the_read_is_kept():
    write(OPERATOR)
    rows()
    edited = OPERATOR.replace("remote_control = true", 'remote_control = "laptop"') \
        + '\n[seat.coder]\nharness = "codex"\n'
    write(edited)
    change("effort", "set", "low")
    assert text() == edited.replace('remote_control = "laptop"\n',
                                    'remote_control = "laptop"\neffort = "low"\n')


def test_a_file_that_is_not_toml_is_refused_and_left_alone():
    write('[defaults\neffort = "low"\n')
    with pytest.raises(ValueError):
        change("effort", "set", "high")
    assert text() == '[defaults\neffort = "low"\n'
    with pytest.raises(ConfigError):
        rows()
    with pytest.raises(ConfigError) as caught:
        config.change_table("launcher", "defaults.effort", "set", "high", by="test")
    assert "is not valid TOML" in caught.value.problem
    assert text() == '[defaults\neffort = "low"\n'


def test_a_broken_config_toml_leaves_the_table_listed_and_written():
    config_file = Layout("launcher").config_dir() / "config.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("this is [not toml\n", encoding="utf-8")
    assert "effort" in rows()
    assert config.change_table("launcher", "defaults.effort", "set", "high",
                               by="test") == (True, [])
    assert presets.read().defaults == {"effort": "high"}
    assert config_file.read_text(encoding="utf-8") == "this is [not toml\n"
