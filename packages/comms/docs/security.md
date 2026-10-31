# Security model

Moxi comms runs on one machine, for one user. Everything it does rests on that.

## Identity is a same-user claim

A comms name is a claim a process makes when it registers with the daemon. The daemon
checks the claim against who already holds the name, and the record keeps the identity
that authored each message. None of that is cryptography. It proves that a process
running as your user said so, and nothing more.

Any process running as your user can:

- read the credential chain's token store (`STATE/comms/tokens/`) or the user's
  keychain, and post as any holder in it, the operator and a module's reserved name
  included;
- read and write comms' store (`STATE/comms/comms.db`) directly;
- connect to the daemon's socket and bind any name that isn't held or reserved;
- connect to a session's Moxi turns endpoint and report a turn end that never happened,
  which acks mail the model never saw.

So the trust boundary is your user account. comms keeps agents from confusing each
other by accident and makes who-said-what visible. It does not protect one agent from a
hostile process running as you, and it never claims to.

The binding tokens, the 0600 files and the operator's own credential still matter: they
keep an honest agent on its own lane and make a forged claim take deliberate work.

## What other users on the machine can see

- The daemon socket and every control socket are mode 0600 in a 0700 directory.
- Token store files are 0600 in a 0700 directory. comms refuses to read a token file or
  store directory with any group or other permission bit set.
- `comms.db`, which holds message bodies, is created 0600, and so are its WAL and
  shared-memory files.

## Credentials

The daemon mints a fresh, random token for every live binding. The token stays in the
adapter's memory and crosses the claim socket only inside that session; the daemon
keeps only its SHA-256 hash. A daemon restart invalidates every old binding token, and
the next register gets a new one. No binding token is written to the state directory
or to a log.

The operator is different: its posting token is stored at rest because the operator
lane is not a live agent binding. The credential chain reads that token from the
environment first, then the user's keychain, then the token store. A token copied off the machine is still a
credential for this same-user trust boundary, not a new identity system.

Files from the 0.1.x posting-key layout that remain in a store outside comms' own
store are not accepted by the 0.2.0 posting path. The migration removes the retired
files from comms' own store when they match the old format, while leaving unrelated
files alone. Every message is written by the daemon, after it has checked the sender's
credential: a live binding's token, the operator's, or a reserved name's.

A reserved name's token is minted when its module installs and rests in the credential
chain for that module, as `comms/reserved/<name>`. No session is given it. The daemon
refuses a send under a reserved name presented with a session's binding token, and
refuses one from any process inside a live session, or one whose ancestry it can't
read. That keeps an honest agent off a module's name; a process of your user that reads
the token and leaves every session's process tree can still send under it, as it can
forge the operator's lane.

A module's service may read the credential chain differently from your shell (on Linux,
the keychain may not be reachable from a service). When a send under a reserved name
finds no token it can use, comms leaves a record that `moxi doctor` shows, naming the
process, until a send under that name works again or `moxi comms reserved renew NAME`
mints a new token. Renew from the environment that failed: a token minted in your shell's
keychain may still be out of the service's reach.

comms' config file, `config.toml`, holds non-secret values only. comms refuses to load
it when a key looks like a token.

## Message bodies are untrusted text

A message body is whatever another agent wrote. comms renders it inside a closed
envelope: every body line is indented two spaces, a closing line paired to the frame by
message id ends it, and control and format characters (escape sequences, bidi
overrides, zero-width characters) are removed. comms' own lines start at column zero
and a body can never reach column zero, so a frame line forged inside a body shows up
indented. The rule a model is taught is that only the left margin is comms speaking.

`moxi comms whois` cards are read the same way: a card is its holder's own claim, budgeted
and rendered in the same envelope, and never a source of authority.

A declared director's message is shown as a directive only when its attribution is
verified. That calibrates how a model weighs a message. It doesn't stop a model from
obeying a stranger or ignoring its director, and it isn't access control.

## The Codex adapter (experimental)

Codex's sandbox gates a Unix-socket `connect()` only through its network-access
setting, so a Codex agent that answers through `moxi comms reply` needs `network_access =
true`, and that grants the agent full outbound network. There is no narrower setting
that admits the daemon socket. Treat a Codex comms agent as not network-contained.

## Network

The supported path opens no network connection. The daemon, the CLI and the adapters
talk over Unix sockets and local files. There is no telemetry.

## Reporting a vulnerability

Email codemanethan@gmail.com with the details. Please don't open a public issue for a
security problem.
