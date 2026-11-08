# moxi-projects

Moxi's list of named places: projects, their roots, kinds and agents.

A project is a key, such as `alpha`, standing for a directory, its root. Other Moxi
modules take the key wherever they take a path, and file what they keep under it: repo's
git hooks, services' rows, notes' folders, the launcher's launches. Each project has a
kind, may name a lead agent and any number of associated agents, and, with the notes
module, may keep its notes in a folder of its own.

```sh
moxi projects add alpha --root ~/code/alpha
moxi projects list
moxi projects find ~/code/alpha/src     # alpha
```

## Commands

| Command | Does |
|---|---|
| `moxi projects add KEY [--root DIR] [--create] [--kind KIND] [--lead NAME] [--notes-root DIR]` | add a project; `--create` makes the root first (for most kinds a directory and `git init`) |
| `moxi projects set KEY [--root DIR] [--kind KIND] [--lead NAME \| --no-lead] [--notes-root DIR \| --no-notes-root]` | change a project's fields |
| `moxi projects remove KEY` | remove a project; its files stay |
| `moxi projects list [--kind KIND]` | every project, by key |
| `moxi projects show KEY` | one project with its lead and associated agents |
| `moxi projects find [PATH]` | the key of the project PATH (default: here) is in, or nothing |
| `moxi projects kinds` | the kinds, built in and contributed, and any contribution that won't load |
| `moxi projects agents add KEY NAME`, `agents remove KEY NAME` | add or remove an associated agent |
| `moxi projects promote SLUG KEY [...]` | make one of notes' inbox one-offs a project (below) |

Every verb takes `--json`. A write prints the project as it now stands, and `remove`
prints it as it was. Paths are made absolute: `~` expanded, a relative one against the
working directory. `--notes-root` needs the notes module installed.

Exit codes: 0 done; 1 an error, such as a store that can't be read or a module's part of
projects that won't load; 2 asked wrongly; 3 refused, such as a taken key or no such
project; 75 the store stayed locked, try again. A refusal prints its reason and the
command that fixes it.

## Keys, roots and agents

A key holds only letters, digits, `.`, `_` and `-`, starts with a letter or a digit and
doesn't end with `.`. Keys are unique ignoring case and looked up ignoring case, and no
key is a Windows device name (`con`, `nul`, `com1`), since other modules use a key as a
folder name. A root is an absolute directory, unique among projects, and may not exist
yet: a project can be added before it is cloned. `find` matches the deepest root a path
is under, and a linked git worktree counts as its main work tree.

An agent name is any word without spaces. The lead is never also an associated agent.
Whether a name is a live agent anywhere is not projects' question.

## Kinds

A kind says what a project is. projects has two built in, `project` (a code project: a
directory, usually a git repository; the default) and `agent` (an agent's home
directory), and other modules add their own: notes adds `topic`, a subject with no code
(see Promote). A module that adds a kind may give its defaults, such as a root, check a
row of its kind before it is written, and refuse a remove while it still holds the
project: services while a service names it, notes while removing it would move where the
project's sessions find notes that are already there.

A module whose part of projects won't load blocks only what it bears on: a remove, since
it can't say whether it holds the project, and an add or set of a kind no loaded module
declares. Any other add goes ahead with a warning naming it. `moxi projects kinds` and
`moxi doctor projects` name it with the fix: repair it, or `moxi uninstall` it.

## The store

The list is one SQLite file, `projects.db`, in projects' data directory (`moxi paths
projects` names it), readable by you alone. The first write makes it; a read never
writes. Other modules read it in process through projects' API, interface 1
(`list_projects`, `get`, `for_path`, `kinds`), reached with
`moxi.modules.load("projects", interface=1)`. `moxi install projects` again is the
upgrade, and moves an older store to the current schema.

`moxi doctor projects` checks the store, each root (a missing one is a warning), two
roots naming one directory, rows whose kind no installed module declares, and each
module's part of projects.

## Promote

With the notes module installed, `moxi projects promote SLUG KEY` turns an inbox one-off
(a folder of notes for work with no project yet, `moxi notes inbox list`) into project
KEY, and moves its folder to the project's notes folder. It moves the folder and never
copies it.

```sh
moxi projects promote sketch gardening                                  # a topic
moxi projects promote sketch alpha --kind project --root ~/code/alpha   # code
moxi projects promote sketch alpha --kind project --root ~/code/alpha --create
```

- **A topic**, the default kind, is a subject with no code. Its root and its notes folder
  are one folder under the notes' `topics` folder, and the move makes it, so a topic
  takes neither `--root` nor `--create`.
- **Any other kind** needs `--root DIR`. Its notes folder is the one notes computes for
  DIR (`~/code/alpha` gets `code/alpha` under the notes root, and under your prefix if you
  set one), stored as the row's notes root, so a later move of the code keeps its notes.
  `--create` makes DIR first, as `add --create` does, and is refused for a git repository
  inside the notes root.

Promote checks everything `moxi projects add` checks, and refuses before anything is made
or moved: no such one-off, a notes folder that already exists (it never merges two
folders), or a root notes gives no notes folder (outside your home, say). A move that
can't be made, across file systems for one, moves nothing and adds no row. If the row
can't be written after the move, the folder is moved back, and a root `--create` made
stays. Exit codes are `add`'s: 1 an error, 2 usage, 3 refused, 75
the store stayed locked.

## The page

With the `ui` extra (`moxi-ui`, the browser UI shell), projects has a page in the shell, at
`/m/projects/`. Each change on it runs the same code as the `moxi projects` command you
would run in a terminal, and refuses where that command refuses, before it makes anything.

- **Where this page acts.** The store file the page reads and writes, and the git a create
  would run, or none. Both are the shell's, found from the environment the shell was
  started with, and can differ from your terminal's.
- **The page writes only where your terminal does.** When you type a `moxi` command in a
  terminal of your own, Moxi records the environment variables that place its folders
  (projects records nothing itself; `moxi paths moxi` names the file). Before every
  change, the page places each folder the change reads or writes twice, once from that
  record and once from the shell's environment, and refuses while any differs: the store
  file, your home folder, and for an add, a set or a remove the data, state and config
  folders of each module that contributes to projects (services' and notes', say). It
  refuses too while nothing is recorded yet. The refusal names what differs, the variables
  to set for the browser shell and their values in your terminal, where to set them so
  the shell has them at every login, and the step that restarts it; or run the command in
  a terminal. The same line shows as a note under "Where this page acts", and the list
  still reads. Commands in a terminal never refuse over the record. The check is ui's, so
  the page needs a `moxi-ui` that offers it; with an older one every change is refused
  with the step that updates it.
- **The list.** Every project with its key, kind and root, filtered by kind as `moxi
  projects list --kind` filters it. A contribution that won't load, or a kind that is
  blocked, is named beside the filter until it is repaired or uninstalled. While a
  module's contribution won't load, a remove is refused, and so is an add or a kind change
  to a kind no module that loads declares, since it may be that module's; any other add
  goes ahead without it, with a warning (see Add). A blocked kind can't be added or set.
  Open a project to see its fields and edit its root, its kind and, while the
  notes module is installed, its notes root (`moxi projects set`), set or clear its lead,
  and add or remove associated agents (`moxi projects agents add` and `remove`). A root
  must be an absolute path; the page refuses a relative one or one starting with `~`.
- **Add.** The form `moxi projects add` takes: key, root (leave it empty when the kind's
  module gives one), kind, lead, the notes root while the notes module is installed, and
  create for a kind that makes its root. Create is hidden for a kind that refuses it. The
  page gives `git init` 20 seconds, so its answer arrives inside the shell's deadline; the
  terminal's add gives it 30. If the shell's `PATH` has no git, a create is refused and the
  answer says how to fix it for good: set `PATH` in the service manager's environment and
  run `moxi ui stop`, or run the add in a terminal. When an add's answer is lost, the page
  reads its list again and, after a create, says the directory may exist. An add made
  while a module's contribution won't load shows a warning beside the success: which
  module was skipped, that its defaults and checks were not applied, and the fix (repair
  it, or `moxi uninstall` it). `moxi projects add` prints the same warning on one line to
  stderr and exits 0.
- **Remove.** Remove opens the shell's sheet, which shows the command, the key and the
  root, and says the files stay. Nothing is sent until you confirm; Cancel sends nothing.
  The page then shows how it ended: removed, or refused with each reason and the fix. The
  outcome is kept in the store for a day, so the same request sent again, also after the
  page server restarts, gets the same answer and doesn't act twice. A store newer than
  this version of projects reads, an unreadable or locked one, or none yet, is answered at
  once, and nothing is written. `moxi projects remove` in a terminal keeps nothing.

### Components

Other modules' pages can place three of projects' parts with the shell's `place()`, from
`/m/ui/client.js`:

| Component | Element | Attributes | What it shows |
| --- | --- | --- | --- |
| `picker` | `<moxi-projects-picker>` | `kind` | the projects, each by key with its root, to choose one |
| `card` | `<moxi-projects-card>` | `key` (required) | one project's key, kind, root, lead and associated agents |
| `add` | `<moxi-projects-add>` | `kind` (the kind it starts with) | the add form, with create |

```js
import { place } from "/m/ui/client.js";

const picker = await place(host, "projects", "picker", { kind: "agent" });
picker?.addEventListener("moxi-change", (event) => use(event.detail.key));
```

When you choose a project, the picker sends a `moxi-change` event that bubbles and is
composed, so it crosses shadow roots, with the detail `{key}`. The key is all it sends:
the root is only shown, and never goes back to the host. Each component reads its
attribute once, when it is placed; place a new one for another kind or key. After a
change that was made, or may have been, the add form and the page send
`moxi-projects-changed` on the document, and every placed picker and card reads its
route again.

## Install

Linux, Python 3.12 or later, and [uv](https://docs.astral.sh/uv/); `git` for
`add --create`. projects also has macOS and Windows code, which the repository's CI tests
without gating and nobody runs yet: treat both as untested.

```sh
uv tool install moxi
moxi install projects
```

The first line installs `moxi`, Moxi's core, which provides the `moxi` command; the
second adds projects beside it and offers its optional extras: `notes` (`moxi projects
promote`) and `ui` (the page). The repository's README, at
[CodeManEthan/moxi](https://github.com/CodeManEthan/moxi), covers the `moxi` command and
lists the other modules.

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/main/packages/projects/LICENSE).
