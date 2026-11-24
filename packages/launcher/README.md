# moxi-launcher

Moxi's launcher starts Claude Code and Codex sessions. A launch is one call: it takes at
most one preset, lays the flags you type over it, checks the result against a catalog of
each harness's flags, composes the command, writes a row to the launch log, and starts the
session in this terminal, headless, or (with slots) in a tmux slot. The CLI calls the same
library a script can import, in its own process; there is no service and no socket.

    uv tool install moxi
    moxi install launcher

The launcher needs only `moxi`, Moxi's core, which the first line installs with the
`moxi` command. Everything else is an optional link (see below). You need Claude Code or
Codex installed and signed in, with their own accounts; the launcher has no account and
no cloud service of its own. Install has the details.

## A launch

    moxi launcher run --model opus --brief "Read the design and start the build."
    moxi launcher run --seat planner
    moxi launcher run --project-preset docs --target headless --brief-file task.md
    moxi launcher run --harness codex --scratch -- --some-new-codex-flag

Each value of a launch comes from, each later one winning: the catalog's shipped default;
your `[defaults]`; the one preset you name; the flags you type. A launch names at most one
preset, a seat (`--seat NAME`: a saved setup for a standing agent) or a project preset
(`--project-preset NAME`: where to work), and never draws values from a second.

`--leave-out ROW`, given once per row, leaves a catalog row out of this launch whatever
your `[defaults]` or the preset sets: the row launches at its shipped default, as if no
layer had set it. Leave out the preset's place and the place in your `[defaults]` stands.
`moxi launcher run --seat planner --leave-out model` starts the planner seat with the
harness's own model. A row both typed and left out, `harness` (a launch needs one), and a
name that is no row of any harness, are refused. A row the harness doesn't offer can be left out too, so a seat that
sets a Codex row can still be launched under Claude.

The flags are the catalog's rows: the launcher's own (`--harness`, `--cwd`, `--project`,
`--scratch`, `--brief`, `--brief-file`, `--handoff`, `--target`, `--slot`,
`--instructions`, `--identity` and the rest) and the harness's (`--model`,
`--permission-mode`, `--sandbox`, `--add-dir` and so on). `moxi launcher catalog` lists
every row this install offers, and marks the rows the launcher sets itself and the rows
whose module is not installed. `--name` is the launcher's own row, the session's name
(below); it used to be passed on to the harness with a warning, which for Claude Code
set its display name. Claude Code still shows the name you give, and Codex, which has
no name flag, is no longer handed it. A flag the catalog does not know is passed on to the
harness, last on its argv, after one warning line; the words after `--` are passed on as
given. A passed-on flag that would set a row the launcher owns (the session id, a resume)
or a permission row is refused, so the permission check always sees it. So is one that
sets what a permission row sets another way: Claude's `--dangerously-skip-permissions`
and a `--settings` JSON string with a `permissions` mode, allow or deny list; Codex's
`--dangerously-bypass-approvals-and-sandbox` and `--approve-for-me` (and their hidden
aliases `--yolo` and `--not-so-yolo`), and a config override (passed on, or a `--config`
item) keyed `sandbox_mode` or `approval_policy`. A settings file, or a Codex profile, is
not checked: what it sets is up to you. (A bypass slot launch reads your settings file to
merge it into its own; see below.)

**Permission rows.** A row that sets how much the session may do without asking (Claude's
permission mode and tool lists, Codex's sandbox and approval) is a posture row. A launch
that sets one to something other than your default (your `[defaults]` value, else the
shipped one) is refused unless you add `--off-posture`. Put the posture you work with in
`[defaults]`.

**Where it runs.** `--target terminal` (the default) becomes the harness in this terminal:
on Linux and macOS the launcher execs it, on Windows it starts the harness and waits for it.
`--target headless` starts the harness's non-interactive mode (`claude -p`, `codex exec`)
detached, with the brief as its prompt, its output going to the launch's run log; a
headless launch needs a brief. With a comms identity it runs comms' wrapper instead
(below). `--target slot` starts the session in a tmux slot, and needs
the slots module (Linux and WSL2; macOS untested). `--slot` names the slot; a launch with
any other target that names one is refused, exit 2. A named slot whose tmux session is
gone (`missing`) is made again first; one held by another holder, a park's reservation
among them, is refused `held`. A slot launch that names no slot takes the lowest slot
with no session running and no hold, making a missing one again, else a new slot.

**How a slot launch starts.** Claude Code can open on a screen of its own before the
session runs: the folder-trust screen, the development-channels screen, and in
`bypassPermissions` the bypass-permissions warning. A Claude slot launch watches for every
one of them, whatever your settings say. A screen whose `startup.*` setting is on is
answered; one whose setting is off ends the launch at once, `screen-not-answered`, with
nothing pressed, the rows the screen showed, and the setting that would answer it. With
`startup.trust_dialog` on, the launch first marks its folder's git root trusted in Claude
Code's own config, so the trust screen does not show at all. A `bypassPermissions` slot
launch passes Claude Code a settings file of its own, `<request id>.settings.json` among
its run files, holding `skipDangerousModePermissionPrompt: true` for that launch only; a
`--settings` you pass is read (never changed) and merged into it, so the session gets one.
A Codex launch settles its folder's trust on its own command line (below), so Codex's
trust screen does not show.

Three more Claude Code screens are on the watch with no setting, and the launcher never
answers them: the first-run theme picker and the welcome screen ("Security notes ... Press
Enter to continue"), which show in a config folder Claude Code has never run in, and the
"Claude in Chrome extension detected" dialog, which shows once onboarding is done until
it is answered once. Each ends the launch at once, `screen-not-answered`, naming the
screen. For the first two, run `claude` once in a terminal with the same
`CLAUDE_CONFIG_DIR` and finish its setup; for the Chrome dialog, answer it once in a
terminal session.

Then every slot launch waits for its session to be ready, up to `ready.wait_s`, first
turn owed or not. With nothing that can say so (no turns), it ends `started` with the
reason `ready-unknown`: attach to the slot to see it. A launch of a new session (a fresh
launch or a resume) that finds a screen it won't answer, or no ready in time (`no-ready`),
ends `failed`, exit 1, naming the slot. The `no-ready` line names both causes a launch
can't tell apart: a screen the launcher doesn't know, and a session turns isn't running
in. The session is never stopped: it stays in its slot for you to attach to. A relaunch
has already exited the session it replaces, so the same
findings only flag it: its row stays `started` with a `flagged` event, a self relaunch's
run ends `first-turn-not-sent` (its handoff is not sent), a director's ends `relaunched`
with the finding as its word, and either tells the directors. A relaunch fails only when
its new session is not running.

**Codex folder trust.** Every Codex launch, interactive, under turns' host or headless,
passes its folder's trust level itself, so Codex neither asks nor writes a `[projects]`
entry into its config. With `trust_cwd` on, the folder runs `trusted`. With it off, your
Codex config (`$CODEX_HOME/config.toml`, else `~/.codex/config.toml`) decides when it gives
the folder, or the git project it belongs to, a trust level; with no entry there, the
folder runs `trusted` for this launch only, written nowhere. A folder you want untrusted
needs one `trust_level = "untrusted"` entry there. `preview` prints which case applied,
and the launch records it as its `trust` value. The launcher never writes Codex's config.

**The brief.** Without turns, the brief (or the handoff) is the harness's opening prompt.
With turns, a launch in this terminal or in a slot waits until the session is ready and
sends the brief as its first turn, once. If turns cannot say whether it went in, the
launch log says the brief is unconfirmed; it is never sent twice.

**The start posture.** The `posture` row tells the agent how its session begins. The
launcher writes it as one line, `start posture: <value>`, in the identity block the
session starts with (the few lines naming its session id, directory, project and comms
name, which Claude Code gets with its system prompt), or, where a session gets no
identity block (a Codex launch without turns, or a headless one), as the second line of
its opening prompt. Past one check on the brief, the launcher does nothing else with it:
the word is for the agent, and your own instructions can say more about each. Unset, the
default, there is no line.

- `operator`: the operator, the person running Moxi, is at this session and directs it.
  The agent takes its work from that person as it comes. A brief is sent, and the
  session reads it and then waits.
- `go`: nobody is waiting at this session to direct it. The agent starts on its brief at
  once and carries it through. A `go` launch with no brief, brief file or handoff would
  sit idle, so `preview` and `run` refuse it (`posture-brief`). On a launch that binds no
  comms identity, words typed after `--` on the command line count too, whatever they
  are: they go last on the harness's argv. Nothing else passed on counts: not an unknown
  flag typed before `--`, not `extra_flags` from a preset or `[defaults]`, and not the
  page's extra flags, so a `go` launch from the page needs a brief, a brief file or a
  handoff. A relaunch launches its row again and is not checked. A resumed session does
  not wake itself either, so a resume of a `go` launch needs a brief (`--brief` or
  `--brief-file`), and is refused without one (`posture-brief`). That holds for the
  session a `go` launch was relaunched into too: a resume reads the posture the launch was
  configured with, not the `relaunched` or `with:` one its relaunch started on. A plain
  resume takes none: `moxi launcher resume --plain`, and Home's `Continue the last
  session` with an empty box, resume the session with no first turn.
- `with:<identity>` (needs comms): the session works for a director, the comms identity
  named. A director is a session that hands this one its work and hears back through
  comms; a launch lists its directors with `--director`. The director sends the first
  message, so a brief under this posture is refused (`posture-brief`). A typed
  `--handoff` is sent as the first turn, and the director's message comes after it. A
  director's relaunch starts the new session with this posture.
- `relaunched (handoff: <file>)`: the launcher's own, never set by a preset. A session
  that relaunched itself with a handoff starts with it, and reads that file before
  anything else; the `moxi-handoff` skill the launcher ships says so.

`moxi launcher preview` takes the same words as `run` and prints the command and the
environment variables the launch would set, starting nothing.

### The session's name

Every launch has a name. `--name TEXT` (or `name` in a preset) sets it; a launch that
sets none is named from a template, `<date>-<time>-<project>-<agent>` as shipped,
which gives `20261108-1245-docs-planner` for the seat `planner` in the project `docs`, and
`20261108-1245-docs` for a launch with no seat or agent. The name is recorded with the
launch, a taken slot in the browser is labelled with it, and Claude Code shows it as the
session's display name
unless `--native-name` (or `native_name` in `[defaults]` or a preset) sets another.
Codex has no display name, so there it is the launcher's label only. A relaunch or a
resume keeps the name its launch had.

A template is text with pieces in angle brackets, filled in the order written:

| piece | filled with |
|---|---|
| `<project>` | the project key, else the name of the folder the session starts in |
| `<agent>` | the seat's name when the launch names a seat, else its agent role |
| `<model>` | the model the launch names; empty on the harness's default |
| `<identity>`, `<scope>` | the comms name, and its scope beside one; empty without comms |
| `<instance>` | the lowest number that makes the name differ from every running launch's |
| `<date[:format]>`, `<time[:format]>` | the local date (`yyyymmdd`) and time (`hhmm`), or as the format says |

A format is read by its tokens, in either case: `yyyy`, `yy`, `mmm` (`Nov`), `mm` (the
month, or the minutes after an `hh`), `dd`, `hh` (24 hours), `ss`; a format holding `%`
is taken as `strftime`'s. The filled name is then cleaned: anything outside printable
ASCII becomes a dash, dashes in a row become one (an empty piece leaves two), the name
starts with a letter or a digit and is cut at 63 characters; a name with nothing left is
`launch`.

`name.template` holds the template for every launch, and `name.templates` one of its own
per project, by project key:

    moxi config set launcher name.template '<identity>-<date:mmm-dd-yyyy hh:mm>'
    moxi config set launcher name.templates '{docs = "<project> <time:hh:mm>"}'

A template that names another piece, or holds a `<` or `>` outside a piece, is not used:
the launch is named by the shipped template and warns, naming the setting.

## Presets

Presets live in `presets.toml` in the launcher's config directory. Moxi ships none.

```toml
[defaults]                     # your defaults for every launch
permission_mode = "acceptEdits"

[seat.planner]                 # a seat: a saved setup for a standing agent
harness = "claude"
model = "opus"
instructions_file = "~/agents/planner.md"
home = "~/work/site"           # a path, or a project key with projects installed

[project.docs]                 # a project preset: where to work
cwd = "~/work/docs"
harness = "codex"
sandbox = "workspace-write"
```

Every key is a catalog row of the preset's harness or one of the launcher's rows. A seat's
standing text is `instructions` or `instructions_file`, never both, and a preset names at
most one place (`cwd`, `project`, `scratch`, or a seat's `home`).

**Policy directors.** comms' config names policy directors (`[policy] directors`) that
direct every comms session on the box, on top of the directors its launch names. A seat
or project preset opts its sessions out with `takes_policy_directors = false` (needs comms;
`--no-takes-policy-directors` for one launch, an unticked box on the page), and the
launcher passes comms' wrapper `--no-policy-directors`. The seat keeps its own `director`
lines. `[defaults]` refuses the row by name (`per-seat`): one line there would opt every
seat out. A launch with an identity that opts out is refused (`not-yet`) when the
installed comms is older than policy directors; upgrade `moxi-comms`. `moxi launcher presets
check` checks the whole file: types and values, rows whose module is missing, project keys,
that an instructions file can be read, and that an agent role a preset names is found in
the harness's agent folders.

## The launch log

Every launch writes one row to a SQLite file in the launcher's state directory before the
session starts: who launched what, where, with which argv and stamped environment, and how
far it got (`spawning`, `started`, `ready`, `sending`, `first-sent`, or `failed` with a
reason). A launch refused before its row is written writes nothing. Each session the
launcher starts carries `MOXI_LAUNCHER_REQUEST`, its launch's request id, and a Claude
launch's session id is set at launch, so the session can always be found from its row.
A launch made from inside a session drops that session's stamps from the environment it
passes on: every `CLAUDE*` variable except your own Claude Code configuration and
sign-in (`CLAUDE_CONFIG_DIR`, the provider switches such as `CLAUDE_CODE_USE_BEDROCK`,
`CLAUDE_CODE_OAUTH_TOKEN` and the others in the launcher's keep-list), and each
module's own session stamps. A scratch launch works in `/tmp/scratch-<uid>/<session
name>`: a folder named by its session name, under a parent of your own, by your numeric
uid, made 0700 if missing, which only you may use. `TMPDIR` is not read. The folder's
name is the session name folded to lowercase letters, digits and single dashes, at most
63 characters (`Foo_Bar` works in `foo-bar`, `My spike` in `my-spike`, `..` in
`launch`), so the folder stays in the parent; the session keeps its own name. A launch
under a name that folds to a folder already there works in it again. A parent that is a
link, not a folder, another user's or open to others is refused (`scratch-root`). On
Windows the parent is `scratch-<user name>` in your own temporary folder. It keeps no notes: a `notes_dir` typed or set by its preset is refused (`scratch-notes`), one from
`[defaults]` is left out, and with notes installed a preview or launch whose scratch
folder notes would keep notes for is refused too. With no notes folder it has no handoff
folder either (see "Handoffs").

Another module can ask the launch log how a session was launched. The launcher offers
interface 1, `moxi.modules.load("launcher", interface=1).api`, whose
`launch_kind(session_id)` answers one of five kinds, the first that holds:

| Kind | When |
|---|---|
| `fork` | the launch is a fork (`moxi launcher fork`), or a resume of one: its row records `fork_of`, the session id it copies |
| `scratch` | the launch works in a scratch folder, whatever its preset |
| `seat` | the launch names a seat preset |
| `project` | the launch names a project, by a project preset or a project key |
| `one-off` | any other launch |

It answers None for a session the launcher didn't start: one started by hand or by a
script, or from inside a launched session. It also answers None for a launch whose session
id it never learned, which is a Codex launch without turns or in `exec` mode. The launcher
learns some ids after the session starts (a Codex launch's, the new one after a `/clear`),
so an id that answers None may answer a kind later. `launch_kinds(session_ids)` answers
many ids in one read. Neither writes to the log, except to bring an older one's
schema up to date; a log held past the wait (1 second)
raises `Busy`, and the caller asks again.

Interface 2 adds what is running now: `running()` gives each launch that recorded a
process and whose process runs, newest first, at most `limit` (200), as a `Running` of
plain values: `request_id`, `harness`, `session_id` (the id the launch was last known by,
None when it never learned one), `target`, `slot`, `project_key`, `cwd`, `name` (the
launch's name, else its comms name, else its folder and start time), `identity`,
`created_at` and `live`. With `live_only=False` it keeps the launches whose process has
ended, with `live` false. It reads the launch log and the process table and nothing else:
it asks no other module, starts no process, and writes nothing but an older log's schema
update. With no launch log it gives none; a log held past `timeout_s` (1 second) raises
`Busy`.

Interface 3 adds two acts. `fork(session_id, target="terminal", slot=None, stamps=None)`
forks a session as `moxi launcher fork` does (below) and returns the launch's
`LaunchResult`, whose `session_id` is the fork's. `stamps` are variables set on the
fork's environment after the scrub. Each must be one a present module lists in its
`SESSION_ENV`, and its own prefix (`MOXI_<MODULE>_`) says which module that is; the
launcher's and turns' are refused, since the launch stamps those itself. A stamp that
fails is refused, `bad-value`, before anything is written.
`exit_session(session_id, dry_run=False)` exits a quiet launched session as `moxi
launcher exit-session` does and returns its verdict; a refusal is raised.

## Handoffs

A handoff is a short file a session writes for the next session of its seat. Its key is
the session's comms name without its scope, else the seat's name, else the project key; it
is filed as `<YYYY-MM-DD>-<key>.md` (then `-2`, `-3`) in the handoff folder. With notes
installed, that is notes' `handoffs/` folder for the session's directory (or its
`notes_dir`), and a session notes gives no notes directory has no handoff folder: it is
pointed to no handoff, its start names no folder, `moxi launcher handoff dir` refuses
(`no-notes-dir`), and it can't relaunch itself. Without notes, the folder is the
launcher's own. With turns,
a seat's next start or resume is pointed to its newest waiting handoff, then shown one
line for each other seat's waiting handoff in the same folder (newest first, at most 5, then
how many more). A session with no key gets only those lines. A handoff counts as used only
when the launcher gave it to a launch of its seat or showed it to one as that seat's own,
so a session started by hand, or another seat, never hides a seat's handoff. The launcher ships a `moxi-handoff` skill, which
`moxi install launcher` offers to place for Claude Code and Codex.

`moxi launcher handoffs` lists this session's handoffs, in its own folder. With `--key KEY`
it lists KEY's handoffs in the folder of KEY's newest launch (that launch's directory and
`notes_dir`), so a seat that works in another project is read where it files; a key with
no launch is read in this directory's folder. It says on stderr which folder it read, and
`--json` gives the launch it took the folder from as `launch` (null when none).

## Acting on launched sessions

The launcher can act on a session it launched: relaunch it, exit it, resume it, recycle it
and keep it warm. Every act finds its session the same way, through the launch log and
the turns endpoint that claimed the launch, so it acts only on a session the launcher
started, and only on the one it started. turns does every act inside the session (the
exit, a clear, a compact, a delivered turn); slots does what lies outside it (the pane,
Claude Code's exit dialog, a job that outlives the session). Each act but keep-warm
waits for the session to be quiet first: no turn open, no subagent at work, no draft in
the prompt, and nobody using its pane (copy mode, or typing in an attached terminal) or
opening a turn in the last 2 minutes (`relaunch.person_s`). The pane's own output, such
as Claude Code redrawing its status line, never holds an act. Keep-warm takes its own
reads instead (below), since it nudges because subagents are at work. A background shell
holds only exit-session (below).

**The claim follows the session.** The first turns endpoint to name a launch's session
claims the launch. Claude Code ends a session's endpoint and starts a new one whenever its
plugins reload. The new endpoint takes the launch as soon as it learns the session's id,
because its parent process is the launch's own `claude` (the pid and start time the
launch recorded); when the old endpoint is still ending, the new one waits up to 5 seconds
for it. So an act still finds a launched session after a plugin reload and any `/clear` or
compaction that follows. A session started from inside a launched one (an agent's
`claude -p`, a `claude --resume` in a subshell) runs under another `claude`, so it never
takes the launch, whatever session id it names. A Codex launch's claim moves to a new
endpoint only when that endpoint names a session id the launch already knows. This needs
moxi-turns 0.2.5 or later, which the `turns` extra asks for.

A launch has one act open at a time. A second one is refused (`run-open`, exit 3), naming
the open run. Each act is a run in the launch log, with its events and its outcome.

**Relaunch** puts a fresh session in the same slot. It needs turns and slots, and a
launch in a slot whose turns session offers exit; comms is needed only for a director's
relaunch. A session relaunches itself only when its launch was made with
`--recycle-at-limit relaunch`; any other launch is refused (`no-self-recycle`, exit 3).
A launch that names no model comes back on the model its transcript last used; a resume
of it runs the row's own model.

    moxi launcher relaunch --self --handoff FILE

An agent runs this in its own turn, with a handoff it has just written. The launcher
checks the handoff (a readable file of at most 64 KiB, written since this session
started, and with notes installed in the launch's notes `handoffs/` folder, refused when
the launch has no notes directory) and that no subagent is running, starts a job outside the session and prints
what it waits for; the agent then ends its turn. Once the session is quiet the job exits
it, answers the exit dialog, and starts the new session in the same pane with the same
launch values, none of the old session's environment, and the handoff as its first turn.
Its environment is what the launch recorded: the system's basic names and temporary folder, the core's
locations, and your configuration names (a configuration folder, a provider switch, a
region or profile), never a token, key, password or endpoint URL; those come back only
through `relaunch.pass_env` (below).
A relaunch that can't go ahead (the session never goes quiet within
`relaunch.wait_s`, the slot can't be held) leaves the session alone and tells it so in
one turn.

Before it touches the session, a relaunch checks that its own launch, on the model it
comes back on, would be accepted now: at the arm, at a director's call, again in the job
just before the exit, and in a repair before it launches. The session's own comms name
and slot don't count against it. A launch that would be refused (its directory gone, its
harness not found, its name held by another session) refuses the relaunch, `launch-check`
at the command or `refused` in the job, with the session left as it was. A repair's
session is down already: a repair refused so launches nothing, and its notice says the
session is down and names the `--from-record` command that tries the repair again.
`--dry-run` prints the model and the check's verdict. The armed text names the model too, and tells
the agent to end its turn and make no further tool call. When a self relaunch ends with
the session still running (it was not exited, it stopped with an error before the exit,
or the session's id changed after a `/clear`, which ends the run `changed`), the session
is told in one turn; its directors hear only when that turn can't be delivered. A run
refused by turns' pace waits it out before it tries again, and the slot hold it takes for
the exit and the respawn lasts 510 s. A hold left by a dead run of the same launch is let
go and taken again. The handoff must be UTF-8 text and not blank; when the session's
start was never seen, it must be written since the launch was made.

    moxi launcher relaunch SESSION_ID
    moxi launcher relaunch IDENTITY

A director relaunches a session it directs, named by its session id or its comms name.
The caller's comms identity must be one of the directors the session was launched with;
a session whose cache is still warm is refused (`warm`: send it a message instead), and a
session that isn't quiet now is held (exit 75). The new session starts with the `with:`
posture of its director and no first turn. The call follows the run for
`relaunch.follow_s` and prints how it ended; past that it exits 75 and names the run.
An agent making the call gives Bash a timeout of at least `relaunch.follow_s` plus 30 s,
since Bash's own default is shorter. The job reads the session's cache again just before
the exit, so a session that warmed up since the call is left alone (`held`, `warm`). A
relaunch that came back names its identity, slot, new session, posture and model, and
whether comms has bound the name, waiting up to 60 s for the bind, or that comms could
not be read. A director named without a scope is read in the launch's own scope.

The box's policy directors (comms' `[policy] directors`) count as directors too, for a
session launched with a comms identity that didn't opt out (`takes_policy_directors =
false`): they may make the call and they hear of a failed relaunch, once each beside the
session's own directors. The launcher reads the setting at the call or the notice, not
as it was at the session's launch, and takes only the names provisioned as standing
identities now (`moxi-comms-provision --standing NAME`); an unprovisioned name gets
neither. A policy name is comms' own spelling, a bare one the global identity, never read
in the launch's scope.

    moxi launcher relaunch --status RUN
    moxi launcher relaunch --stop RUN
    moxi launcher relaunch --from-record RUN

`--status` shows a run and its events, what it waits on first, and exits as the run ended:
0 relaunched, 75 held or still running, 1 any other end. A run still open whose job is
gone will get no end: `--status` says so and exits 1, naming `--from-record` when its
record shows an exit, as it does for a run left at the exit dialog. `--stop` stops an
armed run: before the exit the run ends `stopped` and a self run's session is told; from
the exit on, the run ends once the relaunch has been tried. A repair's session is down
already, so a stop of a repair is recorded and the run ends once its launch has been
tried. A relaunch that exited the session and failed to start the new one tells the
session's directors and policy directors (or the comms identity in `relaunch.fallback`) through comms, naming
the command that repairs it: `--from-record` starts the session again from the run's
record, in its slot, or the lowest free one when someone outside the session's own line of
launches has taken it. It refuses when the slot already runs the relaunched session, and
when the session itself is still live. A run left at the exit dialog (`exit-pending`) can
be repaired too, once a person has answered the dialog and the session has exited.

**Exit-session** exits one launched session, once it is quiet: `moxi launcher
exit-session SESSION_ID`. Anyone with a shell may run it. A Claude Code session is exited
through turns, and the consent is the launch's own, the commands it granted turns. A
background shell or Monitor the session runs holds the exit (`held`, `shells`) until the
session has been idle an hour, as cache reads its last request; with cache not installed
the idle time is unknown and the shells hold. Past the hour, Claude's exit dialog stops
them; park's exit doesn't wait on them (below). In a slot the exit dialog is answered too,
with "Exit and stop tasks"; elsewhere the session is left at the dialog for a person
(`at-dialog`). A Codex session under turns' Codex host is exited by stopping its host:
SIGTERM, then up to 15 seconds for the host to end (`not-exited` when it still runs); in
a slot the pane ends with it. A Codex session without the host can't be exited
(`no-exit`). Every exit tried writes a row to the exit log, `moxi launcher exits`:
`exited`, `held` (with what held it), `at-dialog`,
`not-exited` or `refused`.

**Resume.** `moxi launcher sessions` lists past launches, newest first, each with whether
it is live and whether it can be resumed, and why not: it is live (attach to its slot
instead), it ran headless, it never learned a session id (a Codex launch made without
turns' host: use `codex resume`), it ran Codex under a comms name and the installed comms'
Codex wrapper can't resume a thread (upgrade moxi-comms), its harness is no longer
launched, or a later launch resumed it since. A session a park entry keeps a slot for is
marked `parked in slot N` (below). `moxi launcher resume SESSION_ID` launches
it again from its launch's values, resumed, keeping its session id (a Codex launch under a
comms name continues its thread through comms' wrapper; a relaunch or recycle starts a
new one): in its own slot when that can take it, else
the lowest that can, with a line saying why its own could not (another session's park
reservation, say); `--slot N` picks one. Or in this terminal for a terminal launch,
where it adds your terminal's names (`TERM`, `COLORTERM`, `TERM_PROGRAM`,
`TERM_PROGRAM_VERSION`) and nothing else of your shell's. The launch's own brief is not
sent again. `--brief TEXT` or `--brief-file FILE` (one of them, the file read as a launch's
is) is sent as the resumed session's first turn, the way a launch sends its brief. The
start posture rules it as it rules a launch's: a resume of a `go` launch with neither, and
without `--plain`, is refused before anything starts (`posture-brief`), and so is a brief under
`with:<identity>`. The posture is the one the launch was configured with, so a `go`
launch's relaunch resumes as `go`, and the resumed launch shows `go` as its posture, not
the `relaunched` or `with:` one its relaunch started on. Under any other posture the brief is optional.
`--plain` resumes with no first turn under any posture, a `go` launch's too, and takes no
brief: with `--brief` or `--brief-file` it is refused (`bad-value`, exit 2). Liveness is checked again
as the new launch is written: while
another launch of the session is still starting or running, the resume is refused, so two
resumes of one session at once launch it once. While a relaunch, exit or recycle of the
launch is under way, the resume is refused `run-open` (exit 3), naming the run.

**Fork.** `moxi launcher fork SESSION_ID` starts a new session from a copy of a launched
session's conversation, live or not, and prints the new session's id; the session forked
goes on as it was. Claude Code gets `--resume PARENT --fork-session` beside the session id
the launcher mints. Codex needs turns' Codex host, which forks the thread with `--fork
THREAD_ID` from moxi-turns 0.2.6 on; with an older turns the fork is refused `not-yet`,
naming the version it needs. The fork runs in this terminal (`--target terminal`, the
default) or a slot (`--target slot`, `--slot N` for one, else the lowest free one).

What the fork takes from the launch it copies is each catalog row's fork class. Every row
of the shipped catalogs carries one, and a row without one fails the catalog check:

- `copy`: the rows that shape the request the harness sends (the directory, the model,
  the effort, the tools, the permission rows, `extra_flags` and the rest) are taken as
  the session ran them, so the fork reads the session's prompt cache. A launch that names
  no model may run another (after `/model`, or a relaunch on the model its transcript
  last used); its fork runs the model the transcript last used, as a relaunch does, and
  the harness's default when none can be read;
- `drop`: the rows about who the session is, what its first turn says and how it lives
  and ends (its name, comms identity, posture, instructions, brief, handoff, keep-warm,
  recycle and usage marks) are unset;
- `own`: the rows the launcher composes for each launch (the session id, the resume, the
  target and slot) are composed again.

So a fork has no comms name, no start posture, no brief and no preset. Its environment is
built as a resume's is, from what the launch recorded, never from the shell that asks for
the fork. Its row records `fork_of`, and its launch kind is `fork`; a resume of a fork is
still one. A fork is pointed to no handoff at its start. It is refused when no launch holds
the id (`not-launched`), the launch never learned a session id (`no-session-id`), the
launcher no longer launches its harness (`not-catalogued`), or Codex can't fork here
(`not-yet`).

**Park.** `moxi launcher park` gets the slots ready for a reboot. It exits every session
the launcher launched into a slot, records which session was in which slot (a park
entry), and reserves each of those slots with a slots hold `park-<id>` that lasts until
it is released, so no other launch takes the slot. It holds the slot before it exits the
session, so the slot is never free between the two. Each exit is exit-session's, with
three differences: it waits for the session to be quiet, up to `park.wait_s` (300 s, or
`--wait-s`); it does not count a client's activity as a person, at the quiet read or at
the exit dialog, since the person parking is the one at the panes; and it exits
regardless of background shells and Monitors, which Claude's exit dialog stops, since the
reboot would end them anyway. Copy mode in the pane still holds, as do an open turn,
subagents, a draft and a read that failed. Each slot gets one line:

- `exited`: the session ended.
- `not-exited`, with the reason: the wait ran out or the exit failed. The session still
  runs; its entry and reservation stay, so a reboot ends it and running park again tries
  its exit again.
- `left-running`: a Codex session. Park leaves it running: it is recorded and reserved,
  and the reboot ends it. `moxi launcher exit-session` stops one under turns' Codex host
  first, if you want it ended before the reboot.
- The session park itself runs in is recorded `not-exited: the session running park` and
  left for you to exit.
- A live run the launcher did not launch is `left alone`; a session launched into a
  terminal is listed `not in a slot; left running`; a slot taken by something else since
  park found it is `changed; left alone`; a slot another holder holds is `held by H; left
  running`.

Park exits 0 when every session it found got an entry, and 1 when a slot could not be
reserved or an entry could not be written. `--dry-run` says what park would do and
writes nothing. Park removes no slot. Only one park runs at a time: a second waits 10 s
for the first, then is refused `park or restore is running` (exit 75).

**Restore.** After the reboot, `moxi launcher restore` resumes each parked session into
its reserved slot, making the slot's tmux session again first; `moxi launcher restore 3
5` restores only those slots. Nothing restores by itself: you run it. Restore sends no
first turn: each session comes back as `moxi launcher resume --plain` brings it, a `go`
launch's too. Sessions go one at a time, in slot order, each with one line:

- `restored`: resumed into its slot under the reservation, which the start ends. When its
  slot can't take it (something else runs there, or slots won't make it again), it goes
  to the lowest slot that can, and the reservation is released.
- `still running`: park left it `not-exited` and no reboot came; the entry stays.
- `liveness unread`: whether it runs can't be read; the entry stays.
- `refused`, with the reason: the resume was refused before anything started, such as
  `comms is offline` when the comms daemon is not up yet. The entry and reservation stay;
  run restore again.
- A session resumed some other way since is marked restored, and its slot let go.

A launch that fails after it was placed takes the reservation again, so the entry stays
open; if something took the slot in between, the entry is closed and the line names the
`moxi launcher resume` command for the session. Restore exits 0 when every entry it took
was restored, else 1, and `--dry-run` says what it would do and writes nothing.
`moxi launcher resume SESSION_ID` of a parked session takes the same path: it goes back
into its reserved slot, or, with `--slot N`, into slot N with the reservation released.
Its `--brief`, `--brief-file` or `--plain` goes with it, and a `go` launch's resume with
none is refused before the whole-run park lock is taken and before its slot is touched.
The launch's own crash discovery runs before that check, under the park lock it takes
around itself, and may reserve crashed slots.

**When the machine went down with no park**, the launcher still finds the sessions that
were running in slots: slots records how each run in a slot ended, and a run that was
still going when its tmux server went down reads `crashed`. Before any launch into a slot,
and at the start of park, restore and unpark, the launcher turns each crashed slot whose
last launch was its own into a park entry with a reservation, so no fresh launch takes the
slot and `restore` brings the session back; a resume of the session, or a relaunch
repair, runs this before it chooses a slot. A launch that names such a slot is refused
`held by park-<id>`, with the hint to restore or unpark it. A Codex session quit by hand
in the two minutes before an unplanned shutdown, and a run whose window was killed by
hand, read crashed too; unpark them.

**Unpark.** `moxi launcher unpark 3` gives up slot 3's reservation without restoring its
session, and `moxi launcher unpark --all` every one. The session stays resumable with
`moxi launcher resume`. A slot given up, by unpark or by restore, that reads crashed (a
session park left running that the reboot ended) is settled, so it is not reserved
again.

**Recycle.** Two launch options let a session be cleared or compacted on the launcher's
own reads:

- `--recycle-on-expiry clear|compact` (needs turns and cache): once the session's prompt
  cache has gone cold and the session is quiet, clear or compact it. A clear waits while
  a named hold is open (below). A session idle for an hour has its background shells
  stopped by the clear.
- `--recycle-at-limit relaunch|compact` with `--limit-at 1x|1.5x|2x` (needs turns and
  usage): once the context passes the mark, tell the agent it may relaunch itself with a
  handoff, or compact it once it is idle, waiting up to `recycle.limit_wait_s`.
  `relaunch` is what lets a session recycle itself (it also needs slots and a slot
  launch); its line opens "This launch lets you recycle yourself". A scratch launch has
  no notes directory to keep a handoff in, so it can't take `relaunch`
  (`scratch-recycle`, exit 3) typed or set by its preset: the launch and its preview, on
  the command line and the page, refuse the pair, and `presets check` flags a preset
  that sets `relaunch` and launches in a scratch. A `relaunch` from `[defaults]` reaches
  no scratch launch: there it holds `off`, with a warning. `compact` is open to it. With
  `--limit-at` unset, the mark is the one usage warns this launch at: its `warn_at` when
  that is a mark; when `warn_at` is unset, usage's own `marks.default` when usage loads
  and that is a mark; otherwise (`warn_at` is `off`, or usage's default is off or can't
  be read) `1x`. The mark is fixed when the launch is composed: a later change to usage's
  `marks.default` moves usage's notice but not the stamped mark, and a relaunch composes
  its mark again from the values then. A `compact` launch takes its mark the same way.

**Keep-warm.** Every Claude Code launch that isn't headless is kept warm (needs turns and
cache): shortly before its prompt cache would expire, while it waits on work it handed
out, the launcher nudges it. A headless launch is kept warm only with `--keep-warm on`.
Work out is one of two cases. The subagent case: its own subagents or teammates at work;
a fork of its main loop doesn't count. The director case: an agent whose comms claim
names it as a director at work (its turn open, or its turn closed while subagents of its
own work), or an open named hold. Where turns judges stale subagents, one that has sat
waiting an hour with nothing it owns writing counts as idle in both. The nudge is a short
turn delivered only when the session is idle. A run of nudges counts each case apart:
the subagent case first, up to `keep_warm.subagent_cap`, then the director case up to
`keep_warm.director_cap`, then `blocked` (`capped`), naming each spent count. A session
that hands out work neither case shows names each piece with a hold, from its own tool
shell:

    moxi launcher keep-warm hold NAME
    moxi launcher keep-warm release NAME
    moxi launcher keep-warm holds

A hold stays open until it is released, across a relaunch or a resume of the session.

**Asking by hand.** cache's timer makes both asks on its own. To make them now, or to
check a session nobody watches:

    moxi launcher clear-expired [--session ID] [--dry-run] [--json]
    moxi launcher keep-warm --session ID [--dry-run] [--json]
    moxi launcher keep-warm --dry-run [--json]

`clear-expired` makes the expiry ask of every live launch that recycles on expiry;
with `--session ID`, only of the launch whose current session is ID, and when none is,
it says so and exits 0.
`keep-warm --session ID` makes the keep-warm ask of that session; with `--dry-run` and
no session, of every live launch keep-warm is on for. Each ask is the one the timer
makes. `--dry-run` takes its reads and writes nothing, and prints each session's verdict:
the quiet ends as a run would end them, `blocked` with its word, or `would-clear`,
`would-compact` or `would-nudge` where a run would act. Each exits 1 when a verdict is
`blocked`, `failed` or `unconfirmed`.
Keep-warm and recycle on expiry are offered where cache can read a session's
environment: Linux and WSL2 (macOS untested), for Claude Code sessions.

**Cover.** `Keep warm` on Home's Sessions row (see "The page") puts a running session
under cover: a session past its cap gets a new count, and a headless session launched
without `--keep-warm on` is kept warm. Cover belongs to one launch and the session id it
has now: a `/clear` gives the session a new id, and a relaunch or a resume is a new
launch, so each starts without it. A covered session is nudged for both cases, and under
cover the count in a row starts at the press. Holds belong to the launch alone: a launch
with cover alone takes no holds, and a hold left open in its chain does not keep it
warm. `moxi launcher show` reads a covered session as keep-warm on.

Cover lasts until a keep-warm ask reads the session's own subagents and its directed
agents in full and finds nobody at work. Asks come only near the end of a cache
lifetime, so a session that goes back to work and then waits again is still covered. If
comms or turns can't be read, the cover is kept. There is no off short of a `/clear`, a relaunch
or ending the session.

**What keep-warm costs.** Each nudge is a turn: the session reads its whole cached
context and writes a short reply. It comes about once per cache lifetime while the
session waits (every 45 to 54 minutes on an hour lifetime), up to the cap in a row.
cache's timer asks about every Claude session the launcher started, shortly before its
cache would go cold: at most 5 short `moxi-launcher-job keep-warm` runs per idle
stretch, each ending at its read of the launch log when the launch is neither on nor
covered.

**State.** `moxi launcher show [SESSION_ID]` shows a launched session's relaunch,
recycle and keep-warm state (this session's when none is named). It reads red, with
`failed` or `blocked` and why, when the latest run on the launch failed or was blocked
and nothing has gone well since; a session merely waiting is never red.

## The page

With the UI shell installed (`moxi install launcher --with ui`), the launcher has a page
in the shell and four pieces other pages can show: the box on the shell's home that
starts a session, the Sessions row under it, a launched session's state, red when it
needs a look, and a block on the modules view listing each running launched session with
its state.

The page has four parts.

- **Launch.** Pick a seat or a project preset to start from: its values and your
  defaults fill the form, which shows the same flag rows as `moxi launcher catalog`, each
  with a note saying who set its value. Change what you want. An emptied field is left
  out of this launch, as `--leave-out` leaves it out: a word field offers a "leave out"
  choice naming the word that then launches, and a switch or box turned off is left out.
  A brief or standing instructions that the preset or your `[defaults]` writes inline is
  not shown; a ticked box beside the form keeps it, and unticking the box leaves it out.
  Choosing another preset fills the form afresh and drops your edits; changing the
  harness keeps your edits to the rows both harnesses have. The page only reads the
  presets file: nothing you do in the form changes a preset. "Preview" shows the
  command, the environment stamps, the first turn and every warning, and every reason the
  launch would be refused, each beside its field. A launch from the page needs a
  directory, a project or scratch to start in. A launch in a slot or headless then starts
  with "Launch": the page server hands it to a detached job and the job runs it, so
  closing the page doesn't stop it. A preview can be launched once, within
  `page.launch_wait_s` seconds (600), and only as it was previewed. The page never runs a
  launch in a terminal: it gives the `moxi launcher run` command to copy into a terminal
  of your own, with the preset, the fields you changed and a `--leave-out` for each field
  left out. The preview lists the left-out rows on a "Left out" line.
- **Sessions.** The block the shell's home shows: each running launched session, whether
  it needs a look, and its relaunch, recycle and keep-warm lines.
- **Launches.** The newest launches, updated as they happen. A running session shows its
  state beside it, and keeps showing it when a clear or a compact gives the session a new
  id. When a launch is refused after "Launch" but before its row is written (its
  directory went away since the preview, say), the refusal shows under the launch.
- **Past sessions.** Each launch's session, newest first. A session that ran in a slot
  resumes from here, in a detached job as a launch does; one that ran in a terminal gives
  the `moxi launcher resume` command to copy, with the brief. Each row takes a brief, or a
  brief file (an absolute path), sent as the resumed session's first turn; a session
  launched with posture `go` needs one. The refusals `moxi launcher resume` gives
  (headless, live, a relaunch or recycle still under way, no brief for a `go` launch) come
  back at once.

**The box on Home.** The shell's home opens on a box that starts a session without a
visit to the page. Type what the agent should do and press the button. The line above the
box says where the session starts, a project, a folder or `Scratch`, a throwaway folder
the launcher makes: it opens on the place you last launched in (after a launch in
`Scratch`, which is not kept as a place, on the place before it), and its menu takes a
typed folder path, lists your recent places, offers `Scratch` and, with projects
installed, every project. A launch names only the place the line shows. With any preset
set up, `Preset` on the same line applies a seat or a project preset; a project picked
there, or the one Home opens on, applies that project's own preset, and a preset that
names a place, `Scratch` among them, moves the line there. The chip in the box sets the
harness, the model and the effort. The button names where the launch runs, and its caret
opens the choices: each slot with its state, your terminal, or headless. A missing slot
reads `missing, made again at launch` and can be picked; a slot the next launch reserves
for the session that was running in it when the machine went down reads `crashed,
reserved at the next launch`; a reserved slot names its holder and, for a park's
reservation, the parked session. With slots it starts at the slot a launch naming none
would take (the lowest with no session running and no hold), without slots at your
terminal, and the choice is not kept from one visit to the next. A launch from the box runs where its button says,
whatever `[defaults]` or the preset sets for `target` and `slot`; every other row is
filled from the preset and `[defaults]` as the page's form is. The first turn is what
Home shows: the text in the box, and a handoff on its line under the box. A brief your
`[defaults]` or the preset set does not go with a launch from the box; a handoff they
name shows on that line, with `Clear`, and goes with the launch unless you clear it. A
`Clear` covers the next launch only: once it starts or its command is copied, or you pick
a preset, the handoff shows again and goes. A handoff from the `+` stays when you pick a
preset or copy a command, and goes after the launch that sent it, as a brief file does.
An empty box with no handoff starts a session with no first turn. The `+` beside the chip
takes a brief from a file instead, which turns the box off while it is set, takes a
handoff from a file, and offers to continue your last session in the place on the line;
with `Scratch` on the line there is none to continue. A continue sends the box's text as
the resume's brief, or the brief file while one is set, and the box empties once the
resume starts, as after a launch. An empty box sends no brief and resumes the session
with no first turn, a `go` session too, where the page refuses that for a `go` session. A
session that ran in a terminal gives its resume command to copy as the page does; from an
empty box the command carries `--plain`, so it resumes the same way when you run it. A
slot or headless launch is
previewed and handed to a detached job as on the page, and a line under the box says
where it started, or why it failed or was refused. A terminal launch is not run: its
command is copied for you to paste.

`Options` beside the chip opens a panel with the form's other rows, in groups, with a
find field. Each row you change there is named on a `Changed` line under the box, with
`Reset`. A value that can do harm (skipping permission checks, no sandbox) is drawn in
the theme's warning colour wherever it shows, and picked by hand it is set only after
`Turn on`, for the one launch that starts with it. The panel also shows the session's
name, with an editor for its template that saves `name.template` or `name.templates`
through the shell's own settings, where a locked key is refused.

Rows the form leaves at their shipped defaults are filled from the last launch of the
seat, else the project, else the folder on the line; `From last launch` on the line
names them, each with `Clear`. The comms identity filled is the one the newest launch
there of the harness the form is drawn for bound (a project's include those made through
its seats), even one comms now reports retired, whatever harness the last launch
ran, so it fills even where `[defaults]` or a preset sets another harness and no other
value does; it is always at the global scope: a project scope is never carried. Where
there is no such name, and for any harness but Codex, a seat named for one of its
project's agents that sets no identity is filled with its own name, else a project of
kind `agent` with its own key, any other with its `lead`, or with `<key>-lead` when
comms lists that name; `Default` on the line names it. A filled identity comms refuses a
launch under (held by a live session, reserved or retired) shows comms' refusal beside
it, in the item's list and under its row, as the launch would meet it. One whose name a
live session holds in a project scope shows a note there naming that binding, so you
pick its scope only if you mean it. A harness you
pick in the chip lets go of the values filled and fills the comms identity for that
harness, unless you set the identity yourself. Under the box,
`Run again` lists up to three earlier launches there; pointing at one shows what a click
runs today, and one click runs it again where the button places a launch.

**The Sessions row on Home.** Under the box, the row `Sessions` says how many launched
sessions are running (`3 running`), and shows nothing when none is. Click it to open the
list in place: at most four sessions, each with its name, where it runs (the slot,
`terminal` or `headless`, then the project or the folder's name), its cache when that is
known, and on the second line the time and first line of the latest thing said in it,
or when it was launched. A session that waits on agents or subagents while its cache is
warm, with nothing set to keep it warm, comes first and says in heavy type when its cache
goes cold and what it waits on; the row then names it while closed and rises above the
rows that need nothing. `Open` goes to the session's transcript on the sessions page, and
`All sessions (N)` to that page's list; without sessions installed both go to the
launcher's page. On the phone the list is always open and shows each session's name,
where it runs and its cache, with no time, no message and no `Open`. The row reads
`GET /api/running` and follows the stream's topic `running`; that route is the one the
phone's gateway reaches, and it sends no path: a launch's folder goes by its name alone.

**Keep warm on the row.** Opened at the desk, a session that waits with nothing keeping
it warm (one of those that come first) has `Keep warm` before `Open` when a press would
keep it warm. Hover it for the cost: it sends a short turn before each cold time while the
session waits, at most the cap in a row (see "Acting on launched sessions" for what a
turn costs and how long cover lasts). A press sends nothing at once. It records cover,
and cache's timer's next ask sends the first nudge. The row then reads `kept warm while
1 subagent runs` (or `n agents work`) with `Open` alone, moves down to its place, and
the focus goes to its `Open`. The button isn't offered on a Codex or headless session,
on a cache too close to cold for an ask to come, with text typed in the prompt box,
after an ask that failed since the session's last request, or while cache's watcher
doesn't yet ask about the session (see "Upgrading"). If the launcher refuses a press,
the reason shows under the row until the row next changes, and the button comes back
after 20 seconds. The phone shows the same rows without the button. The press is `POST
/api/keep-warm`, which answers `{"refused": null}` once the session is covered, or a
refusal naming the step that failed. It is desk only: the phone's gateway doesn't reach
it.

The count and the names come from the launch log and are never more than a few seconds
behind it. The cache, the latest message and what a session waits on are read by the page
server apart from them, in a pass over the 24 newest running sessions every 15 seconds and
whenever a session starts, ends or changes its id: the cache from cache's own reading of the
transcript, the message from the transcript's newest turn, read again only when the
transcript changed. A read that takes longer than 8 seconds is left for the next pass,
and the session keeps what was read before for up to 45 seconds. A slow or missing module
never holds up the count. The first look after the page server starts waits up to 3
seconds for these reads.

A session waits only while its own turn is closed. With turns, it waits on its subagents
and teammates at work, counted as keep-warm counts them; with none, and comms installed,
on the agents its comms name directs whose turn is open, or whose turn is closed while
they wait on subagents of their own (a Claude session's; a Codex session counts only by
its open turn). Each comms read may take 2 seconds, and once comms' daemon is found down
the pass asks it nothing more. A waiting session reads `kept warm` when every step of
keep-warm's next ask that can be read without writing ends in a nudge, with an ask still
certain to come before its window shuts: its launch keeps warm (any Claude launch but a
headless one made without `keep_warm` on, or one under cover), cache's watcher says it
asks keep-warm about the session, no run open on the launch, no turn open, a live
subagent, a directed agent at work or an open hold of a launch kept warm by itself, a
case under its cap, nothing typed into the prompt box, and no ask since the session's
last request that ended without a nudge. Any
step it can't read counts against it, so the row says `goes cold` whenever a nudge is
not certain. These reads write nothing, send nothing to a session and never ask slots.

A launch from the page or from the box runs with the shell's environment, so its harness
must be on the shell's `PATH`; the preview says which `PATH` it searched when it isn't.
The page server starts when the page or the shell's home is first opened and stops once
nothing has used it for a while.

## Commands

| Command | Does |
|---|---|
| `moxi launcher run [--seat N \| --project-preset N] [flags] [--leave-out ROW ...] [-- extra]` | launch a session |
| `moxi launcher preview ... [--json]` | print the command and environment a launch would use |
| `--leave-out ROW`, on `run` and `preview`, repeatable | leave a catalog row out of this launch, whatever `[defaults]` or the preset sets |
| `moxi launcher log [--limit N] [--before TIME] [--json]` | the launch log, newest first; `--json` prints one row per line |
| `moxi launcher log --table runs\|exits\|holds [...]` | the runs (with their events), the exit log, or every hold, newest first |
| `moxi launcher presets [list \| show NAME \| check] [--json]` | list, show or check the presets |
| `moxi launcher presets --project KEY [--json]` | a project's agents, each beside the seat whose identity is its name (needs projects) |
| `moxi launcher catalog [HARNESS] [--json]` | the flag rows this install offers |
| `moxi launcher handoff dir [--json]` | this session's handoff folder |
| `moxi launcher handoffs [--key KEY] [--json]` | a key's handoffs, newest first, and which still wait; a `--key` in the folder of its newest launch |
| `moxi launcher deliver REQUEST_ID --pid PID` | internal: the detached process that sends a launch's first turn |
| `moxi launcher relaunch --self --handoff FILE [--dry-run] [--json]` | arm a relaunch of this session, with its handoff |
| `moxi launcher relaunch SESSION_ID \| IDENTITY [--dry-run] [--json]` | relaunch a session you direct |
| `moxi launcher relaunch --status RUN [--json]` | a run and its events |
| `moxi launcher relaunch --stop RUN [--json]` | stop an armed relaunch run |
| `moxi launcher relaunch --from-record RUN [--json]` | start again the session a failed relaunch exited |
| `moxi launcher exit-session SESSION_ID [--dry-run] [--json]` | exit a launched session |
| `moxi launcher exits [--limit N] [--before TIME] [--json]` | the exit log, newest first |
| `moxi launcher sessions [--limit N] [--before TIME] [--json]` | past launches, and which can be resumed |
| `moxi launcher resume SESSION_ID [--slot N] [--brief TEXT \| --brief-file FILE \| --plain]` | launch a past session again, resumed, the brief its first turn; with `--plain`, none |
| `moxi launcher fork SESSION_ID [--target slot\|terminal] [--slot N] [--json]` | start a new session from a copy of a session's conversation |
| `moxi launcher park [--wait-s SECONDS] [--dry-run] [--json]` | exit the sessions in slots and reserve their slots |
| `moxi launcher restore [SLOT ...] [--dry-run] [--json]` | resume parked sessions into their reserved slots |
| `moxi launcher unpark SLOT ... \| --all [--json]` | give up parked slots without restoring them |
| `moxi launcher clear-expired [--session ID] [--dry-run] [--json]` | the expiry ask of every live launch that recycles on expiry, or of the one whose current session is ID, now |
| `moxi launcher keep-warm --session ID [--dry-run] [--json]`, `keep-warm --dry-run [--json]` | the keep-warm ask of one session now; with `--dry-run` alone, of every live launch keep-warm is on for |
| `moxi launcher keep-warm hold NAME`, `release NAME`, `holds [--json]` | this session's named holds |
| `moxi launcher show [SESSION_ID] [--json]` | a launched session's state, and whether it needs a look |

`--dry-run` takes an act's checks and writes nothing. `--json` prints the verb's data, its
outcome word among it.

Exit codes: 0 done (a relaunch armed or relaunched, a session exited); 1 a start that
failed, or an act that ended in a failed outcome (`at-dialog` among them); 2 bad
arguments or a bad value; 3 refused (with nothing written when it was refused before the
launch's row, else the row says why); 75 a lock held past its wait (the launch log's,
slots' or projects'), comms' daemon not answering, an act held because the session is not
quiet now, or a run still going when the call stopped following it: trying again may
work.
A refusal prints one line ending in a short code, such as `[off-posture]`, and a hint.

## Optional links

| Module | With it | Without it |
|---|---|---|
| turns | the first turn delivered once the session is ready; Codex session ids learned; a Codex seat's instructions in its start context; the handoff lines at a seat's start or resume; every act on a launched session; what each running session waits on, on Home's Sessions row | the brief is the opening prompt; no readiness, no pointer; a Codex seat gets only its `AGENTS.md`; Codex session ids unknown; no relaunch, exit-session, recycle or keep-warm; no session reads as waiting |
| comms | identities, scopes, directors and the `with:` start posture; a headless launch with an identity, and the `headless_exit` row; a director's relaunch, and notices of a failed relaunch; a director waiting on its agents, on Home's Sessions row; a covered director kept warm while its agents work | plain harness launches; `with:` and `director` refused; relaunch with `--self` only, its failures recorded only; a session waits on its subagents only, and only those can be covered |
| slots | `--target slot`, `--slot`, and answering the startup screens you turn on; relaunch, and answering the exit dialog | `terminal` and `headless` only; no relaunch, and an exit stops at the exit dialog |
| cache | the `keep_warm` and `recycle_on_expiry` rows, and a director's relaunch refused while the session's cache is warm; each running session's cache and `Keep warm` on Home's Sessions row; the doctor row `keep-warm.timer` | neither row is offered; no session reads as kept warm, and no `Keep warm` |
| projects | `project` keys, a project's agent listing, a directory's project key | `cwd` paths and scratch launches only |
| notes | the `notes_dir` row, and notes' handoff folder; a launch notes gives no notes directory has none | the launcher's own handoff folder |
| usage | the `warn_at`, `recycle_at_limit` and `limit_at` rows | the rows are not offered |
| ui | the launcher's page, and its state views on other pages | the command line only |
| sessions | the latest thing said in each running session, on Home's Sessions row; `Open` and `All sessions` go to the sessions page | the row shows when each session was launched, and its links go to the launcher's page |

An agent role a preset names is looked for in the harness's own agent folders, the user's
and the project's.

With comms installed, comms keeps the name `console` for the launcher's own notices, so
no session can take it.

A launch with an identity runs under comms' wrapper, `moxi comms claude` or `moxi comms
codex` (which needs turns' Codex host), in this terminal or a slot, or headless where
comms' wrapper takes its headless flags (see below). Before anything is written the launcher asks comms about the name: one
held by another session, reserved for a module or retired is refused (3), naming who or
why. When comms can't reach its daemon, or gets an answer from it that it can't read, the
launch is a try-again (75), since the wrapper would stop before the harness starts. A
Codex launch with an identity takes no extra flags, since comms' Codex wrapper takes only
its own. When the wrapper ends before the harness starts (comms' daemon stopped after the
check, say), its words show where it ran and the row ends `failed` with `comms-offline`
(75), `comms-no-answer` (75: the daemon didn't answer the bind in the wrapper's wait),
`identity-taken` (3) or `comms-failed` (1). The launcher waits for the bind as long as
comms says its wrappers wait, and 5 s more. A wrapper that never starts the harness
without the name and is still alive then, the name not its own, ends the row
`comms-failed`. One that starts the harness anyway after its wait leaves the row
`started`.

**A headless launch with an identity.** Where comms' wrapper for the harness takes
`--headless` (comms' API lists the harnesses in `HEADLESS`; today Codex), `--target
headless` with an identity runs `moxi comms codex --name N ... --headless --brief
<first turn>` in turns' Codex host, detached as any headless launch is, its output in the
run log. The session holds its name between turns, takes mail, and ends by its exit
rule: the `headless_exit` row, one `--exit` per value (`done`, `cold`, `idle=<D>`,
`after=<D>`, D like `90m` or `2h`). Unset, comms' default applies: the session ends once
its brief and the mail that reached it are done. The launcher checks the values with
comms' own parser before anything starts, so a bad one is refused (2) in comms' words,
and a rule with `idle=` or `after=` and no `cold` gets a warning: mail sent once the
session's cache is cold waits for the name's next holder. `headless_exit` set by a
preset or a flag on any other launch is left out with a warning; set in your `[defaults]`
it is left out without one, as `director` and `scope` are. `keep_warm` and
`recycle_on_expiry` are left out with a warning on a headless launch with an identity,
since nudging or recycling the session would work against its exit rule. The launch
returns at once; the deliverer watches the bind and ends the row `failed` as above when
the name can't be taken, and sends nothing more. When the deliverer can't be started, the
launcher ends the wrapper (SIGTERM), and its session with it, and the row ends `failed`,
`no-deliverer`. A harness comms doesn't list (Claude, for now) is refused headless with
an identity.
`moxi comms exit-session NAME` ends such a session.

## What the launcher changes on your machine

Every other answer the launcher gives a harness is for one launch only, on its command
line or in its own run files, which the sweep removes. These are the exceptions:

| Change | Who writes it | When | Where you see it | Undo |
|---|---|---|---|---|
| `~/.claude.json` (`$CLAUDE_CONFIG_DIR/.claude.json` when set): `projects["<git root>"].hasTrustDialogAccepted = true` | the launcher | a Claude slot launch with `startup.trust_dialog` on, in a git root (or folder outside any) not yet trusted; never the home folder | the install question; the launch's `trust-seed` event in `moxi launcher log` | leaving it is harmless. To take it out, set that folder's key to false while no Claude session is open (a running session can write its own copy back); the next launch there seeds it again unless the setting is off |
| the same key | Claude Code, after the launcher presses "Yes, I trust this folder" | the seed missed or was refused and the screen showed, with `startup.trust_dialog` on | the launch's `startup-screen` event | as above |
| `~/.claude/settings.json` (`$CLAUDE_CONFIG_DIR/settings.json` when set): `skipDangerousModePermissionPrompt: true`, the file written out again | Claude Code, after the launcher presses "Yes, I accept" | a `bypassPermissions` slot launch with `startup.bypass_warning` on, when the per-launch key did not keep the warning away | the install question; the launch's `startup-screen` event, which names the file | remove the key; the next bypass session started without the per-launch key shows the warning again. While the setting is on, a later held launch writes it back |

Nothing goes into Codex's config. Before this release a Codex launch under turns' host, or
headless, could leave `[projects."<path>"] trust_level = "trusted"` tables in
`~/.codex/config.toml` (Codex's own write, sometimes for the whole repository, or the main
checkout of a worktree); the launcher can't tell those from your own entries, so look there
if you want them gone.

## Settings

Set with `moxi config set launcher <key> <value>`:

- `page.launch_wait_s` (600): how long a launch made from the page may take, and how long
  a preview stays good for launching.
- `ready.wait_s` (60): how long a slot launch, and the first-turn sender, wait for the session to be ready.
- `first.wait_s` (120): how long it then waits for the first turn to start.
- `startup.trust_dialog` and `startup.dev_channels_dialog` (both off): whether a slot
  launch answers Claude's folder-trust and development-channels screens. With
  `startup.trust_dialog` on, a slot launch also marks its folder's git root trusted in
  `~/.claude.json` before the session starts. The launcher presses a key only on a screen
  it has just seen; a screen whose setting is off fails the launch `screen-not-answered`.
- `startup.bypass_warning` (off): whether a slot launch accepts Claude Code's
  bypass-permissions warning when the per-launch key did not keep it away. Claude Code then
  writes `skipDangerousModePermissionPrompt: true` into `~/.claude/settings.json`, which
  turns the warning off for every later bypass session on the machine. Off, the launch
  fails `screen-not-answered`; one bypass launch in a terminal, accepted there, makes the
  same write.
- `startup.wait_s` (20): how long a slot launch watches for those screens. Without turns,
  a Claude slot launch takes this long before it returns.
- `runs.keep_days` (30): how long run logs, prompt files and settings run files are kept.
- `relaunch.wait_s` (900): how long a self relaunch waits for its session to be quiet.
- `park.wait_s` (300): how long park waits for each session to be quiet.
- `relaunch.person_s` (120): how recently a person's use of the pane, or a turn no
  delivery opened, holds an act.
- `relaunch.dialog_s` (60): how long a relaunch or an exit waits at the exit dialog.
- `relaunch.name_wait_s` (15): how long a relaunch waits for the old session's comms name
  to be let go.
- `relaunch.follow_s` (300): how long a director's relaunch, or a repair, follows its run.
- `relaunch.fallback` (empty): a comms identity told of a failed relaunch when the launch
  has no director, or when the notice to its directors is refused or fails.
- `relaunch.pass_env` (empty): variable names a self relaunch, a repair or a resume
  carries from the environment of the process running it into the new session. If you
  sign in with a token or key in the environment (`CLAUDE_CODE_OAUTH_TOKEN`,
  `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, a provider's keys), list it here: the launcher
  never records a credential, so without this a relaunched or resumed session starts
  signed out.
- `relaunch.director_pass_env` (empty): the same for a director's relaunch, read from
  the director's environment. Empty, nothing of the director's reaches the session.
- `relaunch.notice_retry_s` (30): how long a refused notice is tried again.
- `recycle.limit_wait_s` (1800): how long a compact at the limit waits for the session to
  be idle.
- `keep_warm.subagent_cap` (3) and `keep_warm.director_cap` (10): how many nudges in a row
  each case gets; `keep_warm.subagent_texts` and `keep_warm.director_texts` are their
  words. `keep_warm.director_cap` also caps the nudges for the agents a director
  directs.
- `keep_warm.directing_texts`: the nudges for a director while agents it directs are at
  work, in order, the last repeating; the agents at work are named after each, then the
  open holds.
- `records.keep_days` (90): how long ended runs, their events, exits, released holds and
  closed park entries are kept. Open park entries are never deleted by age.
- `name.template` (`<date>-<time>-<project>-<agent>`) and `name.templates` (empty):
  the template a launch's session name is made from, and a project's own by project key
  (see "The session's name").

## Install

Linux, Python 3.12 or later, and [uv](https://docs.astral.sh/uv/), with Claude Code or
Codex. Launching in this terminal and headless needs nothing more. A slot launch needs
the slots module and tmux (Linux and WSL2; macOS untested), and keep-warm and recycle on
expiry need cache, which reads a session's environment on Linux and WSL2 (macOS
untested). The launcher
also has macOS and Windows code for the rest, which the repository's CI tests without
gating and nobody runs yet: treat both as untested.

```sh
uv tool install moxi
moxi install launcher
```

The first line installs `moxi`, Moxi's core, which provides the `moxi` command; the
second adds the launcher beside it, runs its setup and offers each module under
Optional links as an extra (`--with turns,comms`, say, answers ahead). The repository's
README, at [CodeManEthan/moxi](https://github.com/CodeManEthan/moxi), covers the `moxi`
command and lists the other modules.

## Upgrading

After upgrading to this version, restart cache's watcher so it asks about running
sessions: `moxi services restart cache/watch` with services installed, or stop `moxi
cache watch` and start it again. It is harmless where the upgrade already restarted it,
and the asks it started keep running. Until it restarts, `Keep warm` is not offered for
a running session; a launch kept warm by itself is kept warm as before.
`moxi doctor`'s row `keep-warm.timer` warns while the restart is still needed and a
launched session is there to read it by. If it says moxi-cache is older than 0.1.6,
upgrade moxi-cache first: a restart alone doesn't help.

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/moxi-launcher-v0.1.3/packages/launcher/LICENSE).
