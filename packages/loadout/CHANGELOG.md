# Changelog

## Unreleased

- The global instruction file gains a part per harness. `global.md` is what every agent
  gets; `global.claude.md`, `global.codex.md` and `global.opencode.md`, beside it, are one
  harness's own text, found by name with no manifest key. A part is in use while its
  file exists, even empty. Each harness reads the shared text and its own part, never
  another harness's.
- Managed mode: Claude's `CLAUDE.md` gets a second `@` line for its part; the Codex block
  holds the shared text, a blank line and the part's text, and its marker names both
  files. A block of an empty shared text loses its blank line, an update made once.
- Managed mode, OpenCode: loadout writes a marked block in OpenCode's own `AGENTS.md`, as
  for Codex, instead of an `instructions` entry in `opencode.json`. The first install
  after the upgrade takes the old entry out (an emptied `opencode.json` stays as `{}`).
  OpenCode reads Claude's `~/.claude/CLAUDE.md` only while its own `AGENTS.md` is absent,
  so with the block there OpenCode stops reading Claude's text: text for OpenCode goes in
  `global.md` or `global.opencode.md`. A run of OpenCode 1.19.5 confirmed where it reads
  its `AGENTS.md`, so `status`'s note on what no run has confirmed for OpenCode names only
  the `opencode.json` shapes, and shows only while loadout writes into `opencode.json`.
- OpenCode's home is resolved as OpenCode does: `OPENCODE_CONFIG_DIR` when set, else
  `XDG_CONFIG_HOME/opencode`, else `~/.config/opencode`, for its `AGENTS.md` and
  `opencode.json`. A page job whose `OPENCODE_CONFIG_DIR` differs from the terminal's is
  refused.
- If-absent mode: a harness with a part gets a generated file of the shared text and its
  part, under loadout's state folder, placed where its global file is absent; a harness
  with no part still gets a link to `global.md`. The generated file's first line names
  the files and a hash; one edited by hand reads `modified` and keeps its placement until
  `install --replace global:global@<harness>` backs it up and writes it again. Only a
  whole install writes the global file, so a run scoped with `--user` or `--project`
  refuses that `--replace` (exit 3) and leaves the file as it is, and the page's Replace
  on a global row runs the whole install.
- Harness global files that are one real file (one a link to another, or to another
  harness's part) are left as they are and read `blocked`, so no harness reads another's
  part; uninstall refuses while one is recorded.
- `global show` and its `--json` name the files each harness reads (`harness`, `parts`);
  the page's global view lists them under each file. The doctor's global row names each
  harness's state and parts, and warns on Codex's `AGENTS.override.md`, harness files
  that are one file, an OpenCode `instructions` entry naming the global text, and
  OpenCode reading Claude's file while loadout's block isn't in its `AGENTS.md`. A file
  named like a mistyped part is noted.
- The page's confirmed act is `POST /api/global-remove` (was `/api/global-off`), with no
  old route.
- loadout needs moxi 0.1.8 or later, whose terminal record holds `OPENCODE_CONFIG_DIR`.

## 0.1.4 (2026-11-08)

- `moxi loadout global off` is now `moxi loadout global remove`, with no alias.

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
