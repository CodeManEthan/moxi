"""The gateway: the UI on a phone, through the tunnel, as a second listener of the shell.

- `endpoint`: the endpoint's kind (`unix` or `tcp`), resolved once at start; the socket
  path and its check; the runtime-directory check; the `gateway.hold` file.
- `provider`: the provider check, which admits only the provider's own process on Linux.
- `address`: the address rule, what one read of the tunnel's address does to the profile.
- `mount`: whether the phone's confirmed acts are mounted, and the reason; `gateway.acts`.
- `listener`: `Gateway`, which holds the endpoint, serves on it and ticks every 2 s, and
  `Parts`, what the gateway's routes, sessions and audit plug in.

The shell builds one `Gateway` and runs it beside its own listener in the same process.
Nothing here imports `webauthn` or tunnel at import time: tunnel is reached through
`moxi.modules.load("tunnel", interface=2)`.
"""

from .listener import Gateway, Parts

__all__ = ["Gateway", "Parts"]
