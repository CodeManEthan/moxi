# Changelog

## 0.1.0 (2026-11-08)

The first public release.

- An MCP server for the Claude apps' custom connectors: a loopback listener behind its
  `enabled` switch, an OAuth sign-in that needs a one-time code minted at your own
  terminal, grants that last `refresh_days` after their last refresh, and a call log.
- `moxi connector on`, `off`, `status`, `code`, `grants`, `revoke`, `check` and `log`.
- Interface 1, `moxi_connector.provider`: the entry-point group
  `moxi.connector.providers` through which other modules give it tools, standard library
  only.
- The public address comes from the tunnel module (the `tunnel` extra) or from
  `public_url`.
- `docs/security.md` says who can sign in, what the mint gate stops and what it doesn't,
  and what crosses the door.
- Linux only.
- Requires moxi 0.1.6 or later.
- The README covers installing with comms, writing a provider, and says that Claude
  reaching only port 443 is an observation. Its links work on PyPI.
