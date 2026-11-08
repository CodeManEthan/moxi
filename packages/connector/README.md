# moxi-connector

The public MCP door for the Claude apps. Claude on the web, the desktop app and the phone
apps, in text chat and in voice mode, can add a custom connector: an MCP server on the
internet that Anthropic's cloud calls. This module is that server. It owns the listener,
the OAuth sign-in, the public address, the call log, and the entry-point group
`moxi.connector.providers` through which other Moxi modules give it tools. On its own it
serves no tools; comms, installed with its `connector` extra, gives it four (`agents`,
`send`, `check_messages` and `send_and_wait`).

It is off until you turn it on, and only you can sign in to it: each sign-in needs a
one-time code minted at your own terminal.

## Commands

- `moxi connector on` turns it on and prints the next steps. `moxi connector off` revokes
  every grant, then turns it off; the tunnel entry stays until you remove it.
- `moxi connector status [--json]` shows the URL to paste into Claude, the ports, the
  process, each provider with its tools, the grants, the last call and every warning.
- `moxi connector code [--label NAME]` mints a one-time sign-in code. It works once, for
  5 minutes. See "Signing in" below.
- `moxi connector grants [--json]` lists the sign-ins it keeps. `moxi connector revoke
  <id>` revokes one; `moxi connector revoke --all` revokes every one and forgets every
  client, so Claude needs the connector added again.
- `moxi connector check` probes the public address the way Claude reaches it. It is the
  one command here that uses the network, and runs only when you type it.
- `moxi connector log [--days N] [--json]` prints the call log.

Its settings are `enabled`, `port` (the loopback port, 8452 by default), `public_url`,
`redirect_uris` and `refresh_days`; `moxi config connector` shows them.

## Setting it up

1. `moxi connector on`.
2. Give it a public address. With the tunnel module, `moxi tunnel add connector/mcp`;
   without it, set `public_url` to an HTTPS address you forward to the loopback port
   yourself. Use port 443: Claude's connector service has so far been seen reaching only
   that port. That is an observation, not a documented guarantee.
3. `moxi connector check`.
4. `moxi connector status` prints the URL. In Claude, open Settings, Connectors, Add
   custom connector, and paste it.

## Signing in

When you add the connector, Claude opens the connector's sign-in page in your browser. The
page names the client by the name it gave itself and the address it returns to, and asks
for a code. In your own terminal, outside every agent session, run:

```sh
moxi connector code --label phone
```

Type the code it prints into the page. A wrong code costs a try; after five, start the
sign-in again from Claude. Use a code only for a sign-in you started yourself.

A code is minted only when an installed module can tell an agent session's processes from
your terminal and says yes. comms provides that check: a shell or process inside a live
comms session is refused. Without such a module, `moxi connector code` refuses and says
so. [`docs/security.md`](https://github.com/CodeManEthan/moxi/blob/main/packages/connector/docs/security.md) says what the check stops and what it doesn't.

A sign-in lasts `refresh_days` (30) after Claude last refreshed it. Claude refreshes on
its own; when a grant expires or is revoked, Claude asks you to sign in again, with a new
code.

## When the address changes

A grant is good only for the address it was made under. If the address changes (a new
tunnel name, a different `public_url`), every grant made under the old one stops working,
and `moxi connector status` and `moxi doctor` say so. Claude keeps what it learned about
the old address, so pressing Connect again isn't enough: in Claude, remove the connector
and add it again with the new URL. The same advice holds after `moxi connector revoke
--all`, and whenever the sign-in page says it doesn't know the client.

## Writing a provider

A module gives the connector tools through an entry in the group
`moxi.connector.providers`, named after the module and pointing at a `Provider`:

```toml
[project.entry-points."moxi.connector.providers"]
mymodule = "moxi_mymodule.connector:PROVIDER"
```

The connector refuses an entry whose name is not the declaring distribution's module. A
provider imports nothing of the connector but `moxi_connector.provider`, its interface 1,
which is standard library only. A module that has the connector only through an extra
reaches it with `moxi.modules.load("connector", interface=1).api`, never at its own
import time.

A `Provider` holds:

- `tools`: each a `Tool` with a name, a description, an input schema, an optional output
  schema and MCP annotations, and `call(args, ctx)`, an async function that returns a
  `Result`. A refusal is `Result.refused(code, text)`, never an exception. A tool that
  raises answers "The tool failed" and the exception stays in the connector's log.
- `start(env)` and `stop()`, async, run once each in the connector's process. A provider
  whose `start` raises serves no tools and shows a `fail` row.
- `status()`: rows of `(name, level, text)`, the level `ok`, `warn` or `fail`, shown by
  `moxi connector status` and the doctor.
- `gate`, optional: a `Gate` for the sign-in code (see "Signing in").

Each call gets a `Ctx`: the grant's id and label, the request id, `log(**fields)` to add
fields to the call's log row (names that could carry a secret or a message body are
refused), and `after_answer(fn)`, which runs `fn(written)` once it is known whether the
answer reached the caller. No code, token or grant ever reaches a provider. Two providers
that declare one tool name both lose it.

## Install

Linux, Python 3.12 or later, and [uv](https://docs.astral.sh/uv/).

```sh
uv tool install moxi
moxi install connector
```

The connector serves no tools on its own. To have comms' four, install comms with its
`connector` extra, which brings the connector too:

```sh
moxi install comms --with connector
```

The first line installs `moxi`, the core, whose README covers the `moxi` command. The
repository, [CodeManEthan/moxi](https://github.com/CodeManEthan/moxi), lists the other
modules.

Read [`docs/security.md`](https://github.com/CodeManEthan/moxi/blob/main/packages/connector/docs/security.md) before you turn it on.

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/main/packages/connector/LICENSE).
