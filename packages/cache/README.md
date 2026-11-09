# moxi-cache

Moxi's cache module tells you when an agent session's prompt cache goes cold. A read gives
a session's cache one of five kinds, `warm`, `cold`, `fresh`, `no-cache` or `unknown`,
with its age and the lifetime it counts, and a reason code whenever it gives `no-cache` or
`unknown`. It reads Claude Code sessions through the sessions module's transcript reader,
Codex threads from their rollout files, and OpenCode sessions from OpenCode's database,
opened read-only. Codex and OpenCode lifetimes come from a lifetime table shipped with the
module, keyed on harness, provider and model, which you can extend in cache's config. A
watcher keeps a clock record for every live Claude Code session, a status line segment
shows a session's cache age from that record, and a timer runs the commands that modules
or your config subscribe, at set times before or after a session's cache expires. cache
says what a session's cache is and leaves what to do about it to the caller. Other Moxi
modules import it through `moxi_cache.api` (comms, with its `cache` extra, holds a send
while the recipient's cache is cold), and it adds these commands:

- `moxi cache read SESSION... [--harness claude|codex|opencode] [--source PATH] [--json]`
  prints each session's kind, age and lifetime, or why it reads `no-cache` or `unknown`.
  It writes nothing.
- `moxi cache segment SESSION [--color|--json]` prints a Claude Code session's status
  line segment from the watcher's record, such as `cache:12m3s`, and nothing when there
  is no record.
- `moxi cache watch` runs the watcher in the foreground. `moxi install cache` writes it
  as a unit of your system's supervisor (a systemd user unit on Linux) and starts it;
  `moxi cache doctor` shows the unit and its state.
- `moxi cache timers [--json]` lists the timer's subscriptions, the refused ones and why,
  and when each subscription next asks about each live session.
- `moxi cache doctor` checks the config, the lifetime table, the drift record and the
  watcher, and exits 1 when something there stops reads.

Its settings are `claude.ttl_s`, `lifetime` (your own lifetime table rows),
`watch.tick_s`, `segment.warn_before_s` and `timer.subscriber`; `moxi config cache` shows
them.

A lifetime row, shipped or your own, names a harness (`codex` or `opencode`), a provider,
its models, and either `seconds` with an `anchor` or `charge = "free"`. It also needs a
`source` (the provider's documentation that promises the number) or a `basis` (why the
number is counted where no source promises it), and a `checked` date. `evidence` and
`note` are optional. Your row adds a model the shipped table has no row for and never
overrides one. While any row is one the loader can't vouch for, every Codex and OpenCode
read is `unknown`, and `moxi cache doctor` names the row. A row written before cache 0.1.4 may
say `ruling` for `basis`; it is read the same.

## Install

Linux, Python 3.12 or later, and [uv](https://docs.astral.sh/uv/).

```sh
uv tool install moxi
moxi install cache
```

`moxi install cache` brings sessions with it. The core's README, at
[CodeManEthan/moxi](https://github.com/CodeManEthan/moxi/tree/main/packages/moxi), covers
the `moxi` command and how `moxi install` works; the repository's README lists the other
modules.

## Dependencies

`moxi`, Moxi's core, and `moxi-sessions`. One optional extra, `ui`, shows cache's panel
and its chip in the browser UI shell (`moxi install cache --with ui`). The watcher lists
live sessions with Claude Code's `claude agents --json`, so it needs `claude` on PATH or
at `~/.local/bin/claude`. Its unit is written by the core, so it needs a supervisor the
core writes units for: a systemd user manager, launchd or Task Scheduler.

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/main/packages/cache/LICENSE).
