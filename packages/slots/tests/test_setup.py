"""`SETUP`: tmux's absolute path recorded, or a refusal that writes nothing."""

from __future__ import annotations

import os

import pytest

from moxi import config
from moxi_slots import places, support
from moxi_slots import setup as slots_setup
from moxi_slots.settings import SETUP_BY, current

from slots_kit import record

pytestmark = pytest.mark.touches("packages/slots/src/moxi_slots/setup.py",
                                 "packages/slots/src/moxi_slots/settings*")


@pytest.fixture(autouse=True)
def linux(monkeypatch):
    monkeypatch.setattr(support, "system", lambda *a: support.LINUX)


def config_text() -> str | None:
    path = places.layout().config_file()
    return path.read_text() if path.exists() else None


def test_a_bare_name_is_recorded_as_found_on_path_not_its_link_target(
        fake_tmux, fence, monkeypatch, capsys):
    real = fake_tmux(where="Cellar/tmux/3.4/bin")
    link = fence.short / "brew" / "bin" / "tmux"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    monkeypatch.setenv("PATH", f"{link.parent}:{os.environ['PATH']}")
    assert slots_setup.setup() == 0
    assert current()["tmux"] == str(link)
    assert config.history(places.MODULE, "tmux")[-1]["by"] == SETUP_BY
    assert "tmux 3.4" in capsys.readouterr().out


def test_tmux_absent_refuses_naming_the_install_and_writes_nothing(fence, monkeypatch,
                                                                   capsys):
    monkeypatch.setenv("PATH", str(fence.short / "empty"))
    assert slots_setup.setup() == 3
    out = capsys.readouterr().out
    assert "not on PATH" in out and "install tmux" in out
    assert config_text() is None


def test_tmux_below_the_floor_refuses_with_the_version_found(fake_tmux, monkeypatch,
                                                             capsys):
    old = fake_tmux("tmux 3.1c")
    monkeypatch.setenv("PATH", str(old.parent))
    assert slots_setup.setup() == 3
    out = capsys.readouterr().out
    assert "tmux 3.1c" in out and "3.2 or newer" in out
    assert config_text() is None


def test_an_absolute_path_the_user_set_is_kept(fake_tmux, monkeypatch):
    mine = fake_tmux(where="mine")
    other = fake_tmux(where="other")
    record("tmux", str(mine), by="cli")
    monkeypatch.setenv("PATH", str(other.parent))
    assert slots_setup.setup() == 0
    assert current()["tmux"] == str(mine)


def test_a_user_path_that_is_gone_refuses(fence):
    record("tmux", str(fence.short / "gone" / "tmux"), by="cli")
    assert slots_setup.setup() == 3


def test_a_path_setup_wrote_is_looked_up_again(fake_tmux, fence, monkeypatch):
    old = fake_tmux(where="old")
    record("tmux", str(old), by=SETUP_BY)
    old.unlink()
    moved = fake_tmux(where="moved")
    monkeypatch.setenv("PATH", str(moved.parent))
    assert slots_setup.setup() == 0
    assert current()["tmux"] == str(moved)


def test_native_windows_sets_nothing_up_and_succeeds(monkeypatch, capsys):
    monkeypatch.setattr(support, "system", lambda *a: support.WINDOWS)
    assert slots_setup.setup() == 0
    assert "nothing to set up" in capsys.readouterr().out
    assert config_text() is None
