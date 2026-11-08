"""The README names the shell's defaults: the ports and the idle time the shipped settings
schema gives, so a changed default can't leave the README behind."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_readme_names_the_ports_and_the_idle_time():
    schema = tomllib.loads((ROOT / "src" / "moxi_ui" / "settings.toml").read_text())
    readme = (ROOT / "README.md").read_text()
    defaults = {
        "port": schema["port"]["default"],
        "idle_s": schema["idle_s"]["default"],
        "gateway.port": schema["gateway"]["port"]["default"],
    }
    for key, value in defaults.items():
        assert f"`{key}` ({value})" in readme, key
    assert f"on one port ({defaults['port']} by default)" in readme
    assert f"`idle_s`, {defaults['idle_s']} seconds" in readme
