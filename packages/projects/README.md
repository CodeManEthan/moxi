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

## Questions at add

Once a project is added, other modules may ask about it: publishing, for one, asks whether
to keep a git repository's history publishable. Run in a terminal (on both stdin and
stdout) and without `--json`, `moxi projects add` asks each question, its suggestion the
default answer; press Enter to take it. Run from a script or an agent's shell, or with
`--json`, it asks nothing and turns nothing on: it prints one `not asked:` line per
question naming the command that sets it later (with `--json`, the project carries them
as `asked`). A question a module's own setting already answers is not asked, and that
answer is acted on either way (`answered` in `--json`). An answer that fails leaves the
project added, and a warning names the failure and the command to run.

The Projects page's Add panel shows the same questions as fields under `Name`, each set
to its suggestion, once the folder has been read, and sends the answers with the add.

A module asks through projects' contribution interface 3: `questions(fields)` gives the
questions for the new row, and `answer(project, key, value)` acts on each answer
(`moxi_projects/contributions.py` holds the contract).

A module whose part of projects won't load blocks only what it bears on: a remove, since
it can't say whether it holds the project, and an add or set of a kind no loaded module
declares. Any other add goes ahead with a warning naming it. `moxi projects kinds` and
`moxi doctor projects` name it with the fix: repair it, or `moxi uninstall` it.

## The store

The list is one SQLite file, `projects.db`, in projects' data directory (`moxi paths
projects` names it), readable by you alone. The first write makes it; a read never
writes. Other modules read it in process through projects' API, interface 1
(`list_projects`, `get`, `for_path`, `kinds`), reached with
`moxi.modules.load("projects", interface=1)`. Interface 2 is interface 1 and
`for_paths`, which answers many paths' projects with one read of the store, each by
`for_path`'s rules; a module that calls it loads projects with `interface=2`, so an older
projects reads as absent. `moxi install projects` again is the upgrade, and moves an
older store to the current schema.

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

- **The list.** One row per project: its key, its folder in `~` form, its kind when it is
  not a code project (`agent's home`, or a contributed kind's name), at the right when it
  was last active, and `Open`. A row has a second line only when there is something to
  say: a folder that doesn't exist, with `Change folder`, and what other modules report
  of it (see Facts on the Projects page). With a module that knows when each project was
  worked in, projects with bad news come first and the rest follow by latest activity;
  without one the list is by key, and bad news stays on its row's second line. Past
  twelve projects the list gains a find field (by name or folder), a `Kind` filter when
  it holds more than one kind, and an `Earlier` fold for the projects not used in 30
  days, which only a module that knows all activity can say.
- **Reading.** The page reads its list when it opens, when its tab is shown again, after
  each change and on `moxi-projects-changed`; a read still out after 400 ms shows
  `Reading…`. While the page server isn't answering, the last list stays, dim and with
  no acts. A store that can't be read, is too new or stays locked shows its reason and
  fix, and no rows.
- **Addresses.** `/m/projects/open/<key>` opens a project, `edit/<key>` opens it with the
  Edit panel, `remove/<key>` with its remove step, and `add` the Add panel. A key is
  matched ignoring case; one that names no project opens the list with a line saying so.
  Opening and closing rewrite the address without a new history entry, so Back leaves the
  page.
- **An opened project.** One labelled row each: what other modules report with a rank (a
  failing service, say) first; `Folder`, its folder and kind, or that it doesn't exist,
  with `Change folder`; `Agents`, who leads, whether the lead is running where a module
  can say so, and who else works there; the other modules' rows; and `Notes`, while the
  notes module is installed. A module's row links to that module's page. The foot holds
  `Edit` and `Remove`.
- **Folders that are not projects.** With a module that knows where you worked, the
  folders it says you worked in that no project holds show under the list as `Not a
  project` (`Worked in this week` when there are no projects yet), one line each, newest
  first, with `Add`, which opens the Add panel with that folder and its name filled. Five
  show, then how many more there are, with `Show`.
- **Add.** `Add a project` opens a panel at the window's right. Give the folder first, in
  full or starting with `~`; the panel says what is there: whether the folder exists and
  is a git repository, whether it is a project's already, inside one or a worktree of
  one, or that nothing is there yet, with `Create the folder` (`Create the folder and run
  git init` for a kind that makes a repository; hidden for a kind that refuses to make
  its root). The name fills from the folder's last part until you type one; it can't be
  changed later, since everything other modules file under the project is filed by it.
  `More` holds the kind, the lead and, with the notes module, the notes folder. The page
  gives `git init` 20 seconds, so its answer arrives inside the shell's deadline; the
  terminal's add gives it 30. If the shell's `PATH` has no git, a create is refused and
  the answer says how to fix it for good: set `PATH` in the service manager's environment
  and run `moxi ui stop`, or run the add in a terminal. When an add's answer is lost, the
  page reads its list again and says the project may have been added.
- **Edit.** `Edit`, or `Change folder`, opens the same panel over the project: its folder,
  kind, lead, the agents who also work there (each with `Take off`, and a field to add
  one), and, with the notes module, its notes folder; the name stays as it is. `Save`
  sends only what changed, in an order the lead rule allows: the agents taken off, then
  the fields (`moxi projects set`), then the agents added (`moxi projects agents`). The
  first refusal stops it, and the panel says what was saved before it.
- **Folders and `~`.** A folder must be an absolute path, or start with `~/` (`~\` too on
  Windows), or be `~` alone: the page expands `~` to the home folder only once it has
  found the shell's home is your terminal's (below), so `~` means what it means in your
  terminal. It refuses a relative path, `~user`, and a `~` path while nothing places the
  home.
- **Kinds and contributions that won't load.** A contribution that won't load, or a kind
  that is blocked, is named in a dim line under the panel's `Kind` until it is repaired
  or uninstalled. While a module's contribution won't load, a remove is refused, and so
  is an add or a kind change to a kind no module that loads declares, since it may be
  that module's; any other add goes ahead without it, and the panel shows a warning beside
  the success: which module was skipped, that its defaults and checks were not applied,
  and the fix (repair it, or `moxi uninstall` it). `moxi projects add` prints the same
  warning on one line to stderr and exits 0. A blocked kind can't be added or set.
- **Remove.** Remove asks once, in a step under the opened project: what goes (the
  project's entry in Moxi) and what stays (its folder and every file in it, its notes
  folder, and what other modules keep of it), and nothing is sent until you press
  `Remove from Moxi`; Cancel sends nothing. Before that the page reads what would refuse
  the remove now (associated agents, with `Edit`, and each module that still holds the
  project, with its sentence and its page), and while anything does the button is off.
  Removing deletes nothing on disk, only projects' row: the project's row becomes one
  line saying so, with `Add it back`, which adds it again as it was. The remove is an
  ordinary act, not one the shell's sheet confirms: the step is the confirmation, and
  every remove is still refused while anything holds the project. Each press carries a
  job of its own, under which the outcome is kept in the store for a day, so the same
  press sent again, two sent at once, or one sent again after the page server restarts,
  all get the first answer and act once. When an answer is lost, the page reads the
  outcome kept under its job. A store newer than this version of projects reads, an
  unreadable or locked one, or none yet, is answered at once, and nothing is written.
  `moxi projects remove` in a terminal keeps nothing.
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
  a terminal. The same line shows dim above the panel's button and the remove step's, and
  the list still reads. Commands in a terminal never refuse over the record. The check is
  ui's, so the page needs a `moxi-ui` that offers it; with an older one every change is
  refused with the step that updates it.

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

## Facts on the Projects page

The Projects page shows what other modules know of each project (what runs there, its
cost, a service that failed) without naming any module. Each module adds its facts by
announcing a component named `project-facts`; the page places every installed module's
one and draws what each reports. This section is that contract, version 1.

### The rules

1. **Its own interface.** A module's facts reach the page only through its own
   `project-facts`, which reads only its own module's routes and topics, never another
   module's (the shell's module list included), and words its facts itself. It re-reads
   on its own schedule and on its own module's topics. A module installed or removed
   reaches it only as the page placing or removing it.
2. **Its module's own judgment.** Where the module judges a row (a verdict, a state, a
   status), the component reports that judgment, in words a table of its own maps it to,
   and never works it out again from the raw fields beside it. Whenever the module can't
   say (the answer's `state` is not `up`, it has a `problem`, the read failed, or the row
   carries no judgment the table maps), that piece is absent: `null` for the whole
   report, or that part left out. A judgment the page needs and the module doesn't give
   is added to the module, never guessed in the browser.
3. **A fact lives only as long as its read.** Every fact comes from its route's last
   successful read. A read that fails, is refused, or answers a `state` other than `up`
   drops exactly the facts that route fed, and the component reports again at once
   without them (`null` when nothing is left). Its next good read puts them back.
4. **A joined phrase is true by construction.** A phrase that needs two modules' facts is
   one part from each, joined by the page only where they agree. A part that says whether
   something holds carries it as a typed field the page compares without reading words
   (`runs`, under Agents below). Where two reports disagree, neither half is drawn. There
   are two joined phrases: the phrase after an agent's name, and a running line's turn
   word (`· working`, under Live below).

A component that is missing, failing, hung or late draws nothing of itself and changes
nothing else on the page.

### The component

```python
COMPONENTS = {
    "project-facts": {"script": "project-facts.js", "title": "Sessions",
                      "doc": "What sessions knows of each project",
                      "contract": 1, "place": "inline",
                      "attributes": {"projects": {"type": "string"},
                                     "open": {"type": "string"}}},
}
```

`contract: 1` there is the shell's placement contract, not this one; this one's version
is the report's `version`. The page reads the shell's module list when it opens and again
on the `ui.modules` topic, and places every present module's `project-facts`, in
module-name order, in one hidden host. A module listed twice, or whose component the
shell won't place, is skipped. When the list changes, the page places the components of
modules now present and removes those of modules gone, with their reports; a component
already placed is left as it is.

It is given two attributes, and watches both:

- `projects`: JSON, `[{key, root, lead, associated}]`, every project the page holds, each
  root absolute. Set when the component is placed, and again whenever a key, root, lead
  or associated agent changes or a project is added or removed.
- `open`: the key of the project opened on the page, or absent. Set whenever that
  changes.

**When it reads.** Only while it is connected and the document is visible. It starts its
reads and subscriptions on connect and stops all of them on disconnect (another page
placed), pauses its timers while the document is hidden, and reads once when it shows
again.

**What it may not do.** Read anything of another module's, act, draw anything visible
(its host is hidden), or work its module's judgment out again. Its words are its own
page's words for the same fact.

### The report

The component dispatches a bubbling, composed `moxi-project-facts` event whenever what it
would say changes. Each report replaces that module's last one whole; `null`, or none yet,
is nothing to say.

```js
this.dispatchEvent(new CustomEvent("moxi-project-facts", {
  bubbles: true, composed: true,
  detail: {
    version: 1,
    order: 20,
    activity: "all",
    gathers: "sessions",
    projects: {
      alpha: {
        at: 1763221740,
        parts: [{ text: "", count: { one: "active recently", many: "active recently" },
                  claims: ["claude/0f3c…"] }],
      },
    },
    folders: [{ folder: "/home/me/code/finch-scripts", at: 1763218800,
                parts: [{ text: "2 sessions this week" }] }],
    more_folders: 0,
  },
}));
```

| field | what it means |
|---|---|
| `version` | `1`. A report with any other is refused whole. |
| `order` | A whole number from 0 to 999: its parts, lines and sections sit in this order, lower first. A missing or wrong one reads as 500. |
| `activity` | Optional. `all`: its `at` covers every record it has; `window`: only some, such as what runs now. A component that reports `at` is an activity source. |
| `gathers` | Optional. What it gathers under a project (`sessions`, `cost`), for the empty list's sentence. |
| `projects` | By key, exactly as given in `projects`. Each: `at`, epoch seconds of the newest activity it knows there; `parts`, its phrases for the row's second line; and for the key in `open` only, `sections`, `agents` and `stays` (Remove's `Stays` lines). |
| `folders` | Optional. Folders, as absolute paths, that no project holds: `{folder, at, parts}`. |
| `more_folders` | Optional. How many folders it knows past those it lists. |
| `live` | Optional. By claim, at most 100: what it says of each session it can read, `{in_turn, state}`. It names no project. |

The types the fields use:

```ts
Part = { text: string; heavy?: boolean; warn?: boolean; rank?: number; claims?: string[];
         count?: { one: string; many: string; after_one?: string; after_many?: string } }
Section = { label: string; rank?: number; lines: Line[];
            link?: { text: string; module: string; route: string } }
Line = { parts: Part[]; claims?: string[]; runs?: boolean }
Say = { runs: boolean; state: string; where?: string }
Live = { in_turn: boolean; state: string }
```

`Part` also takes `idle_only?: boolean`.

- **`rank`** is Home's `needs` scale (10 someone is blocked on you, 20 something waits for
  you, 30 something is broken, 40 something will run out). A ranked part leads its row's
  second line, lower rank first, and lifts the row above the order by activity while some
  component gives some project an `at`; with none (a component that says `activity` but
  gives no project an `at` counts as none) the list stays by key and nothing rises. A ranked
  section moves to the top of the opened project. Without a rank, in `order`.
- **`heavy`** is weight only. On a row whose folder doesn't exist, the page's own heavy
  words and their fix lead line two, and the heavy parts follow the fix in the text
  colour at normal weight. **`warn`** is a warning: the text colour at normal weight,
  after the ranked parts and before the rest; it doesn't lift the row.
- **Counted parts and claims.** A part with `count` is drawn as a number and words: the
  page counts its `claims` that no part or line of a lower `order` on the same project
  already claims, and writes `<n> <one|many>`; in a section, after lines, `after_one` or
  `after_many` (`3 more this week`). A count of 0 is not drawn. Two modules that see the
  same session never count it twice. Its `text` may be empty.
- **A claim** names one session: `<harness>/<session id>`, the harness as Moxi spells it
  (`claude`, `codex`) and the id the harness writes for that session (Claude Code's
  session id, Codex's rollout id). A claim that names no session yet (a launch with no id)
  starts `request/`, which is no harness, and so matches no other module's.
- **`at`** is epoch seconds, a number. A component whose own time is in another shape
  turns it into epoch seconds, and leaves `at` out when it doesn't parse. The page says a
  project or folder was not used only while a component with `activity: "all"` has
  reported; a `window` source can make a row newer, never unused.
- **Agents.** `agents` says, for the open project's lead and associated agents by name,
  `runs` (whether this module says the agent runs now), `state` (its words for that) and,
  only with `runs` true, `where`. The page draws a phrase after a name only when every
  report that speaks of it agrees on `runs`: the `state` of the lowest `order`, then, with
  `runs` true, the `where` of the lowest `order` that gives one. Where they disagree it
  draws nothing. A `Say` with a `where` and `runs` false is refused.
- **Live.** A line's `runs` is its module's word that the line's session runs now; a
  part's `idle_only` says it holds only while that session is not in a turn (a `last
  active` time). A report's `live` says, by claim, whether a session is in a turn
  (`in_turn`) and its words for that (`state`). On the opened project the page joins them:
  a line with `runs` and exactly one claim, whose session every report's `live` that names
  it agrees is in a turn, is drawn without its `idle_only` parts and ends with the `state`
  of the lowest `order` saying so (`planner · headless · working`). A claim the reports
  disagree on, one that another line of the project's sections also claims, or one the
  line's own report claims on another project too, is left as given. `live` claims
  nothing and counts nothing: no count, `agents` phrase or `stays` line changes, and the
  closed row draws no turn word. A page that doesn't know these fields draws the lines as
  given, and a component that doesn't send them has nothing joined to it.
- **Sections** with one `label` from two modules are one row: lines in `order`, the first
  `link` in `order`, the lowest `rank`. A link is drawn only while its `module` is
  installed, and a press is `navigate(module, route)`.
- **Folders** with one `folder` from two modules are one row: parts in `order`, `at` the
  newest. Only an activity source may give a folder an `at`; the page drops `at` from a
  report with no `activity`, and shows a folder only once some report gives it one. It
  adds the largest `more_folders` of any activity source to the folders it holds past
  those it shows.

**What the page refuses.** Another `version` (the whole report); a key it did not give, or
an agent that is not the open project's; a field of the wrong type; a text over 200
characters; more than 8 parts on a row or a line, or 6 lines in a section; `sections`,
`agents` or `stays` on a project that isn't open; a `live` of more than 100 sessions (the
whole of it). A refused field is dropped and logged
once per module to the console, and the rest of the report is used. Keep a report inside
these caps: end a section's last line `<n> more` when there is more to say.

**Late, slow and fast reports.** The page draws without waiting for any component. It
applies reports at most once per animation frame, so reports that arrive within one frame
are drawn together, and a component that reports in a tight loop re-sorts the list at
most once a frame.

**Cost.** While the page shows, each placed component keeps its module's page server
alive with its reads, each within the memory its page declares. Each page server stops
after its `idle_s` with nothing reading it.

The project's own facts (its folder, kind, agents' names, notes folder and a missing
folder) are projects' and don't go through the contract.

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

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/moxi-projects-v0.1.4/packages/projects/LICENSE).
