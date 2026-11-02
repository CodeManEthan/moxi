"""The warning lines an act shows before it asks, once, for a yes.

Every act that changes a provider's config asks once, with the lines that apply, before it
takes a lock. Any act that puts an entry on a host where that entry was not before is an add
for these lines, whatever verb runs it: an add, a provider switch, and a repair of an entry
whose machine was renamed or moved to another tailnet profile. Its lines are those of an add
on that host, with that entry's exposure.

When the user confirms an act that showed the certificate-log line, the host goes into the
record's `warned_ct`, and a later add shows that line again only for a host not in it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence


def ct_line(host: str) -> str:
    return (f"HTTPS certificates publish the machine name {host} and the tailnet name in "
            "public Certificate Transparency logs. Rename the machine first if its name "
            "says something you'd rather not publish.")


PAIR_LINE = ("Paired devices and saved settings keep this address. Renaming the machine or "
             "switching provider later changes it, and every paired device then pairs "
             "again. Name the machine before you pair.")

REPAIR_LINE = "The old address stops working, and every paired device pairs again."


def internet_line(url: str, module: str) -> str:
    return (f"Anyone on the internet can reach {url}. {module} checks every call itself, "
            "so its sign-in is all that stands in front of it.")


def funnel_line(host: str) -> str:
    return (f"{host} gets public DNS records. Funnel is in beta, and its bandwidth limits "
            "are not published.")


def cloudflare_line(hostname: str) -> str:
    return (f"Cloudflare ends TLS and sees every request in plain text. {hostname} is in "
            "public DNS. Turn off Rocket Loader, email obfuscation and auto-minify for "
            "this zone; Moxi can't check those.")


OWN_DEVICES_LINE = ("This target asked for your own devices only, and over Cloudflare it is "
                    "on the internet.")

SWITCH_LINE = REPAIR_LINE


def replace_line(port: int, copy: Mapping, where: str) -> str:
    shown = json.dumps(copy.get("parts", {}), indent=2, sort_keys=True)
    return (f"The foreign entry on port {port}, below, is removed, and Moxi will not bring "
            f"it back. A copy is kept in {where}.\n{shown}")


def add_lines(*, provider: str, host: str, url: str, module: str, reach: str,
              exposure: str, warned: Sequence[str]) -> tuple[list[str], bool]:
    """The lines of an add of an entry on `host`, and whether the certificate-log line is
    among them."""
    lines = []
    ct = provider == "tailscale" and host not in warned
    if ct:
        lines.append(ct_line(host))
    lines.append(PAIR_LINE)
    if exposure == "internet":
        lines.append(internet_line(url, module))
    if provider == "tailscale" and reach == "public":
        lines.append(funnel_line(host))
    if provider == "cloudflare":
        lines.append(cloudflare_line(host))
        if reach == "own-devices":
            lines.append(OWN_DEVICES_LINE)
    return lines, ct


__all__ = ["ct_line", "PAIR_LINE", "REPAIR_LINE", "SWITCH_LINE", "internet_line",
           "funnel_line", "cloudflare_line", "OWN_DEVICES_LINE", "replace_line",
           "add_lines"]
