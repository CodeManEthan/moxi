"""The module contract check over this package.

It runs with the package's suite, so `moxi-dev check` and CI hold every change to it.
"""

from pathlib import Path

from moxi_dev.contract import check

ROOT = Path(__file__).resolve().parents[1]


def test_the_repo_keeps_the_module_contract():
    assert [str(finding) for finding in check(ROOT)] == []


#: The verbs comms and cache had for writing their own units. The core writes every
#: module's units (`moxi install`), so neither verb is named anywhere but a CHANGELOG.
OLD_VERBS = ("comms service", "cache service")


def test_no_surface_names_a_unit_verb():
    """cache's `src/` (its built page included), `web/src/` and README; a `docs/` too,
    should cache grow one."""
    files = [ROOT / "README.md"]
    for part in ("src", "docs", "web/src"):
        if (ROOT / part).is_dir():
            files += sorted(p for p in (ROOT / part).rglob("*") if p.is_file())
    found = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        found += [f"{path.relative_to(ROOT)}:{n}: {line.strip()}"
                  for n, line in enumerate(text.splitlines(), 1)
                  if any(verb in line for verb in OLD_VERBS)]
    assert found == []
