# Changelog

## 0.1.1 (2026-11-07)

- `moxi ui open` and the gateway's runtime-dir check accept a home given by `MOXI_HOME`,
  since the unit that home wrote runs with it. Module locators (`MOXI_UI_HOME` and the
  rest) are still refused. The fix line names the socket units the core gives this home.
- `moxi_ui.cli.FIRST_SOCKET_UNIT` is replaced by `first_socket_unit()`, since the name
  depends on the home.
- ui needs moxi 0.1.4 or later.
