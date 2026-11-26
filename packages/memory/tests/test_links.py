"""memory's link to the launcher's interface 3, which forks a session and exits one."""

from __future__ import annotations

from types import SimpleNamespace

from moxi import modules
from moxi_memory import links


def test_the_launcher_forks_and_exits_sessions_at_interface_3():
    link = links.launcher_forks()
    assert link.ready
    assert callable(link.api.fork) and callable(link.api.exit_session)


def test_a_launcher_without_interface_3_gives_no_link(monkeypatch):
    real = modules.load
    old = SimpleNamespace(state=modules.PRESENT, interfaces=frozenset({1}), version="0.1.0",
                          reason="")
    monkeypatch.setattr(modules, "available", lambda name: True)
    monkeypatch.setattr(modules, "load", lambda name, **kw: (
        old if name == "launcher" else real(name, **kw)))
    link = links.launcher_forks()
    assert link.state == links.NONE
    assert link.reason == "launcher 0.1.0 doesn't offer interface 3"
