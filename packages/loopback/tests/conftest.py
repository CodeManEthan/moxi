"""The page package the server tests serve, installed once per session."""

import pytest

import asgi_kit


@pytest.fixture(scope="session")
def page_package(tmp_path_factory):
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
