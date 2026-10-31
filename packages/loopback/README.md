# moxi-loopback

Moxi's loopback module is the base every Moxi page server is built on. A module that
shows a page in the browser declares its routes as data, and loopback serves them: it
checks every request, dispatches it to the module's handler, cuts the answer to the
declared shape, serves the page's static files, and starts, idles out and stops the server
the same way for every module. Moxi's UI shell, the one origin the browser talks to, uses
the same check on its own listener and reaches each module's page server through the
runtime file and the connection functions here. loopback is a library: it has no `moxi`
command and no settings.

The threat model: module pages are trusted code like their Python half; Moxi defends
against the network and other users on the machine, not a module you chose to install.

## The request check

Every request passes one check, `check(view, profile)`, before anything else sees it. A
module's page server runs it with an `upstream` profile, and the shell with a `browser`
profile:

1. **Host.** On TCP a module server accepts only `Host: 127.0.0.1:<its port>`, which holds
   against DNS rebinding.
2. **OPTIONS** is refused, so no CORS preflight ever passes.
3. **Browser headers.** A module server refuses any request that carries `Origin`,
   `Cookie` or a `Sec-Fetch-*` header: the shell strips them, so a request that still has
   one came straight from a browser. The shell's own listener applies Origin and Fetch
   Metadata rules instead.
4. **The token.** A module server needs `X-Moxi-Token`, the token in its runtime file.
5. **The table.** A path or method the page doesn't declare is an empty 404, and an act
   without `Content-Type: application/json` is a 403.

Gates 1 and 3 don't run on a Unix socket, which no browser can open and which lives in a
directory only you can enter. Every listener binds loopback only.

Every answer the base sends, refusals and 500s included, carries one fixed header set: a
strict Content-Security-Policy, `nosniff`, `no-referrer`, same-origin opener and resource
policies, a Permissions-Policy that turns off the camera, microphone, location, USB and
payment, and `Cache-Control`. Two answers uvicorn sends on its own carry none of it: its
400 for a request it can't parse, and its 503 when all 128 of a server's connections are
open. The shell adds whatever header of the set an answer lacks before the browser sees it.

## Declaring a page

A module announces its page with one line in its announcement (the module named by its
`moxi.modules` entry point):

```python
PAGE = "moxi_usage.page:PAGE"
```

and declares it in a module that imports only `moxi_loopback.api`:

```python
# moxi_usage/page.py
from moxi_loopback.api import Field, Out, Page, Route

PAGE = Page(
    routes=(
        Route("/api/summary", "read", "moxi_usage.pagehandlers:summary",
              query={"days": Field("int", required=False, max=366)},
              returns={"total": Out("int"), "log": Out("string", mark="path")}),
        Route("/api/refresh", "act", "moxi_usage.pagehandlers:refresh",
              body={"force": Field("bool", required=False)},
              returns={"ok": Out("bool")}),
        Route("/api/events", "read", "moxi_usage.pagehandlers:events", stream=True,
              topics={"today": Out("table", items={"total": Out("int")})}),
    ),
    assets="web",
    health="moxi_usage.pagehandlers:health",
)
```

- A path is literal and matched exactly. A `read` is a GET and an `act` is a POST with a
  JSON object body, so no GET ever acts.
- Every query and body key is declared by a `Field`. An unknown or mistyped key is a 400
  that names the key and never repeats the value.
- Every answer is cut to `returns` before it is sent, at every depth. A key the handler
  added but `returns` doesn't declare is dropped and logged. A `null` is sent only for a
  key declared with `nullable=True`. A `null` for any other key, or a value of the wrong
  type, is refused rather than cut, and the request gets a 500.
- Handlers are `"package.module:function"` strings, so the shell and tests read the table
  without importing handler code. A handler is `def fn(call) -> dict` or
  `async def fn(call) -> dict`. A plain `def` runs on a worker thread. `call` carries the
  checked `query` and `body`, the module name, and `busy(name)`, which holds the idle exit
  while work runs. A handler may return `Accepted(status_path)` (202), or raise
  `Conflict(text)` (409) or `Refused(text)` (422). Anything else it raises is a 500.
- `assets` names one flat directory of static files in the page module's package. That is
  the package whose code builds the `Page`, so a page built by a helper in another package
  names its own with `Page(..., package="moxi_usage")`; without it the base looks in the
  helper's package. `/` serves its `index.html`. A page's own files use relative URLs, so
  they work under the shell's `/m/<module>/`.
- A page has at most one stream route. It sends server-sent events for the declared
  `topics`: the current value of each topic first, then each later change. Handlers feed
  it through `Topics(...).publish(name, value)`, and `Snapshot` rebuilds a topic's value
  once for a burst of changes and publishes it only when it changed.
- `idle_s` (default 300) is how long the server stays up with nothing open. `budget`
  (default `{"rss": "120M", "fds": 64}`) is carried in the export for whoever watches the
  process.

### Confirmed acts

An act that changes something the user should confirm twice, such as stopping a service
or removing a project, is declared as a confirmed act. It runs at most once for each key
its caller sends:

```python
Route("/api/status", "read", "moxi_usage.pagehandlers:status",
      query={"job": Field("string")}, returns={"state": Out("string")}),
Route("/api/stop", "act", "moxi_usage.pagehandlers:stop",
      body={"name": Field("string")}, returns={},
      confirmed=True, status="/api/status"),
```

- `confirmed=True` is allowed only on an act, and a confirmed act names `status`: a read
  route of the same page, not a stream, whose query declares a string `job` key. If the
  gateway reaches the act (`gateway=True`), it must reach the status route too, so a phone
  that started an act can see how it went. A desk-only act may name a status route of
  either reach. `Page` refuses a page that breaks any of this.
- Each request to a confirmed act carries one `Idempotency-Key` header: exactly 43
  characters of letters, digits, `-` and `_`, the shape `secrets.token_urlsafe(32)` makes.
  A missing key, a key sent twice or a key of another shape is a 400 that names the
  header, `{"error": "missing header: Idempotency-Key"}` (or `repeated header`, `malformed
  header`), and never repeats the value.
- The handler gets the key as `call.key` and returns `Accepted(f"/api/status?job={id}")`,
  which is sent as 202 `{"status": "/api/status?job=..."}`. A confirmed act that returns
  anything else, a plain dict included, is a 500 and is logged.
- The base keeps a map of the keys it has run, for the server's life. A key reaches its
  handler at most once while the map holds it. The same key sent again while the first
  request still runs is a 409, `{"conflict": "this act is still running"}`. Sent again
  after it finished, with the same path and body, it gets the first answer again, a 202,
  4xx or 500, and no handler runs. Sent with another path or another body, it is a 422,
  `{"refused": "this Idempotency-Key was used for another request"}`. The map keeps the
  1,024 most recently finished keys; an older one is a new key again, and the module's own
  job records say what a second run does.
- A request answered before its handler would run (a 503 when 32 requests are already
  in, a 413 or 400 for its body, a 400 for its key) claims nothing, so the same key sent
  again is judged as new. A handler cut short by a stop keeps its key, with a 500, since it
  may have acted.
- The export carries `confirmed` and `status` for every route, so the shell reads them as
  data. A route without `confirmed` works exactly as before, and ignores the header.

A page module whose distribution doesn't require `moxi-loopback` reaches the same module
as `moxi.modules.load("loopback", interface=1).api`, and imports nothing else outside the
standard library and the core.

The runner and the export command load each page through `moxi.modules.load`, which
imports the module's package `__init__`, its announcement and its `API` module, and listing
the assets imports the package `__init__` again. So a page module keeps its handler imports
out of its package's `__init__`, its announcement and its `API` module. Otherwise reading
the page's table imports its handlers too.

`gate_matrix(page)` is a test helper. It sends each route the requests the base must
refuse (the wrong method, an unknown key, no token, an act without JSON, and a request
refused at the Host, OPTIONS and Origin gates), checks each status, and runs no handler.

## Running a page server

A page server is started one way:

```sh
python -P -m moxi_loopback.serve <module>
```

with the environment's own Python, from a working directory that exists and that only you
can write. The shell starts it on the first request for the module's page. `-P` keeps the
working directory off the import path, so a planted `moxi_loopback/` or `uvicorn/` there is
never loaded. Started without `-P`, the runner exits 3 before it imports anything outside
the standard library.

- It creates the module's runtime directory, private, and works from there. On Linux and
  macOS it listens on a Unix socket in that directory. On Windows it listens on TCP on
  `127.0.0.1`, on a port the system picks, with `SO_EXCLUSIVEADDRUSE` so no other user can
  bind over it. `--tcp` gives the Windows listener on Linux and macOS, for debugging.
- It holds `page.lock` for its whole life, so only one server per module runs. It writes
  `page.json`, readable only by you, with a fresh token, the socket or port, and a fresh
  `server_id`. Then it prints one line, `ready <server_id>`, and from then on its stdout
  goes to stderr.
- stdin is its lifeline: when the starter closes it, the server stops. `--no-lifeline`
  is for running it by hand, and `--idle-s SECONDS` shortens the idle exit for tests.
- stderr is its log. `MOXI_<MODULE>_LOG_LEVEL=info` shows the start and stop lines, and
  `debug` one line per request, never with a query string or a header value.
- Exit codes: 0 for a stop, 1 for an error (or a stop the watchdog had to end), 2 for
  usage, 3 when refused (no `-P`, a runtime directory that isn't private, a page that
  can't be served), and 75 when another server holds the lock.

It stops when nothing has been open for the page's `idle_s`, when its lifeline closes, or
on SIGTERM, Ctrl-C or Ctrl-Break, and every stop is bounded. From the stop decision it
answers no new proof (below) and closes its listener. It still serves one request: the
next one on a connection whose proof it answered in the last 0.5 s. Every connection is
closed by 1.5 s after the decision, and the process is gone by 2.5 s, which is inside the
3 s a new server waits for the lock. A plain `def` handler still running at the stop is
abandoned, not waited for, so work that must outlive a stop doesn't belong in a page
handler.

### Connecting to one

`call(info, method, path, body=None, *, timeout=5.0)` sends one JSON request and returns
`(status, data)`. `open_connection(info)` returns an asyncio stream pair. `info` is the
`RuntimeInfo` that `read_runtime(module)` gives. Both first prove the server: they send
`GET /api/prove` with a fresh nonce and no token, and the server answers with an HMAC,
keyed by its token, of the nonce and its `server_id`. Only on a match do they send the
request, with the token.
Otherwise they raise `NotOurServer` and send nothing, so a stranger who binds a dead
server's TCP port learns nothing. `call` sends at once. A caller of `open_connection`
sends its first request within 0.25 s of the return, or opens a new connection, so it
falls inside the 0.5 s a stopping server keeps for it.

### In process

The shell serves its own page with the same parts, without a runner: `app(page, module=...,
server_id=..., prefix=...)` is a page's whole server as an ASGI app, `guard(inner,
profile)` runs the check in front of it, and `serve(app, sockets, should_exit=...)` runs
uvicorn on sockets the caller bound. `app` answers 403 to any request `guard` didn't pass.
`serve(..., head_limit=...)` raises how much of an unfinished request head the listener
buffers (h11's 16 KiB by default). The shell's browser listener raises it to 4 MiB, and
each connection holding an unfinished head may then cost that much memory: 128
connections at 4 MiB is up to 512 MiB.

## The export command

```sh
python -P -m moxi_loopback.export <module>...
```

prints one JSON object, keyed by module: `{"ok": true, "export": {...}}` with the module's
routes, their fields and shapes, whether each is a confirmed act and its status route, its
topics, its asset names and its budget, or `{"ok":
false, "error": "..."}` for a module with no `PAGE`, a broken target, a refused
declaration or a refused asset table. It exits 0 when every module was read, 3 when any
was refused, and 2 with no module named. The shell runs it in a fresh process to learn
every module's table without importing any module's code into itself. It builds the table
with the same function the runner serves from, and resolves no handler.

## Limits

On Windows, or with `--tcp`, another user on the machine can open connections to a page
server's port. Nothing they send keeps a server alive or stretches a stop. But while a
server runs they can fill its 128 connections, and it then answers 503. The shell reads
that as a server that is stopping and starts another, which exits 75 because the lock is
still held. So the page is down for as long as they hold the connections. And if the
server's event loop, or the caller's own, stalls past 0.25 s just as an idle stop is
decided, an act sent on a just-proved connection can be dropped unread, and the page shows
a failure for an act that never ran. Neither leaks data or runs code as you.

## Platforms

Linux, macOS and Windows, with Python 3.12 or later. Linux and macOS serve on a Unix
socket; Windows serves on loopback TCP.

## Install

loopback comes with a module that serves a page: that module requires `moxi-loopback`,
so `moxi install <module>` brings it in. The front door's README, at
[moxihq/moxi](https://github.com/moxihq/moxi), covers the `moxi` command and the other
modules.

## Dependencies

`moxi`, Moxi's core; `starlette` and `uvicorn`, which serve the requests; and `h11`, which
uvicorn parses HTTP with, at 0.16 or later for its request-smuggling fix. loopback has no
optional extras.

## License

MIT. See [LICENSE](LICENSE).
