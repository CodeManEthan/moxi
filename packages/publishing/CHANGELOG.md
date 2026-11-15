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
- The push piece, `publishing/push`: refuses a push that rewrites a branch, deletes
  `main`, a protected branch or one not merged, moves a release tag, moves another ref no
  `free-ref` pattern names, or creates more than three tags; and judges every commit and
  tag object new to the remote as a commit would have been judged. A passing push is
  logged.
- `moxi publishing scan`: a commit's whole tree and its path names, judged the same way;
  `--ci` without the private names.
- The six provenance rules, each switched off through `provenance.rules`.
