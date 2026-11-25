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
  `free-ref` pattern names, or creates more than one tag; and judges every commit and
  tag object new to the remote as a commit would have been judged. A passing push is
  logged.
- `moxi publishing scan`: a commit's whole tree and its path names, judged the same way;
  `--ci` without the private names; `--from REV` also reads each commit of the range
  whole (message and headers), and a tag named by `--rev` has its message and tagger
  read.
- The six provenance rules, each switched off through `provenance.rules`.
- Release tags, `<distribution>-v<version>`, read against the workspace members; the
  `[tool.moxi-publishing]` release settings (`not-ready`, `check-workflow`, `check-job`);
  and the tag gate's seven checks, run by `moxi publishing tag`, the push piece (which now
  lets a release tag be created when it passes) and `moxi publishing ci gate`.
- `moxi publishing tag`: the gate at main's tip, then the annotated release tag.
- `[tool.moxi-publishing]` may keep a `ready` list; then every member must be on exactly
  one of it and `not-ready`, and the gate counts one on neither or both as not ready.
- `moxi publishing ci siblings`: the release test from a fresh venv, at the newest and
  the lowest published siblings, with the site-packages check. A package whose `dev`
  group names `moxi-dev` gets it from the checkout without its requirements, so the
  test fence its conftest loads is there.
- `moxi publishing workflows write`: the public scan and release workflow files. The
  release workflow's upload job runs only pinned actions, never the repo's code; a
  verify job before it runs the gate with the build's files.
- `moxi publishing github show` and `github apply`: the rulesets, secret scanning and
  `pypi` environment a public repo expects of GitHub, read with `gh` and printed field
  by field, or written. `moxi-main` requires the root `pyproject.toml`'s `check-job`
  from the GitHub Actions app, and both commands say what that asks of the CI workflow;
  the tag-creation ruleset is `moxi-tags-create`; a rule is compared with its
  parameters, not by type alone, and a rewrite keeps the ones publishing doesn't set.
- `moxi doctor publishing`: fails a hook command running another interpreter than the
  recorded one, and a later commit-msg hook while `drop-trailer` names `Commit-Check`;
  warns on an unset GitHub repo, a workflow file unlike its template, an empty private
  names list and a push log that can't be written.
- `moxi publishing bump`: the next version, the dated CHANGELOG heading, the README
  links and `uv lock`, left unstaged.
- `moxi publishing ci notes`: a tag's CHANGELOG section.
- Every setting carries page fields, which `moxi config publishing show --json` prints
  and the settings page draws: a plain label, a line, and on `provenance.rules` a
  can-hurt mark on taking a rule away. publishing needs moxi 0.1.9 or later.
- The push piece's `standing-branch` setting (`ci/*`, say): a standing branch goes out
  only at the tip of the local branch its name names, may be forced, and is deleted
  whatever its tip; a push of only standing branches to the URL that reduces to
  `moxi-publishing.github` needs no word, while a malformed word still refuses it; the
  log names each ref's pattern; a bad pattern refuses every push, and `enable` refuses
  one. `moxi doctor publishing` fails a bad pattern or an unset GitHub repo beside one,
  and warns when no push URL of `origin` reduces to the GitHub repo.
- In a repo that sets `standing-branch`, a push of `main` to the URL that reduces to
  `moxi-publishing.github` passes only when GitHub has, at its new tip, a run of
  `check-workflow` from a push to a standing branch whose `check-job` passed in the run's
  latest attempt. A repo with no pattern, or a push to another URL, reads no CI.
- `[tool.moxi-publishing]` takes `full-jobs`, the jobs on other systems a release waits
  for: the tag gate's check 3 then passes only when, in the run from a push to main at
  the tag's commit where `check-job` passed, a job of exactly each name concluded
  `success` in the run's latest attempt. A name with no job, another conclusion, or a
  missing or empty `full-jobs` refuses, naming it; a job still running refuses with "wait
  for it"; each such job's conclusion goes in the gate's notes. `moxi publishing tag`,
  the push piece and `ci gate` all read it.
- `moxi publishing ci siblings` runs the suite with `MOXI_TEST_TIMED=hold`, over any value
  the job had, so a release's test run holds every timed test as the land check does;
  they run only in `moxi-dev timed`.
- Clean history, a repo's own choice apart from being public: `moxi publishing
  clean-history enable PATH` registers the message and content pieces, adds each key of
  the new `defaults.refused_trailers` setting (default `Round` and `Reconciles`) to repo's
  commit-rules piece, registering it where it isn't, and writes `moxi-publishing.clean`.
  `clean-history disable PATH` takes back only what enable added, and refuses in a public
  repo. The message and content pieces now run where the repo is marked clean or public,
  so a private clean repo drops `Claude-Session` too; registered with neither flag they
  refuse, naming both commands. `moxi doctor publishing` reads a clean repo's pieces.
- `moxi publishing enable` turns clean history on too, since a public repo always keeps
  it. `moxi publishing disable` removes the push piece and the public flag, and turns
  clean history off where enable turned it on (or the repo has no clean flag, as one
  enabled before clean history); a repo that kept a clean history before it was public
  keeps it.
- Push approval, a repo's own choice: `moxi publishing approval enable PATH --branch
  PATTERN...` registers the new pre-push piece `publishing/approval`, and `approval
  disable PATH` removes it. A push run inside an agent's session that creates, moves or
  deletes a branch a pattern names, or any tag, needs `MOXI_PUBLISHING_PUSH_WORD`
  matching `push.word_pattern`; a push from your own terminal or a Moxi unit needs none.
  It reads no public or clean flag. A passing push is logged in `pushes.jsonl` with the
  word and the agent's harness.
- The push piece's `word-required` setting and `defaults.word_required` are retired: the
  word is push approval's. A repo whose push piece still holds `word-required = true` has
  every push refused, naming `moxi publishing approval enable PATH --branch ...` and
  `moxi publishing enable PATH --set word-required=`; `enable` takes only that clearing
  form, and a config file that still sets the default is read without it. A standing
  branch no longer has a word clause: approval guards it only where a pattern names it.
  `moxi publishing list` shows every repo with clean history, push approval or the
  public flag, each with those three choices (`public`, `clean`, `approval` in
  `--json`); `moxi doctor publishing` fails a leftover `word-required = true` and warns on
  any other leftover value.
- `moxi projects add` asks publishing's two questions for a new project whose root is in
  a git repo (or one `--create` makes): keep the history publishable (suggested yes,
  which runs clean-history enable), and ask for approval before agents push to some
  branches (suggested no; a yes asks which, and an empty answer turns nothing on).
  Without a terminal it asks nothing and names both commands. The settings
  `project_add.clean_history` and `project_add.push_approval` (`ask`, `yes` or `no`,
  default `ask`) and `project_add.approval_branches` answer for the machine: with `yes`
  or `no` the add asks nothing for that choice and applies it. publishing contributes
  through projects' `moxi.projects` group, interface 3, and needs a projects that reads
  it.
