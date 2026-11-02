"""The restart-point equations with their exact coefficients, and every way a mark is
unavailable rather than guessed. The model table is built in each test; the measure's
file sits in the test's tmp."""

import copy
import json
import math
import subprocess
import sys

import pytest

from moxi.paths import Layout
from moxi_usage import models, thresholds

from conftest import doc, write_config

#: Three measured rows, each with w at 2.0 (every cache write for an hour).
PARAMS = {
    "schema": 1,
    "measured_at": "2026-09-26T00:00:00+00:00",
    "models": {
        "claude-fable-5-1": {"main": {"n": 147, "S": 51053, "P": 23116, "g": 2539, "w": 2.0}},
        "claude-opus-5-5": {"main": {"n": 50, "S": 47058, "P": 23171, "g": 1476, "w": 2.0}},
        "claude-opus-5": {"main": {"n": 58, "S": 45999, "P": 23050, "g": 1745, "w": 2.0}},
    },
}
#: Each model's cache-read price over its input price.
READS = {"claude-fable-5-1": 0.025, "claude-opus-5-5": 0.05, "claude-opus-5": 0.1}
#: What the exact equations give from those rows, as whole-token thresholds.
EXACT = {
    "claude-fable-5-1": {"1x": 158_135, "1.5x": 331_397, "2x": 450_688},
    "claude-opus-5-5": {"1x": 100_808, "1.5x": 187_776, "2x": 247_653},
    "claude-opus-5": {"1x": 87_015, "1.5x": 153_380, "2x": 199_072},
}
#: The same rows through the rounded coefficients 2.618 and 3.732, a few tokens lower.
ROUNDED = {
    "claude-fable-5-1": {"1x": 158_135, "1.5x": 331_393, "2x": 450_682},
    "claude-opus-5-5": {"1x": 100_807, "1.5x": 187_773, "2x": 247_650},
    "claude-opus-5": {"1x": 87_015, "1.5x": 153_378, "2x": 199_069},
}


def table(**extra):
    rows = {model: {"input": 10.0, "output": 50.0, "cache_read": 10.0 * r}
            for model, r in READS.items()}
    rows.update(extra)
    return models.Table(doc(rows))


def write_params(document=PARAMS):
    path = thresholds.params_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document) if isinstance(document, dict) else document,
                    encoding="utf-8")
    return path


@pytest.mark.parametrize("model", sorted(EXACT))
@pytest.mark.parametrize("mark", thresholds.MARKS)
def test_the_exact_equations_on_the_measured_rows(model, mark):
    found = thresholds.threshold(model, mark, doc=PARAMS, table=table())
    assert found.available and found.reason is None and found.fallback is None
    assert found.tokens == EXACT[model][mark]
    row = PARAMS["models"][model]["main"]
    S, P, g, w, r = row["S"], row["P"], row["g"], 2.0, READS[model]
    n_star = math.sqrt(2 * (w * (S - P) + r * P) / (r * g))
    k = {"1x": 1.0, "1.5x": 1.5 + math.sqrt(1.25), "2x": 2 + math.sqrt(3)}[mark]
    assert found.exact == pytest.approx(S + g * k * n_star, abs=1e-6)


@pytest.mark.parametrize("model", sorted(EXACT))
def test_the_rounded_coefficients_sit_a_few_tokens_under_the_exact_ones(model):
    row = PARAMS["models"][model]["main"]
    S, P, g, r = row["S"], row["P"], row["g"], READS[model]
    n_star = thresholds.optimum_calls(thresholds.restart_cost(S, P, 2.0, r), r, g)
    for mark, k in (("1x", 1.0), ("1.5x", 2.618), ("2x", 3.732)):
        assert round(S + g * k * n_star) == ROUNDED[model][mark]
    for mark in thresholds.MARKS:
        assert 0 <= EXACT[model][mark] - ROUNDED[model][mark] <= 7


def test_the_threshold_is_the_exact_comparison_on_whole_tokens():
    found = thresholds.threshold("claude-fable-5-1", "1x", doc=PARAMS, table=table())
    assert found.tokens - 1 < found.exact <= found.tokens


def test_a_bracket_or_date_suffix_reads_the_base_models_row():
    long = thresholds.threshold("claude-opus-5-5[1m]", "2x", doc=PARAMS, table=table())
    dated = thresholds.threshold("claude-opus-5-20260101", "2x", doc=PARAMS, table=table())
    assert (long.model, long.tokens) == ("claude-opus-5-5", EXACT["claude-opus-5-5"]["2x"])
    assert (dated.model, dated.tokens) == ("claude-opus-5", EXACT["claude-opus-5"]["2x"])
    assert thresholds.base_model("gpt-5-2025-08-07") == "gpt-5"
    assert thresholds.base_model("gpt-5.3-codex") == "gpt-5.3-codex"
    assert thresholds.base_model("") is None and thresholds.base_model(None) is None


def _w(row, share=None, *, defaults=None):
    found = models.Table(doc({"m": row}, defaults=defaults)).lookup("m")
    return thresholds.write_multiplier(found, share)


ANTHROPIC = {"input": 4.0, "output": 20.0, "cache_write_5m": 5.0, "cache_write_1h": 8.0,
             "provider": "anthropic"}


def test_w_with_both_write_prices_weighs_them_by_the_one_hour_share():
    assert _w(ANTHROPIC, 1.0) == (2.0, None)
    assert _w(ANTHROPIC, 0.0) == (1.25, None)
    assert _w(ANTHROPIC, None) == (1.25, None)          # no request stated the split
    assert _w(ANTHROPIC, 0.5)[0] == pytest.approx(1.625)


def test_w_with_one_write_price_is_that_price_over_input_whatever_the_share():
    five = {"input": 4.0, "output": 20.0, "cache_write_5m": 5.0}
    hour = {"input": 4.0, "output": 20.0, "cache_write_1h": 6.0}
    assert _w(five, 1.0) == (1.25, None) and _w(five, None) == (1.25, None)
    assert _w(hour, 0.0) == (1.5, None)


def test_w_with_no_write_price_of_its_own_is_one():
    plain = {"input": 1.25, "output": 10.0, "cache_read": 0.125, "provider": "openai"}
    assert _w(plain, None) == (1.0, None)
    # A provider default fills both write prices for the lookup, but they are not the
    # row's own, so w stays 1.0.
    filled = _w(plain, 1.0, defaults={"openai": {"cache_write_5m": 1.0,
                                                 "cache_write_1h": 2.0}})
    assert filled == (1.0, None)
    # One write price of its own and one filled: the own one alone.
    partly = _w({**plain, "cache_write_5m": 1.5625}, 1.0,
                defaults={"openai": {"cache_write_1h": 2.0}})
    assert partly == (1.25, None)


def test_a_model_with_no_input_price_or_one_of_0_or_no_row_has_no_w():
    w, why = _w({"output": 20.0, "cache_write_5m": 5.0}, None)
    assert w is None and "no input price" in why
    w, why = _w({"input": 0.0, "output": 0.0, "cache_write_5m": 0.0}, None)
    assert w is None and "not above 0" in why
    w, why = thresholds.write_multiplier(table().lookup("nobody-knows"), None)
    assert w is None and "does not know nobody-knows" in why


def test_a_subagents_fallback_row_gives_the_main_mark_and_says_so():
    params = copy.deepcopy(PARAMS)
    row = params["models"]["claude-opus-5"]
    row["subagent"] = {**row["main"], "fallback": "main"}
    found = thresholds.threshold("claude-opus-5", "1x", "subagent", doc=params,
                                 table=table())
    assert found.tokens == EXACT["claude-opus-5"]["1x"] and found.fallback == "main"
    assert found.as_dict()["fallback"] == "main"


# --- unavailable, and no mark worked out -------------------------------------------------

@pytest.mark.parametrize("model,mark,population,params,reason", [
    ("claude-sonnet-5", "1x", "main", PARAMS,
     "the restart-point measure has no main row for claude-sonnet-5"),
    ("claude-opus-5", "1x", "subagent", PARAMS,
     "the restart-point measure has no subagent row for claude-opus-5"),
    ("claude-opus-5", "1x", "main", None,
     "the restart point has not been measured; run `moxi usage restart-point --measure`"),
    (None, "1x", "main", PARAMS, "no model is known for the session"),
    ("claude-opus-5", "3x", "main", PARAMS, "'3x' is not a mark"),
    ("claude-opus-5", "1x", "workflow", PARAMS,
     "'workflow' is not a population (main, subagent)"),
])
def test_a_mark_without_what_it_needs_is_unavailable(model, mark, population, params,
                                                      reason):
    found = thresholds.threshold(model, mark, population, doc=params, table=table())
    assert not found.available and found.tokens is None and found.exact is None
    assert found.reason == reason


def test_a_row_without_a_read_multiplier_is_unavailable():
    params = copy.deepcopy(PARAMS)
    params["models"]["claude-new-1"] = params["models"]["claude-opus-5"]
    lacking = table(**{"claude-new-1": {"input": 10.0, "output": 50.0}})
    found = thresholds.threshold("claude-new-1", "1x", doc=params, table=lacking)
    assert found.reason == "the model table has no cache-read price for claude-new-1"
    unknown = thresholds.threshold("claude-new-1", "1x", doc=params, table=table())
    assert unknown.reason == "the model table does not know claude-new-1"


@pytest.mark.parametrize("change,reason", [
    ({"g": None}, "is incomplete"),
    ({"g": 0}, "gives no restart point"),
    ({"S": 0, "P": 0}, "gives no restart point"),
])
def test_parameters_that_give_no_restart_point_are_unavailable(change, reason):
    params = copy.deepcopy(PARAMS)
    params["models"]["claude-opus-5"]["main"].update(change)
    found = thresholds.threshold("claude-opus-5", "2x", doc=params, table=table())
    assert not found.available and found.reason.endswith(reason)


def test_a_row_with_no_w_gives_no_mark():
    params = copy.deepcopy(PARAMS)
    del params["models"]["claude-opus-5"]["main"]["w"]
    found = thresholds.all_marks("claude-opus-5", doc=params, table=table())
    assert set(found) == {"1x", "1.5x", "2x"}
    assert not any(each.available for each in found.values())


# --- the measure in effect, and its stamp ------------------------------------------------

def test_the_measure_in_effect_is_read_from_its_file_and_again_when_it_changes():
    assert thresholds.params() is None
    write_params()
    first = thresholds.threshold("claude-opus-5", "1x", table=table())
    assert first.tokens == EXACT["claude-opus-5"]["1x"]
    changed = copy.deepcopy(PARAMS)
    changed["models"]["claude-opus-5"]["main"]["g"] = 3490
    write_params(changed)
    again = thresholds.threshold("claude-opus-5", "1x", table=table())
    assert again.tokens > first.tokens


@pytest.mark.parametrize("text,said", [
    ("{not json", "is not JSON"),
    ("[1, 2]", "is not a JSON object"),
    (json.dumps({**PARAMS, "schema": 9}), "has schema 9"),
])
def test_a_measure_file_that_cannot_be_read_is_unavailable_with_why(text, said):
    write_params(text)
    found = thresholds.threshold("claude-opus-5", "1x", table=table())
    assert not found.available and said in found.reason
    with pytest.raises(thresholds.ParamsError):
        thresholds.params()


def test_the_stamp_changes_with_the_measure_the_snapshot_and_the_config():
    before = thresholds.stamp()
    assert thresholds.stamp() == before
    write_params()
    after_measure = thresholds.stamp()
    assert after_measure != before
    snapshot = models.snapshot_path()
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(json.dumps(doc({})), encoding="utf-8")
    after_snapshot = thresholds.stamp()
    assert after_snapshot != after_measure
    write_config('[[model]]\nid = "claude-opus-5"\ninput = 5.0\noutput = 25.0\n')
    assert thresholds.stamp() != after_snapshot
    assert str(Layout("usage").config_file()) in [path for path, _ in thresholds.stamp()]


def test_the_model_table_in_effect_gives_r():
    write_params()
    write_config('[[model]]\nid = "claude-opus-5"\ninput = 10.0\noutput = 50.0\n'
                 'cache_read = 1.0\n')
    found = thresholds.threshold("claude-opus-5", "1x")
    assert found.tokens == EXACT["claude-opus-5"]["1x"]


def test_importing_the_module_loads_nothing_heavy():
    """A hook path imports it: no json, no dataclasses, none of usage's other modules,
    no core module until a threshold is asked for."""
    code = ("import sys; import moxi_usage.thresholds; "
            "print(' '.join(sorted(m for m in sys.modules if m in ('json', 'dataclasses', "
            "'sqlite3') or m.startswith(('moxi.', 'moxi_sessions', 'moxi_usage.')))))")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                         text=True, check=True).stdout.split()
    assert out == ["moxi_usage.thresholds"]
