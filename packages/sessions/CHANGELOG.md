# Changelog

## 0.2.5 (2026-11-07)

- New guard for turns, `sessions.transcript-read`: once it is on, an agent's Read, Grep or
  Glob of a path under a transcript root, or a shell command naming one, is refused and
  pointed to `moxi sessions read <session>`. A simple command that runs `moxi sessions` or
  `moxi memory` passes. Off by default; its switch is `guard.transcript_read`, and the
  install asks about it.
- New extra `turns`, which installs moxi-turns to enforce the guard.
- New doctor row, `guard`: `off`, `ok` when the guard is on and turns enforces it, or
  `warn` when it is on and nothing does.
