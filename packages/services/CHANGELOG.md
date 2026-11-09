# Changelog

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
