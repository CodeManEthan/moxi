"""Moxi's tunnel module: an address something off the box can reach, for a loopback
service, through Tailscale Serve, Tailscale Funnel or a Cloudflare named tunnel.

Other modules call it once `moxi.modules.available("tunnel")` returns true:
`address(module, name)` reads the record only, with no subprocess; `targets`, `add`,
`remove` and `check` are the rest, and a declaration's `upstream()` raises `NotReady`. The
names are exported lazily from `moxi_tunnel.api` and `moxi_tunnel.errors`.
"""

#: Each exported name -> the module it comes from.
EXPORTS = {**{name: "moxi_tunnel.api" for name in
              ("Address", "Target", "address", "targets", "add", "remove", "check")},
           "NotReady": "moxi_tunnel.errors"}

__all__ = sorted(EXPORTS)


def __getattr__(name):
    module = EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module 'moxi_tunnel' has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module), name)


def __dir__():
    return sorted({*globals(), *EXPORTS})
