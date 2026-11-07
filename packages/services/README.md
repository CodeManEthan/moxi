# moxi-services

Moxi's services: one list of every daemon and timer on the machine, with start, stop,
enable, disable, add, remove and watch.

```
moxi install services
```

## Three kinds of row

- **A module's** daemons and timers, as each installed Moxi module declares them:
  `notes/snapshot`. They come and go with their module. Enable and disable flip the
  switch the module declares for them (`moxi config set`); one that declares none can be
  stopped, not turned off.
- **A command you add**, which Moxi writes as a unit for your system's supervisor
  (systemd, launchd or the Windows Task Scheduler): `services/web`, or just `web`.
- **A unit already on your system** that you adopt: a systemd user unit, a launchd agent
  in `~/Library/LaunchAgents`, or a scheduled task. Its own file is never written.

Names are unique across projects.

## Commands

```
moxi services list [--project KEY] [--kind daemon|timer] [--health] [--json]
moxi services show ID [--json]
moxi services add NAME [--timer (--every N | --at HH:MM[,HH:MM...] [--days mon,...])]
                       [--cwd DIR] [--health "CMD ARGS"] [--project KEY]
                       [--launches-session] [--doc TEXT] [--enable] -- ARGV...
moxi services add NAME --unit REF (--enable | --disable) [--timer] [--health "CMD ARGS"]
                       [--project KEY] [--launches-session] [--doc TEXT]
moxi services remove ID
moxi services start|stop|restart ID...
moxi services enable|disable ID...
moxi services watch [ID...] [--every SECONDS] [--json]
```

A command you add starts off unless you give `--enable`. `stop` stops a daemon until the
next login or start, and is remembered; it changes nothing about whether the service is
on. Adopting a unit takes `--enable` or `--disable`, so the row says what is true; with
neither, `add` tells you what the supervisor reads now. On Windows an adopted task is a
daemon, judged by whether its process runs, unless you give `--timer`; give it for a
scheduled task, or for a launcher whose action starts a program and exits.

Exit codes: 0 done; 1 an act failed, or the state read after it doesn't match; 2 asked
wrongly; 3 refused; 75 busy, try again. Given several ids, a verb acts on each and exits
with the worst.

## One verdict per row

Every row reads `ok`, `warn`, `fail` or `off`, with a reason and the command that fixes
it, the same in `list`, `show`, `watch` and `moxi doctor`. A timer is judged by whether
its schedule is armed and how its last run went, never by whether a process is running.
A daemon that is on and not running is `fail`, unless you stopped it by hand (`warn`). A
value the supervisor can't give is `warn`, "unknown", with the reason. A health check you
give with `--health` runs when asked (`show`, `watch`, `list --health`, the doctor),
only for a running daemon, for five seconds at most: exit 0 is healthy.

## Adopted timers on Linux

An adopted systemd timer is wrapped: Moxi writes one drop-in beside the service it
triggers, so each run goes through Moxi's gate and leaves a run line. Its on/off is a
switch in services' config, `adopted.<name>`:

```
moxi config set services adopted.backup false
```

does what `moxi services disable backup` does. While it is off the schedule stays armed
and the gate holds each run. `remove` takes the drop-in away again and leaves the timer
as it was before (disabled first if its switch was off, since without the gate it would
run). A timer whose service has several `ExecStart` lines, a `Type` other than `oneshot`,
`simple` or `exec`, or a systemd older than 243 can't be wrapped, and so can't be
adopted. A timer and the service it triggers belong to one row: a service is not adopted
while another row holds the timer that triggers it, nor a timer while another row holds
its service.

## Projects

With `moxi-projects` installed (`moxi install services --with projects`), `--project
KEY` files a row under a project, and a project isn't removed while a service names it.

## The page

With the `ui` extra (`moxi-ui`, the browser UI shell), services has a page in the shell,
at `/m/services/`, and a block on the shell's home that counts the rows that are `ok`,
`warn`, `fail` and `off`, each verdict as `moxi services list` gives it, and links to the
page. Each act on the page runs the same code as the `moxi services` command you would
run in a terminal, and refuses where that command refuses.

- **The list.** Every row, grouped by project, with its verdict and reason, its health
  check's last result, its switch or on/off, and since when it runs or how its last run
  went. Open a row to see its command, unit, directory, schedule and the supervisor's own
  words. The page and the home's block follow the rows as they change: while either is
  open, services' page server reads the supervisor every 5 seconds and runs the health
  checks every 30, at most eight at a time, and it keeps doing so for a while after the
  last one closes, until the server stops when idle.
- **Acts.** Start, stop and restart for a daemon, run now and run again for a timer,
  enable and disable, and remove for a row you added. Each opens the shell's confirm
  sheet, and nothing is sent until you confirm. A run of a row whose runs launch an agent
  session says on its sheet that it spends usage.
- **One at a time.** The page server runs its acts one at a time, each as a job, and
  answers at once with the job's status. The page follows that status until the job ends,
  shows what the command printed, and keeps every act's control off while one runs. If
  the answer is lost, the page says the act may have been done and reads the rows again.
  The server keeps its jobs only while it runs: after it restarts, a status it holds no
  record of says so, and the row's state shows the outcome.
- **Add.** The form `moxi services add` takes: a command line to run, or a unit already on
  the system to adopt, with its schedule, directory, health command, project and doc. A
  command line's first word must be a full path (`/usr/bin/python3`, not `python3`), and
  the page refuses one that isn't. The page server runs under your service manager,
  whose `PATH` isn't your shell's, so it doesn't search one; `command -v python3` in your
  shell prints the full path. Give a health command's first word in full too, since the
  checks run with that same `PATH`. As in a terminal, a command you add starts off: tick "Turn
  it on now" to start it now and at every login. Adopting a unit asks you to say whether
  it is on or off.
- **On the phone.** Through the shell's gateway the phone shows the list and the home's
  block, and can start, stop, restart, run, enable and disable a row, each confirmed on
  the phone. It can't add or remove a row, and doesn't see the projects list: those stay
  at the desk. When the gateway lets the phone read but not act, each sheet says to do the
  act at the desk. Every text that carries the supervisor's or a command's words is
  redacted on the way out, and a store that can't be read is named on the phone without
  its path.

## Uninstalling

`moxi uninstall services` refuses while a command you added still has its unit, naming
the `moxi services remove` for each, and gives every adopted Linux timer back as it was.
Other adopted units are left untouched.
