"""Fixtures: a host over fake providers, an app built from an origin, the door's config."""

from __future__ import annotations

import textwrap

import pytest
from kit import HOST, Admit, Fake, echo_tool, provider
from signin_kit import CALLBACKS, Clock, Door

from moxi_connector import grants
from moxi_connector.app import Front, Host, build_app
from moxi_connector.oauth import OAuth
from moxi_connector.calllog import MemoryLog
from moxi_connector.origin import Origin
from moxi_connector.providers import Registry

ORIGIN = Origin(HOST)


@pytest.fixture
def fake() -> Fake:
    return Fake()


@pytest.fixture
def make_host(fake):
    """`await make_host(**providers)`: a started host over `providers` (default: one fake
    provider `demo` with the tool `echo`), with the stand-in sign-in and a memory log."""

    async def make(providers=None, signin=None):
        providers = providers if providers is not None else {
            "demo": provider(fake, echo_tool(fake))}
        registry = Registry.of(providers)
        await registry.start({"HOME": "/nowhere"})
        return Host(registry, signin=signin or Admit(), calllog=MemoryLog(),
                    redirect_uris=("https://claude.ai/api/mcp/auth_callback",))

    return make


@pytest.fixture
def front(make_host):
    """`await front(origin=ORIGIN, **host)`: a `Front` serving the app built from
    `origin`, already started. A test closes it on its own loop (`await f.close()`)."""

    async def make(origin=ORIGIN, **kw):
        host = await make_host(**kw)
        app = build_app(origin, host)
        await app.start()
        return Front(app, host)

    return make


@pytest.fixture
def config_file():
    """`config_file(text)`: write the connector's config file."""
    from moxi.paths import Layout

    def write(text: str):
        path = Layout("connector").config_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
        return path

    return write


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def door(make_host, clock):
    """`await door(origin=ORIGIN)`: the door with the real sign-in over the grants file in
    the test's own state directory, on a clock the test moves."""

    async def make(origin=ORIGIN, **kw):
        host = await make_host()
        oauth = OAuth(grants.Store(), host.calllog, redirect_uris=CALLBACKS, clock=clock,
                      **kw)
        host.signin = oauth
        host.redirect_uris = CALLBACKS
        app = build_app(origin, host)
        await app.start()
        return Door(Front(app, host), oauth, clock)

    return make
