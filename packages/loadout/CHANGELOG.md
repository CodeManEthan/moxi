# Changelog

## 0.1.3 (2026-11-07)

- A folder a module's own setup places at a path of its choosing (turns' Claude Code
  plugins) is left to that module: loadout's install listener, install and setup no
  longer take it over. New `moxi_loadout.adoption.at_its_folder`.

## 0.1.2 (2026-11-07)

- The `ui` extra needs moxi-ui 0.2, where the page-server base now lives as
  `moxi_ui.loopback`.

## 0.1.1 (2026-11-07)

- A page job asks ui's terminal check (interface 2) before it writes anything, and
  refuses with the check's line when the page's folders differ from the user's
  terminal's. loadout no longer records the harness homes itself.
- loadout needs moxi 0.1.4 or later.
