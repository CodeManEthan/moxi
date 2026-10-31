"""The ui module's own tests run `ui_shell` with the `fixture` module installed."""

import pytest

import fixture_kit


@pytest.fixture(scope="module")
def fixture_site(tmp_path_factory):
    """A site directory, one per test module, holding the fixture distribution."""
    return fixture_kit.install(tmp_path_factory.mktemp("fixture-site"))


@pytest.fixture(scope="module")
def ui_shell_options(fixture_site):
    return {"site": [fixture_site]}
