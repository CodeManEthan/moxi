"""The shell document: the one HTML document a navigation with a valid cookie gets.

It is the web build's `shell.html`, from the shell page's assets, with `data-theme` and
`data-theme-name` on its root set from the `ui` settings `theme.mode` and `theme.name`
each time it is served. The document loads
`theme.css`, `shell.css` and `client.js` from `/m/ui/`; `client.js` draws the menu and
places the panel for the URL. It is static: no module's server starts for it, and it
holds nothing a request named.
"""

from __future__ import annotations

import functools
import importlib.resources

from moxi_ui.loopback.api import response_headers

from . import config
from .session import Static

__all__ = ["TEMPLATE", "answer", "body", "root", "template"]

#: The document's asset name in the shell page's assets.
TEMPLATE = "shell.html"
#: The root element as the template writes it; it is replaced as the document is served.
_ROOT = b'<html lang="en" data-theme="system">'

_MISSING = (
    b'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>Moxi</title>'
    b"</head><body><p>The shell's page is not built into this install.</p></body></html>\n"
)


@functools.cache
def template() -> bytes | None:
    """`shell.html` as the build wrote it, or None when this install has no built page
    or the file is not the template (its root element is not there exactly once)."""
    try:
        data = importlib.resources.files("moxi_ui").joinpath("web", TEMPLATE).read_bytes()
    except (OSError, ImportError):
        return None
    return data if data.count(_ROOT) == 1 else None


def root(mode: str, name: str) -> bytes:
    """The root element's opening tag for the theme mode `mode` (`system`, `light` or
    `dark`) and the theme `name`. Raises `ValueError` for a mode or a theme there is not."""
    if mode not in config.THEME_MODES:
        raise ValueError(f"{mode!r} is not a theme mode")
    if name not in config.THEME_NAMES:
        raise ValueError(f"{name!r} is not a theme")
    return b'<html lang="en" data-theme="%s" data-theme-name="%s">' % (mode.encode("ascii"),
                                                                     name.encode("ascii"))


def body(mode: str, name: str) -> bytes:
    """The document with `data-theme` set to `mode` and `data-theme-name` to `name`."""
    tag = root(mode, name)
    data = template()
    if data is None:
        return _MISSING
    return data.replace(_ROOT, tag)


def answer(mode: str | None = None, name: str | None = None) -> Static:
    """The document's answer, its mode and theme read from the settings unless given."""
    data = body(config.theme_mode() if mode is None else mode,
                config.theme_name() if name is None else name)
    return 200, response_headers(extra=[("Content-Type", "text/html; charset=utf-8"),
                                        ("Content-Length", str(len(data)))]), data
