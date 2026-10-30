"""The rules for keys, roots, agent names and kind names."""

from __future__ import annotations

import os

import pytest

from moxi_projects import rules
from moxi_projects.errors import Refused


@pytest.mark.parametrize("key", ["moxi", "a", "9lives", "delta.web", "x_y-z", "A.b.C",
                                 "con2", "nul_x", "lpt"])
def test_a_key_in_the_alphabet_passes(key):
    assert rules.check_key(key) == key


@pytest.mark.parametrize("key", ["", ".hidden", "-dash", "_under", "has space", "a/b",
                                 "a\\b", "tab\tkey", "proj.", "über", "a:b", "line\n"])
def test_a_key_outside_the_rule_is_refused(key):
    with pytest.raises(Refused) as caught:
        rules.check_key(key)
    assert caught.value.exit_code == 3
    assert caught.value.fix


@pytest.mark.parametrize("key", ["con", "CON", "nul", "aux", "prn", "com1", "COM9", "lpt0",
                                 "con.x", "Nul.txt", "com3.tar.gz"])
def test_a_windows_device_name_is_refused_with_or_without_an_extension(key):
    with pytest.raises(Refused, match="device name"):
        rules.check_key(key)


def test_a_key_that_is_not_a_string_is_refused():
    with pytest.raises(Refused):
        rules.check_key(None)


def test_absolutize_expands_home_and_joins_the_working_directory(tmp_path):
    assert rules.absolutize("~/x/../y") == os.path.join(os.path.expanduser("~"), "y")
    assert rules.absolutize("a/./b", cwd=str(tmp_path)) == str(tmp_path / "a" / "b")


def test_a_root_is_normalized_and_its_symlinks_kept(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert rules.check_root(f"{link}/./sub/..") == str(link)


@pytest.mark.parametrize("root", ["relative/path", "", None, "/a\0b"])
def test_a_root_that_is_not_an_absolute_path_is_refused(root):
    with pytest.raises(Refused):
        rules.check_root(root)


@pytest.mark.parametrize("name", ["lead-a", "core@scope", "Ünïcode", "x"])
def test_an_agent_name_without_spaces_passes_as_given(name):
    assert rules.check_agent(name) == name


@pytest.mark.parametrize("name", ["", "two words", "tab\there", "nl\n", "bell\x07",
                                  "zero​width", None])
def test_an_agent_name_with_whitespace_or_control_characters_is_refused(name):
    with pytest.raises(Refused):
        rules.check_agent(name)


@pytest.mark.parametrize("name", ["project", "topic", "a.b", "x_1"])
def test_a_kind_name_follows_the_key_alphabet(name):
    assert rules.check_kind_name(name) == name


@pytest.mark.parametrize("name", ["", "-x", "two words", 3])
def test_a_bad_kind_name_is_refused(name):
    with pytest.raises(Refused):
        rules.check_kind_name(name)
