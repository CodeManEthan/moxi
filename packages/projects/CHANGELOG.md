# Changelog

## Unreleased

- The read API offers interface 2: interface 1 and `for_paths(paths)`, which answers the
  key of the project each path is in (or None) with one read of the store, by
  `for_path`'s rules. A module that calls it loads projects with `interface=2`.
- The page's `/api/projects` gives each project `exists` (whether its root is a directory
  now, null when the server can't say within 250 ms), and the answer `home` (the page
  server's home folder) and `notes_installed`. A root on a mount that hangs holds at most
  four of the page server's threads and never the list.
- The page serves `/api/folder?path=`, what the Add panel says under its folder field:
  the path with a leading `~` taken as the page server's home, whether it is a directory
  and a git repository, the project it is in and how (`root`, `inside`, `worktree`), the
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
