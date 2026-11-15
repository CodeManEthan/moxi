"""A launch's session name: the template it is rendered from, and what a launch is called.

A name template is text with pieces in angle brackets, in the order they are written; the
text between them stays as written:

    <project>     the project key, else the name of the folder the session starts in
    <agent>       the seat's name when the launch names a seat, else its agent role
    <model>       the model the launch names; empty on the harness's default
    <identity>    the comms name; empty without one
    <scope>       the comms scope, only beside an identity
    <instance>    the lowest positive number that makes the name differ from every live
                  launch's
    <date[:fmt]>  the local date at the launch, as 20261108 unless a format is given
    <time[:fmt]>  the local time, as 1245 unless a format is given

A format is read by its tokens, in either case: `yyyy`, `yy`, `mmm` (Nov), `mm` (the
month, or the minutes once an `hh` has come before it), `dd`, `hh` (24 hours), `ss`.
Anything else in it stays as written, and a format holding `%` is taken as `strftime`'s.

**The name is cleaned** after the pieces are filled: every run of characters outside
printable ASCII becomes a dash, dashes in a row become one (an empty piece leaves two),
dashes and spaces are cut from both ends, the name starts with a letter or a digit, and
it is cut at `LIMIT` characters. A name with nothing left is `EMPTY`.

**Which template** names a launch: the entry of the setting `name.templates` for the
launch's project key when it holds one, else the setting `name.template`. A template the
check refuses (`refusal`: a piece outside the list above, or a `<` or `>` that belongs to
no piece) is never rendered: `chosen` gives the shipped template in its place, with a
warning line that names the key.

This module imports `live`, `store` and `catalog` of the launcher and nothing of the UI
shell, so a launch on an install without it is named too.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import catalog, live, store

#: The shipped template, which `name.template` holds until it is set.
DEFAULT = "<project>-<model>-<date:mmm-dd hh:mm>"
#: The settings that hold the templates: one for every launch, and a table of them by
#: project key.
TEMPLATE = "name.template"
TEMPLATES = "name.templates"
#: The module whose settings those are, as a warning names a key.
MODULE = "launcher"
#: The pieces a template may hold; `date` and `time` take a format after a colon.
PIECES = ("project", "agent", "model", "identity", "scope", "instance", "date", "time")
#: Where a template comes from: its project's entry, or the one for every launch.
PROJECT = "project"
GLOBAL = "global"
#: The longest name, in characters, and the name of a launch whose template leaves
#: nothing.
LIMIT = 63
EMPTY = "launch"
#: The most numbers `<instance>` tries before it takes the last.
INSTANCES = 1000

_PIECE = re.compile(r"<(agent|project|model|identity|scope|instance"
                    r"|date(?::[^<>]*)?|time(?::[^<>]*)?)>")
_BRACKETED = re.compile(r"<([^<>]*)>")
_FORMAT_TOKEN = re.compile(r"(?i)yyyy|yy|mmm|mm|dd|hh|ss")
_FORMATS = {"yyyy": "%Y", "yy": "%y", "mmm": "%b", "dd": "%d", "hh": "%H", "ss": "%S"}
_HINT = ("use <project>, <agent>, <model>, <identity>, <scope>, <instance>, "
         "<date[:format]> and <time[:format]>, and no other '<' or '>'")


class BadTemplate(catalog.Refused):
    """A name template that holds a piece no name is made of, or a stray bracket."""

    code = "bad-template"


@dataclass(frozen=True)
class Pieces:
    """What one launch fills a template's pieces with; each is empty when the launch has
    none."""

    project: str = ""
    agent: str = ""
    model: str = ""
    identity: str = ""
    scope: str = ""


@dataclass(frozen=True)
class Chosen:
    """The template that names a launch: its text, where it came from (`PROJECT` or
    `GLOBAL`), and a warning line when the one in the settings was refused and the
    shipped one stands in."""

    template: str
    source: str
    warning: str | None = None


def refusal(template: str) -> BadTemplate | None:
    """Why `template` can't be saved, or None when it can: it names a piece outside
    `PIECES`, or holds a `<` or `>` that is part of no piece."""
    for hit in _BRACKETED.finditer(template):
        if not _PIECE.fullmatch(hit.group(0)):
            return BadTemplate("name", f"the name template holds <{hit.group(1)}>, which is "
                                       "not a piece a name is made of", _HINT)
    rest = _PIECE.sub("", template)
    if "<" in rest or ">" in rest:
        return BadTemplate("name", "the name template holds a '<' or '>' that is not part "
                                   "of a piece", _HINT)
    return None


def date_format(text: str) -> str:
    """The format `text` as `strftime` takes it. One holding `%` is `strftime`'s
    already."""
    if "%" in text:
        return text
    out, at, hour_seen = [], 0, False
    for hit in _FORMAT_TOKEN.finditer(text):
        token = hit.group(0).lower()
        hour_seen = hour_seen or token == "hh"
        out += [text[at:hit.start()],
                ("%M" if hour_seen else "%m") if token == "mm" else _FORMATS[token]]
        at = hit.end()
    return "".join([*out, text[at:]])


def clean(raw: str) -> str:
    """`raw` as a name: printable ASCII, dashes in a row made one, no dash or space at
    either end, a letter or digit first, `LIMIT` characters at most; `EMPTY` when
    nothing is left."""
    out = re.sub(r"[^ -~]+", "-", raw)
    out = re.sub(r"-{2,}", "-", out).strip("- ")
    out = re.sub(r"^[^A-Za-z0-9]+", "", out)
    return out[:LIMIT].strip("- ") or EMPTY


def _filled(template: str, pieces: Pieces, now: datetime, instance: int) -> str:
    def piece(hit: re.Match) -> str:
        head, _, fmt = hit.group(1).partition(":")
        if head == "instance":
            return str(instance)
        if head == "scope":
            return pieces.scope if pieces.identity else ""
        if head == "date":
            return now.strftime(date_format(fmt) if fmt else "%Y%m%d")
        if head == "time":
            return now.strftime(date_format(fmt) if fmt else "%H%M")
        return str(getattr(pieces, head) or "")

    return clean(_PIECE.sub(piece, template))


def number(template: str, pieces: Pieces, *, now: datetime,
           taken: Collection[str] = ()) -> int:
    """The number `<instance>` renders as: the lowest positive one for which the rendered
    name is not in `taken`, the names of the live launches. 1 for a template without the
    piece. Raises `BadTemplate` for a template `refusal` refuses."""
    refused = refusal(template)
    if refused is not None:
        raise refused
    if "<instance>" not in template:
        return 1
    found = 1
    while found < INSTANCES and _filled(template, pieces, now, found) in taken:
        found += 1
    return found


def render(template: str, pieces: Pieces, *, now: datetime,
           taken: Collection[str] = ()) -> str:
    """The name `template` gives a launch with `pieces` at the local time `now`, cleaned,
    with `<instance>` the number `number` finds against `taken`. Raises `BadTemplate`
    for a template `refusal` refuses."""
    return _filled(template, pieces, now, number(template, pieces, now=now, taken=taken))


def chosen(settings: Mapping[str, Any], project_key: str | None = None) -> Chosen:
    """The template that names a launch in the project `project_key`, read from the
    launcher's `settings`: the project's entry of `TEMPLATES` when it holds one, else
    `TEMPLATE`. One the check refuses gives `DEFAULT` with a warning that names its
    key."""
    own = (settings[TEMPLATES] or {}).get(project_key) if project_key else None
    if own:
        template, source, key = str(own), PROJECT, f"{TEMPLATES}.{project_key}"
    else:
        template, source, key = str(settings[TEMPLATE]), GLOBAL, TEMPLATE
    refused = refusal(template)
    if refused is None:
        return Chosen(template, source)
    return Chosen(DEFAULT, source,
                  f"the setting {MODULE} {key} is not used: {refused.problem}; the launch "
                  f"is named by the shipped template {DEFAULT}")


def now() -> datetime:
    """The local time a launch made now is named with."""
    return datetime.now().astimezone()


def taken(conn: sqlite3.Connection) -> frozenset[str]:
    """The names of the live launches in the launch log, which `<instance>` avoids."""
    names = (row.values.get("name") for row in live.live(conn))
    return frozenset(str(name) for name in names if name)


def folder(cwd: str | None) -> str:
    """The name of the folder `cwd`, which stands for a project where a launch has
    none; the path itself when it has no last part, and nothing with no folder."""
    return (Path(cwd).name or str(cwd)) if cwd else ""


def shown(row: store.LaunchRow) -> str:
    """What the launch `row` is called wherever it is listed: its name, else its comms
    identity, else the name of the folder it runs in and the time it started, on this
    machine's clock."""
    name = row.values.get("name")
    if name:
        return str(name)
    if row.identity:
        return row.identity
    started = store.parse_time(row.created_at).astimezone()
    return f"{folder(row.cwd)} {started:%H:%M}"


__all__ = ["DEFAULT", "EMPTY", "GLOBAL", "INSTANCES", "LIMIT", "PIECES", "PROJECT",
           "TEMPLATE", "TEMPLATES", "BadTemplate", "Chosen", "Pieces", "chosen", "clean",
           "date_format", "folder", "now", "number", "render", "refusal", "shown", "taken"]
