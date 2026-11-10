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

repo also ships two pieces of its own. `repo/commit-rules` checks a commit message's
trailers and refuses tags you define. `repo/commit-proof` refuses a commit whose message
lacks the line `moxi repo rules` prints for the staged tree, so a committer has to run
that command, and see the repo's rules, before each commit. Each is in no repo until you
add it.

```sh
moxi repo git-hooks available                    # the pieces installed modules declare
moxi repo git-hooks add ~/src/app notes/secret-gate
moxi repo git-hooks list                         # every repo repo has written to
```

## Git hook pieces

### The commands

- `moxi repo git-hooks [list] [PATH] [--projects] [--json]` lists each repo repo has
  written to, or the one at PATH: git's version and the mode, each event's pieces in
  order (id, fail policy, enabled, settings), the existing git hook, and problems. Its
  JSON also gives the doctor records repo has open for each repo.
- `moxi repo git-hooks available [--json]` lists the pieces installed modules declare: id,
  event, fail policy, the settings each takes, and one line about it.
- `moxi repo git-hooks add PATH [PIECE...] [--mode auto|config|shim] [--set KEY=VALUE]...`
  registers pieces in the repo at PATH. With no piece it only adds the repo to repo's index.
  `--set` sets a named piece's setting in that repo: every `--set` for one key in one call
  together give that key's whole list of values, replacing what the repo had; `--set KEY=`
  alone removes the key; keys you don't name are kept. The key is matched ignoring case.
- `moxi repo git-hooks remove PATH [PIECE...]` removes pieces; with none, everything repo
  wrote to that repo. On a path that no longer exists it drops the repo from the index,
  unless git still has that repo in another work tree: then it names the command to remove
  it there, with the repo's main work tree, exits 1 and removes nothing. It drops the repo
  too where an indexed repo is gone and its path is now inside another repo, which it
  leaves alone.
- `moxi repo git-hooks enable|disable PATH PIECE` turns one piece on or off in one repo. A
  disabled piece stays registered, with its settings. An event whose pieces are all
  disabled, or all removed, loses its hook entry or shim, and in shim mode the existing git
  hook moves back.
- `moxi repo git-hooks refresh [PATH]` reads the declarations, git's version and the
  interpreter again, and rewrites every hook entry, shim and launcher of one repo or of
  every indexed repo. It keeps each piece's `enabled` key and settings.

- `moxi repo rules [PATH]` prints what a committer in the repo at PATH, or in the one you
  are in, must know: see [The command `moxi repo rules`](#the-command-moxi-repo-rules).

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
`moxi repo git-hooks add PATH` puts a repo back in the index. The index knows a repo by its
common git dir and lists it by its main work tree, whichever worktree you named it from, so
removing a linked worktree doesn't lose it; a repo is gone only when its git dir is.

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
runs it after the pieces with the same arguments and stdin, also when the guard stops the
pieces, as git does in config mode; `remove` moves it back. If
`core.hooksPath` later points somewhere else, `refresh` and `remove` take repo's shims out
of `.git/hooks` and move each hook it moved aside there back, and `git-hooks list` names
any left. repo refuses to place a shim where an `<event>.local` that it didn't move is
already there. A
shim is known by its first comment line, so `refresh` rewrites it in place even after
another tool renamed it (`pre-commit`'s installer renames it `.legacy` and still runs it;
lefthook renames it `.old` and doesn't). `git-hooks list` shows a shim that another tool
replaced or renamed.

`refresh`, which every `moxi install repo` runs, puts repo's shim back where another git
hook replaced it, moving that hook to `<event>.local` so that both run, and rewrites every
hook entry in config mode. So overwriting a shim or an entry doesn't stop repo in a repo for
long: to stop it, use `moxi repo git-hooks disable PATH PIECE` or
`moxi repo git-hooks remove PATH`.

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
reads stdin. `git-hooks list` says "chain line needed" until the line is there. Under
husky 9, whose hooks directory is `.husky/_`, the line goes in `.husky/<event>`, the hook
you edit: husky writes the files in `.husky/_` again on its next run, and they run yours.

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

- **Trailer shape.** The piece reads trailers as git's own parser does. The message's last
  paragraph is its trailer block when git reads it as one: every line a trailer
  (`Key: value`), or at least a quarter of them with a `Signed-off-by:`, a cherry-pick line
  or a key set in the repo's `trailer.*` config among them. A last paragraph git won't read
  that way is refused when it holds a trailer whose key is plainly one (hyphenated, on the
  refused list, or set in `trailer.*` config), since git then reads none of its trailers:
  put a blank line before them. Otherwise it is prose, so `Note: check length first.` at
  the end of a body passes. A line whose `:` is followed by `//`, a URL, is never a
  trailer. A line outside the block whose key is one of the block's keys, or a refused
  key, is a trailer stranded in the body. Every trailer has a value. A key on the repo's
  refused list is refused.
- **No tags.** The subject and body, without the block's trailers, comment lines and
  anything below the scissors line, are matched against the repo's tag patterns. With no
  pattern there is no tag check. A comment line is any line that starts with the repo's
  comment prefix, whatever git's cleanup mode: a line that starts with `#` is not checked
  even under `git commit -m` or `--cleanup=verbatim`, where git keeps it in the message, and
  with `core.commentChar=auto` the prefix is taken as `#`.

```text
repo: commit-msg refused by repo/commit-rules: no tags: line 3 has "TAG-12"; say it in plain words
```

Its three settings live in each repo's git config, and only a command that names the
repo changes them. Each takes any number of values:

- `refused-trailer`: a trailer key the repo refuses, matched ignoring case.
- `tag-pattern`: a regular expression a tag matches.
- `rule`: one of the repo's own rules for a committer, free text. The piece checks none
  of them; `moxi repo rules` prints them.

```sh
moxi repo git-hooks add ~/src/app repo/commit-rules \
    --set refused-trailer=Signed-off-by --set 'tag-pattern=\bTAG-[0-9]+\b' \
    --set 'rule=say what changed and why in plain words'
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
pattern that won't compile, or that matches the empty string (it would find an empty match
on every line and check nothing), is refused by `add` before anything is written, and by
`moxi config` in the config file.

## The command `moxi repo rules`

`moxi repo rules [PATH]` prints what a committer must know before committing in the repo
at PATH, or in the one you are in. It reads the two pieces above from the repo's git
config and always exits 0 on a repo:

```text
commit rules for /home/alice/src/app
- trailers this repo refuses: Round, Reconciles
- no text matching: \bTAG-[0-9]+\b
- say what changed and why in plain words
Commit-Check: 3fa1c2d4e5b6
```

- The heading names the work tree PATH is in, a linked work tree's own top.
- Where `repo/commit-rules` is registered: the trailer keys it refuses on one line, a line
  for each tag pattern, then each `rule` value as written. A kind with no value prints no
  line. Where the piece is disabled its lines still print, under one line saying so:
  `repo/commit-rules is disabled in this repo: nothing checks a commit against the lines
  below`.
- Where `repo/commit-proof` is registered, disabled or not: last, the `Commit-Check` line
  for what is staged in that work tree now. When the index has unresolved conflicts, or
  the piece has no salt there, a line starting `no Commit-Check line:` says why instead.
- With neither piece registered: `no commit rules for PATH`.

The pieces and their settings are the repo's, shared by all its work trees. The
`Commit-Check` line is not: each work tree has its own index, so run the command in the
work tree you commit in. Printing the line runs `git write-tree`, which writes the tree
objects of what is staged into the repo.

## The commit-proof piece

`repo/commit-proof` runs on commit-msg, fail closed, order 20, after the commit-rules
piece. It refuses a commit unless its message holds the `Commit-Check` line
`moxi repo rules` printed for the tree being committed. A committer therefore stages,
runs the command, reads the rules above the line, and commits with the line:

```sh
git add notes/today.md
moxi repo rules                      # prints the rules, then Commit-Check: 3fa1c2d4e5b6
git commit -m 'notes: add today' -m 'Commit-Check: 3fa1c2d4e5b6'
```

- **The line** is `Commit-Check: <h>`: the first 12 hex characters of SHA-256 over
  `commit-check`, NUL, the repo's salt, NUL, and the tree id `git write-tree` gives for
  the index. It shows the command ran on this staged tree. It is not a security boundary:
  whoever can read the repo's git config can make it.
- **The salt** is the piece's one setting, `salt`: 16 random hex characters written into
  the repo's git config when the piece is first registered there, and kept from then on,
  so a line copied from another repo doesn't match. Removing the piece and adding it again
  gives the repo a new one. `--set salt=VALUE` takes 16 hex characters and nothing else;
  `--set salt=` is refused, since the piece refuses every commit without one.
- **Where it is read**: every trailer paragraph after the subject's, a paragraph whose
  every line is a trailer (`Key: value`) or continues one, so the line may sit beside
  other trailers or in a paragraph of its own. The key is matched ignoring case. A
  paragraph with a prose line is prose and is left alone: a body may quote a line.
- **What passes without a line**: a commit repo's committer makes; a replay, which is a
  commit made while a rebase, cherry-pick or revert is in progress; and a clean merge,
  one other head merged and the index holding exactly the tree git makes of the two heads
  with no conflict (`git merge-tree --write-tree`, which git has from 2.38, under the
  2.39 repo needs anyway). A merge with a resolved conflict, or
  with a change added by hand, holds changes of its own and needs the line.
- **What is refused**: no line, more than one, a line that isn't 12 hex characters, and
  a line that doesn't match the tree being committed. Each refusal names the command and
  the work tree to run it in:

```text
repo: commit-msg refused by repo/commit-proof: commit proof: line 3 doesn't match the tree being committed in /home/alice/src/app: the index changed after the line was printed, or the line is another repo's; stage first, run: moxi repo rules /home/alice/src/app, and commit with the line it prints
```

The piece checks the tree that will land. Under `git commit PATH` and `git commit -a` git
commits a temporary index, so the line for the work tree's whole index doesn't match when
those commit something else: `git commit -a` over unstaged changes is refused as stale,
and so is a commit whose pre-commit hook staged more files. Stage first, run the command,
then commit with plain `git commit`; after a hook restages, run the command again.

One config key turns it on more widely: `commit_proof.everywhere` (default false) makes
`git-hooks add` and `refresh` register the piece in each repo they touch that lacks it,
as `commit_rules.everywhere` does for the commit-rules piece. To keep it off one of those
repos, `disable` it there.

## The committer

```python
import moxi_repo

spec = moxi_repo.Spec(
    name="notes/snapshot",               # <module>/<name>
    root="/home/you/notes",              # the work tree's top
    paths=("notes",),                    # what it commits, read literally; "." is all
    author=("notes-bot", "bot@example.invalid"),
    min_interval_s=300,                  # seconds since this author's last commit
    push="origin",                       # None: no push
)
result = moxi_repo.commit(spec)          # one pass; never raises
print(result.line())
```

A pass takes the committer's lock in the repo's common git dir (try once by default; pass
`wait_s=` to wait). It commits only the paths the spec names, leaves a path someone else
staged for a later pass, and leaves out a file over `max_bytes`. A prepare step,
`prepare(ctx) -> Prepared(drop=[(path, reason)], refuse=None)`, may drop paths or refuse
the pass. The git hooks run on the committer's commits. The default message is
`<spec name>: N paths`, with no text taken from file names or content; `message` and
`trailers` callables can give your own. Every path reaches git in its literal form, and
lists of changed paths go on stdin, never in argv.

`result.status` is one of `committed`, `clean`, `throttled`, `busy` (another pass holds
the lock), `index-busy` (git's own index lock is held), `refused` (a git hook or the
prepare step refused) or `error`. The push part runs on every pass that holds the lock:
it pushes when the branch is ahead, and retries a failed push after `push_backoff_s`
seconds.

`moxi_repo.save_spec(spec)` saves a spec whose callables are `"pkg.mod:fn"` strings into
the repo's git config, and `moxi_repo.commit_saved(root, name)` runs it. A saved spec's
`message` is kept as the summary line itself, not a target: `message="pkg.mod:fn"` makes
that text the commit's subject, and a callable `message` can't be saved.

- `moxi repo commit PATH [--spec NAME] [--wait-s SECONDS] [--json]` runs one saved spec, or
  every spec saved in that repo, and prints each pass's line. It exits 1 on an error or a
  failed push, 3 when a pass was refused, 75 when `--wait-s` ran out on a busy lock, else 0.
- `moxi repo specs [PATH] [--json]` lists saved specs, and specs that ran without being
  saved, each with its last result. Its JSON also gives a saved spec's last commit (its
  author's newest) and each branch's last pushed commit.
- `moxi repo specs remove PATH NAME` forgets a spec: its saved section, its state and its
  doctor records.

A failure the committer can't show its caller, such as a refused pass run from a timer, is
kept as a doctor record, replaced on each pass and cleared once the condition ends.

repo also exports the small git helper the committer uses:
`moxi_repo.git(root, *args, timeout_s=30)` runs git with no prompts and git's local and
pathspec variables removed, `moxi_repo.changes(root, staged=True)` returns the staged (or
`rev_range=(a, b)`) changes as `Change(status, path, old_path, blob)`, and
`moxi_repo.literal_pathspec(path)` gives a path's literal pathspec form.

## Projects

repo keys every repo by its path. With the projects module installed
(`moxi install projects`), these work too:

- an argument that isn't an existing path, has no path separator in it (`/`, and `\` on
  Windows) and doesn't start with `~`, is a project key (matched ignoring case) wherever a
  command takes PATH. An unknown key exits 2;
  a projects store that can't be read exits with projects' reason and fix;
- `git-hooks list` and `specs` show each repo's project key;
- `git-hooks list --projects` adds every project's repo, including those repo hasn't
  touched, shown as not indexed.

A projects error leaves the key column empty with one line; a listing never fails because
of it. Without projects, paths work as before. The git hooks never ask projects anything.

## Doctor checks, upgrades and uninstall

### What `moxi doctor` shows

`moxi doctor repo` adds these rows. They only read: nothing is written or cleared.

- **git**: the git on `PATH` against 2.39, and the mode a repo that isn't pinned gets. On
  macOS also `/usr/bin/git`: when it is below 2.54 and a repo uses config mode, a commit
  it makes runs no piece there, and the row says to pin that repo to shim mode
  (`moxi repo git-hooks add PATH --mode shim`).
- **The index** (`index`): a `fail` row when repo's index of repos can't be read. Nothing
  overwrites it then, and the verbs that need it refuse, since it is the one list of the
  repos repo has written to; fix or remove the file named, then index each repo again with
  `moxi repo git-hooks add PATH`.
- **Each indexed repo** (`hooks PATH`): every event's hook entry, shim or launcher is
  there and is repo's; the interpreter and runner it names exist (a `fail` row when one is
  missing; a `warn` row when it names another environment's); a shim another tool moved
  aside (`<event>.legacy`, which that tool runs, is fine, and its interpreter and runner
  are checked as a shim's are; any other name means the pieces don't run); a chain line still needed, with the line to add; a launcher missing where
  the chain line is in place, which makes the pieces skip quietly; a registered piece that
  no installed module declares (a `fail` row when it is enabled and closed, since it then
  refuses every commit). With nothing wrong, one `ok` row gives the mode and each event's
  pieces. Most fixes are `moxi repo git-hooks refresh PATH`.
- **Each spec** (`spec PATH NAME`): changed paths that have waited more than an hour past
  the spec's interval; commits not pushed to its remote, with the last push error; paths
  left out for over an hour, with why and since when (a path someone else staged that no
  commit takes, a file over `max_bytes`); and a push record no pass will clear by itself,
  for a spec that isn't saved or a branch no pass pushes any more.
- **Uncovered files** (`uncovered PATH`): in a repo with a saved spec, changed or
  untracked files, not ignored, that no saved spec's paths cover. Up to ten are named,
  with the count.
- **The ledger** (`ledger KEY`): a `warn` row for each open doctor record whose subject is
  gone (its repo, piece, spec or branch), or whose piece lacks its `recorded` flag. The
  next `refresh`, `git-hooks` or `specs` verb on that repo, or the repo's next committer
  pass, takes care of it: it clears a record whose subject is gone, and sets a missing
  `recorded` flag.
- **Projects** (`projects`): `ok` when a project key works in place of PATH; `off`, with
  the fix `moxi install projects`, when projects isn't installed, is broken, or doesn't
  offer the interface repo reads.

The core shows the records repo leaves as `fail` rows of their own (`record KEY`): a piece
that broke or returned `record`, a refused or failed commit pass, a failed push. Each goes
when its condition ends; `moxi doctor --clear repo KEY` removes one by hand.

### Upgrades

Running `moxi install repo` again is how you upgrade repo, and each run runs repo's
setup: `refresh` over every indexed repo, so an install that moved the
environment rewrites every hook entry, shim and launcher to name the new interpreter. The
doctor flags repo when its setup last ran at another version, with `moxi install repo` as
the fix. One repo's problem, such as a repo that no longer exists, is printed with its
fix and stays as a doctor row; the install still finishes. The setup fails only when it
can't run at all: no git 2.39 or later while repos are indexed, or repo's config or its
index can't be read.

`refresh` stays for what an install doesn't see: a git upgrade or downgrade, or a change
of `git_hooks.mode`.

### Other modules' upgrades, and uninstall

repo listens to every module's install and uninstall (through the core's `moxi.installs`
group), so a module's pieces in your repos follow the module:

- **A module installed or upgraded.** Last in each `moxi install <module>`, repo reads that
  module's declarations again and, in every indexed repo, rewrites each of its pieces whose
  event, function, fail policy or order changed, with one line per piece. Only those four
  keys are rewritten: whether the piece is enabled, its `--set` settings and an open doctor
  record stay as they were. A piece the module no longer declares is removed, as `remove
  PATH PIECE` removes it, with a line naming it and the repo. So an upgrade that moves a
  piece's function doesn't leave a closed piece refusing every commit.
- **A module uninstalled.** `moxi uninstall <module>` removes that module's pieces from
  every indexed repo, and forgets its committer specs there, saved or not, with their
  state and doctor records, as `moxi repo specs remove` does.

A repo that can't be read (a broken git config, say) is named with the reason and left as
it is; the other repos go on. A piece whose new declaration is refused is left as it was,
with a line.

**`moxi uninstall repo`** first takes out of every indexed repo everything repo wrote
there, as `moxi repo git-hooks remove PATH` does: its git config sections, hook entries
and shims, each existing git hook it moved to `<event>.local` moved back, the launcher, and
`<git dir>/moxi-repo/`. It names each git hook file that still holds a chain line, which
does nothing once the launcher is gone, for you to delete. A git hook it moved aside whose
place another tool's hook has taken stays where it is, named, for you to merge or delete.
An indexed path that is no longer a repo is dropped from the index with one line; when
its git dir is still there with no work tree (a bare repo whose last worktree was
removed), what repo wrote in that git dir goes too, and the line says what went. When
that git dir's `core.hooksPath` points outside it, repo removes only its own shims there,
says so, and names each chain line and each git hook it moved aside that it leaves there.
Then every doctor record repo left is cleared.

It reads every indexed repo before it removes anything. If one can't be read (a broken
git config, say), it removes nothing and the uninstall stops with the reason, before
repo's package is removed; fix it and run the uninstall again. If a step still fails once
removing began, it stops there: that repo and the rest stay indexed with their records,
and the last line names the repos already cleaned. With `--purge` the uninstall also asks
whether to remove repo's index and its ledger of records.

## The git hooks page

With the `ui` extra, the browser UI shell shows a git hooks page: for each indexed repo,
its problems, each git event with its pieces in the order they run, the existing git hook
and the chain line's state, each committer spec's last pass, last commit and pushes, and
the doctor records repo has open. The page reads what `moxi repo git-hooks list --json`
and `moxi repo specs --json` print, and changes nothing: each action is a `moxi repo`
command to copy and run in a terminal.

## Install

Linux, Python 3.12 or later, git 2.39 or later, and [uv](https://docs.astral.sh/uv/).
Pushing needs a remote you can already push to without a prompt.

repo also has macOS and Windows code. On macOS it takes the Xcode command line tools' git
or Homebrew's; on Windows, Git for Windows. The repository's CI runs repo's tests on both
without gating, but nobody runs repo there yet, and the Windows path, where git runs the
hook entries and shims through Git for Windows, has never been run on a real machine:
treat macOS and Windows as untested.

```sh
uv tool install moxi
moxi install repo
```

The first line installs `moxi`, Moxi's core, which provides the `moxi` command; the
second adds repo beside it and runs repo's setup. The repository's README, at
[CodeManEthan/moxi](https://github.com/CodeManEthan/moxi), covers the `moxi` command and
lists the other modules.

## Dependencies

`moxi`, Moxi's core. The projects module is optional and reached only when installed.
The `ui` extra (`moxi-ui`, the browser UI shell) adds the git hooks page; see
[The git hooks page](#the-git-hooks-page).

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/main/packages/repo/LICENSE).
