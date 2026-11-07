"""The export command: `python -P -m moxi_ui.loopback.export <module>...`.

It prints each named module's route table export as one JSON object on stdout, keyed by
module: `{"ok": true, "export": {...}}`, or `{"ok": false, "error": "<one line>"}` for a
module with no `PAGE`, a broken target, a refused declaration or a refused asset table.
It exits 0 when every module was read, 3 when any was refused, and 2 with no module
named. A caller gets the exports this way without importing any module's code into
itself.

Each `PAGE` is loaded as the runner loads it (`load_page`), and the table is built by the
same `build_table` the runner serves from, so a module's export and its server's table
agree for one installed version. No handler or health target is resolved, so no handler
module is imported.

It must be started with `python -P`, which keeps the working directory off `sys.path`.
Started without it, it refuses before it imports anything outside the standard library,
so a planted `moxi/` or `uvicorn/` in a shared directory is never loaded.
"""

from __future__ import annotations

import sys

if __name__ == "__main__" and not sys.flags.safe_path:
    sys.stderr.write("moxi_ui.loopback.export must be started with python -P, which keeps "
                     "the working directory off the import path\n")
    sys.exit(3)

import importlib  # noqa: E402 - imported after the check above, on purpose
import json  # noqa: E402
import os  # noqa: E402
from typing import TYPE_CHECKING  # noqa: E402

if TYPE_CHECKING:
    from .routes import Page

# `moxi_ui.loopback.api` re-exports nothing from the command, so it has no public names.
__all__: list[str] = []

_USAGE = "usage: python -P -m moxi_ui.loopback.export <module>...\n"


class PageLoadError(Exception):
    """A module's `PAGE` could not be loaded; the message is one line saying why."""


def load_page(module: str) -> Page:
    """Module `module`'s `Page`, through `moxi.modules.load` and its `PAGE` target.

    Raises `PageLoadError` when the module is not present, declares no `PAGE`, or its
    target does not import or does not name a `Page`. A `PageError` the declaration
    raises while it is imported is raised as it is.
    """
    import moxi.modules

    from .routes import Page, PageError

    found = moxi.modules.load(module)
    if not found.ok:
        raise PageLoadError(f"{module} is {found.state}: {found.reason}")
    if found.page is None:
        raise PageLoadError(f"{module} declares no PAGE")
    name, _, attr = found.page.partition(":")
    try:
        page_module = importlib.import_module(name)
    except PageError:
        raise
    except Exception as err:  # noqa: BLE001 - any failure of the module's import
        raise PageLoadError(f"importing {name} failed: {type(err).__name__}: {err}") from None
    try:
        page = getattr(page_module, attr)
    except AttributeError:
        raise PageLoadError(f"{name} has no {attr}") from None
    if not isinstance(page, Page):
        raise PageLoadError(f"{found.page} is a {type(page).__name__}, not a Page")
    return page


def _one(module: str) -> dict:
    from .routes import export

    try:
        return {"ok": True, "export": export(load_page(module))}
    except Exception as err:  # noqa: BLE001 - every refusal is reported, never raised
        reason = str(err) or type(err).__name__
        return {"ok": False, "error": " ".join(reason.split())}


def main(argv: list[str] | None = None) -> int:
    """Run the command over `argv` (the process's arguments without one); the exit code."""
    from moxi import exit as codes

    modules = sys.argv[1:] if argv is None else list(argv)
    if not modules or any(arg.startswith("-") for arg in modules):
        sys.stderr.write(_USAGE)
        return codes.USAGE
    # A page module that prints while it is imported must not spoil the one JSON line, so
    # stdout points at stderr until that line is written.
    sys.stdout.flush()
    saved = os.dup(1)
    try:
        os.dup2(2, 1)
        real_stdout, sys.stdout = sys.stdout, sys.stderr
        try:
            result = {module: _one(module) for module in modules}
        finally:
            sys.stdout = real_stdout
            sys.stderr.flush()
    finally:
        os.dup2(saved, 1)
        os.close(saved)
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return codes.OK if all(entry["ok"] for entry in result.values()) else codes.REFUSED


if __name__ == "__main__":
    sys.exit(main())
