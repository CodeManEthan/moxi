# Changelog

## 0.1.0 (unreleased)

The first version.

- The public flag in a repo's git config, and `moxi publishing enable`, `disable` and
  `list`.
- The message piece, `publishing/message`: drops the trailers a repo names, leaves those
  a later git hook reads, and refuses a message holding a credential, a private name or a
  provenance pointer.
- The content piece, `publishing/content`: refuses a commit that adds a credential, a
  private name or a provenance pointer, reading every added line and new path name as git
  stores it, with the allowlists of the index; a merge is judged by its own lines.
- `moxi publishing scan`: a commit's whole tree and its path names, judged the same way;
  `--ci` without the private names.
- The six provenance rules, each switched off through `provenance.rules`.
