"""The converter: which LiteLLM rows it keeps, how it maps them, and the supplement."""

import json
import tomllib
from pathlib import Path

import pytest

from moxi_usage import convert

DATA = Path(convert.__file__).resolve().parent / "data"


def litellm() -> dict:
    """A LiteLLM file in miniature, in its field names."""
    return {
        "sample_spec": {"mode": "one of: chat, embedding", "input_cost_per_token": 0.0},
        "claude-x-1": {"mode": "chat", "litellm_provider": "anthropic",
                       "input_cost_per_token": 4e-06, "output_cost_per_token": 2e-05,
                       "cache_read_input_token_cost": 2e-07,
                       "cache_creation_input_token_cost": 5e-06,
                       "cache_creation_input_token_cost_above_1hr": 8e-06,
                       "cache_creation_input_token_cost_above_1hr_above_100k_tokens": 1e-05,
                       "input_cost_per_token_batches": 2e-06,
                       "max_input_tokens": 1000000, "max_output_tokens": 128000},
        "codex-y": {"mode": "responses", "litellm_provider": "openai",
                    "input_cost_per_token": 1.75e-06, "output_cost_per_token": 1.4e-05,
                    "input_cost_per_token_priority": 3.5e-06,
                    "output_cost_per_token_above_272k_tokens_flex": 1.5e-05,
                    "max_input_tokens": 272000},
        "old-z": {"mode": "completion", "litellm_provider": "text-completion",
                  "input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06},
        "embed-e": {"mode": "embedding", "input_cost_per_token": 1e-07,
                    "output_cost_per_token": 0},
        "image-i": {"mode": "image_generation", "output_cost_per_image": 0.04},
        "unpriced-chat": {"mode": "chat", "litellm_provider": "x", "max_input_tokens": 8000},
        "max-output-only": {"mode": "chat", "output_cost_per_token": 1e-06,
                            "max_output_tokens": "lots",
                            "cache_creation_input_token_cost_above_200k_tokens": 3e-06},
    }


def supplement(**changes) -> dict:
    base = {"schema": 1, "as_of": "2026-10-30",
            "defaults": {"anthropic": {"cache_read": 0.1, "source": "s", "as_of": "d"}},
            "variants": {"[1m]": {"window": 1000000, "source": "s", "as_of": "d"}},
            "model": [{"id": "claude-x-1", "tiers": {"fast": {"speed": "fast", "input": 8,
                                                             "output": 40}},
                       "source": "fast pricing", "as_of": "2026-09-25"}]}
    base.update(changes)
    return base


def test_keeps_chat_responses_and_completion_rows_that_price_tokens():
    table = convert.convert(litellm(), supplement(), date="2026-10-30", sha256="ab")
    assert sorted(table["models"]) == ["claude-x-1", "codex-y", "max-output-only", "old-z"]
    assert table["snapshot"] == {"source": "litellm", "date": "2026-10-30", "sha256": "ab"}


def test_maps_fields_per_million_tokens_and_tiers():
    row = convert.convert(litellm(), supplement(), date="d", sha256="s")["models"]["claude-x-1"]
    assert (row["input"], row["output"], row["cache_read"]) == (4.0, 20.0, 0.2)
    assert (row["cache_write_5m"], row["cache_write_1h"]) == (5.0, 8.0)
    assert (row["window"], row["max_output"], row["provider"]) == (1000000, 128000, "anthropic")
    assert row["tiers"]["above_100k"] == {"above_tokens": 100000, "cache_write_1h": 10.0}
    assert row["tiers"]["fast"] == {"speed": "fast", "input": 8.0, "output": 40.0}
    assert "batches" not in json.dumps(row)


def test_service_and_combined_tiers():
    row = convert.convert(litellm(), supplement(), date="d", sha256="s")["models"]["codex-y"]
    assert row["tiers"] == {
        "above_272k_flex": {"above_tokens": 272000, "service_tier": "flex", "output": 15.0},
        "priority": {"service_tier": "priority", "input": 3.5}}


def test_a_bad_limit_is_left_out_not_guessed():
    row = convert.convert(litellm(), supplement(), date="d", sha256="s")["models"]["max-output-only"]
    assert "max_output" not in row and "input" not in row


def test_supplement_row_is_laid_over_and_cited():
    row = convert.convert(litellm(), supplement(), date="d", sha256="s")["models"]["claude-x-1"]
    assert row["provenance"] == {"source": "litellm", "key": "claude-x-1", "supplement": [
        {"fields": ["tiers.fast"], "source": "fast pricing", "as_of": "2026-09-25"}]}


def test_supplement_only_row_and_prefix_row():
    extra = supplement(model=[
        {"id": "new-1", "input": 1, "output": 2, "source": "s", "as_of": "d"},
        {"id": "fam-2-", "match": "prefix", "input": 3, "output": 4, "source": "s",
         "as_of": "d"}])
    table = convert.convert(litellm(), extra, date="d", sha256="s")
    assert table["models"]["new-1"]["provenance"]["source"] == "supplement"
    assert table["prefixes"]["fam-2-"]["input"] == 3.0


def test_a_supplement_row_with_no_base_price_for_a_model_litellm_dropped_is_left_out():
    extra = supplement(model=[
        {"id": "gone-1", "tiers": {"fast": {"speed": "fast", "input": 8, "output": 40}},
         "source": "s", "as_of": "d"},
        {"id": "gone-2", "window": 400000, "source": "s", "as_of": "d"}])
    dropped: list[str] = []
    table = convert.convert(litellm(), extra, date="d", sha256="s", dropped=dropped)
    assert "gone-1" not in table["models"] and "gone-2" not in table["models"]
    assert dropped == ["gone-1", "gone-2"]


@pytest.mark.parametrize("bad, said", [
    ({"model": [{"id": "m", "input": 1, "as_of": "d"}]}, "needs a source"),
    ({"model": [{"id": "fam-", "match": "prefix", "input": 1, "source": "s",
                 "as_of": "d"}]}, "no digit"),
    ({"model": [{"id": "m", "inptu": 1, "source": "s", "as_of": "d"}]}, "unknown keys"),
    ({"variants": {"1m": {"window": 1, "source": "s", "as_of": "d"}}}, "bracket suffix"),
    ({"defaults": {"openai": {"cache_read": -1, "source": "s", "as_of": "d"}}},
     "non-negative"),
    ({"schema": 2}, "schema"),
])
def test_supplement_refusals(bad, said):
    with pytest.raises(convert.ConvertError, match=said):
        convert.convert(litellm(), supplement(**bad), date="d", sha256="s")


def test_a_renamed_litellm_field_stops_the_conversion():
    renamed = {key: {("max_context" if k == "max_input_tokens" else k): v
                     for k, v in row.items()} for key, row in litellm().items()}
    assert convert.check_fields(renamed) == ["max_input_tokens"]
    with pytest.raises(convert.ConvertError, match="max_input_tokens"):
        convert.convert(renamed, supplement(), date="d", sha256="s")


def test_dumps_round_trips_one_model_per_line():
    table = convert.convert(litellm(), supplement(), date="d", sha256="s")
    text = convert.dumps(table)
    assert json.loads(text) == table
    assert sum(1 for line in text.splitlines() if line.startswith('    "')) >= 6


def test_shipped_supplement_reads_and_cites_every_row():
    checked = convert.check_supplement(
        tomllib.loads((DATA / "supplement.toml").read_text(encoding="utf-8")))
    assert checked["variants"]["[1m]"]["window"] == 1000000
    assert checked["defaults"]["anthropic"]["cache_write_1h"] == 2.0
    assert {row["id"] for row in checked["model"]} >= {"claude-opus-5-5", "claude-opus-5"}


def test_shipped_table_is_the_converters_shape():
    table = json.loads((DATA / "models.json").read_text(encoding="utf-8"))
    assert table["schema"] == 1 and table["snapshot"]["source"] == "litellm"
    assert len(table["snapshot"]["sha256"]) == 64
    assert len(table["models"]) > 1000
    for key in ("claude-opus-5-5", "gpt-5.3-codex", "claude-haiku-4-5"):
        assert table["models"][key]["input"] > 0
