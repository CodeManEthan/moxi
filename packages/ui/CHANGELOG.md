# Changelog

## Unreleased

- Past its 20 sessions, the shell's sign-in store now evicts the least recently used
  session, not the oldest made, and never one that holds an open push stream or proxied
  request. Twenty sign-ins (CLI tickets, a phone) no longer sign out a desktop tab in use
  and end its live updates. When every session holds a stream, a sign-in still succeeds
  and the store keeps more than 20 until a stream closes. A session's last use is the
  last request it made, or the end of a stream it held. The gateway's phone sessions (4
  per device, 16 in all) are evicted least recently used first too.

## 0.2.5 (2026-11-22)

- A page may announce `band_unless`, a list of module names: while any of them is
  installed, the page has no entry in the band of pages, and its address still opens it.
  `/m/ui/api/modules` and `ComponentInfo` carry it on a page (`[]` when it names none) and
  null on any other component. A value that is not a list of module names reads as none.
- `<moxi-ui-rail>` sizes its small and vertical forms with custom element states, so it
  draws as designed from Chrome 125, Firefox 126 and Safari 17.4. Below that a mark no
  longer throws: an engine with no custom states, or one that takes only names starting
  with `--` (Chrome 123 and 124), draws each mark plainly, at a full cell's size, where
  before the whole rail failed to draw.

## 0.2.4 (2026-11-20)

- On the Modules view the Page servers block now sits last, under the Installed list, and
  its list is mostly drawn with the rest of the page. When the list arrives late it no
  longer pushes the blocks and links already on the page down by a few hundred pixels, so
  a click that lands on a link just as the list arrives opens what it pressed.
- A page's push stream closes as the page leaves. In Firefox, a reload or a navigation
  within about 50 ms of the view's topics changing could leave the shell holding a push
  stream no page read, beside the new page's own, for 30 s and more. Each such stream
  took one of the shell's 16 stream slots, and enough of them would make the shell answer
  `busy` and stop live updates. Nothing showed on screen.
- On Windows a process sample counts open handles as `handles`, and has no `fds`: in
  `/m/ui/api/servers`, a page server's `sample` and the shell's own carry `handles` there
  in place of `fds`, and `moxi ui doctor` shows `N open handles`. A handle count also
  holds events, threads and registry keys, so it is never compared with a budget's `fds`:
  the doctor no longer warns `232 open fds over its 64` for the shell on Windows. Other
  systems keep `fds` and its warning as before.
- On a wide page a Settings row's control stays on its label's first line in any sans
  face: the label and key take the width left beside the control and wrap there, the key
  on a line of its own when it doesn't fit. A row laid out for Adwaita Sans's widths no
  longer drops its control under the label in a wider face such as DejaVu Sans. At a
  phone's width a control still drops under a label too long to share its line.
- The test kit reads back what a page copied in any engine: `moxi_ui.testing.browser`'s
  `ready_copying(context)` readies a browser context and `copied(page)` returns the text.
  On Chromium it grants `clipboard-read` and `clipboard-write` and reads the clipboard.
  Firefox refuses a `clipboard-read` grant and WebKit a `clipboard-write` one, so there it
  returns the text the page last passed to `navigator.clipboard.writeText`.
- A check's name reaches the phone with each path that starts a word, or follows `:` or
  `=`, with `/` or `~` cut to its last folder name. The gateway sends a kept row's `name`
  (`/m/ui/api/checks` and the topic `ui.checks`) that way, so Home's Health row on the
  phone reads `repo · hooks proj-b` where a repository's row is named by its root, and
  `turns · start list: run:ctx` where a skipped entry names a program. A path runs to the
  next whitespace, and a path in another form is sent as it is. It comes with a new mark
  a page's `Out` may carry, `scrub`, beside `path` and `redact`.
- The test kit's waits for what must happen are long, so a busy machine does not fail
  them: `WAIT_MS` is 20000 (it was 5000), every kit context's default timeout, and
  `WAIT_S` is the same bound in seconds for a helper that takes seconds. While the plugin
  is loaded, Playwright's sync `expect` takes `WAIT_MS` too (its own default is 5 s), in
  any test that imports `playwright.sync_api`, the kit's fixtures or not. The test
  shell's request reads (`TestShell.session`, `call`, `wait_server`, `Session`) wait up
  to `READ_S`, 30 s, by default (they waited 10 s).
- The Settings page, at `/m/ui/settings`, replaces the settings view: a list of the
  modules with settings (the core, ui, then A to Z) with each one's count of changed
  settings, and one module's settings at a time. Each setting shows by its label, with a
  control by its type, its line, and `default <value> · Reset` when changed; settings the
  schema marks `common` come first and the rest behind `More`. Nothing is written until
  Save, which writes the shown module's changes, says what each answer was, asks before a
  change that can hurt, and keeps a value changed since the page read it. Find searches
  every module's settings, `N changed` shows every changed one, and leaving with changes
  not saved asks first. Addresses: `settings`, `settings/<module>`,
  `settings/<module>/more`, `settings/ui/pair` and `settings/changed`. `docs/settings.md`
  says how a module's page fields show.
- The modules view's Gateway section moved to Settings › ui, as its Phone group: Phone
  access and where phones reach it, Phone port, the paired phones with Revoke, Pair a phone
  and Activity. The group shows only while the gateway extra is installed.
- On the Settings page the top line's own `Settings` is hidden, and Tab skips it. On the
  phone, a Settings address says settings are changed at the desk, with a link to Home.
- ui's settings carry page fields: `Port`, `Light or dark` and `Look` show first, `Idle
  shutdown` in seconds, and `Phone access` (`gateway.enabled`) is changed at the terminal,
  `moxi ui gateway enable` and `disable`. ui needs moxi 0.1.9 or later.
- New route `POST /m/ui/api/settings/save`: one module's changes, as `keys`, `values` and
  `was` (JSON text) and `resets`, each change its own `moxi config set --json` or `unset
  --json` in the order sent. The shell reads the module's schema itself first and decides
  from that read alone what each key is; a key whose value is not its `was` is `moved` and
  not written. Each change answers `saved`, `unchanged`, `refused`, `moved`, `busy` or
  `failed`, with `finished` false for a command stopped before it ended.
- `GET /m/ui/api/settings` answers `listed`, the modules with settings, from the module
  listing alone, and a `module` not in it gets no section and runs no command. A section
  gains `file` (its config file, the home folder written `~`) and `count`; each key gains
  its page fields, `json` and `default_json` (its value and default as JSON text) and
  `changed`.
- `POST /m/ui/api/settings/set` and `/unset` read the module's schema first and refuse,
  with a 422, a key that is not its setting or that is changed at the terminal, so no page
  turns the gateway or the connector on or off. Their command may now run 20 s, past the
  settings file lock's 10 s wait, so a held lock answers as busy.
- A `table` setting given as a string is passed to `moxi config set` as its TOML text, so
  a table can be saved from its field.
- New route `GET /m/ui/api/gateway/state`: whether the gateway extra is installed, its
  switch, whether it serves, its address and who can reach it, whether phones read only,
  and why it doesn't serve. `POST /m/ui/api/gateway/pair` also answers `until`, when the
  code ends.
- `client.js` gains `holdLeave(ask)`, which holds the shell's moves off a page while it
  has changes not saved and asks it first, and `setAddress(module, route)`, which writes
  the page's own address in place. `navigate()` asks a page that holds. The README's "A
  page that holds changes" has both.
- A page handler may raise `BadRequest(key, reason)` from `moxi_ui.loopback.api` for a 400
  about a body its `Field`s can't refuse alone.
- New in `moxi_ui.settings`: `read_schema`, `find_key`, `save_one`, `is_changed`,
  `is_drawn`, `file_of`, `SchemaRead`, `SAVE_SHAPE` and `WRITE_TIMEOUT_S`.
- The contract check (`ui_component_check`) waits up to `WAIT_MS` (20 s) for each of
  `tokens` to reach a colour of the placed component, or up to `token_ms`, so a part
  drawn from data that arrives after the first render (a pushed topic's first value)
  counts. Before, it probed once two frames after the first render, and a slow push
  failed the check.
- The shell reads the modules list once more when its first read at load gets no
  answer. On a phone in Firefox, the gateway's worker being replaced dropped that read,
  and the page said `No module named ui` until the push stream sent the list.
- `ui_contexts(color_scheme=...)` also sets the scheme on its signed-in page with
  `page.emulate_media`, so the page shows it in Firefox too. Firefox kept a context's
  scheme from every shell document, each sent with `Cross-Origin-Opener-Policy:
  same-origin`, and a test in dark mode saw light colours.
- `moxi_ui.testing` makes the `--ui-traces` directory when the run starts, a relative one
  taken from where pytest started, so a failed browser test keeps its trace there even
  when the directory is outside every test's tmp (a directory in the repository). The
  teardown that keeps a trace no longer makes that directory, which the test kit's fence
  refused: every failing browser test also errored and kept no trace.
- `moxi_ui.testing.phone` gains the two helpers a module's phone test needs:
  `gateway_shell(home, site=(), *, port=None, enabled=True, address=True, start=True,
  **options)`, a gateway-mode test shell with its port set, its tunnel address written and
  its hold waited for, and `pair_page(context, shell, *, passkey=False)`, which pairs a
  browser context through a `PhoneBridge` and gives its page on the signed-in shell
  document. `free_port`, `hold_port` and `wait_hold` come with them.
- Keyboard order follows what is seen. Home puts its rows in the document in the order
  shown, so Tab reaches a risen row before the rows under it; a row that moves takes its
  component along, which gets one `disconnectedCallback` and one `connectedCallback` and
  is not placed again, and an element inside a row that held the focus keeps it. A row
  component must hold up under that move and not report `null` on the way
  (`docs/home.md`, "Order").
- With the band of pages open, Tab goes from the page-name button to the band's first
  name and from its last name to `Settings`; from `Settings`, or from the last name where
  `Settings` is hidden, the band closes and Tab goes on into the page. Shift+Tab walks
  back the same way. With the band closed Tab is the browser's own.
- Health's `Check again` hands the focus to `Checking…` while the run lasts and takes it
  back when the run ends.
- Home moves its column aside for a main piece's side panel. The piece dispatches a
  bubbling, composed `moxi-home-side` event with `detail: {width}`, the width in px of a
  panel it draws fixed to the window's right edge, and from 1100 px of window Home puts
  its column's centre at the centre of the window less that width. A width of `0`, or
  the piece leaving the spot, puts the column back; under 1100 px the column stays and
  the panel lies over the page. `docs/home.md` has the contract.
- New environment variable `MOXI_UI_LOCKED_SETTINGS`: a list of full setting keys
  (`<module>.<key>`, separated by commas or spaces) that the shell's pages may not change
  for that run. An entry locks its key and every key under it, and a write to a key
  above it (the table that holds a locked entry) is refused as well. The settings routes answer
  a write to one with a 422, the settings read marks each key and switch-table entry with
  `locked`, and the form shows a locked field off with one line. Unset or empty, nothing
  is locked.
- Changed for callers: `moxi_ui.settings.change`, `read_section` and `descriptor` take
  `locked`, the locked entries; a field descriptor and a switch-table entry carry `locked`.
- The default theme is now plain: near-white paper or near-black, one text colour, greys
  and one blue accent, with states told in words. The colours until now, calm-workbench
  in light mode and midnight-neon in dark, are the theme `midnight-neon`, unchanged;
  `moxi config set ui theme.palette midnight-neon` brings them back.
- New setting `theme.palette` (`plain` or `midnight-neon`, default `plain`). The shell
  document's root carries it as `data-theme-name`, beside `data-theme`, at the desk and on
  the phone. `theme.mode` still picks light, dark or the browser's scheme within the theme.
- New colour tokens: `on-accent`, text on the accent's fill, and `danger`, a launch
  setting that can do harm.
- `--moxi-font-sans` and `--moxi-font-mono` lead with Adwaita Sans and Adwaita Mono, the
  earlier stacks after them; no font file ships. `--moxi-font-size` is 15px and
  `--moxi-font-size-sm` 13px.
- `tokens.json` names its themes in `$themes`, each a light set and a dark set of
  `$sets`, the first the default. The build refuses a theme without one of each, a set in
  two themes and a set in none. `theme.css` holds the default theme on `:root` and a
  block of colours per other theme under `:root[data-theme-name="<name>"]`.
- `setTheme(mode, name?)` in `client.js` takes the theme's name as a second, optional
  argument.
- The pair, sign-in and offline pages and the installed app's colour take the default
  theme's colours, whatever `theme.palette` says.
- Changed for callers: `moxi_ui.document.body` takes the theme's name after the mode.
- The shell's bar and menu are now one top line: Moxi, the current page's name with a
  caret, and Settings alone at the right. Pressing the name opens the band, a layer over
  the page that lists every page by name; a pick, Escape or a click outside closes it.
  Names that do not fit one row wrap onto further rows, and at 720 px and under the band
  is one name per line.
- A module's `page` component may name its place in the band: `group` (`daily` or
  `setup`) and `order` (0 to 999, lower first; then by title). A page that names neither
  is in `setup` at 500. The shell's own Home is first and its Modules last.
- The modules view moved to `/m/ui/modules`. It and the settings view carry no title of
  their own; the top line names the page. The document's title is `Moxi` while the panel
  holds a message and no page.
- `ComponentInfo` in `client.js` carries `home_as`, `group` and `order`.
- `/m/ui/` is now Home: one main spot and a row per module with something to report. A
  component takes a seat with `home_as` (`"main"` or `"row"`) and `order`; a row component
  reports its line with a `moxi-home-row` event and Home draws it, puts the rows that need
  you first and opens a row's component in place. `docs/home.md` has the contract and the
  `needs` and `order` scales. With no main piece installed the spot says `Nothing needs
  you.`
- On the phone every page says `This phone reads only here.`, first under the top line.
- A block with `home: True` and no `home_as` is on the modules view, at `/m/ui/modules`,
  and no longer at `/m/ui/`.
- New component `health`, the shell's own row on Home: whether the doctor's checks pass,
  from the run the shell keeps. Opened, it lists the checks that fail or warn with their
  fixes, and `Check again` runs the doctor.
- The kept doctor run: `GET /m/ui/api/checks` answers with it and starts a new run when
  it is old, `POST /m/ui/api/checks/run` starts one now, and the topic `ui.checks`
  carries it. The shell keeps it in `checks.json` in its state directory, a declared
  store that `moxi uninstall ui --purge` removes.
- New element `<moxi-ui-row>`, defined by `client.js` beside the form: the two-line row
  of a list. Line one is `name`, `where` and a `fact` at the right end; line two is
  `lead`, `lead-more`, `time` with `rest`, and `tail`, a middle dot between each two, then
  the slot `acts`; line three is `note`. An unset attribute draws nothing, and a row with
  a name alone is one line. `open` shows the default slot's content under the lines. A
  row with `toggles` shows a pointer and sends `moxi-row-toggle` for a click on its lines
  outside `acts`; the page sets `open`. Its line one is the tab stop, a button whose
  `aria-expanded` follows `open`, and Enter or Space there sends the event too; Space
  does not scroll the page. A click that ends a selection of text does not toggle, so a
  name can be copied. When line one is too narrow the where and the name are cut before
  the fact. `docs/row.md` has the list. `client.d.ts` gains `RowElement`.
- `<moxi-ui-row>` gains the slot `acts-one`, at line one's right end after the fact, so a
  row with nothing on line two stays one line with its act beside it. The slot sits
  beside line one's button, not inside it, and is never cut; a click or key in it sends
  no `moxi-row-toggle`, as for `acts`. A page that leaves it empty draws as before. A page
  that fills it needs a ui with this slot.
- New element `<moxi-ui-rail>`, defined by `client.js`: a line of step markers, each
  `<moxi-ui-mark>` child one cell. A mark's `state` is one of eight shapes (`done`,
  `none`, `yours`, `failed`, `owed`, `waiting`, `todo`, `child`), with a `word` under it,
  a `count`, a `label` for its accessible name and an opaque `key`; the flags `settled`,
  `here`, `open`, `quiet` and `wide`. The line is solid from the first marker through
  each settled one up to and including the first not settled, and dashed after. A click,
  Enter or Space on a marker sends `moxi-rail-mark` from the rail with `{key}`; the page
  sets `open`. `size="small"` draws a picture with no buttons, and `vertical` stacks the
  marks with their labels and words. Cells take `--moxi-rail-cell` (56 px) and
  `--moxi-rail-label` (132 px). `docs/rail.md` has the list. `client.d.ts` gains
  `RailElement`, `MarkElement` and `RailMark`.
- New colour tokens `state-done`, `state-yours`, `state-yours-soft` and `state-failed`:
  a green, an amber, an amber tint and a red in every colour set, `plain` included, for
  a step's state on the rail. In calm-workbench and midnight-neon they are those sets'
  `ok`, `attention`, `attention-soft` and `error`; in `plain`, where those are the text
  colour and a grey, they are colours of their own. The two `plain` sets' descriptions
  say so. Each is held to the text floor, 4.5:1, on every background.

## 0.2.3 (2026-11-08)

- The shell's console script `moxi-ui-serve` is now `moxi-ui-shell`. Run `moxi install
  ui` so the install plans the shell's unit again under the new script.
- `moxi ui gateway on` and `off` are now `moxi ui gateway enable` and `disable`, with no
  alias.
- Public names renamed for the naming rules (callers passing the old keyword or reading
  the old name must change): `moxi_ui.api.check`: `page` is now `for_page`.

The first public release.

- The README gains a section for using the shell: install, the verbs, how the shell
  runs, its port and settings, and which modules show pages. Its gateway section says
  the gateway's port can be taken while Moxi isn't running, that macOS and Windows have
  no provider check, and what to do on a machine others log in to. The README's links
  work on PyPI.
- The package metadata gains keywords and classifiers.
- The theme is two named colour sets: calm-workbench, a warm paper grey, in light mode
  and midnight-neon, a near-black violet, in dark mode. `theme.mode` switches between
  them as before, and its default, `system`, follows the browser. The old blue-grey
  colours are gone.
- New colour tokens: `live-soft`, `launching`, `launching-soft`, `selected`,
  `selected-soft`, `attention`, `attention-soft` and `series-1` to `series-6`.
- `tokens.json` names its sets in `$sets`, and each colour token holds one value per
  set; the build refuses a file that doesn't, and set values on any token but a colour.
  A test holds each set to contrast floors (4.5:1 for text, 3:1 for control edges and
  chart series).
- The pair, sign-in and offline pages take their colours from `tokens.json` when built,
  and follow the browser's light or dark scheme.
- The installed app's colour is calm-workbench's background.
- The shell menu marks the current page with the selected colours.
- A page's sources hold no colour of their own: the web build refuses a hex colour, a
  colour function or a named colour in a module's page sources, unless a comment on its
  line holding `colour-fixed` says why. The QR code and the confirm dialog's scrim keep
  theirs.

## 0.2.2 (2026-11-08)

- The width check no longer counts an element held by a scroll container (`overflow-x` of
  `auto` or `scroll`) that itself ends inside the viewport, as its docstring always said:
  a table that scrolls sideways in its own wrapper passes. An element cut off by
  `overflow: hidden` or `clip` (inside such a container too), held only by a container
  that passes the edge itself, or placed `fixed` or `absolute` outside the container's
  reach is still named. Every module
  that runs the check gets the looser rule.

## 0.2.1 (2026-11-07)

- `moxi ui open` and the gateway's runtime-dir check accept a home given by `MOXI_HOME`,
  since the unit that home wrote runs with it. Module locators (`MOXI_UI_HOME` and the
  rest) are still refused. The fix line names the socket units the core gives this home.
- `moxi_ui.cli.FIRST_SOCKET_UNIT` is replaced by `first_socket_unit()`, since the name
  depends on the home.
- ui needs moxi 0.1.6 or later.

## 0.2.0 (2026-11-07)

- The page-server base moves into ui as `moxi_ui.loopback`, reached through ui's API
  module. ui no longer needs moxi-loopback; it requires starlette, uvicorn and h11
  itself.
- ui offers interface 3: interface 2 plus `api.loopback`, the page-server base, which
  every page module now loads with `moxi.modules.load("ui", interface=3)`.

## 0.1.1 (2026-11-07)

- The terminal check moves from the core into ui as its interface 2, `moxi_ui.terminal`
  through the new API module `moxi_ui.api`.
- ui needs moxi 0.1.4 or later, the core that writes the terminal record.
