"""The Claude plugins inside the package: their layout as a
folder marketplace, `claude plugin validate` on each, and their own tests under
`claude plugin test`. The last two need Claude Code on PATH and are skipped without it;
the plugin tests take most of a minute, so they are `slow` and come back into
`moxi-dev check` when a plugin's files change."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import moxi_turns

CLAUDE_DIR = Path(moxi_turns.__file__).parent / "claude"
PLUGINS = ("moxi-turns", "moxi-turns-gate")

needs_claude = pytest.mark.skipif(shutil.which("claude") is None, reason="Claude Code is not on PATH")


@pytest.fixture(autouse=True)
def stub_claude():
    """These tests run Claude Code's own plugin checks on the package's folders, so they
    keep the `claude` PATH finds in place of conftest's stand-in. Each runs in the fence's
    tmp home."""


def test_the_local_marketplace_lists_both_plugins_by_relative_path():
    doc = json.loads((CLAUDE_DIR / ".claude-plugin" / "marketplace.json").read_text())
    assert doc["name"] == "moxi-turns"
    assert [(p["name"], p["source"]) for p in doc["plugins"]] == [
        ("moxi-turns", "./moxi-turns"), ("moxi-turns-gate", "./moxi-turns-gate")]
    for name in PLUGINS:
        manifest = json.loads((CLAUDE_DIR / name / ".claude-plugin" / "plugin.json").read_text())
        assert manifest["name"] == name
        hooks = json.loads((CLAUDE_DIR / name / "hooks" / "hooks.json").read_text())
        assert hooks == {"modules": ["./register.ts"]}


def test_the_runner_reports_the_version_its_manifest_names():
    manifest = json.loads((CLAUDE_DIR / "moxi-turns" / ".claude-plugin" / "plugin.json").read_text())
    source = (CLAUDE_DIR / "moxi-turns" / "hooks" / "register.ts").read_text()
    assert f"const MOD_VERSION = '{manifest['version']}'" in source


#: The runner version that speaks each leg, the latest when a leg had several. Claude Code
#: runs a user-scope plugin from a cache kept per version, so a runner on a new leg, or a
#: runner changed on the same leg, needs a new version, or an install made before it keeps
#: running the old one.
RUNNER_VERSION_OF_LEG = {5: "0.3.3"}


def test_the_runner_speaks_the_endpoints_leg_under_the_version_named_for_it():
    from moxi_turns import claude_setup
    from moxi_turns.claude_leg import LEG

    manifest = json.loads((CLAUDE_DIR / "moxi-turns" / ".claude-plugin" / "plugin.json").read_text())
    source = (CLAUDE_DIR / "moxi-turns" / "hooks" / "register.ts").read_text()
    found = claude_setup._LEG_LINE.search(source)       # as the doctor reads a cached copy
    assert found is not None and int(found.group(1)) == LEG
    assert manifest["version"] == RUNNER_VERSION_OF_LEG[LEG]


def test_endpoint_json_is_never_tracked():
    ignore = (Path(__file__).parents[1] / ".gitignore").read_text().splitlines()
    assert "src/moxi_turns/claude/moxi-turns/endpoint.json" in ignore


def _claude(*args: str, timeout: int = 240) -> subprocess.CompletedProcess:
    return subprocess.run(["claude", "plugin", *args], capture_output=True, text=True,
                          timeout=timeout, stdin=subprocess.DEVNULL)


@needs_claude
@pytest.mark.parametrize("plugin_dir", ["", *PLUGINS])
def test_claude_plugin_validate_passes(plugin_dir):
    out = _claude("validate", str(CLAUDE_DIR / plugin_dir))
    assert out.returncode == 0, out.stdout + out.stderr
    assert "Validation passed" in out.stdout


@needs_claude
@pytest.mark.slow
@pytest.mark.touches("packages/turns/src/moxi_turns/claude/**",
                     "packages/turns/tests/test_claude_plugins.py")
@pytest.mark.parametrize("name", PLUGINS)
def test_claude_plugin_test_passes(name):
    out = _claude("test", str(CLAUDE_DIR / name), timeout=420)
    assert out.returncode == 0, (out.stdout + out.stderr)[-4000:]
    assert " 0 fail" in out.stdout + out.stderr
