# moxi-tunnel

Moxi's tunnel module gives a loopback service an address that something off the box can
reach. A module declares a target, a loopback port held in one of its settings and a reach
(`own-devices` or `public`), and `moxi tunnel add <target>` turns it on through Tailscale
Serve, Tailscale Funnel or a Cloudflare named tunnel. Tunnel records what it applied, in
`applied.json` in its state directory, and removes only that.

Tunnel authenticates nothing: the module behind the address checks every call itself.
