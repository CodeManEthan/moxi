# Changelog

## 0.1.0 (2026-11-08)

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
