# moxi-statusline

Moxi's statusline module gives Claude Code a status line. Claude runs one command,
`moxi-statusline-render`, at each render of its line. The command reads the JSON Claude
hands it on stdin and prints one or two rows: base fields from that input (directory,
branch, model, effort, context and a few more) and the segments installed Moxi modules
add, such as usage's limit figures and cache's timer. It shows each value as Claude or the
module gives it and computes nothing of its own.

A render shows status and triggers nothing. It writes no file, starts no process, sends
no signal and opens no socket, and a segment that tries is refused and shows nothing. It
exits 0 on every path: an input that isn't JSON prints the line with `?` for the
directory, and a config that can't be read prints `config?`, each with one line on
stderr.

## Install

Linux, Python 3.12 or later, and [uv](https://docs.astral.sh/uv/).

```sh
uv tool install moxi
moxi install statusline
```

The core's README, at
[CodeManEthan/moxi](https://github.com/CodeManEthan/moxi/tree/main/packages/moxi), covers
the `moxi` command and how `moxi install` works; the repository's README lists the other
modules.

At each module's install, statusline compiles the bytecode a render loads (the core, this
package, and each installed module's segment), since a render can't write it.

### Claude Code's setting

After the install, statusline looks at the `statusLine` setting in Claude Code's user
`settings.json` (in `$CLAUDE_CONFIG_DIR`, else `~/.claude`):

- Moxi's line already: it points the setting at this environment's render, and asks
  nothing.
- No status line: it asks "Use Moxi's status line in Claude Code? [Y/n]". Yes sets Moxi's
  line, with the items you saved, or the default items when you saved none.
- Your own line: it offers to run that line once with test input, to see what it shows
  (your script may store what it is given, so it asks first). Then it says what it
  found and offers two things:
  1. Switch to Moxi's line, set up to show the same pieces in the same order and rows,
     plus the installed modules' segments yours lacked at the end of the first row.
     Pieces Moxi has that yours didn't show stay off. When your line shows Claude's 5h
     and 7d limits and usage isn't installed, it says `moxi install usage`. Your line is
     kept for `restore`.
  2. Keep your line. Nothing is written; it prints one command per piece of Moxi's line
     (cache's timer first) to call from your script, with what each costs, measured just
     then. `moxi statusline pieces` prints the same list later.

It asks once and records the answer, so a later install asks nothing; `moxi statusline
use` asks again. With nobody at the keyboard it asks nothing, runs nothing and records
nothing: with no line it says how to switch, and over your own line it prints both
offers' commands. An install never edits your own command: it replaces the setting whole
or leaves it alone.

The test run is your command as Claude Code runs it, `sh -c`, with made-up input on
stdin: each piece of it (the folder, branch, model, effort, context, limits, session,
transcript, lines, cost) carries a marker no other piece or the clock can print, so the
output shows which pieces your line prints, and where. It runs in a fresh folder under the
system temp folder, with that folder's empty `home` as `HOME` and no `XDG_*_HOME`,
`MOXI_*` or `CLAUDE_CONFIG_DIR` set, so what the script writes under its home lands there
and is removed after; a `~` or `$HOME` in the command itself is still your own home. It
gets 5 seconds, then it and every process it started in its group are killed. Writes to
absolute paths, and processes that leave the group, are not caught.

Moxi's line is a `statusLine` whose command is the absolute path of a
`moxi-statusline-render`. A script of your own that calls it stays yours.

Each write keeps the old bytes of `settings.json` (the newest 5) in statusline's state
folder, and is dropped, with one line, if the file changed since it was read. The file
keeps its mode, key order and indent; a `settings.json` that is a link stays one; a file
that isn't a plain JSON object is left alone. When the switch replaces a line, the line is
kept so `restore` can put it back. Moxi's line keeps that line's `padding` and
`refreshInterval`, and refreshes every 5 seconds when it set none, so the cache timer
counts while a session is idle. A project's own `.claude/settings.json` is never touched,
though Claude Code uses its line in that project.

`moxi uninstall statusline` puts your old line back, or removes Moxi's line when there was
none before it. A line of your own is left as it is; any `moxi-statusline-render --only`
pieces it calls stop working.

## The line

The items, in the order `show` gives them:

| item | from Claude's input | shown as |
|---|---|---|
| `dir` | `workspace.current_dir`, else `cwd` | `~` for your home folder; `…/a/b` past `dir.parts` parts |
| `branch` | the repository holding that folder | the branch in its `HEAD` file, or the first 7 characters of a detached commit |
| `model` | `model.display_name` | with a leading `Claude ` cut |
| `effort` | `effort.level` | as given |
| `context` | `context_window.used_percentage` | `ctx:42%`, yellow from `context.warn_at`, red from `context.alert_at`; `ctx:—` before the first response |
| `session` | `session_id` | `#` and its first 6 characters |
| `transcript` | `transcript_path` | the file name |
| `lines` | `cost.total_lines_added`, `total_lines_removed` | `+12/-3` |
| `cost` | `cost.total_cost_usd` | `$1.24` |
| `row` | | starts the second row |
| a module's name | that module's segment | what the segment shows |

An item with no value is left out with its separator. The branch is read from files, so
the line starts no `git` process, and shows no mark for uncommitted changes. A module item
shows only while that module is installed and announces a segment; the base field names
come first, so a module named like one is hidden behind it.

## Config

Every key is read at each render; `moxi config statusline` shows them.

| key | default | what it does |
|---|---|---|
| `show` | `["dir", "branch", "model", "effort", "context", "usage", "cache"]` | the items, in order; one `row` starts a second row, and may not be first or last |
| `separator` | `" │ "` | the text between items |
| `color` | `true` | colours on |
| `dir.parts` | `2` | how many trailing parts of the directory are kept (at least 1) |
| `context.warn_at` | `50` | yellow from this percent (not above `context.alert_at`) |
| `context.alert_at` | `80` | red from this percent |

## Commands

- `moxi statusline use` sets Claude Code's status line to Moxi's, keeping the line it
  replaces. Over Moxi's line it points it at this environment's render. With no line it
  asks first, and the switch keeps the items you saved; with nobody at the keyboard the
  command is the yes. Over your own line it makes the two offers above, whatever was
  answered before; with nobody at the keyboard it prints both and changes nothing.
- `moxi statusline restore [--force]` puts back the line `use` replaced, or removes the
  setting when there was none. When Claude Code's line is no longer Moxi's (you set
  another since), it refuses with one line; `--force` puts the saved line back over it.
- `moxi statusline status [--json]` says whether Claude Code's line is Moxi's, its
  command, the line kept to put back, the items shown and which show nothing, modules
  named like a base field, and a project's own status line in the current folder.
- `moxi statusline pieces` prints the commands that add Moxi's pieces to a status line
  of your own, one per installed module with a segment, cache's timer first:

  ```sh
  printf '%s' "$input" | /path/to/bin/moxi-statusline-render --only cache
  ```

  where `$input` holds the JSON Claude Code gave your script on stdin. Each piece starts
  Python once per render of your line; the verb runs each command through `sh -c` with
  a sample input, once to warm up and 5 times more, and prints each median. cache's
  figure is for a session with no clock record.
- `moxi statusline preview [--show a,b,c] [--no-color]` renders a sample input with the
  real command and prints the line and how long the render took. `--show` renders these
  items in place of `show`.
- `moxi-statusline-render [--show a,b,c] [--only NAME] [--config PATH] [--no-color]` is
  the render itself, reading Claude's input on stdin. `--only` prints one item and nothing
  else; `--config` reads that file in place of the module's config file, and prints
  `config?` when it is missing or can't be read.

`moxi doctor` checks that the command Claude Code's setting names resolves to an
executable, that what a render loads is compiled, times one sample render
against the module's budget (three times the start of `python -I -c pass`), and names
`show` items with nothing behind them and installed modules named like a base field.

## The page

With the `ui` extra (`moxi-ui`, the browser UI shell), statusline has a page in the shell
where you pick the line's items, put them in order and see the line before Claude Code
does: `moxi install statusline --with ui`.

- **The line.** Three lists: Row 1, Row 2 and Off. Each item on the line has a switch
  that turns it off and up and down buttons that move it one place. Moving the last item
  of row 1 down puts it at the start of row 2, starting row 2 when there is none, and
  moving the first item of row 2 up puts it at the end of row 1. An item switched on
  from Off goes at the end of row 1. Off lists the base fields and the installed
  modules with a segment that `show` leaves out. A base field is named in plain words
  (Folder, Git branch, Context used) with its `show` name beside it; a module's item
  shows its name and summary.
- **Options.** The text between items, colours on or off, how many folder parts are
  kept, and the percents where the context turns yellow and red. Each key's name sits
  beside its label (`separator`, `dir.parts`, ...); click it to copy it.
- **Preview.** The line your changes give, rendered by the real
  `moxi-statusline-render` over a sample input in a made-up folder, with how long the
  render took. An item that shows nothing for the sample gets a note under it: `branch`
  shows inside a git repository, and a module's segment shows in a live session. When
  the render fails or runs past 2 seconds, the preview says why.
- **Save and Discard.** Every change goes into a draft, and the preview shows it at
  once. Claude Code's line changes only when you press Save, which writes the draft to
  the config in one checked write; Discard drops it. Both show while the draft differs
  from the saved config, with how many settings it changes ("2 settings changed"): all
  the moves and switches in the line are one setting, `show`. A draft the config's
  check refuses shows why in place of the preview, and Save stays off until you fix it.
  When the config file changed since the page read it, the page reads the new file,
  keeps your changes over it and says so. A config that can't be read shows why, with
  the defaults, and the page saves nothing over it; the preview shows `config?`, the
  line Claude Code prints until the config reads again.
- **Claude Code's status line.** Whether Claude Code shows no line, this line, Moxi's
  line from another install or your own, with its command. "Use Moxi's line" sets it to
  Moxi's line with the saved `show`, keeping the line it replaces, as `moxi statusline
  use` does but with no question and no offers. "Put my old line back" shows while
  Moxi's line is set and an old one is kept, and does what `moxi statusline restore`
  does. Both buttons are disabled while the draft has unsaved changes: save or discard
  first.

## Dependencies

`moxi`, Moxi's core. Segments come from whichever modules are installed; statusline
imports none of them. One optional extra, `ui`, is for the status line's page in the
browser UI shell.

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/moxi-statusline-v0.1.0/packages/statusline/LICENSE).
