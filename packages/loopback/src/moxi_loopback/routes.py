"""The route table: what a page declares, the table the check reads, and the export.

A page module declares one `Page` of `Route`s as data. Handlers are named by target
string, so the shell, the gateway and tests read the table without importing handler
code. From a `Page` this module derives:

- the `Table` of entries the request check looks a request up in, through
  `build_table`, which the runner and the export command both call, so a module's
  mounted export and the table its server serves agree for one installed version;
- the query and body allowlist, `parse_query` and `parse_body`;
- projection, `project`, which cuts every JSON value a handler produces to its declared
  shape before it is sent;
- `export(page)`, the table as JSON-able data, which the shell mounts and the gateway
  reads as its allowlist.

Paths are literal and matched exactly on the raw path as received. A path holds no
pattern or parameter; arguments are query keys. The method comes from the kind, GET for
a read and POST for an act, so the table cannot declare a GET that acts.

This module imports only the standard library.
"""

from __future__ import annotations

import importlib.resources
import json
import math
import re
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import KW_ONLY, dataclass, field
from types import MappingProxyType
from typing import Any
from urllib.parse import parse_qsl

# Only the names `moxi_loopback.api` re-exports are public; the rest are the package's own.
__all__ = [
    "Entry",
    "Field",
    "Out",
    "Page",
    "PageError",
    "Route",
    "Table",
    "build_table",
    "export",
]

#: The export's shape. A reader tells shapes apart by it.
FORMAT = 1

HEALTH_PATH = "/api/health"
PROVE_PATH = "/api/prove"
_RESERVED = frozenset({HEALTH_PATH, PROVE_PATH})

#: An act's body cap, and the most a route may raise it to.
BODY_CAP = 64 * 1024
BODY_CAP_MAX = 1024 * 1024

#: A page's budget when it declares none, or for a key it leaves out.
BUDGET = MappingProxyType({"rss": "120M", "fds": 64})
_IDLE_S = 300
_IDLE_MIN = 5

#: The only file types an asset directory may hold, with what each is served as.
CONTENT_TYPES = MappingProxyType({
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
})

_METHODS = MappingProxyType({"read": "GET", "act": "POST"})
_ENTRY_KINDS = ("read", "act", "asset", "builtin")

# Every pattern anchors with `\A` and `\Z`, never `^` and `$`: Python's `$` matches
# before a trailing newline.
_PATH = re.compile(r"\A(/[a-z0-9][a-z0-9._-]*)+\Z")
#: A table entry's path: a route path, an asset URL, `/`, or a mounted prefix's `/`.
_ENTRY_PATH = re.compile(r"\A(?:/|(?:/[A-Za-z0-9][A-Za-z0-9._-]*)+/?)\Z")
_PREFIX = re.compile(r"\A(/[A-Za-z0-9][A-Za-z0-9._-]*)+\Z")
_TOPIC = re.compile(r"\A[a-z0-9]+(-[a-z0-9]+)*\Z")
_KEY = re.compile(r"\A[A-Za-z0-9_-]+\Z")
_TARGET = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*\Z")
_PACKAGE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
_ASSET_DIR = re.compile(r"\A[A-Za-z0-9_][A-Za-z0-9._-]*\Z")
_ASSET_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_SIZE = re.compile(r"\A([0-9]+)([KMG]?)\Z")
_QUERY_INT = re.compile(r"\A-?[0-9]{1,18}\Z")

_SCALARS = ("string", "int", "float", "bool", "enum")
_QUERY_TYPES = ("string", "int", "bool", "enum")
_FIELD_TYPES = _SCALARS + ("list", "table")
_OUT_TYPES = ("string", "int", "float", "bool", "list", "table")
_MARKS = (None, "path", "redact")
_EMPTY: Mapping[str, Any] = MappingProxyType({})


class PageError(ValueError):
    """A declaration or an asset table refused. The runner exits 3 on one."""


class BadRequest(ValueError):
    """A query or body refused: the server's 400.

    `key` is the dotted path of the key at fault, or None for the body as a whole. The
    message names the key and never holds the value sent.
    """

    def __init__(self, key: str | None, reason: str):
        self.key = key
        self.reason = reason
        super().__init__(f"{reason}: {key}" if key else reason)


class ProjectionError(ValueError):
    """A handler's value does not fit its declared shape: the server's 500.

    `path` is the dotted path of the value at fault ("" for the value itself). The
    message never holds the value.
    """

    def __init__(self, path: str, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"{reason}: {path or '(the value)'}")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _frozen_map(value: object, what: str, check: Callable[[str, Any], None]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PageError(f"{what} must be a mapping of key names")
    items = dict(value)
    for key, item in items.items():
        if not isinstance(key, str) or not _KEY.match(key):
            raise PageError(f"{what} has a key that is not letters, digits, _ or -: {key!r}")
        check(key, item)
    return MappingProxyType(items)


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


# --- declarations -------------------------------------------------------------------


@dataclass(frozen=True)
class Field:
    """One query or body key: its type and what it accepts.

    `type` is `string`, `int`, `bool` or `enum` in a query, and also `float`, `list` or
    `table` in a body. `values` lists an `enum`'s words. `max` caps an `int` or `float`
    value, a `string`'s length in characters and a `list`'s item count. `items` says what
    a `list` or `table` holds: for a `list`, one scalar `Field` every item must match;
    for a `table`, a key map of `Field`s, nested to any depth.

    Every value a body may carry, at every depth, is declared by a `Field`, so a `list`
    or `table` without `items`, `items` on any other type, and a `list` whose item is not
    a scalar are refused here.
    """

    type: str
    required: bool = True
    values: tuple[str, ...] = ()
    max: int | float | None = None
    items: Field | Mapping[str, Field] | None = None

    def __post_init__(self) -> None:
        if self.type not in _FIELD_TYPES:
            raise PageError(f"a Field type is one of {', '.join(_FIELD_TYPES)}, not {self.type!r}")
        if not isinstance(self.required, bool):
            raise PageError("a Field's required is True or False")
        if isinstance(self.values, str) or not isinstance(self.values, Iterable):
            raise PageError("a Field's values is a sequence of words")
        values = tuple(self.values)
        object.__setattr__(self, "values", values)
        if self.type == "enum":
            if not values or not all(isinstance(v, str) for v in values):
                raise PageError("an enum Field needs values, each a string")
            if len(set(values)) != len(values):
                raise PageError("an enum Field lists a value twice")
        elif values:
            raise PageError(f"values belongs to an enum Field, not a {self.type}")
        self._check_max()
        self._check_items()

    def _check_max(self) -> None:
        cap = self.max
        if cap is None:
            return
        if self.type in ("string", "list", "int"):
            if not _is_int(cap):
                raise PageError(f"a {self.type} Field's max is an integer")
            if self.type != "int" and cap < 0:
                raise PageError(f"a {self.type} Field's max is 0 or more")
        elif self.type == "float":
            if not _is_number(cap) or not math.isfinite(cap):
                raise PageError("a float Field's max is a finite number")
        else:
            raise PageError(f"max caps an int, float, string or list, not a {self.type}")

    def _check_items(self) -> None:
        items = self.items
        if self.type == "list":
            if not isinstance(items, Field):
                raise PageError("a list Field needs items, one scalar Field")
            if items.type not in _SCALARS:
                raise PageError(f"a list Field's items is a scalar Field, not a {items.type}")
        elif self.type == "table":
            if items is None:
                raise PageError("a table Field needs items, a key map of Fields")

            def check(key: str, item: object) -> None:
                if not isinstance(item, Field):
                    raise PageError(f"a table Field's key {key!r} is not a Field")

            object.__setattr__(self, "items", _frozen_map(items, "a table Field's items", check))
        elif items is not None:
            raise PageError(f"items belongs to a list or table Field, not a {self.type}")


@dataclass(frozen=True)
class Out:
    """One value of a response or a stream event: its type, mark and contents.

    `type` is `string`, `int`, `float`, `bool`, `list` or `table`. `items` says what a
    `list` holds (one `Out`) or a `table` holds (a key map of `Out`s), nested to any
    depth; a list of rows is `Out("list", items=Out("table", items={...}))`. `mark` is
    `path` (the gateway sends a handle in its place) or `redact` (the gateway cuts it);
    only the gateway acts on a mark. `nullable` lets the value be JSON `null`; a `null`
    anywhere else is refused by projection.
    """

    type: str
    mark: str | None = None
    items: Out | Mapping[str, Out] | None = None
    nullable: bool = False

    def __post_init__(self) -> None:
        if self.type not in _OUT_TYPES:
            raise PageError(f"an Out type is one of {', '.join(_OUT_TYPES)}, not {self.type!r}")
        if self.mark not in _MARKS:
            raise PageError(f"an Out mark is 'path' or 'redact', not {self.mark!r}")
        if self.mark == "path" and self.type != "string":
            raise PageError("the path mark belongs on a string Out")
        if not isinstance(self.nullable, bool):
            raise PageError("an Out's nullable is True or False")
        items = self.items
        if self.type == "list":
            if not isinstance(items, Out):
                raise PageError("a list Out needs items, one Out")
        elif self.type == "table":
            if items is None:
                raise PageError("a table Out needs items, a key map of Outs")

            def check(key: str, item: object) -> None:
                if not isinstance(item, Out):
                    raise PageError(f"a table Out's key {key!r} is not an Out")

            object.__setattr__(self, "items", _frozen_map(items, "a table Out's items", check))
        elif items is not None:
            raise PageError(f"items belongs to a list or table Out, not a {self.type}")


@dataclass(frozen=True)
class Route:
    """One literal path, its kind and handler, and the shapes it takes and returns.

    `kind` is `read` (GET) or `act` (POST). `handler` is a `"pkg.mod:fn"` target,
    resolved once at start. `query` and `body` map key names to `Field`s; a body belongs
    to an act. `returns` maps key names to `Out`s and is required on every route but a
    stream (`returns={}` always sends `{}`). A `stream` route is a read that answers with
    server-sent events; it declares `topics`, topic names to the `Out` each event is cut
    to, and no `returns`, and `topics` is then a reserved query key. `gateway` says
    whether the gateway may reach the route. `body_cap` is an act's body cap in bytes, up
    to 1 MiB.

    `confirmed=True` makes an act a confirmed act: one the user confirms a second time and
    that runs at most once per `Idempotency-Key`. It names `status`, the path of a read of
    the same page whose query declares a string `job` key, and its handler returns
    `Accepted` with a path on that route. Only a confirmed act has a `status`, and every
    confirmed act has one.
    """

    path: str
    kind: str
    handler: str
    _: KW_ONLY
    query: Mapping[str, Field] = _EMPTY
    body: Mapping[str, Field] = _EMPTY
    returns: Mapping[str, Out] | None = None
    gateway: bool = False
    stream: bool = False
    topics: Mapping[str, Out] | None = None
    body_cap: int = BODY_CAP
    confirmed: bool = False
    status: str | None = None
    shape: Out | None = field(init=False, repr=False, compare=False, default=None)

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not _PATH.match(self.path):
            raise PageError(f"a route path is literal lower-case segments, not {self.path!r}")
        where = f"route {self.path}"
        if self.path in _RESERVED:
            raise PageError(f"{self.path} is reserved for the base")
        if self.kind not in _METHODS:
            raise PageError(f"{where}: kind is 'read' or 'act', not {self.kind!r}")
        if not isinstance(self.handler, str) or not _TARGET.match(self.handler):
            raise PageError(f"{where}: handler is a 'pkg.mod:fn' target")
        if not isinstance(self.gateway, bool) or not isinstance(self.stream, bool):
            raise PageError(f"{where}: gateway and stream are True or False")
        if not _is_int(self.body_cap) or not 1 <= self.body_cap <= BODY_CAP_MAX:
            raise PageError(f"{where}: body_cap is 1 to {BODY_CAP_MAX} bytes")
        if not isinstance(self.confirmed, bool):
            raise PageError(f"{where}: confirmed is True or False")
        if self.confirmed and self.kind != "act":
            raise PageError(f"{where}: only an act is confirmed")
        if self.status is not None:
            if not self.confirmed:
                raise PageError(f"{where}: only a confirmed act names a status route")
            if not isinstance(self.status, str) or not _PATH.match(self.status):
                raise PageError(f"{where}: status is a route path, not {self.status!r}")
        elif self.confirmed:
            raise PageError(f"{where}: a confirmed act names its status route")

        def query_field(key: str, item: object) -> None:
            if not isinstance(item, Field) or item.type not in _QUERY_TYPES:
                raise PageError(f"{where}: query key {key!r} is a string, int, bool or enum Field")
            if self.stream and key == "topics":
                raise PageError(f"{where}: topics is a reserved query key on a stream route")

        def body_field(key: str, item: object) -> None:
            if not isinstance(item, Field):
                raise PageError(f"{where}: body key {key!r} is not a Field")

        def out(key: str, item: object) -> None:
            if not isinstance(item, Out):
                raise PageError(f"{where}: {key!r} is not an Out")

        def topic(name: str, item: object) -> None:
            if not _TOPIC.match(name):
                raise PageError(f"{where}: {name!r} is not a topic name")
            out(name, item)

        object.__setattr__(self, "query", _frozen_map(self.query, f"{where}: query", query_field))
        object.__setattr__(self, "body", _frozen_map(self.body, f"{where}: body", body_field))
        if self.body and self.kind != "act":
            raise PageError(f"{where}: only an act takes a body")
        if self.stream:
            if self.kind != "read":
                raise PageError(f"{where}: a stream route is a read")
            if self.returns is not None:
                raise PageError(f"{where}: a stream route declares topics, not returns")
            if not self.topics:
                raise PageError(f"{where}: a stream route declares its topics")
            object.__setattr__(self, "topics", _frozen_map(self.topics, f"{where}: topics", topic))
        else:
            if self.topics is not None:
                raise PageError(f"{where}: only a stream route declares topics")
            if self.returns is None:
                raise PageError(f"{where}: a route declares returns")
            returns = _frozen_map(self.returns, f"{where}: returns", out)
            object.__setattr__(self, "returns", returns)
            object.__setattr__(self, "shape", Out("table", items=returns))

    @property
    def method(self) -> str:
        """GET for a read, POST for an act."""
        return _METHODS[self.kind]


def parse_size(text: str) -> int:
    """A size in bytes: digits with an optional `K`, `M` or `G`, powers of 1024.

    The same spelling the core's announcement uses for a size. Raises `ValueError` on
    anything else.
    """
    found = _SIZE.match(text) if isinstance(text, str) else None
    if not found:
        raise ValueError(f"a size is digits with an optional K, M or G, not {text!r}")
    return int(found[1]) * {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}[found[2]]


def _budget(value: object) -> Mapping[str, Any]:
    if value is None:
        return BUDGET
    if not isinstance(value, Mapping):
        raise PageError("a page's budget is a mapping with rss and fds")
    unknown = sorted(str(key) for key in value if key not in BUDGET)
    if unknown:
        raise PageError(f"a page's budget takes rss and fds only, not {', '.join(unknown)}")
    filled = {**BUDGET, **value}
    try:
        parse_size(filled["rss"])
    except ValueError:
        raise PageError("a page's budget rss is a size: digits with an optional K, M or G") from None
    if not _is_int(filled["fds"]) or filled["fds"] <= 0:
        raise PageError("a page's budget fds is an integer above 0")
    return MappingProxyType(filled)


@dataclass(frozen=True)
class Page:
    """A page server's whole declaration: its routes, assets, health check, idle time
    and budget.

    `assets` names one flat resource directory in the page module's package. `package`
    is that package; when it is left out, `Page` takes the `__package__` of the module
    whose code builds it. `health` is a `"pkg.mod:fn"` target returning `(ok, detail)`.
    `idle_s` is 5 or more. `budget` is `{"rss": <size>, "fds": <int>}` with either key
    optional; a key left out takes its default, `{"rss": "120M", "fds": 64}`. The base
    never reads the budget: `export` carries it for whoever samples the process.
    """

    routes: tuple[Route, ...]
    assets: str | None = None
    health: str | None = None
    idle_s: float = _IDLE_S
    budget: Mapping[str, Any] | None = None
    _: KW_ONLY
    package: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.routes, (str, bytes)) or not isinstance(self.routes, Iterable):
            raise PageError("a page's routes is a sequence of Route")
        routes = tuple(self.routes)
        object.__setattr__(self, "routes", routes)
        paths: set[str] = set()
        streams = 0
        for route in routes:
            if not isinstance(route, Route):
                raise PageError("a page's routes holds only Route values")
            if route.path in paths:
                raise PageError(f"a page declares {route.path} twice")
            paths.add(route.path)
            streams += route.stream
        if streams > 1:
            raise PageError("a page has at most one stream route")
        _check_status_routes(routes)
        if self.assets is not None and (
            not isinstance(self.assets, str) or not _ASSET_DIR.match(self.assets)
        ):
            raise PageError("a page's assets names one directory in its package")
        if self.health is not None and (
            not isinstance(self.health, str) or not _TARGET.match(self.health)
        ):
            raise PageError("a page's health is a 'pkg.mod:fn' target")
        if not _is_number(self.idle_s) or not math.isfinite(self.idle_s) or self.idle_s < _IDLE_MIN:
            raise PageError(f"a page's idle_s is {_IDLE_MIN} seconds or more")
        object.__setattr__(self, "budget", _budget(self.budget))
        package = self.package
        if package is None and self.assets is not None:
            # Frames: this method, the dataclass __init__, then the code building the Page.
            package = sys._getframe(2).f_globals.get("__package__") or None
        if package is not None and (not isinstance(package, str) or not _PACKAGE.match(package)):
            raise PageError(f"a page's package is a dotted package name, not {package!r}")
        object.__setattr__(self, "package", package)

    @property
    def stream_route(self) -> Route | None:
        """The page's one stream route, or None."""
        return next((route for route in self.routes if route.stream), None)


def _check_status_routes(routes: tuple[Route, ...]) -> None:
    """Each confirmed act's status route is a plain read of this page that takes a string
    `job` query key, and the gateway reaches it whenever the gateway reaches the act, so a
    phone that started an act can read how it went. A desk-only act may name a status
    route of either reach."""
    by_path = {route.path: route for route in routes}
    for route in routes:
        if not route.confirmed:
            continue
        where = f"route {route.path}"
        target = by_path.get(route.status)
        if target is None:
            raise PageError(f"{where}: its status route {route.status} is not on this page")
        if target.kind != "read" or target.stream:
            raise PageError(f"{where}: its status route {route.status} is not a plain read")
        job = target.query.get("job")
        if job is None or job.type != "string":
            raise PageError(f"{where}: its status route {route.status} takes a string job "
                            "query key")
        if route.gateway and not target.gateway:
            raise PageError(f"{where}: the gateway reaches this act, so it must reach its "
                            f"status route {route.status} too")


# --- the table ----------------------------------------------------------------------


def _entry_problem(method: object, raw_path: object, kind: object, stream: object) -> str | None:
    if kind not in _ENTRY_KINDS:
        return f"an entry's kind is one of {', '.join(_ENTRY_KINDS)}, not {kind!r}"
    if method not in ("GET", "POST"):
        return f"an entry's method is GET or POST, not {method!r}"
    if kind in _METHODS and method != _METHODS[kind]:
        return f"a {kind} entry's method is {_METHODS[kind]}"
    if kind == "asset" and method != "GET":
        return "an asset entry's method is GET"
    if not isinstance(raw_path, str) or not _ENTRY_PATH.match(raw_path):
        return f"an entry's path is literal segments, not {raw_path!r}"
    if not isinstance(stream, bool) or (stream and kind != "read"):
        return "only a read entry streams"
    return None


@dataclass(frozen=True)
class Entry:
    """One row of a `Table`: a method and a literal raw path, and what is there.

    `kind` is `read`, `act`, `asset` or `builtin`. `stream` marks a read that answers
    with server-sent events.
    """

    method: str
    raw_path: str
    kind: str
    stream: bool = False

    def __post_init__(self) -> None:
        problem = _entry_problem(self.method, self.raw_path, self.kind, self.stream)
        if problem:
            raise ValueError(problem)


class Table:
    """The entries the request check looks a request up in: `(method, raw path)` to
    `Entry`. Anything not in it is the empty 404.

    A table is filled with `add` and `mount`, then frozen by building a profile from it;
    `add` or `mount` on a frozen table raises `ValueError`. A path is matched exactly on
    the raw path as received, never on a decoded one.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], Entry] = {}
        self._frozen = False

    def __repr__(self) -> str:
        state = "frozen" if self._frozen else "open"
        return f"<Table {len(self._entries)} entries, {state}>"

    def __iter__(self):
        return iter(tuple(self._entries.values()))

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def frozen(self) -> bool:
        return self._frozen

    def freeze(self) -> Table:
        """Refuse every later `add` and `mount`. Returns the table."""
        self._frozen = True
        return self

    def _put(self, entries: Iterable[Entry]) -> None:
        if self._frozen:
            raise ValueError("this table is frozen into a profile; build a new one")
        new: dict[tuple[str, str], Entry] = {}
        for entry in entries:
            key = (entry.method, entry.raw_path)
            if key in self._entries or key in new:
                raise ValueError(f"the table already holds {entry.method} {entry.raw_path}")
            new[key] = entry
        self._entries.update(new)

    def add(self, method: str, raw_path: str, kind: str = "builtin", stream: bool = False) -> Entry:
        """Add one literal entry and return it."""
        entry = Entry(method, raw_path, kind, stream)
        self._put([entry])
        return entry

    def mount(self, prefix: str, export: Mapping[str, Any]) -> None:
        """Add a page's `export` under `prefix`, such as `/m/usage`.

        Each route becomes an entry at `prefix + path`, each asset one at
        `prefix + "/" + name`, and `index.html`, when present, one at `prefix + "/"`.
        All or nothing: an export it refuses adds no entry.
        """
        if not isinstance(prefix, str) or not _PREFIX.match(prefix):
            raise ValueError(f"a mount prefix is literal segments with no trailing /, not {prefix!r}")
        self._put(_export_entries(prefix, export))

    def find(self, method: str, raw_path: bytes | str) -> Entry | None:
        """The entry for this method and raw path, or None."""
        if isinstance(raw_path, bytes):
            try:
                raw_path = raw_path.decode("ascii")
            except UnicodeDecodeError:
                return None
        return self._entries.get((method, raw_path))


def _export_entries(prefix: str, data: Mapping[str, Any]) -> list[Entry]:
    if not isinstance(data, Mapping) or data.get("format") != FORMAT:
        raise ValueError(f"an export to mount is a mapping with format {FORMAT}")
    routes, assets = data.get("routes"), data.get("assets")
    if not isinstance(routes, list) or not isinstance(assets, list):
        raise ValueError("an export holds a routes list and an assets list")
    entries = []
    for route in routes:
        if not isinstance(route, Mapping):
            raise ValueError("an export's route is a mapping")
        path, kind, stream = route.get("path"), route.get("kind"), route.get("stream", False)
        if not isinstance(path, str) or not _PATH.match(path) or kind not in _METHODS:
            raise ValueError("an export's route has a literal path and a read or act kind")
        entries.append(Entry(_METHODS[kind], prefix + path, kind, stream))
    for name in assets:
        if not isinstance(name, str) or not _ASSET_NAME.match(name):
            raise ValueError("an export's asset is a plain file name")
        entries.append(Entry("GET", f"{prefix}/{name}", "asset"))
        if name == "index.html":
            entries.append(Entry("GET", f"{prefix}/", "asset"))
    return entries


def list_assets(page: Page) -> tuple[str, ...]:
    """The file names in the page's asset directory, sorted.

    Raises `PageError` when the directory is missing or holds a subdirectory, a name
    outside `[A-Za-z0-9][A-Za-z0-9._-]*`, or an extension outside `CONTENT_TYPES`.
    """
    if page.assets is None:
        return ()
    if page.package is None:
        raise PageError("a page with assets needs its package; pass package= to Page")
    try:
        root = importlib.resources.files(page.package).joinpath(page.assets)
    except (ImportError, TypeError, ValueError) as err:
        raise PageError(f"the assets package {page.package} cannot be read: {err}") from None
    if not root.is_dir():
        raise PageError(f"the asset directory {page.assets} is not in {page.package}")
    names = []
    for item in root.iterdir():
        name = item.name
        if item.is_dir():
            raise PageError(f"the asset directory {page.assets} holds a subdirectory, {name}")
        if not item.is_file():
            # A dangling link or a special file would pass here and break the server's
            # start, so the export and the runner refuse it alike.
            raise PageError(f"the asset {name} is not a file")
        if not _ASSET_NAME.match(name):
            raise PageError(f"the asset name {name!r} is not a plain file name")
        suffix = name[name.rfind("."):] if "." in name else ""
        if suffix not in CONTENT_TYPES:
            raise PageError(f"the asset {name} has a file type the base does not serve")
        names.append(name)
    return tuple(sorted(names))


def build_table(page: Page) -> tuple[Table, tuple[str, ...]]:
    """The table a page's server serves, and its asset names.

    The table holds the page's routes, the built-ins `/api/health` and `/api/prove`, and
    one asset entry per file (`/` too, for `index.html`). It is not frozen. The runner and
    the export command both build through this one function, so the export the shell
    mounts and the table the server serves agree. Raises `PageError` for a refused asset
    table or an asset name that collides with a route.
    """
    if not isinstance(page, Page):
        raise PageError("build_table takes a Page")
    names = list_assets(page)
    entries = [Entry(route.method, route.path, route.kind, route.stream) for route in page.routes]
    entries.append(Entry("GET", HEALTH_PATH, "builtin"))
    entries.append(Entry("GET", PROVE_PATH, "builtin"))
    for name in names:
        entries.append(Entry("GET", f"/{name}", "asset"))
        if name == "index.html":
            entries.append(Entry("GET", "/", "asset"))
    table = Table()
    try:
        table._put(entries)
    except ValueError as err:
        raise PageError(f"an asset collides with a route: {err}") from None
    return table, names


# --- export -------------------------------------------------------------------------


def _field_data(item: Field) -> dict[str, Any]:
    items: Any = None
    if isinstance(item.items, Field):
        items = _field_data(item.items)
    elif item.items is not None:
        items = {key: _field_data(sub) for key, sub in item.items.items()}
    return {"type": item.type, "required": item.required, "values": list(item.values),
            "max": item.max, "items": items}


def _out_data(item: Out) -> dict[str, Any]:
    items: Any = None
    if isinstance(item.items, Out):
        items = _out_data(item.items)
    elif item.items is not None:
        items = {key: _out_data(sub) for key, sub in item.items.items()}
    return {"type": item.type, "mark": item.mark, "nullable": item.nullable, "items": items}


def _route_data(route: Route) -> dict[str, Any]:
    return {
        "path": route.path,
        "kind": route.kind,
        "stream": route.stream,
        "gateway": route.gateway,
        "query": {key: _field_data(item) for key, item in route.query.items()},
        "body": {key: _field_data(item) for key, item in route.body.items()},
        "returns": None if route.returns is None
        else {key: _out_data(item) for key, item in route.returns.items()},
        "topics": None if route.topics is None
        else {name: _out_data(item) for name, item in route.topics.items()},
        "body_cap": route.body_cap,
        "confirmed": route.confirmed,
        "status": route.status,
    }


def export(page: Page) -> dict[str, Any]:
    """The page's table as JSON-able data: `{"format": 1, "routes": [...], "assets":
    [...], "budget": {...}}`.

    Each route carries its kind, its declared fields and shapes, its topics, and whether
    it is a confirmed act with the path of its status route (`confirmed`, `status`). The
    asset names come from `build_table`, and the budget has its defaults filled in.
    Handler targets and the built-ins `/api/health` and `/api/prove` are left out, so no
    browser reaches a built-in through the shell. Raises `PageError` as `build_table`
    does.
    """
    _, names = build_table(page)
    return {
        "format": FORMAT,
        "routes": [_route_data(route) for route in page.routes],
        "assets": list(names),
        "budget": dict(page.budget),
    }


# --- the allowlist ------------------------------------------------------------------


def _key_name(key: str) -> str:
    # A key the page did not declare is named only when it is a plain key name, so a 400
    # never repeats arbitrary text a client sent.
    return key if _KEY.match(key) else "(a key that is not a plain name)"


def parse_query(route: Route, query_string: bytes | str) -> dict[str, Any]:
    """The route's query keys, checked and parsed, from the raw query string.

    An `int` is `-?[0-9]{1,18}`, a `bool` is `true` or `false`, an `enum` is one of its
    values exactly, and a `string` is taken as sent. `max` caps an `int` and a
    `string`'s length. An unknown, repeated, missing or mistyped key raises `BadRequest`
    naming the key. On a stream route the `topics` key is left for the stream's own
    reader and is not in the result.
    """
    if isinstance(query_string, bytes):
        try:
            query_string = query_string.decode("ascii")
        except UnicodeDecodeError:
            raise BadRequest(None, "the query string is not ASCII") from None
    try:
        pairs = parse_qsl(query_string, keep_blank_values=True, strict_parsing=bool(query_string),
                          errors="strict")
    except (ValueError, UnicodeDecodeError):
        raise BadRequest(None, "the query string is malformed") from None
    out: dict[str, Any] = {}
    seen: set[str] = set()
    for key, raw in pairs:
        if key in seen:
            raise BadRequest(_key_name(key), "repeated key")
        seen.add(key)
        if route.stream and key == "topics":
            continue
        item = route.query.get(key)
        if item is None:
            raise BadRequest(_key_name(key), "unknown key")
        out[key] = _query_value(key, raw, item)
    _require(route.query, out, "")
    return out


def _query_value(key: str, raw: str, item: Field) -> Any:
    if item.type == "int":
        if not _QUERY_INT.match(raw):
            raise BadRequest(key, "not an integer")
        value: Any = int(raw)
        if item.max is not None and value > item.max:
            raise BadRequest(key, "over its max")
        return value
    if item.type == "bool":
        if raw not in ("true", "false"):
            raise BadRequest(key, "not true or false")
        return raw == "true"
    if item.type == "enum":
        if raw not in item.values:
            raise BadRequest(key, "not one of its values")
        return raw
    if item.max is not None and len(raw) > item.max:
        raise BadRequest(key, "over its max length")
    return raw


def _require(fields: Mapping[str, Field], got: Mapping[str, Any], path: str) -> None:
    for key, item in fields.items():
        if item.required and key not in got:
            raise BadRequest(_join(path, key), "missing key")


def _no_repeats(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise BadRequest(_key_name(key), "a key appears twice in one object")
        out[key] = value
    return out


def _no_constant(name: str) -> Any:
    raise BadRequest(None, "the body is not JSON")


def parse_body(route: Route, raw: bytes) -> dict[str, Any]:
    """The act's body, decoded from UTF-8 JSON and checked against its fields.

    The body is one JSON object. The JSON type must match: `true` is a `bool` and never
    an `int`, and an integer is taken as a `float` where one is declared. Each list item
    is checked against its item `Field` and each table's keys against its map, at every
    depth. Raises `BadRequest` naming the dotted path of the key at fault; a list item's
    path ends in its index.
    """
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_repeats,
                          parse_constant=_no_constant)
    except (UnicodeDecodeError, ValueError) as err:
        if isinstance(err, BadRequest):
            raise
        raise BadRequest(None, "the body is not JSON") from None
    if not isinstance(data, dict):
        raise BadRequest(None, "the body is not a JSON object")
    return _table_value(data, route.body, "")


def _table_value(data: dict[str, Any], fields: Mapping[str, Field], path: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        item = fields.get(key)
        if item is None:
            raise BadRequest(_join(path, _key_name(key)), "unknown key")
        out[key] = _body_value(value, item, _join(path, key))
    _require(fields, out, path)
    return out


def _body_value(value: Any, item: Field, path: str) -> Any:
    kind = item.type
    if kind == "string":
        if not isinstance(value, str):
            raise BadRequest(path, "not a string")
        if item.max is not None and len(value) > item.max:
            raise BadRequest(path, "over its max length")
        return value
    if kind == "int":
        if not _is_int(value):
            raise BadRequest(path, "not an integer")
        if item.max is not None and value > item.max:
            raise BadRequest(path, "over its max")
        return value
    if kind == "float":
        if not _is_number(value):
            raise BadRequest(path, "not a number")
        try:
            number = float(value)
        except OverflowError:
            raise BadRequest(path, "not a finite number") from None
        if not math.isfinite(number):
            # JSON's 1e400 parses to inf, which projection could never send back.
            raise BadRequest(path, "not a finite number")
        if item.max is not None and number > item.max:
            raise BadRequest(path, "over its max")
        return number
    if kind == "bool":
        if not isinstance(value, bool):
            raise BadRequest(path, "not true or false")
        return value
    if kind == "enum":
        if not isinstance(value, str) or value not in item.values:
            raise BadRequest(path, "not one of its values")
        return value
    if kind == "list":
        if not isinstance(value, list):
            raise BadRequest(path, "not a list")
        if item.max is not None and len(value) > item.max:
            raise BadRequest(path, "over its max items")
        return [_body_value(sub, item.items, _join(path, str(n))) for n, sub in enumerate(value)]
    if not isinstance(value, dict):
        raise BadRequest(path, "not a table")
    return _table_value(value, item.items, path)


# --- projection ---------------------------------------------------------------------


def project(
    value: Any,
    shape: Out,
    *,
    strict: bool = False,
    dropped: Callable[[str], None] | None = None,
) -> Any:
    """`value` cut to `shape` at every depth, ready to send as JSON.

    A table key the shape does not declare is dropped: `dropped` is called with its
    dotted path, or, when `strict` (the testing helper), `ProjectionError` is raised. A
    value of the wrong JSON type, a non-finite float, or a `null` where the shape is not
    nullable raises `ProjectionError` whether strict or not. A declared nullable value
    that is `null` is kept as `null`. A declared key the value lacks stays absent.
    """
    return _cut(value, shape, "", strict, dropped)


def _cut(value: Any, shape: Out, path: str, strict: bool, dropped: Callable[[str], None] | None) -> Any:
    if value is None:
        if shape.nullable:
            return None
        raise ProjectionError(path, "null for a value not declared nullable")
    kind = shape.type
    if kind == "string":
        ok = isinstance(value, str)
    elif kind == "int":
        ok = _is_int(value)
    elif kind == "float":
        ok = _is_number(value) and math.isfinite(value)
    elif kind == "bool":
        ok = isinstance(value, bool)
    elif kind == "list":
        if not isinstance(value, (list, tuple)):
            raise ProjectionError(path, "not a list")
        return [_cut(sub, shape.items, _join(path, str(n)), strict, dropped)
                for n, sub in enumerate(value)]
    else:
        if not isinstance(value, Mapping):
            raise ProjectionError(path, "not a table")
        out = {}
        for key, sub in value.items():
            where = _join(path, str(key))
            declared = shape.items.get(key) if isinstance(key, str) else None
            if declared is None:
                if strict:
                    raise ProjectionError(where, "a key the shape does not declare")
                if dropped is not None:
                    dropped(where)
                continue
            out[key] = _cut(sub, declared, where, strict, dropped)
        return out
    if not ok:
        raise ProjectionError(path, f"not a {'finite ' if kind == 'float' else ''}{kind}")
    return value
