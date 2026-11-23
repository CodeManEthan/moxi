# Changelog

## Unreleased

- An opened row draws its foot (its acts) and its module's links only once the row's own
  reads (`/api/row`, and `/api/ends` for slots' server) have answered, so a line those
  answers add, such as `Unit` or `Needs it`, no longer moves `Stop` under a press.
- The page's `Reading…` line, while its first read is out, shows once that read has
  been out 400 ms in every browser. In Firefox it could stay blank until something
  else redrew the page, since the page checked the wait against `Date.now()`, which
  Firefox coarsens. It now takes the wait from moxi-ui's `WaitClock`, so the `ui` extra
  asks for moxi-ui 0.2.6 or later.
- The page is rebuilt around what needs you: the rows that need you rise into a first
  group, worst first, the rest are grouped by project and Moxi's by module, each row
  says in words what it is doing, and an opened row shows its lines and the acts of its
  foot. Acts go at a press and are followed to their end, asking in place only where
  they spend usage, remove a row or end slots' sessions; adding and adopting move into a
  panel; the page has addresses for a row, a project, and the panel. With the gateway's
  acts on, the phone offers every row act but Remove through the shell's sheet.
- A verdict gains `cause` in `moxi services list --json`, `show --json`, `watch --json`
  and the page's reads: one word for each case a verdict tells apart, listed in the
  README's "One verdict per row".
- The page's rows gain a timer's declared schedule (`timing`), `ends_slots` on slots'
  server, `has_settings` on a module row whose module has settings, the last run's
  `exit` code, when its health check ran (`checked.at`), a failing timer's
  `streak` and `wrapped` on an adopted Linux timer; the list gains `at`, when the supervisor was read, and the stream sends it
  again at least once a minute. One row's read gains the unit that runs it, every id
  the supervisor knows it by, and a timer's runs today; a new desk read says what the
  supervisor knows of a unit not yet adopted. A module row in `moxi
  services show --json` gains a timer's `every`, `at` and `days`.
- With the `ui` extra, a `project-facts` component reports each project's failed and
  warned services to the Projects page, and the opened project's `Services` row.
- The `adopted` switch table carries page fields, which `moxi config services show
  --json` prints and the settings page draws: a plain label and a line. services needs
  moxi 0.1.9 or later.

## 0.1.2 (2026-11-08)

- `moxi services watch --every` is now `--every-s`, with no alias.
- The process fields `uptime_seconds` and `cpu_seconds` are now `uptime_s` and `cpu_s`.

The first public release.

- One list of every daemon and timer on the machine: each installed module's, commands
  you add, and units already on the system that you adopt, each with one verdict, and
  `moxi services list`, `show`, `add`, `remove`, `start`, `stop`, `restart`, `enable`,
  `disable` and `watch`.
- With the `ui` extra, a page in the browser UI shell and a block on its home; with the
  `projects` extra, rows filed under projects.
- services needs moxi 0.1.6 or later.
- The package metadata gains the README as its long description, keywords and
  classifiers, and the source distribution includes this file.
- The README gains install, dependency and license sections and a line on what Moxi is.
