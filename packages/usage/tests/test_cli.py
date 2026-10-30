"""`moxi usage prices` and `prices update`, with the fetch handed in: no test reaches the
network."""

import json

import pytest

from conftest import write_config
from moxi_usage import cli, models, prices

LITELLM = {
    "claude-opus-5-5": {"mode": "chat", "litellm_provider": "anthropic",
                        "input_cost_per_token": 3e-06, "output_cost_per_token": 2e-05,
                        "cache_creation_input_token_cost_above_1hr": 8e-06,
                        "cache_read_input_token_cost_above_200k_tokens": 4e-07,
                        "input_cost_per_token_priority": 6e-06,
                        "max_input_tokens": 1000000, "max_output_tokens": 128000},
    "brand-new-1": {"mode": "responses", "litellm_provider": "openai",
                    "input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06,
                    "cache_read_input_token_cost": 1e-07,
                    "cache_creation_input_token_cost": 1e-06},
}


def big_litellm() -> dict:
    """The shipped table's rows back as a LiteLLM file, with two changes: one model
    repriced and one added, and the rest as they were."""
    out = {}
    for key, row in models.shipped()["models"].items():
        if row.get("provenance", {}).get("source") != "litellm":
            continue
        line = {"mode": "chat", "litellm_provider": row.get("provider", "x")}
        for ours, theirs in (("input", "input_cost_per_token"),
                             ("output", "output_cost_per_token")):
            if ours in row:
                line[theirs] = row[ours] / 1e6
        for ours, theirs in (("cache_read", "cache_read_input_token_cost"),
                             ("cache_write_5m", "cache_creation_input_token_cost"),
                             ("cache_write_1h", "cache_creation_input_token_cost_above_1hr")):
            if ours in row:
                line[theirs] = row[ours] / 1e6
        for ours, theirs in (("window", "max_input_tokens"), ("max_output", "max_output_tokens")):
            if ours in row:
                line[theirs] = row[ours]
        out[key] = line
    out.update(LITELLM)
    return out


@pytest.fixture
def fetched(monkeypatch):
    """Hand `prices.fetch` a file; record the URLs asked for."""
    asked = []

    def give(body):
        def fake(url):
            asked.append(url)
            if isinstance(body, Exception):
                raise body
            return body if isinstance(body, bytes) else json.dumps(body).encode()
        monkeypatch.setattr(prices, "fetch", fake)
        return asked
    return give


def test_prices_model_text_and_json(capsys):
    assert cli.main(["prices", "claude-opus-5-5"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("claude-opus-5-5  (exact, shipped layer, snapshot of ")
    assert "input" in out and "window" in out and "tier fast" in out
    assert cli.main(["prices", "claude-opus-5-5[1m]", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["window"] == 1000000 and data["provenance"]["variant"] == "[1m]"


def test_prices_unknown_model_says_unpriced_and_exits_1(capsys):
    assert cli.main(["prices", "nobody-knows-me"]) == 1
    assert "unpriced, never as $0" in capsys.readouterr().out
    assert cli.main(["prices", "nobody-knows-me", "--json"]) == 1
    assert "input" in json.loads(capsys.readouterr().out)["unknown"]


def test_prices_summary_names_the_layers(capsys):
    write_config('[[model]]\nid = "mine-1"\ninput = 1\noutput = 2\n')
    assert cli.main(["prices"]) == 0
    out = capsys.readouterr().out
    assert "shipped" in out and "1 [[model]] rows" in out
    assert cli.main(["prices", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert [layer["layer"] for layer in summary["layers"]] == ["shipped", "override"]


def test_a_bad_config_exits_2(capsys):
    write_config('[[model]]\nid = "x-1"\n')
    assert cli.main(["prices", "x-1"]) == 2
    assert "no fact" in capsys.readouterr().err


def test_update_writes_the_snapshot_and_names_the_changes(fetched, capsys):
    asked = fetched(big_litellm())
    assert cli.main(["prices", "update", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(asked) == 1 and asked[0].startswith("https://raw.githubusercontent.com/")
    assert result["written"] and result["added"] == ["brand-new-1"]
    assert "claude-opus-5-5" in result["repriced"]
    snapshot = json.loads(models.snapshot_path().read_text())
    assert snapshot["snapshot"]["sha256"] == result["snapshot"]["sha256"]
    facts = models.model_facts("claude-opus-5-5")
    assert facts.input == 3.0 and facts.provenance.layer == "snapshot"
    assert facts.tiers["fast"]["input"] == 8.0      # the supplement rides along
    assert models.snapshot_path().stat().st_mode & 0o777 == 0o600


def test_update_text_and_dry_run_write_nothing(fetched, capsys):
    fetched(big_litellm())
    assert cli.main(["prices", "update", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "added     1: brand-new-1" in out and "dry run: nothing written" in out
    assert not models.snapshot_path().exists()


def test_update_uses_the_configured_url(fetched):
    asked = fetched(big_litellm())
    write_config('[prices]\nurl = "https://example.invalid/prices.json"\n')
    assert cli.main(["prices", "update", "--dry-run"]) == 0
    assert asked == ["https://example.invalid/prices.json"]


def test_a_small_result_is_refused(fetched, capsys):
    fetched(LITELLM)
    assert cli.main(["prices", "update"]) == 3
    assert "under half" in capsys.readouterr().err
    assert not models.snapshot_path().exists()


def test_a_renamed_field_is_refused(fetched, capsys):
    renamed = {key: {("max_context" if k == "max_input_tokens" else k): v
                     for k, v in row.items()} for key, row in big_litellm().items()}
    fetched(renamed)
    assert cli.main(["prices", "update"]) == 3
    assert "max_input_tokens" in capsys.readouterr().err


@pytest.mark.parametrize("body", [prices.FetchError("no route to host"), b"<html>oops"])
def test_a_failed_fetch_exits_75(fetched, body, capsys):
    fetched(body)
    assert cli.main(["prices", "update"]) == 75
    assert capsys.readouterr().err
    assert not models.snapshot_path().exists()


def test_revert_removes_the_snapshot(fetched, capsys):
    fetched(big_litellm())
    assert cli.main(["prices", "update"]) == 0
    assert cli.main(["prices", "update", "--revert", "--dry-run"]) == 0
    assert models.snapshot_path().exists()
    assert cli.main(["prices", "update", "--revert"]) == 0
    assert not models.snapshot_path().exists()
    assert models.model_facts("claude-opus-5-5").provenance.layer == "shipped"
    capsys.readouterr()
    assert cli.main(["prices", "update", "--revert", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"reverted": False, "path": None}


def test_the_real_fetch_reports_a_failure_as_fetch_error(monkeypatch):
    import urllib.request

    def refuse(*args, **kwargs):
        raise OSError("network is off in tests")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(prices.FetchError, match="network is off"):
        prices.fetch("https://example.invalid/x.json")
