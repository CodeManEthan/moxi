"""The package holds no fleet name: moxi-dev's tag, reference and naming checks pass on it,
and no private-name entry of the tests' secrets config appears in its source."""

from __future__ import annotations

from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
WORKSPACE = PACKAGE.parents[1]
PREFIX = PACKAGE.relative_to(WORKSPACE).as_posix() + "/"
#: The private names the tests' secrets config holds (the `names` fixture).
TEST_NAMES = ("bobsecret", "bob@private.example.test")


#: The reference and naming checks read every file of the workspace (about 8 s each), as
#: `moxi-dev check` does on every run; here they run in the slow tier, put back when a file
#: of this package changes.
_WHOLE_TREE = (pytest.mark.slow, pytest.mark.touches("packages/publishing/**"))


@pytest.mark.parametrize("check", ["tags", pytest.param("refs", marks=_WHOLE_TREE),
                                   pytest.param("naming", marks=_WHOLE_TREE)])
def test_moxi_devs_checks_pass_on_the_package(check):
    import importlib

    scanner = importlib.import_module(f"moxi_dev.{check}")
    found = [str(hit) for hit in scanner.scan(WORKSPACE) if hit.path.startswith(PREFIX)]
    assert found == []


def test_no_private_name_of_the_test_config_is_in_the_source():
    sources = [p for p in (PACKAGE / "src").rglob("*")
               if p.is_file() and "__pycache__" not in p.parts]
    assert len(sources) > 5
    for path in sources:
        text = path.read_text(encoding="utf-8").casefold()
        for name in TEST_NAMES:
            assert name not in text, (path.name, name)
