# Changelog

## 0.1.4 (2026-11-08)

- The chosen project in the picker and the open project are edged with the theme's
  selected colour.
- The `ui` extra needs moxi-ui 0.2.3 or later, which has that colour.

## 0.1.3 (2026-11-07)

- New extra `notes`, for `moxi projects promote`.

## 0.1.2 (2026-11-07)

- The `ui` extra needs moxi-ui 0.2, where the page-server base now lives as
  `moxi_ui.loopback`.

## 0.1.1 (2026-11-07)

- The page's write routes ask ui's terminal check (interface 2) first and refuse with
  its line when they would write anywhere the user's terminal doesn't.
- projects needs moxi 0.1.4 or later.
