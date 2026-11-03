# moxi-projects

Moxi's list of named places: projects, their roots, kinds and agents.

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
  started with, and can differ from your terminal's. The page doesn't compare them with
  anything: a different store shows as a different list and path.
- **The list.** Every project with its key, kind and root, filtered by kind as `moxi
  projects list --kind` filters it. A contribution that won't load, or a kind that is
  blocked, is named beside the filter; until it is repaired or uninstalled, every add is
  refused. Open a project to see its fields and edit its root, its kind and, while the
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
  reads its list again and, after a create, says the directory may exist.
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
