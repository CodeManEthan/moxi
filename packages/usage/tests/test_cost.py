"""A group's cost as its five terms: each term's tokens, price and dollars, the cost as
their sum, and the unknowns."""

import random

import pytest

from conftest import doc
from moxi_sessions.api import UNKNOWN
from moxi_usage import cost, models

ANTHROPIC = {"cache_read": 0.1, "cache_write_5m": 1.25, "cache_write_1h": 2.0}
OPENAI = {"cache_write_5m": 1.0, "cache_write_1h": 1.0}
TABLE = models.Table(doc({
    "m-a": {"input": 3.0, "output": 15.0, "provider": "anthropic",
            "tiers": {"fast": {"speed": "fast", "input": 6.0, "output": 30.0}}},
    "gpt-x": {"input": 1.0, "output": 8.0, "cache_read": 0.1, "provider": "openai"},
    "half": {"input": 1.0, "output": 2.0, "provider": "other"},
}, defaults={"anthropic": ANTHROPIC, "openai": OPENAI}))

SUMS = cost.Sums(requests=3, input=1500, fresh=500, cache_read=400, cache_write=600,
                 write_5m=350, cache_write_1h=250, output=120)


def old_total(sums: cost.Sums, paid: cost.Prices) -> float:
    """The cost as `price` worked it out before it kept its terms."""
    total = 0.0
    for field, tokens in cost.term_tokens(sums):
        if tokens:
            total += tokens * getattr(paid, field)
    return total / cost.PER_MILLION


def test_each_term_is_tokens_times_price_and_the_cost_is_their_sum():
    priced = cost.price(SUMS, TABLE.lookup("m-a"), ANTHROPIC)
    assert [t.cls for t in priced.terms] == list(cost.PRICE_TERMS)
    assert [(t.tokens, t.price) for t in priced.terms] == [
        (500, 3.0), (400, 0.3), (350, 3.75), (250, 6.0), (120, 15.0)]
    for term in priced.terms:
        assert term.cost == term.tokens * term.price / 1e6
        assert term.price_from == "row"
    assert priced.cost == sum(t.cost for t in priced.terms)
    assert priced.priced and priced.unpriced_tokens == 0


def test_a_fast_groups_cache_terms_ride_on_the_tier_input():
    priced = cost.price(SUMS, TABLE.lookup("m-a"), ANTHROPIC, speed="fast")
    assert priced.prices.tier == "fast"
    froms = {t.cls: (t.price_from, t.price) for t in priced.terms}
    assert froms == {"input": ("tier", 6.0), "output": ("tier", 30.0),
                     "cache_read": ("tier-input", 0.6),
                     "cache_write_5m": ("tier-input", 7.5),
                     "cache_write_1h": ("tier-input", 12.0)}
    assert priced.cost == sum(t.cost for t in priced.terms)


def test_a_term_with_no_tokens_and_no_price_leaves_the_group_priced():
    sums = cost.Sums(requests=1, input=1000, fresh=1000, output=100)
    priced = cost.price(sums, TABLE.lookup("half"), None)
    assert priced.priced and priced.cost == pytest.approx((1000 * 1 + 100 * 2) / 1e6)
    write = next(t for t in priced.terms if t.cls == "cache_write_5m")
    assert write.tokens == 0 and write.cost == 0 and write.price is UNKNOWN


def test_a_term_with_tokens_and_no_price_unprices_every_term():
    sums = cost.Sums(requests=1, input=1100, fresh=1000, cache_write=100, write_5m=100,
                     output=100)
    priced = cost.price(sums, TABLE.lookup("half"), None)
    assert priced.cost is UNKNOWN and priced.unpriced_tokens == 1200
    assert priced.why == "no cache_write_5m price"
    assert all(t.cost is UNKNOWN for t in priced.terms)
    # each term keeps its tokens, and its price where the table has one
    assert [(t.cls, t.tokens, t.price) for t in priced.terms] == [
        ("input", 1000, 1.0), ("cache_read", 0, UNKNOWN), ("cache_write_5m", 100, UNKNOWN),
        ("cache_write_1h", 0, UNKNOWN), ("output", 100, 2.0)]


def test_an_unknown_model_keeps_its_tokens_with_no_price():
    priced = cost.price(SUMS, TABLE.lookup("mystery-9"), None)
    assert priced.cost is UNKNOWN and priced.why == "the model table does not know the model"
    assert [(t.tokens, t.price, t.cost, t.price_from) for t in priced.terms] == [
        (500, UNKNOWN, UNKNOWN, None), (400, UNKNOWN, UNKNOWN, None),
        (350, UNKNOWN, UNKNOWN, None), (250, UNKNOWN, UNKNOWN, None),
        (120, UNKNOWN, UNKNOWN, None)]


def test_the_openai_write_rule_shows_on_the_group():
    sums = cost.Sums(requests=1, input=1000, fresh=600, cache_read=400, output=50,
                     unknown_any=1, unknown_write=1)
    priced = cost.price(sums, TABLE.lookup("gpt-x"), OPENAI)
    assert priced.rule == cost.rule_name("openai") and priced.unknown_rows == 0
    assert priced.cost == sum(t.cost for t in priced.terms)
    assert priced.cost == pytest.approx((600 * 1.0 + 400 * 0.1 + 50 * 8.0) / 1e6)


def test_the_sum_of_terms_is_the_old_total_within_a_billionth_of_a_dollar():
    rng = random.Random(7)
    for _ in range(500):
        sums = cost.Sums(requests=1, fresh=rng.randrange(10**9),
                         cache_read=rng.randrange(10**10), write_5m=rng.randrange(10**8),
                         cache_write_1h=rng.randrange(10**8), output=rng.randrange(10**8))
        speed = rng.choice((None, "fast"))
        priced = cost.price(sums, TABLE.lookup("m-a"), ANTHROPIC, speed=speed)
        assert abs(priced.cost - old_total(sums, priced.prices)) < 1e-9
