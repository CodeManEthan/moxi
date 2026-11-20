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
   `--replace` takes it. **Experimental:** Codex's `hooks.json` and rules file, and
   everything loadout writes for OpenCode. No run has confirmed yet that Codex reads
   `hooks.json` and the rules file as loadout writes them, or how Codex asks you to trust
   a new hook; the OpenCode shapes follow its published schema, also unconfirmed by a
   run. `status` notes both.
5. The global instruction file, `global.md` in your folder, reaches every session in one
   of three modes: `off` (the default), `managed` (a line or a marked block added to your
   own file, which `moxi loadout global remove` takes out exactly) or `if-absent` (placed
   only where a harness has none). Text for one harness goes beside it in
   `global.claude.md`, `global.codex.md` or `global.opencode.md`: each harness reads
   `global.md` and its own part, never another's. Claude gets an `@` line per file; Codex
   and OpenCode get one marked block in their `AGENTS.md`, rewritten by each install; in
   `if-absent` a harness with a part gets a generated file of both. OpenCode's `AGENTS.md`
   is under `OPENCODE_CONFIG_DIR` when that is set, else `XDG_CONFIG_HOME/opencode` or
   `~/.config/opencode`; while it exists OpenCode doesn't read Claude's
   `~/.claude/CLAUDE.md`, so text for OpenCode goes in `global.md` or its part. Use absolute paths for
   `@` imports in the global text, since a relative one resolves against the file Claude
   reads. `global show --diff` previews the next install and names each harness's parts.
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

## Install

Linux, Python 3.12 or later, and [uv](https://docs.astral.sh/uv/), with the harnesses you
place for: Claude Code, Codex or OpenCode, each signed in with its own account. MCP
servers are added through the `claude` and `codex` commands, so those need to be on
`PATH`, and `git` lets loadout check that nothing it places would be committed. loadout
also has macOS and Windows code, which the repository's CI tests without gating and
nobody runs yet: treat both as untested.

```sh
uv tool install moxi
moxi install loadout
```

The first line installs `moxi`, Moxi's core, which provides the `moxi` command; the
second adds loadout beside it, runs its setup and offers its optional extras: `projects`
(project sections by key), `repo` (reinstall on commit) and `ui` (the page). The
repository's README, at [CodeManEthan/moxi](https://github.com/CodeManEthan/moxi), covers
the `moxi` command and lists the other modules.

The loadout folder is the `source` setting (`moxi config set loadout source DIR`); empty,
it is a folder in loadout's data directory (`moxi paths loadout`). Setup writes an example
manifest into an empty folder, every item commented out; the same file is
[`example/loadout.toml`](https://github.com/CodeManEthan/moxi/blob/moxi-loadout-v0.1.4/packages/loadout/src/moxi_loadout/example/loadout.toml)
in the package. Take the `#` off an item, put the item's files in the folder
(`skills/<name>/SKILL.md`, `agents/<name>.md`, `global.md`), then run
`moxi loadout install`.

## What a write keeps

No write of loadout's destroys bytes it can't show it wrote. Before an install, a
`--replace`, a removal, `enable`, `disable`, `add` or a global verb goes over a file, or an
entry in a file it shares with you, whose bytes aren't what loadout last wrote there, it
keeps those bytes in the core's `displaced/` folder, in Moxi's data folder, as
`<stamp>/<harness>/<file name>` (mode 0600). Nothing in loadout or Moxi deletes there: a
copy stays until you delete it.

- **A generated agent file** (the Codex and OpenCode forms of an agent) that was edited by
  hand, or has no loadout header, reads `modified`. Every install leaves it; only a
  `--replace` naming it writes it, after keeping it. A generated file that is a link is
  refused and left as it is.
- **A placement.** What stands where loadout places something, an edited copy, or a file
  saved over loadout's link is moved aside by the core's placer, which says so on its own
  line (`moved <it> to <copy>`).
- **An entry in a harness's file** (Claude Code's `settings.json` and `.claude.json`,
  Codex's `hooks.json` and `config.toml`, OpenCode's `opencode.json`, a managed global
  file), when you edited it or loadout didn't write it (one you had before loadout
  included): the whole file is kept before the write, and before the `claude` or `codex`
  command runs for an MCP server. Codex's rules file is kept the same way. The rest of the
  file is carried into the new one value for value; what a write can change there is its
  layout and spelling, and a duplicate key's earlier value. Those exact bytes are in the
  rolling backup every rewrite of a harness file takes, `backups/<harness>/` in loadout's
  data folder: it is what keeps them, and it is pruned to the newest `backups.keep` of
  each file.

`loadout.toml` is outside the rule: each verb that writes it changes the one value it
names and keeps every other byte, comments and order included. So are the three writes
loadout hands to their owners, each guarded by that owner: repo's git hook pieces, the
OpenCode row in the core's harness table, and the core's `skills.harnesses` key.

A run names every copy it kept, `kept a copy of <target> at <copy>` (with `--json`,
`kept`, each with `how` and `back`), and how to put it back:

- a copy loadout wrote before writing the target: `cp <copy> <target>` puts the file back
  as it was. An agent file then reads `modified` again and loadout leaves it; a harness
  file comes back whole, so later changes to it go too.
- moved aside with loadout's placed there: turn the item off there first (`moxi loadout
  disable <id> --user` or `--project=<section>`), then `mv <copy> <target>`.
- moved aside on a removal: `mv <copy> <target>`.

On Windows the commands are `copy` and `move`. `moxi loadout install --help` says the
same.

## The page

With the `ui` extra (`moxi-ui`, the browser UI shell), loadout has a page in the shell,
at `/m/loadout/`, and a block on the shell's home that counts the items that
aren't ok and links to the page. An item counts once, however many of its harnesses
aren't ok. The page draws no colour of warning: what needs you rises to the top and is
set heavier.

- **Placed** says what needs you, one line per problem: the item and one word for its
  state (`changed`, `in the way`, `not placed`, `out of date`, `would be committed`,
  `can't be read`), its section, harnesses and why, then the fix with its act, Install
  or Replace. A section that can't be placed (named by a project key without projects) is
  one problem, with the path to name it by when loadout recorded one. Install all runs a
  whole install. The counts say how many items each harness gets and how many are
  placed.
- **Folder** names your loadout folder and, in a git work tree, the files git hasn't
  committed; with repo, that a commit places them again. Its settings are a link away.
- **The items**: Your items with the global file, a group per module whose skills you
  took over, then each project section, with a column per harness. A section past twelve
  items folds by kind. Open an item for where each harness has it and why, its notes,
  Turn off or Turn on per harness (Turn on for every harness when it is off through
  `@*`), Replace where something stands in the way, and Remove from the section, which
  leaves its files in the folder. Open the global file for its mode, each harness's file
  and what the next install changes there, with the diff, and Turn off.
- **Replace** opens in place and says what it would do before it does it: what it moves
  aside or keeps, and where, the diff between what is there and what loadout writes, what
  else the run changes, and what the diff leaves out. Every Replace keeps what it
  replaces (see "What a write keeps").
- **Not listed**: the skills and agents in your folder that no section lists, each with
  Add, which lists it in a section and places it at once. Add something by its name
  instead, always there, takes a kind, a name and a section: a hook, a permission rule,
  an MCP server, or an item another section lists already.
- With projects installed, `/m/loadout/project/<key>` shows only what that project's
  sessions load: its own section and your items, with a menu of every project and a link
  to the project's page.

With projects' page in the shell too, a project opened there has a `Loadout` row naming
the items its own section lists, in the manifest's order and with each kind in this
page's words (`skill release-checklist · permission rule git-push`; past eight, the first
seven and how many more), and a `Loadout` link to
`/m/loadout/project/<key>`. A project's sections are the one named by its key and the one
whose path is its folder itself, since loadout places both there: the row names the items
of each, the key section's first, and an item both list once. An item switched off for
every harness there reads `off` after its name, as its cells do on this page. (With both
sections, that address shows the key section alone.) A project loadout lists nothing for has no row. The row comes from loadout's `project-facts`
component, which reads only `/api/list`, from the manifest alone: when the Projects page
places it, every minute while that page is shown, and once when it is shown again. While
the manifest can't be read, the row is left out.

Each change runs the same `moxi loadout` command you would run in a terminal, in a
process of its own, and a line under the act then says the command and how it ended,
with Show for its lines and rows, and, open, each copy it kept with the command that
puts it back. The page runs one change at a time: every control that starts a change is
off while one runs. Replace and Add still open, so you can read what they would do, and
their own button waits. Closing or reloading the page doesn't stop a change. The tab
keeps the change it follows, so after a reload, or another page and back, the page
follows it again and keeps those controls off until it ends; a new tab doesn't, and `moxi loadout status` shows where
things stand. When the shell runs as a service, a change still running when the service
stops, by `moxi ui stop` or because nothing has used the shell for a while, stops with
it; run it again. The page reads again when it is shown, after each change and every
minute.

A change from the page refuses before it writes anything when the shell would read or
write somewhere other than your last `moxi` command in a terminal did. Moxi itself keeps a
record of the environment that command ran in, and the page compares, through the shell's
own check, every folder the change touches: loadout's data and state folders and config
file, Moxi's state folder and settings file, each harness row's skills and agents
folders, Claude Code's, Codex's and OpenCode's homes, projects' or repo's folders when the
change reads them, and your home folder. It also refuses when your terminal found git and
the shell's `PATH` doesn't: without git, nothing could check what git would commit. While
the two differ, the page says so first and shows each change as the command to run in a
terminal, with Copy. The line names each variable to set and its value. Set them where
the shell gets them at every login (a file under `~/.config/environment.d/`, then
`systemctl --user daemon-reload` and `moxi ui stop`, on Linux; your LaunchAgent
environment on macOS or your user environment variables on Windows, then log out and back
in), or run the command in a terminal. With no record yet, the line says to type `moxi
modules` in a terminal of your own, which makes one. `moxi loadout status` and the
home's block show the same comparison as a note, which changes neither the exit code nor
the count; in a terminal, the note says what the page does, and it shows only when the
browser UI is installed.

MCP servers are added through the `claude` and `codex` CLIs, so the page needs them on
the shell's `PATH`. Without one, a change from the page does what the terminal does
without it: the servers that need it stand, each with a note naming the fix, and the
rest is written.

The page is for the browser on this machine: none of its routes is offered through the
shell's gateway, so the phone doesn't reach it.

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/moxi-loadout-v0.1.4/packages/loadout/LICENSE).
