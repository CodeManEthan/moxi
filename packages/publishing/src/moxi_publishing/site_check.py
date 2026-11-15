"""A pytest plugin the release test loads into a fresh venv: it fails the run, before any
test, when a package of the wheel under test imports from anywhere but the venv's
site-packages, such as the checkout the tests sit in.

It runs where only the wheel, its dependencies, the test tools and the test fence are
installed, so it imports nothing beyond the standard library. It never imports pytest:
publishing doesn't depend on it, and the session pytest hands the hook carries what it
needs. `moxi publishing ci siblings` copies this file beside the run and loads it with
`-p`; the names to check are in the environment variable below, separated by commas.
"""

from __future__ import annotations

import os
import sys

#: The import names of the wheel under test, separated by commas.
NAMES_ENV = "MOXI_PUBLISHING_SITE_CHECK_NAMES"


def site_roots() -> set[str]:
    """This interpreter's site-packages directories, resolved."""
    import sysconfig

    paths = sysconfig.get_paths()
    return {os.path.realpath(paths[key]) for key in ("purelib", "platlib")}


def outside(names, roots) -> list[str]:
    """One line for each of `names` that imports from outside every one of `roots`."""
    import importlib

    found = []
    for name in names:
        try:
            module = importlib.import_module(name)
        except ImportError as exc:
            found.append(f"{name} can't be imported: {exc}")
            continue
        where = getattr(module, "__file__", None) or next(iter(getattr(module, "__path__", [])), "")
        where = os.path.realpath(where)
        if not any(where.startswith(root + os.sep) for root in roots):
            found.append(f"{name} imports from {where}, not the installed wheel")
    return found


def pytest_collection_finish(session) -> None:
    # After collection, so a path a conftest or a test module put on sys.path counts too.
    names = [n for n in os.environ.get(NAMES_ENV, "").split(",") if n]
    problems = outside(names, site_roots())
    if not problems:
        return
    text = "site check: " + "; ".join(problems)
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(text, red=True)
    else:
        sys.stderr.write(text + "\n")
    # pytest ends a session that raises its own Failed as a failed run, exit status 1,
    # before any test runs.
    raise session.Failed(text)
