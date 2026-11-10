"""The note on a report whose model is weaker than the tier its pass is held to: the
tiers' order is the order the config file writes them in, a model is looked up under
every harness, and nothing is noted when the question can't be answered."""

from __future__ import annotations

import pytest

from moxi_rounds import flows, model_tiers, settings
from moxi_rounds.model_tiers import BELOW_TIER

STEP = "verify-design"
STEPS = [{"step": "design", "by": "planner", "model": "first"},
         {"step": STEP, "by": "reviewer", "model": "second", "recheck_model": "third"}]
NO_RECHECK = [{"step": STEP, "by": "reviewer", "model": "second"}]

STRONGEST_FIRST = """\
    [models]
    first  = { claude = "model-one", codex = "model-a" }
    second = { claude = "model-two", codex = "model-b" }
    third  = { claude = "model-three", codex = "model-c" }
"""
WEAKEST_FIRST = """\
    [models]
    third  = { claude = "model-three", codex = "model-c" }
    second = { claude = "model-two", codex = "model-b" }
    first  = { claude = "model-one", codex = "model-a" }
"""


@pytest.fixture
def models(write_config):
    """`models(text)`: the `models` table as the settings read it from a config file
    whose text is `text`."""

    def read(text: str):
        write_config(text)
        return settings.load()["models"]

    return read


def noted(table, model, *, pass_="full", steps=STEPS):
    return model_tiers.flags(table, steps, STEP, pass_=pass_, model=model)


# -- the order ---------------------------------------------------------------------------

@pytest.mark.parametrize(("model", "flags"), [
    ("model-three", [BELOW_TIER]), ("model-two", []), ("model-one", [])])
def test_a_model_below_the_tier_of_a_full_pass_is_noted_and_one_at_or_above_is_not(
        models, model, flags):
    assert noted(models(STRONGEST_FIRST), model) == flags


@pytest.mark.parametrize(("model", "flags"), [
    ("model-three", []), ("model-two", []), ("model-one", [BELOW_TIER])])
def test_the_same_tiers_written_in_another_order_give_the_other_answer(
        models, model, flags):
    assert noted(models(WEAKEST_FIRST), model) == flags


def test_the_settings_keep_the_tiers_in_the_order_the_file_writes_them(models):
    assert list(models(STRONGEST_FIRST)) == ["first", "second", "third"]
    assert list(models(WEAKEST_FIRST)) == ["third", "second", "first"]


def test_a_model_under_two_tiers_is_in_the_strongest_that_lists_it(models):
    table = models("""\
        [models]
        first  = { claude = "model-one" }
        second = { claude = "model-shared" }
        third  = { claude = "model-shared" }
    """)
    assert model_tiers.tier_of(table, "model-shared") == "second"
    assert noted(table, "model-shared") == []
    held_to_first = [{"step": STEP, "model": "first"}]
    assert noted(table, "model-shared", steps=held_to_first) == [BELOW_TIER]


# -- which tier a pass is held to --------------------------------------------------------

@pytest.mark.parametrize("pass_", ["re-check", "drift"])
def test_a_pass_that_is_not_full_is_held_to_the_tier_for_a_recheck(models, pass_):
    table = models(STRONGEST_FIRST)
    assert model_tiers.held_to(STEPS, STEP, pass_) == "third"
    assert noted(table, "model-three", pass_=pass_) == []
    assert noted(table, "model-three", pass_="full") == [BELOW_TIER]


def test_a_recheck_of_a_step_with_no_tier_for_one_is_held_to_the_step_s_model(models):
    table = models(STRONGEST_FIRST)
    assert model_tiers.held_to(NO_RECHECK, STEP, "re-check") == "second"
    assert noted(table, "model-three", pass_="re-check", steps=NO_RECHECK) == [BELOW_TIER]
    assert noted(table, "model-two", pass_="re-check", steps=NO_RECHECK) == []


def test_a_report_is_held_to_the_tiers_of_its_own_step(models):
    steps = [{"step": "verify-build", "model": "first"}, *STEPS]
    assert model_tiers.held_to(steps, "verify-build", "full") == "first"
    assert model_tiers.flags(models(STRONGEST_FIRST), steps, "verify-build", pass_="full",
                             model="model-two") == [BELOW_TIER]


def test_the_built_in_flows_name_a_tier_for_each_pass_of_the_step_that_verifies():
    steps = flows.read({"flows": {}})["full"]
    assert model_tiers.held_to(steps, STEP, "full") == "second"
    assert model_tiers.held_to(steps, STEP, "re-check") == "third"


# -- nothing is noted when the question has no answer ------------------------------------

def test_an_empty_table_notes_nothing(models):
    assert noted(models(""), "model-three") == []
    assert noted({}, "model-three") == []


def test_a_model_in_no_tier_notes_nothing(models):
    assert noted(models(STRONGEST_FIRST), "model-unlisted") == []


def test_a_step_whose_tier_is_not_in_the_table_notes_nothing(models):
    table = models("""\
        [models]
        first = { claude = "model-one" }
        third = { claude = "model-three" }
    """)
    assert noted(table, "model-three") == []


@pytest.mark.parametrize("model", [None, ""])
def test_a_report_that_names_no_model_notes_nothing(models, model):
    assert noted(models(STRONGEST_FIRST), model) == []


def test_a_step_that_names_no_tier_notes_nothing(models):
    table = models(STRONGEST_FIRST)
    assert noted(table, "model-three", steps=[{"step": STEP, "by": "reviewer"}]) == []
    assert noted(table, "model-three", steps=[]) == []


# -- the harness -------------------------------------------------------------------------

def test_a_model_is_looked_up_under_every_harness(models):
    table = models(STRONGEST_FIRST)
    assert noted(table, "model-three") == [BELOW_TIER]
    assert noted(table, "model-c") == [BELOW_TIER]
    assert noted(table, "model-two") == noted(table, "model-b") == []


def test_a_model_two_harnesses_list_in_two_tiers_is_in_the_stronger(models):
    table = models("""\
        [models]
        first  = { claude = "model-one", codex = "model-x" }
        second = { claude = "model-two", codex = "model-b" }
        third  = { claude = "model-x", codex = "model-c" }
    """)
    assert model_tiers.tier_of(table, "model-x") == "first"
    assert noted(table, "model-x") == []
    weaker_first = models("""\
        [models]
        first  = { claude = "model-one", codex = "model-a" }
        second = { claude = "model-two", codex = "model-x" }
        third  = { claude = "model-x", codex = "model-c" }
    """)
    assert model_tiers.tier_of(weaker_first, "model-x") == "second"
    held_to_first = [{"step": STEP, "model": "first"}]
    assert noted(weaker_first, "model-x", steps=held_to_first) == [BELOW_TIER]


def test_a_tier_that_lists_the_model_under_no_harness_is_passed_over(models):
    table = models("""\
        [models]
        first  = { codex = "model-a" }
        second = { claude = "model-two" }
        third  = { claude = "model-three" }
    """)
    assert noted(table, "model-three") == [BELOW_TIER]
    assert noted(table, "model-a") == []


# -- where the order is said -------------------------------------------------------------

def test_the_setting_s_own_text_says_the_written_order_is_the_tiers_order():
    assert "strongest first" in settings.schema().keys["models"].doc
