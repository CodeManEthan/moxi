"""The ui module's own tests run `ui_shell` with the `fixture` module installed; the page
server base's tests serve a page package of their own."""

import pytest

import asgi_kit
import fixture_kit


@pytest.fixture(scope="module")
def fixture_site(tmp_path_factory):
    """A site directory, one per test module, holding the fixture distribution."""
    return fixture_kit.install(tmp_path_factory.mktemp("fixture-site"))


@pytest.fixture(scope="module")
def ui_shell_options(fixture_site):
    return {"site": [fixture_site]}


@pytest.fixture(scope="session")
def page_package(tmp_path_factory):
    """The page package the base's server tests serve, installed once per session."""
    asgi_kit.install_pages(tmp_path_factory.mktemp("pages"))
    return asgi_kit.handlers()


@pytest.fixture
def pages(page_package):
    """The handlers module, with its state cleared for the test."""
    page_package.STATE.clear()
    page_package.CALLS.clear()
    page_package.TOPICS._values.clear()
    yield page_package
    page_package.STATE.clear()
