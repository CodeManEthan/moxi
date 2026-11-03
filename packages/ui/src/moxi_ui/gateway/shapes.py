"""What the gateway reaches, and what it sends: its route table, the checks a module's
export must pass to be mounted there, an act's body check, and redaction.

**The table.** The gateway reaches a module route only when the module declares it with
`gateway=True`; nothing else is in its table, so any other path is the check's 404. Per
module it holds those routes under `/m/<module>/`, except a stream route and a confirmed
act's own path, and the module's assets when it has at least one such route. A stream
route is never an entry, whatever its flag: a direct read of it would reach the proxy,
which doesn't redact. Its `gateway` flag names the topics the push channel's gateway form
may ask for. A confirmed act is reached only through the gateway's prepare and commit.

**The export checks.** A route the gateway reaches that is an act must be a confirmed act,
and its status route must be one the gateway reaches. A module whose export breaks either
gets no gateway mount, and the modules view names the route. The checks are the same
whether the phone's acts are mounted or not.

**Redaction.** On the gateway, each 2xx JSON body from a route, each stream event and each
of the shell's own declared answers is walked against its declared shape at every depth,
and exactly the marked values change: `redact` becomes `null`, and `path` becomes a handle,
`@h/<12 hex>/<name>`, the hex an HMAC-SHA-256 of the path under the gateway's key and the
name the path's last segment, cut to 255 bytes. Nothing unmarked changes. A handle is for
display only: nothing maps one back to its path.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from moxi.names import is_module_name
from moxi_loopback.api import Out, Table

__all__ = [
    "BODY_LIMIT", "BadBody", "acts_of", "canonical", "check_body", "gateway_problem",
    "handle", "out_data", "redact", "redact_value", "route_of", "shape_data", "table",
]

#: The largest 2xx JSON body the gateway reads to redact; a larger one is a 502.
BODY_LIMIT = 1024 * 1024
_NAME_BYTES = 255
_SEGMENTS = re.compile(r"[/\\]")


# --- the export checks ----------------------------------------------------------------


def _routes(export: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    routes = export.get("routes") if isinstance(export, Mapping) else None
    return [route for route in routes or () if isinstance(route, Mapping)]


def gateway_problem(export: Mapping[str, Any] | None) -> str | None:
    """Why the gateway can't mount `export`, naming the route, or None. A gateway act must
    be confirmed and name a status route the gateway reaches."""
    routes = _routes(export)
    reads = {route.get("path"): route for route in routes if route.get("kind") == "read"}
    for route in routes:
        if route.get("gateway") is not True or route.get("kind") != "act":
            continue
        path = route.get("path")
        if route.get("confirmed") is not True:
            return f"the act {path} reaches the gateway but isn't a confirmed act"
        status = reads.get(route.get("status"))
        if status is None or status.get("gateway") is not True:
            return (f"the act {path} reaches the gateway but its status route "
                    f"{route.get('status')} doesn't")
    return None


def acts_of(export: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    """The confirmed acts of `export` the gateway reaches, path to route."""
    if gateway_problem(export) is not None:
        return {}
    return {route["path"]: route for route in _routes(export)
            if route.get("gateway") is True and route.get("kind") == "act"
            and route.get("confirmed") is True and isinstance(route.get("path"), str)}


# --- the table --------------------------------------------------------------------------


def table(mounts: Mapping[str, Mapping[str, Any]], *,
          entries: Iterable[tuple[str, str, str]] = (),
          problem: Callable[[str, str], None] | None = None) -> Table:
    """The gateway's table: `entries` (`(method, path, kind)` of its own), then for each
    module of `mounts` (module to export) that passes the export checks, its gateway reads
    under `/m/<module>/` and, when it has one, its assets. `problem(module, text)` hears of
    each module left out."""
    built = Table()
    for method, path, kind in entries:
        built.add(method, path, kind)
    for name, export in sorted(mounts.items()):
        if not is_module_name(name):
            continue
        refused = gateway_problem(export)
        if refused is not None:
            if problem is not None:
                problem(name, refused)
            continue
        reads = [route for route in _routes(export) if route.get("gateway") is True
                 and route.get("kind") == "read" and not route.get("stream")]
        if not reads:
            continue
        prefix = f"/m/{name}"
        for route in reads:
            built.add("GET", prefix + str(route["path"]), "read")
        for asset in export.get("assets") or ():
            if not isinstance(asset, str):
                continue
            if built.find("GET", f"{prefix}/{asset}") is None:
                built.add("GET", f"{prefix}/{asset}", "asset")
            if asset == "index.html" and built.find("GET", f"{prefix}/") is None:
                built.add("GET", f"{prefix}/", "asset")
    return built


def route_of(mounts: Mapping[str, Mapping[str, Any]], module: str, path: str
             ) -> Mapping[str, Any] | None:
    """The gateway read of `module` at `path` (the page's own path), or None."""
    for route in _routes(mounts.get(module)):
        if route.get("path") == path and route.get("gateway") is True:
            return route
    return None


# --- shapes as data -----------------------------------------------------------------------


def out_data(item: Out) -> dict[str, Any]:
    """An `Out` as the export writes it: type, mark, nullable and items."""
    items: Any = None
    if isinstance(item.items, Out):
        items = out_data(item.items)
    elif item.items is not None:
        items = {key: out_data(sub) for key, sub in item.items.items()}
    return {"type": item.type, "mark": item.mark, "nullable": item.nullable, "items": items}


def shape_data(shape: Mapping[str, Out]) -> dict[str, Any]:
    """A key map of `Out`s, such as a route's `returns`, as export data."""
    return {key: out_data(item) for key, item in shape.items()}


# --- redaction ------------------------------------------------------------------------------


def handle(path: str, key: bytes) -> str:
    """The handle of `path` under `key`: `@h/<12 hex>/<name>`."""
    digest = hmac.new(key, path.encode("utf-8", "surrogatepass"), hashlib.sha256).hexdigest()
    parts = [part for part in _SEGMENTS.split(path) if part]
    name = parts[-1] if parts else ""
    cut = name.encode("utf-8", "surrogatepass")[:_NAME_BYTES].decode("utf-8", "ignore")
    return f"@h/{digest[:12]}/{cut}"


def redact_value(value: Any, out: Mapping[str, Any] | None, key: bytes) -> Any:
    """`value` with every value its shape `out` (export data) marks changed."""
    if out is None or value is None:
        return value
    mark = out.get("mark")
    if mark == "redact":
        return None
    if mark == "path":
        return handle(value, key) if isinstance(value, str) else value
    items = out.get("items")
    if out.get("type") == "list" and isinstance(value, list) and isinstance(items, Mapping):
        return [redact_value(item, items, key) for item in value]
    if out.get("type") == "table" and isinstance(value, dict) and isinstance(items, Mapping):
        return redact(value, items, key)
    return value


def redact(value: Any, shape: Mapping[str, Any] | None, key: bytes) -> Any:
    """A JSON object `value` with the marked values of its key map `shape` (export data, a
    route's `returns`) changed. Anything that isn't an object passes as it is."""
    if not isinstance(value, dict) or not shape:
        return value
    return {name: redact_value(item, shape.get(name), key) for name, item in value.items()}


# --- an act's body --------------------------------------------------------------------------


class BadBody(ValueError):
    """An act's body that its declared fields refuse; the text names the key."""


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _plain(key: object) -> str:
    return key if isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", key) \
        else "(a key that is not a plain name)"


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _field(value: Any, item: Mapping[str, Any], path: str) -> Any:
    kind, cap = item.get("type"), item.get("max")
    if kind == "string":
        if not isinstance(value, str):
            raise BadBody(f"{path}: not a string")
        if cap is not None and len(value) > cap:
            raise BadBody(f"{path}: over its max length")
    elif kind == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            raise BadBody(f"{path}: not an integer")
        if cap is not None and value > cap:
            raise BadBody(f"{path}: over its max")
    elif kind == "float":
        if not _number(value) or not math.isfinite(float(value)):
            raise BadBody(f"{path}: not a finite number")
        if cap is not None and float(value) > cap:
            raise BadBody(f"{path}: over its max")
    elif kind == "bool":
        if not isinstance(value, bool):
            raise BadBody(f"{path}: not true or false")
    elif kind == "enum":
        if not isinstance(value, str) or value not in (item.get("values") or ()):
            raise BadBody(f"{path}: not one of its values")
    elif kind == "list":
        if not isinstance(value, list):
            raise BadBody(f"{path}: not a list")
        if cap is not None and len(value) > cap:
            raise BadBody(f"{path}: over its max items")
        inner = item.get("items")
        if not isinstance(inner, Mapping):
            raise BadBody(f"{path}: its items aren't declared")
        for index, sub in enumerate(value):
            _field(sub, inner, f"{path}.{index}")
    elif kind == "table":
        inner = item.get("items")
        if not isinstance(value, dict):
            raise BadBody(f"{path}: not an object")
        if not isinstance(inner, Mapping):
            raise BadBody(f"{path}: its keys aren't declared")
        _table(value, inner, path)
    else:
        raise BadBody(f"{path}: its type isn't one a body takes")
    return value


def _table(data: Mapping[str, Any], fields: Mapping[str, Any], path: str) -> None:
    for name, value in data.items():
        item = fields.get(name)
        if not isinstance(item, Mapping):
            raise BadBody(f"{_join(path, _plain(name))}: unknown key")
        _field(value, item, _join(path, name))
    for name, item in fields.items():
        if isinstance(item, Mapping) and item.get("required", True) and name not in data:
            raise BadBody(f"{_join(path, name)}: missing")


def check_body(body: Any, route: Mapping[str, Any]) -> None:
    """Check an act's `body` (decoded JSON) against `route`'s declared `body` fields and
    `body_cap`, as the module's own base would. Raises `BadBody`."""
    if not isinstance(body, dict):
        raise BadBody("the body is not a JSON object")
    fields = route.get("body") or {}
    if not isinstance(fields, Mapping):
        raise BadBody("the act's body isn't declared")
    _table(body, fields, "")
    cap = route.get("body_cap")
    if isinstance(cap, int) and len(canonical(body)) > cap:
        raise BadBody("the body is over the act's cap")


def canonical(body: Mapping[str, Any]) -> bytes:
    """`body` as the gateway stores and sends it: sorted keys, compact, UTF-8."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")
