"""What a group of requests cost, worked out when a report reads it and never stored.

A report sums the token columns of the claimed requests in SQL, one group per report key,
model, provider, speed, service tier, prompt-size tier and price period, and prices each
group here, through the model table (`moxi_usage.models`):

* fresh input = `input - cache_read - cache_write`, floored at 0 (sessions' `input` is the
  whole prompt, cache reads and writes included);
* the 5-minute write = `cache_write - cache_write_1h`, floored at 0;
* cost = fresh input x the input price + cache reads x the cache-read price + 5-minute
  writes x the 5-minute price + 1-hour writes x the 1-hour price + output x the output
  price. Reasoning is inside output, so it has no term of its own.

Prices are dollars per million tokens. The floors are taken per request, in SQL, before
the sums.

**Tiers.** A model's row may carry tiers (`above_200k`, `priority`, `flex`, `fast`,
`above_272k_priority`...), each with the conditions it applies under (`above_tokens`,
`service_tier`, `speed`) and its own prices. A group takes the tier whose conditions all
hold, the one with the most conditions first and then the highest `above_tokens`; no tier
holds, the row's own prices. A price the tier leaves out is, for a cache price, the tier's
input price times the provider's default multiplier when the table has one (so fast mode's
cache prices ride on the fast input price), else the row's own price for it.

**Unknown is never $0.** A model the table does not know, or a term with tokens whose
price is unknown, leaves the group unpriced: its tokens go to `unpriced_tokens` and its
cost is unknown. A request whose `unknown` names a token column the group sums counts in
`unknown_rows`, so its totals read as at least the sums shown. A cache-write count a
provider rule stands in for is the exception: when the provider bills cache writes as
input (OpenAI's default multipliers are 1.0) an unknown write count is priced inside fresh
input, the group says which rule applied, and those requests are not counted as unknown.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from moxi_sessions.api import UNKNOWN

from .models import CACHE_FIELDS, ModelFacts

#: The price fields a term reads, in the order of the cost's terms.
PRICE_TERMS = ("input", "cache_read", "cache_write_5m", "cache_write_1h", "output")
#: The conditions a tier can carry, and the request field each is matched against.
CONDITIONS = ("above_tokens", "service_tier", "speed")
PER_MILLION = 1_000_000


@dataclass(frozen=True, slots=True)
class Prices:
    """The prices one group pays, each a number or `UNKNOWN`, and where they came from:
    `tier` is the tier applied or None; `from_tier_input` names the cache prices a
    provider multiplier gave over the tier's input price; `from_row` names the prices a
    tier left out and the row's own gave."""

    input: Any
    output: Any
    cache_read: Any
    cache_write_5m: Any
    cache_write_1h: Any
    tier: str | None = None
    from_tier_input: tuple[str, ...] = ()
    from_row: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Sums:
    """One group's token sums. `fresh` and `write_5m` are the per-request floored terms.
    `unknown_core` counts the requests whose `unknown` names input, cache read or output;
    `unknown_any` those naming any of those or a cache-write column; `unknown_write`
    those naming a cache-write column."""

    requests: int = 0
    input: int = 0
    fresh: int = 0
    cache_read: int = 0
    cache_write: int = 0
    write_5m: int = 0
    cache_write_1h: int = 0
    output: int = 0
    unknown_core: int = 0
    unknown_any: int = 0
    unknown_write: int = 0

    @property
    def tokens(self) -> int:
        """The group's tokens: the whole prompt plus output."""
        return self.input + self.output


@dataclass(frozen=True, slots=True)
class Priced:
    """One group's price. `cost` is dollars, or `UNKNOWN` when the group is unpriced, and
    `why` then says what was missing. `unknown_rows` counts the requests with an unknown
    count the group sums. `rule` names the provider rule that stood in for an unknown
    cache-write count, or None."""

    cost: Any
    unpriced_tokens: int
    unknown_rows: int
    prices: Prices | None
    rule: str | None = None
    why: str | None = None

    @property
    def priced(self) -> bool:
        return self.cost is not UNKNOWN


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def matches(tier: Mapping[str, Any], *, size: int, service_tier: str | None,
            speed: str | None) -> bool:
    """True when every condition of `tier` holds for a request whose prompt is above
    `size` tokens (the group's size bound), with that service tier and speed."""
    above = tier.get("above_tokens")
    if above is not None and not (_number(above) and size >= above):
        return False
    if "service_tier" in tier and tier["service_tier"] != service_tier:
        return False
    if "speed" in tier and tier["speed"] != speed:
        return False
    return any(name in tier for name in CONDITIONS)


def pick_tier(tiers: Any, *, size: int, service_tier: str | None,
              speed: str | None) -> tuple[str, Mapping[str, Any]] | None:
    """The tier that applies, or None: of the tiers whose conditions all hold, the one
    with the most conditions, then the highest `above_tokens`, then the first by name."""
    if not isinstance(tiers, Mapping):
        return None
    best: tuple[tuple, str, Mapping[str, Any]] | None = None
    for name in sorted(tiers):
        tier = tiers[name]
        if not isinstance(tier, Mapping):
            continue
        if not matches(tier, size=size, service_tier=service_tier, speed=speed):
            continue
        rank = (sum(1 for c in CONDITIONS if c in tier), tier.get("above_tokens") or 0)
        if best is None or rank > best[0]:
            best = (rank, name, tier)
    return None if best is None else (best[1], best[2])


def prices(facts: ModelFacts, defaults: Mapping[str, Any] | None, *, size: int = 0,
           service_tier: str | None = None, speed: str | None = None) -> Prices:
    """The prices a group pays for `facts`'s model, its tier applied. `defaults` are the
    provider's default cache multipliers from the table, or None."""
    base = {name: getattr(facts, name) for name in PRICE_TERMS}
    picked = pick_tier(facts.tiers, size=size, service_tier=service_tier, speed=speed)
    if picked is None:
        return Prices(**base)
    name, tier = picked
    out: dict[str, Any] = {}
    from_tier_input: list[str] = []
    from_row: list[str] = []
    tier_input = tier.get("input")
    for field in PRICE_TERMS:
        value = tier.get(field)
        if _number(value):
            out[field] = value
        elif (field in CACHE_FIELDS and _number(tier_input) and defaults
              and _number(defaults.get(field))):
            out[field] = float(f"{tier_input * defaults[field]:.12g}")
            from_tier_input.append(field)
        else:
            out[field] = base[field]
            from_row.append(field)
    return Prices(**out, tier=name, from_tier_input=tuple(from_tier_input),
                  from_row=tuple(from_row))


def writes_as_input(defaults: Mapping[str, Any] | None) -> bool:
    """True when the provider's rule is that a cache write bills as input: its default
    multiplier for the 5-minute write is 1.0, and for the 1-hour write 1.0 or none."""
    if not defaults:
        return False
    one_hour = defaults.get("cache_write_1h", 1.0)
    return defaults.get("cache_write_5m") == 1.0 and one_hour == 1.0


def rule_name(provider: str) -> str:
    return f"{provider}: an unknown cache-write count is billed as input"


def price(sums: Sums, facts: ModelFacts, defaults: Mapping[str, Any] | None, *,
          size: int = 0, service_tier: str | None = None,
          speed: str | None = None) -> Priced:
    """Price one group's sums. See the module's text for the terms and the unknowns."""
    rule = None
    unknown_rows = sums.unknown_any
    if sums.unknown_write and writes_as_input(defaults):
        rule = rule_name(str(facts.provider))
        unknown_rows = sums.unknown_core
    if not facts.known:
        return Priced(UNKNOWN, sums.tokens, unknown_rows, None, rule,
                      why="the model table does not know the model")
    paid = prices(facts, defaults, size=size, service_tier=service_tier, speed=speed)
    terms = (("input", sums.fresh), ("cache_read", sums.cache_read),
             ("cache_write_5m", sums.write_5m), ("cache_write_1h", sums.cache_write_1h),
             ("output", sums.output))
    total = 0.0
    missing = []
    for field, tokens in terms:
        if not tokens:
            continue
        value = getattr(paid, field)
        if value is UNKNOWN or not _number(value):
            missing.append(field)
            continue
        total += tokens * value
    if missing:
        return Priced(UNKNOWN, sums.tokens, unknown_rows, paid, rule,
                      why=f"no {', '.join(missing)} price")
    return Priced(total / PER_MILLION, 0, unknown_rows, paid, rule)
