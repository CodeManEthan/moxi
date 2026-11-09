# Changelog

## 0.1.0 (2026-11-08)

The first release.

- `moxi-statusline-render`, the command Claude Code runs for its status line: base fields
  from Claude's input (`dir`, `branch`, `model`, `effort`, `context`, `session`,
  `transcript`, `lines`, `cost`) and installed modules' segments, in the order `show`
  gives, on one or two rows. It runs under the core's render audit hook, so it writes,
  starts and connects nothing, and it exits 0 on every path. Flags `--show`, `--only`,
  `--config` and `--no-color`.
- Config keys `show`, `separator`, `color`, `dir.parts`, `context.warn_at` and
  `context.alert_at`, with a check that refuses an unknown item, more than one `row`, a
  `row` first or last, `warn_at` above `alert_at` and `dir.parts` under 1.
- `moxi statusline preview` renders a sample input and prints the line and its time.
- An install listener compiles the bytecode a render loads at every module's install.
- Doctor rows: bytecode for what a render loads, one sample render against the budget,
  `show` items with nothing behind them, and installed modules named like a base field.
