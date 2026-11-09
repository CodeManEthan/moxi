# Changelog

## 0.1.2 (2026-11-08)

- `moxi repo commit --wait` is now `--wait-s`, with no alias.
- Saved spec keys `min-interval` and `push-backoff` are now `min-interval-s` and
  `push-backoff-s`; the page and `--json` spec fields are `min_interval_s` and
  `push_backoff_s`. A saved spec that still has an old key is refused, naming the new
  one, until it is saved again.
- Public names renamed for the naming rules (callers passing the old keyword or reading
  the old name must change): `moxi_repo.committer.PrepareContext.git`: `input` is now
  `stdin_data`; `moxi_repo.committer.PrepareContext.git`: `timeout` is now `timeout_s`;
  `moxi_repo.committer.Spec`: `min_interval` is now `min_interval_s`;
  `moxi_repo.committer.Spec`: `push_backoff` is now `push_backoff_s`;
  `moxi_repo.committer.commit`: `wait` is now `wait_s`;
  `moxi_repo.committer.commit_saved`: `wait` is now `wait_s`;
  `moxi_repo.committer.remove_spec`: `wait` is now `wait_s`;
  `moxi_repo.committer.save_spec`: `wait` is now `wait_s`; `moxi_repo.gitrun.changes`:
  `staged` is now keyword-only; `moxi_repo.gitrun.changes`: `range` is now `rev_range`;
  `moxi_repo.gitrun.changes`: `timeout` is now `timeout_s`; `moxi_repo.gitrun.git`:
  `input` is now `stdin_data`; `moxi_repo.gitrun.git`: `timeout` is now `timeout_s`;
  `moxi_repo.pieces.HookContext.git`: `input` is now `stdin_data`;
  `moxi_repo.pieces.HookContext.git`: `timeout` is now `timeout_s`.

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
