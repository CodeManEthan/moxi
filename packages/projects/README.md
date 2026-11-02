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
  DIR (`~/code/alpha` gets `code/alpha` under the notes root), stored as the row's notes root, so a
  later move of the code keeps its notes. `--create` makes DIR first, as `add --create`
  does, and is refused for a git repository inside the notes root.

Promote checks everything `moxi projects add` checks, and refuses before anything is made
or moved: no such one-off, a notes folder that already exists (it never merges two
folders), or a root notes gives no notes folder (outside your home, say). A move that
can't be made, across file systems for one, moves nothing and adds no row. If the row
can't be written after the move, the folder is moved back, and a root `--create` made
stays. Exit codes are `add`'s: 1 an error, 2 usage, 3 refused, 75
the store stayed locked.
