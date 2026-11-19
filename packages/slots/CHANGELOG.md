# Changelog

## 0.1.1 (unreleased)

- Each slot reads `crashed` (`Slot.crashed`): its last run was going when its tmux
  server went down. It is not occupied, its last run was placed under another server
  than the one answering now, it did not end by itself (status 0, no signal, a trusted
  status), it was not settled, and either no end was recorded or it ended within 120 s
  before its server's end. A server's end is the next server's start within one boot,
  or the boot's last journal entry for an earlier boot (`journalctl --list-boots`);
  where that can't be read only a run with no end recorded is crashed, and with no
  server answering nothing is. `moxi slots list` and `show` print it in the state, as
  `missing, crashed`. slots itself never acts on it. `settle(slot, why)` ends a slot's
  crashed reading by recording why in place of an end, and refuses an occupied slot.

- Each run records how it ended. Every `start` and `respawn` writes a fresh run token
  (`Last.run`) and the tmux server's id (`Last.server`, `<boot id>:<tmux pid>`) into the
  slot's last launch, and sets a `pane-died` hook on the run window that writes
  `Last.ended` (a `RunEnd`: the pane's death time, its exit status, the signal that
  ended it) for that token only, so a late hook of an earlier run writes nothing. The
  hook waits up to 60 s for the module lock. `start` and `respawn` take
  `status_trusted` (default True), kept as `Last.status_trusted`, for a command whose
  exit status doesn't tell a clean end from a kill. `moxi slots show` prints `ended`.

- `start(None, ..., create=True)` takes the lowest registered slot with no session
  running that it may use, a missing one included when it has no hold but the caller's,
  and makes that slot's session again with a new run window. Its name, hold and last
  launch stay. A new number is made only when no registered slot takes the launch, so
  after a reboot launches no longer climb past the missing numbers. Without `create`,
  `start(None)` still skips missing slots.

- A hold can last until its holder releases it: `hold(slot, holder)` with no `seconds`,
  or `moxi slots hold N --holder H --until-released`. Its `Hold.until` is None, and
  `list` and `show` print it as `until released by hand`. A missing slot can now be held
  (the `missing` refusal is gone), and the hold stays when the slot is made again.
  `hold(..., expect=REQUEST_ID)` holds only while the slot's last launch carries that
  `request_id` in its meta, else it is refused with the new reason `changed`. The
  registry file moves to version 2, which an older slots refuses by its version; this
  one reads version 1 files.

- Three screens slots reads and never answers, from Claude Code 2.1.326:
  `CLAUDE_THEME_PICKER` and `CLAUDE_WELCOME`, the first-run theme picker and welcome
  screen, and `CLAUDE_CHROME_DIALOG`, the "Claude in Chrome extension detected" dialog.
  `read_screen` names them; `answer_screen` refuses them with a `UsageError`. A `Screen`
  gains `ruled` (False: drawn with no full-width rule, read over the whole pane) and
  `answerable` (False: never answered, and no marked row needed for a full match).

- A new screen, `CLAUDE_BYPASS_WARNING`: the warning Claude Code draws before a
  `bypassPermissions` session starts, answered with "Yes, I accept". Answering it makes
  Claude Code write `skipDangerousModePermissionPrompt: true` into its user settings, so
  slots presses it only when a caller asks.

- Both settings carry page fields, which `moxi config slots show --json` prints and
  the settings page draws: a plain label and a line. slots needs moxi 0.1.9 or later.

## 0.1.0 (2026-11-08)

- Public names renamed for the naming rules (callers passing the old keyword or reading
  the old name must change): `moxi_slots.api.answer_screen`: `wait` is now `wait_s`;
  `moxi_slots.api.wait_ended`: `timeout` is now `timeout_s`; `moxi_slots.api`:
  `CLAUDE_TRUST_FOLDER` is now `CLAUDE_TRUST_DIR`.

The first public release.

- Numbered tmux sessions, slots 1 to 999, on one tmux server of the module's own, run
  under the systemd user manager or launchd, or started on first use where neither runs.
- `moxi slots` with `list`, `show`, `create`, `remove`, `rename`, `attach`, `hold`,
  `release`, `jobs` and `screen`.
- The Python API: `start`, `respawn` and `run_job` place a launch with exactly the
  environment given, which never passes through tmux; `wait_ended`, holds, and the
  listing with its drift rows.
- `read_screen` and `answer_screen` for Claude Code's folder trust warning, development
  channels warning and exit dialog, matched by their text.
- `moxi doctor slots` rows for the system, tmux, the server mode, linger, the server's
  options, the socket, launch files, the trampoline, saved mismatches, the registry and
  holds.
- Linux and WSL2; macOS is untested; refused on native Windows. Needs tmux 3.2 or
  later.
- Requires moxi 0.1.6 or later.
- A README for someone new to Moxi.
