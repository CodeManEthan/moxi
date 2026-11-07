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
   manifest disables stays off when its module is installed again. Removing a module
   skill, or dropping one of its harnesses from the manifest, lasts only until the
   module's next `moxi install`, which places it again if you said yes at the module's
   question. A folder a module's own setup places at a path of its choosing (turns'
   Claude Code plugins, say) is not a module skill: loadout leaves it to that module.
8. On install it writes an example manifest, every item commented out, into an empty
   folder, adds an OpenCode row to the core's harness table, and asks for the global mode
   when the manifest sets none and someone is at the keyboard.
9. With repo installed (`moxi install loadout --with repo`) and the folder in a git work
   tree, install registers two git hook pieces there, so a commit or a merge that
   changes the folder runs `moxi loadout install` in that work tree, on whatever branch.
   The install runs with git's own variables taken out of its environment; one that
   fails prints the command to run and leaves a doctor record, which the next clean
   install clears. `moxi config set loadout reinstall_on_commit false` turns it off, and
   the next install removes the pieces. Without repo, run `moxi loadout install` after a
   commit.
10. `moxi uninstall loadout` takes out everything it placed or wrote: harness entries, the
    global part, placements, its ignore files and the OpenCode row. Module skills from
    your own yes go back to their module; ones placed only because the manifest listed
    them are removed. When something it wrote was edited since, it refuses and removes
    nothing, naming each with its fix. repo's own uninstall listener takes the git hook
    pieces out.
11. `status`, `add`, `remove`, `enable`, `disable` and `list` show or change the manifest,
    each with `--json`, and `moxi doctor` shows loadout's rows: the manifest, entries
    not ok, the global file, hook programs, the projects link, the git hook pieces and
    Codex's switches.

## The page

With the `ui` extra (`moxi-ui`, the browser UI shell), loadout has a page in the shell,
at `/m/loadout/`, and a block on the shell's home that counts the items that aren't ok
and links to the page. An item counts once, however many of its harnesses aren't ok.

- **Items.** Each section of the manifest, its own items first and then the module skills
  it took over, with one cell per harness: the item's state, why, and a switch that runs
  `moxi loadout enable` or `disable` for that harness. Add and Remove run `moxi loadout
  add` and `remove`; the add form also offers each project that has no section yet. Rows
  that belong to no listed item show under "Other rows". The notes `status` gives show
  above the items.
- **Install.** Runs `moxi loadout install` for every section, the user section or one
  project section.
- **Replace.** A row that `moxi loadout install --replace` would fix offers Replace,
  which runs that command for the row once you confirm on the shell's sheet. Those are
  the rows where something else stands in the way and the ones edited by hand since
  loadout wrote them. A `would-commit` row whose path holds a copy the project's git
  tracks is one: the copy is moved aside, so git shows it deleted, and the row reads
  `would-commit` until you commit that.
- **The global instruction file.** The mode, and what the next install does to each
  global file, with its diff. Managed and Only where absent run `moxi loadout global
  mode`. Turn off runs `moxi loadout global off` once you confirm on the sheet.

Each change runs the same `moxi loadout` command you would run in a terminal, in a
process of its own, and the page shows the command and how it ended under "Last change".
The page runs one change at a time: every control is off while one runs. Closing or
reloading the page doesn't stop a change. The tab keeps the change it follows, so after a
reload, or another page and back, the page follows it again and keeps every control off
until it ends; a new tab doesn't, and `moxi loadout status` shows where things stand. When the shell runs as a service,
a change still running when the service stops, by `moxi ui stop` or because nothing has
used the shell for a while, stops with it; run it again.

A change from the page refuses before it writes anything when the shell's
`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `XDG_CONFIG_HOME` or `HOME` puts a home somewhere
other than your last terminal run did, or when your last terminal run found git and the
shell's `PATH` doesn't: without git, nothing could check what git would commit. The
line names the variable. Set it where the shell gets it at every login (a file under
`~/.config/environment.d/` on Linux, your LaunchAgent environment on macOS, your user
environment variables on Windows), then run `moxi ui stop`, or run the command in a
terminal. Until a terminal run has recorded the homes (`moxi install loadout` does, and
so does every `moxi loadout` command that installs), every change from the page refuses
and says so. `moxi loadout status` and the home's block show the same comparison as a
note, which changes neither the exit code nor the count; in a terminal, the note says
what the page does.

MCP servers are added through the `claude` and `codex` CLIs, so the page needs them on
the shell's `PATH`. Without one, a change from the page does what the terminal does
without it: the servers that need it stand, each with a note naming the fix, and the
rest is written.
