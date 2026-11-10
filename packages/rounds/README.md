# moxi-rounds

Moxi's record of a piece of work: its questions, the operator's rulings on it, and each
project's standing rulings.

A round is one proposal, from its design to the step that puts what was built where it
goes. rounds is for anyone who directs agents that write code in git repos: one person,
here called the operator, with a lead agent that runs the round and workers that build
and check. Everything the operator decides is recorded as his, with a pointer to where he
said it, and is shown back to him as it was recorded.

```sh
moxi projects add alpha --root ~/code/alpha --notes-root ~/notes/alpha
moxi rounds open alpha/search --description "Search over the notes folder"
moxi rounds ask alpha/search "Should search read archived notes too?" --from comms:12 \
    --option a="Yes, all notes" --option b="No, live notes only"
moxi rounds list --waiting                                  # alpha/search Q1, open
moxi rounds rule alpha/search Q1 --chose b --record comms:14     # Recorded as C1
moxi rounds mark alpha/search C1 --shown --record comms:17
moxi rounds show alpha/search
```

rounds starts no agent, registers no git hook and draws no page. It writes the open
questions out as markdown and as data, and whatever shows them to the operator is outside
it.

## What this version does

This version keeps the record: a round's log, its questions and rulings, a project's own
list of questions and its register of standing rulings, and the files generated from
them. Its verbs are `open`, `ask`, `rule`, `mark`, `show`, `list` and `drop`.

It does not yet do the rest of a round:

- design versions and their verification;
- the command that says what the next step is and who does it;
- builds, their worktrees and their checks;
- finishing a build, which puts it where it goes, and closing a round;
- decisions a build records for the operator to ratify;
- answers prepared ahead for a sitting with the operator;
- parent rounds.

So a round opened now records its flow and steps and never moves through them, and it has
no stages: `ask --blocks` takes only `none`, `mark --blocks` only `all`, and `rule
--stages` and `rule --until B<n>` are refused, since each names a stage the round does
not have. A round ends only by `drop`. Config keys that belong to the parts above are
declared and checked, and nothing reads them yet (see Config).

## Commands

A round's name is `<project>/<slug>`: a project's key from `moxi projects`, and a slug of
lower-case letters, digits and `-`.

| Command | Does |
|---|---|
| `moxi rounds open ROUND --description TEXT [--flow NAME] [--record PTR]` | open a round: make its folder and record what it is for and its flow; `--record` points to where the operator gave the go |
| `moxi rounds ask ROUND TEXT --from PTR [--blocks none] [--option K=TEXT ...]` | put a question to the operator in a round, as `Q<n>`; it holds up every stage unless `--blocks none` |
| `moxi rounds ask PROJECT TEXT --from PTR [--option K=TEXT ...]` | put a question on the project's list, as `P<n>`, where it holds up nothing |
| `moxi rounds ask ROUND\|PROJECT ID [--option K=TEXT ...] [--retire K ...]` | add options to a question on record, or retire them |
| `moxi rounds ask ROUND --adopt P<n> [--record PTR]` | bring a question of the project's list into the round |
| `moxi rounds ask PROJECT --move ROUND:Q<n> [--record PTR]` | put a round's question on the project's list |
| `moxi rounds rule ROUND --text TEXT --record PTR [--reason TEXT]` | record a ruling of the round, as `C<n>` |
| `moxi rounds rule ROUND --standing --text TEXT --record PTR [--scope SPEC]`, `rule PROJECT --text TEXT --record PTR [--scope SPEC]` | record a standing ruling in the project's register, as `S<n>` |
| `moxi rounds rule ROUND\|PROJECT Q<n>\|P<n> (--text TEXT \| --chose K) --record PTR` | answer a question |
| `moxi rounds rule ROUND\|PROJECT park:Q<n> --record PTR [--until round:ROUND]` | park a question, so it is not put to him until it is reopened; `--until` names another round it waits for |
| `moxi rounds rule ROUND narrow:Q<n>=none --record PTR` | make a question hold up nothing |
| `moxi rounds rule ROUND\|PROJECT withdraw:Q<n> --record PTR` | withdraw a question |
| `moxi rounds rule ROUND\|PROJECT ... --supersedes C<n>\|S<n>` | replace an earlier ruling of the same thing |
| `moxi rounds rule ROUND\|PROJECT revoke:C<n>\|S<n> --record PTR` | take a ruling back and put nothing in its place |
| `moxi rounds rule PROJECT readback=TIME --record PTR` | record that he read the list of rulings not yet shown to him through TIME |
| `moxi rounds mark ROUND Q<n> --blocks all`, `mark ROUND Q<n> --reopen` | make a question hold up every stage again, or reopen a parked one |
| `moxi rounds mark ROUND\|PROJECT C<n>\|S<n>[,...] --shown --record PTR` | say that rulings were shown to the operator as recorded |
| `moxi rounds show ROUND [--json]` | one round: status, flow, questions by state, rulings and history |
| `moxi rounds list [PROJECT] [--closed] [--json]` | the open rounds, or with `--closed` the ended ones, each with its counts |
| `moxi rounds list [PROJECT] --waiting [--json]` | what waits on the operator: open questions, parked ones, rulings not yet shown |
| `moxi rounds list ROUND\|PROJECT --questions [--view docket] [--json] [--out DIR]` | the questions written out as markdown; `--out` writes `questions.md` and `questions.json` into DIR |
| `moxi rounds drop ROUND --by operator\|lead --record PTR [--withdraw-questions]` | end a round short of its work |

Every writing verb takes `--as NAME`, who is doing it; the default is the user the command
runs as. `moxi rounds VERB --help` has each option in full.

Exit codes: 0 done; 1 an error; 2 asked wrongly, such as a name that is no round's name or
a config value of the wrong type; 3 refused, such as a condition that is not met, no such
project or round, a log that can't be read, or a lock another process held for 30 seconds.
A refusal names every condition that is not met, each with the command that meets it, and
writes nothing.

## Pointers

A question says where it came from and a ruling says where the operator said it. Each is
one token:

- `comms:<digits>`, a message;
- `<session>@<UTC time>`, a session id of 8 or more hex digits and a time such as
  `2026-01-02T03:04Z`;
- an `https://` URL;
- `file:<path in the round's folder>#<anchor>`, as `file:design.md#index` for a section
  of the design.

A question with no `--from` and a ruling with no `--record` are refused. A question that
is the operator's own takes `--record` in place of `--from`. `mark --shown` takes a
message, a session or a URL, and never a file, the pointer of one of the rulings it
marks, or a message from before one of them was said.

## Questions

A question is one sentence on one line. It may offer two to four options, each a line
under a short key; a key keeps its line, and a retired key is never used again, so to
correct a line retire its key and add the right one under a new key. A question is open,
parked, ruled, withdrawn or moved.

A round's question holds up every stage of the round until the operator rules on it.
Only he lets go of it: his answer, `park:`, `narrow:` and `withdraw:` are rulings.
`mark` changes a question only towards holding up more, and reopens a parked one.

A project's list holds questions that have no round yet, and they hold up nothing.
`--adopt` brings one into a round, where it holds up every stage. `--move` puts a
round's question on the list, which ends what it holds up, so it needs the operator's
pointer.

## Rulings

A ruling is a plain sentence or a few that read on their own. A round's rulings are
`C<n>`. A standing ruling, `S<n>`, goes in the project's register and outlasts the round;
`--scope` names the repos and paths it applies to, as `KEY` or `KEY:GLOB`, several
separated by commas.

A second ruling of one thing is refused without `--supersedes`, and `revoke:` takes a
ruling back. A question whose answer was revoked is open again.

Every ruling is printed as it was recorded:

```text
Recorded as C1: No, live notes only
in answer to Q1: Should search read archived notes too?
show him this under "Recorded as" in your next message, then:
  moxi rounds mark alpha/search C1 --shown --record <pointer to that message>
```

Until `mark --shown` names it, a ruling is listed by `list --waiting` among the rulings
not yet shown to him, with the project's own and its rounds' together. `rule PROJECT
readback=TIME` records instead that he read that list through TIME, the time of the last
ruling on it, and covers every ruling of the project recorded up to then.

## Dropping a round

`drop --by operator` ends any round, with a pointer to where he said so. `drop --by lead`
ends only a round that holds none of the operator's acts: no ruling, no question asked
with his pointer, no opening on his go. The round's open and parked questions go to the
project's list as they are, or with `--withdraw-questions`, which is the operator's
alone, are withdrawn.

A dropped round takes no new question and no new ruling. A correction of one of its
rulings is still recorded, and its rulings are still marked as shown.

## The store

Each project has one rounds folder: its `dir` setting, by default `rounds` in the
project's notes folder. The logs live there, beside the documents they describe, so they
are backed up and pushed with them.

```text
<dir>/project.jsonl          the project's log: standing rulings and the project's questions
<dir>/rulings.md             generated: the register
<dir>/questions.md           generated: the project's questions
<dir>/<slug>/round.jsonl     the round's log
<dir>/<slug>/round-note.md   generated: the round's state and history
```

A log is only appended to. The three generated files are written again whole after every
write to their log, so an edit to one is lost; keep your own notes on a round in another
file of its folder. A folder of the rounds folder that holds a `round.jsonl` is a round,
however it got there: one that arrived by git is listed as soon as it is there.

A rounds folder belongs to one project. Two projects whose folders are one folder, once
every link on the way is followed, are both refused until one is given a folder of its
own.

`open` refuses a folder that holds a `round-note.md` and no log: the round there belongs
to another tool, and this one takes another slug.

rounds keeps two things outside the rounds folder (`moxi paths rounds` names the
directories):

- `index.db` in its data directory, a cache of what the logs say so that a listing need
  not read every log. Deleting it loses nothing: `moxi rounds list` builds it again from
  the logs.
- Lock files in its state directory, `locks/projects/<key>.lock` for a project's log and
  `locks/rounds/<key>/<slug>.lock` for a round's. They hold nothing but the pid of the
  process writing, and are never in a rounds folder, so none is backed up or pushed.

A log that two machines both appended to, or one with a line this version can't read, is
not used at all: every verb on it refuses and names the file, the line and the way back.
When a log's earlier lines changed outside this machine, it is read as it stands and
`show` says since when.

## Config

`moxi config rounds` shows every key, and `moxi config rounds init` writes the file,
`config.toml` in rounds' config directory. This version reads:

| Key | Default | What it does |
|---|---|---|
| `defaults.dir` | `"rounds"` | a project's rounds folder: absolute, or relative to the project's notes folder |
| `defaults.flow` | `"full"` | the flow a round opens with when `--flow` names none |
| `flows` | `{}` | your own flows by name, each a list of steps; one named like a built-in flow replaces it |
| `tiers` | `["fix", "small", "full"]` | the flows from lightest to heaviest; each must be a flow |
| `projects` | `{}` | per-project tables, `[projects.<key>]`, which may set `dir` and `flow` for that project |

```toml
[projects.alpha]
dir = "~/work/alpha-rounds"
flow = "small"
```

The built-in flows are `fix`, `small` and `full`. `open` records the flow's steps in the
round, so a later edit of the flow does not change a round already open. Changing `dir`
moves no folder: rounds looks in the new place, and the rounds stay where they were until
you move them.

The file also takes `models`, `worktree_dir`, `defaults.ratify`, `defaults.trace`,
`defaults.check_on`, `defaults.idle_days`, `defaults.dormant_days`,
`defaults.slice_minutes` and `defaults.sitting_at`, and in a project's table `into`,
`check`, `finish`, `shared_paths`, `keep_branch`, `lock_ref` and `brief`. They are
checked when the file is read, and nothing in this version acts on them.

rounds reads no environment variable but the ones that place its directories,
`MOXI_ROUNDS_HOME` and the rest, and `MOXI_HOME`.

## The read API

Other modules read rounds in process through interface 1, reached with
`moxi.modules.load("rounds", interface=1)`. Each call gives the data its command prints
with `--json`:

| Call | Gives |
|---|---|
| `rounds(project=None, status="open")` | the rounds of every project or of one, open or `"closed"`: `moxi rounds list --json` |
| `round(name)` | one round's whole state: `moxi rounds show ROUND --json` |
| `waiting()` | what waits on the operator: `moxi rounds list --waiting --json` |
| `questions(name, view=None)` | the questions of a round or a project: `moxi rounds list NAME --questions --json` |

A call never writes, the index included. A round whose log can't be read is returned as
unreadable, with the file and the line, and is never left out. What a command refuses,
its call raises as a `moxi_rounds.errors.RoundsError`, with `reason`, `fix` and
`exit_code`.

## Doctor

`moxi doctor rounds` checks, besides the module, its settings and the index file:

| Row | Says |
|---|---|
| `git` | fails when git is missing, won't say its version, or is older than 2.17 |
| `rounds folders` | warns for each project whose rounds folder can't be worked out or is another project's too, with the setting that fixes it |
| `logs` | fails for each log that can't be read, with the file, the line and the way back |
| `index` | warns when the index says something else than the logs do; `moxi rounds list` brings it up to them |

The doctor only reads.

## Notes

rounds works without the notes module. A project's rounds folder is then its `dir` when
that is absolute, or `dir` inside the notes folder the project's own record names
(`moxi projects set KEY --notes-root DIR`); a project with neither is refused, with both
fixes. With the `notes` extra (`moxi-notes`), a project that names no notes folder gets
the one notes gives it by default.

## Install

Linux, Python 3.12 or later, [uv](https://docs.astral.sh/uv/) and git 2.17 or later.

```sh
uv tool install moxi
moxi install rounds
```

The first line installs `moxi`, Moxi's core, which provides the `moxi` command; the
second adds rounds beside it, with the projects module it needs, and offers its optional
extra, `notes`. The repository's README, at
[CodeManEthan/moxi](https://github.com/CodeManEthan/moxi), covers the `moxi` command and
lists the other modules.

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/main/packages/rounds/LICENSE).
