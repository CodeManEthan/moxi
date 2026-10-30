"""Where slots runs: the system, `supported()`, and the command on a system it doesn't."""

from __future__ import annotations

import pytest

from moxi_slots import api, cli, support

from slots_kit import record

pytestmark = pytest.mark.touches("packages/slots/src/moxi_slots/support.py",
                                 "packages/slots/src/moxi_slots/cli.py",
                                 "packages/slots/src/moxi_slots/api.py")


@pytest.mark.parametrize("name, release, kind", [
    ("linux", "6.8.0-45-generic", support.LINUX),
    ("linux", "5.15.153.1-microsoft-standard-WSL2", support.WSL2),
    ("linux", "4.4.0-19041-Microsoft", support.WSL2),
    ("darwin", "", support.MACOS),
    ("win32", "", support.WINDOWS),
    ("freebsd14", "", "freebsd14"),
])
def test_the_system_is_named(name, release, kind):
    assert support.system(name, release) == kind


def test_native_windows_is_unsupported_and_says_where_slots_runs(monkeypatch):
    monkeypatch.setattr(support, "system", lambda *a: support.WINDOWS)
    got = api.supported()
    assert not got.ok
    assert "Linux, macOS and WSL2" in got.why and "in-terminal and headless" in got.why


def test_no_recorded_tmux_is_unsupported_naming_the_install(monkeypatch):
    monkeypatch.setattr(support, "system", lambda *a: support.LINUX)
    got = api.supported()
    assert not got.ok and "moxi install slots" in got.why


def test_a_recorded_tmux_is_supported(monkeypatch, fake_tmux):
    monkeypatch.setattr(support, "system", lambda *a: support.MACOS)
    record("tmux", str(fake_tmux()))
    assert api.supported() == api.Support(True, "")


def test_every_verb_exits_3_on_windows(monkeypatch, capsys):
    monkeypatch.setattr(support, "system", lambda *a: support.WINDOWS)
    assert cli.main(["list"]) == 3
    assert "slots runs on Linux, macOS and WSL2" in capsys.readouterr().err


def test_no_verb_is_a_usage_error(monkeypatch):
    monkeypatch.setattr(support, "system", lambda *a: support.LINUX)
    assert cli.main([]) == 2


def test_a_refusal_carries_its_word_and_holder():
    error = api.Refused("held", "slot 3 is held by run-7 until 12:00", {"holder": "run-7"})
    assert (error.reason, error.words, error.holder) == (
        "held", "slot 3 is held by run-7 until 12:00", {"holder": "run-7"})
    with pytest.raises(ValueError):
        api.Refused("busy", "not a reason")
