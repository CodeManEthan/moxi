# Changelog

## 0.2.3 (2026-11-08)

- The open turn is shaded with the theme's selected colour.
- The `ui` extra needs moxi-ui 0.2.3 or later, which has that colour.

## 0.2.2 (2026-11-07)

- Setup places a copy of the Claude marketplace in turns' data dir, writes
  `endpoint.json` there, and installs the plugins from it. It never writes into the
  package. An older user-scope install made from the package's folder is moved to the
  copy.
- Launches and the doctor's plugin rows use the copy when it is current, else the
  package's folders. A new doctor row, `claude placed copy`, compares it with the
  package.
- New `TEARDOWN`: `moxi uninstall turns` removes the marketplace and the copy.
- Setup without Claude Code on PATH skips its plugin with one line and succeeds.
- A fresh Codex thread is opened with one injected developer item instead of a priming
  turn. No model call is spent, and the window opens on an empty transcript.
- A Codex without `thread/inject_items` (before 0.153.0) is refused with one line naming
  the version that has it.
- A Codex thread reloaded before its first turn is treated as a start: its Ready reads
  `startup`, and the first turn's SessionStart `resume` reaches the listeners as
  `startup`. Each resumed load makes one more `thread/turns/list` call.
- `moxi-turns-codex-host` stopped once its first load is accepted, while the launch
  is still finishing, ends the session and exits 0. It used to exit 1, saying the
  launch was stopped before the session was ready.
- turns needs moxi 0.1.6 or later.

## 0.2.1 (2026-11-07)

- The `ui` extra needs moxi-ui 0.2, where the page-server base now lives as
  `moxi_ui.loopback`.
