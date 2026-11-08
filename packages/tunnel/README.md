# moxi-tunnel

Moxi's tunnel module gives a loopback service an address that something off the box can
reach. A module declares a target, a loopback port held in one of its settings and a reach
(`own-devices` or `public`), and `moxi tunnel add <target>` turns it on through Tailscale
Serve, Tailscale Funnel or a Cloudflare named tunnel. Tunnel records what it applied, in
`applied.json` in its state directory, and removes only that.

| reach | default provider | who can reach it |
|---|---|---|
| `own-devices` | Tailscale Serve, on the target's own port | your tailnet |
| `public` | Tailscale Funnel, on 443, 8443 or 10000, one target per port | the internet |
| either, with `--provider cloudflare` | your Cloudflare named tunnel, on 443 | the internet |

Tunnel authenticates nothing. The module behind the address checks every call itself.

## Install

Linux, macOS or Windows, Python 3.12 or later, and [uv](https://docs.astral.sh/uv/).

```sh
uv tool install moxi
moxi install tunnel
```

The first line installs `moxi`, the core, whose README covers the `moxi` command. The
repository, [CodeManEthan/moxi](https://github.com/CodeManEthan/moxi), lists the other
modules.

Tunnel needs only the core. For Tailscale it needs the `tailscale` CLI, the machine logged
in, MagicDNS and HTTPS certificates on for the tailnet, and for a public target the
`funnel` node attribute in the tailnet policy file. `moxi tunnel add` checks each of these
before it changes anything and names the fix for each one missing. On Linux, a normal user
may change the serve config only as Tailscale's operator, set once with
`sudo tailscale set --operator=$USER`.

## The verbs

| command | what it does |
|---|---|
| `moxi tunnel status [--json] [--check]` | every declared or recorded target: reach, exposure, provider, state, URL and loopback port, then the copies in `displaced/`. `--check` compares each with the live config |
| `moxi tunnel add TARGET [--provider tailscale\|cloudflare] [--port N] [--hostname H] [--replace] [--yes]` | turns a target on. On a target already recorded with another provider, it switches provider |
| `moxi tunnel remove TARGET [--forget] [--yes]` | turns it off. It also removes an address whose module is gone, and undoes a replace that has not finished. `--forget` drops the entry from the record and leaves a Tailscale entry's live config as it is |
| `moxi tunnel repair [--yes]` | puts each recorded address right: missing, moved, renamed or left half way. It also rewrites a `cloudflared.yml` edited by hand, and brings the Cloudflare switch and the `cloudflared` unit into step with the record |

A target is named `<module>/<name>`, such as `ui/gateway`; the bare name works when only
one module declares it. Every act prints the warnings that apply and asks once before it
changes anything. `--yes` answers yes, and the warnings are still printed. With no terminal
and no `--yes`, tunnel refuses.

Exit codes: 0 ok; 1 a provider command failed, its stderr shown unchanged; 2 usage; 3
refused (a prerequisite missing, the port taken, no confirmation, a bad declaration); 75 a
lock is busy, or what you confirmed changed while tunnel waited. `--json` prints
`{"error": ..., "fix": ...}` on a failure.

`--replace` takes over a port that holds Tailscale config Moxi did not apply. It shows that
config in full and asks; a copy is kept in `displaced/`, with the commands that would
restore it, and tunnel never deletes one. Removing Moxi's entry later does not bring the
old config back.

## Warnings

Tunnel says these before the first change, and asks:

- HTTPS certificates publish the machine's name and the tailnet's name in public
  Certificate Transparency logs. Rename the machine first if its name says something you'd
  rather not publish. Tunnel says this once for each host.
- Paired devices keep the address. Renaming the machine or switching provider changes it,
  and every paired device pairs again. Name the machine before you pair.
- A Funnel address gets public DNS records, and anyone on the internet can reach it.
- Cloudflare ends TLS and sees every request in plain text, and the hostname is in public
  DNS. Turn off Rocket Loader, email obfuscation and auto-minify for the zone; tunnel can't
  check those. An `own-devices` target over Cloudflare is on the internet.

## For a module that declares a target

```toml
[project.entry-points."moxi.tunnel"]
"ui.gateway" = "moxi_ui.tunnel:GATEWAY"
```

```python
GATEWAY = {"reach": "own-devices",          # or "public"
           "port_setting": "gateway.port",   # an int key in this module's settings
           "doc": "the UI on your phone"}
```

The entry is named `<module>.<name>`, and the module part must be the declaring
distribution's module. The loopback port is a setting, so it stays stable while
tailscaled's config outlives the process; tunnel reads it each time it applies the target
and follows a moved port on the same address. A module reads its address with
`moxi_tunnel.address(module, name)` once `moxi.modules.available("tunnel")` is true, or
with `moxi tunnel status --json` from outside Python. It uses the URL only while `state` is
`on`.

`moxi tunnel status --json` prints one object, `{"targets": [...], "displaced": [...]}`.
Each item of `targets` is one declared or recorded target, with `module`, `name`, `reach`,
`exposure`, `provider`, `state` (`applying`, `on` or `removing`), `url`, `host`, `port`,
`form`, `target`, `retired`, `change`, `displaced`, `applied_at` and `detail`, each null but the first three
while the target is not recorded; with `--check` it also has `check`, the live
comparison, and `check_detail` and `fix` when that is not `ok`. Each item
of `displaced` is one file in `displaced/`: `name`, `port`, `pending`, `path`, `target` and
`settles`. A failure prints `{"error": ..., "fix": ...}` and exits with its code.

The module behind the address must itself:

- listen on `127.0.0.1` only;
- authenticate every request: Funnel and Cloudflare add no access control, and neither does
  tunnel;
- never trust `Tailscale-User-*` or `Tailscale-Funnel-Request`, and never use
  `X-Forwarded-For` as sign-in: any local process can send them to the loopback port;
- admit `Address.host`, with and without `:port`, in its Host check;
- go by `Address.exposure`, not the reach it declared;
- send a keepalive on long streams;
- keep the `Address.change` it built its Host check from, and rebuild when it reads a
  different one. A changed host or provider means its paired devices pair again.

### A target on a Unix socket, or one that needs its owner ready

A declaration in the group `moxi.tunnel.targets`, read the same way, may also name two
callables, each as `"<python module>:<callable>"`:

```toml
[project.entry-points."moxi.tunnel.targets"]
"ui.gateway" = "moxi_ui.tunnel:GATEWAY"
```

```python
GATEWAY = {"reach": "own-devices", "port_setting": "gateway.port",
           "upstream": "moxi_ui.tunnel:upstream",   # -> "unix:<path>" or "http://127.0.0.1:<port>"
           "retired": "moxi_ui.tunnel:retired"}     # -> [{"host": ..., "port": ...}, ...]
```

- `upstream()` gives what the provider forwards to, or raises `moxi_tunnel.NotReady(text)`.
  Tunnel calls it before every apply (an add, a repair's re-apply, a provider switch) and
  refuses the act with the text, applying nothing, when it raises. `check` compares the
  live entry with what it gives, so a new value reads `moved`. A `unix:` target is
  forwarded by Serve (Linux, macOS and BSD) and by `cloudflared`; Funnel takes only the
  loopback form. Such an entry has `Address.form` `unix` and its path as `Address.target`,
  and holds no loopback port.
- `retired()` gives origins the module keeps a copy of; tunnel adds them to its own list
  before it places the target, which refills the list after a `--purge`.

These keys are refused in `moxi.tunnel`, and so is a target declared in both groups. A
caller that relies on them loads tunnel with interface 2. `lifetime` is refused in either
group.

**Retired origins.** An origin is `https://<host>:<port>`. Tunnel's record keeps a list,
which only grows, of every origin at which a persistent loopback mapping has been live:
such a mapping forwards whether or not its owner runs, so another program may have
answered under it. Tunnel adds the origin before any command that can make such a mapping
live, and the origin of foreign config a `--replace` takes over; nothing takes one out, not
a remove, a forget, a switch or an uninstall without `--purge`. `Address.retired` is true
for an entry whose origin is on the list. A Unix socket target retires nothing, and its
port skips every retired one, starting from its port setting's value; a repair that puts
an entry onto a socket picks a new port when the one it would keep is retired. A
`--port` or a Cloudflare hostname you name is taken as given, with one line saying so.

Each Cloudflare rule carries `originRequest: httpHostHeader: <hostname>`, so the target
sees its own hostname as the Host.

A module may also declare `on_change(target, old, new)` in the group
`moxi.tunnel.changes`, named by the module. Tunnel calls it in the process that made the
change, after the change, once per changed address.

## Settings

`moxi config tunnel` shows them, and `moxi config tunnel path` prints the file's path.

| key | default | what |
|---|---|---|
| `tailscale.binary` | empty | the `tailscale` CLI. Empty looks on `PATH`, then in the platform's usual place |
| `cloudflare.tunnel_id` | empty | the id of the Cloudflare named tunnel you created |
| `cloudflare.credentials` | empty | the tunnel's credentials JSON. Tunnel passes the path to `cloudflared` and never reads or copies the file |
| `cloudflare.metrics_port` | 20241 | the loopback port `cloudflared` serves `/ready` on |
| `cloudflare.binary` | empty | the `cloudflared` binary. Empty looks on `PATH` |
| `cloudflare.enabled` | false | the `cloudflared` daemon's switch. Tunnel sets it: on exactly while a Cloudflare target is recorded. Don't set it by hand; `moxi tunnel repair` sets it right |

For Cloudflare you create the tunnel and its DNS routes yourself, with `cloudflared tunnel
create` and `cloudflared tunnel route dns`, then set the id and the credentials path.
Tunnel writes one `cloudflared.yml` in its state directory, 0600, covering every Cloudflare
target, and runs `cloudflared` on it as its one daemon, through the core's units. Removing
or forgetting a Cloudflare target takes its rule out of that file; the tunnel and its DNS
route in your Cloudflare account stay as they are.

## The doctor

`moxi doctor tunnel` shows these rows. The Tailscale rows are `off` while no Tailscale
target is recorded, and the doctor then runs no `tailscale` command.

| row | ok when | otherwise |
|---|---|---|
| record | `applied.json` reads, in a format this version writes | `fail`, naming the fix |
| declaration `<entry>` | (shown only for a bad one) | `fail`: the entry and what is wrong with it |
| tailscale CLI | found, version 1.52 or later | `fail`, with the install link |
| certificate renewal | version 1.102.5 or later, which renews certificates in the background | `warn`: update Tailscale |
| logged in | Tailscale is logged in and running | `fail`: `tailscale up` |
| HTTPS certificates | MagicDNS on, a certificate domain, HTTPS allowed | `fail`: the admin console step |
| Funnel allowed | Funnel and its ports allowed; only while a public Tailscale target is recorded | `fail`: the policy file's node attribute |
| may change serve config | Linux: you are Tailscale's operator, or root | `fail`: `sudo tailscale set --operator=$USER` |
| shields up | shields are down; only while a public Tailscale target is recorded | `fail`: Funnel can't take connections |
| `<target>` | the live config matches the record | `fail`, naming `repair`, `remove`, or for a clash the two settings |
| `<target>` answers | something listens on its loopback port | `warn`, naming the module: the address answers 502 |
| cloudflare | `cloudflared`, the tunnel id and the credentials are in place; only while a Cloudflare target is recorded | `fail`, with the fix |
| cloudflare in step | `cloudflared.yml`, the switch, the unit and the record agree | `fail` when the file differs from the record, `warn` when only the switch or the unit does; each names `repair` |

The doctor gives tunnel ten seconds. Tunnel keeps to eight: each `tailscale` read waits at
most 2 s and each connect 0.5 s. When tailscale does not answer, that row fails and the rows
that needed it say "not checked", so a hung tailscaled never costs the whole report.

## Uninstall

`moxi uninstall tunnel` removes every address tunnel applied before anything else goes, and
turns the Cloudflare daemon off. When an address can't be removed (its live config was
changed by hand, tailscale can't be reached), the uninstall stops before changing anything
and names each one, with the command that removes it by hand and
`moxi tunnel remove <target> --forget` for one you want to keep. A Tailscale removal that
fails part way through also stops the uninstall and names what is left, but by then the
Cloudflare addresses are already gone.
`moxi uninstall tunnel --skip-teardown` goes past it: the Tailscale addresses then stay
live, and the uninstall names each with its hand command. The record stays, so reinstalling
tunnel takes them back as they were. With `--purge` too, the record goes once you say yes;
the live addresses then read as config Moxi did not apply.

Uninstalling another module removes that module's addresses without asking. One that can't
be removed stays in the record, and the doctor shows it until `moxi tunnel remove` takes it
out.

## What tunnel never does

- It never authenticates a request, and never adds an identity of its own.
- It never changes Tailscale config it did not apply, unless you confirmed a `--replace` of
  that port, and then it keeps a copy first.
- It never runs `tailscale serve reset`, which clears every entry, its own and others'.
- It never opens or copies the Cloudflare credentials file, and never changes the tunnel or
  its DNS routes in your Cloudflare account.
- It never holds a lock while it asks you, and never runs anything of its own for
  Tailscale: tailscaled keeps the config across reboots.
- It carries HTTP only. There is no media or UDP path.

## A hand check, after you install it

Run this yourself, on your own box, with a target whose reach is `public` and a phone off
your tailnet (on mobile data):

1. Run `moxi tunnel status --check`, and save `tailscale serve status --json` as the before
   state.
2. Run `moxi tunnel add <target>`, read the warnings, and confirm.
3. From the phone, open the URL `moxi tunnel status` prints: the service answers, and
   refuses a call without its sign-in.
4. Run `moxi tunnel remove <target>`, then compare `tailscale serve status --json` with the
   before state: the Funnel's `TCP`, `Web` and `AllowFunnel` keys are gone, and nothing else
   changed.
5. If a step fails, or you stop part way, end with `tailscale funnel --https=<port> off` for
   the port step 2 printed, and check that `tailscale serve status --json` no longer lists
   it.

## Platforms

| | Linux | macOS | Windows |
|---|---|---|---|
| `tailscale` CLI | on `PATH` | the app's CLI in `/Applications/Tailscale.app/Contents/MacOS/Tailscale`, or `PATH` | `C:\Program Files\Tailscale\tailscale.exe`, or `PATH` |
| who may change serve config | root, or the operator set once with sudo | the app runs as you | may need an Administrator prompt |
| `cloudflared` daemon | a systemd user unit | a LaunchAgent | a Task Scheduler task |

The `tailscale.binary` setting overrides the search.

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/main/packages/tunnel/LICENSE).
