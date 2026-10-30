"""The layers, the lookup ladder and its provenance, and the context window."""

import datetime
import json

import pytest

from conftest import doc, write_config
from moxi.config import ConfigError
from moxi_usage import api, models
from moxi_usage.settings import Override

UNKNOWN = api.UNKNOWN
DAY = datetime.datetime(2026, 10, 15, tzinfo=datetime.timezone.utc).timestamp()


def base() -> dict:
    return doc({
        "claude-a-1": {"input": 4.0, "output": 20.0, "cache_read": 0.2, "window": 200000,
                       "max_output": 64000, "provider": "anthropic"},
        "anthropic/claude-b-2": {"input": 1.0, "output": 5.0, "provider": "anthropic"},
        "azure/gpt-c": {"input": 2.0, "output": 8.0, "provider": "azure"},
        "gpt-d": {"input": 1.75, "output": 14.0, "cache_read": 0.175, "provider": "openai",
                  "window": 272000},
    }, date="2026-10-01",
        defaults={"anthropic": {"cache_read": 0.1, "cache_write_5m": 1.25,
                                "cache_write_1h": 2.0, "source": "s", "as_of": "d"},
                  "openai": {"cache_write_5m": 1.0, "cache_write_1h": 1.0, "source": "s",
                             "as_of": "d"}},
        variants={"[1m]": {"window": 1000000, "source": "s", "as_of": "d"}},
        prefixes={"claude-fam-9-": {"input": 3.0, "output": 15.0,
                                    "provenance": {"source": "supplement"}}})


def step(facts) -> tuple:
    return facts.provenance.step, facts.provenance.row


# --- the ladder, step by step ---------------------------------------------------------

def test_step_1_synthetic_is_free():
    facts = models.Table(base()).lookup("<synthetic>")
    assert step(facts) == (1, None)
    assert (facts.input, facts.output, facts.cache_read) == (0.0, 0.0, 0.0)
    assert facts.window is UNKNOWN


def test_step_2_exact():
    facts = models.Table(base()).lookup("claude-a-1")
    assert step(facts) == (2, "claude-a-1")
    assert (facts.input, facts.output, facts.cache_read, facts.window) == (4.0, 20.0, 0.2, 200000)
    assert facts.provenance.layer == "shipped" and facts.provenance.snapshot == "2026-10-01"


def test_step_3_provider_prefix_from_the_table_and_the_request():
    table = models.Table(base())
    assert step(table.lookup("claude-b-2")) == (3, "anthropic/claude-b-2")
    assert step(table.lookup("gpt-c", provider="azure")) == (3, "azure/gpt-c")
    assert step(table.lookup("gpt-c")) == (7, None)


def test_step_4_variant_laid_over_the_base():
    facts = models.Table(base()).lookup("claude-a-1[1m]")
    assert step(facts) == (4, "claude-a-1")
    assert facts.window == 1000000 and facts.input == 4.0
    assert facts.provenance.variant == "[1m]"


def test_step_4_unknown_suffix_is_stripped():
    facts = models.Table(base()).lookup("claude-a-1[fast]")
    assert step(facts) == (4, "claude-a-1") and facts.window == 200000
    assert facts.provenance.variant is None


def test_step_4_variant_over_a_dated_base():
    facts = models.Table(base()).lookup("claude-a-1-20260101[1m]")
    assert step(facts) == (4, "claude-a-1") and facts.window == 1000000


def test_step_4_variant_of_an_unknown_base_gives_its_window_and_no_price():
    facts = models.Table(base()).lookup("mystery-7[1m]")
    assert facts.provenance.step == 4 and facts.window == 1000000
    assert not facts.priced and facts.input is UNKNOWN


@pytest.mark.parametrize("model", ["claude-a-1-20260101", "claude-a-1-2026-01-01"])
def test_step_5_date_suffix(model):
    assert step(models.Table(base()).lookup(model)) == (5, "claude-a-1")


def test_step_6_longest_prefix_override_before_supplement():
    over = [Override(id="claude-fam-9-x", match="prefix", prices={"input": 9.0, "output": 9.0},
                     number=1)]
    table = models.Table(base(), overrides=over)
    assert step(table.lookup("claude-fam-9-new")) == (6, "claude-fam-9-")
    facts = table.lookup("claude-fam-9-xl")
    assert step(facts) == (6, "claude-fam-9-x") and facts.input == 9.0
    assert facts.provenance.layer == "override"


def test_step_7_unknown_is_unpriced_never_zero():
    facts = models.Table(base()).lookup("nobody-knows-me")
    assert step(facts) == (7, None) and not facts.known and not facts.priced
    for name in ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h",
                 "window", "max_output", "tiers", "provider"):
        assert getattr(facts, name) is UNKNOWN
    with pytest.raises(TypeError):
        facts.input or 0
    data = facts.as_dict()
    assert "input" not in data and "input" in data["unknown"]
    assert data["provenance"] == {"layer": None, "step": 7, "rule": "unknown"}


@pytest.mark.parametrize("model", [None, "", UNKNOWN])
def test_no_model_is_unknown(model):
    assert models.Table(base()).lookup(model).provenance.step == 7


# --- provider defaults ----------------------------------------------------------------

def test_provider_defaults_fill_missing_cache_prices_and_say_so():
    table = models.Table(base())
    facts = table.lookup("claude-a-1")
    assert (facts.cache_write_5m, facts.cache_write_1h) == (5.0, 8.0)
    assert facts.cache_read == 0.2
    assert facts.provenance.filled == ("cache_write_5m", "cache_write_1h")
    openai = table.lookup("gpt-d")
    assert (openai.cache_write_5m, openai.cache_write_1h) == (1.75, 1.75)
    azure = table.lookup("azure/gpt-c")
    assert azure.cache_read is UNKNOWN and azure.provenance.filled == ()


# --- the layers -----------------------------------------------------------------------

def test_snapshot_wins_per_model_id_and_keeps_shipped_rows():
    snap = doc({"claude-a-1": {"input": 3.0, "output": 15.0, "provider": "anthropic"}},
               date="2026-11-02")
    table = models.Table(base(), snap)
    facts = table.lookup("claude-a-1")
    assert facts.input == 3.0 and facts.window is UNKNOWN
    assert (facts.provenance.layer, facts.provenance.snapshot) == ("snapshot", "2026-11-02")
    assert table.lookup("gpt-d").provenance.layer == "shipped"
    assert table.effective_date == "2026-11-02"


def test_an_override_price_replaces_every_price_and_limits_replace_only_themselves():
    over = [Override(id="claude-a-1", prices={"input": 5.0, "output": 25.0}, number=1),
            Override(id="gpt-d", limits={"window": 400000}, number=2)]
    table = models.Table(base(), overrides=over)
    facts = table.lookup("claude-a-1")
    assert (facts.input, facts.output) == (5.0, 25.0)
    assert facts.cache_read == 0.5           # from the default, not the old $0.20
    assert facts.window == 200000
    assert facts.provenance.overridden == ("input", "output")
    assert facts.provenance.layer == "override"
    window = table.lookup("gpt-d")
    assert window.window == 400000 and window.input == 1.75 and window.cache_read == 0.175


def test_override_periods_match_the_requests_own_time():
    sept = datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc).timestamp()
    nov = datetime.datetime(2026, 11, 1, tzinfo=datetime.timezone.utc).timestamp()
    over = [Override(id="gpt-d", prices={"input": 9.0, "output": 9.0}, number=1),
            Override(id="gpt-d", prices={"input": 1.0, "output": 2.0}, start=sept, end=nov,
                     note="promotional", number=2)]
    table = models.Table(base(), overrides=over)
    inside = table.lookup("gpt-d", at=DAY)
    assert inside.input == 1.0 and inside.provenance.note == "promotional"
    assert inside.provenance.period == {"from": "2026-09-01", "to": "2026-11-01"}
    assert table.lookup("gpt-d", at=nov).input == 9.0
    assert table.lookup("gpt-d", at=sept - 1).input == 9.0


def test_an_override_only_model_and_its_lapse():
    nov = datetime.datetime(2026, 11, 1, tzinfo=datetime.timezone.utc).timestamp()
    over = [Override(id="private-1", prices={"input": 1.0, "output": 2.0}, end=nov, number=1)]
    table = models.Table(base(), overrides=over)
    facts = table.lookup("private-1", at=DAY)
    assert step(facts) == (2, "private-1") and facts.provenance.source == {
        "source": "config", "row": 1}
    assert table.lookup("private-1", at=nov).provenance.step == 7
    assert "private-1" in table.ids(at=DAY) and "private-1" not in table.ids(at=nov)


# --- the table in effect: files, config, the API ------------------------------------

def test_the_shipped_table_through_the_api():
    facts = api.model_facts("claude-opus-5-5")
    assert facts.priced and facts.provenance.layer == "shipped"
    assert facts.provenance.snapshot == models.shipped()["snapshot"]["date"]
    assert facts.tiers["fast"]["input"] > facts.input
    assert api.models()[0].provenance.step == 2
    assert len(api.models()) == len(models.shipped()["models"])


def test_config_overrides_reach_the_api_and_a_bad_row_raises():
    write_config('[[model]]\nid = "made-up-1"\ninput = 1.5\noutput = 6\n'
                 'from = 2026-09-01\nnote = "mine"\n')
    facts = api.model_facts("made-up-1", at=DAY)
    assert facts.input == 1.5 and facts.provenance.note == "mine"
    write_config('[[model]]\nid = "made-up-1"\ninput = -1\n')
    with pytest.raises(ConfigError, match="row 1"):
        api.model_facts("made-up-1")


def test_the_snapshot_file_is_read_and_a_bad_one_is_skipped():
    path = models.snapshot_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(doc({"claude-opus-5-5": {"input": 1.0, "output": 2.0}},
                                   date="2026-12-01")))
    facts = api.model_facts("claude-opus-5-5")
    assert facts.input == 1.0 and facts.provenance.layer == "snapshot"
    path.write_text("{not json")
    assert api.model_facts("claude-opus-5-5").provenance.layer == "shipped"
    assert "snapshot" in api.table().problems[0]


def test_context_window_order():
    assert api.context_window("claude-opus-5-5", reported=258400) == 258400
    assert api.context_window("claude-opus-5-5", reported=UNKNOWN) == \
        api.model_facts("claude-opus-5-5").window
    assert api.context_window("claude-haiku-4-5") == 200000
    assert api.context_window("claude-haiku-4-5[1m]") == 1000000
    assert api.context_window("nobody-knows-me", reported=None) is UNKNOWN
    assert api.context_window("nobody-knows-me", reported=0) is UNKNOWN


def test_api_imports_nothing_heavy():
    import subprocess
    import sys

    code = ("import sys, moxi_usage.api; "
            "print(sorted(m for m in ('sqlite3', 'json', 'moxi_usage.models') "
            "if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True)
    assert out.stdout.strip() == "[]"
