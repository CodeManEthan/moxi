"""The module contract check over this package, and the announcement as the core reads it.

It runs with the package's suite, so `moxi-dev check` and CI hold every change to it.
"""

from pathlib import Path

from moxi_dev.contract import check

from moxi import modules

ROOT = Path(__file__).resolve().parents[1]


def test_the_repo_keeps_the_module_contract():
    assert [str(finding) for finding in check(ROOT)] == []


def test_the_announcement_loads():
    found = modules.load("statusline")
    assert found.state == modules.PRESENT, found.reason
    assert found.segment is None
    assert found.config_check == "moxi_statusline.settings:check"
    assert found.budgets["hook_ms"] == 3.0
    assert set(found.stores) == {"previous", "asked", "backups"}


def test_the_install_listener_is_a_module_with_both_calls():
    (ext,) = [e for e in modules.extensions("moxi.installs") if e.name == "statusline"]
    listener = ext.load()
    assert callable(listener.on_install)
    assert callable(listener.on_uninstall)
