"""The connector's one public origin O, every value derived from it, and the rule that
swaps them.

O is `https`, a host and a port. Every value that names the public address comes from O
alone, never from a copy kept elsewhere: the Host gate, the SDK's DNS-rebinding allowed
hosts, the issuer (O), the resource (`O/mcp`), the metadata documents' endpoints, the
`resource_metadata` URL of the 401 challenge, the sign-in page's `Origin` check and its
`form-action`. The process builds its whole app from O (`moxi_connector.app.build_app`),
and a different O builds a new app that replaces the old one whole.

Where O comes from (`read`), at start and every `POLL` seconds:

- `public_url` when it is set, read anew each time;
- else tunnel's address of `connector/mcp`, while its state is `on` and its exposure is
  `internet`;
- else none.

No forwarded header ever decides anything here: O comes from the config and tunnel's
record, never from a request.

`Watch` holds the swap rule: a different O swaps at once; a read that finds no O swaps to
the no-origin app only when the read before it found none too, so a tunnel state that
drops for one poll swaps nothing.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

log = logging.getLogger("moxi.connector.origin")

#: Seconds between two reads of O.
POLL = 30.0
#: The ports Claude's connector service is known to reach. A port outside this set is
#: warned about, never refused.
REACHED_PORTS = frozenset({443})
#: The tunnel target the connector declares.
TARGET = ("connector", "mcp")
#: The MCP endpoint's path on O.
MCP_PATH = "/mcp"
#: The protected-resource metadata document's path (RFC 9728, for the resource `O/mcp`).
PRM_PATH = "/.well-known/oauth-protected-resource/mcp"
#: The authorization-server metadata document's path (RFC 8414).
ASM_PATH = "/.well-known/oauth-authorization-server"

_LABEL = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)")


class BadOrigin(ValueError):
    """A `public_url` the connector can't serve under; the text says why."""


@dataclass(frozen=True)
class Origin:
    """One public origin: `https://<host>[:<port>]`, the host lower case."""

    host: str
    port: int = 443

    @property
    def url(self) -> str:
        """The origin as a browser writes it: no port for 443, no trailing slash."""
        return f"https://{self.host}" if self.port == 443 else f"https://{self.host}:{self.port}"

    @property
    def issuer(self) -> str:
        return self.url

    @property
    def resource(self) -> str:
        return self.url + MCP_PATH

    @property
    def resource_metadata_url(self) -> str:
        return self.url + PRM_PATH

    @property
    def authorization_endpoint(self) -> str:
        return self.url + "/authorize"

    @property
    def token_endpoint(self) -> str:
        return self.url + "/token"

    @property
    def registration_endpoint(self) -> str:
        return self.url + "/register"

    @property
    def revocation_endpoint(self) -> str:
        return self.url + "/revoke"

    @property
    def page_origin(self) -> str:
        """What the sign-in page's POST must carry as `Origin`, when it carries one."""
        return self.url

    @property
    def allowed_hosts(self) -> tuple[str, str]:
        """The `Host` values the listener admits: the host, and the host with its port."""
        return (self.host, f"{self.host}:{self.port}")

    def admits_host(self, value: str | None) -> bool:
        """Whether a request's `Host` header names this origin: its host, with or without
        its port, in any case."""
        return value is not None and value.strip().lower() in self.allowed_hosts

    def challenge(self) -> str:
        """The `WWW-Authenticate` value of a 401 on `O/mcp`."""
        return f'Bearer resource_metadata="{self.resource_metadata_url}"'

    def form_action(self, redirect_uris: Iterable[str]) -> str:
        """The sign-in page's CSP `form-action`: itself, and the origin of each allowed
        redirect URI, since Chromium applies it to the redirect after the form's POST."""
        seen = ["'self'"]
        for uri in redirect_uris:
            parts = urlsplit(uri)
            if parts.scheme and parts.netloc:
                origin = f"{parts.scheme}://{parts.netloc.lower()}"
                if origin not in seen:
                    seen.append(origin)
        return " ".join(seen)

    def port_warning(self) -> str | None:
        """The port row's warning when O's port is outside `REACHED_PORTS`, else None."""
        return port_warning(self.port)


def port_warning(port: int, reached: Iterable[int] = REACHED_PORTS) -> str | None:
    """The text of the port row's `warn` for `port`, or None for a port Claude is known to
    reach."""
    known = sorted(reached)
    if port in known:
        return None
    ports = ", ".join(str(p) for p in known)
    return (f"Claude's connector service is known to reach only ports {ports}. Claude may "
            "say it couldn't reach the server.")


def parse(text: str) -> Origin:
    """The origin a `public_url` names. Raises `BadOrigin` unless it is `https://` with a
    DNS name, an optional port, and no path, query, fragment or user part."""
    text = text.strip()
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as e:
        raise BadOrigin(f"{text!r} is not a URL: {e}") from None
    if parts.scheme.lower() != "https":
        raise BadOrigin(f"{text!r} is not https://")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise BadOrigin(f"{text!r} carries a user part")
    if parts.path not in ("", "/") or parts.query or parts.fragment or text.endswith("?"):
        raise BadOrigin(f"{text!r} has a path, a query or a fragment; give the origin alone")
    host = (parts.hostname or "").rstrip(".")
    if not is_dns_name(host):
        raise BadOrigin(f"{text!r} doesn't name a DNS host")
    if port is not None and not 0 < port < 65536:
        raise BadOrigin(f"{text!r} has no usable port")
    return Origin(host, 443 if port is None else port)


def is_dns_name(host: str) -> bool:
    """Whether `host` is a DNS name of two labels or more, not an IP address."""
    host = host.lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return False
    labels = host.split(".")
    return (len(host) <= 253 and len(labels) >= 2 and not labels[-1].isdigit()
            and all(_LABEL.fullmatch(label) for label in labels))


def from_address(address) -> Origin | None:
    """O from a tunnel `Address`, when it is `on` and reaches the internet."""
    if address is None or address.state != "on" or address.exposure != "internet":
        return None
    host = str(address.host).lower().rstrip(".")
    if not is_dns_name(host):
        return None
    return Origin(host, int(address.port))


def tunnel_address():
    """tunnel's address of `connector/mcp`, or None while tunnel is absent or has none.

    tunnel is reached only through `moxi.modules.load`, so the connector works without
    it. A record that can't be read reads as no address, logged."""
    from moxi import modules

    found = modules.load("tunnel", interface=1)
    if found.state != modules.PRESENT or found.api is None:
        return None
    try:
        return found.api.address(*TARGET)
    except Exception as e:  # noqa: BLE001 - an unreadable record is no address
        log.warning("tunnel's record couldn't be read: %s", e)
        return None


def read(public_url: str, tunnel: Callable[[], object] = tunnel_address) -> Origin | None:
    """O now: `public_url` when set, else tunnel's address, else None. A `public_url` that
    doesn't parse reads as no origin, logged: `moxi config set` refuses one, but the file
    may be edited by hand."""
    if public_url and public_url.strip():
        try:
            return parse(public_url)
        except BadOrigin as e:
            log.warning("public_url is not served: %s", e)
            return None
    return from_address(tunnel())


class Watch:
    """The swap rule over successive reads of O.

    `see(found)` takes one read and answers what the running app should become: the
    `Origin` to build a new app for, `NONE` to swap to the no-origin app, or None to keep
    the running one. `current` is the origin the running app was built with."""

    #: `see`'s answer: swap to the no-origin app.
    NONE = "none"

    def __init__(self, current: Origin | None):
        self.current = current
        self._missed = current is None

    def see(self, found: Origin | None):
        if found is None:
            if self.current is None:
                return None
            if self._missed:
                self.current = None
                return self.NONE
            self._missed = True
            return None
        self._missed = False
        if found == self.current:
            return None
        self.current = found
        return found


__all__ = ["ASM_PATH", "BadOrigin", "MCP_PATH", "Origin", "POLL", "PRM_PATH",
           "REACHED_PORTS", "TARGET", "Watch", "from_address", "is_dns_name", "parse",
           "port_warning", "read", "tunnel_address"]
