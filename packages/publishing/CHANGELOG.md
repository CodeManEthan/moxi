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
