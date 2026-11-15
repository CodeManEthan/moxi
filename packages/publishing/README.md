# moxi-publishing

Moxi's publishing module keeps private text out of a public git repo. You mark a repo
public, and three git hook pieces, run by Moxi's repo module, guard what it commits and
pushes. The pattern sets they read (credentials, and your private names) are Moxi's secrets
module's.

```sh
moxi publishing enable ~/src/app --github alice/app
moxi publishing list
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
