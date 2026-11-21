# Changelog

## Unreleased

- `tools.withheld` lists tools the door serves to no grant: they are left out of
  `tools/list`, a call to one answers as an unknown tool, and `moxi connector status` and
  the doctor's providers row name each one. Read at the connector's start.
- Every tool answer, refusals included, leaves the door with control and format
  characters removed from its text and from every string of its structured value: C0
  controls but newline and tab, DEL, C1 and Unicode format characters (bidi controls,
  zero-width characters, tag characters, the byte order mark). The provider interface's
  doc says a provider that redacts hands over text its last redaction saw in final form:
  it redacts the raw text, removes those characters, then redacts again.
- Every setting carries page fields, which `moxi config connector show --json` prints
  and the settings page draws: a plain label, a line where the label can't carry it,
  and the unit of a sign-in's life. The connector needs moxi 0.1.9 or later.
- `enabled` is marked `terminal`, naming `moxi connector enable` and `disable`: the
  settings page shows it as a word with that command beside it, and its routes refuse to
  write it.

## 0.1.0 (2026-11-08)

- `moxi connector on` and `off` are now `moxi connector enable` and `disable`, with no
  alias.

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
