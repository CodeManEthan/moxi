"""The module snapshot: which modules this environment holds, read without importing them.

A refresh runs two commands in fresh processes, `[python(), "-P", "-m", ...]` with the
shell's runtime dir as their working directory, so the shell imports no module's code and
an upgraded module is read as it now is:

1. `moxi modules --json`, every module's announcement;
2. `moxi_ui.loopback.export <module>...` for every present module that declares `PAGE`
   (the shell's own page is served in process and exported in process).

From the two the shell builds a new route table, gives the starters each module's
version (a running server of another version is drained), gives the push each module's
topics, and publishes the modules view's value as `ui.modules`. The table is used from the
next request on.

A refresh runs at the shell's start, and again when a directory modules are installed in
changes its mtime: the environment's site-packages and each `PYTHONPATH` entry the
children see. That is looked at on a request that needs the snapshot, at most every 2 s,
and the request goes on with the table it found; it never waits for the refresh, except
for the first one after the shell starts. A refresh whose listing or any export failed
(a command that timed out at a busy logon, a module caught half-written by an upgrade) is
run again on such a look, after 2 s and then twice as long each time, up to 60 s, until
one succeeds.

A module's components (`COMPONENTS`) are checked here, every rule of the contract. A bad
one is not placed; the modules view lists it with the reason. A module whose export
fails is listed with the reason and gets no mount.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sysconfig
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from moxi import proc
from moxi.names import is_module_name
from moxi.paths import python

from .gateway.shapes import gateway_problem
from .starter import Exports, export_tables

__all__ = [
    "CONTRACTS",
    "EVERY",
    "FIRST_WAIT",
    "MODULES_TIMEOUT",
    "OWN_MODULE",
    "RETRY_MAX",
    "ModuleSnapshot",
    "View",
    "budget_of",
    "check_components",
    "confirmed_paths",
    "modules_argv",
    "modules_value",
    "read_modules",
    "watched_dirs",
]

#: The component contract versions this shell places.
CONTRACTS = frozenset({1})
#: The fewest seconds between two looks at the watched directories.
EVERY = 2.0
#: The longest wait, in seconds, before a failed refresh is run again.
RETRY_MAX = 60.0
#: Seconds `moxi modules --json` may run.
MODULES_TIMEOUT = 10.0
#: Seconds a request that needs the snapshot waits for the first one after the start.
FIRST_WAIT = 15.0
#: The shell's own module, whose page is served in process.
OWN_MODULE = "ui"

_NAME = re.compile(r"\A[a-z0-9]+(-[a-z0-9]+)*\Z")
_ATTRIBUTE = re.compile(r"\A[a-z][a-z0-9]*(-[a-z0-9]+)*\Z")
_PLACES = ("page", "block", "inline")
_ATTRIBUTE_TYPES = ("string", "int", "bool", "enum")
_REASON_CAP = 500
_log = logging.getLogger("moxi.ui.snapshot")


# --- the components ---------------------------------------------------------------------


def _one_line(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\n" not in value and "\r" not in value


def _attribute(name: object, spec: object) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(name, str) or not _ATTRIBUTE.match(name):
        return None, f"its attribute {name!r} is not a lower-case attribute name"
    if not isinstance(spec, Mapping):
        return None, f"its attribute {name} is not a table"
    kind = spec.get("type")
    if kind not in _ATTRIBUTE_TYPES:
        return None, f"its attribute {name} has type {kind!r}, not one of {', '.join(_ATTRIBUTE_TYPES)}"
    values = spec.get("values", [])
    if kind == "enum":
        if (not isinstance(values, (list, tuple)) or not values
                or not all(isinstance(v, str) and v for v in values)
                or len(set(values)) != len(values)):
            return None, f"its enum attribute {name} needs values, each a different word"
    elif values not in ([], (), None):
        return None, f"its attribute {name} has values, which only an enum takes"
    required = spec.get("required", False)
    if not isinstance(required, bool):
        return None, f"its attribute {name} has required = {required!r}, not true or false"
    doc = spec.get("doc", "")
    if doc != "" and not _one_line(doc):
        return None, f"its attribute {name} has a doc that is not one line"
    return {"name": name, "type": kind, "values": list(values or ()), "required": required,
            "doc": doc}, None


def _component(module: str, name: object, decl: object,
               assets: Sequence[str] | None) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(name, str) or not _NAME.match(name):
        return None, "its name is not lower-case words joined by hyphens"
    if not isinstance(decl, Mapping):
        return None, "it is not a table"
    contract = decl.get("contract")
    if not isinstance(contract, int) or isinstance(contract, bool):
        return None, f"its contract is {contract!r}, not a version number"
    if contract not in CONTRACTS:
        newest = max(CONTRACTS)
        if contract > newest:
            return None, (f"it is written for contract {contract}, and this shell places "
                          f"contract {newest}; upgrade moxi-ui")
        return None, (f"it is written for contract {contract}, which this shell no longer "
                      f"places; upgrade moxi-{module}")
    script = decl.get("script")
    if not isinstance(script, str) or not script:
        return None, "it names no script"
    if assets is None:
        return None, "its module's page has no export, so its script can't be served"
    if script not in assets:
        return None, f"its script {script} is not an asset of its module's page"
    for key in ("title", "doc"):
        if not _one_line(decl.get(key)):
            return None, f"its {key} is not one line"
    place = decl.get("place")
    if place not in _PLACES:
        return None, f"its place is {place!r}, not one of {', '.join(_PLACES)}"
    if (place == "page") != (name == "page"):
        return None, "only the component named page takes the place page, and it takes only that"
    attributes = decl.get("attributes", {})
    if not isinstance(attributes, Mapping):
        return None, "its attributes is not a table"
    checked = []
    for attr, spec in attributes.items():
        item, reason = _attribute(attr, spec)
        if reason is not None:
            return None, reason
        checked.append(item)
    home = decl.get("home", False)
    if not isinstance(home, bool):
        return None, f"its home is {home!r}, not true or false"
    if home and any(item["required"] for item in checked):
        return None, "it asks to be placed at home but has a required attribute"
    return {
        "name": name,
        "element": f"moxi-{module}-{name}",
        "script": script,
        "url": f"/m/{module}/{script}",
        "title": decl["title"],
        "doc": decl["doc"],
        "contract": contract,
        "place": place,
        "home": home,
        "attributes": checked,
    }, None


def check_components(module: str, components: object, *, page: bool,
                     assets: Sequence[str] | None
                     ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Module `module`'s `COMPONENTS` checked: `(placed, refused)`.

    `page` says whether the module declares `PAGE`; `assets` are its page's asset names
    from its export, or None when it has no export. Each placed component carries its
    element name and its script's URL; each refused one, `{"name", "reason"}`. A field
    this shell doesn't know is ignored, since a newer contract 1 may add optional ones.
    """
    placed: list[dict[str, Any]] = []
    refused: list[dict[str, str]] = []
    if not isinstance(components, Mapping):
        return placed, refused
    for name, decl in sorted(components.items(), key=lambda item: str(item[0])):
        if not page:
            item, reason = None, "its module declares no PAGE, which would serve its script"
        else:
            item, reason = _component(module, name, decl, assets)
        if reason is not None:
            refused.append({"name": str(name)[:64], "reason": reason})
        else:
            placed.append(item)
    return placed, refused


# --- the modules view's value -----------------------------------------------------------


def _text(value: object) -> str:
    return value[:_REASON_CAP] if isinstance(value, str) else ""


def budget_of(export: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """A page export's budget, `{"rss": <size>, "fds": <int>}`, or None when it has none
    of that shape."""
    budget = export.get("budget") if isinstance(export, Mapping) else None
    if not isinstance(budget, Mapping):
        return None
    rss, fds = budget.get("rss"), budget.get("fds")
    if not isinstance(rss, str) or not isinstance(fds, int) or isinstance(fds, bool):
        return None
    return {"rss": rss, "fds": fds}


def confirmed_paths(export: Mapping[str, Any] | None) -> list[str]:
    """The paths of a page export's confirmed acts, sorted. `client.js` reads them so that
    `api()` refuses such a path and `confirm()` takes only such a path."""
    routes = export.get("routes") if isinstance(export, Mapping) else None
    if not isinstance(routes, Sequence):
        return []
    return sorted({route["path"] for route in routes
                   if isinstance(route, Mapping) and route.get("kind") == "act"
                   and route.get("confirmed") is True and isinstance(route.get("path"), str)})


def modules_value(listing: Sequence[Mapping[str, Any]], mounts: Mapping[str, Mapping[str, Any]],
                  errors: Mapping[str, str], *, at: str | None = None,
                  error: str | None = None) -> dict[str, Any]:
    """The modules view's value, `ui.modules` and `/m/ui/api/modules`: every module of
    `listing` (`moxi modules --json`'s), what its page export gave (`mounts`, `errors`)
    and its components as checked. Built key by key to the route's declared shape."""
    modules = []
    for item in listing:
        name = item["name"]
        page = bool(item.get("page"))
        export = mounts.get(name)
        assets = list(export.get("assets") or ()) if export is not None else None
        placed, refused = check_components(name, item.get("components") or {}, page=page,
                                           assets=assets)
        modules.append({
            "name": name,
            "state": _text(item.get("state")),
            "reason": _text(item.get("reason")),
            "version": _text(item.get("version")),
            "summary": _text(item.get("summary")),
            "page": page,
            "mounted": export is not None,
            "export_error": _text(errors[name]) if name in errors else None,
            "gateway_error": gateway_problem(export) if export is not None else None,
            "budget": budget_of(export),
            "confirmed": confirmed_paths(export),
            "components": placed,
            "refused": refused,
        })
    return {"at": at, "error": error, "contracts": sorted(CONTRACTS), "modules": modules}


# --- reading the modules ----------------------------------------------------------------


def modules_argv() -> list[str]:
    """`moxi modules --json`, in the spawn rule's form."""
    return [str(python()), "-P", "-m", "moxi", "modules", "--json"]


def read_modules(*, cwd: str | os.PathLike, env: Mapping[str, str] | None = None,
                 timeout: float = MODULES_TIMEOUT,
                 run: Callable[..., subprocess.CompletedProcess] = proc.run
                 ) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Every module `moxi modules --json` lists, run in a fresh process from `cwd`:
    `(modules, None)`, each with a valid module name, or `(None, reason)`. Blocking."""
    try:
        done = run(modules_argv(), timeout_s=timeout, cwd=str(cwd), env=env)
    except subprocess.TimeoutExpired:
        return None, f"moxi modules ran past {timeout:g} s"
    except OSError as err:
        return None, f"moxi modules could not run: {err}"
    try:
        data = json.loads(done.stdout)
        listed = data["modules"]
        if not isinstance(listed, list):
            raise TypeError("not a list")
    except (ValueError, KeyError, TypeError):
        return None, f"moxi modules exited {done.returncode} with no answer"
    found = [item for item in listed if isinstance(item, dict)
             and isinstance(item.get("name"), str) and is_module_name(item["name"])]
    return sorted(found, key=lambda item: item["name"]), None


def watched_dirs(env: Mapping[str, str] | None = None) -> list[str]:
    """The directories whose mtime starts a refresh: this environment's site-packages
    (pure and platform) and each `PYTHONPATH` entry of `env` (the children's
    environment), each once."""
    paths = sysconfig.get_paths()
    found: list[str] = []
    entries = [paths.get("purelib"), paths.get("platlib")]
    pythonpath = (env if env is not None else os.environ).get("PYTHONPATH", "")
    entries += pythonpath.split(os.pathsep) if pythonpath else []
    for entry in entries:
        if entry and entry not in found:
            found.append(entry)
    return found


def _mtimes(dirs: Iterable[str]) -> tuple[float | None, ...]:
    out = []
    for directory in dirs:
        try:
            out.append(os.stat(directory).st_mtime)
        except OSError:
            out.append(None)
    return tuple(out)


# --- the snapshot -----------------------------------------------------------------------


@dataclass(frozen=True)
class View:
    """One refresh's result.

    `listing` is every module `moxi modules --json` gave, less those left out; `pages`
    the present modules with `PAGE` other than the shell's own, each with a door in the
    table; `exports` their exports and export errors; `versions` module to version, for
    the starters; `value` the modules view's value; `error` why `moxi modules` failed, in
    which case the rest is the previous view's.
    """

    listing: tuple[dict[str, Any], ...] = ()
    pages: tuple[str, ...] = ()
    exports: Exports = field(default_factory=Exports)
    versions: dict[str, str] = field(default_factory=dict)
    value: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _now_text() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class ModuleSnapshot:
    """The shell's module snapshot. See the module docstring.

    `cwd` is the shell's runtime dir, the commands' working directory; `env` gives the
    environment they run in (the children's, a callable read at each refresh, or None for
    this process's). `own` is the shell's own page export, used to check its own
    components. `leave_out` names installed modules the snapshot leaves out (test shells
    only). `on_view(view)` runs on the loop after each refresh. `dirs`, `every`, `clock`
    and `run` are seams for tests.

    Everything but the commands runs on the shell's loop; the commands run on a worker
    thread.
    """

    def __init__(self, *, cwd: str | os.PathLike,
                 env: Mapping[str, str] | Callable[[], Mapping[str, str]] | None = None,
                 own: Mapping[str, Any] | None = None, leave_out: Iterable[str] = (),
                 on_view: Callable[[View], Any] | None = None,
                 dirs: Sequence[str] | None = None, every: float = EVERY,
                 clock: Callable[[], float] = time.monotonic,
                 run: Callable[..., subprocess.CompletedProcess] = proc.run):
        self.cwd = cwd
        self._env = env
        self.own = dict(own) if own is not None else None
        self.leave_out = frozenset(leave_out)
        self.on_view = on_view
        self.every = every
        self.clock = clock
        self.run = run
        self._dirs = list(dirs) if dirs is not None else None
        self.current: View | None = None
        self.refreshes = 0
        self._marks: tuple[float | None, ...] | None = None
        self._looked: float | None = None
        self._retry_at: float | None = None
        self._retry_wait = 0.0
        self._task: asyncio.Task[None] | None = None
        self._again = False
        self._ready: asyncio.Event | None = None
        self._closed = False

    # -- seams the shell calls -----------------------------------------------------------

    def env(self) -> Mapping[str, str] | None:
        """The environment the commands run in."""
        return self._env() if callable(self._env) else self._env

    def dirs(self) -> list[str]:
        """The watched directories."""
        return self._dirs if self._dirs is not None else watched_dirs(self.env())

    def start(self) -> None:
        """Start the first refresh. Call on the shell's loop."""
        self._event()
        self.request()

    def request(self) -> None:
        """Refresh now, or once more after the refresh that is running."""
        if self._closed:
            return
        if self._task is not None and not self._task.done():
            self._again = True
            return
        self._task = asyncio.ensure_future(self._refreshing())

    def touch(self) -> None:
        """A request needs the snapshot: at most every `every` seconds, look at the
        watched directories, and refresh in the background when one changed or when the
        last refresh failed and its retry is due."""
        now = self.clock()
        if self._looked is not None and now - self._looked < self.every:
            return
        self._looked = now
        if self._marks is None:
            return
        if self._retry_at is not None and now >= self._retry_at:
            self._retry_at = None
            self.request()
        elif _mtimes(self.dirs()) != self._marks:
            self.request()

    async def ready(self, timeout: float = FIRST_WAIT) -> bool:
        """Wait for the first refresh, up to `timeout` seconds; whether it is done."""
        if self.current is not None:
            return True
        try:
            async with asyncio.timeout(timeout):
                await self._event().wait()
        except TimeoutError:
            return False
        return True

    async def aclose(self) -> None:
        """Stop refreshing; a command already running is left to its own time limit."""
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    # -- the refresh ---------------------------------------------------------------------

    def _event(self) -> asyncio.Event:
        if self._ready is None:
            self._ready = asyncio.Event()
        return self._ready

    async def _refreshing(self) -> None:
        from moxi import aio

        while True:
            self._again = False
            try:
                view = await aio.offload(self.read)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one failed refresh keeps the last view
                _log.exception("the module snapshot failed")
                view = None
            self._retry(failed=view is None or view.error is not None
                        or bool(view.exports.errors))
            if view is not None and not self._closed:
                self.current = view
                self.refreshes += 1
                if self.on_view is not None:
                    try:
                        self.on_view(view)
                    except Exception:  # noqa: BLE001 - a bad view keeps the table it had
                        _log.exception("taking the module snapshot failed")
            self._event().set()
            if not self._again or self._closed:
                return

    def _retry(self, *, failed: bool) -> None:
        """After a refresh: when it failed, the time the next look runs it again, each
        wait twice the last from `every` up to `RETRY_MAX`; when it succeeded, none."""
        if not failed:
            self._retry_at, self._retry_wait = None, 0.0
            return
        self._retry_wait = min(max(self._retry_wait * 2, self.every), RETRY_MAX)
        self._retry_at = self.clock() + self._retry_wait

    def read(self) -> View:
        """One refresh's commands and checks: blocking, so the shell runs it on a worker
        thread. The watched directories' mtimes are taken first, so a change made while
        the commands run starts another refresh."""
        env = self.env()
        self._marks = _mtimes(self.dirs())
        at = _now_text()
        listing, error = read_modules(cwd=self.cwd, env=env, run=self.run)
        previous = self.current
        if listing is None:
            _log.warning("the module snapshot kept its last view: %s", error)
            if previous is None:
                return View(value=modules_value((), {}, {}, at=at, error=error), error=error)
            value = dict(previous.value, error=error)
            return View(previous.listing, previous.pages, previous.exports, previous.versions,
                        value, error)
        listing = [item for item in listing if item["name"] not in self.leave_out]
        pages = tuple(item["name"] for item in listing
                      if item.get("state") == "present" and item.get("page")
                      and item["name"] != OWN_MODULE)

        def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
            return self.run(argv, env=env, **kwargs)

        exports = export_tables(pages, cwd=self.cwd, run=run)
        versions = {item["name"]: item["version"] for item in listing
                    if item["name"] in pages and isinstance(item.get("version"), str)
                    and item["version"]}
        mounts = dict(exports.mounts)
        if self.own is not None:
            mounts[OWN_MODULE] = self.own
        value = modules_value(listing, mounts, exports.errors, at=at)
        return View(tuple(listing), pages, exports, versions, value)
