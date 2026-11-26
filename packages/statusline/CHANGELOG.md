# Changelog

## 0.1.0 (unreleased)

- The `effort` field falls back to the `CLAUDE_EFFORT` environment variable, trimmed,
  when Claude Code's input names no effort or a blank one. The input still wins when it
  names one.

The first version.

- `moxi-statusline-render`, the command Claude Code runs for its status line: base fields
  from Claude's input (`dir`, `branch`, `model`, `effort`, `context`, `session`,
  `transcript`, `lines`, `cost`) and installed modules' segments, in the order `show`
  gives, on one or two rows. It runs under the core's render audit hook, so it writes,
  starts and connects nothing, and it exits 0 on every path. Flags `--show`, `--only`,
  `--config` and `--no-color`.
- Config keys `show`, `separator`, `color`, `dir.parts`, `context.warn_at` and
  `context.alert_at`, with a check that refuses an unknown item, more than one `row`, a
  `row` first or last, `warn_at` above `alert_at` and `dir.parts` under 1.
- Claude Code's `statusLine` setting: `moxi statusline use` switches it to Moxi's line,
  keeping the old line, and `moxi statusline restore [--force]` puts it back. Each write
  keeps a backup of `settings.json`, drops itself when the file changed since it was
  read, and keeps the file's mode, key order, indent and link.
- Setup keeps Moxi's line on this environment's render, and with no line asks once
  whether to use it, recording the answer. Uninstall puts the old line back, or removes
  Moxi's.
- Over a status line of your own, setup and `moxi statusline use` offer to run it once
  with marked test input, in a throwaway folder and home, to see which pieces it shows;
  then offer to switch to Moxi's line set up to show the same, or to keep yours and print
  the commands that add Moxi's pieces to it, each with its measured cost.
- `moxi statusline status [--json]`.
- `moxi statusline pieces` prints those commands, cache's timer first, with each one's
  measured time.
- `moxi statusline preview` renders a sample input and prints the line and its time.
- With the `ui` extra, a page in the browser UI shell: the line's items in Row 1, Row 2
  and Off lists, each with a switch and up and down buttons; the line's options; a
  preview rendered by the real command over a sample input, with its time; Save and
  Discard, so the config is written only on Save; and "Use Moxi's line" and "Put my old
  line back", disabled while changes are unsaved. The preview is asked for through
  moxi-ui's `readLater` once changes settle, so the page root's `data-reading` mark
  counts it from the change until its lines are drawn; the `ui` extra asks for moxi-ui
  0.2.8 or later.
- An install listener compiles the bytecode a render loads at every module's install.
- Doctor rows: the command Claude Code's setting names resolves, bytecode for what a
  render loads, one sample render against the budget, `show` items with nothing behind
  them, and installed modules named like a base field.
