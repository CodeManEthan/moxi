"""`toml_string`: any text, a path above all, as a TOML string that reads back the same."""

from __future__ import annotations

import tomllib
from pathlib import PureWindowsPath

import pytest

from moxi_dev.testing import toml_string


@pytest.mark.parametrize("value", [
    "C:\\Users\\runner\\AppData\\Local\\moxi",      # \U and \A would be escapes
    "\\\\server\\share\\notes",                      # a UNC path
    "/home/you/notes", "", "a \"quoted\" name", "it's", "tab\there", "two\nlines",
    "\x00\x1f\x7f", "é ü 日本", "\U0001F600", "a 'single' and \"double\""])
def test_it_reads_back_as_the_value(value):
    assert tomllib.loads(f"key = {toml_string(value)}")["key"] == value


def test_a_path_object_reads_back_as_its_text(tmp_path):
    assert tomllib.loads(f"root = {toml_string(tmp_path)}")["root"] == str(tmp_path)
    windows = PureWindowsPath("C:/Users/you/notes")
    assert tomllib.loads(f"root = {toml_string(windows)}")["root"] == "C:\\Users\\you\\notes"


def test_it_is_one_line_and_a_basic_string():
    text = toml_string("a\nb\\c")
    assert text == '"a\\u000Ab\\\\c"' and "\n" not in text
