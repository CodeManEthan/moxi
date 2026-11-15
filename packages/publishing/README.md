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
| a tag shaped like a release tag (`<package>-v<version>`) | never moved or deleted; this version creates none, since it runs no release gate |
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

`moxi publishing scan [PATH] [--rev REV] [--ci]` reads every file of the tree at `REV`
(default `HEAD`) as git stores it, and every path name, as the content piece reads what a
commit adds, with the allowlists of that tree. It prints one line per hit and exits 3 when
there is one, 1 when the tree can't be read. `--ci` leaves out the private names list and
the private allowlist, which exist only on your machine, for a scan run on a server.

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

```sh
uv tool install moxi
moxi install publishing
```

## Dependencies

`moxi`, Moxi's core; `moxi-repo`, which runs the git hook pieces; and `moxi-secrets`,
which holds the pattern sets and lists the pieces read.

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/main/packages/publishing/LICENSE).
