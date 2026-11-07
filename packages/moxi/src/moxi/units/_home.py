"""Which Moxi home a unit belongs to, and the variables it runs with.

A unit belongs to one home, and its name says which. The default home (no `MOXI_HOME`, or
an empty one) keeps the plain names. A home given by `MOXI_HOME` adds a tag made from that
home to every name it gives, so two homes on one machine never write, start or remove each
other's units. A home treats as its own only the names it gives itself: an untagged unit is
the default home's.

    home_path(env=None)            the home's absolute real path (None: the default home)
    home_tag(env=None)             "h" and the first 8 hex digits of SHA-256 over that path
    environment(module, env=None)  the variables the module's units are written with
    locators_set(module, env=None) the module's own locators set in `env`

The tag is read from the path with `~` expanded, made absolute and every link resolved, so
two spellings of one folder give one tag. It stays the same while the folder stays put.

Every unit a tagged home writes carries `MOXI_HOME`, set to the same path the tag was made
from, so the tag, the unit and the installing shell all name one folder. A module's own
locators (`MOXI_<M>_HOME` and the rest) move that module's stores and nothing else; a
unit's name can't say them, so `plan` refuses a module's units while any of them is set.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping

#: How many hex digits of the digest a tag keeps.
TAG_DIGITS = 8


def home_path(env: Mapping[str, str] | None = None) -> str | None:
    """The home `MOXI_HOME` names, as an absolute real path: `~` expanded, made absolute
    against the working directory, then every link resolved. None when it is unset or
    empty, which is the default home."""
    from .. import paths

    environ = os.environ if env is None else env
    value = environ.get(paths.HOME)
    if not value:
        return None
    return os.path.realpath(os.path.abspath(paths._expanduser(value, environ)))


def home_tag(env: Mapping[str, str] | None = None) -> str | None:
    """The home's tag, `h` and the first 8 hex digits of SHA-256 over `home_path`; None for
    the default home."""
    path = home_path(env)
    if path is None:
        return None
    digest = hashlib.sha256(path.encode("utf-8", "surrogateescape")).hexdigest()
    return "h" + digest[:TAG_DIGITS]


def environment(module: str, env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The variables `module`'s units are written with: `{"MOXI_HOME": home_path}` for a
    tagged home, nothing for the default home. Every module's units carry the same."""
    from .. import names

    names.check_module_name(module)
    path = home_path(env)
    return {} if path is None else {"MOXI_HOME": path}


def locators_set(module: str, env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """The locators of `module` set to a non-empty value in `env` (default this process's
    environment): the prefix variables `moxi.paths.Layout(module).locators()` names, never
    `MOXI_HOME`. Other variables in the module's namespace are not locators."""
    from .. import paths

    environ = os.environ if env is None else env
    return tuple(name for name in paths.Layout(module).locators()
                 if name != paths.HOME and environ.get(name))
