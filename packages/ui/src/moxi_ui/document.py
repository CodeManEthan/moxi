"""The shell document: the one HTML document a navigation with a valid cookie gets.

It is the web build's `shell.html`, from the shell page's assets, with `data-theme` on its
root set from the `ui` setting `theme.mode` each time it is served. The document loads
`theme.css`, `shell.css` and `client.js` from `/m/ui/`; `client.js` draws the menu and
places the panel for the URL. It is static: no module's server starts for it, and it
holds nothing a request named.
"""

from __future__ import annotations

import functools
import importlib.resources

from moxi_loopback.api import response_headers

from . import config
from .session import Static

__all__ = ["TEMPLATE", "answer", "body", "template"]

#: The document's asset name in the shell page's assets.
TEMPLATE = "shell.html"
#: The root element as the template writes it; its mode is replaced as it is served.
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


def body(mode: str) -> bytes:
    """The document with `data-theme` set to `mode` (`system`, `light` or `dark`)."""
    if mode not in config.THEME_MODES:
        raise ValueError(f"{mode!r} is not a theme mode")
    data = template()
    if data is None:
        return _MISSING
    return data.replace(_ROOT, b'<html lang="en" data-theme="%s">' % mode.encode("ascii"))


def answer(mode: str | None = None) -> Static:
    """The document's answer, its mode read from the setting unless given."""
    data = body(config.theme_mode() if mode is None else mode)
    return 200, response_headers(extra=[("Content-Type", "text/html; charset=utf-8"),
                                        ("Content-Length", str(len(data)))]), data
