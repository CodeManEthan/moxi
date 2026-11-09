"""The module contract check over this package: imports, dependencies, extras, names."""

from pathlib import Path

from moxi_dev.contract import check

ROOT = Path(__file__).resolve().parents[1]


def test_the_package_keeps_the_module_contract():
    assert [str(finding) for finding in check(ROOT)] == []


def test_the_repository_readme_lists_this_package():
    readme = ROOT.parents[1] / "README.md"
    assert "| `packages/memory` | `moxi-memory` |" in readme.read_text()


def test_the_turns_pin_starts_at_the_first_turns_whose_start_order_names_memory():
    import re
    import tomllib

    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    turns = [d for d in deps if re.match(r"moxi-turns\b", d)]
    assert len(turns) == 1
    low = re.search(r">=\s*([\d.]+)", turns[0]).group(1)
    assert tuple(int(p) for p in low.split(".")) >= (0, 2, 4)
