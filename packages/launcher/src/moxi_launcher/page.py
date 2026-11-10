"""The launcher's page: the routes its page server answers, declared as data.

    GET  /api/catalog   the rows a launch may set on this install, per harness, as the
                        UI shell's form reads them
    GET  /api/presets   the seats and project presets, checked
    GET  /api/form      the value of each row a launch takes from the shipped defaults,
                        `[defaults]` and at most one preset, for one harness: what the
                        launch form shows when a preset is chosen; and the command a
                        launch of that harness composes in each place it may run
    GET  /api/launches  the launch log, newest first, paged by `limit` and `before`; with
                        `request`, the one launch a page launch's 202 points to
    GET  /api/sessions  the past-sessions list, as `moxi launcher sessions` gives it
    GET  /api/state     a session's state, or the state of every live launch
    GET  /api/runs      runs and their events: one run, one launch's, or the newest
    GET  /api/places    where this user last launched: a project or a folder each, newest
                        first, and whether a launch here may name a project
    GET  /api/targets   where a launch may run now: each slot with whether it is free and
                        which launch took it, whether a new slot can be made, and the
                        choice a launch starts with
    GET  /api/last      what the newest launch of a seat, a project or a folder was
                        asked for beyond its preset and the defaults: the values a
                        new launch there starts filled with
    GET  /api/name      the session name a launch of the form would take from its name
                        template, or from a template being edited, with what saving
                        that template would write
    POST /api/preview   what a launch would start, its refusals, and a ticket to launch it
    POST /api/launch    the launch a ticket was issued for, run as a detached job (202)
    POST /api/resume    a past session resumed: a slot one as a detached job (202), a
                        terminal one answered with the command to copy
    GET  /api/events    the stream; topics `sessions` and `launches`

**The page server only translates requests.** Every key a body may carry and every key an
answer sends is declared here at every depth, so loopback refuses an undeclared key coming
in and cuts one going out. A launch request's `values` and `leave_out` are catalog keys
only: the table is built from the launcher's catalog and both harnesses' when this module
is imported. A request carries the whole form: each field showing a value in `values`,
each field shown empty in `leave_out`, since a body holds no null; `filled` names the
fields whose value the page filled in from an earlier launch. A map
whose keys aren't fixed (a preset's values, an event's detail) goes as rows or as JSON
text. No answer carries a value from an environment or a launch's recorded environment. A
preview's argv and first turn may hold the brief, and are marked `redact`, and paths are
marked `path`. `/api/last` hands back values an earlier launch was asked with, each
marked `redact`, and is not open to the gateway; the launch log's own routes carry no
recorded value and no brief.

The page's assets are what the repository's web build writes from `packages/launcher/web/`
into this package's `web/` folder: the panel and the three components the announcement
declares (`page.js`, `prompt.js`, `sessions.js`, `state.js`), committed with their sources.

The browser UI shell starts the page server on the first request for the launcher's page,
and loopback stops it when it has been idle. Only that server and loopback's export command
import this module, each in a process of its own: nothing in the launcher's announcement,
API, CLI or console scripts does. The launcher's distribution doesn't require ui, which
comes with the `ui` extra, so this module reaches loopback, ui's page-server base, only
through `moxi.modules.load("ui", ...)` and raises `ImportError` when that fails.
"""

from __future__ import annotations

from moxi import modules

from . import catalog, names

_ui = modules.load("ui", interface=3)
if not _ui.ok:
    raise ImportError(f"the launcher's page needs the ui module: {_ui.reason}")
lb = _ui.api.loopback

Out, Field = lb.Out, lb.Field

#: The most rows a paged read answers with, and how many it gives when `limit` is left out.
LIMIT_MAX = 200
LIMIT = 50
#: The longest id or time a query takes, in characters.
QUERY_MAX = 128
#: The longest folder and the longest name template a query takes, in characters.
PATH_MAX = 4096
TEMPLATE_MAX = 512


def _text(nullable: bool = False, mark: str | None = None) -> Out:
    return Out("string", mark=mark, nullable=nullable)


def _texts(mark: str | None = None) -> Out:
    return Out("list", items=Out("string", mark=mark))


def _table(nullable: bool = False, **items: Out) -> Out:
    return Out("table", items=items, nullable=nullable)


#: How a read stands: `state` is `up` or `error`; `detail` says why when it is `error`.
STANDING = {"state": _text(), "detail": _text(nullable=True)}

#: A refusal, as the launcher words it: its short code, the row at fault if one is, what is
#: wrong and what to do instead.
REFUSAL = _table(code=_text(), row=_text(nullable=True), problem=_text(), hint=_text())

#: One catalog row as the UI shell's form reads it. `default` and `value_items` are the
#: shipped default spelled as the command line spells it; `flag` is the launcher's option.
#: A row this install doesn't offer is `needs_absent`, with `note` saying why.
#: `group`, `sub` and `label` say where the launch options draw the row and what they call
#: it; `danger` lists its values that can hurt; `carry` is false for a row never filled
#: from an earlier launch; `modes` names the commands the row composes into, none for a
#: launcher row. `shown` is false for a row to leave undrawn here: one this install
#: doesn't offer, or one drawn only with a module that is absent. `typed` is false for a
#: row that takes only its listed values here, the module its typed values need absent.
FIELD = _table(
    name=_text(), type=_text(), doc=_text(),
    default=_text(nullable=True),
    value_items=Out("list", items=Out("string"), nullable=True),
    values=Out("list", items=Out("string"), nullable=True),
    items=_text(nullable=True),
    flag=_text(nullable=True),
    since=_text(nullable=True),
    repeatable=Out("bool"), optional_value=Out("bool"), hidden=Out("bool"),
    posture=Out("bool"),
    requires=_text(nullable=True),
    needs_absent=Out("bool"),
    note=_text(nullable=True),
    group=_text(nullable=True), sub=_text(nullable=True), label=_text(),
    danger=_texts(), carry=Out("bool"), modes=_texts(),
    shown=Out("bool"), typed=Out("bool"),
)

CATALOG = {**STANDING,
           "harnesses": Out("list", items=_table(name=_text(), fields=Out("list", items=FIELD)))}

#: One value a preset sets: a list row's in `items`, any other's in `value`, spelled as
#: the command line spells it. A preset's inline brief or instructions is named, never
#: sent: both are null.
PRESET_VALUE = _table(name=_text(), value=_text(nullable=True),
                      items=Out("list", items=Out("string"), nullable=True))
PRESET = _table(kind=_text(), name=_text(), harness=_text(nullable=True),
                values=Out("list", items=PRESET_VALUE))
#: One thing wrong with a preset, or with the file (`where` is `file`).
PROBLEM = _table(where=_text(), key=_text(nullable=True), problem=_text(), hint=_text(),
                 code=_text(), level=_text())
PRESETS = {**STANDING, "presets": Out("list", items=PRESET),
           "problems": Out("list", items=PROBLEM)}

#: One row of the launch form as the layers fill it, spelled as a preset's value is:
#: `source` is `shipped`, `defaults` or `preset`. A brief or standing instructions that
#: `[defaults]` or the preset writes inline is `withheld`, its text not sent.
FORM_ROW = _table(name=_text(), value=_text(nullable=True),
                  items=Out("list", items=Out("string"), nullable=True),
                  source=_text(), withheld=Out("bool"))
#: The command a launch of the form's harness composes when it runs in `target`, one of
#: the names a catalog row's `modes` holds; null where such a launch is refused.
FORM_MODE = _table(target=_text(), mode=_text(nullable=True))
#: One value an earlier launch's own request gave, spelled as a preset's value is.
HANDED = _table(name=_text(), value=_text(nullable=True, mark="redact"),
                items=Out("list", items=Out("string", mark="redact"), nullable=True))
#: The newest launch of the key asked for: when it was made, its harness, and each value
#: its own request gave for a row that may be filled from an earlier launch. `at` and
#: `harness` are null, and `values` empty, when the key has no such launch.
LAST = {**STANDING, "at": _text(nullable=True), "harness": _text(nullable=True),
        "values": Out("list", items=HANDED)}

#: The launch form for one harness: the harness drawn, the value of each row offered for
#: it, or, when the preset can't be laid for that harness, no rows and the refusal.
#: `modes` has one entry per value of the `target` row, in the row's order, for a launch
#: under the comms name the query's `identity` gives.
FORM = {**STANDING, "harness": _text(), "rows": Out("list", items=FORM_ROW),
        "modes": Out("list", items=FORM_MODE),
        "refused": Out("table", items=REFUSAL.items, nullable=True)}

#: One launch log row, cut to what the page shows: none of its recorded values, argv,
#: environment or brief, and none of its process facts.
LAUNCH = _table(
    request_id=_text(), created_at=_text(), updated_at=_text(), actor=_text(),
    harness=_text(), target=_text(), state=_text(), reason=_text(nullable=True),
    posture=_text(), stale=Out("bool"),
    preset_kind=_text(nullable=True), preset_name=_text(nullable=True),
    slot=_text(nullable=True), cwd=_text(mark="path"), project_key=_text(nullable=True),
    identity=_text(nullable=True), scope=_text(nullable=True),
    session_id=_text(nullable=True), relaunch_of=_text(nullable=True),
    resume_of=_text(nullable=True),
)
#: With `request`, `launches` holds that launch's row once it is written, and `refused`
#: the refusal of a page launch that was refused before its row was; `why` is the sentence
#: recorded for that launch's failure, null for a row that did not fail or recorded none.
LAUNCHES = {**STANDING, "launches": Out("list", items=LAUNCH),
            "refused": Out("table", items=REFUSAL.items, nullable=True),
            "why": _text(nullable=True, mark="redact")}

#: One entry of the past-sessions list. `live` is null when it could not be read.
PAST = _table(
    request_id=_text(), created_at=_text(), harness=_text(), target=_text(),
    slot=_text(nullable=True), cwd=_text(mark="path"), identity=_text(nullable=True),
    state=_text(), session_id=_text(nullable=True), live=Out("bool", nullable=True),
    resumable=Out("bool"), why=_text(nullable=True), hint=_text(nullable=True),
    unread=_text(nullable=True), held_by=_text(nullable=True),
    resume_of=_text(nullable=True), relaunch_of=_text(nullable=True),
)
SESSIONS = {**STANDING, "sessions": Out("list", items=PAST)}

#: A run as a session's state shows it.
RUN_LINE = _table(run_id=_text(), kind=_text(), mode=_text(), state=_text(),
                  outcome=_text(nullable=True), word=_text(nullable=True),
                  detail=_text(nullable=True), created_at=_text(), updated_at=_text())
_RUN_LINE_OR_NONE = Out("table", items=RUN_LINE.items, nullable=True)
#: A launched session's state: its relaunch, recycle and keep-warm, and whether it is red.
#: When one launch's state could not be read, its entry names the launch, says why in
#: `unreadable`, and holds null for its relaunch, recycle and keep-warm; the others stand.
SESSION_STATE = _table(
    session_id=_text(), request_id=_text(),
    relaunch=_table(state=_text(nullable=True), run=_RUN_LINE_OR_NONE,
                    made_by=_text(nullable=True), notices_refused=_texts(),
                    note_not_taken=Out("bool"), nullable=True),
    recycle=_table(on_expiry=_text(), at_limit=_text(), limit_at=_text(nullable=True),
                   last=_RUN_LINE_OR_NONE, pending_compact=_text(nullable=True),
                   told_at=_text(nullable=True), pending_dropped_at=_text(nullable=True),
                   nullable=True),
    keep_warm=_table(on=Out("bool"), holds=_texts(), nudges=Out("int"),
                     last=_RUN_LINE_OR_NONE, window=_text(nullable=True), nullable=True),
    red=Out("bool"), since=_text(nullable=True), why=_text(nullable=True),
    word=_text(nullable=True), run_id=_text(nullable=True),
    unreadable=_text(nullable=True),
)
#: With `session`, `sessions` holds that session's state, or nothing and `refused` when no
#: launch holds the id; without, the state of every live launch.
STATE = {**STANDING, "sessions": Out("list", items=SESSION_STATE),
         "refused": Out("table", items=REFUSAL.items, nullable=True)}

#: One event of a run: `detail` is its detail as JSON text.
RUN_EVENT = _table(at=_text(), kind=_text(), detail=_text())
#: One run, cut to what the page shows: not the job that runs it, nor its pane.
RUN = _table(
    run_id=_text(), kind=_text(), mode=_text(), request_id=_text(), session_id=_text(),
    actor=_text(), state=_text(), outcome=_text(nullable=True), word=_text(nullable=True),
    detail=_text(nullable=True), repeats=Out("int"), created_at=_text(),
    updated_at=_text(), slot=_text(nullable=True), turn_number=Out("int", nullable=True),
    new_request_id=_text(nullable=True), repair_of=_text(nullable=True),
    handoff_path=_text(nullable=True, mark="path"),
    events=Out("list", items=RUN_EVENT),
)
RUNS = {**STANDING, "runs": Out("list", items=RUN)}

#: One place the user launched in: a project's key, or null for a folder, with the folder
#: and the time of the newest launch there.
PLACE = _table(project_key=_text(nullable=True), cwd=_text(mark="path"), at=_text())
#: `projects` is whether a launch here may name a project; `actor` is who the page
#: server launches as, whose launches the places are; `home` is the user's home folder
#: as the page server sees it.
PLACES = {**STANDING, "projects": Out("bool"), "actor": _text(), "home": _text(mark="path"),
          "places": Out("list", items=PLACE)}

#: One slot a launch may name. `holder` and `until` (seconds since the epoch) are a live
#: hold's; `taken_by` is the launch that occupies the slot when the launch log knows it.
SLOT = _table(number=Out("int"), state=_text(), launchable=Out("bool"),
              holder=_text(nullable=True), until=Out("float", nullable=True),
              taken_by=_table(request_id=_text(), label=_text(), nullable=True))
#: Where a launch may run. `slots` is empty and `new_slot` false where slots can't be
#: used or read; `unread` is true only where slots is installed and can run and its
#: slots could not be read. `default` is the choice a launch starts with: `target` is
#: `slot` or `terminal`, and `slot` the lowest slot free now, or null to take a new one.
TARGETS = {**STANDING, "slots": Out("list", items=SLOT), "new_slot": Out("bool"),
           "unread": Out("bool"),
           "default": _table(target=_text(), slot=Out("int", nullable=True))}


#: The name a launch would take. `name` is the template rendered now, null while
#: `refused`; `template` is the text rendered and `from` whose it is, `project` or
#: `global`. `refused` is why a template being edited can't be saved. `save` is what
#: saving it writes, a launcher setting's key and its value as the command line spells
#: it; null while refused, and when no template is being edited.
NAME = {**STANDING, "name": _text(nullable=True), "template": _text(), "from": _text(),
        "refused": Out("table", items=REFUSAL.items, nullable=True),
        "save": _table(nullable=True, key=_text(), value=_text())}


def _values_table() -> dict[str, Field]:
    """A launch request's `values`: every row a user may set, of the launcher and of each
    harness, by name, each optional. A list row, or one given more than once, takes a list
    of strings; any other row one string, spelled as the command line spells it."""
    out: dict[str, Field] = {}
    for rows in (catalog.launcher(), *(catalog.load(h) for h in catalog.HARNESSES)):
        for row in rows:
            if not row.settable:
                continue
            many = row.type == "list" or row.repeatable
            field = (Field("list", required=False, items=Field("string")) if many
                     else Field("string", required=False))
            if out.setdefault(row.name, field) != field:
                raise lb.PageError(f"the catalog row {row.name} is a list for one harness "
                                   "and a single value for another")
    return out


#: Every catalog key a launch request may set.
VALUES = _values_table()

#: A launch as the page asks for it: at most one preset, the value of each field the form
#: shows filled, the name of each it shows empty, whether a posture off its default is
#: meant, and the name of each field whose value was filled in from an earlier launch.
REQUEST = {
    "seat": Field("string", required=False, max=QUERY_MAX),
    "project_preset": Field("string", required=False, max=QUERY_MAX),
    "values": Field("table", required=False, items=VALUES),
    "leave_out": Field("list", required=False, max=len(VALUES),
                       items=Field("string", max=QUERY_MAX)),
    "off_posture": Field("bool", required=False),
    "filled": Field("list", required=False, max=len(VALUES),
                    items=Field("string", max=QUERY_MAX)),
}

#: What a launch would start. `refused` lists what stops it; `ticket` is issued only when
#: nothing does and the target is not `terminal`, whose `command` the page shows to copy.
#: `left_out` names the rows the launch leaves out that a layer set.
PREVIEW = {
    "refused": Out("list", items=REFUSAL),
    "harness": _text(nullable=True), "mode": _text(nullable=True),
    "target": _text(nullable=True),
    "argv": Out("list", items=Out("string", mark="redact")),
    "stamps": Out("list", items=_table(name=_text(), value=_text())),
    "first_turn": _text(nullable=True, mark="redact"),
    "not_applied": _texts(), "warnings": _texts(), "left_out": _texts(),
    "command": _text(nullable=True, mark="redact"),
    "request_id": _text(nullable=True), "ticket": _text(nullable=True),
    "expires_at": _text(nullable=True),
}

#: A refusal a page launch or resume meets before anything runs; null when it went ahead
#: (202) or, for a terminal resume, with the command to copy.
ACTED = {"refused": Out("table", items=REFUSAL.items, nullable=True)}
#: A page launch's refusal; for a launch in a terminal, which the page never runs, the
#: `moxi launcher run` command to copy too.
LAUNCHED = {**ACTED, "command": _text(nullable=True, mark="redact")}

#: Whose launches a read is about: a seat's, else a project's, else a folder's. The first
#: of the three that is given decides.
_WHOSE = {"seat": Field("string", required=False, max=QUERY_MAX),
          "project": Field("string", required=False, max=QUERY_MAX),
          "cwd": Field("string", required=False, max=PATH_MAX)}

_PAGED = {"limit": Field("int", required=False, max=LIMIT_MAX),
          "before": Field("string", required=False, max=QUERY_MAX)}


def _target(name: str) -> str:
    return f"moxi_launcher.pagehandlers:{name}"


PAGE = lb.Page(
    routes=(
        lb.Route("/api/catalog", "read", _target("read_catalog"), returns=CATALOG),
        lb.Route("/api/presets", "read", _target("read_presets"), returns=PRESETS),
        lb.Route("/api/form", "read", _target("read_form"),
                 query={"seat": Field("string", required=False, max=QUERY_MAX),
                        "project_preset": Field("string", required=False, max=QUERY_MAX),
                        "harness": Field("enum", required=False,
                                         values=catalog.HARNESSES),
                        "identity": Field("string", required=False, max=QUERY_MAX)},
                 returns=FORM),
        lb.Route("/api/launches", "read", _target("read_launches"),
                 query={**_PAGED, "request": Field("string", required=False, max=QUERY_MAX)},
                 returns=LAUNCHES),
        lb.Route("/api/sessions", "read", _target("read_sessions"), query=_PAGED,
                 returns=SESSIONS),
        lb.Route("/api/state", "read", _target("read_state"),
                 query={"session": Field("string", required=False, max=QUERY_MAX)},
                 returns=STATE),
        lb.Route("/api/runs", "read", _target("read_runs"),
                 query={**_PAGED, "run": Field("string", required=False, max=QUERY_MAX),
                        "request": Field("string", required=False, max=QUERY_MAX)},
                 returns=RUNS),
        lb.Route("/api/places", "read", _target("read_places"), returns=PLACES),
        lb.Route("/api/targets", "read", _target("read_targets"), returns=TARGETS),
        lb.Route("/api/last", "read", _target("read_last"), query=_WHOSE, returns=LAST),
        lb.Route("/api/name", "read", _target("read_name"),
                 query={**{key: Field("string", required=False, max=QUERY_MAX)
                           for key in ("seat", "project", "model", "identity", "scope",
                                       "agent")},
                        "cwd": Field("string", required=False, max=PATH_MAX),
                        "harness": Field("enum", required=False, values=catalog.HARNESSES),
                        "template": Field("string", required=False, max=TEMPLATE_MAX),
                        "keep": Field("enum", required=False,
                                      values=(names.PROJECT, names.GLOBAL))},
                 returns=NAME),
        lb.Route("/api/preview", "act", _target("preview"), body=REQUEST, returns=PREVIEW),
        lb.Route("/api/launch", "act", _target("launch"),
                 body={**REQUEST, "ticket": Field("string", max=1024)}, returns=LAUNCHED),
        lb.Route("/api/resume", "act", _target("resume"),
                 body={"session": Field("string", max=QUERY_MAX),
                       "slot": Field("string", required=False, max=QUERY_MAX)},
                 returns={**ACTED, "command": _text(nullable=True)}),
        lb.Route("/api/events", "read", _target("events"), stream=True,
                 topics={"sessions": _table(**STANDING,
                                            sessions=Out("list", items=SESSION_STATE)),
                         "launches": _table(**STANDING,
                                            launches=Out("list", items=LAUNCH))}),
    ),
    assets="web",
)

__all__ = ["ACTED", "CATALOG", "FIELD", "FORM", "FORM_MODE", "FORM_ROW", "HANDED", "LAST", "LAUNCH", "LAUNCHED", "LAUNCHES", "LIMIT", "LIMIT_MAX",
           "NAME", "PAGE", "PAST", "PATH_MAX", "PLACE", "PLACES", "PRESET", "PRESETS", "PREVIEW", "PROBLEM", "REFUSAL", "REQUEST", "RUN",
           "RUNS", "RUN_EVENT", "RUN_LINE", "SESSIONS", "SESSION_STATE", "SLOT", "STANDING", "STATE",
           "TARGETS", "TEMPLATE_MAX", "VALUES"]
