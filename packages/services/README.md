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
neither, `add` tells you what the supervisor reads now.

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
adopted.

## Projects

With `moxi-projects` installed (`moxi install services --with projects`), `--project
KEY` files a row under a project, and a project isn't removed while a service names it.

## Uninstalling

`moxi uninstall services` refuses while a command you added still has its unit, naming
the `moxi services remove` for each, and gives every adopted Linux timer back as it was.
Other adopted units are left untouched.
