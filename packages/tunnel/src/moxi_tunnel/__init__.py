"""Moxi's tunnel module: an address something off the box can reach, for a loopback
service, through Tailscale Serve, Tailscale Funnel or a Cloudflare named tunnel.

Other modules call it through `moxi_tunnel.api`, once `moxi.modules.available("tunnel")`
returns true.
"""
