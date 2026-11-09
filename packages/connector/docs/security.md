# Security model

The connector is a public HTTPS endpoint on your machine. It is off by default. With its
switch on and an address from the tunnel module (or a `public_url` you forward yourself),
anyone on the internet can reach it. Everything below is about who can get past its door,
and what crosses it.

## Who can sign in

Every call to the tools needs a bearer token, and a token comes only from a sign-in. A
sign-in needs a one-time code typed into the connector's sign-in page, and a code is
minted only at your machine, by `moxi connector code`. There is no default code, no code
in the config file, and no code written to disk or to the log. A process running as your
user can also go around the sign-in by writing the grants file; "Processes of your own
user" below says what that means.

A code is 8 characters (40 bits), works once, and lives 5 minutes. At most 3 live at once.
A sign-in in progress takes 5 wrong codes before it is dropped. After 20 wrong codes across
every sign-in in 10 minutes, every live code is dropped and the call log gets a
`code-burned` row. Whoever redeems a code at a sign-in in progress gets a grant that lasts
30 days from its last refresh (`refresh_days`).

### The mint gate

The connector mints a code only when an installed module declares a mint gate and no
gate refuses. With no gate installed, nothing can be minted, and `moxi connector code`
says so. A gate has two checks. The connector runs the first on the process at the other
end of its control socket before every mint; that one is the gate. `moxi connector code`
runs the second on its own shell before it asks, to give an honest refusal early; it is
not a gate, since a process can call the socket directly.

comms provides the one gate today. Its socket check walks the calling process's ancestry
against every live comms session, the walk comms makes before a send under a reserved
name. A process inside a session is refused, and so is one the walk can't place. Its
shell check refuses a shell inside a session, or one with no operator stamp. In comms the
operator is the human running the system, as opposed to the agents, and the operator stamp
is the `MOXI_COMMS_OPERATOR` variable you set in your own terminal to send messages as
yourself ([comms' configuration](https://github.com/CodeManEthan/moxi/blob/main/packages/comms/docs/configuration.md)).

What this stops, and what it doesn't, plainly. It refuses every process the session walk
places inside a live comms session, so an agent that runs `moxi connector code` or calls
the socket from its own process tree can't mint a code. An agent that deliberately starts
a process outside its tree (with `setsid` or `systemd-run --user`, say) and calls the
socket from there can mint one, and so can any other process of your user outside every
session's tree: a harness comms doesn't know, a timer. This is the boundary comms already
keeps for the operator's lane (sending as the operator, which comms refuses inside an
agent's session) and for reserved names. It is accepted as designed, and no
per-code approval on another device is built. The other way in is a person: someone who
talks you into reading a code out. Use a code only for a sign-in you started yourself.

### The sign-in page

The page checks a sign-in request before it shows anything. A client the connector doesn't
know, or a return address that isn't registered and allowed (`redirect_uris`, Claude's
callbacks by default), gets a plain error page and no redirect. A client may register only
those callbacks, so a code it wins can reach only Claude. The name a client gave itself is
shown as text: cut to 64 characters, control and format characters removed, escaped, and
labelled as the client's own name next to the address it returns to.

The page runs no script. It sends `Cache-Control: no-store`, a content security policy that
allows only its own style, posts its form only to itself and the allowed callbacks, and
forbids framing, plus `Referrer-Policy: same-origin` and `X-Content-Type-Options: nosniff`.
Its form's POST is refused when the browser says it came from another site (`Origin` or
`Sec-Fetch-Site`).

## Tokens and grants

Tokens rest only as hashes. The grants file (`STATE/connector/grants/grants.json`, 0600)
keeps each grant's id, client, label, address and times, and the SHA-256 of its tokens;
client secrets rest as hashes too. A copy of the file grants nothing.

An access token lives an hour. Each refresh replaces the refresh token. A replaced refresh
token presented again is answered once more only while its replacement has never been used
and less than 10 minutes have passed; otherwise the whole grant is revoked and the call log
gets a `refresh-reuse` row, since a token used twice is how a leaked one shows.

A grant is good only for the address it was made under. When the address changes, every
older grant is refused until the address comes back, and `moxi connector status` and
`moxi doctor` say: "The connector's address changed. In Claude, remove the connector and
add it again with the new URL."

`moxi connector revoke <id>` revokes one grant, and `moxi connector revoke --all` revokes
every grant and forgets every client; either works with the process up or down, and takes
effect on the next call. `moxi connector disable` revokes every grant before it turns the
switch off, and the process does the same when it stops with the switch off, so no grant
outlives the door it was made for.

## What a caller without a token can do

It can't take anything, and it can't push out state you are using: every open route keeps
bounded state and refuses when full. At most 64 clients that never finished a sign-in
exist, each dropped after 24 hours; at most 8 sign-ins are in progress, and a ninth is
refused while the eight stay; codes are minted only through the gate. Per minute, the
connector takes 10 registrations, 20 loads and 20 posts of the sign-in page, and 60 token
requests; past a cap it answers 429.

It can still deny service:

- fill the 8 sign-in slots, so your sign-in waits up to 10 minutes;
- fill the 64 client slots, which keeps a first add or a re-add refused while it keeps
  going and for 24 hours after. A sign-in on a client that already held a grant needs no
  registration; `moxi connector revoke --all` frees the slots;
- flood the connections, which uvicorn and a cap of 32 requests at once bound;
- spend the per-minute caps. Every caller shares them, since no header the connector
  trusts tells one caller from another. 60 junk token requests a minute make your
  Claude's refresh answer 429, so its grant stops working within the hour (an access
  token lives an hour) for as long as that goes on. 20 posts of the sign-in page a minute
  refuse your own form, so no sign-in finishes then. And with a client and sign-ins of
  its own, its wrong codes count toward the burn at 20, which drops every live code,
  yours included, and writes a `code-burned` row.

Each lasts only while the caller keeps going. `moxi connector disable` closes the door.

## What crosses the door

Claude's calls come from Anthropic's cloud, through your tunnel provider (Tailscale Funnel
or Cloudflare). Tool calls, their arguments and their answers cross both. With comms' tools
that means message bodies you send and read through Claude. Claude in the app also reads
untrusted text (mail, web pages, other connectors), and a Claude steered by it could call a
tool you didn't mean it to call, especially with "always allow" on. comms' own security
doc says what its tools can reach.

## The call log

Every request and every sign-in event goes to `STATE/connector/calls/<UTC date>.jsonl`,
0600, kept 30 days. A row holds the time, the route, the status, the tool, the grant's id,
the client's id and the first 64 characters of the user agent. No row holds a token, a
code, an `Authorization` header, a query string, a request or answer body, or a message
body: the writer refuses those fields. Every row a caller without a token can cause is
collapsed: past 10 a minute of one route and status (every request without a valid grant,
metadata fetches and page loads among them) and past 10 registrations a minute, the rest
of the minute is one row with a count, so a flood can't fill the disk.

## Processes of your own user

The mint gate guards one thing: who can mint a sign-in code. The rest of the connector's
local state rests on your user account, the boundary comms keeps for its own store and
sockets. Any process running as your user, a process inside an agent session included,
can:

- write the grants file and plant a grant for a token it picked, with no code and no
  gate involved, or revoke the grants there;
- connect to the control socket, though the gate still refuses its mint when the walk
  places it inside a session;
- change the connector's config file, its switch included.

So a grant comes only from a code for a process that keeps to the connector's own verbs.
The gate keeps an honest agent from signing Claude in on its own, and a planted grant
takes deliberate work. The connector does not protect itself from a hostile process
running as you, and doesn't claim to. comms' security doc says the same of its store and
sockets.

## Other users on the machine

The connector listens on loopback only. While its switch is on, a socket unit holds the
port, so no other local user can bind it while the process restarts and collect tokens.
Its control socket is mode 0600 in a 0700 directory and drops a peer of another user before
reading it.

That holds while you are logged in. A Tailscale forward outlives your login: tailscaled
keeps forwarding to 8452 after you log out, while your user units, the socket unit among
them, stop with your last logout. On a machine other people log in to, turn on lingering
for your user (`loginctl enable-linger`), or remove the tunnel entry before you log out.

## Reporting a vulnerability

Email codemanethan@gmail.com with the details. Please don't open a public issue for a
security problem.
