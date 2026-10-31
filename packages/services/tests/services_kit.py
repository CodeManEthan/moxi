"""Rows for tests to write: the fields of a written and an adopted row, and one write."""

from __future__ import annotations

import sys

from moxi_services import store


def written(name: str = "web", **fields) -> dict:
    """The fields of a written daemon row, with `fields` laid over them."""
    base = dict(name=name, form="written", kind="daemon", argv=["/usr/bin/python3", "-m",
                                                                  "http.server"],
                by="test", platform=sys.platform)
    base.update(fields)
    return base


def adopted(name: str = "backup", **fields) -> dict:
    """The fields of an adopted timer row, with `fields` laid over them."""
    base = dict(name=name, form="adopted", kind="timer", unit="backup.timer", by="test",
                platform=sys.platform)
    base.update(fields)
    return base


def add(**fields):
    """Insert one row as a write of its own and return it."""
    return store.write(lambda conn: store.insert(conn, **fields))
