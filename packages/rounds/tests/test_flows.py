"""Flows: the steps rounds defines, the flows it ships, a config's own flows and steps,
and each flow the config check refuses."""

from __future__ import annotations

import pytest
from moxi import config

from moxi_rounds import flows, settings

TEN = ["design", "verify-design", "docket", "commission", "build", "check",
       "verify-build", "finish", "ratify", "retro"]

REPORT_STEP = ('{ step = "security-review", does = "report", before = "finish", '
               'by = "checker", model = "second", reads = ["the diff"] }')
COMMAND_STEP = ('{ step = "staging", does = "command", before = "finish", '
                'by = "builder", argv = ["./stage.sh", "{tip}"], timeout_s = 900 }')


def flow_text(*entries: str, name: str = "mine") -> str:
    """A config text with one flow, `name`, of the step `entries`."""
    return f"[flows.{name}]\nsteps = [\n" + "".join(f"  {e},\n" for e in entries) + "]\n"


def built(*entries: str) -> str:
    """A flow that builds: the `entries`, then `build` and `finish`."""
    return flow_text(*entries, '{ step = "build", by = "builder" }',
                     '{ step = "finish", by = "builder" }')


def refusal(text: str) -> config.ConfigError:
    """The error the module's check raises for config text `text`."""
    with pytest.raises(config.ConfigError) as caught:
        settings.check_text(text)
    assert caught.value.exit_code == 2
    assert caught.value.problem and caught.value.fix
    return caught.value


# -- the steps and the flows rounds ships ------------------------------------------------

def test_rounds_defines_ten_steps_each_for_a_round_or_a_stage():
    assert list(flows.STEPS) == TEN
    assert [name for name, scope in flows.STEPS.items() if scope == flows.STAGE] == [
        "commission", "build", "check", "verify-build", "finish"]
    assert set(flows.STEPS.values()) == {flows.ROUND, flows.STAGE}


def test_the_three_built_in_flows_list_their_steps_in_order():
    steps = {name: [entry["step"] for entry in flow]
             for name, flow in flows.BUILT_IN.items()}
    assert steps == {
        "fix": ["build", "verify-build", "finish"],
        "small": ["design", "build", "verify-build", "finish", "retro"],
        "full": ["design", "verify-design", "build", "verify-build", "finish", "retro"],
    }


def test_a_built_in_flow_says_who_does_each_step_and_at_which_tier():
    assert flows.BUILT_IN["full"] == [
        {"step": "design", "by": "lead", "model": "second"},
        {"step": "verify-design", "by": "checker", "model": "second",
         "recheck_model": "third"},
        {"step": "build", "by": "builder", "model": "second"},
        {"step": "verify-build", "by": "checker", "model": "second",
         "recheck_model": "third"},
        {"step": "finish", "by": "builder"},
        {"step": "retro", "by": "worker", "model": "third"},
    ]
    assert flows.BUILT_IN["fix"][1] == {"step": "verify-build", "by": "checker",
                                        "model": "third"}


def test_no_built_in_flow_lists_a_step_every_flow_has_unlisted():
    for flow in flows.BUILT_IN.values():
        assert not {entry["step"] for entry in flow} & set(flows.UNLISTED_STEPS)
    assert set(flows.UNLISTED_STEPS) == {"commission", "check", "ratify", "docket"}


def test_the_built_in_flows_pass_the_check_written_as_config():
    written = {name: {"steps": flow} for name, flow in flows.BUILT_IN.items()}
    config.set("rounds", "flows", written, by="test")
    assert flows.read(settings.load()) == flows.BUILT_IN


# -- reading the flows -------------------------------------------------------------------

def test_an_empty_config_has_the_three_built_in_flows():
    assert flows.read(settings.load()) == flows.BUILT_IN
    assert list(flows.read(settings.load())) == ["fix", "small", "full"]


def test_a_flow_of_the_config_joins_the_built_in_ones(write_config):
    write_config(flow_text('{ step = "design", by = "lead" }', name="design"))
    read = flows.read(settings.load())
    assert list(read) == ["fix", "small", "full", "design"]
    assert read["design"] == [{"step": "design", "by": "lead"}]


def test_a_flow_named_like_a_built_in_one_replaces_it(write_config):
    write_config(flow_text('{ step = "build", by = "coder" }',
                           '{ step = "finish", by = "coder" }', name="fix"))
    read = flows.read(settings.load())
    assert read["fix"] == [{"step": "build", "by": "coder"},
                           {"step": "finish", "by": "coder"}]
    assert read["small"] == flows.BUILT_IN["small"]
    assert read["full"] == flows.BUILT_IN["full"]


def test_a_flow_as_read_is_the_callers_own_copy():
    first = flows.read(settings.load())
    first["fix"][0]["by"] = "changed"
    first["full"].clear()
    assert flows.read(settings.load()) == flows.BUILT_IN
    assert flows.BUILT_IN["fix"][0]["by"] == "builder"


# -- a step of the user's own ------------------------------------------------------------

def test_a_report_step_of_the_users_own_is_accepted(write_config):
    write_config(built(REPORT_STEP))
    entry = flows.read(settings.load())["mine"][0]
    assert entry == {"step": "security-review", "does": "report", "before": "finish",
                     "by": "checker", "model": "second", "reads": ["the diff"]}
    assert flows.is_user_step(entry)
    assert not flows.is_user_step({"step": "build"})


def test_a_command_step_of_the_users_own_is_accepted(write_config):
    write_config(built(COMMAND_STEP))
    assert flows.read(settings.load())["mine"][0] == {
        "step": "staging", "does": "command", "before": "finish", "by": "builder",
        "argv": ["./stage.sh", "{tip}"], "timeout_s": 900}


def test_a_flow_may_hold_both_shapes_and_a_command_needs_no_time_limit():
    assert settings.check_text(built(
        REPORT_STEP, '{ step = "smoke-2", does = "command", before = "finish", '
                     'argv = ["make", "smoke"] }', COMMAND_STEP)) is None


@pytest.mark.parametrize("entry, key, words", [
    ('{ step = "Staging", does = "report", before = "finish" }', "step",
     "not a name a step of your own may have"),
    ('{ step = "stage_two", does = "report", before = "finish" }', "step",
     "not a name a step of your own may have"),
    ('{ step = "staging", before = "finish" }', "does", "is missing"),
    ('{ step = "staging", does = "script", before = "finish" }', "does",
     "which it cannot be"),
    ('{ step = "staging", does = "report" }', "before", "is missing"),
    ('{ step = "staging", does = "report", before = "build" }', "before",
     "which it cannot be"),
    ('{ step = "staging", does = "command", before = "finish" }', "argv", "is missing"),
    ('{ step = "staging", does = "command", before = "finish", argv = [] }', "argv",
     "is not a command"),
    ('{ step = "staging", does = "command", before = "finish", argv = "make" }', "argv",
     "is not a command"),
    ('{ step = "staging", does = "command", before = "finish", argv = ["make", 1] }',
     "argv", "is not a command"),
    ('{ step = "staging", does = "command", before = "finish", argv = ["make"], '
     'timeout_s = 0 }', "timeout_s", "is not a number of seconds"),
    ('{ step = "staging", does = "command", before = "finish", argv = ["make"], '
     'timeout_s = "900" }', "timeout_s", "is not a number of seconds"),
    ('{ step = "staging", does = "report", before = "finish", argv = ["make"] }', "argv",
     "is not a key of a report step"),
    ('{ step = "staging", does = "report", before = "finish", recheck_model = "third" }',
     "recheck_model", "is not a key of a report step"),
    ('{ step = "staging", does = "command", before = "finish", argv = ["make"], '
     'repeat = true }', "repeat", "is not a key of a command step"),
    ('{ step = "staging", does = "report", before = "finish", reads = "the diff" }',
     "reads", "is not a list of strings"),
    ('{ step = "staging", does = "report", before = "finish", by = 3 }', "by",
     "is not a name"),
])
def test_a_malformed_step_of_the_users_own_is_refused(entry, key, words):
    error = refusal(built(entry))
    assert error.key == f"flows.mine.steps[0].{key}"
    assert words in error.problem


# -- each flow the check refuses ---------------------------------------------------------

def test_a_step_that_is_neither_one_of_the_ten_nor_a_users_own_is_refused():
    error = refusal(built('{ step = "review", by = "checker" }'))
    assert error.key == "flows.mine.steps[0].does"
    assert "'review' is none of rounds' steps" in error.problem
    assert 'does = "report"' in error.fix


def test_a_step_listed_twice_is_refused():
    error = refusal(built('{ step = "design", by = "lead" }',
                          '{ step = "design", by = "planner" }'))
    assert error.key == "flows.mine.steps"
    assert error.problem == "lists the step 'design' twice"


def test_a_users_own_step_listed_twice_is_refused():
    error = refusal(built(REPORT_STEP, REPORT_STEP))
    assert error.problem == "lists the step 'security-review' twice"


def test_verify_design_with_no_design_is_refused():
    error = refusal(flow_text('{ step = "verify-design", by = "checker" }'))
    assert error.key == "flows.mine.steps"
    assert error.problem == "has `verify-design` and no `design`"
    assert "add a `design` step" in error.fix


@pytest.mark.parametrize("entry, name", [
    ('{ step = "verify-build", by = "checker" }', "verify-build"),
    ('{ step = "finish", by = "builder" }', "finish"),
    (REPORT_STEP, "security-review"),
    (COMMAND_STEP, "staging"),
])
def test_a_step_that_follows_a_build_is_refused_with_no_build(entry, name):
    error = refusal(flow_text('{ step = "design", by = "lead" }', entry))
    assert error.key == "flows.mine.steps"
    assert error.problem == f"has {name!r} and no `build`"
    assert "add a `build` step" in error.fix


def test_a_build_with_no_finish_is_refused():
    error = refusal(flow_text('{ step = "build", by = "builder" }'))
    assert error.key == "flows.mine.steps"
    assert error.problem == "has `build` and no `finish`"
    assert "add a `finish` step" in error.fix


def test_a_built_in_flow_replaced_by_a_bad_one_is_refused():
    error = refusal(flow_text('{ step = "build", by = "builder" }', name="full"))
    assert error.key == "flows.full.steps"


@pytest.mark.parametrize("text, key, words", [
    ("[flows]\nmine = 3\n", "flows.mine", "is an int, not a table"),
    ("[flows.mine]\n", "flows.mine.steps", "is missing"),
    ("[flows.mine]\nsteps = \"build\"\n", "flows.mine.steps", "is a string, not a list"),
    ("[flows.mine]\nsteps = []\ntiers = []\n", "flows.mine.tiers",
     "is not a key of a flow"),
    ("[flows.mine]\nsteps = [\"build\"]\n", "flows.mine.steps[0]",
     "is a string, not a table"),
    ("[flows.mine]\nsteps = [ { by = \"lead\" } ]\n", "flows.mine.steps[0].step",
     "is missing"),
    ("[flows.mine]\nsteps = [ { step = 3 } ]\n", "flows.mine.steps[0].step",
     "is not a step's name"),
])
def test_a_flow_that_is_not_a_list_of_step_tables_is_refused(text, key, words):
    error = refusal(text)
    assert error.key == key and words in error.problem


@pytest.mark.parametrize("entry, key, words", [
    ('{ step = "design", by = "lead", recheck_model = "third" }', "recheck_model",
     "is not a key of the step 'design'"),
    ('{ step = "design", does = "report" }', "does", "is not a key of the step 'design'"),
    ('{ step = "ratify", by = "lead" }', "by", "is not a key of the step 'ratify'"),
    ('{ step = "docket", by = "lead" }', "by", "is not a key of the step 'docket'"),
    ('{ step = "design", by = "" }', "by", "is not a name"),
    ('{ step = "design", model = 2 }', "model", "is not a name"),
    ('{ step = "design", reads = ["the brief", 2] }', "reads",
     "is not a list of strings"),
])
def test_a_key_one_of_the_ten_steps_does_not_take_is_refused(entry, key, words):
    error = refusal(built(entry))
    assert error.key == f"flows.mine.steps[0].{key}"
    assert words in error.problem


def test_a_flow_may_list_a_step_it_would_have_unlisted():
    assert settings.check_text(built(
        '{ step = "commission", by = "lead" }', '{ step = "check", by = "builder" }',
        '{ step = "ratify" }')) is None


# -- tiers -------------------------------------------------------------------------------

def test_a_tier_that_names_no_flow_is_refused():
    error = refusal('tiers = ["fix", "medium", "full"]\n')
    assert error.key == "tiers"
    assert error.problem == "names 'medium', which is no flow"
    assert "[flows.medium]" in error.fix and "fix, small, full" in error.fix


def test_a_tier_may_name_a_flow_of_the_config():
    assert settings.check_text(
        'tiers = ["fix", "medium", "full"]\n'
        + built('{ step = "design", by = "lead" }', ).replace("mine", "medium")) is None


def test_moxi_config_set_refuses_a_bad_flow_and_changes_nothing(write_config):
    path = write_config(built(REPORT_STEP))
    before = path.read_text()
    with pytest.raises(config.ConfigError) as caught:
        config.set("rounds", "flows", {"mine": {"steps": [{"step": "build"}]}}, by="test")
    assert caught.value.key == "flows.mine.steps"
    assert caught.value.file == str(path)
    assert path.read_text() == before
    with pytest.raises(config.ConfigError):
        config.set("rounds", "tiers", ["fix", "nowhere"], by="test")
    assert path.read_text() == before
