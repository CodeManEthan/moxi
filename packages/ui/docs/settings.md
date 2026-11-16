# The Settings page

The Settings page, at `/m/ui/settings`, is the shell's own. It shows one module's settings
at a time and writes them only through `moxi config`, run in a fresh process, so a value
the page saves is checked exactly as one typed at a terminal. This file says how a
module's settings show on it, what a save does, and the routes the page uses.

## How a module's settings show

The page reads `moxi config <module> show --json` for each module and draws its keys in
schema order. A schema with no page fields still shows: each key by its dotted name, its
`doc` as the line under it, and every key at once. The page fields, which the core checks
when it reads the schema, change only how a key looks on the page, never how it is read:

| field | on the page |
|---|---|
| `label` | the row's words; the dotted key follows, dim, and a click copies it |
| `line` | the one dim sentence under a labelled row (an unlabelled row shows its `doc`) |
| `common` | the key shows before `More`; the rest wait behind it. A schema with no `common` key has no `More` |
| `group` | the sub-head the key sits under; `More` names the groups it holds |
| `unit` | the word after a number's field |
| `choices` | the words shown for each value of an enum |
| `pick` | an enum is a pick list whatever its number of values |
| `hurts` | the change it names (`on`, `off`, `add`, `remove`) is drawn in the danger colour and asked about before it is saved; with `stays`, the row stays in that colour while the value held is `when` |
| `terminal` | the value shows as a word, with no control, and the line `Turned off at the terminal: <off>` (or `on`) with `Copy`. The shell refuses to write the key from any page |
| `elsewhere` | the key is not drawn, counted or shown changed; Find answers with this sentence |

A key whose `needs` names a module that is not installed is left out the same way.
ui's `gateway.*` keys show only in the Phone group (below).

**Controls, by type.** A `bool` is a switch. An `enum` of two to four values shows them
side by side, the current one boxed; five or more, or `pick`, a pick list. An `int` or
`float` is a short field. A `string` or `path` is a field; one over 80 characters, or one
holding a line break, shows its first line and `Edit`. A `list` of short items (none over
24 characters, none holding a space) shows each as a box with `Add`; a list of longer items
shows `<n> lines · Edit`; a list of tables is a table with a row per item. A switch table
has a switch per entry, and any other `table` its TOML text behind `Edit`.

**Under a row**, dim: the line; when a change does not apply at once, the key's mark as a
sentence (`Applies at the next start.`); `Set by <variable> in the shell's environment,
which wins over the file.` when the environment sets it; `Locked for this run of the
shell.` when `MOXI_UI_LOCKED_SETTINGS` locks it, with the control off. A key whose value is
not its default shows `default <value>`, and `· Reset` after it when a write would change
the value read: the file holds it, it is not locked and it is not changed at the terminal.

## Changing and saving

Nothing is written until Save. A changed row says `not saved · was <value> · Undo`, and the
page holds each module's changes while you move between modules or search. Save sends the
shown module's changes, and only those, in schema order. Each runs as one `moxi config set`
or `unset` in its own process; one refused does not stop the next, and nothing is rolled
back. The Save line then says what was saved and when each change applies, or `<k> of <n>
saved`, with each change that was not saved saying why beside its row.

A change is not written when the key's value moved since the page read it (another tab, a
hand edit, an item added to a list elsewhere): the row says so, keeps what was typed, and a
second Save writes it. A list is sent whole and never merged.

While any change is held, leaving the page asks first, in the Save line's place, and
closing the tab gets the browser's own prompt. The page does this with `holdLeave` from
`/m/ui/client.js`, which any page may use (the README's "A page that holds changes").

## Find and the changed view

Find filters every drawn key of every listed module, by its label, key, line and value, and
each result can be changed in place, with a Save line per module. `N changed`, under the
list, shows every key not at its default, with `default <value>` and Reset by the rule
above.

## The Phone group

While the gateway extra is installed, ui's settings end with the Phone group: `Phone
access` as a word (it is changed at the terminal, `moxi ui gateway enable`), where phones
reach the gateway or why they can't, `Phone port`, each paired phone with `Revoke`, `Pair a
phone` while the gateway serves, and `Activity`, the audit log. Revoke acts at once; it is
not a setting and waits for no Save.

## Addresses

After `/m/ui/`: `settings` opens the core, `settings/<module>` a module,
`settings/<module>/more` the module with `More` shown, `settings/ui/pair` ui with `Pair a
phone` open, and `settings/changed` the changed view (a listed module named `changed` wins).
An address naming a module that isn't listed opens the core with `<name> has no settings
here.` The page rewrites its address as things open, without a new history entry, so Back
leaves the page. On a phone, Settings is not shown, and its addresses say settings are
changed at the desk.

## The routes

The page's routes are ui's, served by the shell under `/m/ui/`, desk only: each takes the
desk session's cookie and key, and none is the gateway's, so a phone reaches none of them.

**`GET /api/settings[?module=<m>]`** answers `{sections, listed, error}`. `listed` names the
core and every present module with settings, from the shell's module listing alone; with
`module`, the answer holds that module's section only, and a module not in `listed` gets no
section and runs no command. A section is `{module, keys, error, fix, readonly, file,
count}`: `file` is the module's config file with the home folder written `~`, `count` its
drawn keys (not counting ui's `gateway.*`). Each key carries its field descriptor, its page
fields as `show --json` prints them, and:

- `json` and `default_json`: its value and its default as JSON text, so a page never reads
  TOML;
- `changed`: true when the value is not the default (a switch table: when any entry is on).

**`POST /api/settings/save`** writes one module's changes. Its body is `module` and four
lists of equal length, at most 64 items each: `keys`; `values`, each new value as JSON text
(at most 64 KiB, `""` for a reset); `resets`, true to unset the key; and `was`, the value
the page read, as JSON text. The body is at most 256 KiB. Lists of unequal length are a 400
naming `keys`.

The shell reads the module's schema once, with its own `show --json`, and takes from that
read alone whether a key is a setting of the module, its type, its value now and whether it
is changed at the terminal; nothing the request carries decides them. It answers `{saved_at,
results}`: `saved_at` the shell's time in epoch seconds, and per change `{key, outcome,
problem, fix, mark, warning, lines, finished}`, where `outcome` is one of:

- `saved`, or `unchanged` (an unset of a key the file did not hold);
- `refused`: not a setting of the module, locked, changed at the terminal, or refused by
  `moxi config`, with its `problem` and `fix`;
- `moved`: the value now is not `was`; `problem` holds the value now, as JSON text;
- `busy`: the settings file stayed locked; nothing was written;
- `failed`: with `warning`, the value was written and applying the module's units failed
  (`lines` are theirs, `fix` is `moxi install <module>`); with `finished` false, the command
  was stopped before it ended and may or may not have written; else `problem` says why
  nothing was written. When the schema read itself fails, every change is `failed` with its
  error and nothing runs.

`mark` is when the change applies, as the read marks the key. A save may take a while: each
command may run 20 s, and the handler holds none of the shell's worker threads meanwhile.

**`POST /api/settings/set` and `/unset`** keep their shapes. Each reads the module's schema
first, as a save does, and refuses a key that is not a setting of the module or that is
changed at the terminal with a 422; a read that fails is a 422 with its text and fix, and
one that does not end a 409.

**`GET /api/gateway/state`** answers `{installed, switch, on, host, port, exposure,
reads_only, problem}` from the shell's gateway, with no new read of the tunnel: whether the
`gateway` extra is installed, `gateway.enabled` as the shell last read it, whether the
gateway serves, its address and `exposure` (`internet` or `tailnet`) when one is read,
`reads_only` unless the phone's confirmed acts are mounted, and, when the switch is on and
the gateway does not serve, `problem`, the first reason the shell has, or null.

**`POST /api/gateway/pair`** answers `until` beside `url`: when the pairing code ends, in
epoch seconds.
