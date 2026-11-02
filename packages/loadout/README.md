# moxi-loadout

Your own skills, agents and settings, placed for each harness: Claude Code, Codex and
OpenCode. You keep one folder of your items and one manifest, `loadout.toml`, that says
which item goes where.

1. It reads the manifest: a user section for every session, project sections by path
   (or by key, with projects installed), and the items' own tables. Every key is checked
   before anything is used, and an unknown one names the closest known key.
2. It writes the manifest through the core's comment-keeping TOML editor, holding a lock
   in its own state directory, so no lock file lands in your folder.
3. It keeps its machine settings, the folder's place, backups kept and each harness's
   project folders, in `moxi config loadout`; every choice of what goes where stays in
   the manifest, so a second machine cloned from the folder installs what you chose.
