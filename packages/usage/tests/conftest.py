"""Shared fixtures for usage's suite, and usage's own guard on its real roots.

Every test already runs inside the Moxi test kit's fence (`moxi_dev.testing`), which
points every home into the test's tmp and refuses a write outside it. usage adds a guard
of its own on the roots that matter most to it, kept from the suite it was ported from,
where a test once undid its own redirect and wrote into the real state tree:

* usage's real data, state, cache and config directories are worked out at import, from
  the environment pytest started in, before any fixture moves them;
* an audit hook records every write this process makes under one of them (open for
  writing, rename, replace, mkdir, remove, chmod, truncate, an SQLite connect), and the
  test that made it fails, whatever the fence let through;
* every test must end with usage's roots still inside its own tmp, so a test that undoes
  the redirect fails even when it wrote nothing;
* at the end of the run, a real usage directory whose files changed fails the run, which
  catches a subprocess a test started with the redirect gone.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from moxi.paths import Layout

MODULE = "usage"


def _real_roots() -> list[Path]:
    layout = Layout(MODULE)
    roots = {layout.data_dir(), layout.state_root() / MODULE, layout.cache_root() / MODULE,
             layout.config_dir()}
    return sorted({Path(os.path.realpath(root)) for root in roots})


GUARDED: list[Path] = _real_roots()
VIOLATIONS: list[str] = []
_PATH_EVENTS = {"os.rename", "os.replace", "os.mkdir", "os.remove", "os.rmdir",
                "os.symlink", "os.link", "os.chmod", "os.truncate", "shutil.copyfile",
                "shutil.rmtree", "shutil.move", "sqlite3.connect"}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def guarded(path) -> bool:
    """True when `path` is one of usage's real roots or anything under one."""
    if path is None or isinstance(path, int):
        return False
    try:
        text = os.fsdecode(path)
    except TypeError:
        return False
    full = Path(os.path.realpath(os.path.abspath(text)))
    return any(full == root or root in full.parents for root in GUARDED)


def _audit(event, args):
    if event == "open":
        path, mode, flags = (tuple(args) + (None, None, None))[:3]
        writing = ((isinstance(mode, str) and any(c in mode for c in "wax+"))
                   or (isinstance(flags, int) and flags & _WRITE_FLAGS))
        if writing and guarded(path):
            VIOLATIONS.append(f"open {path!r}")
    elif event in _PATH_EVENTS:
        for path in tuple(args)[:2]:
            if isinstance(path, (str, bytes, os.PathLike)) and guarded(path):
                VIOLATIONS.append(f"{event} {path!r}")


sys.addaudithook(_audit)


def _listing() -> dict[str, int]:
    found: dict[str, int] = {}
    for root in GUARDED:
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                path = os.path.join(dirpath, name)
                try:
                    found[path] = os.stat(path).st_mtime_ns
                except OSError:
                    pass
    return found


_AT_START = _listing()


def pytest_sessionfinish(session, exitstatus):
    now = _listing()
    changed = sorted(path for path in set(now) | set(_AT_START)
                     if now.get(path) != _AT_START.get(path))
    if changed:
        sys.stderr.write("\nREAL USAGE FILES TOUCHED BY THE SUITE:\n  "
                         + "\n  ".join(changed) + "\n")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture(autouse=True)
def usage_guard(fence, tmp_path):
    """usage's roots inside the test's tmp, before the body runs and still after."""
    VIOLATIONS.clear()
    yield
    problems = list(VIOLATIONS)
    VIOLATIONS.clear()
    layout = Layout(MODULE)
    allowed = [Path(os.path.realpath(root)) for root in fence.roots]
    for name, root in (("data", layout.data_dir()), ("config", layout.config_dir()),
                       ("state", layout.state_root())):
        real = Path(os.path.realpath(root))
        if not any(real == base or base in real.parents for base in allowed):
            problems.append(f"usage's {name} root left the test's tmp ({real}); was the "
                            "environment put back by hand?")
    if problems:
        pytest.fail("usage's real-root guard: " + "; ".join(problems))


# --- helpers --------------------------------------------------------------------------

def write_config(text: str) -> Path:
    """Write usage's config file, in the test's tmp, and return its path."""
    path = Layout(MODULE).config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def doc(models: dict | None = None, *, date: str = "2026-10-01", prefixes: dict | None = None,
        defaults: dict | None = None, variants: dict | None = None) -> dict:
    """A small model table document in the converter's shape."""
    return {"schema": 1, "snapshot": {"source": "litellm", "date": date, "sha256": "0" * 64},
            "supplement": {"as_of": date}, "defaults": defaults or {},
            "variants": variants or {}, "prefixes": prefixes or {},
            "models": {key: {**row, "provenance": {"source": "litellm", "key": key}}
                       for key, row in (models or {}).items()}}
