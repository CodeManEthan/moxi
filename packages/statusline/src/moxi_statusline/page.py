"""statusline's page: the routes its page server answers, declared as data.

    GET  /api/state    the saved config's values and `base`, the stamp of the file they
                       were read from; the items the line can show; Claude Code's
                       `statusLine` as statusline sees it
    POST /api/preview  `{base, changes}`: the line the draft would render, by the real
                       command over the sample input, with its time; writes no config
    POST /api/save     `{base, changes}`: the draft written in one checked write
    POST /api/use      Claude Code's line set to Moxi's, as `moxi statusline use` does
                       when the line isn't Moxi's, with no question
    POST /api/restore  the old line put back, as `moxi statusline restore`

**The page writes the config only on Save, and the preview always renders what Save
would write now.** A draft is a set of changes over `base`. The preview and Save build
its text with the same function over the same file (`moxi.config.draft_text` and
`set_many`), so a draft the preview shows is the draft Save writes, and one Save
refuses is one the preview refuses.

`changes` is a table of the keys a draft changes, each only when it does: `show`,
`separator`, `color`, `dir.parts` as `dir` `{parts}`, and the two context bands as
`context` `{warn_at, alert_at}`. The handlers apply them in the order the body gives.

A refused draft is a 422 naming its key, or saying the draft as a whole was refused. A
config file changed since `base`, or held by another writer, is a 409, and nothing is
written; the page then reads `/api/state` and keeps its changes over the new base.

The browser UI shell starts the page server on the first request for statusline's page,
and loopback stops it when it has been idle. Only that server and loopback's export
command import this module, each in a process of its own: nothing in statusline's
announcement, CLI or render does. statusline's distribution doesn't require ui, which
comes with the `ui` extra, so this module reaches loopback, ui's page-server base, only
through `moxi.modules.load("ui", ...)` and raises `ImportError` when that fails.
"""

from __future__ import annotations

from moxi import modules

_ui = modules.load("ui", interface=3)
if not _ui.ok:
    raise ImportError(f"statusline's page needs the ui module: {_ui.reason}")
lb = _ui.api.loopback

#: The longest `show` list, item name or separator a body takes.
LIST_MAX = 64
NAME_MAX = 64
SEPARATOR_MAX = 64
#: The largest number a draft may give a numeric key; the config's own check refuses
#: what it doesn't take below this.
NUMBER_MAX = 1_000_000
#: The length of a stamp: SHA-256 in hex.
STAMP_MAX = 64

_TEXT = lb.Out("string")
_TEXTS = lb.Out("list", items=_TEXT)
#: A sentence that can name a path.
_SAYS = lb.Out("string", mark="redact")
_MAYBE_SAYS = lb.Out("string", mark="redact", nullable=True)

#: The config's values, nested as the file holds them.
VALUES = lb.Out("table", items={
    "show": _TEXTS,
    "separator": _TEXT,
    "color": lb.Out("bool"),
    "dir": lb.Out("table", items={"parts": lb.Out("int")}),
    "context": lb.Out("table", items={"warn_at": lb.Out("int"),
                                      "alert_at": lb.Out("int")}),
})

#: A module the line can show: an installed module with a status line segment.
MODULE_ITEM = lb.Out("table", items={"name": _TEXT, "summary": _TEXT})

#: Claude Code's `statusLine`: `set`, whether it has one; `command`, its command (null
#: when none); `moxis`, whether it is Moxi's line; `here`, Moxi's at this install's
#: render; `saved`, whether an old line is kept to put back; `problem`, why the settings
#: or the kept line can't be read (null when they can).
CLAUDE = lb.Out("table", items={
    "set": lb.Out("bool"),
    "command": _MAYBE_SAYS,
    "moxis": lb.Out("bool"),
    "here": lb.Out("bool"),
    "saved": lb.Out("bool"),
    "problem": _MAYBE_SAYS,
})

#: The page's state. `problem` says why the saved config can't be read (its values are
#: then the defaults and nothing can be saved over it), null when it reads.
STATE = {
    "base": _TEXT,
    "values": VALUES,
    "problem": _MAYBE_SAYS,
    "fields": _TEXTS,
    "modules": lb.Out("list", items=MODULE_ITEM),
    "claude": CLAUDE,
}

#: A preview: the rows the render printed, its time in ms, a note for each shown item
#: with nothing for the sample, and `problem` when the render didn't finish or run.
PREVIEW = {
    "lines": _TEXTS,
    "ms": lb.Out("float", nullable=True),
    "notes": _TEXTS,
    "problem": _MAYBE_SAYS,
}

#: An act on Claude Code's line: what it said, and the state after it.
ACTED = {"said": lb.Out("list", items=_SAYS), **STATE}


def _optional_int() -> lb.Field:
    return lb.Field("int", required=False, max=NUMBER_MAX)


#: The keys a draft changes.
CHANGES = lb.Field("table", required=False, items={
    "show": lb.Field("list", required=False, max=LIST_MAX,
                     items=lb.Field("string", max=NAME_MAX)),
    "separator": lb.Field("string", required=False, max=SEPARATOR_MAX),
    "color": lb.Field("bool", required=False),
    "dir": lb.Field("table", required=False, items={"parts": _optional_int()}),
    "context": lb.Field("table", required=False, items={"warn_at": _optional_int(),
                                                        "alert_at": _optional_int()}),
})

DRAFT = {"base": lb.Field("string", max=STAMP_MAX), "changes": CHANGES}


def _target(name: str) -> str:
    return f"moxi_statusline.pagehandlers:{name}"


PAGE = lb.Page(
    assets="web",
    routes=(
        lb.Route("/api/state", "read", _target("state"), returns=STATE),
        lb.Route("/api/preview", "act", _target("preview"), body=DRAFT, returns=PREVIEW),
        lb.Route("/api/save", "act", _target("save"), body=DRAFT, returns=STATE),
        lb.Route("/api/use", "act", _target("use"), returns=ACTED),
        lb.Route("/api/restore", "act", _target("restore"), returns=ACTED),
    ),
)

__all__ = ["ACTED", "CHANGES", "CLAUDE", "DRAFT", "MODULE_ITEM", "PAGE", "PREVIEW", "STATE",
           "VALUES"]
