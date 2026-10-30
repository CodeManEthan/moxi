"""moxi.doctor's light half: Row, record and clear."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys

import pytest

from moxi import doctor


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Every root under the test's tmp: no real home, no real state."""
    for name in list(os.environ):
        if name.startswith("MOXI_") or name.startswith("XDG_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MOXI_DEMO_STATE_HOME", str(tmp_path / "state"))
    return tmp_path / "state"


def records(state):
    return json.loads((state / "demo" / "doctor.json").read_text())


def test_row_fields_and_default_fix():
    row = doctor.Row("demo", "socket", "ok", "serving")
    assert (row.module, row.name, row.state, row.detail, row.fix) \
        == ("demo", "socket", "ok", "serving", None)
    assert doctor.Row(module="demo", name="n", state="fail", detail="d", fix="run x").fix \
        == "run x"
    assert row == doctor.Row("demo", "socket", "ok", "serving", None)
    assert row._asdict()["detail"] == "serving"


def test_row_keeps_four_states():
    assert doctor.STATES == ("ok", "warn", "fail", "off")
    for state in doctor.STATES:
        doctor.Row("demo", "n", state, "d")
    with pytest.raises(ValueError, match="state"):
        doctor.Row("demo", "n", "info", "d")


def test_record_writes_one_entry_per_key(home):
    doctor.record("demo", "the committer failed", key="commit:abc", fix="repo check")
    doctor.record("demo", "another", key="other")
    got = records(home)
    assert set(got) == {"commit:abc", "other"}
    assert got["commit:abc"]["text"] == "the committer failed"
    assert got["commit:abc"]["fix"] == "repo check"
    assert got["other"]["fix"] is None
    at = got["other"]["at"]
    assert len(at) == 20 and at.endswith("Z") and at[10] == "T"


def test_a_newer_record_replaces_the_older(home):
    doctor.record("demo", "first", key="k")
    doctor.record("demo", "second", key="k", fix="f")
    assert records(home) == {"k": {"text": "second", "fix": "f",
                                   "at": records(home)["k"]["at"]}}


def test_clear_removes_one_record(home):
    doctor.record("demo", "a", key="a")
    doctor.record("demo", "b", key="b")
    doctor.clear("demo", "a")
    assert set(records(home)) == {"b"}
    doctor.clear("demo", "missing")
    assert set(records(home)) == {"b"}


def test_clear_with_no_records_writes_nothing(home):
    doctor.clear("demo", "k")
    assert not (home / "demo").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_the_records_file_is_private(home):
    doctor.record("demo", "a", key="a")
    assert stat.S_IMODE(os.stat(home / "demo" / "doctor.json").st_mode) == 0o600


def test_an_unreadable_records_file_starts_again(home):
    (home / "demo").mkdir(parents=True)
    (home / "demo" / "doctor.json").write_text("not json")
    doctor.record("demo", "a", key="a")
    assert set(records(home)) == {"a"}


def test_bad_names_and_keys_are_refused():
    with pytest.raises(ValueError):
        doctor.record("Not-A-Module", "t", key="k")
    with pytest.raises(ValueError):
        doctor.record("demo", "t", key="")
    with pytest.raises(ValueError):
        doctor.clear("demo", None)


def test_importing_the_package_stays_light():
    code = ("import sys, moxi.doctor; "
            "print(' '.join(m for m in ('json', 'moxi.paths', 'moxi.files', 'logging') "
            "if m in sys.modules))")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                         text=True, check=True).stdout.strip()
    assert out == ""


def test_the_core_s_records_are_in_the_core_s_state_directory(tmp_path, monkeypatch):
    """A module-style variable for the name `moxi` doesn't move the core's records."""
    from moxi import paths

    monkeypatch.setenv("MOXI_HOME", str(tmp_path / "moxi-home"))
    monkeypatch.setenv("MOXI_MOXI_STATE_HOME", str(tmp_path / "elsewhere"))
    doctor.record("moxi", "the core's own row", key="k")
    written = paths.core().state_dir() / "doctor.json"
    assert json.loads(written.read_text())["k"]["text"] == "the core's own row"
    assert not (tmp_path / "elsewhere").exists()
