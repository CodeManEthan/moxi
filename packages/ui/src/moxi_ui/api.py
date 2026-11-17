"""ui's interfaces 2 and 3, the module other Moxi modules reach (`moxi_ui.api`).

Interface 2 is interface 1 (the component contract) plus this module: the check a page
part makes before it writes. Interface 3 is 2 plus `loopback`, ui's page-server base. A
module whose page is optional reaches it at the point of use, through the `ui` extra it
already declares:

    found = moxi.modules.load("ui", interface=2)
    found.api.check(places, *, programs=None, for_page=True) -> str | None

* `check(places, *, programs=None, for_page=True)`: None when the folders `places(env)`
  resolves from this process's environment agree with those it resolves from the core's
  record of the user's last run in a terminal, else the one line to refuse with
  (`moxi_ui.terminal`).
* `where(platform=None)`: a `Where` (`place`, `step`), where to set a variable for the
  browser shell and the step that makes its next process take it, for a module's own lines.
* `Where`: what `where` gives back.
* `loopback` (interface 3): the module `moxi_ui.loopback.api`, the base every page
  server is built on, which a page module reaches as
  `moxi.modules.load("ui", interface=3).api.loopback`. The name imports the base on its
  first use, so a caller that never reads it pays nothing for it.

A page act that writes calls `check` before it writes anything and refuses with the line;
a page read may show the line as a note. A page part that can't load ui at interface 2
refuses its write and shows why with the step that fixes it: install moxi-ui when the load
finds none, update moxi-ui when it finds an older one. It never writes unchecked.

Loading this module imports only the standard library, so every `moxi.modules.load("ui")`
caller, the shell's own loaders among them, pays nothing for it.
"""

from __future__ import annotations

from .terminal import Where, check, where

__all__ = ["check", "where", "Where", "loopback"]


def __getattr__(name):
    """`loopback`, imported on its first read and kept as a plain global after it."""
    if name == "loopback":
        from .loopback import api as loopback
        globals()["loopback"] = loopback
        return loopback
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
