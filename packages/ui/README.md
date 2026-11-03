# moxi-ui

The browser shell: one origin for every module's page. It runs one process, the shell,
which listens on `127.0.0.1` and `[::1]` on one port (8460 by default), signs the browser
in with a one-use ticket from `moxi ui open`, and serves each module's page under
`/m/<module>/`.

## Confirmed acts in a page

An act the user should confirm a second time, such as stopping a service or removing a
project, is a confirmed act: it runs at most once, and only after the user says yes on a
sheet the shell draws. The page declares it in its route table with `confirmed=True` and
the status route it reports on; moxi-loopback's README, "Confirmed acts", has the route's
whole shape. For example:

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
  - `{outcome: "cancelled"}`: the user closed the sheet and nothing was sent.
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
moxi ui gateway on
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

The gateway needs moxi-tunnel 0.2 or later. `moxi install ui --with tunnel` counts the extra
installed once any tunnel is, so it leaves an older tunnel as it is; `moxi ui gateway on` and
`moxi doctor ui` name the command that raises it.

`moxi doctor ui` shows three gateway rows: `gateway` (its address, endpoint, devices and
whether the phone's acts are mounted, or what stops it), `gateway login` (whether the shell
starts at login while the gateway is on) and `gateway audit` (the audit log's directory 0700
and its files 0600).

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
- reaches loopback in `page.py` and its handlers only through
  `moxi.modules.load("loopback", interface=1)`, and raises `ImportError` when that fails;
- imports neither from its announcement, API, CLI or console scripts;
- gets the contract check in its suite through the plugin's fixtures, by name, and never
  imports `moxi_ui`.
