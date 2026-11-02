"""The announcement of Moxi's tunnel module, which moxi.modules reads.

It offers interface 1, the API in `moxi_tunnel.api`: a declared target's address, read
from the record tunnel keeps of what it applied.
"""

ANNOUNCES = 1
INTERFACES = frozenset({1})
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
