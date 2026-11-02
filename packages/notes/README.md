# moxi-notes

Moxi's notes module gives every agent session a notes directory, snapshots your notes
folder into git, and keeps secrets out of what it commits.

1. **It finds a session's notes directory.** From your notes root, a project's own notes
   folder (with the projects module), or the session's path: `~/code/alpha` gets
   `~/notes/code/alpha`, whichever subfolder, linked worktree or symlinked spelling of the
   project the session starts in. A session in your home folder itself, outside it, or in
   an excluded folder gets none.
2. **It says it at session start.** One line, `Notes directory: <path>`, through a turns
   start listener, at every start (a new session, a resume, a clear, a compact). Each
   project's `handoffs/` folder lives in its notes directory.
3. **It snapshots the notes repo.** When an agent turn or session ends, a background pass of
   repo's committer commits the notes folder, so no turn waits for git. An optional timer
   covers edits made outside sessions.
4. **It keeps secrets out of the history.** A pre-commit gate and a pre-push gate, both
   registered with repo, judge every file a commit or a push would carry against secrets'
   `credentials` and `private-names` sets. A file with a secret is left out of the commit,
   or committed redacted, as you choose at install. A push that would carry one is refused.
5. **It checks the notes' frontmatter** against document types you declare, without
   writing anything, and can add the keys it can infer when you ask it to.

```sh
moxi install notes
moxi notes dir                 # this folder's notes directory
moxi notes snapshot            # commit a snapshot now
moxi doctor notes
```

## Commands

| Command | Does |
|---|---|
| `moxi notes dir [PATH] [--json]` | print the notes directory for PATH (default: here); exit 1 when it has none |
| `moxi notes handoffs dir [PATH]` | print that notes directory's `handoffs/` folder |
| `moxi notes snapshot` | run one snapshot pass now and print repo's line |
| `moxi notes scan [--json]` | what a snapshot would leave out now, with rules and lines; changes nothing; exit 1 when any file would be left out |
| `moxi notes inbox list [--json]`, `add SLUG`, `rename SLUG NEW` | inbox one-offs: folders of notes for work with no project |
| `moxi notes setup` | ask setup's questions again and write what they change |
| `moxi notes validate [--json] [--unfiled] [--limit N]` | check every note's frontmatter against the types file; writes nothing; exit 1 only when the types file can't be used |
| `moxi notes backfill [--apply] [--json] [--limit N]` | the required keys each filed note lacks and that can be inferred; a dry run unless `--apply` |

`MOXI_NOTES_DIR`, set by the launcher for one launch, overrides the computed folder for
that session and for `moxi notes` run in it. Exit codes: 0 done, 1 an error, no notes
directory, a scan that finds a file a snapshot would leave out, a types file validate or
backfill can't use, or a backfill write that failed, 2 bad arguments or a bad setting, 3
refused.

`moxi notes scan` judges the notes folder's files the way the setup preview does, which
is the way a snapshot does. Under `leave-out` it lists every file with a finding. Under
`redact` it asks the clean filter about each one: a file the filter redacts is listed as
committed with its hits blanked, and one it can't redact is listed as left out. A
symbolic link whose target holds a finding is listed as left out too, since git runs no
clean filter on a link. A file
already committed stays as committed until it changes. `--json` prints one object, the
same shape whatever the exit code, and other tools read it, so the shape stays stable:

```text
{"root": str, "action": "leave-out" | "redact", "judged": int,
 "left_out": [{"path": str, "reason": str,
               "findings": [{"rule": str, "line": int | null, "note": str}]}],
 "redacted": [{"path": str, "findings": [...]}],
 "authors": int, "allowlist_problem": str | null, "error": str | null}
```

Lists are sorted by path. `rule` is `<set>/<rule id>`. `line` is 1-based, or null for a
whole-file finding such as `notes/too-large`. No finding carries the text that matched.
`authors` counts the commit identities the push gate would refuse for a private name.
`error` says why nothing could be judged, and the lists are then empty.

## What setup costs, and what it asks

`moxi install notes` brings turns, repo and secrets with it. You need a notes folder, which
setup makes a git repo if it isn't one, and, only if you want snapshots pushed, a remote: a
private repo on GitHub or your own git server that you can push to without a prompt (an
ssh key or a credential helper). No other account.

Setup runs at `moxi install notes` and again whenever you run `moxi notes setup`. At a
terminal it asks; with nobody at the keyboard it keeps the current value (or the default),
prints the command that would change each, and goes on.

1. **Where are your notes?** Default `~/notes`. When that folder is in no git repo, setup
   offers to make it one (`git init`, default yes). That is the only `git init` notes ever
   runs: nothing in notes makes a git repository inside your notes folder, because the
   committer leaves a nested repository out of every snapshot. A notes folder that sits
   inside another repo, below its top, gets no snapshots or gates; make it a repo of its
   own.
2. **What a commit does with a secret.** Leave the file out until you fix it (the default),
   or redact it: the committed copy gets `[REDACTED:<rule>]` and the file on disk keeps the
   text. Read the warning below before you choose redact.
3. **Where snapshots are pushed**, when the repo has remotes and no push remote is set.
   Enter or `none` means no push.
4. **A preview** of what the snapshots would hold back, before the first one runs (below).

Then it writes the settings, registers the two gates in the notes repo (and enables one
you had disabled), writes or removes the clean filter for the action you chose, and saves
the snapshot spec. The timer's question comes from `moxi install` after setup, default no.

Changing `root` or `action` later by hand takes `moxi notes setup` to take effect; until
then `moxi doctor notes` shows the mismatch.

### The preview, and your private names

The preview judges every tracked and untracked file of the notes folder (ignored files and
nested repos aside) as a snapshot would, and prints how many hold a secret, the first ten
with their rules and lines, and how many commit authors the push gate would refuse.

Your private names list (in secrets' config) usually holds your own login and user name. A
note that spells a path under your home spells your login, so the list hits every such
note: on one real notes tree that was 3,250 of 6,869 tracked files. Until you allow it:

- with leave-out, each such file leaves the snapshots at its next change;
- with redact, the committed and pushed copy has those paths blanked, and a cloud session
  that reads the pushed copy sees them blanked;
- a commit you make by hand under that name is refused at push, because the push gate
  scans each commit's author and committer for private names.

So the preview ends with the allowance lines that would keep what it found, and the full
path of the private allowlist they go in, `<common git dir>/secrets-allow-private` (for a
plain repo, `~/notes/.git/secrets-allow-private`):

```text
*	private-names/private-name	text:<the word>	<today>	paths in my own notes
@author	private-names/private-name	text:<the word>	<today>	my own commits
```

Add the ones you mean to keep, as they are (the fields are tab-separated). The next
snapshot takes the files they keep.

## Snapshots

Each snapshot is one pass of repo's committer with notes' spec, `notes/snapshot`: the whole
notes root, at most one commit per `snapshot.interval` minutes, authored `moxi notes` with
the email `notes+<device>@moxi.invalid` (one author per machine, so a snapshot fetched from
another machine never throttles this one), and pushed to `snapshot.push` when set.
The spec is rebuilt from notes' config at every pass notes starts, so a config edit takes
effect at the next pass; `moxi repo commit` runs it as last saved.

- An ended turn starts a pass at most once a minute; an ended session always starts one.
  Neither waits for it.
- A session's last edits are committed within `snapshot.interval` + 6 minutes after it
  ends (21 minutes with the defaults), while the machine stays up and `snapshot.auto` is
  on, even when no other event comes. A failed push of that commit is tried once more
  after the push backoff. If the notes repo's lock is held for five minutes, the wait ends
  and `moxi doctor` shows a `settle` record.
- The timer (`snapshot.timer`, off by default) runs the same pass every 15 minutes outside
  sessions, through systemd, launchd or the Task Scheduler.
- A pass never pulls. A push the remote rejects (another machine pushed first) stays a
  push record in `moxi doctor repo` until you pull.

Repo's doctor rows report the last snapshot against its interval, commits not pushed and
paths left out for over an hour.

## Secrets

### What is judged

Every judge reads whole files from git's object store, never a diff, and scans each with
the `credentials` and `private-names` sets at its path. A file larger than `max_mb` is a
finding of its own: raise `max_mb` to have it judged. Two allowlist files keep hits you
have judged, each read where secrets accepts it:

- `.secrets-allow` at the top of the notes folder: credentials lines only, and it may be
  committed;
- `secrets-allow-private` in the notes repo's common git dir, where git never tracks it:
  lines of any set, `@author` lines and `text:` matches for private names.

A `.secrets-allow-private` at the top of the folder is never read (and a snapshot would
commit it); the doctor warns about one.

An allowlist with a malformed line reads as empty, which only takes allowances away: every
file with a hit is left out and every push with one is refused until you fix the line, and
the doctor names the file and the line.

**A notes repo whose git dir isn't a folder named `.git` has no private allowlist.**
secrets reads the private file only from inside a `.git` folder or its own config folder,
so a notes repo made with `git init --separate-git-dir`, or one whose common git dir is a
bare `notes.git`, can't keep one. That fails closed: no private allowance applies, every
file with a private name stays left out, every push with one is refused, and the doctor's
`allowlist` row fails. Keep the notes repo's git dir at `<notes>/.git`.

### What a hit does

| commit | `action = leave-out` | `action = redact` |
|---|---|---|
| a snapshot | the file is left out and the rest commits; the pass's record names it | the clean filter committed it redacted; a file it couldn't redact is left out |
| a commit by hand | the file is left out and the rest commits, with one line naming it | refused, naming the fixes: `git add` the file again so the filter redacts it, remove it, or add an allowance |
| a commit by hand that concludes a merge, a cherry-pick, a revert or a rebase step | refused, with the fixes and the index unchanged | the same |
| a push | refused | refused |

On a hand commit that concludes a merge or a rebase step, leaving a file out would drop the
change the commit brings in, so the gate refuses instead. `git rebase --continue` makes its
commit without running the pre-commit git hook at all, so a secret in a rebase step you
resolve and continue reaches your local history; the push gate refuses it at push. Conclude
such a step with `git commit` to have it judged then.

The push gate is the backstop: it judges every commit the remote lacks, each new file and
each author and committer line, and refuses the push on any hit not kept by an allowlist.

### Redaction, and what it costs you

With `action = redact`, a git clean filter puts the redacted text in the index at
`git add`; the file on disk keeps the original. A file it can't redact (binary, over
`max_mb`, one whose redaction would leave nothing but a placeholder, or one the filter
fails on) is staged as it is, then left out by the snapshot and refused at push. The
filter is not required, so its failure costs only the files it failed on, never another git
command. The attribute that runs it lives in the git dir's `info/attributes`, never in a
tracked `.gitattributes`.

**What git writes back is the redacted copy.** Checking out another commit, `reset --hard`,
`stash` and its `pop`, a merge, a rebase, and a pull that changes the file all write the
committed text over your file, and the original is lost. Another machine that clones the
notes repo gets the redacted text.

**A clone without the filter commits unredacted text.** git ignores an attribute whose
driver isn't configured, so a second clone of the notes repo, set up by hand, commits
secrets as they are. notes guards only the clone at its configured root: the doctor fails
there when `action = redact` and the driver or the attribute is missing. A clone notes was
never told about has no gates at all, so its commits and pushes are not judged.

## Validation

`moxi notes validate` reads every `.md` file under the notes root and reports what its
frontmatter (the `---` block at the top) says against the document types you declare. It
writes nothing, and everything it finds is information: it exits 0 whatever it finds. It
exits 1 only when the types file can't be used, with the file, the line and the reason,
or when the root is not a folder.

You write the types file; notes never does. It is TOML at `doc-types.toml` under the root
(the `types` setting):

```toml
required = ["type", "project", "description", "tags", "updated"]   # the default

[[type]]
name = "design"
homes = ["*/designs/*/design.md"]   # globs under the notes root: * stays in one folder, ** crosses
nature = "record"                   # record, living-state or ephemeral
priority = 50                       # optional; the highest matching type wins a file
status = ["open", "closed"]         # optional: the values a status: key may take
frozen = false                      # optional: a corpus nothing writes any more
exempt = false                      # optional: another tool's format, never opened
```

Each file is `filed` (a type's homes win it, and its frontmatter is checked), `frozen`,
`exempt` (both counted, never opened) or `unfiled` (no home matches; counted by top
folder, never opened). With no types file every file is unfiled, and each one's
frontmatter is still checked against the default required keys. The findings are
`missing-frontmatter`, `unreadable-frontmatter`, `missing-required`, `unknown-type`,
`type-disagrees-with-home`, `unknown-keys`, `unknown-status` and `ambiguous-home` (two
types claim the file at the same priority). The walk never descends into `.git` or a
vendored folder (`node_modules`, `.venv`, `venv`, `site-packages`, `vendor`,
`bower_components`) and never follows a symlink; it counts each one it skipped.

`moxi notes backfill` proposes, for each filed note, the required keys it lacks and that
can be inferred: `type` from its home, `project` (with the projects module) from the
project whose notes folder holds it, and `updated` from its modified date. It never
infers `description`, `tags` or any other key a person writes, never replaces a key that
is there, and gives no proposal for a note whose frontmatter it can't read or whose home
is a tie. It writes only with `--apply`, only regular files under the root, and never
through a symlink; a second run adds nothing. The next snapshot commits what it wrote.

## Doctor

`moxi doctor notes` shows these rows:

| row | ok | otherwise |
|---|---|---|
| `root` | the notes folder, a git work tree's top | `fail` when missing; `warn` when no git repo, or inside one below its top (snapshots and gates off) |
| `snapshot` | a trigger is on, and the saved spec equals notes' config | `off` with `snapshot.auto` and the timer both off; `warn` when the saved spec differs or none is saved |
| `gates` | both gates registered and enabled in the notes repo | `fail` when either is missing or disabled |
| `filter` | under redact: the driver, its attribute and its interpreter in place, not required | `fail` when one is missing under redact; `warn` for `required = true`, a driver written for another interpreter, or leftovers under leave-out |
| `allowlist` | both files read, each where secrets accepts it | `fail` on a line that can't be read, or a git dir with no private allowlist; `warn` for a `.secrets-allow-private` at the root |
| `topics` | with projects: every topic's folder exists and holds no `.git` | `warn` for a missing folder or a `.git` in one; `off` without projects |

The fix for each setup row is `moxi notes setup`.

## Settings

`moxi config notes` shows every key; each is read where it is used, so an edit takes effect
at the next read.

| key | default | what |
|---|---|---|
| `root` | `~/notes` | the folder that holds your notes; a git repo for snapshots |
| `prefix` | `""` | a subfolder under the root for the computed folders, topics and inbox, such as your user name |
| `exclude` | `[]` | folders whose sessions get no notes directory |
| `action` | `leave-out` | what a commit does with a file that holds a secret: `leave-out` or `redact` |
| `max_mb` | `10` | the largest file a snapshot takes and a gate reads |
| `types` | `doc-types.toml` | the document types file, relative to the root |
| `snapshot.auto` | `true` | snapshot when an agent turn or session ends |
| `snapshot.interval` | `15` | at most one snapshot commit per this many minutes |
| `snapshot.push` | `""` | the remote snapshots are pushed to; empty means none |
| `snapshot.author_name` | `moxi notes` | the name on snapshot commits |
| `snapshot.author_email` | `""` | the email on snapshot commits; empty means one per machine at `moxi.invalid` |
| `snapshot.timer` | `false` | also snapshot every 15 minutes outside sessions |

## Uninstall

`moxi uninstall notes` removes the clean filter's driver and attribute from the notes repo;
repo removes the two gates and the saved spec. Your notes folder and its history are never
touched.

## Install

Python 3.12 or later and git 2.39 or later, on Linux, macOS or Windows (Git for Windows),
and [uv](https://docs.astral.sh/uv/). Each snapshot is one short background process at most
once a minute, plus at most one sleeping process per notes root waiting to commit an ended
session's last edits, and, while the remote fails, one sleeping push retry per recent
commit.

```sh
uv tool install moxi
moxi install notes
```

Before the modules are on PyPI, install the core from git and give `moxi install` a git
source for each module notes needs:

```sh
uv tool install git+https://github.com/CodeManEthan/moxi#subdirectory=packages/moxi
moxi install notes \
  --from 'moxi-turns @ git+https://github.com/CodeManEthan/moxi#subdirectory=packages/turns' \
  --from 'moxi-repo @ git+https://github.com/CodeManEthan/moxi#subdirectory=packages/repo' \
  --from 'moxi-secrets @ git+https://github.com/CodeManEthan/moxi#subdirectory=packages/secrets' \
  --from 'moxi-notes @ git+https://github.com/CodeManEthan/moxi#subdirectory=packages/notes'
```

On Windows the clean filter runs through Git for Windows' `sh`, and paths compare without
case. On macOS a path spelled in another case than your home gets no computed folder, and
Apple's git 2.39 runs the gates through repo's shim mode.

The front door's README, at
[CodeManEthan/moxi](https://github.com/CodeManEthan/moxi), covers the `moxi` command and
the other modules.

## Dependencies

`moxi`, Moxi's core; `moxi-turns`, which calls the start line and the snapshot trigger;
`moxi-repo`, the committer and the git hook runner; and `moxi-secrets`, the pattern sets.
The `projects` extra (`moxi-projects`) adds a project's own notes folder, the `topic` kind
and projects' remove check. notes reads every other module through its Python API and
parses no module's printed output.

## License

MIT. See [LICENSE](LICENSE).
