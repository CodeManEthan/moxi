"""Helpers the tests of notes' connector provider share: a notes root under tmp, made-up
secrets built at run time, and a call context that records what a tool logs."""

from __future__ import annotations

import asyncio
import os
import random
import string
from pathlib import Path

import pytest
from moxi import modules
from notes_kit import configure

HOST = modules.load("connector", interface=1)
API = HOST.api if HOST.ok else None


def make_root(tmp_path: Path) -> Path:
    """A notes root under tmp, set as notes' `root`, every link in its path resolved."""
    root = (tmp_path / "notes").resolve()
    root.mkdir(parents=True, exist_ok=True)
    configure(root=str(root))
    return root


#: Windows has no FIFOs, so a test of one runs elsewhere.
FIFOS = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="Windows has no FIFOs")


def make_fifo(path: Path) -> bool:
    """A FIFO at `path` where the system has them, and whether one was made."""
    if not hasattr(os, "mkfifo"):
        return False
    os.mkfifo(path)
    return True


def token(seed: int = 1) -> str:
    """A made-up GitHub token, of the shape the chat redaction finds."""
    pick = random.Random(seed)
    return "ghp_" + "".join(pick.choice(string.ascii_letters + string.digits)
                            for _ in range(36))


def stripe(seed: int = 2) -> str:
    """A made-up Stripe secret key."""
    pick = random.Random(seed)
    return "sk_live_" + "".join(pick.choice(string.ascii_letters + string.digits)
                                for _ in range(24))


def write(root: Path, rel: str, text: str | bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8", newline="")
    return path


class Ctx:
    """What the connector gives one call, with `log` checked as the host checks it."""

    def __init__(self):
        self.fields: dict = {}
        self.grant_id, self.grant_label, self.request_id = "g1", "phone", "r1"

    def after_answer(self, fn):
        pass

    def log(self, **fields):
        bad = API.refused_field(fields)
        assert bad is None, bad
        assert not API.HOST_FIELDS.intersection(fields), fields
        self.fields.update(fields)


def call(tool, args: dict | None = None):
    """`(result, logged fields)` of one call of `tool` (a `Tool`), with `args`."""
    ctx = Ctx()
    result = asyncio.run(tool.call(dict(args or {}), ctx))
    return result, ctx.fields
