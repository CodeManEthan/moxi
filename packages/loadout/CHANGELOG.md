# Changelog

## 0.1.5 (2026-11-08)

- loadout needs moxi 0.1.8 or later.
- A leading `~` in `CLAUDE_CONFIG_DIR` is the home folder, for `settings.json`,
  `CLAUDE.md` and `.claude.json` alike. It was taken as a folder named `~` under the
  folder loadout ran in.
- Claude's folder is found by the core (`moxi.paths.claude_home`), and harness files are
  read and written by the core's writer (`moxi.harnessfile`), which other modules that
  write Claude's `settings.json` share. What loadout writes, its backups and its lines are
  unchanged.

## 0.1.4 (2026-11-08)

The first public release.

- loadout needs moxi 0.1.6 or later.
- The `ui` extra needs moxi-ui 0.2.3 or later, which has the selected colour.
- A switch that's on is filled with the theme's selected colour.
- Experimental: Codex's `hooks.json` and rules file, and everything loadout writes for
  OpenCode. No real run has confirmed them yet.
- The package metadata gains the README as its long description, keywords and
  classifiers.
- The README gains an install section and points to the example manifest.

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
