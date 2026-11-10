# Changelog

## Unreleased

- New command `moxi repo rules [PATH]`: what a committer in a repo must know. It prints
  the trailers the repo refuses and the text its messages may not hold, from the
  commit-rules piece's settings, then the repo's own rule lines, then the commit-proof
  piece's `Commit-Check` line for what is staged in the work tree PATH is in. A repo with
  neither piece says it has no commit rules. It exits 0 on any repo.
- The commit-rules piece has a third setting, `rule`: the repo's own rules for a
  committer, free text, any number of values (`--set 'rule=TEXT'`). The piece checks none
  of them; `moxi repo rules` prints them.
- New piece `repo/commit-proof` (commit-msg, fail closed, order 20): it refuses a commit
  whose message lacks the `Commit-Check` line `moxi repo rules` printed for the tree being
  committed, or holds two, a malformed one or one for another tree or another repo. A
  commit the committer makes, one made during a rebase, cherry-pick or revert, and a
  clean merge pass without the line. Its setting `salt` is written when the piece is
  first registered in a repo; a `--set salt=` value that isn't 16 hex characters is
  refused. It is in no repo until you add it.
- New config key `commit_proof.everywhere` (bool, default false): `git-hooks add` and
  `refresh` register the commit-proof piece in each repo they touch that lacks it.
- `moxi_repo.api`: `rules(path)`; `everywhere()` takes a piece id; `EVERYWHERE` maps each
  of repo's own pieces to its config key; a `RULE_LINES` function is now called with the
  keywords `top` (the work tree asked about) and `enabled`. `moxi_repo.gitconf` gains
  `work_tree(path)`.

## 0.1.2 (2026-11-08)

- `moxi repo commit --wait` is now `--wait-s`, with no alias.
- Saved spec keys `min-interval` and `push-backoff` are now `min-interval-s` and
  `push-backoff-s`; the page and `--json` spec fields are `min_interval_s` and
  `push_backoff_s`. A saved spec that still has an old key is refused, naming the new
  one, until it is saved again.
- Public names renamed for the naming rules (callers passing the old keyword or reading
  the old name must change): `moxi_repo.api.changes`: `staged` is now keyword-only;
  `moxi_repo.api.changes`: `range` is now `rev_range`; `moxi_repo.api.changes`:
  `timeout` is now `timeout_s`; `moxi_repo.api.commit`: `wait` is now `wait_s`;
  `moxi_repo.api.commit_saved`: `wait` is now `wait_s`; `moxi_repo.api.git`: `input` is
  now `stdin_data`; `moxi_repo.api.git`: `timeout` is now `timeout_s`;
  `moxi_repo.api.HookContext.git`: `input` is now `stdin_data`;
  `moxi_repo.api.HookContext.git`: `timeout` is now `timeout_s`;
  `moxi_repo.api.PrepareContext.git`: `input` is now `stdin_data`;
  `moxi_repo.api.PrepareContext.git`: `timeout` is now `timeout_s`;
  `moxi_repo.api.remove_spec`: `wait` is now `wait_s`; `moxi_repo.api.save_spec`: `wait`
  is now `wait_s`; `moxi_repo.api.Spec`: `min_interval` is now `min_interval_s`;
  `moxi_repo.api.Spec`: `push_backoff` is now `push_backoff_s`.

The first public release.

- repo needs moxi 0.1.6 or later.
- The package metadata gains the README as its long description, keywords and
  classifiers.
- The README installs from PyPI, says Linux is supported and macOS and Windows untested,
  and that the Windows git hook path has never been run. The README's links work on
  PyPI.
- New extra `projects`, for project keys.

## 0.1.1 (2026-11-07)

- The `ui` extra needs moxi-ui 0.2, where the page-server base now lives as
  `moxi_ui.loopback`.
