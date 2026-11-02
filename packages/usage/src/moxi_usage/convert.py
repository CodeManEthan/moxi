"""The converter: LiteLLM's model price file plus the supplement, into usage's model table.

`convert(litellm, supplement, *, date, sha256, url)` returns the table as a dict in the
shape `data/models.json` ships in and `moxi usage prices update` writes its snapshot in:

    {"schema": 1,
     "snapshot": {"source": "litellm", "url": ..., "date": "YYYY-MM-DD", "sha256": ...},
     "supplement": {"as_of": ...},
     "defaults": {provider: {"cache_read": m, ..., "source": ..., "as_of": ...}},
     "variants": {"[1m]": {"window": 1000000, "source": ..., "as_of": ...}},
     "prefixes": {prefix: {...facts, "provenance": ...}},
     "models": {id: {...facts, "provenance": ...}}}

A row's facts are `provider`, the prices `input`, `output`, `cache_read`,
`cache_write_5m` and `cache_write_1h` in dollars per million tokens, `window` and
`max_output` in tokens, and `tiers`, each a number or absent when unknown.

What is kept from LiteLLM: every row whose `mode` is `chat`, `responses` or `completion`
and which gives an input or output price per token. A model's facts never depend on which
API endpoint LiteLLM lists it for, so the three modes are kept alike. Per row:

    input_cost_per_token                        input
    output_cost_per_token                       output
    cache_read_input_token_cost                 cache_read
    cache_creation_input_token_cost             cache_write_5m (the 5-minute write)
    cache_creation_input_token_cost_above_1hr   cache_write_1h (the 1-hour write)
    max_input_tokens                            window
    max_output_tokens                           max_output
    litellm_provider                            provider

and, as tiers, any of the five prices with an `_above_<N>k_tokens` suffix (a prompt-size
tier, `above_<N>k`, with `above_tokens` N * 1000), a `_priority` or `_flex` suffix (a
service tier, with `service_tier`), or both (`above_<N>k_priority`). The supplement adds
speed tiers (`fast`, with `speed`). Other LiteLLM prices
(batch, per-image, per-audio-token, longer cache lifetimes) are not kept.

`check_fields(litellm)` says which of the field names above the file does not carry, so a
LiteLLM rename stops the conversion instead of emptying a column. The supplement's rows
are laid over LiteLLM's, field by field, and a row's `provenance` names both. An
exact supplement row that adds to a model LiteLLM no longer lists, and gives no input or
output price of its own, is left out and named, so it never stands as an unpriced row.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

SCHEMA = 1

#: The LiteLLM modes a row is kept for: every one that prices text generation per token.
MODES = ("chat", "responses", "completion")

#: LiteLLM's price fields, in dollars per token, and the table's name for each.
PRICES = {
    "input_cost_per_token": "input",
    "output_cost_per_token": "output",
    "cache_read_input_token_cost": "cache_read",
    "cache_creation_input_token_cost_above_1hr": "cache_write_1h",
    "cache_creation_input_token_cost": "cache_write_5m",
}
#: LiteLLM's token limits, and the table's name for each.
LIMITS = {"max_input_tokens": "window", "max_output_tokens": "max_output"}
PROVIDER = "litellm_provider"
MODE = "mode"
#: Every LiteLLM field name the converter reads by name.
FIELD_NAMES = (*PRICES, *LIMITS, PROVIDER, MODE)
#: The service tiers kept.
SERVICE_TIERS = ("priority", "flex")

#: The table's price fields and limit fields.
PRICE_FIELDS = ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h")
LIMIT_FIELDS = ("window", "max_output")
FACT_FIELDS = (*PRICE_FIELDS, *LIMIT_FIELDS)

# A price field with an optional tier suffix. The 1-hour write comes first among the
# alternatives so it is never read as the 5-minute write with a suffix.
_TIERED = re.compile(
    "^(" + "|".join(re.escape(name) for name in PRICES) + ")"
    r"(?:_above_(\d+)k_tokens)?(?:_(" + "|".join(SERVICE_TIERS) + "))?$")
_PER_MILLION = 1_000_000


class ConvertError(ValueError):
    """A LiteLLM file or supplement the converter cannot read."""


def check_fields(litellm: Mapping[str, Any]) -> list[str]:
    """The field names the converter reads that no kept row of `litellm` carries: the
    names in `FIELD_NAMES`, and one example each of a prompt-size tier and a service
    tier. Empty when every name is there."""
    seen: set[str] = set()
    above = service = False
    for row in litellm.values():
        if not isinstance(row, Mapping):
            continue
        seen.update(row)
        if row.get(MODE) not in MODES:
            continue
        for key in row:
            matched = _TIERED.match(key)
            if matched:
                above = above or matched.group(2) is not None
                service = service or matched.group(3) is not None
    missing = [name for name in FIELD_NAMES if name not in seen]
    if not above:
        missing.append("*_above_<N>k_tokens")
    if not service:
        missing.append("*_priority / *_flex")
    return missing


def convert(litellm: Mapping[str, Any], supplement: Mapping[str, Any], *, date: str,
            sha256: str, url: str | None = None,
            dropped: list[str] | None = None) -> dict[str, Any]:
    """The model table from a parsed LiteLLM file and a parsed supplement. Raises
    `ConvertError` when the LiteLLM file is not a JSON object, lacks a field name the
    converter reads, or the supplement is malformed.

    An exact supplement row that finds no LiteLLM row and gives neither `input` nor
    `output` is left out, since it would make a row with no base price that replaces a
    priced one in a table below. Its id is appended to `dropped` when one is given."""
    if not isinstance(litellm, Mapping):
        raise ConvertError("the LiteLLM file is not a JSON object")
    missing = check_fields(litellm)
    if missing:
        raise ConvertError("the LiteLLM file carries none of these fields the converter "
                           f"reads: {', '.join(missing)}; check LiteLLM's names and "
                           "update the converter")
    models: dict[str, dict[str, Any]] = {}
    for key, row in litellm.items():
        converted = _row(row)
        if converted is not None:
            converted["provenance"] = {"source": "litellm", "key": key}
            models[key] = converted
    supplement = check_supplement(supplement)
    prefixes: dict[str, dict[str, Any]] = {}
    for row in supplement["model"]:
        target = prefixes if row.get("match") == "prefix" else models
        if (target is models and row["id"] not in models
                and "input" not in row and "output" not in row):
            if dropped is not None:
                dropped.append(row["id"])
            continue
        _lay(target, row)
    snapshot = {"source": "litellm", "date": date, "sha256": sha256}
    if url:
        snapshot["url"] = url
    return {
        "schema": SCHEMA,
        "snapshot": snapshot,
        "supplement": {"as_of": supplement.get("as_of", "")},
        "defaults": supplement["defaults"],
        "variants": supplement["variants"],
        "prefixes": dict(sorted(prefixes.items())),
        "models": dict(sorted(models.items())),
    }


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


def _per_million(value: float) -> float:
    """Dollars per token as dollars per million tokens, without the float noise of the
    multiplication (4e-06 is 4.0, not 3.9999999999999996)."""
    return float(f"{value * _PER_MILLION:.12g}")


def _tokens(value: Any) -> int | None:
    number = _number(value)
    if number is None or number <= 0 or number != int(number):
        return None
    return int(number)


def _row(row: Any) -> dict[str, Any] | None:
    """One LiteLLM row as the table's facts, or None when it is not kept."""
    if not isinstance(row, Mapping) or row.get(MODE) not in MODES:
        return None
    out: dict[str, Any] = {}
    tiers: dict[str, dict[str, Any]] = {}
    for key, value in row.items():
        matched = _TIERED.match(key)
        if matched is None:
            continue
        price = _number(value)
        if price is None:
            continue
        name, above, service = matched.groups()
        field = PRICES[name]
        if above is None and service is None:
            out[field] = _per_million(price)
            continue
        tier_name = "_".join(part for part in
                             ((f"above_{above}k" if above else None), service) if part)
        tier = tiers.setdefault(tier_name, {})
        if above:
            tier["above_tokens"] = int(above) * 1000
        if service:
            tier["service_tier"] = service
        tier[field] = _per_million(price)
    if "input" not in out and "output" not in out:
        return None
    for key, field in LIMITS.items():
        tokens = _tokens(row.get(key))
        if tokens is not None:
            out[field] = tokens
    provider = row.get(PROVIDER)
    if isinstance(provider, str) and provider:
        out["provider"] = provider
    if tiers:
        out["tiers"] = dict(sorted(tiers.items()))
    return dict(sorted(out.items()))


# --- the supplement -------------------------------------------------------------------

_ROW_KEYS = frozenset({"id", "match", "provider", "tiers", "source", "as_of",
                       *FACT_FIELDS})
_TIER_KEYS = frozenset({*PRICE_FIELDS, "above_tokens", "service_tier", "speed"})
_MULTIPLIER_KEYS = frozenset({"cache_read", "cache_write_5m", "cache_write_1h"})
_DIGIT = re.compile(r"\d")


def check_supplement(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The supplement checked and normalised: `defaults`, `variants` and `model` rows,
    every one with a `source` and an `as_of`. Raises `ConvertError` naming the first
    thing wrong."""
    if not isinstance(doc, Mapping):
        raise ConvertError("the supplement is not a table")
    if doc.get("schema") != SCHEMA:
        raise ConvertError(f"the supplement's schema is {doc.get('schema')!r}, not {SCHEMA}")
    unknown = sorted(set(doc) - {"schema", "as_of", "defaults", "variants", "model"})
    if unknown:
        raise ConvertError(f"the supplement has unknown keys: {', '.join(unknown)}")
    defaults: dict[str, dict[str, Any]] = {}
    for provider, row in (doc.get("defaults") or {}).items():
        where = f"defaults.{provider}"
        _cited(row, where)
        extra = sorted(set(row) - _MULTIPLIER_KEYS - {"source", "as_of"})
        if extra:
            raise ConvertError(f"{where} has unknown keys: {', '.join(extra)}")
        for key in _MULTIPLIER_KEYS & set(row):
            if _number(row[key]) is None:
                raise ConvertError(f"{where}.{key} is not a non-negative number")
        defaults[provider] = {key: (float(v) if key in _MULTIPLIER_KEYS else v)
                              for key, v in sorted(row.items())}
    variants: dict[str, dict[str, Any]] = {}
    for suffix, row in (doc.get("variants") or {}).items():
        where = f"variants.{suffix!r}"
        if not (suffix.startswith("[") and suffix.endswith("]") and len(suffix) > 2):
            raise ConvertError(f"{where}: a variant is a bracket suffix such as \"[1m]\"")
        _cited(row, where)
        extra = sorted(set(row) - set(FACT_FIELDS) - {"source", "as_of"})
        if extra:
            raise ConvertError(f"{where} has unknown keys: {', '.join(extra)}")
        variants[suffix] = _facts(row, where, keep=("source", "as_of"))
    rows = []
    for n, row in enumerate(doc.get("model") or []):
        where = f"model[{n}]"
        if not isinstance(row, Mapping) or not isinstance(row.get("id"), str) \
                or not row["id"]:
            raise ConvertError(f"{where} needs an id")
        where = f"model[{n}] ({row['id']})"
        _cited(row, where)
        extra = sorted(set(row) - _ROW_KEYS)
        if extra:
            raise ConvertError(f"{where} has unknown keys: {', '.join(extra)}")
        match = row.get("match", "exact")
        if match not in ("exact", "prefix"):
            raise ConvertError(f"{where}.match is {match!r}, not exact or prefix")
        if match == "prefix" and not _DIGIT.search(row["id"]):
            raise ConvertError(f"{where}: a prefix row's id carries no digit, so it "
                               "could cover every future model")
        rows.append(_facts(row, where, keep=("id", "match", "provider", "source", "as_of")))
    as_of = doc.get("as_of", "")
    return {"as_of": as_of if isinstance(as_of, str) else str(as_of),
            "defaults": dict(sorted(defaults.items())),
            "variants": dict(sorted(variants.items())), "model": rows}


def _cited(row: Any, where: str) -> None:
    if not isinstance(row, Mapping):
        raise ConvertError(f"{where} is not a table")
    for key in ("source", "as_of"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise ConvertError(f"{where} needs a {key}: every supplement row says where "
                               "its facts came from and when they were read")


def _facts(row: Mapping[str, Any], where: str, *, keep: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {key: row[key] for key in keep if key in row}
    for field in PRICE_FIELDS:
        if field in row:
            if _number(row[field]) is None:
                raise ConvertError(f"{where}.{field} is not a non-negative number")
            out[field] = float(row[field])
    for field in LIMIT_FIELDS:
        if field in row:
            if _tokens(row[field]) is None:
                raise ConvertError(f"{where}.{field} is not a whole number of tokens")
            out[field] = int(row[field])
    if "tiers" in row:
        tiers = row["tiers"]
        if not isinstance(tiers, Mapping):
            raise ConvertError(f"{where}.tiers is not a table")
        out["tiers"] = {}
        for name, tier in tiers.items():
            if not isinstance(tier, Mapping):
                raise ConvertError(f"{where}.tiers.{name} is not a table")
            extra = sorted(set(tier) - _TIER_KEYS)
            if extra:
                raise ConvertError(f"{where}.tiers.{name} has unknown keys: "
                                   f"{', '.join(extra)}")
            out["tiers"][name] = _facts(tier, f"{where}.tiers.{name}",
                                        keep=("service_tier", "speed"))
            if "above_tokens" in tier:
                if _tokens(tier["above_tokens"]) is None:
                    raise ConvertError(f"{where}.tiers.{name}.above_tokens is not a whole "
                                       "number of tokens")
                out["tiers"][name]["above_tokens"] = int(tier["above_tokens"])
    return out


def _lay(target: dict[str, dict[str, Any]], row: Mapping[str, Any]) -> None:
    """Lay one supplement row over the table's row of the same id, field by field (a
    tier by tier name), recording which fields it gave."""
    key = row["id"]
    facts = {k: v for k, v in row.items() if k not in ("id", "match", "source", "as_of")}
    fields = sorted(f"tiers.{name}" if k == "tiers" else k
                    for k, v in facts.items()
                    for name in (v if k == "tiers" else (None,)))
    cited = {"fields": fields, "source": row["source"], "as_of": row["as_of"]}
    current = target.get(key)
    if current is None:
        current = {"provenance": {"source": "supplement"}}
        target[key] = current
    for k, v in facts.items():
        if k == "tiers":
            tiers = dict(current.get("tiers") or {})
            for name, tier in v.items():
                tiers[name] = {**tiers.get(name, {}), **tier}
            current["tiers"] = dict(sorted(tiers.items()))
        else:
            current[k] = v
    provenance = current.pop("provenance")
    provenance = {**provenance, "supplement": [*provenance.get("supplement", ()), cited]}
    ordered = dict(sorted(current.items()))
    ordered["provenance"] = provenance
    target[key] = ordered


# --- writing --------------------------------------------------------------------------

def dumps(table: Mapping[str, Any]) -> str:
    """The table as JSON text with one model per line, so a change to the table reads as
    a change to the lines of the models it touched."""
    import json

    def one(value: Any) -> str:
        return json.dumps(value, sort_keys=False, separators=(", ", ": "),
                          ensure_ascii=False)

    lines = ["{"]
    keys = list(table)
    for i, key in enumerate(keys):
        value = table[key]
        comma = "," if i < len(keys) - 1 else ""
        if isinstance(value, Mapping) and key in ("models", "prefixes", "defaults",
                                                  "variants") and value:
            lines.append(f"  {one(key)}: {{")
            ids = list(value)
            for j, row_id in enumerate(ids):
                inner = "," if j < len(ids) - 1 else ""
                lines.append(f"    {one(row_id)}: {one(value[row_id])}{inner}")
            lines.append(f"  }}{comma}")
        else:
            lines.append(f"  {one(key)}: {one(value)}{comma}")
    lines.append("}")
    return "\n".join(lines) + "\n"
