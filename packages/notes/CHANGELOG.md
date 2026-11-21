# Changelog

## Unreleased

- notes' tools in the Claude apps need the secrets release with `chat_redact` and the
  connector release with `Limit`. Neither is released yet; notes' floors on them rise
  when they are.
- `search_notes` in the Claude apps finds notes by words in their path or text: whole
  words, case ignored, every word needed, path hits first and then the newest, at most
  20. Its walk never follows a link and opens only files `read_note` would read, at most
  1 MiB each, and stops at 6 seconds; each match is read again, put through the chat
  redaction and kept only when every word still matches, until 16 seconds after the call
  began. A word that sits only inside a redacted secret finds nothing.
- Two tools in the Claude apps, with the `connector` extra: `list_notes` lists one folder
  of the notes root (folders by name, then files newest first, 200 a page, and a count of
  what it hides), and `read_note` reads a text file a page at a time (400 lines and 40,000
  characters, a line cut at 4,000). A note is read whole, at most 2 MiB, and put through
  secrets' chat redaction whole before it is split or cut; line numbers count the
  redacted text. Each tool refuses `redaction-off` when a chat set can't run, never
  returns a text secrets couldn't scan, runs at most two calls at once, and ends a call
  at 20 seconds.
- A `connector` extra and a provider for the connector, the public MCP host, so notes can
  give tools to the Claude apps. The `connector` extra brings the host and python-stdnum,
  and a `secrets` extra brings secrets with its personal-data set; install both,
  `moxi-notes[connector,secrets]`. The provider reads only the notes root, judges every path by one
  set of rules (relative, no dot part, no secret path, no name the chat redaction would
  change, links followed and judged again, plain files of one link, text only, a size
  cap, and the file read checked against the file judged), and shows a `root` and a
  `redaction` status row.
- Under `action = redact`, the committed copy redacts a secret beside a `gitleaks:allow`
  mark or on a line a rule's line allowlist names, which the gates still pass, and a
  token an invisible character splits, which the gates now refuse too; only an allowlist
  line keeps a value whole. A note that is one split secret with no line end is left
  out. notes' code is unchanged: it follows secrets' `redact` and `scan`.
- `moxi notes home-paths [--apply] [--json] [--limit N]`: across the notes folder's files,
  each path under your home folder becomes `~` and each Claude Code session path becomes
  its session id, and links into the notes folder become relative. A dry run unless
  `--apply`, which changes only committed files and commits nothing. A home path right
  after a quote or a non-assignment `=` outside JSON stays as written. Its `--json`
  shape is new and stable.
- The home-paths guard, `notes.home-paths`, registered with turns: an agent's file write
  under the notes root that spells your home folder or your login reads one line after
  it saying how to write it instead. It tells and never refuses. Its switch is the new
  setting `guard.home_paths`, off by default; setup asks about it with yes as the
  default, and says that with it off nothing warns.
- Setup's preview, when files it found spell your home folder, names `moxi notes
  home-paths` above the allowance lines, and the README says the same before them.
- The `prefix` setting's text, its two errors and the README's settings table no longer
  suggest your user name: the example is `work`, and the setting's text says notes are
  often pushed and shared, so pick a plain word, not your login.
- Every setting carries page fields, which `moxi config notes show --json` prints
  and the settings page draws: a plain label, a line where the label can't carry it,
  a unit on the largest file and the snapshot interval, and words for each way a
  commit handles a secret. notes needs moxi 0.1.9 or later.
- A notes repo whose git dir isn't a folder named `.git` (made with
  `git init --separate-git-dir`, or backed by a bare `notes.git`) still has no private
  allowlist, but while no private file is there its judges apply the public
  `.secrets-allow` and the doctor's `allowlist` row passes. A private file put there is
  refused as before: no allowance applies and the row fails.

## 0.1.1 (2026-11-08)

- Config keys `max_mb`, `snapshot.interval` and `snapshot.auto` are now `max_mib`,
  `snapshot.interval_minutes` and `snapshot.session_end`. An old key, set in the config
  file, is refused, naming the new one.
- Public names renamed for the naming rules (callers passing the old keyword or reading
  the old name must change): `moxi_notes.api.Resolution`: the keyword `dir` is now
  `notes_dir` (the attribute is still `dir`).

The first public release.

- `moxi notes dir` says why a folder has no notes directory: an excluded folder, a
  computed folder inside the topics or inbox folder, or a folder outside your home.
- The package metadata gains the README as its long description, keywords and
  classifiers.
- The README installs from PyPI and says Linux is supported and macOS and Windows
  untested. The README's links work on PyPI.
- `moxi doctor notes` no longer fails a missing notes root before notes' setup has run
  in this home. It warns that setup hasn't run and names `moxi install notes`. A root
  that goes missing after setup still fails.
- notes needs moxi 0.1.6 or later.
