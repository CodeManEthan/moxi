# moxi-repo

Moxi's repo module does two jobs for the git repos you name.

1. **It runs git hook pieces.** Other modules declare *pieces*: a Python function that runs
   on one git event, such as a gate that refuses a commit adding a secret. You add a piece
   to a repo by its path. On git 2.54 and later repo writes one hook entry per event into
   that repo's git config and git runs it, then the repo's existing git hook. Below 2.54 it
   writes one small shell script (a shim) per event that runs the pieces, then the existing
   git hook, which it has moved aside. Either way one Python process per event runs every
   piece for that event, importing only the pieces it runs.
2. **It is the one committer.** `moxi_repo.commit(spec)` stages the paths a spec names,
   lets a prepare step drop paths, commits those paths alone with the git hooks running,
   and pushes. A spec can be saved in the repo and run by `moxi repo commit`.

repo also ships one piece of its own, `repo/commit-rules`, which checks a commit message's
trailers and refuses tags you define. It is in no repo until you add it.

```sh
moxi repo git-hooks available                    # the pieces installed modules declare
moxi repo git-hooks add ~/src/app notes/secret-gate
moxi repo git-hooks list                         # every repo repo has written to
```

## Git hook pieces

### The commands

- `moxi repo git-hooks [list] [PATH] [--projects] [--json]` lists each repo repo has
  written to, or the one at PATH: git's version and the mode, each event's pieces in
  order (id, fail policy, enabled, settings), the existing git hook, and problems.
- `moxi repo git-hooks available [--json]` lists the pieces installed modules declare: id,
  event, fail policy, the settings each takes, and one line about it.
- `moxi repo git-hooks add PATH [PIECE...] [--mode auto|config|shim] [--set KEY=VALUE]...`
  registers pieces in the repo at PATH. With no piece it only adds the repo to repo's index.
  `--set` sets a named piece's setting in that repo: every `--set` for one key in one call
  together give that key's whole list of values, replacing what the repo had; `--set KEY=`
  alone removes the key; keys you don't name are kept. The key is matched ignoring case.
- `moxi repo git-hooks remove PATH [PIECE...]` removes pieces; with none, everything repo
  wrote to that repo. On a path that no longer exists it drops the repo from the index.
- `moxi repo git-hooks enable|disable PATH PIECE` turns one piece on or off in one repo. A
  disabled piece stays registered, with its settings.
- `moxi repo git-hooks refresh [PATH]` reads the declarations, git's version and the
  interpreter again, and rewrites every hook entry, shim and launcher of one repo or of
  every indexed repo. It keeps each piece's `enabled` key and settings.

PATH is a path inside the repo. With the projects module installed it may also be a
project key; see [Projects](#projects).

Exit codes: 0 done; 1 a git or config failure, or `refresh` found a problem; 2 a usage
error, such as an unknown piece or a path that isn't a git work tree; 3 refused: a
declaration repo refuses, git older than 2.39, or a shim that can't be placed. A refused
command writes nothing.

### Where things live

Everything repo writes for a repo is in that repo's own local git config, which git never
tracks: the mode, each piece's registration and settings, and the hook entries. Every
worktree of the repo shares it, and `git config --get-regexp '^moxi-repo\.'` shows it:

```ini
[moxi-repo "piece.notes/secret-gate"]
	event = pre-commit
	target = moxi_notes.gates:pre_commit
	fail = closed
	order = 20
	enabled = true
```

So the git hook needs nothing outside the repo to run its pieces: an editor or a GUI
committing with another environment runs the same ones. repo also keeps an index of the
repos it has written to, for listings and `refresh`; the git config is the truth, and
`moxi repo git-hooks add PATH` puts a repo back in the index.

### How pieces run

Pieces of one event run by their `order` (0 to 99), then by id. Every enabled piece runs
even after one refuses, and every refusal prints, as git does with its own config hooks.
The repo's existing git hook runs after the pieces. Output goes to stderr:

```text
repo: pre-commit refused by notes/secret-gate: notes/plan.md adds a token (github-token)
```

Each piece declares a fail policy:

- **closed**: if it raises, or its function can't be imported, a pre-* or commit-msg event
  is refused with one line naming the piece and the command that turns it off.
- **open**: if it raises, the event goes on, one line prints, and a doctor record is kept.

On post-* events git ignores the exit status, so a piece that breaks there prints one line
and keeps a doctor record whatever its policy. The git hook exits 0 when the pieces passed,
3 when one refused, and 1 when a closed piece broke; git treats any non-zero as a refusal.

Each hook entry, shim and launcher starts with a guard that checks the interpreter and
repo's runner file still exist, without starting Python. When one is missing it prints one
line, such as

```text
repo: git hooks can't run here: /home/you/.local/share/uv/tools/moxi/bin/python is missing; run: moxi install repo (or: moxi repo git-hooks remove /home/you/src/app)
```

and refuses the event only if a closed piece is enabled for it.

### Modes and git versions

repo needs git 2.39 or later. `add` and `refresh` pick the mode from the git on PATH:
**config** mode (hook entries in git config) on git 2.54 and later, **shim** mode below.
`add --mode config|shim` pins a repo's mode and `--mode auto` unpins it; the config key
`git_hooks.mode` sets the default for repos that pin none. After a git upgrade, `refresh`
moves an unpinned repo from shims to hook entries and puts the existing git hook back.

A config-mode repo committed to by a git older than 2.54 runs no pieces at all. That can
happen with two gits on one machine (Apple's and Homebrew's, or a GUI that bundles its
own). Shim mode works under every git, so pin `--mode shim` where that is a risk.

In shim mode an existing git hook moves to `<event>.local` beside the shim, and the shim
runs it after the pieces with the same arguments and stdin; `remove` moves it back. repo
refuses to place a shim where an `<event>.local` that it didn't move is already there. A
shim is known by its first comment line, so `refresh` rewrites it in place even after
another tool renamed it (`pre-commit`'s installer renames it `.legacy` and still runs it;
lefthook renames it `.old` and doesn't). `git-hooks list` shows a shim that another tool
replaced or renamed.

### Hook managers and the chain line

In config mode a hook manager that owns `core.hooksPath` (husky) or writes `.git/hooks`
(pre-commit, lefthook) keeps working: git runs repo's entry, then the manager's hook. git
runs the manager's hook even when a piece refused.

In shim mode with a hooks directory outside the repo's git dir (a `core.hooksPath` such as
`.husky/_`, a tracked `.githooks/`, or a directory other repos share), repo writes nothing
there. It writes a launcher, `<git common dir>/moxi-repo/hook`, which git never tracks,
and prints a **chain line** for you to add to the manager's hook for each event:

```sh
h="$(git rev-parse --path-format=absolute --git-common-dir)/moxi-repo/hook"; if [ -f "$h" ]; then sh "$h" pre-commit "$@" || exit 1; fi
```

The line names no path of your install. On a clone without the launcher (a teammate, a CI
job) it does nothing and the commit goes on; where the launcher is, the pieces run and a
refusal stops the hook's later lines. For an event with stdin (pre-push) the printed line
saves stdin first, so the lines after it still read every ref; put it before any line that
reads stdin. `git-hooks list` says "chain line needed" until the line is there.

### Skipping the git hooks for one command

Pieces are local guards, and whoever runs git can skip them, as with any git hook:

| switch | config mode | shim mode |
|---|---|---|
| `git commit --no-verify` | skips the pre-commit and commit-msg entries | skips those shims |
| `git -c hook.moxi-repo-<event>.enabled=false ...` | skips repo's entry for that event | no effect |
| `git -c core.hooksPath=<other dir> ...` | repo's entries still run; the existing git hook is skipped | skips the shims and the existing git hook |

## Declaring a piece

A module declares a piece in the entry-point group `moxi.repo.git_hooks`, named
`<module>.<name>`, pointing at a table in a module that imports little:

```toml
[project.entry-points."moxi.repo.git_hooks"]
"notes.secret-gate" = "moxi_notes.gitpieces:SECRET_GATE"
```

```python
SECRET_GATE = {
    "event": "pre-commit",                    # one git event per piece
    "target": "moxi_notes.gates:pre_commit",  # the function the git hook calls
    "fail": "closed",                         # "closed" or "open"
    "order": 20,                              # 0-99, default 50
    "doc": "Refuse a commit that adds a secret.",
    "settings": {"refused-name": "multi"},    # optional: per-repo settings, "one" or "multi"
}
```

The piece's id is `<module>/<name>`. A setting's name is lower-case letters, digits and
`-`, starting with a letter, because git hands config names back lower-cased; it can't be
one of the registration's own keys (`event`, `target`, `fail`, `order`, `enabled`,
`recorded`). repo refuses any other declaration and names the key.

The function takes a `moxi_repo.HookContext` and returns a `moxi_repo.Outcome`:

```python
import moxi_repo

def pre_commit(ctx: moxi_repo.HookContext) -> moxi_repo.Outcome:
    staged = ctx.git("diff", "--cached", "--name-only", "-z").stdout.split("\0")
    if any(name.endswith(".pem") for name in staged):
        return moxi_repo.Outcome.refuse("a key file is staged")
    return moxi_repo.Outcome.ok()
```

`HookContext` holds `event`; `root`, `git_dir` and `common_dir`; `args`, git's arguments;
`stdin`, the bytes git fed the git hook; `message_path` on commit-msg; `updates`, the
parsed ref lines, on pre-push; `settings`, this repo's settings for the piece, each name
to its list of values; `committer`, the spec's name when repo's committer is making the
commit, else None; and `git(*args)`, which runs git with the git hook's own environment, so
a partly staged commit is judged as it will land.

`Outcome` is one of `Outcome.ok()`; `Outcome.refuse(line, *more)` on pre-* and commit-msg
events; `Outcome.notice(line)` on post-* events, a failure the person committing should
see, printed at once; and `Outcome.record(line)` on post-* events, any other failure,
kept as a doctor record with nothing printed. A piece that raises has broken, and its fail
policy says what that does.

A piece may change files and the index as any git hook may: what a pre-commit piece stages
lands in the commit. On a commit repo's committer makes (`ctx.committer` is set), a piece
that would change the index refuses instead and names the files, since that commit holds
only what its prepare step judged.

## The commit-rules piece

`repo/commit-rules` runs on commit-msg, fail closed, order 10. It checks:

- **Trailer shape.** When the message's last paragraph holds any trailer (`Key: value`),
  it holds nothing but trailers, so git's parser reads them all. A line outside that block
  whose key is one of the block's keys, or a refused key, is a trailer stranded in the
  body. Every trailer has a value. A key on the repo's refused list is refused.
- **No tags.** The subject and body, without the trailer block, comment lines and anything
  below the scissors line, are matched against the repo's tag patterns. With no pattern
  there is no tag check.

```text
repo: commit-msg refused by repo/commit-rules: no tags: line 3 has "TAG-12"; say it in plain words
```

Its two settings live in each repo's git config, and only a command that names the repo
changes them:

```sh
moxi repo git-hooks add ~/src/app repo/commit-rules \
    --set refused-trailer=Signed-off-by --set 'tag-pattern=\bTAG-[0-9]+\b'
moxi repo git-hooks add ~/src/app repo/commit-rules --set tag-pattern=   # no tag check
```

Two config keys turn it on more widely:

- `commit_rules.everywhere` (default false): `git-hooks add` and `refresh` register the
  piece in each repo they touch that lacks it. They touch only repos you name or that are
  in repo's index. To keep it off one of those, `disable` it there; a disabled piece is
  registered, so it is left alone.
- `commit_rules.tag_patterns` (default empty): the tag patterns a repo starts with when the
  piece is first registered there and no `--set tag-pattern` is given. It is read only
  then; a later change reaches no repo that already has the piece.

The refused-trailer list has no config default: refusing a trailer is decided per repo. A
pattern that won't compile is refused by `add` before anything is written, and by
`moxi config` in the config file.

## The committer

```python
import moxi_repo

spec = moxi_repo.Spec(
    name="notes/snapshot",               # <module>/<name>
    root="/home/you/notes",              # the work tree's top
    paths=("notes",),                    # what it commits, read literally; "." is all
    author=("notes-bot", "bot@example.invalid"),
    min_interval=300,                    # seconds since this author's last commit
    push="origin",                       # None: no push
)
result = moxi_repo.commit(spec)          # one pass; never raises
print(result.line())
```

A pass takes the committer's lock in the repo's common git dir (try once by default; pass
`wait=` to wait). It commits only the paths the spec names, leaves a path someone else
staged for a later pass, and leaves out a file over `max_bytes`. A prepare step,
`prepare(ctx) -> Prepared(drop=[(path, reason)], refuse=None)`, may drop paths or refuse
the pass. The git hooks run on the committer's commits. The default message is
`<spec name>: N paths`, with no text taken from file names or content; `message` and
`trailers` callables can give your own. Every path reaches git in its literal form, and
lists of changed paths go on stdin, never in argv.

`result.status` is one of `committed`, `clean`, `throttled`, `busy` (another pass holds
the lock), `index-busy` (git's own index lock is held), `refused` (a git hook or the
prepare step refused) or `error`. The push part runs on every pass that holds the lock:
it pushes when the branch is ahead, and retries a failed push after `push_backoff`
seconds.

`moxi_repo.save_spec(spec)` saves a spec whose callables are `"pkg.mod:fn"` strings into
the repo's git config, and `moxi_repo.commit_saved(root, name)` runs it.

- `moxi repo commit PATH [--spec NAME] [--wait SECONDS] [--json]` runs one saved spec, or
  every spec saved in that repo, and prints each pass's line. It exits 1 on an error or a
  failed push, 3 when a pass was refused, 75 when `--wait` ran out on a busy lock, else 0.
- `moxi repo specs [PATH] [--json]` lists saved specs, and specs that ran without being
  saved, each with its last result.
- `moxi repo specs remove PATH NAME` forgets a spec: its saved section, its state and its
  doctor records.

A failure the committer can't show its caller, such as a refused pass run from a timer, is
kept as a doctor record, replaced on each pass and cleared once the condition ends.

repo also exports the small git helper the committer uses:
`moxi_repo.git(root, *args, timeout=30)` runs git with no prompts and git's local and
pathspec variables removed, `moxi_repo.changes(root, staged=True)` returns the staged (or
`range=(a, b)`) changes as `Change(status, path, old_path, blob)`, and
`moxi_repo.literal_pathspec(path)` gives a path's literal pathspec form.

## Projects

repo keys every repo by its path. With the projects module installed
(`moxi install projects`), these work too:

- an argument that isn't an existing path, and has no `/` in it, is a project key
  (matched ignoring case) wherever a command takes PATH. An unknown key exits 2;
  a projects store that can't be read exits with projects' reason and fix;
- `git-hooks list` and `specs` show each repo's project key;
- `git-hooks list --projects` adds every project's repo, including those repo hasn't
  touched, shown as not indexed.

A projects error leaves the key column empty with one line; a listing never fails because
of it. Without projects, paths work as before. The git hooks never ask projects anything.

## Doctor checks, upgrades and uninstall

> **Not written yet.** This section will cover the checks repo adds to `moxi doctor`
> (hook entries, shims, launchers and their interpreter; git versions; saved specs and
> files no spec covers; open doctor records), what `moxi install repo` does on an upgrade,
> how other modules' upgrades and uninstalls reach their registered pieces, and what
> `moxi uninstall repo` removes. Until then: `moxi repo git-hooks refresh` rewrites every
> entry, shim and launcher after an upgrade, and `moxi repo git-hooks remove PATH` takes
> everything repo wrote out of one repo.

## Install

Python 3.12 or later, git 2.39 or later, and [uv](https://docs.astral.sh/uv/). On macOS
use the Xcode command line tools' git or Homebrew's; on Windows, Git for Windows. Pushing
needs a remote you can already push to without a prompt.

```sh
uv tool install moxi
moxi install repo
```

Before the modules are on PyPI, install the core from git and give `moxi install` a git
source for repo:

```sh
uv tool install git+https://github.com/moxihq/moxi#subdirectory=packages/moxi
moxi install repo \
  --from 'moxi-repo @ git+https://github.com/moxihq/moxi#subdirectory=packages/repo'
```

The front door's README, at [moxihq/moxi](https://github.com/moxihq/moxi), covers the
`moxi` command and the other modules.

## Dependencies

`moxi`, Moxi's core. The projects module is optional and reached only when installed.
repo has no optional extras.

## License

MIT. See [LICENSE](LICENSE).
