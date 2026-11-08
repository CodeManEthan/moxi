# Changelog

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
