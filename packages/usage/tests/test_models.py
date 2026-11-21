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


def test_the_shipped_table_carries_litellms_license_notice():
    """The table is derived from LiteLLM's price file, so LiteLLM's MIT notice ships
    beside it in the package, and the README's License section names it."""
    from importlib import resources
    from pathlib import Path

    notice = resources.files("moxi_usage").joinpath("data", "LICENSE-litellm").read_text()
    assert "MIT License" in notice and "Copyright (c) 2023 Berri AI" in notice
    assert "The above copyright notice and this permission notice" in notice
    shipped = json.loads(resources.files("moxi_usage").joinpath("data", "models.json")
                         .read_text())
    assert shipped["snapshot"]["source"] == "litellm"
    readme = (Path(__file__).parents[1] / "README.md").read_text()
    license_section = readme[readme.index("## License"):]
    assert "data/LICENSE-litellm" in license_section


# --- the shipped table's own rows for models newer than its LiteLLM snapshot -------------

NEWER = {"claude-fable-5-2": "anthropic", "claude-mythos-5-2": "anthropic",
         "big-pickle": "opencode", "minimax-m3.1-flash-free": "opencode",
         "space-bunny-free": "opencode"}


@pytest.mark.parametrize("model", ["claude-fable-5-2", "claude-mythos-5-2"])
def test_the_fable_and_mythos_rows_carry_every_price_the_multipliers_read(model):
    from moxi_usage import thresholds

    table = models.Table(models.shipped())
    facts = table.lookup(model)
    assert step(facts) == (2, model)
    assert facts.provider == "anthropic" and facts.provenance.filled == ()
    assert (facts.input, facts.output, facts.cache_read, facts.cache_write_5m,
            facts.cache_write_1h) == (8.0, 40.0, 0.2, 10.0, 16.0)
    assert (facts.window, facts.max_output) == (1000000, 128000)
    assert facts.tiers["fast"] == {"speed": "fast", "input": 16.0, "output": 80.0,
                                   "cache_read": 0.4, "cache_write_5m": 20.0,
                                   "cache_write_1h": 32.0}
    assert thresholds.read_multiplier(model, table=table) == (0.025, None)
    assert thresholds.write_multiplier(facts, 1.0) == (2.0, None)
    assert thresholds.write_multiplier(facts, 0.0) == (1.25, None)


#: Each fast tier's prices on Anthropic's pricing page: input, output, cache read, 5-minute
#: and 1-hour cache write. The cache prices are the model's own caching multipliers over
#: the fast input price (Opus 5.5 reads at 0.05x, Fable and Mythos 5.2 at 0.025x).
FAST = {"claude-opus-5-5": (8.0, 40.0, 0.4, 10.0, 16.0),
        "claude-opus-5": (10.0, 50.0, 1.0, 12.5, 20.0),
        "claude-opus-4-8": (10.0, 50.0, 1.0, 12.5, 20.0),
        "claude-fable-5-2": (16.0, 80.0, 0.4, 20.0, 32.0),
        "claude-mythos-5-2": (16.0, 80.0, 0.4, 20.0, 32.0)}


def test_every_fast_tier_carries_the_pricing_pages_own_cache_prices():
    """No fast tier's cache price is filled from the provider's default multiplier: that
    would read Opus 5.5's fast cache at 0.1x ($0.80), where Anthropic charges 0.05x."""
    from moxi_usage import cost

    table = models.Table(models.shipped())
    fast = sorted(key for key, row in models.shipped()["models"].items()
                  if "fast" in row.get("tiers", {}))
    assert fast == sorted(FAST)
    for model, want in FAST.items():
        facts = table.lookup(model)
        paid = cost.prices(facts, table.defaults.get(facts.provider), speed="fast")
        assert paid.tier == "fast" and paid.from_tier_input == () and paid.from_row == ()
        assert (paid.input, paid.output, paid.cache_read, paid.cache_write_5m,
                paid.cache_write_1h) == want, model


def test_an_opus_5_5_fast_cache_read_costs_40_cents_a_million():
    from moxi_usage import cost

    table = models.Table(models.shipped())
    facts = table.lookup("claude-opus-5-5")
    sums = cost.Sums(requests=1, input=1_000_000, cache_read=1_000_000)
    priced = cost.price(sums, facts, table.defaults.get("anthropic"), speed="fast")
    read = next(t for t in priced.terms if t.cls == "cache_read")
    assert (read.price, read.price_from) == (0.4, cost.FROM_TIER)
    assert read.cost == pytest.approx(0.40)


@pytest.mark.parametrize("model", ["big-pickle", "minimax-m3.1-flash-free",
                                   "space-bunny-free"])
def test_opencodes_free_models_are_priced_at_0_and_get_no_multiplier(model):
    from moxi_usage import thresholds

    table = models.Table(models.shipped())
    facts = table.lookup(model)
    assert step(facts) == (2, model) and facts.priced
    assert facts.provider == "opencode" and facts.provenance.filled == ()
    assert (facts.input, facts.output) == (0.0, 0.0)
    assert facts.cache_read is UNKNOWN and facts.cache_write_1h is UNKNOWN
    assert thresholds.read_multiplier(model, table=table)[0] is None
    w, why = thresholds.write_multiplier(facts, 1.0)
    assert w is None and "not above 0" in why


def test_the_newer_rows_come_from_the_supplement_with_its_source_and_date():
    rows = models.shipped()["models"]
    for model, provider in NEWER.items():
        provenance = rows[model]["provenance"]
        assert provenance["source"] == "supplement" and rows[model]["provider"] == provider
        (cited,) = provenance["supplement"]
        assert cited["source"] and cited["as_of"] == "2026-11-20"
