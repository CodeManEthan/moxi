# moxi-loadout

Your own skills, agents and settings, placed for each harness: Claude Code, Codex and
OpenCode. You keep one folder of your items and one manifest, `loadout.toml`, that says
which item goes where; `moxi loadout install` puts each item where each harness looks,
and `moxi loadout status` says what has drifted.

1. It reads the manifest: a user section for every session, project sections by path
   (or by project key, with projects installed), and the items' own tables. Every key is
   checked before anything is used, and an unknown one names the closest known key.
2. It writes the manifest through the core's comment-keeping TOML editor, holding a lock
   in its own state directory, so no lock file lands in your folder. Your choices of what
   goes where live in the manifest, so a second machine cloned from the folder installs
   what you chose; `moxi config loadout` holds only facts about this machine: the
   folder's place, how many backups to keep and each harness's project folders.
3. `moxi loadout install` plans the whole manifest through the core's one placer and
   applies what is in scope (`--user`, `--project P`, or one item's verb). Skills and
   agent files go to each harness; a project's own copies are placed from where they
   are; Codex and OpenCode agent files are generated from the Markdown one, and a hand
   edit to a generated file is kept and reported. An item it can't resolve this run
   keeps its placements; only an item you drop or disable is removed.
4. It writes hooks, permission rules and MCP servers into each harness's own files,
   from the user section only (a project section that lists one is refused with a line):
   Claude's `settings.json`, Codex's `hooks.json` and a rules file of its own, OpenCode's
   `opencode.json` (permission rules and MCP servers; OpenCode takes no hooks, since it
   extends by plugins), and MCP servers through the `claude` and `codex` CLIs. Each file
   is read once and written once per run, backed up first. It removes only what it added:
   an entry you had first stays yours, and one you edited is left and reported until
   `--replace` takes it. No run has confirmed yet that Codex reads `hooks.json` and the
   rules file as loadout writes them, or how Codex asks you to trust a new hook; the
   OpenCode shapes follow its published schema, also unconfirmed by a run. `status`
   notes both.
5. The global instruction file, `global.md` in your folder, reaches every session in one
   of three modes: `off` (the default), `managed` (a line or a marked block added to your
   own file, which `moxi loadout global off` takes out exactly) or `if-absent` (placed
   only where a harness has none). `global show --diff` previews the next install.
6. No placement lands where git would commit it. In a project tree it writes a
   `.gitignore` of its own, which ignores itself, before it places anything there; where
   a `.gitignore` of yours would leave a target committed, the target is not placed and
   the line names the entry to add.
7. It takes over the module skills you said yes to at `moxi install`, and writes them into
   the manifest, so a second machine places them too; there it places each one as its
   module and takes it over, so the module still asks about the rest. A skill the
   manifest disables stays off when its module is installed again.
8. On install it writes an example manifest, every item commented out, into an empty
   folder, adds an OpenCode row to the core's harness table, and asks for the global mode
   when the manifest sets none and someone is at the keyboard.
9. `moxi uninstall loadout` takes out everything it placed or wrote: harness entries, the
   global part, placements, its ignore files and the OpenCode row. Module skills from your
   own yes go back to their module; ones placed only because the manifest listed them are
   removed. When something it wrote was edited since, it refuses and removes nothing,
   naming each with its fix.
10. `status`, `add`, `remove`, `enable`, `disable` and `list` show or change the manifest,
    each with `--json`, and `moxi doctor` shows loadout's rows: the manifest, entries
    not ok, the global file, hook programs, the projects link and Codex's switches.
