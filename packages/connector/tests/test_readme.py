"""The README and the security doc name the door's port: the default the shipped settings
schema gives, so a changed default can't leave either behind."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_readme_and_the_security_doc_name_the_port():
    schema = tomllib.loads((ROOT / "src" / "moxi_connector" / "settings.toml").read_text())
    port = schema["port"]["default"]
    readme = (ROOT / "README.md").read_text()
    assert f"`port` (the loopback port, {port} by default)" in readme
    security = (ROOT / "docs" / "security.md").read_text()
    other_users = security[security.index("## Other users on the machine"):]
    other_users = other_users[:other_users.index("\n## ", 1)]
    assert f"forwarding to {port}" in other_users
    assert "loginctl enable-linger" in other_users
