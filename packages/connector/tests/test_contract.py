"""The module contract check over this package: imports, dependencies, extras, names."""

from pathlib import Path

from moxi_dev.contract import check

ROOT = Path(__file__).resolve().parents[1]


def test_the_package_keeps_the_module_contract():
    assert [str(finding) for finding in check(ROOT)] == []


def test_interface_one_is_the_provider_module():
    from moxi import modules

    import moxi_connector.provider as provider

    found = modules.load("connector", interface=1)
    assert found.state == modules.PRESENT, found.reason
    assert found.api is provider
    for name in provider.__all__:
        assert hasattr(found.api, name), name
    assert modules.load("connector", interface=2).state != modules.PRESENT
