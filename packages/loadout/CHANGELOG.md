# Changelog

## Unreleased

- The page is redrawn around what needs you. Placed comes first: each problem is an
  item and one word (`changed`, `in the way`, `not placed`, `out of date`, `would be
  committed`, `can't be read`), with its section, harnesses and why, and the fix with
  Install or Replace; Install all runs a whole install, and the counts say what each
  harness gets. Then the folder, with the files git hasn't committed, and the items in a
  grid with a column per harness, a section past twelve items folded by kind. An item,
  a kind or the global file opens in place, at an address of its own, with Turn off and
  Turn on, Turn on for every harness, Remove, and the global mode. Not listed shows the
  skills and agents in the folder that no section lists, each with Add. With projects,
  `/m/loadout/project/<key>` shows only what a project's sessions load. While the shell's
  environment differs from the terminal's, every act shows as its command, with Copy.
  The page reads again when shown, after each change and every minute; `Read again`, the
  `Other rows` list and the notes above the items are gone.
- Replace opens in place, not on the shell's sheet: it reads what the Replace would do
  (`/api/replace-preview`: what is moved aside or kept and where, the diff, what else the
  run changes, what the diff leaves out) and runs only once you press Replace. A line
  under each act says how it ended, Show opens its lines and rows, and each copy the run
  kept is named with the command that puts it back.
- No write of loadout's destroys bytes it can't show it wrote. What a write goes over
  that isn't loadout's (an edited or header-less generated agent file under `--replace`,
  an entry you edited or loadout didn't write, Codex's rules file, an occupant, an edited
  copy on a removal) is kept under the core's `displaced/` folder first, until you delete
  it. The run names each copy (`kept a copy of <target> at <copy>`; `--json` `kept`, with
  `back`, the command that puts it back), and `install --help` says how. A generated
  agent file without loadout's header now reads `modified`: install leaves it, and
  uninstall refuses while it is there. A generated file that is a link is refused.
- New reads for the page: `/api/view`, the rows `status` gives with what the page draws
  (how each item is placed and what fixes it, the sections and why one is refused, the
  items' notes, the global file); `/api/folder`, the folder's uncommitted and unlisted
  files and whether a commit installs it again; `/api/projects` gains `cause`.
  `/api/replace` and `/api/global-remove` are plain acts that take a `job` id from the
  page, in place of the shell's confirmation; `/api/status` and `/api/job` gain `kept`.
  No route is the gateway's.
- Every setting carries page fields, which `moxi config loadout show --json` prints
  and the settings page draws: a plain label, a line where the label can't carry it,
  and the unit of the backups kept. loadout needs moxi 0.1.9 or later.
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
