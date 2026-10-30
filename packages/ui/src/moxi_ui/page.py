"""The shell's own page: its routes under `/m/ui/`, served in process.

    GET  /m/ui/api/servers   the page servers' rows, sampled now, and the shell's own
    POST /m/ui/api/stop      stop the shell
    GET  /m/ui/api/modules   the modules view's value (also the topic `ui.modules`)
    GET  /m/ui/api/doctor    `moxi doctor [module] --json`'s rows, run in a fresh process

`servers` and `stop` take the runtime file's token and nothing a browser sends (`moxi ui
status` and `stop`, and the doctor, call them); the others take the session's cookie and
key. `/m/ui/api/health` is loopback's built-in. The push channel, `/m/ui/api/push`, is the
router's, before this page.

The page is served by loopback's in-process `app`, which resolves each handler by name, so
the handlers find the shell they serve through `current()`, which `serving(shell, app)`
sets for each request.

The settings view's routes are declared by `settings.py` and joined here as
`SETTINGS_ROUTES`; its handlers reach the shell the same way.
"""

from __future__ import annotations

import contextvars
import json
import os
import subprocess
import time
from collections.abc import Awaitable, Callable
from typing import Any

from moxi_loopback.api import Field, Out, Page, Route

from . import settings as _settings

__all__ = [
    "DOCTOR_TIMEOUT",
    "MODULES_SHAPE",
    "PAGE",
    "ROUTES",
    "SERVERS_SHAPE",
    "SETTINGS_ROUTES",
    "current",
    "doctor_argv",
    "serving",
]

#: Seconds `moxi doctor --json` may run for the modules view's check.
DOCTOR_TIMEOUT = 60.0
_HERE = "moxi_ui.page"

_shell: contextvars.ContextVar[Any] = contextvars.ContextVar("moxi_ui_shell")


def current() -> Any:
    """The shell the request being served belongs to. Raises `LookupError` outside one."""
    return _shell.get()


def serving(shell: Any, inner: Callable[..., Awaitable[None]]) -> Callable[..., Awaitable[None]]:
    """`inner`, the page's in-process app, with `current()` giving `shell` to the handlers
    of each request it serves (offloaded handlers too: they run in the request's
    context)."""

    async def serve(scope: dict[str, Any], receive: Callable[..., Any],
                    send: Callable[..., Any]) -> None:
        token = _shell.set(shell)
        try:
            await inner(scope, receive, send)
        finally:
            _shell.reset(token)

    return serve


# --- the shapes -------------------------------------------------------------------------

_SAMPLE = Out("table", nullable=True, items={
    "pid": Out("int"), "rss": Out("int"), "cpu_s": Out("float"), "fds": Out("int")})
_BUDGET = Out("table", nullable=True, items={"rss": Out("string"), "fds": Out("int")})
_STRING = Out("string", nullable=True)
_INT = Out("int", nullable=True)
_FLOAT = Out("float", nullable=True)

#: A page server's row of `/servers`, as the starters give it, with its page's budget.
SERVER_ROW = Out("table", items={
    "module": Out("string"), "state": Out("string"), "server_id": _STRING,
    "version": _STRING, "transport": _STRING, "pid": _INT, "child": Out("bool"),
    "uptime_s": _FLOAT, "failed_for_s": _FLOAT, "log": Out("string", mark="path"),
    "starts": Out("int"), "failed_starts": Out("int"), "failed_in_row": Out("int"),
    "crashes": Out("int"), "last_exit": _INT, "last_exit_at": _STRING,
    "last_crash_at": _STRING, "reason": _STRING, "sample": _SAMPLE,
    "open": Out("table", nullable=True, items={"requests": Out("int"), "streams": Out("int")}),
    "budget": _BUDGET,
})

#: The shell's own part of `/servers`.
SHELL_ROW = Out("table", items={
    "pid": Out("int"), "port": Out("int"), "server_id": Out("string"),
    "version": Out("string"), "started_at": Out("string"), "uptime_s": Out("float"),
    "names": Out("list", items=Out("string")), "localhost": Out("bool"),
    "children": Out("int"), "streams": Out("int"), "sample": _SAMPLE, "budget": _BUDGET,
    "snapshot_error": _STRING,
})

SERVERS_SHAPE = {"shell": SHELL_ROW, "servers": Out("list", items=SERVER_ROW)}

_ATTRIBUTE = Out("table", items={
    "name": Out("string"), "type": Out("string"), "values": Out("list", items=Out("string")),
    "required": Out("bool"), "doc": Out("string")})
_COMPONENT = Out("table", items={
    "name": Out("string"), "element": Out("string"), "script": Out("string"),
    "url": Out("string"), "title": Out("string"), "doc": Out("string"),
    "contract": Out("int"), "place": Out("string"), "home": Out("bool"),
    "attributes": Out("list", items=_ATTRIBUTE)})
_MODULE = Out("table", items={
    "name": Out("string"), "state": Out("string"), "reason": Out("string"),
    "version": Out("string"), "summary": Out("string"), "page": Out("bool"),
    "mounted": Out("bool"), "export_error": _STRING, "budget": _BUDGET,
    "components": Out("list", items=_COMPONENT),
    "refused": Out("list", items=Out("table", items={"name": Out("string"),
                                                      "reason": Out("string")})),
})

#: The modules view's value: `/m/ui/api/modules` and the topic `ui.modules`.
MODULES_SHAPE = {"at": _STRING, "error": _STRING, "contracts": Out("list", items=Out("int")),
                 "modules": Out("list", items=_MODULE)}

_ROW = Out("table", items={"module": Out("string"), "name": Out("string"),
                           "state": Out("string"), "detail": Out("string"), "fix": _STRING})
DOCTOR_SHAPE = {"rows": Out("list", items=_ROW), "error": _STRING}


# --- the handlers -----------------------------------------------------------------------


async def servers(call: Any) -> dict[str, Any]:
    """`/servers`: every page server's row with one sample each, taken now, and the
    shell's own row with its own sample and budget."""
    import asyncio

    from .health import SHELL_BUDGET
    from .snapshot import budget_of
    from .starter import sample

    shell = current()
    data = await shell.starters.servers()
    view = shell.snapshot.current if shell.snapshot is not None else None
    mounts = view.exports.mounts if view is not None else shell.mounts
    for row in data["servers"]:
        row["budget"] = budget_of(mounts.get(row["module"]))
    names = sorted(shell.listeners.names)
    push = getattr(shell.push, "streams", 0)
    data["shell"] = {
        "pid": os.getpid(),
        "port": shell.listeners.port,
        "server_id": shell.info.server_id,
        "version": shell.info.version,
        "started_at": shell.info.started_at,
        "uptime_s": round(time.monotonic() - shell.started, 3),
        "names": names,
        "localhost": "localhost" in names,
        "children": shell.starters.children(),
        "streams": push if isinstance(push, int) else 0,
        "sample": await asyncio.to_thread(sample, os.getpid()),
        "budget": dict(SHELL_BUDGET),
        "snapshot_error": view.error if view is not None else None,
    }
    return data


async def stop(call: Any) -> dict[str, Any]:
    """Stop the shell: the answer goes out, then the server stops."""
    current().stop()
    return {"ok": True}


async def modules(call: Any) -> dict[str, Any]:
    """The modules view's value, after the first snapshot (waited for, briefly)."""
    from .snapshot import modules_value

    shell = current()
    snapshot = shell.snapshot
    if snapshot is None:
        return modules_value((), {}, {})
    snapshot.touch()
    await snapshot.ready()
    if snapshot.current is None:
        return modules_value((), {}, {}, error="the module snapshot is not ready yet")
    return snapshot.current.value


def doctor_argv(module: str | None = None) -> list[str]:
    """`moxi doctor [module] --json`, in the spawn rule's form."""
    from moxi.paths import python

    return [str(python()), "-P", "-m", "moxi", "doctor", *([module] if module else []), "--json"]


def doctor(call: Any) -> dict[str, Any]:
    """`moxi doctor [module] --json`, run in a fresh process from the shell's runtime
    dir. Blocking: the base runs it on a worker thread."""
    from moxi import proc
    from moxi.names import is_module_name

    shell = current()
    module = call.query.get("module")
    if module is not None and not is_module_name(module):
        return {"rows": [], "error": "not a module name"}
    try:
        done = proc.run(doctor_argv(module), timeout=DOCTOR_TIMEOUT,
                        cwd=str(shell.layout.runtime_dir()), env=shell.starters.child_env())
    except subprocess.TimeoutExpired:
        return {"rows": [], "error": f"moxi doctor ran past {DOCTOR_TIMEOUT:g} s"}
    except OSError as err:
        return {"rows": [], "error": f"moxi doctor could not run: {err}"}
    try:
        listed = json.loads(done.stdout)
        if not isinstance(listed, list):
            raise TypeError("not a list")
    except (ValueError, TypeError):
        return {"rows": [], "error": f"moxi doctor exited {done.returncode} with no answer"}
    rows = []
    for item in listed:
        if isinstance(item, dict) and all(isinstance(item.get(key), str)
                                          for key in ("module", "name", "state", "detail")):
            fix = item.get("fix")
            rows.append({"module": item["module"], "name": item["name"],
                         "state": item["state"], "detail": item["detail"],
                         "fix": fix if isinstance(fix, str) else None})
    return {"rows": rows, "error": None}


# --- the page ---------------------------------------------------------------------------

#: The shell's own routes.
ROUTES = (
    Route("/api/servers", "read", f"{_HERE}:servers", returns=SERVERS_SHAPE),
    Route("/api/stop", "act", f"{_HERE}:stop", returns={"ok": Out("bool")}),
    Route("/api/modules", "read", f"{_HERE}:modules", returns=MODULES_SHAPE),
    Route("/api/doctor", "read", f"{_HERE}:doctor",
          query={"module": Field("string", required=False, max=64)}, returns=DOCTOR_SHAPE),
)

#: The settings view's routes, declared by `settings.py`.
SETTINGS_ROUTES: tuple[Route, ...] = _settings.ROUTES


#: The shell's page, served in process at `/m/ui/`. Its assets are the web build's
#: output, `web/`: client.js, the theme, the document and the shell's components.
PAGE = Page(routes=(*ROUTES, *SETTINGS_ROUTES), assets="web", package="moxi_ui")
