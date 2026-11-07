"""`retention()`: each harness's transcript retention as set, the doctor's `retention` row and
the setup that prints it. Nothing here writes a harness's settings."""

from __future__ import annotations

import json
import os

import pytest

from moxi_sessions import doctor, retention as kept, setup
from moxi_sessions.api import retention


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "own-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)
    return home


def by_harness(environ=None) -> dict[str, kept.Retention]:
    return {item.harness: item for item in retention(environ)}


def claude_settings(home, data) -> str:
    path = home / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return str(path)


def codex_config(home, text: str) -> str:
    path = home / ".codex" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_one_answer_per_harness_in_order(home):
    assert [item.harness for item in retention()] == ["claude", "codex", "opencode"]


# --- Claude Code ---------------------------------------------------------------------------

def test_claude_with_no_settings_file_is_thirty_days(home):
    item = by_harness()["claude"]
    assert item.file == str(home / ".claude" / "settings.json")
    assert item.key == "cleanupPeriodDays" and item.value is None and not item.warn
    assert item.lines[0] == (f"Claude Code: {item.file} doesn't exist, so cleanupPeriodDays "
                             "is not set: 30 days. Claude Code deletes transcripts older "
                             "than that.")
    assert "raise cleanupPeriodDays" in item.lines[1]


def test_claude_with_the_key_unset_is_thirty_days(home):
    claude_settings(home, {"model": "x"})
    item = by_harness()["claude"]
    assert item.value is None and not item.warn
    assert "cleanupPeriodDays is not set in" in item.lines[0]
    assert item.brief == "Claude Code: cleanupPeriodDays not set, 30 days"


def test_claude_prints_the_setting_as_set(home):
    path = claude_settings(home, {"cleanupPeriodDays": 36500})
    item = by_harness()["claude"]
    assert item.value == 36500 and not item.warn
    assert item.lines[0] == (f"Claude Code: cleanupPeriodDays is 36500 in {path}. Claude "
                             "Code deletes transcripts older than 36500 days.")


def test_claude_reads_under_claude_config_dir(home, monkeypatch, tmp_path):
    config = tmp_path / "elsewhere"
    config.mkdir()
    (config / "settings.json").write_text('{"cleanupPeriodDays": 90}', encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    item = by_harness()["claude"]
    assert item.file == str(config / "settings.json") and item.value == 90


def test_claude_reads_the_environment_handed_in(home, tmp_path):
    other = tmp_path / "other-home"
    claude_settings(other, {"cleanupPeriodDays": 7})
    assert by_harness({"HOME": str(other)})["claude"].value == 7
    assert by_harness()["claude"].value is None


@pytest.mark.parametrize("text", ["{not json", "[1, 2]"])
def test_a_claude_file_that_does_not_parse_warns(home, text):
    claude_settings(home, text)
    item = by_harness()["claude"]
    assert item.warn and item.value is None and item.reason
    assert "is not known" in item.lines[0] and "30 days" in item.lines[0]


@pytest.mark.parametrize("value", ["30", 1.5, True])
def test_a_claude_value_that_is_not_whole_days_warns(home, value):
    claude_settings(home, {"cleanupPeriodDays": value})
    item = by_harness()["claude"]
    assert item.warn and item.value == value
    assert "not a whole number of days" in item.lines[0]


@pytest.mark.parametrize("value", [0, -5])
def test_a_claude_value_below_one_day_warns(home, capsys, value):
    path = claude_settings(home, {"cleanupPeriodDays": value})
    item = by_harness()["claude"]
    assert item.warn and item.value == value
    assert item.lines[0] == (f"Claude Code: cleanupPeriodDays is {value} in {path}, which is "
                             "below one day, so how long Claude Code keeps transcripts is "
                             "not known.")
    assert item.brief == f"Claude Code: cleanupPeriodDays is {value}, below one day"
    assert "older than" not in " ".join(item.lines)
    row = doctor.retention_row()
    assert row.state == "warn" and f"cleanupPeriodDays is {value}, below one day" in row.detail
    setup.setup()
    assert f"sessions: {item.lines[0]}" in capsys.readouterr().out.splitlines()


# --- Codex ---------------------------------------------------------------------------------

def test_codex_has_no_age_limit_and_compression_unset_is_off(home):
    item = by_harness()["codex"]
    assert item.file == str(home / ".codex" / "config.toml")
    assert item.key == "features.local_thread_store_compression"
    assert item.value is None and not item.warn
    assert item.lines[0] == "Codex: no setting deletes rollouts by age."
    assert "is not set (off)" in item.lines[1]
    assert item.archive == str(home / ".codex" / "archived_sessions")
    assert item.archived == 0
    assert "holds no rollout" in item.lines[2]


def test_codex_compression_on_warns(home):
    path = codex_config(home, "[features]\nlocal_thread_store_compression = true\n")
    item = by_harness()["codex"]
    assert item.value is True and item.warn
    assert item.lines[1] == (f"Codex: features.local_thread_store_compression is on in "
                             f"{path}: Codex compresses rollouts to .jsonl.zst, which "
                             "sessions finds but can't read.")


def test_codex_compression_off(home):
    codex_config(home, 'model = "x"\n[features]\nlocal_thread_store_compression = false\n')
    item = by_harness()["codex"]
    assert item.value is False and not item.warn and "is off in" in item.lines[1]


def test_a_codex_value_that_is_not_a_switch_warns(home):
    codex_config(home, '[features]\nlocal_thread_store_compression = "yes"\n')
    item = by_harness()["codex"]
    assert item.warn and "not true or false" in item.lines[1]


def test_a_codex_config_that_does_not_parse_warns(home):
    codex_config(home, "[features\n")
    item = by_harness()["codex"]
    assert item.warn and item.reason and "cannot be parsed" in item.lines[1]


def test_codex_counts_the_archived_rollouts_under_codex_home(home, monkeypatch, tmp_path):
    codex = tmp_path / "codex"
    day = codex / "archived_sessions" / "2026" / "11" / "01"
    day.mkdir(parents=True)
    for n in range(3):
        (day / f"rollout-2026-11-01T10-00-0{n}-thread{n}.jsonl").write_text("{}\n")
    monkeypatch.setenv("CODEX_HOME", str(codex))
    item = by_harness()["codex"]
    assert item.archive == str(codex / "archived_sessions") and item.archived == 3
    assert "it holds 3 rollouts" in item.lines[2]


def test_opencode_says_no_setting_is_read(home):
    item = by_harness()["opencode"]
    assert item.file is None and not item.warn
    assert item.lines == ("OpenCode: sessions reads no retention setting for it.",)


def test_retention_writes_nothing(home):
    claude = claude_settings(home, {"cleanupPeriodDays": 5, "other": [1, 2]})
    codex = codex_config(home, "[features]\nlocal_thread_store_compression = true\n")
    before = {path: (open(path, "rb").read(), os.stat(path).st_mtime_ns)
              for path in (claude, codex)}
    listing = sorted(str(p) for p in home.rglob("*"))
    retention()
    list(doctor.rows())
    setup.setup()
    assert {path: (open(path, "rb").read(), os.stat(path).st_mtime_ns)
            for path in (claude, codex)} == before
    assert sorted(str(p) for p in home.rglob("*")) == listing


# --- the doctor row and the setup ---------------------------------------------------------

def test_the_doctor_row_is_ok_with_each_harness_said(home):
    claude_settings(home, {"cleanupPeriodDays": 36500})
    row = doctor.retention_row()
    assert (row.module, row.name, row.state) == ("sessions", "retention", "ok")
    assert row.detail.startswith("Claude Code: cleanupPeriodDays 36500 days; Codex: no age "
                                 "limit, compression not set (off)")
    assert row.detail.endswith("OpenCode: no setting read")
    assert [r for r in doctor.rows() if r.name == "retention"] == [row]


def test_the_doctor_row_warns_when_codex_compresses(home):
    codex_config(home, "[features]\nlocal_thread_store_compression = true\n")
    row = doctor.retention_row()
    assert row.state == "warn" and "compression on" in row.detail


def test_setup_prints_every_line_and_asks_nothing(home, capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("setup asked"))
    claude_settings(home, {"cleanupPeriodDays": 36500})
    assert setup.setup() == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"sessions: {kept.HEAD}"
    assert out[1:] == [f"sessions: {line}" for item in retention() for line in item.lines]
