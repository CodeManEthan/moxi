# Changelog

## 0.1.6 (2026-11-07)

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

- `units.status` and the five acts take `kinds`, a mapping of a foreign unit to
  `"daemon"` or `"timer"`. Only Task Scheduler reads it: a foreign task read as a
  daemon is active only while it runs, failed when its last result is outside Task
  Scheduler's success band, and its acts wait for the task's state around `/run` and
  `/end`. With no kinds every row and act is as before.
- New `units.TASK_STATUS_CODES`, Task Scheduler's success band (0x41300-0x413FF).

## 0.1.4 (2026-11-07)

- New `moxi.terminal`: the front door records the terminal's environment and its launch
  chain, and the doctor reports that record in its terminal row. The check that compares
  a page's environment with the record lives in ui.
- The Windows folders come from the shell through ctypes; the core no longer needs
  platformdirs.
