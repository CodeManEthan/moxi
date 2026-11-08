# Changelog

## 0.2.3 (2026-11-08)

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

## 0.1.1 (2026-11-07)

- The terminal check moves from the core into ui as its interface 2, `moxi_ui.terminal`
  through the new API module `moxi_ui.api`.
- ui needs moxi 0.1.4 or later, the core that writes the terminal record.
