# Changelog

## Unreleased

- The launcher offers a piece for Home's main spot in the browser UI shell: a box to say
  what the agent should do, the line above it that picks the project or folder the
  session starts in, a chip that picks the harness, the model and the effort, and a
  button that starts the session. The button names where the launch runs (the lowest
  free slot, a new slot, or the command to copy where no slot can be used) and its caret
  opens the other choices; a launch from Home runs where the button says, whatever
  `[defaults]` set for `target` and `slot`. The lines under the box say how the launch
  went. New component `prompt` (`<moxi-launcher-prompt>`).
- Home's launch piece has `Options` beside the chip: a panel at the window's right edge
  with a find field and the launch form's other rows in the catalog's groups, each group
  saying while closed which of its values differ from the defaults. Each row changed
  there is named on a `Changed` line under the box and counted on the word
  (`Options · 2`); `Reset` puts every changed row back and leaves the box, the place,
  the chip and where the launch runs. A value that can do harm is in the theme's
  `danger` colour wherever it is drawn. Picked by hand it is set only after `Turn on`
  under it, and it holds for the one launch that starts with it or whose command is
  copied. A request from Home
  carries `off_posture: true` when a permission row it sends or leaves out is off the
  user's usual value, so such a launch is not refused for it.
- Home's `Options` panel draws a harness's row only where the place the button launches
  in passes its flag, and lets go of a value set on a row it stops drawing. Its Tools >
  Built-in is a line per built-in tool with `allow` and `deny`: a press changes only that
  tool's own entry in `allowed_tools` or `disallowed_tools`, scoped rules and every other
  entry stay listed in their places with `Remove`, and while `tools` names a set the
  lines it leaves out are dim.
- Home's launch piece has `Preset` on the line above the box once any seat or project
  preset is set up: its menu takes the preset off or applies one, each row saying what it
  sets and where it puts the launch. A project picked there, or the one Home opens on,
  applies that project's own preset; a seat stays applied when a place is picked. The
  applied preset's values are what `Changed` and the count measure from, and a dim line
  under the box names the permission rows it sets off the user's defaults.
- Home's launch piece fills each row the form leaves at its shipped default with the
  value the last launch of the seat, else the project, else the folder on the line above
  the box gave it, the harness first. `From last launch` on that line names the filled
  values, its list gives each with `Clear`, and the panel says under each when that
  launch was made. A filled value is no change, an edited one becomes the user's, and
  one that can do harm is in the `danger` colour, asks nothing and stays after a launch.
  The request names the filled rows in `filled`.
- Home's launch piece lists up to three earlier launches of the same seat, project or
  folder under the box, `Run again`, each by the first words of its brief and when it was
  made. Pointing at one shows a card with what a click runs today and where (the prompt,
  the harness, model and effort, the preset, where and when it ran, each permission row
  off the user's defaults, a value that can do harm in the `danger` colour, and the rows
  this install no longer offers) and what the click does; one click runs it again where
  the button's default places it, or copies its command for the user's terminal.
- Home's `Options` panel shows the session name under Seat > Name & notes: the name the
  template gives, following the seat, the place, the model and the comms name, with a
  sentence saying whose template it is. A name typed over it is a change for that
  launch, and `Use the template` takes it back. Its caret opens the template's editor
  (pieces, date format, the template as written, `Keep for` a project), each edit
  rendered unsaved; `Save template` writes `name.template` or `name.templates` through
  the shell's own settings route, so a key locked for the shell's run is refused there.
- Home's launch piece has a `+` at the left of the chip with two entries. `Brief from a
  file` takes a path: while it is set the box is off and the launch sends that file as
  its first turn (`brief_file`, and no `brief`). `Continue the last session`, read each
  time the menu opens, resumes the newest session the user made in the place the line
  names that can be resumed now, in a slot or by copying its resume command, with the
  lines under the box saying how the resume went; the box keeps its text.
- A catalog row takes seven new optional fields, read by the browser's launch options:
  `group` and `sub` (where the row is drawn), `label` (its name in plain words),
  `danger` (its values that can hurt), `carry` (false for a row never filled from an
  earlier launch), `shown_with` (modules the row is drawn only with; no launch rule) and
  `typed_with` (the module a row with listed values needs to take a typed one). Every
  row a user may set has its label, and its group unless another control sets it. New
  `catalog.GROUPS` and `catalog.PANEL_FIELDS`. `GET /api/catalog` sends each field's
  `group`, `sub`, `label`, `danger`, `carry` and `modes` (the commands the row composes
  into), and `shown` and `typed`, which say whether this install draws the row and
  whether it takes a typed value.
- `GET /api/form` takes the query `identity` and answers `modes`: for each value of the
  `target` row, the command a launch of the form's harness composes there under that
  comms name, or null where such a launch is refused. A catalog field's `modes` are
  matched with it. New `pagehandlers.modes` and `page.FORM_MODE`.
- A fresh launch records what its own request gave: its values hold `sources`, where
  each row's value came from (`last` for a typed row the request names in `filled`, a
  value filled in from an earlier launch), and `filled`. A relaunch or resume records
  neither, and the launch log takes no new step. A page request's `filled` is signed
  with its ticket and written to the request file. New `GET /api/last?seat=|project=|cwd=`:
  the values the newest launch of a seat, else a project, else a folder was asked for
  beyond its preset and the defaults, without the place, the first turn, where it ran
  and its name. A launch logged before this records no sources and fills nothing. New
  `pagehandlers.request_of`, `api.LaunchRequest.filled` and `page.LAST`.
- New `GET /api/recents?seat=|project=|cwd=`: the newest three launches of a seat, else
  a project, else a folder that asked for different requests, each with the start of
  its brief (else its model and effort, else its harness) and where it ran. New `GET
  /api/recent?request=&target=&slot=`: that launch's request with where the page would
  run it now, resolved as `/api/preview` resolves a launch but with no ticket, and what
  it would run: harness, model, effort, brief, folder and project, the posture rows off
  the user's defaults, the rows holding a danger value, and the rows this install no
  longer offers. A launch that can't run again (its preset deleted, a value that no longer
  fits) is answered `refused`. New `presets.off_posture_rows`, the comparison the
  resolver's posture check refuses on, and `page.RECENTS`, `page.RECENT`.
- Every launch has a session name. New catalog row `name` (`--name`, or `name` in a
  preset); a launch that sets none is named from a template, the new settings
  `name.template` (shipped `<project>-<model>-<date:mmm-dd hh:mm>`) and `name.templates`
  (a project's own, by project key). The name is recorded in the launch's values, a
  relaunch or resume keeps it, and Claude Code shows it as the display name unless
  `native_name` is set, so every Claude launch now passes `-n`. **`--name` changed
  meaning for `moxi launcher run`:** it was passed on to the harness with a warning and
  is now the launcher's row; a Codex launch is no longer handed it. New module
  `moxi_launcher.names` (the template's pieces, date formats, cleanup and save check,
  `shown(row)`, what a launch is called), which needs no UI shell. `api.preview` names
  the launch as `api.launch` does. The page server answers `GET /api/name`: the name a
  launch of the form would take, a template being edited rendered unsaved with the save
  check's refusal, and what saving it writes. A taken slot's label in `GET /api/targets`
  is the launch's name.
- Each entry of the past-sessions list (`GET /api/sessions`, `moxi launcher sessions
  --json`) names the project its launch ran in, `project_key` (null for a launch in a
  folder alone), and who made it, `actor`. `resume.Past` and `page.PAST` carry both.
- New module `moxi_launcher.live` holds `alive` and `live`, the read of which launches
  run now. They moved out of `moxi_launcher.pagehandlers`, which still offers both names,
  so a caller on an install without the UI shell can read what is alive.
- Home's launch line names the slot the launch takes when the page reads the slots twice
  at once: a press that reads the slots again waits for the newest read asked, where it
  used to go on with the old listing when a later read passed its own.
- `moxi launcher state` is now `moxi launcher show`, with no alias.
- Public names renamed for the naming rules (callers passing the old keyword or reading
  the old name must change): `moxi_launcher.api.check`: `environ` is now `env`;
  `moxi_launcher.api.launch`: `environ` is now `env`; `moxi_launcher.api.launch_kind`:
  `timeout` is now `timeout_s`; `moxi_launcher.api.launch_kinds`: `timeout` is now
  `timeout_s`; `moxi_launcher.api.preview`: `environ` is now `env`.
- `moxi launcher handoffs --key KEY` reads KEY's own handoff folder, the one of KEY's
  newest launch, so it finds the handoffs of a seat that works in another project. A key
  with no launch is read in the folder of the caller's directory, as before. It says on
  stderr which folder it read, and its `--json` names that launch as `launch`. Without
  `--key` nothing changes. New `handoffs.folder_of_key` and `handoffs.newest_launch`.
- The page server answers two new reads. `GET /api/places` gives where the user last
  launched (a project or a folder each, newest first, five at most), whether a launch
  here may name a project, and the user's home folder; `GET /api/targets` gives each slot with whether it is free
  and which launch took it, whether a new slot can be made, and the choice a launch
  starts with, which is never headless. New `store.places` and `store.launched_by`.

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
