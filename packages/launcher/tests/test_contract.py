"""The module contract check over this package: imports, dependencies, extras, names."""

import re
import tomllib
from pathlib import Path

from moxi_dev.contract import check
from moxi_launcher import compose

ROOT = Path(__file__).resolve().parents[1]


def test_the_package_keeps_the_module_contract():
    assert [str(finding) for finding in check(ROOT)] == []


def test_every_link_a_launch_may_use_is_an_extra_that_installs_it():
    # The contract check reads only a module named by a literal or a constant; LINKS is
    # looked up by a loop, so this holds each of its names to an extra of that name.
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = project["project"]["optional-dependencies"]
    for name in compose.LINKS:
        assert name in extras, f"LINKS names {name!r}, which is no extra"
        dists = {re.match(r"[A-Za-z0-9._-]+", req).group(0) for req in extras[name]}
        assert f"moxi-{name}" in dists, f"the extra {name!r} doesn't install moxi-{name}"
