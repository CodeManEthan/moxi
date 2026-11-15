# Changelog

## 0.1.0 (unreleased)

The first version.

- The public flag in a repo's git config, and `moxi publishing enable`, `disable` and
  `list`.
- The message piece, `publishing/message`: drops the trailers a repo names, leaves those
  a later git hook reads, and refuses a message holding a credential, a private name or a
  provenance pointer.
- The six provenance rules, each switched off through `provenance.rules`.
