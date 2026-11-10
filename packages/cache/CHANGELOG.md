# Changelog

## 0.1.5 (unreleased)

- The watcher writes its reading, `watch.json` beside the clock records, after every
  tick: when it last ticked, when it last listed Claude Code's live sessions (null until
  it has) and the records' `stale_after`. The file stays when the watcher stops.
- `moxi cache doctor`'s watcher line leads with what the reading says: the watcher is
  reading, running but not listing Claude's sessions since a time, or stopped at a time.
  With no reading file the line is as before.
- The page server's `/api/clocks` and its topic carry the watcher's reading and the
  server's local date (`today`); the topic publishes when the date changes.
- Two new reads on the page server, neither the gateway's. `GET /api/names` names every
  session with a clock record, in the records' order, through sessions'
  `session_facts`: its typed name, folder and start, and whether sessions holds it, with
  the server's date and the account's home folder. `GET /api/session?session=` sends the
  model of one session with a clock record, through sessions' `session_opened`, and
  nothing else it read. A read that raises answers `error` or a null model, and is
  logged once per class.
- cache needs moxi-sessions 0.2.8 or later, for `session_facts`.

## 0.1.4 (2026-11-08)

- Config keys `claude.ttl_seconds`, `watch.tick` and `segment.warn_before` are now
  `claude.ttl_s`, `watch.tick_s` and `segment.warn_before_s`. An old key, set in the
  config file, is refused, naming the new one.
- `moxi cache read --json` prints each session's `age_s` and `ttl_s` where it printed
  `age_seconds` and `ttl_seconds`.
- Public names renamed for the naming rules (callers passing the old keyword or reading
  the old name must change): `moxi_cache.api.Clock`: `age_seconds` is now `age_s`;
  `moxi_cache.api.Clock`: `ttl_seconds` is now `ttl_s`; `moxi_cache.api.projects_root`:
  `environ` is now `env`.
- A lifetime row's `ruling` key is now `basis`: the stated reason a number is counted
  where the source doesn't promise it. A row in your config that still says `ruling` is
  read as `basis`; a row that says both is refused. `Row.ruling` is now `Row.basis`.
- The shipped rows' `evidence` says what was measured and when.
- The README installs from PyPI, says what a lifetime row needs, and its links work from
  PyPI. The package declares keywords and classifiers.

## 0.1.3 (2026-11-07)

Not published.

- `moxi cache service install|remove|status` is removed. `moxi install cache` writes the
  watcher's unit.
- `moxi cache doctor`'s `service:` line is now `unit: cache/watch (<id>): <state>`, read
  from the core's unit status, with the id the supervisor knows the watcher by in this
  home.
- cache needs moxi 0.1.6 or later, for that id.

## 0.1.2 (2026-11-07)

Not published.

- The `ui` extra needs moxi-ui 0.2, where the page-server base now lives as
  `moxi_ui.loopback`.

Since 0.1.1, summarised from the repository's history:

- cache reads Claude Code, Codex and OpenCode sessions through the sessions module's
  readers, and needs moxi-sessions 0.2.
- With the `ui` extra, cache has a page and a chip in Moxi's browser shell, served by a
  page server of its own. `moxi_cache.api` lists the watcher's clock records.
- The watcher is declared as a daemon for the core to write as a unit, and the module
  imports on Windows.
- Python 3.12 or later.

## 0.1.1 (2026-10-22)

- A README and the MIT license.
- The module's invariants are stated in plain words, and test fixtures and names say
  what they are. The lifetime table's reasons drop their dated prefix.
- The source distribution lists the paths it includes.

## 0.1.0 (2026-10-21)

The first public release.

- `moxi cache read`: a session's cache is `warm`, `cold`, `fresh`, `no-cache` or
  `unknown`, with its age and lifetime, for Claude Code, Codex and OpenCode.
- A shipped lifetime table for Codex and OpenCode, which your config can extend.
- A watcher that keeps a clock record for each live Claude Code session, a status line
  segment, and a timer that runs subscribed commands before or after a cache expires.
- Python 3.11 or later.
