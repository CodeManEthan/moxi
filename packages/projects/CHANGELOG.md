# Changelog

## Unreleased

- With no project, `Add a project` sits under `No projects yet.` and the empty
  sentence under it. The sentence grows as other modules' facts report, and it used to
  push the button down a line, under a click that was already on its way.
- The page's `Reading…` line, while its first read is out, shows once that read has
  been out 400 ms in every browser. In Firefox it could stay blank until something
  else redrew the page, since the page checked the wait against `Date.now()`, which
  Firefox coarsens. It now takes the wait from moxi-ui's `WaitClock`, so the `ui` extra
  asks for moxi-ui 0.2.6 or later.
- The read API offers interface 2: interface 1 and `for_paths(paths)`, which answers the
  key of the project each path is in (or None) with one read of the store, by
  `for_path`'s rules. A module that calls it loads projects with `interface=2`.
- The page's `/api/projects` gives each project `exists` (whether its root is a directory
  now, null when the server can't say within 250 ms), and the answer `home` (the page
  server's home folder) and `notes_installed`. A root on a mount that hangs holds at most
  four of the page server's threads and never the list.
- The page serves `/api/folder?path=`, what the Add panel says under its folder field:
  the path with a leading `~` taken as the page server's home, whether it is a directory
  and a git repository (`exists`, `has_git`), the project it is in and how (`root`, `inside`, `worktree`), the
  key its last segment would make and whether that key is taken. It writes nothing.
- The page serves `/api/remove-check?key=`, what a remove would be refused with now,
  without removing: the associated agents, then each contribution's sentence, or the
  reason and fix of one that won't load, raises or answers out of shape. It gathers them
  all rather than stopping at the first. `Contributions.remove_check` gives the
  contributions' rows.
- The page's add and set take a root and a notes root starting with `~/` (or `~\` on
  Windows), or `~` alone, and expand it to the home folder once the terminal comparison
  has passed, so `~` means what it means in your terminal. `~user` and relative paths
  are still refused.
- The page's `/api/remove` is an ordinary act instead of a confirmed one, since the page's
  own step is the confirmation: its body is `{key, job}`, `job` 32 hexadecimal characters
  the page mints per press, and it answers the outcome at once instead of 202 with a
  status link. One outcome is kept per `job`, so a repeat or two presses sent at once
  answer the first. `/api/remove-status?job=` stays, for a lost answer.
- The README's "Facts on the Projects page" states the contract through which another
  module adds its facts to the Projects page: a `project-facts` component that reports in
  a `moxi-project-facts` event, version 1.
- The page lists each project as a row: its name, its folder in `~` form and its kind,
  at the right when it was last active and `Open`, and a second line only when there is
  something to say, such as a folder that doesn't exist, with `Change folder`. It places
  each installed module's `project-facts` and draws what they report, at most once a
  frame; with a source of activity, projects with bad news come first and the rest by
  latest activity, else by name. Past twelve projects it gains a find field, a kind
  filter (which replaces the old one) and an `Earlier` fold.
- The page has addresses: `/m/projects/open/<key>` opens a project, `edit/<key>` opens
  it for its edit, `remove/<key>` for its remove, and `add` the add; a key is matched
  ignoring case. Opening and closing rewrite the address without a new history entry, so
  Back leaves the page. It reads its list on opening, when its tab is shown again and
  after each change; a read still out after 400 ms shows `Reading…`. While the page server
  isn't answering the last list stays, dim and with no acts; a store that refuses shows
  its reason and fix. The heading, the lede, "Where this page acts" and `Read again` are
  gone: the shell's top line names the page.
- An opened project shows one labelled row each: the facts modules' sections with a rank
  first, `Folder` (its folder in `~` form and its kind, or that it doesn't exist, with
  `Change folder`), `Agents` (who leads, what the facts modules say of them, and who also
  works there), the other sections, and `Notes`; each section's link shows that module's
  page. Its foot holds `Edit` and `Remove`, which replace its old field list.
- `Add a project` opens a panel at the window's right instead of the old add form. It
  takes the folder first, with `~` or in full, and says what is there as you type:
  whether the folder exists and is a git repository, whether it is a project's already,
  inside one or a worktree of one, or that nothing is there yet, with `Create the
  folder`. The name fills from the folder's last part until you type one; `More` holds
  the kind, the lead and the notes folder.
- `Edit` opens the same panel over a project instead of the old edit form: folder, kind,
  lead, the agents who also work there and the notes folder. `Save` sends only what
  changed, the agents taken off first and the agents added last, so the lead can move
  onto an agent taken off in one save; a refusal part way says what was saved before it.
- Remove is a step under the opened project instead of the shell's confirm sheet: what
  goes and what stays, or every thing that refuses the remove now with a way to it, and
  `Remove from Moxi`. A removed project's row becomes one line with `Add it back`.
- With a module that knows where you worked, the folders worked in that no project holds
  show under the list, `Not a project` (or `Worked in this week` with no projects), one
  line each with `Add`, which opens the Add panel with the folder and its name filled.
- The README's page section describes the page as it now is.

## 0.1.4 (2026-11-08)

The first public release.

- projects needs moxi 0.1.6 or later.
- The `ui` extra needs moxi-ui 0.2.3 or later, which has the selected colour.
- The chosen project in the picker and the open project are edged with the theme's
  selected colour.
- The package metadata gains the README as its long description, keywords and
  classifiers.
- The README covers the commands, keys, kinds, the store and install.

## 0.1.3 (2026-11-07)

- New extra `notes`, for `moxi projects promote`.

## 0.1.2 (2026-11-07)

- The `ui` extra needs moxi-ui 0.2, where the page-server base now lives as
  `moxi_ui.loopback`.

## 0.1.1 (2026-11-07)

- The page's write routes ask ui's terminal check (interface 2) first and refuse with
  its line when they would write anywhere the user's terminal doesn't.
- projects needs moxi 0.1.4 or later.
