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

The watcher also writes its reading, `watch.json` beside the clock records, after every
tick: when it last ticked and when it last listed Claude Code's live sessions. Its page
and `moxi cache doctor` read it to say whether the watcher is reading, running but not
listing, or stopped, and when.

With the `ui` extra, cache's page in Moxi's browser shell lists each session that has a
clock record. A row is named as the Sessions page names it, by its typed name, else its
folder and when it started, and never by its id. The rows sit in two groups, `warm` and
`cold`, each with its count: warm rows say when they go cold, soonest first; cold rows
say since when, most recent first. A row turns cold at its time with no new read.
Sessions whose record holds no clock time sit behind one line at the foot of the list.
Opened, a row shows its last request and its cold time to the second, the time left or
since, the lifetime in words, a line saying when an old record was last rewritten, its
harness, model, start and folder, a link to the session on the Sessions page when
sessions holds it, and `Copy id`. The foot says when the records were last read and
links to cache's settings. When the watcher is not reading, a row above the groups says
so and, when it has stopped or never ran, gives the command that starts it. The page
says nothing it did not read: a fact that is missing is not drawn. The chip, one
session's cache age, stays offered to other pages.

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
