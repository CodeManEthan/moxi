"""The announcement of Moxi's tunnel module, which moxi.modules reads.

It offers interfaces 1 and 2, the API in `moxi_tunnel.api`: a declared target's address,
read from the record tunnel keeps of what it applied. Interface 2 adds `NotReady`,
`Address.form`, `Address.retired` and the declarations in `moxi.tunnel.targets`, whose
`upstream` and `retired` tunnel honours.
"""

ANNOUNCES = 1
INTERFACES = frozenset({1, 2})
API = "moxi_tunnel.api"
#: `moxi tunnel`: status, add, remove and repair.
CLI = "moxi_tunnel.cli:main"
SUMMARY = ("An address something off the box can reach, for a loopback service: Tailscale "
           "Serve, Tailscale Funnel or a Cloudflare named tunnel.")
OPTIONAL = {}
GATED = {}
INTERNAL_ENV = {}
#: The config schema; every key is read at its use.
SETTINGS = "settings.toml"
#: The module's whole loader over a config text: the schema, then the tunnel id, the
#: paths and the metrics port.
CONFIG_CHECK = "moxi_tunnel.settings:check"
SETUP = None
SEGMENT = None
#: The Cloudflare named tunnel's daemon, behind its switch: tunnel turns the switch on
#: exactly while a Cloudflare target is recorded. Tailscale needs no daemon of Moxi's.
DAEMONS = {"cloudflared": {"command": "moxi-tunnel-cloudflared",
                           "doc": "the Cloudflare named tunnel",
                           "switch": "cloudflare.enabled"}}
#: The daemon's health: `GET /ready` on cloudflared's metrics port.
HEALTH = {"cloudflared": "moxi_tunnel.cloudflare:health"}
#: Run first by `moxi uninstall tunnel`: removes every address tunnel applied, or stops the
#: uninstall naming each one it could not remove and the command that removes it.
TEARDOWN = "moxi_tunnel.uninstall:teardown"
#: tunnel's doctor rows: Tailscale's setup (off while no Tailscale target is recorded),
#: each address against its live config, each target's loopback port, and Cloudflare's.
DOCTOR = "moxi_tunnel.doctor:rows"
#: What tunnel keeps, for `moxi paths tunnel`, the doctor's checks and `moxi uninstall
#: --purge`. Each is made by its first write. The record of what tunnel applied, its event
#: log and the copies of foreign config a replace took over can't be made again;
#: cloudflared.yml is rendered from the record.
STORES = {
    "applied": {"root": "state", "path": "applied.json", "kind": "file", "private": True,
                "retention": "keep", "lazy": True, "rebuildable": False},
    "events": {"root": "state", "path": "events.jsonl", "kind": "jsonl", "private": True,
               "retention": "2M", "lazy": True, "rebuildable": False},
    "displaced": {"root": "state", "path": "displaced", "kind": "dir", "private": True,
                  "retention": "keep", "lazy": True, "rebuildable": False},
    "cloudflared": {"root": "state", "path": "cloudflared.yml", "kind": "file",
                    "private": True, "retention": "keep", "lazy": True,
                    "rebuildable": True},
}
