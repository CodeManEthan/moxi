# Changelog

## Unreleased

- The default theme is now plain: near-white paper or near-black, one text colour, greys
  and one blue accent, with states told in words. The colours until now, calm-workbench
  in light mode and midnight-neon in dark, are the theme `midnight-neon`, unchanged;
  `moxi config set ui theme.name midnight-neon` brings them back.
- New setting `theme.name` (`plain` or `midnight-neon`, default `plain`). The shell
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
  theme's colours, whatever `theme.name` says.
- Changed for callers: `moxi_ui.document.body` takes the theme's name after the mode.

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
