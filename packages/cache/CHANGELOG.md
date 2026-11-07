# Changelog

## 0.1.2 (2026-11-07)

- `moxi cache service install|remove|status` is removed. `moxi install cache` writes the
  watcher's unit.
- `moxi cache doctor`'s `service:` line is now `unit: cache/watch (<id>): <state>`, read
  from the core's unit status, with the id the supervisor knows the watcher by in this
  home.
- cache needs moxi 0.1.4 or later, for that id.
