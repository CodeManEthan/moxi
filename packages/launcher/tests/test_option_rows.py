"""The launch option rows for acts on a launched session (keep-warm, recycle on expiry,
recycle at limit, the limit mark) and the catalog properties they need: several modules,
a condition on the system and harness, a value's own needs, and a row offered only with
another, each refused with its own words on every path that resolves a launch."""

from __future__ import annotations

import pytest

from moxi_launcher import catalog, presets
from moxi_launcher.catalog import CatalogError, NeedsModule


def everything(module: str) -> bool:
    return True


def without(*absent):
    return lambda module: module not in absent


@pytest.fixture
def readable(monkeypatch):
    monkeypatch.setattr(catalog, "session_env_readable", lambda: True)
    monkeypatch.setattr(catalog, "slots_here", lambda: True)


def test_the_rows_their_values_and_needs():
    rows = catalog.launcher()
    assert rows["keep_warm"].values == ("off", "on") and rows["keep_warm"].default == "off"
    assert rows["recycle_on_expiry"].values == ("off", "clear", "compact")
    assert rows["recycle_at_limit"].values == ("off", "relaunch", "compact")
    assert rows["limit_at"].values == ("1x", "1.5x", "2x") and rows["limit_at"].default == "1x"
    assert rows["keep_warm"].required == ("turns", "cache")
    assert rows["recycle_at_limit"].required == ("turns", "usage")
    assert rows["limit_at"].offered_with == "recycle_at_limit"
    assert rows["keep_warm"].key.needs == "turns"


def test_a_row_needing_two_modules_names_the_one_missing(readable):
    row = catalog.launcher()["keep_warm"]
    assert row.offered(everything, "claude")
    assert row.unoffered(without("cache"), "claude")[0] == "keep_warm needs cache"


def test_the_cache_rows_are_offered_for_claude_only(readable):
    assert "keep_warm" in catalog.offered("claude", everything)
    assert "keep_warm" not in catalog.offered("codex", everything)
    assert "recycle_on_expiry" not in catalog.offered("codex", everything)
    # compact at the limit needs no cache timer: offered for Codex too.
    assert "recycle_at_limit" in catalog.offered("codex", everything)
    why = catalog.launcher()["keep_warm"].unoffered(everything, "codex")[0]
    assert "cache times only claude sessions" in why


def test_the_cache_rows_need_session_environments_read_here(monkeypatch):
    monkeypatch.setattr(catalog, "session_env_readable", lambda: False)
    row = catalog.launcher()["recycle_on_expiry"]
    assert row.unoffered(everything, "claude")[0] == (
        "recycle_on_expiry needs cache to read session environments on this system")
    with pytest.raises(NeedsModule, match="read session environments"):
        catalog.parse(["--recycle-on-expiry", "clear"], "claude", available=everything)


def test_relaunch_at_limit_needs_slots_and_a_slot_launch(readable):
    values = {"recycle_at_limit": "relaunch", "target": "terminal"}
    with pytest.raises(NeedsModule, match="relaunch at limit needs slots and a slot launch"):
        catalog.refuse_values("claude", values, available=everything)
    catalog.refuse_values("claude", {**values, "target": "slot"}, available=everything)
    with pytest.raises(NeedsModule):
        catalog.refuse_values("claude", {**values, "target": "slot"},
                              available=without("slots"))


def test_relaunch_at_limit_is_refused_where_slots_cant_run(monkeypatch):
    monkeypatch.setattr(catalog, "slots_here", lambda: False)
    values = {"recycle_at_limit": "relaunch", "target": "slot"}
    with pytest.raises(NeedsModule, match="relaunch at limit"):
        catalog.refuse_values("claude", values, available=everything)
    catalog.refuse_values("claude", {**values, "recycle_at_limit": "compact"},
                          available=everything)


def test_limit_at_needs_recycle_at_limit_when_chosen(readable):
    with pytest.raises(NeedsModule, match="limit_at needs recycle_at_limit"):
        catalog.refuse_values("claude", {"limit_at": "2x", "recycle_at_limit": "off"},
                              chosen={"limit_at"}, available=everything)
    # Left at its default it is never refused.
    catalog.refuse_values("claude", {"limit_at": "1x", "recycle_at_limit": "off"},
                          available=everything)
    catalog.refuse_values("claude", {"limit_at": "2x", "recycle_at_limit": "compact"},
                          chosen={"limit_at"}, available=everything)


def test_resolve_refuses_a_typed_value_that_breaks_a_rule(tmp_path, readable):
    request = presets.Request(typed={"recycle_at_limit": "relaunch"}, cwd=str(tmp_path))
    with pytest.raises(NeedsModule, match="relaunch at limit"):
        presets.resolve(request, available=everything)


def test_presets_check_refuses_a_preset_with_the_rules_words(tmp_path, readable):
    path = tmp_path / "presets.toml"
    path.write_text('[seat.a]\nlimit_at = "2x"\n\n[seat.b]\nharness = "codex"\n'
                    'keep_warm = "on"\n', encoding="utf-8")
    found = {(p.where, p.key): p for p in presets.check(path, available=everything)}
    assert found["seat.a", "limit_at"].problem == "limit_at needs recycle_at_limit"
    assert "cache times only claude" in found["seat.b", "keep_warm"].problem
    assert found["seat.b", "keep_warm"].code == "needs-module"


@pytest.mark.parametrize("spec", [
    'condition = "nope"\n',
    'requires = ["turns", "turns"]\n',
    'offered_with = "nothing"\n',
    '[x.when.zz]\nrefused = "r"\nhint = "h"\n',
    '[x.when.a]\nrequires = "slots"\n',
])
def test_the_catalog_refuses_malformed_new_fields(spec):
    text = ('[x]\ntype = "enum"\ndefault = "a"\nvalues = ["a", "b"]\ndoc = "d"\n' + spec)
    with pytest.raises(CatalogError):
        catalog.parse_text(text, harness=catalog.LAUNCHER)


# -- the stamps and the grant -------------------------------------------------------------

from moxi_launcher.compose import common  # noqa: E402


@pytest.mark.parametrize(("values", "granted"), [
    ({}, ("exit",)),
    ({"recycle_on_expiry": "clear"}, ("exit", "clear")),
    ({"recycle_on_expiry": "compact"}, ("exit", "compact")),
    ({"recycle_at_limit": "compact"}, ("exit", "compact")),
    ({"recycle_at_limit": "relaunch", "recycle_on_expiry": "clear"},
     ("exit", "clear", "compact")),
    ({"recycle_at_limit": "off", "recycle_on_expiry": "off"}, ("exit",)),
])
def test_the_grant_widens_with_the_recycle_rows(values, granted):
    assert common.commands(values) == granted


def test_the_option_stamps():
    assert common.option_stamps({"keep_warm": "off", "recycle_on_expiry": "off",
                                 "recycle_at_limit": "off", "limit_at": "1x"}) == {}
    assert common.option_stamps({"keep_warm": "on", "recycle_on_expiry": "compact",
                                 "recycle_at_limit": "relaunch", "limit_at": "1.5x"}) == {
        "MOXI_LAUNCHER_KEEP_WARM": "on", "MOXI_LAUNCHER_RECYCLE_ON_EXPIRY": "compact",
        "MOXI_USAGE_CROSSING_AT": "1.5x"}


def test_the_grant_reaches_the_stamp_and_the_additions_alike(monkeypatch):
    """turns renders its own grant into its additions, which land over the stamp, so the
    additions must be asked with the same commands."""
    from moxi_launcher import compose

    asked = {}

    class Turns:
        TurnsError = RuntimeError

        @staticmethod
        def launch_additions(harness, *, cwd, env, session_id, commands):
            asked["commands"] = commands
            return type("A", (), {"argv": (), "env": {"MOXI_TURNS_COMMANDS":
                                                      ",".join(commands)}, "notes": ()})

    monkeypatch.setattr(compose.first, "turns_api", lambda quiet=False: Turns)
    got = compose.launch_additions(cwd="/w", env={}, session_id="s",
                                   granted=common.commands({"recycle_on_expiry": "clear"}))
    assert asked["commands"] == ("exit", "clear")
    env = common.stamps("r1", turns=True, additions=got, granted=("exit", "clear"))
    assert env["MOXI_TURNS_COMMANDS"] == "exit,clear"


def test_a_keep_warm_launch_has_its_identity_line():
    block = common.identity_block(session_id="s", cwd="/w", posture="go", keep_warm=True)
    assert block.splitlines()[-1].startswith('keep warm:     on; open a hold with "moxi '
                                             'launcher keep-warm hold <name>"')
    assert "keep warm" not in common.identity_block(session_id="s", cwd="/w", posture="go")
