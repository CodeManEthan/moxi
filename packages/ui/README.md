# moxi-ui

The browser shell: one origin for every module's page. It runs one process, the shell,
which listens on `127.0.0.1` and `[::1]` on one port (8460 by default), signs the browser
in with a one-use ticket from `moxi ui open`, and serves each module's page under
`/m/<module>/`.

## Using the shell

Linux, Python 3.12 or later, and [uv](https://docs.astral.sh/uv/), with a browser on the
same machine (or one that reaches it through an SSH forward). ui also has macOS and
Windows code, and the repository's CI runs its browser tests there without gating, but
nobody runs ui there yet: treat both as untested.

```sh
uv tool install moxi
moxi install ui
moxi ui open
```

The first line installs `moxi`, Moxi's core, which provides the `moxi` command; the
second adds the shell beside it. A module shows a page only when it has one: the modules
with a `ui` extra, such as services, projects, loadout and the launcher. Install the shell
with one of them, `moxi install services --with ui`, or answer yes when `moxi install`
offers the extra. The repository's README, at
[CodeManEthan/moxi](https://github.com/CodeManEthan/moxi), lists the modules.

| `moxi ui` | what it does |
|---|---|
| `open [--print] [--no-browser]` | makes sure a shell of yours holds the port, then opens the browser on a one-use sign-in link, good for 30 seconds; `--print` prints the link instead, for an SSH forward or a browser elsewhere |
| `serve` | runs the shell in the foreground until stopped |
| `status [--json]` | whether the shell runs, its listener, and each page server's state |
| `stop` | stops the shell; under its socket unit the next connection starts it again |
| `signout` | ends every browser session the shell holds |
| `gateway ...` | the UI on your phone (below) |

**How the shell runs.** `moxi install ui` gives the shell to your system's supervisor. On
Linux a systemd user socket unit holds port 8460 on `127.0.0.1` and `[::1]`, so the shell
starts on the first browser connection, and stops again after 15 minutes with nothing
open (`idle_s`, 900 seconds); the socket keeps the port meanwhile, so no other program can
take it. launchd does the same on macOS, and on Windows a logon task starts the shell at
every logon. While the gateway is on, the shell also starts at login. The shell starts
each module's page server on the first request for its page.

**Settings** (`moxi config ui`): `port` (8460), `idle_s` (900), `theme.mode` (`system`,
`light` or `dark`), `theme.palette` (`plain` or `midnight-neon`), and the gateway's
`gateway.enabled` (off) and `gateway.port` (8461).
A change to `port` takes effect once `moxi install ui` rewrites the unit.

**Environment.** ui reads one variable of its own, `MOXI_UI_LOCKED_SETTINGS`, from the
shell's environment: the settings its pages may not change for that run. Its value is a
list of full keys, `<module>.<key>` (`ui.gateway.port`, `moxi.<key>` for the core's),
separated by commas or spaces. An entry locks the key it names and every key under it, so
`ui.gateway` locks both gateway keys and a module's name alone locks all of its settings.
A key above a locked one is refused too, since writing a table would replace the entry it
holds; the other entries of that table can still be changed.
The settings view shows a locked field read-only with `Locked for this run of the shell.`,
and the shell answers a write to one with a 422 before any command runs. Unset or empty,
it locks nothing. It guards the shell's pages only: `moxi config set` in a terminal and an
edit of the file still change the key, and the variable holds no value for it.

## A module on Home

Home, at `/m/ui/`, shows one main piece and a row per module with something to report. A
module takes a seat there by announcing a component with `home_as` (`"main"` or `"row"`)
and an `order`; a row component reports its line with a `moxi-home-row` event and Home
draws it. [docs/home.md](https://github.com/CodeManEthan/moxi/blob/main/packages/ui/docs/home.md)
has the fields, how rows are ordered and opened, and the `needs` and `order` scales.

## Confirmed acts in a page

An act the user should confirm a second time, such as stopping a service or removing a
project, is a confirmed act: it runs at most once, and only after the user says yes on a
sheet the shell draws. The page declares it in its route table with `confirmed=True` and
the status route it reports on; [docs/loopback.md](https://github.com/CodeManEthan/moxi/blob/main/packages/ui/docs/loopback.md), "Confirmed acts",
has the route's whole shape. For example:

```python
Route("/api/status", "read", "moxi_usage.pagehandlers:status",
      query={"job": Field("string")}, returns={"state": Out("string")}),
Route("/api/stop", "act", "moxi_usage.pagehandlers:stop",
      body={"name": Field("string")}, returns={},
      confirmed=True, status="/api/status"),
```

A component calls it with `confirm` from `/m/ui/client.js`, never with `api`:

```js
import { api, confirm } from "/m/ui/client.js";

const outcome = await confirm("usage", "api/stop", { name: "backup" },
                              ["Stops the backup timer until you start it again."]);
if (outcome.outcome === "accepted") {
  const job = await api("usage", outcome.status.slice(1));  // "api/status?job=..."
}
```

- `confirm(module, path, body, sheet)` shows `<moxi-ui-confirm>`: the module's page title,
  the component's `sheet` lines and the body. On Confirm it makes a new `Idempotency-Key`
  from 32 random bytes and posts the body once with it. A second click sends nothing more.
  It never retries.
- It resolves with one of:
  - `{outcome: "accepted", status}`: the module took the act; `status` is its status
    route's path and query on the module's page, such as `/api/status?job=7`;
  - `{outcome: "refused", code, text}`: the act was not done. The module refused it (any
    4xx, or its 503 when it was too busy), or the shell couldn't hand it over (a page
    server that didn't start or couldn't be reached, or one that was stopping);
  - `{outcome: "unknown", text}`: the act was sent and no answer says how it went (a 500,
    a page server that ended before answering, no answer in 30 s, or no answer from the
    shell at all). The module may have acted, so the component points the user at the
    job, not at a retry;
  - `{outcome: "cancelled"}`: the user closed the sheet and nothing was sent;
  - `{outcome: "desk", text}`: on a phone where the act can't be done, the sheet told the
    user to do it at the desk, with `text` saying why, and nothing was sent. This outcome
    came with the gateway: a component that matches the outcomes exhaustively, such as a
    `switch` TypeScript checks for `never`, needs a branch for it.
- The component shows the outcome itself; the sheet closes once the answer is in.
- `api()` on a confirmed act's path throws a `TypeError`, so a component can't skip the
  sheet. `confirm()` on any other path throws one too. The shell reads which paths are
  confirmed from the page's export.
- The shell passes `Idempotency-Key` on to the page server with the other headers it
  forwards, and nothing more.

## The gateway: the UI on your phone

The gateway is a second listener of the shell, reached from a phone through the tunnel
module's `ui/gateway` target. A phone pairs by scanning a QR code the desktop shows and
registering a passkey, then signs in with the passkey. It reaches only the routes a module
marks `gateway=True`, with marked fields redacted, and every request is in an audit log.

To set it up, outside Moxi: a Tailscale account, Tailscale on the computer and the phone,
MagicDNS and HTTPS certificates on the tailnet, and on Linux `sudo tailscale set
--operator=$USER` once. A phone already on another VPN can't run Tailscale beside it;
Cloudflare is the other provider. Then:

```sh
moxi install ui --with gateway,tunnel
moxi ui gateway enable
```

and scan the QR code in the desktop's Gateway section. No domain, no key to type.

| `moxi ui gateway` | what it does |
|---|---|
| `pair` | prints a one-use pairing URL, good for 2 minutes, whether or not the shell runs |
| `devices` | lists the paired devices |
| `revoke <id>` | deletes a device; its sessions end within 2 s |
| `log [--days N] [--json]` | prints the audit log, today and the N-1 days before |
| `on` | checks the extras, the tunnel and the runtime directory, sets `gateway.enabled`, starts the shell, and runs `moxi tunnel add ui/gateway` when there is no entry |
| `off` | clears `gateway.enabled`, ending every phone session, and offers to remove the tunnel entry |

Each verb works on files: none calls the shell, so none needs a browser session.

`gateway.enabled` turns the gateway on. While it is on, the shell starts at login on systemd
and launchd (on macOS from the next login) and is restarted after a crash; on Windows the
shell's logon task starts it at every logon either way. Changing it never restarts a running
shell. `gateway.port` (8461) is the loopback port the tunnel forwards to.

Unlike 8460, the gateway's port is held by the shell itself, not by a socket unit, so it
listens on a port another program could take while Moxi isn't running. The tunnel entry
still forwards to it then, so a program that took the port could answer the phone at the
gateway's address. The phone's acts are never mounted on this port: from the phone you
can only read, redacted. `moxi doctor ui` says the same on its `gateway` row. On macOS and
Windows the gateway also has no provider check, so any local program can reach its open
routes; those still need a pairing code or a passkey.

On a machine other people log in to, a Tailscale forward outlives your login: tailscaled
keeps forwarding after you log out, while your user units, which hold Moxi's ports, stop
with your last logout. Turn on lingering for your user (`loginctl enable-linger`), or
remove the tunnel entry before you log out.

The gateway needs moxi-tunnel 0.2 or later. `moxi install ui --with tunnel` counts the extra
installed once any tunnel is, so it leaves an older tunnel as it is; `moxi ui gateway enable` and
`moxi doctor ui` name the command that raises it.

`moxi doctor ui` shows three gateway rows: `gateway` (its address, endpoint, devices and
whether the phone's acts are mounted, or what stops it), `gateway login` (whether the shell
starts at login while the gateway is on) and `gateway audit` (the audit log's directory 0700
and its files 0600).

## The check a page makes before it writes

A page server runs in the browser shell's environment, which can differ from the user's
terminal. ui's interface 2 adds `moxi_ui.api`, which a module reaches through its `ui` extra:

```python
found = moxi.modules.load("ui", interface=2)
line = found.api.check(places)   # None, or the one line to refuse with
```

`places(env)` returns `{label: path}`, the folders and files the act reads or writes,
resolved from the mapping alone. `check` resolves them twice, from the core's record of the
user's last run in a terminal and from the page's own environment, and returns None when
they agree; otherwise the line names each difference and the variables to set, with
`where()`'s place and step for the browser shell. A page act that writes calls it first
and refuses with the line. A page part that can't load ui at interface 2 refuses its write
too, and names the step: install or update moxi-ui.

## Page servers

The shell starts each module's page server on the first request for its page, as
`python -P -m moxi_ui.loopback.serve <module>`, and reads every module's route table with
`python -P -m moxi_ui.loopback.export <module>...`. Both run on loopback, ui's
page-server base: [docs/loopback.md](https://github.com/CodeManEthan/moxi/blob/main/packages/ui/docs/loopback.md) has the request check, how a page
is declared, and the runner's rules.

## Testing a module's pages

`moxi_ui.testing` is a pytest plugin, installed with moxi-ui and loaded in every pytest run
beside it. Its `ui_*` fixtures start a real shell over the installed modules
(`ui_shell`), open signed-in browser contexts (`ui_page`), and run the contract check
(`ui_component_check`) and the 360 px width check (`ui_width_check`) on a module's own
components. The browser fixtures need the `testing` extra and a browser:
`pip install 'moxi-ui[testing]'`, then `playwright install chromium`. The plugin's
docstring lists every fixture.

A page module whose UI is optional:

- declares an extra `ui = ["moxi-ui>=V,<W"]` and an `OPTIONAL` sentence for it;
- reaches loopback, ui's page-server base, in `page.py` and its handlers only through
  `moxi.modules.load("ui", interface=3).api.loopback`, and raises `ImportError` when the
  load fails;
- imports neither from its announcement, API, CLI or console scripts;
- gets the contract check in its suite through the plugin's fixtures, by name, and never
  imports `moxi_ui`.

## Dependencies

`moxi`, Moxi's core; `starlette` and `uvicorn`, which serve every page server's requests;
`h11`, which uvicorn parses HTTP with, at 0.16 or later for its request-smuggling fix;
and `psutil`, which the shell uses to watch its page servers.

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/main/packages/ui/LICENSE).
