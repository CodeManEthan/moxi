"""Fixtures: rounds' config file in the test's own Moxi home.

The test kit points `MOXI_ROUNDS_HOME` into each test's tmp, so the config file every
call reads and writes is the test's own.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from moxi_rounds import paths


@pytest.fixture
def config_file() -> Path:
    """The test's own config file (not yet created)."""
    return paths.LAYOUT.config_file()


@pytest.fixture
def write_config(config_file):
    """`write_config(text)`: write `text`, dedented, as rounds' config file; returns the
    file's path."""

    def write(text: str) -> Path:
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(textwrap.dedent(text), encoding="utf-8")
        return config_file

    return write
