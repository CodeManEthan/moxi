"""Fixtures shared by the secrets tests: both engines, and the private names config."""

from __future__ import annotations

import sys

import pytest

from moxi_secrets import engine, fallback


def use_engine(name: str, monkeypatch) -> None:
    """Run the scan on RE2 ("re2") or with re2 hidden from import ("fallback")."""
    if name == "fallback":
        monkeypatch.setitem(sys.modules, "re2", None)
    engine.reset()


@pytest.fixture(params=["re2", "fallback"])
def engine_name(request, monkeypatch):
    use_engine(request.param, monkeypatch)
    yield request.param
    engine.reset()


@pytest.fixture
def fallback_engine(monkeypatch):
    use_engine("fallback", monkeypatch)
    yield
    engine.reset()


@pytest.fixture(scope="session", autouse=True)
def _stop_worker():
    yield
    fallback.shutdown()


def toml_list(values) -> str:
    return "[" + ", ".join('"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
                           for v in values) + "]"


@pytest.fixture
def names_config():
    """Write secrets' config file: names_config(words=[...], anywhere=[...], ...)."""
    from moxi.paths import Layout

    path = Layout("secrets").config_file()

    def write(words=(), anywhere=(), emails=(), allowed=(), author_name="",
              author_email="", raw=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        if raw is None:
            raw = (f"[private_names]\nwords = {toml_list(words)}\n"
                   f"anywhere = {toml_list(anywhere)}\nemails = {toml_list(emails)}\n"
                   f"allowed = {toml_list(allowed)}\n\n[author]\n"
                   f"name = {toml_list([author_name])[1:-1]}\n"
                   f"email = {toml_list([author_email])[1:-1]}\n")
        path.write_text(raw, encoding="utf-8")
        return path

    return write
