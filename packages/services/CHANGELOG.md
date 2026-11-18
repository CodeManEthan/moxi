# Changelog

## Unreleased

- A verdict gains `cause` in `moxi services list --json`, `show --json`, `watch --json`
  and the page's reads: one word for each case a verdict tells apart, listed in the
  README's "One verdict per row".
- The page's rows gain a timer's declared schedule (`timing`), `ends_slots` on slots'
  server, `has_settings` on a module row whose module has settings, the last run's
  `exit` code and when its health check ran (`checked.at`). A module row in `moxi
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
