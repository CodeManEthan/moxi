# moxi-publishing

Moxi's publishing module keeps private text out of a public git repo. You mark a repo
public, and three git hook pieces, run by Moxi's repo module, guard what it commits and
pushes. The pattern sets they read (credentials, and your private names) are Moxi's secrets
module's.

```sh
moxi publishing enable ~/src/app --github alice/app
moxi publishing list
moxi publishing scan ~/src/app
moxi publishing disable ~/src/app
```

## The public flag

`enable` writes `moxi-publishing.public = true` into the repo's own git config, then
registers the pieces `publishing/message` (commit-msg), `publishing/content` (pre-commit)
and `publishing/push` (pre-push). Each piece reads the flag first: a piece registered in a
repo without it refuses, since whoever registered it meant the repo to be guarded.
`disable` removes the pieces, then the flag. `list` shows every repo marked public with
each piece's state and settings.

`enable` warns when secrets' private names list is empty: add yours with
`moxi config secrets`.

## Settings

Each piece's settings live in the repo's git config, so the git hooks read nothing outside
the repo for them.

| piece | setting | values | what it is |
|---|---|---|---|
| message | `drop-trailer` | any number | Trailer keys removed at commit. |
| message | `pass-trailer` | any number | Trailer keys a later commit-msg git hook in this repo reads: left in place at commit. |
| push | `main` | one | The repo's main branch. |
| push | `protected` | any number | Branches beside `main` a push may never delete. |
| push | `word-required` | one | `true`: a push needs `MOXI_PUBLISHING_PUSH_WORD`. |
| push | `free-ref` | any number | Ref patterns outside branches and release tags a push may create, move or delete. |

Where `enable` first registers a piece, the piece's settings start from publishing's
config: `defaults.drop_trailers` (`Claude-Session` and `Commit-Check`), `defaults.main`
(`main`) and `defaults.word_required` (`false`). Running `enable` again on a repo keeps the
settings it has; a later change to a default reaches no repo already enabled.

`--set KEY=VALUE` sets one. Every `--set` for one key together give that key's whole
list, **replacing** its seed or what the repo had, so `--set drop-trailer=Claude-Session`
alone leaves `Commit-Check` off the list. `--set KEY=` alone removes the key.

A key on `pass-trailer` is never dropped, even when `drop-trailer` holds it too. Use it for
a trailer another commit-msg git hook reads after publishing's piece has run, such as a
check line your own hook verifies and removes:

```sh
moxi publishing enable ~/src/app --set pass-trailer=Commit-Check
```

`free-ref` takes full ref names in which `*`, `?` and `[...]` are wildcards, such as
`refs/notes/*` or `refs/tags/docs/*`. `enable` refuses a pattern that could match a branch
(`refs/heads/...`) or a release tag (`<package>-v<version>`).

## The message piece

On every commit, merge and replay that runs commit-msg, the piece reads every line above
git's scissors line, comment lines included. It drops each trailer line on `drop-trailer`
(with the lines continuing its value), leaves each on `pass-trailer` as it is, and refuses
a line holding a hit of secrets' `credentials` or `private-names` set, or of a provenance
rule. A refusal names each line as `line N, <rule>: <text>`; a secrets hit is named by its
rule only, so a credential never prints.

## The content piece

On every commit, the piece reads what the commit adds as git stores it: every added line,
whatever `.gitattributes`, diff drivers, text conversion or colour config say, a moved
file whole (renames are off), and each new path's name. Each goes through secrets'
`credentials` and `private-names` sets and the provenance rules for files. A refusal names
`path:line: set/rule`; a provenance hit shows its line too, a secrets hit never does. A hit
in a new path's name prints the path with the hit replaced by its rule.

A merge is judged by its own lines only: a line counts when it is added against every
parent, and a path when no parent has it. Lines a merge brings in from its other parents
were judged when those commits were made, and are judged again when they are pushed.

Every tracked path is scanned. A hit someone has judged and kept goes in an allowlist,
read from the index, so an allowance counts only once it is staged:

- `.secrets-allow` at the repo's root: credentials lines in secrets' format.
- `.residue-allow` at the repo's root: `path TAB exact text TAB why`, letting that
  provenance text stand in that file (`@path` for a path name). The provenance rules skip
  this file itself, since it spells what it lets stand; the secrets sets still read it.
- `secrets-allow-private` in the repo's common git directory: lines of every set, private
  names among them, where git never tracks them.

A malformed allowlist, a secrets set that can't run or a failed git read refuses the
commit, naming why.

## The push piece

Nothing leaves a public repo unless every ref the push moves follows these rules:

| what is pushed | allowed when |
|---|---|
| a branch created or moved | the remote's tip is an ancestor of the new one: a public branch is never rewritten |
| a branch deleted | it is not `main` or on `protected`, and the remote's tip, as this repo has it, is in the tracking ref of `main`; otherwise "fetch first" |
| a tag shaped like a release tag (`<package>-v<version>`) | never moved or deleted; created only when it is a release tag of a member at its commit and passes the tag gate ([Releases](#releases)), read against the tracking ref of `main` |
| any other tag or ref | a `free-ref` pattern names it; then it is created, moved or deleted freely |

Every ref pushed points at a commit or a tag of one, a push creates at most three tags
(GitHub starts nothing for a push that creates more), and each ref name goes through
secrets' sets. A `free-ref` pattern never applies to a branch or a release tag, even one
written into git config by hand. GitHub's rulesets still hold on the server.

Everything the push makes public for the first time is judged as a commit would have
been. Already public means reachable from the remote's tracking refs or a ref's remote
tip; a remote with no tracking refs here is asked for its refs, and only an empty one is
scanned whole. For each commit sent:

- its added lines and new path names, as the content piece reads them (a merge by its own
  lines), with the allowlists that commit holds;
- its message, through the message piece's sets and rules, and the repo's
  `repo/commit-rules` rules;
- its headers whole (author, committer, a merged signed tag) through secrets' sets;
- a trailer whose key is on `drop-trailer` or `pass-trailer` means a commit-msg hook
  didn't run, and refuses the push when the committer is your identity (secrets'
  `author.name` and `author.email`). A contributor's commit keeps its trailers, and is
  still scanned like any other.

Each tag pushed has every tag object of its chain read the same way: tagger and message.

With `word-required` set to `true`, a push needs `MOXI_PUBLISHING_PUSH_WORD`, matching
`push.word_pattern` in full: where the push was approved. It is a record, not a proof; any
process can set it. A push that passes is logged first, one JSON line in the module's
state directory, `pushes.jsonl` (time, repo, remote, URL, word, refs, commits); a log that
can't be written refuses the push.

## Scanning a whole tree

`moxi publishing scan [PATH] [--rev REV] [--since REV] [--ci]` reads every file of the
tree at `--rev` (default `HEAD`) as git stores it, and every path name, as the content
piece reads what a commit adds, with the allowlists of that tree. When `--rev` names an
annotated tag, each tag object of its chain is read too: message and tagger. With
`--since`, each commit of `SINCE..REV` is read whole as well: its message and every header
(author, committer, a merged signed tag), as the push piece reads them. It prints one line
per hit and exits 3 when there is one, 1 when the tree can't be read. `--ci` leaves out the
private names list and the private allowlist, which exist only on your machine, for a scan
run on a server.

The pieces and the scan read git objects. A file stored with Git LFS is a small pointer in
git; its content goes to the LFS server through git-lfs's own pre-push hook, and nothing
here reads it.

## Releases

Each package of a repo goes to the package index on its own tag, `<distribution>-v<version>`
(`moxi-v0.1.7`, `moxi-comms-v0.3.1`). A tag is read against the workspace members at the
commit it names: the distribution is the member whose name, plus `-v`, is the tag's
longest matching prefix, and the rest is a version in its normalised spelling. A version
is *published* when the index has a file of it that isn't yanked, and *released* when it
is published and tagged.

The repo's release settings sit in its root `pyproject.toml`, so CI reads what your
machine reads:

```toml
[tool.moxi-publishing]
not-ready = ["proj-a", "proj-b"]   # never released from a commit that lists them
ready = ["proj-c"]                 # confirmed
check-workflow = "ci.yml"          # the workflow of the check that must pass on main
check-job = "linux"                # that check's job
```

A package on `not-ready` is still built and tested; it moves to `ready` by its own commit
once you have confirmed it. `ready` is optional. When the table holds it, every member
must be on exactly one of the two lists, so a package added to the workspace later is
never ready until a commit says so: one on neither list or on both counts as not ready,
and `moxi-dev check` refuses it. Without `ready`, only `not-ready` counts.
`check-workflow` and `check-job` have no default: without them nothing is released, and
the gate names the missing key.

### The tag gate

One gate decides whether a tag may release, run alike by `moxi publishing tag`, the push
piece and the release workflow's `ci gate`. For a tag on commit C naming package D at
version V:

1. **shape**: the tag is annotated and peels, through tag objects only, to C; D is a
   member at C and V is its `project.version` there;
2. **changelog**: D's `CHANGELOG.md` at C has `## V (YYYY-MM-DD)` as its first version
   heading;
3. **main and green**: C is on the first-parent line of main, and GitHub has a
   successful run of `check-job` in `check-workflow` from a push to main at C;
4. **ready**: D is not on `not-ready` at C, and is on `ready` where the table keeps it;
5. **siblings**: each requirement D places on another member admits at least one
   published version of it (an extra's are reported, not checked);
6. **new**: the index has no file of D at V; or, when the release workflow has built the
   files, every file the index holds at V is one of them, so a release that stopped
   halfway can be run again;
7. **catalogue**, for the core `moxi` only: every entry of its catalogue has a released
   version or says `unreleased = true`.

Checks 3, 5, 6 and 7 read GitHub's API and the package index, ten seconds at most each,
with the token `gh auth token` gives where there is one (sent to api.github.com only); a
read that fails refuses. `MOXI_PUBLISHING_GITHUB_API_URL` and `MOXI_PUBLISHING_PYPI_URL`
point the reads elsewhere.

### Setting the next version

A build writes its CHANGELOG lines under `## Unreleased` and leaves `version` alone; the
release sets the version. `moxi publishing bump PACKAGE... [--part patch|minor|major]`
(default `patch`), run on a build branch, makes one commit's worth of edits and leaves them
unstaged: each package's `[project] version` goes to the next one; its CHANGELOG's
`## Unreleased` heading becomes `## <version> (<today>)` under a new empty `## Unreleased`;
each README link into the repository moves from the old release tag to the new one; then
`uv lock` runs. It refuses, changing nothing, a CHANGELOG with no `## Unreleased` heading,
and a requirement another package places on a bumped one (a ceiling such as `<0.2`) that
admits the old version but not the new; change that requirement in the same commit.

### Making a release tag

`moxi publishing tag PACKAGE [--at REV] [--remote NAME]` runs the gate at REV (default:
the tip of main as `refs/remotes/origin/<main>` has it, so fetch first) and, when every
check passes, makes the annotated tag, whose message is the package's CHANGELOG section.
It pushes nothing: `git push origin <tag>` does, and the push piece runs the gate again.
It prints each check, and exits 3 when the gate refuses.

### The workflow files

`moxi publishing workflows write [PATH]` writes two files into the repo's
`.github/workflows/`, replacing what is there; commit them.

- `publishing.yml`, the public scan, runs on every push to any branch and every tag:
  `moxi publishing scan --ci` over the pushed commit's tree, the commits the push brings
  (those not on the default branch, for a new ref) and a pushed tag. A hit fails the check
  and blocks nothing.
- `release.yml` runs on a pushed release tag, or on `gh workflow run release.yml --ref
  <tag>`. Its jobs run in order, each needing the one before: the gate; the public scan of
  the tagged tree; one build of the sdist and wheel, tested from a fresh install; the
  published-siblings test of that build; the gate again, judging the files the index may
  already hold against that build; the upload to the index, in the `pypi` environment;
  and the GitHub Release, with the CHANGELOG section as its notes. The file's opening
  comment says which re-run is for which failure.

Each job holds `contents: read` only, but for `actions: read` where the gate runs,
`id-token: write` for the upload and `contents: write` for the GitHub Release. The
upload uses no stored token: it needs a trusted publisher on the index for each package
(this repo, `release.yml`, environment `pypi`), and a `pypi` environment on GitHub whose
required reviewer approves each upload. The upload job runs none of the repo's code,
only the pinned actions that fetch the build and upload it: the one job that can get an
upload token runs nothing a commit changes but the workflow file itself.

The gate in CI is a third check, not a wall: the run uses the workflow file and the code
of the tagged commit, so a bad commit can change both. What holds against one is the
`pypi` environment's required reviewer, who approves each upload on GitHub before
anything reaches the index, and the trusted publisher's environment claim: the index
hands a token only to a job of `release.yml` running in the `pypi` environment.

The steps the workflow runs:

- `moxi publishing ci gate TAG [--github OWNER/REPO] [--artifact DIR]`: the gate, with
  main read from `origin/<the default branch>`. Without `--artifact` a version the index
  already holds files of passes check 6, since the verify job runs the gate again with
  the build's files.
- `moxi publishing ci notes TAG`: the tag's CHANGELOG section.
- `moxi publishing ci siblings TAG --artifact DIR [--resolution highest|lowest-direct]`:
  the package's whole test suite from a fresh venv that holds only its wheel, its
  published siblings, the extras whose sibling requirements a published version admits,
  and the tools of its own `dev` dependency group at the versions `uv.lock` pins; never
  another workspace member. It runs once with the newest published siblings each
  requirement admits and once with the lowest set that resolves, or only at
  `--resolution`, and reports a sibling the resolver had to raise above its floor, with
  what raised it. An extra left out is reported, not failed; a required sibling with no
  published version it admits refuses, naming it. Before any test runs, the run fails if
  the package imports from anywhere but the venv's site-packages.

## GitHub's side

The local guards can be skipped (`--no-verify`, a tool that runs no hooks), so GitHub
checks too. `moxi publishing github show [PATH]` reads, with [gh](https://cli.github.com),
the GitHub repo `moxi-publishing.github` names and prints each field unlike what a
public repo expects; `moxi publishing github apply [PATH]` writes those that differ.

- Ruleset `moxi-main` on the push piece's `main` branch: no deletion and no force push.
- No other branch ruleset: other branches are created, updated and deleted freely; the
  push piece keeps a branch from being deleted before it is merged.
- Ruleset `moxi-tags` on every tag: no update, deletion or force push.
- Ruleset `tags-create` on every tag but release tags (`*-v*`): no creation.
- Each ruleset active, with no bypass actors.
- Secret scanning with push protection, which needs admin access to read.
- Environment `pypi`, deployable from release tags only, whose required reviewer is the
  account `gh` is signed in as, allowed to approve its own runs since that account
  pushes the tags.

`show` also names a push piece `main` setting unlike the repository's default branch.
The trusted publisher each package needs on the package index can't be read from
GitHub; `show` says what each needs (this repository, `release.yml`, environment
`pypi`), to set on pypi.org. `apply` deletes no ruleset and changes no branch: an
unexpected branch or tag ruleset and a `main` unlike the default branch are printed
for you, with the command that would remove the ruleset.

## Doctor

`moxi doctor publishing` reads each repo repo has indexed, and uses no network. It fails
a repo marked public where a piece is missing or disabled; a piece registered where the
repo isn't marked public; a hook command repo wrote for the pieces that runs another
interpreter than the one `enable` recorded in `moxi-publishing.interpreter`; and a repo
where a commit-msg hook of its own runs after the pieces while the message piece's
`drop-trailer` names `Commit-Check`, or holds no value, which reads as the seed that
names it: that hook would never see the line. It warns when `moxi-publishing.github` is
unset and when a workflow file `workflows write` writes is there but differs from the
template; and, once, when secrets' private names list is empty and when the push log
can't be written.

## Provenance rules

Shapes that point into notes, sessions or messages a reader of the public repo can't
follow. Each is on by default; `provenance.rules` in `moxi config publishing` lists those
that run.

| rule | applies to | shape |
|---|---|---|
| `session-link` | messages, files | a Claude Code session URL |
| `session-pointer` | messages, files | 8 or more hex digits, `@`, an ISO date |
| `message-id` | messages | `comms:` and digits, `msg` and digits, or 12 or more digits |
| `quoted-ruling` | messages | a ruling tag on a line that also holds a quoted passage |
| `ruling-tag` | files | a design or review tag |
| `round-pointer` | files | `round <home>/<slug>`, or a memory key |

## Install

Linux, Python 3.12 or later, git 2.39 or later, and [uv](https://docs.astral.sh/uv/).
`github show` and `github apply` need [gh](https://cli.github.com), signed in.

```sh
uv tool install moxi
moxi install publishing
```

## Dependencies

`moxi`, Moxi's core; `moxi-repo`, which runs the git hook pieces; and `moxi-secrets`,
which holds the pattern sets and lists the pieces read.

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/moxi-publishing-v0.1.0/packages/publishing/LICENSE).
