# Changelog

## 0.1.7 (2026-11-08)

- Public names renamed for the naming rules (callers passing the old keyword or reading
  the old name must change): `moxi.cli.shortcut_text`: `windows` is now `is_windows`;
  `moxi.config.Key`: `type` is now `value_type`; `moxi.files`: `CLAIM_VARIABLE` is now
  `LOCK_FD_ENV`; `moxi.files.edit_toml`: `timeout` is now `timeout_s`;
  `moxi.files.lock`: `timeout` is now `timeout_s`; `moxi.ipc.Client`: `timeout` is now
  `timeout_s`; `moxi.ipc.Client.call`: `timeout` is now `timeout_s`; `moxi.ipc.connect`:
  `timeout` is now `timeout_s`; `moxi.ipc.serve`: `write_deadline` is now
  `write_timeout_s`; `moxi.ipc.Server`: `write_deadline` is now `write_timeout_s`;
  `moxi.names`: `is_skill_folder` is now `is_skill_dir`; `moxi.paths`: `ANCHOR_HOME` is
  now `ANCHOR_HOME_ENV`; `moxi.paths`: `DEVICE` is now `DEVICE_ENV`; `moxi.paths`:
  `HOME` is now `HOME_ENV`; `moxi.place`: `folders` is now `harness_dirs`;
  `moxi.place.plan`: `git_seconds` is now `git_s`; `moxi.proc`: `environ` is now
  `process_env`; `moxi.proc.run`: `input` is now `stdin_data`; `moxi.proc.run`:
  `timeout` is now `timeout_s`; `moxi.store.connect`: `timeout` is now `timeout_s`;
  `moxi.terminal`: `MARK` is now `STARTED_BY_ENV`; `moxi.terminal.read`: `retry` is now
  `retry_s`; `moxi.units.stop`: `record` is now `stopped`.
- The catalogue lists all sixteen first-party modules, so `moxi install <module>` and
  `moxi modules --available` know each by name. Its version is 2.
- The README says what the core needs (psutil, and its own copies of tomlkit and
  packaging), that macOS and Windows are untested, every command `moxi help` lists, and
  where `moxi install` writes outside Moxi's own folders: skills and agent files for
  Claude Code and Codex, and a module's daemons and timers. It points to the
  repository's list of packages instead of keeping its own, and installs from PyPI.

## 0.1.6 (2026-11-07)

Not published.

- Unit names carry the home. With `MOXI_HOME` set, units are named
  `moxi-<tag>-<module>-<name>` (systemd), `moxi.<tag>.<module>.<name>` (launchd) and
  `\Moxi\<tag>\<module>-<name>` (Task Scheduler), and run with that `MOXI_HOME`. The
  default home's names are unchanged.
- New `moxi.units.home_tag()`, `moxi.units.environment(module)` and
  `moxi.units.ids(module, name, kind="daemon")`. `ids` returns `()` with no supervisor,
  and on a supervisor this moxi has no writer for.
- `moxi run` takes `--home PATH`.
- The terminal check reads a tagged home's unit names too, and names the module that
  started the terminal, not the home's tag.
- `moxi install` refuses a module's units, cause `locator`, while the shell sets one of
  that module's own locators (`MOXI_<M>_HOME` and the rest); use `MOXI_HOME` for a
  second store.
- `moxi install`, and every other caller of the unit plan, refuses a daemon's socket
  address when another Moxi home's socket file names it, or when something other than
  this home's own daemon listens on it. The refusal names what holds it and the setting
  to change. A home re-installed while its own daemon runs is never refused for its
  port. To tell whose listener holds an address, the plan makes one supervisor status
  read, and a short TCP connect when its own socket doesn't hold it. Each address is
  judged by the socket unit that names it, so a home whose `::1` socket unit is down
  while its `127.0.0.1` one listens is still not refused for `127.0.0.1`.
- One-time cleanup: a unit an earlier `MOXI_HOME` install wrote has the default home's
  name, and only its owner can tell it apart, so disable and remove it by hand.
- `place.Item` gains `volatile`: paths inside a copied folder that the copy's and the
  source's hashes leave out, so an owner may write into a placed copy. The mark records
  them.
- A copied folder the placer makes is writable by its owner, so a copy of a read-only
  install can be written into and removed.
- The settle's skills step leaves every placement made with `dest` to whoever made it:
  a copy a module's `SETUP` places survives that module's settle and every later one.
- `test` is a name the core keeps for itself, since `MOXI_TEST_` is moxi-dev's test-tier
  prefix. New `names.reserved_why(name)`; `moxi install` and `modules.load` say why a
  kept name is refused.
- New `modules.setup_ran(module)`: whether the core records that the module's `SETUP`
  has run in this home.
- The fake units backend, a test helper, is no longer part of the installed package
  (`moxi.units._fake` is gone); it lives in the core's tests.

## 0.1.5 (2026-11-07)

Not published.

- `units.status` and the five acts take `kinds`, a mapping of a foreign unit to
  `"daemon"` or `"timer"`. Only Task Scheduler reads it: a foreign task read as a
  daemon is active only while it runs, failed when its last result is outside Task
  Scheduler's success band, and its acts wait for the task's state around `/run` and
  `/end`. With no kinds every row and act is as before.
- New `units.TASK_STATUS_CODES`, Task Scheduler's success band (0x41300-0x413FF).

## 0.1.4 (2026-11-07)

Not published.

- New `moxi.terminal`: the `moxi` command records the terminal's environment and its launch
  chain, and the doctor reports that record in its terminal row. The check that compares
  a page's environment with the record lives in ui.
- The Windows folders come from the shell through ctypes; the core no longer needs
  platformdirs.

## 0.1.3 (2026-11-04)

Not published; summarised from the repository's history.

- `files.append_record` takes `refuse`, a set of field names a record may not hold at
  any depth, and `collapse`, which writes the first rows of a busy class and counts the
  rest, so a log facing unauthenticated callers stays bounded without losing a row
  silently. Both are off by default.

## 0.1.2 (2026-11-03)

Not published; summarised from the repository's history. Moxi's packages moved into one
repository, `CodeManEthan/moxi`, and the core grew the foundation the modules share.

- Python 3.12 or later.
- Only the core provides the `moxi` command; a module installed on its own brings none.
- New commands: `moxi uninstall` (with `--purge`), `moxi paths`, `moxi doctor`,
  `moxi help`, `moxi version`, `moxi modules --available [--refresh]`, and `moxi config
  set` and `unset`.
- The catalogue of first-party modules: `moxi install` refuses a name it doesn't list
  unless given `--third-party` or `--from`.
- The announcement, a module's one declaration of itself, read in schemas 1 and 2.
  Among what it may declare: daemons and timers, which the core writes as systemd user
  units, launchd agents or Task Scheduler tasks; socket daemons, started at login or at
  the first connection (schema 2 lets that follow a bool setting); skills and agent
  files, which the core places for Claude Code and Codex; and doctor checks.
- New modules for module code: `moxi.files` (atomic writes, locks, records, with
  `append_record` able to write durably and without a size cap), `moxi.proc`,
  `moxi.aio`, `moxi.ipc` (a newline JSON-RPC wire over Unix sockets), `moxi.units`,
  `moxi.place`, `moxi.doctor` and `moxi.log`.
- `MOXI_HOME` puts every module's files under one directory.
- The config reader keeps comments when it writes, through a vendored copy of tomlkit.
- The module contract check moved to the contributor tools, `moxi-dev`; `moxi check` is
  gone.

## 0.1.1 (2026-10-22)

- Comments and docstrings state each rule in plain words.
- The source distribution lists the paths it includes instead of the ones it leaves out.

## 0.1.0 (2026-10-21)

The first public release.

- The `moxi` command: `moxi modules`, `moxi install <module>` with its optional extras,
  `moxi shortcut <module>`, `moxi config <module>` and `moxi check`, which checks a
  module repository against the module contract.
- Names, paths, one config file per module, and module discovery through the
  `moxi.modules` entry point.
- Python 3.11 or later.
