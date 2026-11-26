# Changelog

## Unreleased

- A handoff waits until its seat takes it. A seat's start no longer marks the handoff it
  points to as picked, so the pointer comes back at every start until the handoff is
  taken, given to a launch, or a newer one of the key is. The pointer now ends "then run:
  moxi launcher handoff take <path>". A handoff an older launcher marked as shown stays
  picked.
- The handoff lines show after a `/clear` too (a compaction still shows nothing). A
  launched session keeps its claim across the clear, so it is pointed to its own handoff.
- New `moxi launcher handoff take [FILE] [--key KEY] [--json]` records a handoff as taken:
  this session's waiting one with no FILE. It refuses, writing nothing, with no key, with
  nothing waiting, and for a FILE that is missing, named for another key, or outside the
  session's handoff folder unless `--key` names its key.
- The launch log moves to schema version 5: the `picks` table is rebuilt so a pick may be
  `taken`, keeping every row.
- The launch defaults, `[defaults]` of `presets.toml`, are a table `moxi config` shows and
  changes (`CONFIG_TABLES`, `moxi_launcher.defaults`): `moxi config launcher show` lists
  them as `defaults.<row>`, and `moxi config set launcher defaults.<row> VALUE` and
  `unset` write one key, keeping every other byte of the file. The Settings page shows
  them as the `Launch defaults` block. The rows are those a launch on this machine takes;
  keys launches refuse or leave out are listed first, under `needs a fix`, to be cleared.
  A write is refused only by the row's own check (its type, the values offered here, a
  refused, unknown or retired name or value) or a file that can't be read; a value that
  fails only on some launches is written, and the row carries the launcher's words for
  it. A key held for a row this machine doesn't offer is kept and not listed. `moxi
  config unset` prints the lines the table still carries. Needs `moxi>=0.1.11` for `moxi
  config` to reach the table; an older core ignores it.
- `moxi launcher presets check` warns, at `defaults`, of what a launch of either harness
  with no preset and nothing typed meets from `[defaults]`: a rule between rows that
  refuses it, or a self-relaunch item that a `recycle_at_limit = "relaunch"` fails before
  the launch has a folder (the harness, a slot target, slots running here, scratch).
- On the Projects page, each running session's line in the opened project's `Sessions`
  row says it runs (`runs`) and its `last active` part that it holds only while the
  session is not in a turn (`idle_only`), so the page can join turns' `working` there.
  The component reads nothing new.
- `recycle_on_expiry = compact` compacts the session shortly before its cache expires,
  no longer after: inside a window from 360 s before the expiry (where keep-warm's
  shuts) to 60 s before it, counted on cache's age plus the last response's length
  (cache's `response_s`), while cache reads it warm, it is quiet and nothing is at work
  by keep-warm's reading (live subagents, the chain's open holds, directed agents),
  whether or not keep-warm is on. An open hold now stops a compact as it stops a clear.
  It compacts at most once in an idle stretch, read from the compact runs of the
  launch's chain, and reads cache again after its run is written, right before it acts.
  A lifetime of 360 s or less has no window. cache's timer asks through a new
  subscription, `launcher.compact-before-expiry`, every 60 s in the window; the
  `launcher.recycle-on-expiry` subscription now asks only sessions stamped `clear`. A
  cache older than 0.1.7 gives no `response_s`, and a compact ask then ends quietly
  `unreadable`.
- keep-warm reads cache again after its run is written and drops the nudge, quietly
  `outside`, once the window has shut, so on one cache clock no nudge comes after the
  compact's window opens.
- The launcher no longer compacts a session at its context limit. `recycle_at_limit`
  takes `off` and `relaunch`; `compact` and the `limit_at` row are retired, refused when
  typed or set by a preset and left out with a warning from `[defaults]`.
  `recycle.limit_wait_s` is retired and left out with a warning. A replay of a launch
  recorded with `compact` runs `off`, with a warning. Pending compacts and open limit
  compacts are ended by the store's prune, and `moxi-launcher-job compact` is a stub
  that ends its run `failed` (`retired`). A relaunch launch grants turns `exit` alone.
- `moxi launcher show` and Home show a relaunch launch's mark again, read from its
  `warn_at` (`at limit relaunch (2x)`), and show a launch recorded with `compact` as
  `compact (no longer acts)`.
- The `cache` extra now needs `moxi-cache>=0.1.7`, the first cache whose clock carries
  `response_s`.
- The launcher needs moxi 0.1.11 or later: its settings mark the retired
  `recycle.limit_wait_s` `left_out`, a field an older core refuses.
- A director's relaunch always carries a handoff, the new session's first turn as a self
  relaunch's is: `moxi launcher relaunch SESSION_ID|IDENTITY --handoff FILE`, else the
  seat's newest waiting handoff written since the old session began, checked against the
  relaunched session's launch; with neither it is refused `handoff`. The new session
  starts `relaunched`, stamped `MOXI_LAUNCHER_RELAUNCH`, and the line after its header
  names the old session and whether it went cold or could not take a message, with `moxi
  sessions read` for it when sessions is installed.
- A director's call held by subagents names each one, with how long ago it last wrote and
  when it would read stale, and says that a message or ending its shells would wake the
  session and whether keep-warm was on for it. One held only by subagents listed waiting,
  on a session whose turns endpoint doesn't judge stale ones (`stale-agents`), is refused
  `old-endpoint`, exit 3, since trying again won't help.
- A relaunch run follows its session to a new turns endpoint. Claude Code replaces a
  session's endpoint when its plugins reload, and applies a pending reload as a turn
  ends, the moment a self relaunch's wait releases; the run used to send its exit to the
  old endpoint, read `gone`, and end `not-exited` (`no-dialog`). A Claude launch's run
  that meets its endpoint gone at the exit, at the end of its wait's watch, or before
  its closing note, while the launch's own `claude` still runs, looks the session up
  again for up to 5 s and goes on with the new endpoint, writing a `moved` event: the
  exit waits again for a fresh turn number (a director's run reads it from the new
  endpoint, whose count restarts), the wait waits on, the note reaches the session. A run
  moves at most 3 times. With `claude` ended, nothing found in time or another harness,
  it goes on as before. `exit-session` is unchanged.
- A launch records the session bus address (`DBUS_SESSION_BUS_ADDRESS`) with its base
  environment when it has one, and a relaunch restores it, so a relaunched seat and the
  jobs it starts can still reach the system keychain on Linux. The replaying process's
  own address wins when it has one, since the address can change at each login.
  Elsewhere the name is absent and nothing changes. A seat already relaunched without it
  gets it back at its next launch or relaunch from a process that has it.
- The `subconscious` row (`off`, `automatic`, `on-demand`; needs turns and memory) turns
  memory's subconscious on for one launch, or for every launch of a preset that sets it:
  a launch with it on stamps the mode under memory's `SUBCONSCIOUS_ENV`, read from
  memory's API. A fork drops it. memory joins the optional links, with a `memory` extra.
- The `subconscious_route` row (`subagent`, the default, or `slot`; needs turns and
  memory, and for `slot` with the subconscious on, slots, comms and tmux slots that run
  here) picks where a launch's recalls run, stamped beside the mode under memory's
  `SUBCONSCIOUS_ROUTE_ENV`; nothing is stamped with the subconscious off. On Codex only
  `slot` runs a recall: a Codex launch on `subagent` is accepted and has the
  subconscious off at run time, which memory's log and doctor say. A fork drops it.
- A value's `when` table in a catalog takes `applies_with`, another launcher row: the
  value's needs hold only while that row is set off its default. `subconscious_route`'s
  `slot` applies with `subconscious`, so a route set in `[defaults]` or a seat preset
  doesn't refuse a launch with the subconscious off where slots or comms are missing.
- A recall's slot fork, a launch whose recorded environment carries memory's
  `SUBCONSCIOUS_FORK_ENV`, is memory's to end: park lists it `a recall's fork, which
  memory ends; left alone` and neither exits nor reserves its slot, crash discovery
  settles its slot rather than reserving it, and restore drops an entry made for one
  before this, releasing its slot, without counting it against the exit status.
- A headless launch with an identity keeps `keep_warm = "on"`, with no warning, as any
  other launch does. A nudge is a turn, which starts the exit rule's `idle=` count again,
  so the session ends by its rule once keep-warm stops nudging. `recycle_on_expiry` is
  still left out there with its warning.
- An open hold is work out wherever a live subagent or a directed agent at work is. Home's
  Sessions row reads a session with neither, but holds of its chain open, as waiting on
  them (`waits` kind `holds`, `waiting on 1 open hold`, `kept warm while 1 hold is
  open`), and offers `Keep warm` at its cap; the press counts those holds as work out
  (read as if cover had just been set) and records cover with that wait. A refused
  press with nobody at work now says no hold is open either.
- `moxi launcher show` reads keep-warm off wherever keep-warm can't act on this install
  (`keepwarm.can_act`: the `keep_warm` row isn't offered, with turns or cache missing or
  where cache can't read a session's environment), as the stamp and the start text's
  `keep warm:` line already do.
- The director case no longer needs cover: every ask about a launch keep-warm is on for
  that doesn't nudge for a subagent reads the agents the session directs (the join,
  within 20 s), and with comms absent nobody is directed. A join or agent list not read
  whole, with no case found, ends the ask `unreadable`, quietly.
- Holds are taken and read wherever keep-warm is on, cover included: a launch made with
  `keep_warm` off takes holds while its session is covered, the hold verbs work
  there, `show` lists them, and an open hold keeps the cover as work out does.
- Home's Sessions row and the `Keep warm` press read the agents a session directs
  wherever keep-warm's ask would (`keepwarm.joins`): unless a live subagent's case is
  under its cap. A session whose subagent nudges are spent while an agent it directs
  works reads `kept warm`, and a press there is answered, as the ask nudges it.
- The start text's `keep warm:` line no longer asks for a hold per piece of work: it says
  subagents and directed agents at work keep the session warm, and that a hold covers
  other work.
- A run of nudges counts each case apart, by the word each nudge ended with: the subagent
  case up to `keep_warm.subagent_cap`, then the director case up to
  `keep_warm.director_cap`, so a director whose subagent nudges are spent is still nudged
  while an agent it directs works. A run with every case spent ends `blocked` (`capped`)
  naming each count (`subagents 3/3, director 10/10`). Each case's texts go by its own
  count. A director nudge with agents it directs at work and holds open names both: the
  directing texts, the agents, then `Open holds:`.
- Keep-warm's subagent case and the agents a session directs count the same entries
  (`waits.live_subagents`): a `subagent` or `teammate` entry not final and pending,
  running or waiting, and not a fork of the session's main loop. A directed Claude agent
  whose turn is closed is at work by the subagents its own agent list holds at work, not
  by turns' busy count, so a fork, or a subagent turns' `stale-agents` judgment lists
  `idle`, keeps neither case going. A list turns can't read whole leaves that agent
  unlisted, as before.
- A `compact` launch with `limit_at` unset and no `warn_at` mark crosses at `1x`; it no
  longer reads usage's `marks.default`, which usage no longer has, and
  `compose.common.usage_default_mark` is removed.
- A Claude launch is found by its `claude` process. An act accepts a session served by
  a new endpoint whose parent is the launch's `claude` when the claimed endpoint is gone,
  where it said `changed`; a session id no launch knows resolves to the launch whose
  `claude` serves it, where it said `not-launched`, in `relaunch --self`, exit-session,
  keep-warm, recycle on expiry, `show SESSION_ID` and a director's call by session id. A
  context crossing claims the launch for its endpoint (moving it by parent) and learns
  the session's id before it answers. A self relaunch's handoff of an id the launch never
  learned is checked against the first record of the id's transcript.
- A `relaunch` launch's self relaunch is armed only where its session can recycle
  itself: a Claude launch in a slot, where slots run, not in a scratch folder, with a
  handoff folder. It is told at its `warn_at` mark that it may only where it is also
  warned at a mark. One test (`selfrecycle.missing`, with `told` for the mark) decides
  what the composer stamps, what the crossing tells and what `relaunch --self` accepts.
  A `relaunch` with no warn-at mark is armed and never told, with no refusal and no
  warning; `show` says `at limit relaunch (armed, no warning)`. `recycle_at_limit` still
  ships `off`.
- A `relaunch` launch is told at `warn_at`, never at `limit_at`. A `limit_at` typed or
  set by the preset beside `relaunch` is refused `relaunch-mark`; from `[defaults]` it is
  left out with a warning. A `relaunch` set for a launch that can't act on it, a Codex one
  or one with no handoff folder, is kept and the launch warns, naming why.
- A `relaunch --self` refusal names what the launch fails: not set to `relaunch`, not a
  Claude slot launch, slots not here, a scratch folder, or no handoff folder. A launch with no notes directory is now refused `no-self-recycle` before its
  handoff is read.
- The relaunch line now says to stop your background shells: "write a handoff with the
  handoff skill, stop your background shells, then run `moxi launcher relaunch --self
  --handoff <file>`". It no longer names the mark (usage's notice, which it is appended
  to, does), so `RELAUNCH_AFTER` and `relaunch_line` are gone; the crossing returns
  `RELAUNCH_LINE`. A `relaunch` launch is still told at every rise past the mark, a
  compaction's included, and a `compact` launch still compacts at every rise.
- The `moxi-handoff` skill's recycle steps stop your background shells before the
  relaunch.
- With the `ui` extra, a `project-facts` component reports to the Projects page how many
  launched sessions run in each project, and for the opened project where its lead runs,
  a `Launch` row (its preset, its last launch and how it names sessions) and a `Sessions`
  row of what runs there. It reads only the launcher's existing routes.
- After a topic value the page reads its past sessions again through moxi-ui's
  `readLater`, so the page root's `data-reading` mark counts that read from the moment it
  is scheduled until its answer is drawn, and a test's press waits for the list it
  draws. A user sees no change. The `ui` extra asks for moxi-ui 0.2.8 or later.
- A `launches` value from the stream that leaves out a launch the page shows is read
  again from `/api/launches` instead of drawn. The stream can hand over a list older than
  the page's own read, which took a launch away from `Launches` for a moment and moved
  everything under it, the past sessions' buttons included.
- `moxi launcher fork SESSION_ID [--target slot|terminal] [--slot N] [--json]` starts a
  new session from a copy of a launched session's conversation, live or not, and prints
  its id. Claude Code forks with `--resume PARENT --fork-session`; Codex needs turns'
  Codex host, which forks with `--fork THREAD_ID` from moxi-turns 0.2.6 on, and the
  `turns` extra now requires that version. With an older turns a Codex fork is refused
  `not-yet`, naming it. Every catalog row now carries a fork class, `copy`, `drop` or
  `own`, and a row with none fails the catalog check: the fork copies the rows that shape
  the request the harness sends, unsets those about who the session is, its first turn
  and its life, and composes the launcher's own again. Its environment is a replay's,
  never the asking shell's.
- The launch log moves to schema version 4, adding `launches.fork_of`, the session id a
  fork copies; a log at version 3 upgrades in place. A launch with it has the new launch
  kind `fork`, first in the order `launch_kind` decides by, and a resume of a fork keeps
  it. A fork is pointed to no handoff and marks none picked. An older launcher refuses
  the log once it is at version 4.
- Interface 3 adds `fork(session_id, target=, slot=, stamps=)`,
  `exit_session(session_id, dry_run=)` and `slots_here()`, whether tmux slots run here
  as the launcher reads it, to the API. A fork's `stamps` are set on its
  environment after the scrub; each must be listed in the `SESSION_ENV` of the present
  module its own prefix names, and the launcher's and turns' are refused.
- `exit-session` exits a quiet Codex session under turns' Codex host by stopping the
  host: SIGTERM, then up to 15 seconds for it to end (`not-exited` when it still runs).
  It used to refuse every Codex session `no-exit`; one without the host still is.
- A seat or project preset (or `--no-takes-policy-directors`) opts its sessions out of
  comms' policy directors with the new row `takes_policy_directors = false`, composed as
  `--no-policy-directors` on comms' wrapper. `[defaults]` refuses the row (`per-seat`). A
  launch that opts out under a comms without policy directors is refused, naming the
  upgrade. On the page, a bool row whose default is true sends its unticked box as
  `false`, so the opt-out is kept.
- comms' policy directors count as directors of a launch with a comms identity that
  didn't opt out: they may make a director's relaunch call and they hear of a failed
  relaunch, beside the launch's own directors, each recipient once. The launcher reads
  comms' `policy_directors()` at the call or the notice, so only names provisioned as
  standing identities now, each as comms qualifies it (a bare name is the global
  identity, never read in the launch's scope). A comms without the call adds none.
- A launch follows its Claude session when Claude Code replaces the session's turns
  endpoint (each plugin reload does). A held Claude claim moves only to an endpoint whose
  parent process is the launch's own `claude`, the row's `pid` and `pid_started`
  compared by pid and start time, once the endpoint that held it has ended. No session id
  moves a Claude claim any more, so a session started from inside a launched one never
  takes it, even by naming a known id; a row with no `pid_started` never moves. The
  `reclaimed` event's detail gains `"by": "parent"`. A Codex launch keeps the known-id
  move.
- The ready recorder listens for turns' `load`, raised when an endpoint first learns its
  session id, so the replacing endpoint claims the launch at its own start. An id first
  learned there is recorded with the new source `load`, until a `ready` of the same
  endpoint names that id and gives it the `ready`'s source.
- When the replacing endpoint starts before the old one has ended, its `load` and `ready`
  events are queued in arrival order and handled once the old one ends, checked every
  100 ms for up to 5 s; if it outlives the wait, the next event moves the claim.
- Launches stuck on an ended endpoint heal at their next endpoint start after the
  update, with no repair step. A limit crossing a seat passed while stuck is not
  replayed.
- The `changed` refusal's hint for a claim not yet moved says it moves within seconds.
- The `turns` extra needs moxi-turns 0.2.5 or later: an older turns refuses a listener
  row naming `load`, and with it every launcher listener.

- Crash discovery: before every slot launch, a slot slots reads crashed (its run was
  going when its tmux server went down) whose last launch is a launcher slot launch with
  a session no one has taken up since is reserved for that session (an open-ended hold
  `park-<id>` and a `crash` park entry), and a crashed slot with nothing to restore is
  settled. A launch naming a reserved slot is refused `held by park-<id>`, with the hint
  `moxi launcher restore N` or `moxi launcher unpark N`. Every slot placement tells slots
  whether the harness's exit status can be trusted: not for Codex, which ends with status
  0 on SIGHUP and SIGTERM alike.
- The launch log moves to schema version 3, adding `park_entries`: one row per session
  that belongs back in a slot, at most one open per slot. Open entries are never pruned;
  closed ones follow `records.keep_days`. A park lock (`park.lock` in the state
  directory) names its holder's kind and pid. Needs moxi-slots 0.1.1 or later. An older
  launcher refuses the log once it is at version 3, so restart the launcher's page and
  every process that runs the launcher with the update.
- `moxi launcher park [--wait-s SECONDS] [--dry-run] [--json]` exits every launched
  session in a slot and reserves its slot for it first (an open-ended `park-<id>` hold
  and a park entry). Each exit waits for the session to be quiet, up to `--wait-s` or the
  setting `park.wait_s` (300 s), not counting a client's activity; copy mode, an open
  turn, subagents and a draft still hold it. It exits regardless of background shells,
  which Claude's exit dialog stops. A Codex session is left
  running and reserved, since Codex offers no exit, and so is the session park runs in.
  Exits 75 while another park or restore runs.
- `moxi launcher restore [SLOT ...] [--dry-run] [--json]` resumes each parked session
  into its reserved slot, made again when it is missing, under the reservation. A slot
  that can't take it sends the session to the lowest slot and releases the reservation;
  a session still running, or one taken up by another launch, is said and left or
  closed. A launch that fails keeps the entry and its reservation.
- `moxi launcher unpark SLOT ... | --all [--json]` gives reserved slots up; their
  sessions stay resumable with `moxi launcher resume`. A slot given up that reads
  crashed is settled, so it is not reserved again.
- `moxi launcher resume` of a parked session goes back into its reserved slot as restore
  does, with the brief, brief file or `--plain` it was given. Restore itself is given no
  brief, so it resumes each parked session with no first turn, as `--plain` does, a `go`
  launch's too. A slot resume runs crash discovery first, and a recorded slot another
  session's reservation holds sends the resume to the lowest slot, with a line saying
  why.
- `moxi launcher run --slot N`, the page's slot choice and a resume make a missing slot
  again when no one else holds it; one held by another is refused `held by H`.
  `moxi launcher run --help` has a line for `--slot`.
- The page's targets list marks each slot `choosable`, `crashed` (crash discovery would
  reserve it) and `parked` (the session a park reservation keeps it for), and its
  default slot is the one a launch naming none would take. Home's run menu words them
  `missing, made again at launch`, `crashed, reserved at the next launch` and `held for
  park-<id> · parked <name>`.
- `moxi launcher sessions` marks a session with an open park entry `parked in slot N`
  (`parked` in `--json`).
- Home fills the comms identity with the name the newest launch of the seat, project or
  folder of the harness the form is drawn for bound (a project's include those made
  through its seats), even one comms now reports retired, whatever harness the last launch
  ran, always at the global scope, so a project scope is no longer carried. Where there is
  no such name, and for any harness but Codex, it fills a default: a seat named for one of
  its project's agents that sets no identity gets its own name, else a project of kind
  `agent` its own key, else the project's `lead`, else `<key>-lead` when comms lists that
  name. `/api/last` answers the identity apart from the values, once for each harness, in
  `identities`. A filled identity comms refuses a launch under shows comms' refusal beside
  it, so the page says why that name can't launch; each identity in `/api/last` carries it
  as `refused`. One whose name a live session holds in a project scope shows a note naming
  that binding (`note`). A harness picked by hand fills the comms identity for that
  harness, unless the identity was set by hand.
- `moxi launcher clear-expired [--session ID] [--dry-run]` and `moxi launcher keep-warm
  --session ID [--dry-run]` make the expiry and keep-warm asks by hand; they run the same
  ask cache's timer starts. `clear-expired --session ID` asks only of the launch whose
  current session is ID, and says so and exits 0 when none is. `--dry-run` writes nothing
  and prints each session's verdict and what a run would do, and `keep-warm --dry-run`
  with no session reads every live launch keep-warm is on for.
- Exit-session holds while the session runs a background shell or Monitor, until the
  session has been idle an hour (`held`, word `shells`); only then does it exit and stop
  them at Claude's exit dialog. `--dry-run` says which. Park's exit doesn't wait on them.
- `moxi launcher resume` takes `--brief TEXT` or `--brief-file FILE`, and the page's
  resume a brief or a brief file beside each past session; the brief is sent as the
  resumed session's first turn, as a launch's is. A resume of a `go` launch with no brief
  is refused before anything starts (`posture-brief`), since a resumed session does not
  wake itself either, and a brief under `with:<identity>` is refused as on a launch. The
  posture is the one the launch was configured with, so a `go` launch's relaunch needs a
  brief to resume too, and the resumed launch shows that posture, where the relaunch
  shows its own. A plain resume, `moxi launcher resume --plain` or Home's `Continue the
  last session` with an empty box (`plain` on `/api/resume`), takes no brief and sends no
  first turn, a `go` launch's too; `--plain` with `--brief` or `--brief-file` is refused
  (`bad-value`). Without `--plain` the command line keeps the refusal, and so does the
  page's resume. Home's continue of a session that ran in a terminal copies the resume
  command with `--plain` when the box is empty.
- The start posture checks the brief: `preview` and `run` refuse a `go` launch with no
  brief, brief file or handoff, and a brief under `with:<identity>`, whose director
  sends the first message (`posture-brief`). A handoff under `with:<identity>` is sent
  as the first turn, before the director's message. On a launch that binds no comms
  identity, any words typed after `--` on the command line count as the first turn,
  flags alone included, since they go last on the harness's argv. Other flags passed on
  (unknown flags typed before `--`, a preset's or `[defaults]`' `extra_flags`, the page's
  field) don't count. A brief under `operator` is sent.
- A scratch launch works in `/tmp/scratch-<uid>/<session name>`, a folder named by its
  session name under a parent of the user's own, by the numeric uid, made 0700 if missing;
  `TMPDIR` is no longer read. The folder's name is the session name folded to lowercase
  letters, digits and single dashes (`Foo_Bar` works in `foo-bar`, `My spike` in
  `my-spike`), so the folder stays in the parent. A later launch under the same name works
  in the same folder. On Windows the parent is `scratch-<user name>` in the user's own
  temporary folder.
- A scratch launch keeps no notes: a `notes_dir` typed or set by its preset
  is refused by name (`scratch-notes`), one from `[defaults]` is left out, and a scratch
  folder notes would keep notes for is refused at preview and launch, with a hint that
  names the `notes exclude` setting for the scratch parent.
- With notes installed, a launch notes gives no notes directory (no `notes_dir` and none
  for its folder) has no handoff folder: a self relaunch is refused, saying the launch has
  no `handoffs/` to read from, `moxi launcher handoff dir` and `handoffs` refuse
  (`no-notes-dir`), its start names no folder and points to no handoff, and the
  moxi-handoff skill gives that reason beside a session with no key. The launcher's
  own folder, which these fell back to, is used only without notes. A notes that can't be
  read counts as giving no folder.
- `name.template` ships as `<date>-<time>-<project>-<agent>`, so a launch that sets no
  name is called, say, `20261108-1245-docs-planner`. It shipped as
  `<project>-<model>-<date:mmm-dd hh:mm>`; a template set in the settings is kept.
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
  lines under the box saying how the resume went. The box's text goes as the resume's
  brief, or the brief file while one is set, and a resume that starts empties the box as
  a launch does; an empty box sends none and resumes the session with no first turn, a
  `go` session too.
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
