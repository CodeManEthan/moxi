# Changelog

## 0.2.0 (2026-11-08)

The first public release.

- `moxi tunnel status`, `add`, `remove` and `repair` give a module's loopback service an
  address through Tailscale Serve (your own devices), Tailscale Funnel (public) or a
  Cloudflare named tunnel (either). Tunnel records what it applied in `applied.json` and
  removes only that.
- Interface 2: declarations in `moxi.tunnel.targets` may name `upstream`, for a target on
  a Unix socket or one that needs its owner ready (`NotReady`), and `retired`. Tunnel
  keeps a list of retired origins and gives a socket target a port that is not on it.
  `Address.form` and `Address.retired` are new.
- Each Cloudflare rule carries `originRequest: httpHostHeader: <hostname>`.
- `moxi doctor tunnel` rows for the record, the declarations, Tailscale, Cloudflare and
  each target.
- `moxi tunnel --help` names Cloudflare beside Tailscale.
- Linux; macOS and Windows are untested.
- Requires moxi 0.1.6 or later.
- The README installs from PyPI, and its links work on PyPI.

### From an earlier, unpublished moxi-tunnel 0.1

Version 0.1 was never published; these notes matter only to a machine that ran it.

- `upstream` and `retired` are refused in `moxi.tunnel`, and so is a target declared in
  both groups, since tunnel 0.1 reads only `moxi.tunnel` and would apply such a target as
  a plain loopback port.
- After the upgrade, the doctor's `cloudflare in step` row fails, `cloudflared.yml` being
  behind the record, until you run `moxi tunnel repair`.
