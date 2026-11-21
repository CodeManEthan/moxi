# Changelog

## Unreleased

- A relaunch or repair job no longer fails as it ends: it read `relaunch.fallback` and
  `relaunch.notice_retry_s` in a way the launcher's settings don't support, so every
  note and notice was lost and the job crashed before it told anyone.
- A one-off, project or scratch launch with usage installed warns at `2x` when it sets no
  `warn_at` of its own; the row's default was empty, which left the mark to usage's
  `marks.default` (`off`). A seat launch outside a scratch folder, and a Codex launch,
  still warn only at a mark set in `[defaults]`, a preset or a flag. An install without
  usage neither offers the row nor stamps it.
- Home's line above the box offers `Scratch`, a throwaway folder, beside the folder and
  project picks. Picked, the launch sends `scratch` and neither `cwd` nor `project`; a
  folder or project picked again sends `scratch` off, so a launch from Home never names
  two places. A preset that sets `scratch` moves the line to `Scratch`, and `Continue the
  last session` says there is none to continue there.
- `moxi launcher relaunch --self` needs a launch made with `--recycle-at-limit relaunch`.
  Any other launch, one that compacts at its limit among them, is refused
  `no-self-recycle` (exit 3), a dry run too. A director's call and a repair need no such
  launch.
- The line a `--recycle-at-limit relaunch` launch gets at its mark now opens "This launch
  lets you recycle yourself", names the mark it passed, and tells the agent to recycle only
  when it is mid-work, to let running subagents finish and start nothing new, and to write
  its handoff with its handoff skill. The shipped `moxi-handoff` skill triggers on that
  line, and its last step says to end the turn and make no further tool call.
- `limit_at` has no default of its own: unset, a launch recycles at its `warn_at` mark,
  or at `1x` when it warns at none. `moxi launcher show`, the page and its feed name the
  mark such a launch was stamped with beside "at limit".
- A scratch launch can't set `--recycle-at-limit relaunch` (`scratch-recycle`, exit 3): a
  scratch has no notes directory to keep a handoff in. Its preview refuses it too, on the
  command line and the page, and `presets check` flags a preset that sets it and
  launches in a scratch. A `[defaults]` relaunch holds `off` on a scratch launch, with a
  warning. `compact` is open to it. A scratch
  launch recorded with the option before is refused the self relaunch, gets no recycle
  line, and recycles `off` when it is relaunched or resumed, with a warning. A scratch
  launch refused the self relaunch for any other value is pointed at compacting or a
  launch in a directory, not at the option it can't take.
- A relaunch of a launch that names no model comes back on the model the session last
  used, read from the newest request in its transcript that names one, and records it as
  a `model-pin` event; the new row keeps no model, so a resume of it runs the row's own. The
  arm, the call and the dry run name the model and how it was chosen, in `--json` as
  `model` and `model_how`.
- A relaunch checks that its own launch would be accepted before it touches the session:
  at the arm, at a director's call, in the job just before the exit, and in a repair
  before it launches. A launch that would be refused refuses the relaunch (`launch-check`,
  exit 3) or ends its run `refused`, with the session left as it was. A repair refused so
  launches nothing, and its notice says the session is down and names the `--from-record`
  command that tries it again. The session's own comms name and slot don't count against
  it. `--dry-run` prints the model and the check's verdict.
- A director's relaunch reads the session's cache again just before the exit, and a
  session that warmed up since the call ends the run `held`, word `warm`.
- A self relaunch that ends with its session still running tells the session in one
  turn: when it was not exited, when it stopped with an error before the exit, and when
  the session's id changed (a `/clear`), which now ends the run `changed` with nothing
  exited. The directors hear only when that turn can't be delivered.
- `moxi launcher relaunch --stop RUN` stops an open relaunch or repair run by signalling
  its job: before the exit the run ends `stopped` and a self run's session is told; from
  the exit on, the run ends once the relaunch has been tried. A repair's session is down
  already, so its stop is recorded and its line says so. It is refused for an unknown
  run, one that is not a relaunch, one that has ended, and one whose job is gone.
- A self relaunch refused by turns' pace (`rate`, `in-flight`) waits 60 s before it
  tries again, and every retry waits 2 s, so the run no longer loops at once.
- A relaunch's slot hold lasts 510 s, not `relaunch.wait_s`: it spans the exit, the
  dialog, the name step and the respawn. A hold left by a dead run of the same launch is
  released and taken again, once.
- A run left at the exit dialog (`exit-pending`) can be repaired with `--from-record` once
  a person has answered the dialog and the session has exited; its notice names the
  command.
- `relaunch --status RUN` exits as the run ended: 0 relaunched, 75 held or still running,
  1 any other end. Its lines go to stdout whatever the code. A run still open whose job is
  gone says it has no end coming and exits 1, and it, like a run left at the exit dialog,
  names the `--from-record` command when a repair would take it.
- A director's relaunch that came back names the identity, slot, new session, posture,
  model and whether comms has bound the name, in its line and in the run's `ended` event.
  The job waits up to 60 s for the bind, and a comms read that fails says `unreadable`,
  not "not yet bound".
- The armed text, the dry run and the came-back line say how the model was chosen in
  words: "pinned from your transcript", "the row's", or "default:" and the reason.
- The armed text names the model the session comes back on and ends "End your turn now
  and make no further tool call."
- A self relaunch's handoff must be UTF-8 text and not blank, and when the session's
  start was never seen it must be written since the launch was made.
- A director named without a scope directs only in the launch's own scope.
- A director's relaunch follows its run for `relaunch.follow_s` (300 s shipped);
  `--help` and the README say to give Bash a timeout of at least that plus 30 s.
- Home's `+` has `Handoff from a file`, where the harness's form has the `handoff` row.
  The handoff a launch sends, set there or named by `[defaults]` or the applied preset,
  shows on a line under the box with `Clear`; a cleared one is sent as left out, so no
  layer gives it, for the next launch only: once that launch starts or its command is
  copied, or a preset is picked or picked again, the preset's handoff, or the defaults',
  shows and is sent again. One set through the `+` stays over a preset change and a copied
  command and goes after the launch that sent it, as a brief file does. Home used to leave out every handoff. A handoff alone, with an empty
  box, is a headless launch's first turn.
- Three more Claude Code screens are on the startup list with no setting, so a slot launch
  held on one fails at once, `screen-not-answered`, naming the screen: the first-run
  theme picker and welcome screen (the line says to run `claude` once in a terminal with
  the same `CLAUDE_CONFIG_DIR` and finish its setup) and the "Claude in Chrome extension
  detected" dialog (the line says to answer it once in a terminal session). The launcher
  never answers them. Needs slots with `CLAUDE_THEME_PICKER`, `CLAUDE_WELCOME` and
  `CLAUDE_CHROME_DIALOG`.
- The `no-ready` line now reads "not ready: turns never reported the session ready; a
  screen the launcher does not know may be waiting, or turns may not be running in the
  session; attach to slot N", naming both causes.
- A Claude slot launch watches for every screen on the launcher's startup list (folder
  trust, development channels, the bypass-permissions warning) whatever the settings say.
  A screen whose `startup.*` setting is off now ends the launch at once,
  `screen-not-answered` (exit 1), with nothing pressed, the rows the screen showed and the
  setting that would answer it. Before, the launch waited out its watch. New setting
  `startup.bypass_warning` (off, asked at install) lets the launcher accept the bypass
  warning, after which Claude Code writes `skipDangerousModePermissionPrompt: true` into
  `~/.claude/settings.json`.
- With `startup.trust_dialog` on, a Claude slot launch marks its folder's git root trusted
  in Claude Code's `~/.claude.json` (`$CLAUDE_CONFIG_DIR/.claude.json` when set) just
  before the session starts, and records a `trust-seed` event.
- Every slot launch waits for ready, up to `ready.wait_s`, whether or not it owes a first
  turn. A fresh launch or a resume not ready in time now ends `failed`, `no-ready`, exit 1,
  where it used to end `started`, so a slow but healthy session can read `failed`; the
  session is left running. With nothing that can report ready (no turns) the launch ends
  `started` with the reason `ready-unknown`, and a Claude slot launch then takes
  `startup.wait_s` before it returns.
- A relaunch is flagged, never failed, by a screen the launcher won't answer or no ready:
  its row stays `started` with a `flagged` event and the result's `finding`. A self
  relaunch's run ends `first-turn-not-sent` with the finding as its word (a self
  relaunch's `no-ready` row used to end `failed`); a director's ends `relaunched` with the
  finding as its word and now tells the directors. `relaunch-failed` stays for a new
  session that is not running.
- A `bypassPermissions` Claude slot launch passes `--settings <request id>.settings.json`,
  a run file holding `skipDangerousModePermissionPrompt: true` for that launch only, merged
  with the launch's own `--settings` (a JSON string, or a file read and never changed),
  which it replaces on the argv. The run-file sweep removes it.
- A Codex launch, in interactive, host and exec mode, settles its folder's trust on its
  own argv: `trusted` with `trust_cwd` on; with it off, nothing when your Codex config gives
  the folder or its git project a trust level, else `trusted` for that launch only. The
  launch records the case as its `trust` value and `preview` prints it. Codex no longer
  writes a trust entry for a launch. Earlier Codex launches under turns' host, or headless,
  may have left `[projects."<path>"]` tables with `trust_level = "trusted"` in
  `~/.codex/config.toml` (`$CODEX_HOME/config.toml`), sometimes for a whole repository;
  they look like your own entries, so check that file. The no-ready line of a Codex slot
  launch no longer suggests the trust prompt.
- README: a section "What the launcher changes on your machine".
- A launch whose comms identity is a send-only name a program registered is refused as
  `identity <name> is a send-only name a program registered`, where it used to say the
  name had no owner. A launch whose wrapper ended, or ran out its bind wait, on such a
  name says the same in its failure line.
- A headless launch with a comms identity runs where comms' wrapper takes the headless
  flags: comms' API names those harnesses in `HEADLESS` (read by
  `presets.comms_headless`), today Codex, which then runs `moxi comms codex ...
  --headless [--exit W]... --brief <first turn>` in turns' Codex host (`host` mode),
  detached, its output in the run log. A harness comms doesn't name (Claude, for now) is
  refused as before, at each of the three places that refused it. The launch returns at
  once and starts the detached deliverer with the wrapper's pid, which settles the bind
  (a name the wrapper can't take ends the row `failed`, comms' words being in the run
  log) and sends nothing, since the brief rides `--brief`; a deliverer that can't start
  ends the wrapper (SIGTERM), so no session runs under the name of a `failed` row. New
  launcher row `headless_exit` (`--headless-exit`, repeatable, with comms): one `--exit`
  per value, checked at resolve and by `check` with comms' own `parse_exit`, so a bad
  value is refused (2) in comms' words before anything starts; `idle=` or `after=`
  without `cold` warns that mail held for a cold cache waits for the name's next holder.
  Set by a preset or a flag on any other launch it is left out with a warning; set in
  `[defaults]` it is left out without one, as `director` and `scope` are. `keep_warm`
  and `recycle_on_expiry` are left out of a headless launch with an identity with a
  warning. The size checks count the brief on `--brief` as the opening prompt.
- Every setting but the two name templates carries page fields, which `moxi config
  launcher show --json` prints and the settings page draws: a plain label, a line where
  the label can't carry it, the unit of each wait and count, six settings shown first
  (run files, run records, the fallback agent, the two startup screens and the variables
  a relaunch carries), the rest under Waits, Relaunch and Keep warm, and a can-hurt mark
  on turning on either startup-screen answer (the folder-trust one stays marked while on)
  and on adding a name to `relaunch.pass_env` or `relaunch.director_pass_env`. The
  launcher needs moxi 0.1.9 or later.
- `name.template` and `name.templates` are marked `elsewhere`: the settings page leaves
  them off, and its Find answers a search for either with the sentence that they are
  edited in Home's Options.
- The prompt's menus open inside the page: each one that would pass 16 px from an edge
  is shifted back, after it opens and when the window is resized. On a narrow page the
  plus, the chip and the options word wrap, so no button passes the edge either.
- The handoff lines answer at a resume as at a startup, and a resume of a launch the
  endpoint holds marks the handoff it shows as picked. After the seat's own pointer, one
  line per other seat's waiting handoff in the same folder, newest first, at most 5, then
  `… and N more`; these lines mark nothing picked. A session with no key gets them
  for every waiting handoff in its folder. New `handoffs.others`, `handoffs.keyed` and
  `handoffs.other_lines`: read without a key, a name's trailing `-<n>` is a count only
  when the same date holds the name without it.
- The crossing handler `recycle.on_crossing` is declared on usage's
  `moxi.usage.crossings` group, so a launch with `recycle_at_limit` gets the relaunch line
  or the limit compact when its session passes `limit_at`. Before, nothing called it.
- Home's Sessions row: a new component `running` (`<moxi-launcher-running>`), a row under
  Home's box that says how many launched sessions are running, and none when nothing is.
  Opened, it lists the first four with where each runs, its cache when known and the
  time of its latest message, or of its launch, then `All sessions (N)`. A session waiting
  on agents or subagents with a warm cache and no keep-warm cover comes first, says when
  it goes cold, and puts the row above the rows that need nothing. On the phone the list
  is always open. New route `GET /api/running`, reached by the gateway, with no path in
  its answer, and a third topic `running` on `/api/events`; a call of either starts the
  page server's poller. New optional link `sessions` (an extra of the same name).
  The page server reads each running session's cache (when cache is installed) and its
  latest message (when sessions is) apart from the launch log, for the 24 newest, every
  15 seconds and when the running sessions change, on four threads with an 8 second
  bound; what it read last stands for 45 seconds when a read does not finish. The first
  call of `/api/running` waits up to 3 seconds for the first of these reads.
- Home's Sessions row reads what each running session waits on while its own turn is
  closed: its subagents at work (with turns), else the agents its comms name directs
  that work (with comms too), each comms read bounded at 2 seconds and none after the
  daemon is found down. It reads a waiting session as kept warm only when keep-warm's next
  ask would nudge it at every step it can read, with an ask still certain to come before
  the window shuts. The reads write nothing, send nothing and never ask slots.
- Keep-warm: `keepwarm.signal_of` is the count of the ask's signal on values alone, which
  the ask and Home's row share, and `keepwarm.would_nudge` reads the ask's steps without
  writing. The ask does what it did.
- `target.find(.., slot=False)` finds a launched session without asking slots; the
  default, which every act uses, still asks.
- `Keep warm` for a session that is already running. On Home's Sessions row, opened at
  the desk, a waiting session that nothing keeps warm and that a press would cover has a
  `Keep warm` button before `Open`. Its tooltip says what a press costs, with the most
  nudges in a row. A press sends nothing to the session: it records keep-warm cover, and
  cache's timer's next ask sends the first nudge. The row then turns quiet, `kept warm
  while 1 subagent runs`, with `Open` alone, and the focus moves to that `Open`. The
  button stays disabled until the row's next update after the answer, or 20 s. A refused
  press shows its reason under the row until the row next changes. The phone reads the
  same rows and draws no button.
- `POST /api/keep-warm` (`{"request_id"}`), the press. It reads the session now, within
  8 s, as Home's row reads it. When keep-warm's next ask would nudge the session once
  covered, it writes one `keep-warm-cover` event and wakes the row's reader. On a
  session already covered it writes nothing. Otherwise it is refused with the first step
  that fails: `not-launched`, `no-session`, `unclaimed`, `changed`, `no-turns`,
  `turns-failed`, `not-live`, `run-open`, `no-timer`, `cold`, `no-window`, `too-late`,
  `busy`, `not-waiting`, `unreadable`, `capped`, `draft` or `ask-failed`. The phone's
  gateway does not reach it.
- Cover belongs to one launch and the session id it has now: a `/clear`, a relaunch or a
  resume starts without it. It is an event in the launch log, not a store step, so the
  log's version does not move and an older launcher passes over it. `moxi launcher show`
  reads a covered session as keep-warm on, with no holds listed. Holds still need the
  launch value: `keep-warm hold`, `release` and `holds` refuse a launch with cover alone,
  and an open hold keeps a session warm only when its launch was made with `keep_warm`
  on.
- Under cover a session is also kept warm while agents it directs through comms are at
  work. With no subagent of its own at work, the ask reads those agents and nudges with
  the new `keep_warm.directing_texts`, naming them; the `nudge` event records them as
  `directed`. `keep_warm.director_cap` applies to both of a director's signals, and
  under cover the cap counts only the nudges made since the press. The cover ends at the
  first ask that reads the session's own subagents and its directed agents whole and
  finds nobody at work; a read that falls short keeps the cover and ends the ask
  `unreadable`.
- Cache's timer now asks about every session the launcher started, by the request stamp
  `MOXI_LAUNCHER_REQUEST`, which a press needs, since a running process can't gain the
  old keep-warm stamp. An ask about a launch where keep-warm is off ends at its read of
  the launch log. `cachetimers.KEEP_WARM` names the subscription. After upgrading,
  restart cache's watcher (see the README's "Upgrading").
- `moxi doctor` has a row `keep-warm.timer`, with cache installed: it warns when the
  installed moxi-cache is older than 0.1.6, when cache refuses the keep-warm
  subscription, or when cache's running watcher started before the upgrade and should be
  restarted; it is `off` where a process's environment can't be read.
- The `cache` extra now needs `moxi-cache>=0.1.6`.
- Home's Sessions row reads cover. A waiting session counts as covered when its launch
  keeps warm by the launch value or by cover, cache's watcher says it asks keep-warm about
  it (its clock record's `asks`), and every other step of the next ask passes. A launch
  made with `keep_warm` on is no longer read as covered when the watcher's record says it
  doesn't ask. Each entry of `GET /api/running` and the `running` topic gains
  `coverable`: `{cap}` on a waiting session that isn't covered when a press would cover
  it, `cap` being the most nudges in a row the press would lead to; else null.
- `keepwarm.reading` names the first step at which the next ask would not nudge;
  `would_nudge` and `reading` take `directed` and `pressed`. The agents a session directs
  that are at work are read by one function, `waits.directed`, shared by Home's row, the
  ask and the press; it reports each way its read fell short as its own fact. The row
  reads as before.
- The launcher offers interface 2: `running()` gives the launches running now, newest
  first, each a frozen `Running` with its request id, harness, the session id it was
  last known by, target, slot, project key, folder, name, comms name, start time and
  whether its process runs. It reads only the launch log and the process table.
  `running(live_only=False)` keeps the launches whose process has ended; `limit` caps
  the count (200) and `timeout_s` the wait for a busy log (1 s).
- The launcher offers a piece for Home's main spot in the browser UI shell: a box to say
  what the agent should do, the line above it that picks the project or folder the
  session starts in, a chip that picks the harness, the model and the effort, and a
  button that starts the session. The button names where the launch runs (the lowest
  free slot, a new slot, or the command to copy where no slot can be used) and its caret
  opens the other choices; a launch from Home runs where the button says, whatever
  `[defaults]` set for `target` and `slot`. The lines under the box say how the launch
  went. New component `prompt` (`<moxi-launcher-prompt>`).
- Home's launch piece has `Options` beside the chip: a panel at the window's right edge
  with a find field and the launch form's other rows in the catalog's groups, each group
  saying while closed which of its values differ from the defaults. Each row changed
  there is named on a `Changed` line under the box and counted on the word
  (`Options · 2`); `Reset` puts every changed row back and leaves the box, the place,
  the chip and where the launch runs. A value that can do harm is in the theme's
  `danger` colour wherever it is drawn. Picked by hand it is set only after `Turn on`
  under it, and it holds for the one launch that starts with it or whose command is
  copied. A request from Home
  carries `off_posture: true` when a permission row it sends or leaves out is off the
  user's usual value, so such a launch is not refused for it.
- Home's `Options` panel draws a harness's row only where the place the button launches
  in passes its flag, and lets go of a value set on a row it stops drawing. Its Tools >
  Built-in is a line per built-in tool with `allow` and `deny`: a press changes only that
  tool's own entry in `allowed_tools` or `disallowed_tools`, scoped rules and every other
  entry stay listed in their places with `Remove`, and while `tools` names a set the
  lines it leaves out are dim.
- Home's launch piece has `Preset` on the line above the box once any seat or project
  preset is set up: its menu takes the preset off or applies one, each row saying what it
  sets and where it puts the launch. A project picked there, or the one Home opens on,
  applies that project's own preset; a seat stays applied when a place is picked. The
  applied preset's values are what `Changed` and the count measure from, and a dim line
  under the box names the permission rows it sets off the user's defaults.
- Home's launch piece fills each row the form leaves at its shipped default with the
  value the last launch of the seat, else the project, else the folder on the line above
  the box gave it, the harness first. `From last launch` on that line names the filled
  values, its list gives each with `Clear`, and the panel says under each when that
  launch was made. A filled value is no change, an edited one becomes the user's, and
  one that can do harm is in the `danger` colour, asks nothing and stays after a launch.
  The request names the filled rows in `filled`.
- Home's launch piece lists up to three earlier launches of the same seat, project or
  folder under the box, `Run again`, each by the first words of its brief and when it was
  made. Pointing at one shows a card with what a click runs today and where (the prompt,
  the harness, model and effort, the preset, where and when it ran, each permission row
  off the user's defaults, a value that can do harm in the `danger` colour, and the rows
  this install no longer offers) and what the click does; one click runs it again where
  the button's default places it, or copies its command for the user's terminal.
- Home's `Options` panel shows the session name under Seat > Name & notes: the name the
  template gives, following the seat, the place, the model and the comms name, with a
  sentence saying whose template it is, a folder inside a project naming that project's.
  A name typed over it is a change for that launch, and `Use the template` takes it
  back. Its caret opens the template's editor (pieces, date format, the template as
  written, `Keep for` that project), each edit rendered unsaved; `Save template` writes
  `name.template` or `name.templates` through the shell's own settings route, so a key
  locked for the shell's run is refused there.
- Home's launch piece has a `+` at the left of the chip with two entries. `Brief from a
  file` takes a path: while it is set the box is off and the launch sends that file as
  its first turn (`brief_file`, and no `brief`). `Continue the last session`, read each
  time the menu opens, resumes the newest session the user made in the place the line
  names that can be resumed now, in a slot or by copying its resume command, with the
  lines under the box saying how the resume went; the box keeps its text.
- A catalog row takes seven new optional fields, read by the browser's launch options:
  `group` and `sub` (where the row is drawn), `label` (its name in plain words),
  `danger` (its values that can hurt), `carry` (false for a row never filled from an
  earlier launch), `shown_with` (modules the row is drawn only with; no launch rule) and
  `typed_with` (the module a row with listed values needs to take a typed one). Every
  row a user may set has its label, and its group unless another control sets it. New
  `catalog.GROUPS` and `catalog.PANEL_FIELDS`. `GET /api/catalog` sends each field's
  `group`, `sub`, `label`, `danger`, `carry` and `modes` (the commands the row composes
  into), and `shown` and `typed`, which say whether this install draws the row and
  whether it takes a typed value.
- `GET /api/form` takes the query `identity` and answers `modes`: for each value of the
  `target` row, the command a launch of the form's harness composes there under that
  comms name, or null where such a launch is refused. A catalog field's `modes` are
  matched with it. New `pagehandlers.modes` and `page.FORM_MODE`.
- A fresh launch records what its own request gave: its values hold `sources`, where
  each row's value came from (`last` for a typed row the request names in `filled`, a
  value filled in from an earlier launch), and `filled`. A relaunch or resume records
  neither, and the launch log takes no new step. A page request's `filled` is signed
  with its ticket and written to the request file. New `GET /api/last?seat=|project=|cwd=`:
  the values the newest launch of a seat, else a project, else a folder was asked for
  beyond its preset and the defaults, without the place, the first turn, where it ran
  and its name. A launch logged before this records no sources and fills nothing. New
  `pagehandlers.request_of`, `api.LaunchRequest.filled` and `page.LAST`.
- New `GET /api/recents?seat=|project=|cwd=`: the newest three launches of a seat, else
  a project, else a folder that asked for different requests, each with the start of
  its brief (else its model and effort, else its harness) and where it ran. New `GET
  /api/recent?request=&target=&slot=`: that launch's request with where the page would
  run it now, resolved as `/api/preview` resolves a launch but with no ticket, and what
  it would run: harness, model, effort, brief, folder and project, the posture rows off
  the user's defaults, the rows holding a danger value, and the rows this install no
  longer offers. A launch that can't run again (its preset deleted, a value that no longer
  fits) is answered `refused`. New `presets.off_posture_rows`, the comparison the
  resolver's posture check refuses on, and `page.RECENTS`, `page.RECENT`.
- Every launch has a session name. New catalog row `name` (`--name`, or `name` in a
  preset); a launch that sets none is named from a template, the new settings
  `name.template` (shipped `<project>-<model>-<date:mmm-dd hh:mm>`) and `name.templates`
  (a project's own, by project key). The name is recorded in the launch's values, a
  relaunch or resume keeps it, and Claude Code shows it as the display name unless
  `native_name` is set, so every Claude launch now passes `-n`. **`--name` changed
  meaning for `moxi launcher run`:** it was passed on to the harness with a warning and
  is now the launcher's row; a Codex launch is no longer handed it. New module
  `moxi_launcher.names` (the template's pieces, date formats, cleanup and save check,
  `shown(row)`, what a launch is called), which needs no UI shell. `api.preview` names
  the launch as `api.launch` does. The page server answers `GET /api/name`: the name a
  launch of the form would take and the key of the project whose template gave it (for a
  folder, the project it is in), a template being edited rendered unsaved with the save
  check's refusal, and what saving it writes. A taken slot's label in `GET /api/targets`
  is the launch's name.
- Each entry of the past-sessions list (`GET /api/sessions`, `moxi launcher sessions
  --json`) names the project its launch ran in, `project_key` (null for a launch in a
  folder alone), and who made it, `actor`. `resume.Past` and `page.PAST` carry both.
- New module `moxi_launcher.live` holds `alive` and `live`, the read of which launches
  run now. They moved out of `moxi_launcher.pagehandlers`, which still offers both names,
  so a caller on an install without the UI shell can read what is alive.
- Home's launch line names the slot the launch takes when the page reads the slots twice
  at once: a press that reads the slots again waits for the newest read asked, where it
  used to go on with the old listing when a later read passed its own.
- `moxi launcher state` is now `moxi launcher show`, with no alias.
- Public names renamed for the naming rules (callers passing the old keyword or reading
  the old name must change): `moxi_launcher.api.check`: `environ` is now `env`;
  `moxi_launcher.api.launch`: `environ` is now `env`; `moxi_launcher.api.launch_kind`:
  `timeout` is now `timeout_s`; `moxi_launcher.api.launch_kinds`: `timeout` is now
  `timeout_s`; `moxi_launcher.api.preview`: `environ` is now `env`.
- `moxi launcher handoffs --key KEY` reads KEY's own handoff folder, the one of KEY's
  newest launch, so it finds the handoffs of a seat that works in another project. A key
  with no launch is read in the folder of the caller's directory, as before. It says on
  stderr which folder it read, and its `--json` names that launch as `launch`. Without
  `--key` nothing changes. New `handoffs.folder_of_key` and `handoffs.newest_launch`.
- The page server answers two new reads. `GET /api/places` gives where the user last
  launched (a project or a folder each, newest first, five at most), whether a launch
  here may name a project, and the user's home folder; `GET /api/targets` gives each slot with whether it is free
  and which launch took it, whether a new slot can be made, and the choice a launch
  starts with, which is never headless. New `store.places` and `store.launched_by`.

## 0.1.3 (2026-11-08)

The first public release.

- The launcher needs moxi 0.1.6 or later.
- The `usage` extra needs moxi-usage 0.2, the version in this release; with 0.1
  `moxi-launcher[usage]` could not be installed.
- The `ui` extra needs moxi-ui 0.2.3 or later, which has the colours below.
- A live session that's fine shows in the theme's live colour, and the launch form's
  "Starting…" line in its launching colour.
- A Codex launch with an identity whose comms lacks the Codex wrapper names the comms
  the `comms` extra asks for, read from the launcher's own requirement (it named
  moxi-comms 0.2). New `presets.needed(extra)`.
- The README documents the start postures (`operator`, `go`, `with:<identity>` and a
  self relaunch's `relaunched`) and installs from PyPI with `moxi install`.
- The package metadata gains keywords and classifiers.

## 0.1.2 (2026-11-07)

- The bind wait for a launch under comms' wrapper is comms' own `BIND_WAIT` plus 5 s
  (15 s with a comms that doesn't say).
- A wrapper that never starts its harness without the name, and is still alive when the
  bind wait runs out while comms says the name isn't this launch's, ends the launch
  `comms-failed` (1). Other live wrappers leave it `started`, as before.
- New reason `comms-no-answer` (75): comms' wrapper exited 75 because the daemon didn't
  answer the bind in time.
- The first-turn deliverer waits the full bind wait, and its ready wait counts from the
  bind.
- New extras `notes`, `cache` and `usage`, for the handoff folder, the prompt cache rows
  and the context warning rows. The `comms` extra now needs moxi-comms 0.3.

## 0.1.1 (2026-11-07)

- The launcher offers interface 1: `launch_kind(session_id)` and `launch_kinds(ids)`
  answer scratch, seat, project or one-off from the launch log, or None for a session
  the launcher didn't start.
- The launcher needs moxi 0.1.4 or later, for `moxi.terminal`.
- The `ui` extra needs moxi-ui 0.2, where the page-server base now lives as
  `moxi_ui.loopback`.
