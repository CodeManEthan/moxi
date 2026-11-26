# Changelog

## Unreleased

- `Add a repo` reads the typed folder's plan through moxi-ui's `readLater`, so the page
  root's `data-reading` mark counts that read from the keystroke until the plan is drawn,
  and a test's press waits for it. A user sees no change. The `ui` extra asks for moxi-ui
  0.2.8 or later.
- The page's `Reading…` line, while its first read is out, shows once that read has
  been out 400 ms in every browser. In Firefox it could stay blank until something
  else redrew the page, since the page checked the wait against `Date.now()`, which
  Firefox coarsens. It now takes the wait from moxi-ui's `WaitClock`, so the `ui` extra
  asks for moxi-ui 0.2.6 or later.
- `repo/commit-rules` takes a `tag-check-trailers` setting, `true` or `false`. When true,
  the repo's tag patterns are matched against every line of the trailer block too,
  continuation lines included; off, as before, the trailers are not read for tags. The
  push piece's check of commit-rules at push and `moxi repo rules` read it the same way;
  `rules` prints `- no text matching, trailers included:` for each pattern where it is on.
- `repo/commit-proof` drops the `Commit-Check` lines it read from a message it passes,
  with the lines continuing them, so the line is not in the commit. On a clean merge only
  the lines holding the merge's own value go. A refused message, and a commit repo's
  committer makes, keep theirs.
- The git hooks page is redrawn on ui's row element. `Hooks` gathers what needs you
  (problems, failing auto-commits, open records) under one verdict line; `Repos` has a
  row per repo that opens in place with its events, today's records, auto-commit, rules,
  git and commands; `Pieces` lists every offered piece. From the page you can add pieces
  to a repo, add a repo, turn a piece on and refresh a repo. Each act first shows all it
  would write, and is offered only when it would change something. Turning a piece off
  and removing one stay commands to copy. repo's `ui` extra needs moxi-ui 0.2.3 or later.
- `moxi_repo.api.plan_add`, `plan_enable` and `plan_refresh`: what `add`, `enable` and
  `refresh` would write in a repo, read without writing or taking a lock, with a digest
  of it. The three verbs take `expect`, that digest, and write only if the plan they make
  again under the repo's lock has it; otherwise they write nothing and raise
  `moxi_repo.api.Changed`, which holds the new plan. Given `within`, a plan or verb
  refuses a repo outside that folder.
- Every command that writes a repo's hooks holds a lock of that repo's own
  (`moxi-repo/hooks.lock` in its common git dir). One that waits more than 10 seconds for
  another exits 75 and writes nothing.
- A `moxi_repo.api.Failure` carries `reason`, one of `REFUSAL_CODES`, and `partial`, true
  when it came after the first write. A problem (`moxi_repo.api.Problem`) carries `code`
  (one of `PROBLEM_CODES`), `piece`, `event` and `refresh`, true where `git-hooks refresh`
  fixes it.
- `git-hooks list --json` gives each event's `existing_hook`, the user's own git hook
  there, and each repo's `own_hooks`. A repo git can't read names git's reason.
- A folder that isn't there now reads `no git work tree at <path>: not a directory`
  (was `no git work tree at <path> is not a directory`), as git's own reasons read.
- `moxi repo specs --json` gives a run of failing passes' start and count
  (`error_since`, `error_passes`), as for refused and index-busy passes, and
  `index_stale` once git's index lock has been held for five passes in a row. The
  committer's records count passes in words that agree (`1 pass`, `2 passes`), and say
  when in a local clock time (`since 14:05`, `since Nov 17 14:05` on an earlier day),
  not a UTC stamp. A refused pass's record no longer counts the passes: the spec's
  `refused_passes` does.

- Every setting carries page fields, which `moxi config repo show --json` prints and
  the settings page draws: a plain label, a line where the label can't carry it, and
  words for each git hook mode. repo needs moxi 0.1.9 or later.
- `moxi_repo.commit_rules_problems(root, text)`: the commit-rules piece's rules applied
  to a message after the commit was made, with the piece's settings in the repo at
  `root`; empty where the piece is not registered or is disabled. Every line of the
  message is judged, comment-prefixed or below a scissors line alike.
- `moxi_repo.api` also hands out `interpreter`, `resolve_project`,
  `ProjectUnavailable`, `resolve_repo` (a repo's paths from a path in any of its work
  trees), `NotARepo`, `comment_prefix` and `quote_arg`.
- `moxi_repo.api.status` and `git-hooks list --json` give each event's `command`, the text
  git runs for it as repo wrote it, so a caller reads no repo file format itself.
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
