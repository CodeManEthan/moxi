# Changelog

## 0.1.3 (2026-11-08)

The first public release.

- The launcher needs moxi 0.1.6 or later.
- The `usage` extra needs moxi-usage 0.2, the version in this release; with 0.1
  `moxi-launcher[usage]` could not be installed.
- The `ui` extra needs moxi-ui 0.2.3 or later, which has the colours below.
- A live session that's fine shows in the theme's live colour, and the launch form's
  "Starting…" line in its launching colour.
- A Codex launch with an identity whose comms lacks the Codex wrapper names the comms
  the `comms` extra asks for, read from the launcher's own requirement (it named
  moxi-comms 0.2). New `presets.needed(extra)`.
- The README documents the start postures (`operator`, `go`, `with:<identity>` and a
  self relaunch's `relaunched`) and installs from PyPI with `moxi install`.
- The package metadata gains keywords and classifiers.

## 0.1.2 (2026-11-07)

- The bind wait for a launch under comms' wrapper is comms' own `BIND_WAIT` plus 5 s
  (15 s with a comms that doesn't say).
- A wrapper that never starts its harness without the name, and is still alive when the
  bind wait runs out while comms says the name isn't this launch's, ends the launch
  `comms-failed` (1). Other live wrappers leave it `started`, as before.
- New reason `comms-no-answer` (75): comms' wrapper exited 75 because the daemon didn't
  answer the bind in time.
- The first-turn deliverer waits the full bind wait, and its ready wait counts from the
  bind.
- New extras `notes`, `cache` and `usage`, for the handoff folder, the prompt cache rows
  and the context warning rows. The `comms` extra now needs moxi-comms 0.3.

## 0.1.1 (2026-11-07)

- The launcher offers interface 1: `launch_kind(session_id)` and `launch_kinds(ids)`
  answer scratch, seat, project or one-off from the launch log, or None for a session
  the launcher didn't start.
- The launcher needs moxi 0.1.4 or later, for `moxi.terminal`.
- The `ui` extra needs moxi-ui 0.2, where the page-server base now lives as
  `moxi_ui.loopback`.
