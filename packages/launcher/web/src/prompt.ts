// <moxi-launcher-prompt>: the launcher's piece for Home's main spot: a box to say what the
// agent should do, the line above it that says where the session starts, the chip in it
// that says which harness and model run it, and the button that starts it.
//
// Home places it in its spot and expects no report from it: a main piece is shown as it
// is. The caret is in the box as soon as the piece is drawn.
//
// The line above the box reads `Project <key>` or `Folder <path>`. When the piece connects
// it reads `/api/places` and works out once whether projects is there (the page server
// takes a project, and the shell lists projects' picker); the line opens on the newest
// place, or, on a Home that never launched, on the one project, on `Project choose` with
// several, else on the home folder. Its menu takes a typed folder path, lists the places
// (read again each time it opens) and, with projects, places projects' picker under them.
//
// The chip reads `/api/catalog` and `/api/form`: its face is the harness, the model and
// the effort the form is filled with, until one is set in its menu. A read of either that
// failed is made again when the tab is shown again and when the button's menu opens. The menu is three
// lists side by side, the harness first; `older…` opens a field for a model's full name.
// Another harness reads `/api/form` again for it and lets go of the model and the effort
// set here, which are the first harness's own.
//
// `Preset` beside the place, once `/api/presets` lists any, names the one preset the launch
// starts from, `none` dim while none is applied. Its menu takes it off, or applies one of
// the project presets or the seats, each row saying what it sets and where it puts the
// launch; a preset that names a place moves the line there. A project picked on the line,
// or the one it opens on, applies that project's own preset; while a seat is applied a
// picked place is only the place, and the seat stays. The form is read for the applied
// preset, so its values are what the panel and the Changed line count from, and read again
// with no preset for the user's usual values: the permission rows the preset sets off
// those are named on a dim line under the box.
//
// The button names the act and where it runs: `Launch in slot 2`, `Launch in a new slot`,
// `Copy command`, `Launch headless`. Its caret opens the choices `/api/targets` gives. The
// choice starts at that read's default each time the piece is drawn. The listing is read
// when the piece connects, when the menu opens, on every event of the topic `launches`,
// when the tab is shown again, and before a press when the last read is over five seconds
// old.
//
// A press sends the whole form to `/api/preview` and, with its ticket, to `/api/launch`,
// as the launcher's panel does, then reads the 202's status path until the launch's row
// has started or failed. The button is off from the press to the first end; every end
// leaves a line under the box and gives the button back. A launch in the user's terminal
// is never run here: its command is copied. The lines stay until the next press or an
// edit of the box.
//
// `Options`, beside the chip, opens a panel at the window's right edge, drawn here and
// fixed to the window, with the box and the button still in use beside it. The piece tells
// Home the panel's width with `moxi-home-side` when it opens and 0 when it closes or the
// piece leaves, and Home moves its column aside where the window has the room. The panel
// holds a find field and the launch form's other rows in the catalog's groups, each group
// and subgroup opening in place and saying, while closed, which of its values differ from
// the shipped defaults. Each row is drawn by the shell's form renderer; a value changed
// there is one more row this piece sets, and goes in the launch request with the rest.
// The panel draws a row of the harness's only where the place the button launches in
// passes its flag, by the command mode `/api/form` names for that place under the comms
// name the form holds; the form is read again when that name changes. When the button
// is set to another place, a value set on a row the panel no longer draws is let go of.
//
// Tools > Built-in draws the set of built-in tools as its plain list row, then a line for
// each tool the page names with `allow` and `deny`, the one the rules hold boxed. A press
// changes that tool's own entry in the allowed or the denied tools, and the boxed word takes
// it out. A tool's scoped rules are counted on its line and open to a list with `Remove`;
// every other entry of the two rows is listed under `Other rules`.
//
// A row changed in the panel is named by its label on a dim line under the box, `Changed`,
// and counted on the word, `Options · 2`; `Reset` on that line puts every changed row back
// to what the form is filled with and leaves the box, the place, the chip and the button
// as they are. A value that can do harm is in the warning colour wherever it is drawn: the
// row's name and control, the summaries above it, and first on the line under the box.
// Picked in a row's control it is not set until `Turn on` under it is pressed, and it holds
// for one launch: once a launch with it starts, or its command is copied, the row is back
// at the form's fill, and a pick of it asks again. A launch that fails or is refused keeps
// it. A launch whose permission rows are not all at the user's usual values says so in its
// request, and each such row is one of those the lines under and above the box name.
//
// The piece reads `/api/last` when it connects and whenever what the line above the box names
// changes (a place picked, a seat applied or taken off), never on a launch, so nothing fills
// under the user's hands; an answer for a key the line no longer names fills nothing. The
// values that launch gave fill the rows the form leaves at their shipped defaults, the
// harness first, and `From last launch` on the line above the box names them, with `Clear`
// for each in its list and under its row in the panel. A filled value is not a change; one
// the user edits becomes his, and leaves the item. A filled value that can do harm asks
// nothing and stays after a launch. The button is off until the fill is read and made, and
// the request names each filled row.
//
// `Run again` under the box lists the recent launches of what the line names, read when the
// piece connects, when what the line names changes, and on every event of the topic
// `launches`. Pointing at one, or giving it the focus, underlines it and reads `/api/recent`
// with the button's default, and a card under it says what a click runs and where, a value
// that can do harm in the warning colour, and what the click does. A click previews and
// launches that request as the button does, with the lines under the box, or copies its
// command where the default is the user's terminal; it uses the card's answer while it is
// under five seconds old and the default is the same, else reads it again, and does nothing
// for a launch that can't run again. It leaves the box, the panel and the fill as they are.
//
// Seat > Name & notes > Session name, while the panel is open, shows the name `/api/name`
// renders for the seat, the place, the model and the comms name the piece holds, read again
// whenever one of them changes, with a dim sentence naming its template. A name typed over it
// is a changed row; `Use the template` lets go of it. Its caret opens the template's editor,
// each edit rendered unsaved by `/api/name`; `Save template` writes through the shell's own
// settings route, never the launcher's, so a key the shell was started with locked is refused
// in the shell's words, and the name is read again after a save.
//
// A dim `+` at the left of the chip opens a menu drawn like the chip's. `Brief from a file`
// opens a field for a path: while one is set the box is off, a dim line under the box names
// the file with `Clear`, and the launch's first turn is that file, not the box's text. A
// launch that starts with it lets go of it, as it empties the box. `Continue the last
// session` is offered when the menu's read of `/api/sessions`, made each time it opens, holds
// a session of the line's place that he made and can resume now; its dim line says whether
// it resumes in a slot or copies the resume command. A click resumes it at once through
// `/api/resume`, with the lines under the box a launch has, in a resume's words; the box
// keeps its text.

import { css, html, LitElement, nothing } from "lit";
import type { TemplateResult } from "lit";
import { api, components, navigate, place, subscribe } from "/m/ui/client.js";
import type { ApiResult, FieldDescriptor, FormChange, FormValue } from "/m/ui/client.js";

import {
  afterLaunch, ALLOWED, asksFirst, CHANGED, changedValue, changes, DEFAULTS, DENIED, drawnField, findRows, HIDDEN_FLAG,
  isDanger, isToolsView, keptOptions, modeOf, NO_MATCH, NOT_IN_SET, offRows, optionsWord, OTHER_RULES, otherRules, PANEL_WIDTH_PX, panelFields,
  panelGroups, pressTool, RESET, rowLabel, rulesWord, SIDE_EVENT, toolLines, toolLists, turnedOn, valueNow, withOption,
  withoutRule, withTools,
} from "./options-model.ts";
import type {
  Change, Mode, PanelField, PanelGroup, PanelRow, PanelSub, Pending, ToolLine, ToolLists, ToolRule, ToolWord, Word,
} from "./options-model.ts";

import {
  CLEAR, fillFace, fillIn, fillNote, fillOf, fillRows, FROM_LAST, fromLast, HARNESS_ROW, harnessStep, keptFill,
  lastKey, lastWhen, parseLast, withoutFill,
} from "./fill-model.ts";
import type { Last, LastKey } from "./fill-model.ts";
import {
  choiceKey, fieldsFor, filledFields, FOLLOW_FOR_MS, FOLLOW_MS, formPath, NO_PRESET, parseCatalog, parseForm,
  parsePasts, parsePresets, same,
} from "./panel.ts";
import type { Catalog, Choice, Form, LaunchBody, Past, Preset } from "./panel.ts";
import {
  afterPick, atLoad, lastQuery, NO_PRESET_ROW, PRESET, presetFace, presetMenu, presetOff, presetPlace, sameChoice,
} from "./preset-model.ts";
import {
  afterNoAnswer, aroundPage, buttonWords, chipFace, chipMenu, copied, dangerSentences, edited, followed, following, hasEffort, IDLE,
  inFlight, launched,
  LAUNCHER_PAGE, launchRequest, openWhere, parsePlaces, parseTargets, pickValues, placed, placeRows, PRESSED, previewed, runMenu,
  placeTime, resumeSent, runOf, SLOTS_UNREAD, STARTING, stopped, TARGETS_FRESH_MS, typedFolder, valueOf, whereFace,
} from "./prompt-model.ts";
import {
  BRIEF_FILE_FIELD, BRIEF_FROM_FILE, CLEAR_FILE, CONTINUE_LAST, continueWords, lastSession, PLUS_LABEL, resumeBody,
  SESSIONS_READ, takesBriefFile,
} from "./plus-model.ts";
import type { ChipItem, Flight, Line, Pick, Place, PlaceRow, Run, RunRow, Targets } from "./prompt-model.ts";
import {
  againRun, CARD_FRESH_MS, cardAct, leftOutWords, OFF_DEFAULTS, OWN_DEFAULTS, parseRecent, parseRecents, placeWords,
  postureWords, presetWords, ranWhere, recentBody, recentQuery, RUN_AGAIN, runsWith, sameDefault,
} from "./recent-model.ts";
import type { Recent, RecentAnswer, RecentRequest, Shows } from "./recent-model.ts";
import {
  ADD, addList, CANCEL, DATE_FORMATS, DATE_WORD, dateFormat, dateSample, draftOf, EVERY_PROJECT, KEEP_FOR, keepShown,
  NAME_ROW, nameNote, nameQuery, parseName, PIECES_WORD, SAVE, saveBody, saveSaid, tokens, TYPED, USE_TEMPLATE, withDate,
  withoutPiece, withPiece, WRITTEN,
} from "./name-model.ts";
import type { Draft, NameContext, Named, Piece } from "./name-model.ts";

const TAG = "moxi-launcher-prompt";
/** The id of the card under a recent, which names the recent it is for. */
const CARD_ID = "recent-card";

/** `/api/recent`'s answer for one recent, read with the button's default `run`, at `at` by the
 * page's clock. */
interface Card {
  id: string;
  answer: RecentAnswer;
  run: Run;
  at: number;
}
/** The event projects' picker sends its host on a choice, with `{key}`. */
const PICKED = "moxi-change";

const STYLES = css`
  :host { display: block; position: relative; min-width: 0; color: var(--moxi-text); font-family: var(--moxi-font-sans); }
  * { box-sizing: border-box; }
  button { margin: 0; cursor: pointer; }
  .where { position: absolute; left: 0; bottom: calc(100% + 10px); max-width: 100%; padding: 0 2px;
           color: var(--moxi-text-dim); font-size: 14px; line-height: 20px; white-space: nowrap; }
  .where .k { margin-right: 10px; }
  .anchor { position: relative; display: inline-block; }
  .where .anchor { position: static; }
  .face { padding: 0; border: 0; background: none; color: var(--moxi-text); font: inherit; }
  .p { color: var(--moxi-text-dim); }
  .v { position: relative; top: 1px; margin-left: 6px; color: var(--moxi-text-dim); font-size: 15px; line-height: 0; }
  .menu { position: absolute; z-index: 2; left: 0; top: calc(100% + 8px); width: 420px; max-width: calc(100vw - 32px);
          padding: 0 0 6px; border: 1px solid var(--moxi-border); border-radius: 10px;
          background: var(--moxi-bg); color: var(--moxi-text); font: 14px/1.5 var(--moxi-font-sans);
          text-align: left; white-space: normal; }
  .find { display: block; width: 100%; padding: 11px 14px 10px; border: 0; border-bottom: 1px solid var(--moxi-border);
          outline: 0; background: none; color: var(--moxi-text); font: inherit; }
  .find::placeholder { color: var(--moxi-text-dim); opacity: 1; }
  .hint { margin: 0; padding: 7px 14px 0; color: var(--moxi-text-dim); }
  .places { display: grid; margin-top: 6px; }
  .row { display: grid; grid-template-columns: 20px 1fr auto; align-items: baseline; width: 100%; padding: 7px 14px;
         border: 0; background: none; color: var(--moxi-text); font: inherit; text-align: left; }
  .row:hover { background: var(--moxi-bg-hover); }
  .row .name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row .t { padding-left: 10px; color: var(--moxi-text-dim); font-variant-numeric: tabular-nums; }
  .row.d { color: var(--moxi-text-dim); }
  .where .sep { margin: 0 10px; }
  .pre { padding: 0; border: 0; background: none; color: var(--moxi-text); font: inherit; }
  .menu.presets { width: 470px; padding: 4px 0 6px; }
  .presets .row { grid-template-columns: 20px 108px 1fr auto; }
  .presets .row .name { padding-right: 8px; }
  .presets .row .d { min-width: 0; overflow: hidden; color: var(--moxi-text-dim); text-overflow: ellipsis; white-space: nowrap; }
  .presets .row .d .dg { color: var(--moxi-danger); }
  .presets .hd { padding: 8px 14px 0 34px; line-height: 22px; }
  .presets hr { margin: 4px 0 0; border: 0; border-top: 1px solid var(--moxi-border); }
  .chip { padding: 8px 10px; border: 1px solid var(--moxi-border); border-radius: 7px; background: none;
          color: var(--moxi-text); font: 13px/1 var(--moxi-font-mono); white-space: nowrap; }
  .chip .v { margin-left: 8px; }
  .open > .chip { border-color: var(--moxi-border-strong); }
  .menu.model { top: calc(100% + 6px); width: 440px; max-width: calc(100vw - 66px); padding: 8px 0 4px; }
  .cols { display: grid; grid-template-columns: 1.3fr 1fr 1fr; }
  .cols > div { min-width: 0; }
  .hd { padding: 0 10px 0 34px; color: var(--moxi-text-dim); font-size: 13px; line-height: 24px; }
  .model .row { grid-template-columns: 20px 1fr; padding: 6px 10px 6px 14px; }
  .model .find { margin-top: 4px; border-top: 1px solid var(--moxi-border); border-bottom: 0; }
  .all { margin: 0; padding: 7px 14px 4px 34px; color: var(--moxi-text-dim); }
  .picker { max-height: 240px; padding: 0 14px 2px; overflow: auto; }
  .box { display: flex; flex-direction: column; justify-content: space-between; height: 150px;
         padding: 16px 16px 12px; border: 1px solid var(--moxi-border); border-radius: 12px; }
  textarea { flex: 1 1 auto; width: 100%; min-height: 0; margin: 0 0 8px; padding: 0; border: 0; outline: 0;
             resize: none; background: none; color: var(--moxi-text); font: inherit; font-size: 16px;
             line-height: 1.5; }
  textarea::placeholder { color: var(--moxi-text-dim); opacity: 1; }
  .boxrow { display: flex; justify-content: space-between; align-items: center; }
  .left { display: flex; gap: 8px; align-items: center; min-width: 0; }
  .go { padding: 9px 16px; border: 0; border-radius: 7px; background: var(--moxi-accent);
        color: var(--moxi-on-accent); font: 600 14px/1 var(--moxi-font-sans); }
  .go:disabled, .more:disabled { cursor: default; opacity: 0.6; }
  .split { position: relative; display: inline-flex; flex: none; }
  .split .anchor { position: static; display: inline-flex; margin-left: 1px; }
  .split .go { border-radius: 7px 0 0 7px; white-space: nowrap; }
  .more { padding: 9px 10px; border: 0; border-radius: 0 7px 7px 0; background: var(--moxi-accent);
          color: var(--moxi-on-accent); font: 600 14px/1 var(--moxi-font-sans); }
  .more span { position: relative; top: 1px; font-size: 15px; line-height: 0; }
  .menu.run { left: auto; right: 0; width: 440px; max-width: calc(100vw - 49px); padding: 6px 0; }
  .run .row { grid-template-columns: 20px 112px 1fr; }
  .run .row .d { min-width: 0; color: var(--moxi-text-dim); overflow-wrap: anywhere; }
  .run .row.taken { color: var(--moxi-text-dim); cursor: default; }
  .run .row.taken:hover { background: none; }
  .run hr { margin: 6px 0; border: 0; border-top: 1px solid var(--moxi-border); }
  .opt { padding: 8px 6px; border: 0; background: none; color: var(--moxi-text-dim); font: 14px/1 var(--moxi-font-sans);
         font-variant-numeric: tabular-nums; white-space: nowrap; }
  .opt.on { color: var(--moxi-text); }
  .lab { margin: 4px 0; color: var(--moxi-text-dim); font-size: 13px; line-height: 18px; }
  .nfield { display: flex; align-items: center; margin-bottom: 6px; border: 1px solid var(--moxi-border); border-radius: 7px; }
  .nfield.on, .nfield:focus-within { border-color: var(--moxi-border-strong); }
  .nfield input { flex: 1 1 auto; min-width: 0; padding: 6px 0 6px 10px; border: 0; outline: 0; background: none;
              color: var(--moxi-text); font: 13px/20px var(--moxi-font-sans); }
  .nfield input::placeholder { color: var(--moxi-text); opacity: 1; }
  .nfield > button { flex: none; padding: 6px 10px; border: 0; background: none; color: var(--moxi-text-dim); font-size: 15px;
                 line-height: 20px; }
  .tnote { margin: 0; padding: 0 44px 8px 0; color: var(--moxi-text-dim); font-size: 13px; line-height: 18px;
           overflow-wrap: anywhere; }
  .tnote button { padding: 0; border: 0; background: none; color: var(--moxi-text); font: inherit; }
  .tpl { margin: 8px 0 10px; padding-left: 12px; border-left: 1px solid var(--moxi-border); }
  .tl { display: grid; grid-template-columns: 60px minmax(0, 1fr); align-items: center; min-height: 29px; font-size: 13px; }
  .tl > .k { color: var(--moxi-text-dim); }
  .toks { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px; padding: 2px 0; }
  .tok { padding: 4px 7px; border: 1px solid var(--moxi-border-strong); border-radius: 6px; background: none;
         color: var(--moxi-text); font: 13px/1 var(--moxi-font-sans); }
  .pk { padding: 0; border: 0; background: none; color: var(--moxi-text); font: inherit; }
  .pk.d { margin-left: 4px; color: var(--moxi-text-dim); }
  .pk .v { font-size: 14px; }
  .plus { padding: 4px 6px; border: 0; background: none; color: var(--moxi-text-dim); font: 18px/1 var(--moxi-font-sans); }
  .open > .plus { color: var(--moxi-text); }
  .menu.plusm { top: calc(100% + 6px); width: 320px; padding: 6px 0 4px; }
  .plusm .row { grid-template-columns: 20px 1fr; padding: 7px 14px; }
  .plusm .row .d { display: block; color: var(--moxi-text-dim); font-size: 13px; line-height: 18px; }
  .plusm .find { margin-top: 4px; border-top: 1px solid var(--moxi-border); border-bottom: 0; }
  textarea:disabled { color: var(--moxi-text-dim); cursor: default; }
  .changed .file { min-width: 0; overflow-wrap: anywhere; font-family: var(--moxi-font-mono); font-size: 13px; }
  .menu.nmenu { width: 260px; padding: 4px 0; }
  .menu.nmenu[data-menu="name-add"] { left: auto; right: 0; width: 200px; }
  .nmenu .row { grid-template-columns: 1fr auto; padding: 6px 14px; }
  .written { width: 100%; min-width: 0; padding: 2px 0; border: 0; border-bottom: 1px solid var(--moxi-border); outline: 0;
             background: none; color: var(--moxi-text); font: 11px/18px var(--moxi-font-mono); }
  .nref { margin: 2px 0 4px; color: var(--moxi-text); font-size: 13px; line-height: 18px; overflow-wrap: anywhere; }
  .btns { display: flex; justify-content: flex-end; gap: 18px; }
  .btns button { padding: 0; border: 0; background: none; color: var(--moxi-text); font: inherit; }
  .btns button:first-child { color: var(--moxi-text-dim); }
  .btns button:disabled { cursor: default; opacity: 0.5; }
  .menu.fill { width: 540px; padding: 6px 0 0; }
  .fr { display: grid; grid-template-columns: 104px 1fr auto; align-items: baseline; gap: 0 10px; padding: 7px 14px; }
  .fr .d { min-width: 0; color: var(--moxi-text-dim); overflow-wrap: anywhere; }
  .fr.dg .d, .fr.dg > span { color: var(--moxi-danger); }
  .fr button, .came button { padding: 0; border: 0; background: none; color: var(--moxi-text); font: inherit; }
  .fnote { margin: 6px 0 0; padding: 9px 14px 10px; border-top: 1px solid var(--moxi-border); color: var(--moxi-text-dim);
           font-size: 13px; line-height: 18px; }
  .came { display: flex; justify-content: space-between; gap: 12px; margin-top: 6px; color: var(--moxi-text-dim);
          font-size: 13px; line-height: 18px; }
  .came.dg > span { color: var(--moxi-danger); }
  .changed { display: flex; justify-content: space-between; align-items: baseline; gap: 16px; margin: 10px 2px 0;
             color: var(--moxi-text-dim); font-size: 14px; line-height: 20px; }
  .changed .k { margin-right: 10px; }
  .changed b { color: var(--moxi-text); font-weight: 400; }
  .changed .sep { margin: 0 8px; }
  .changed button { flex: none; padding: 0; border: 0; background: none; color: inherit; font: inherit; }
  .dg, .changed b.dg { color: var(--moxi-danger); }
  .prow.dg moxi-ui-form { --moxi-text: var(--moxi-danger); }
  .ask { margin: 6px 0 2px; color: var(--moxi-text-dim); font-size: 13px; line-height: 18px; }
  .ask p { margin: 0; }
  .ask div { display: flex; gap: 14px; margin-top: 6px; }
  .ask button { padding: 0; border: 0; background: none; color: var(--moxi-text-dim); font: inherit; }
  .ask button.on { color: var(--moxi-text); font-weight: 600; }
  .panel { position: fixed; z-index: 5; top: 0; right: 0; bottom: 0; width: ${PANEL_WIDTH_PX}px; max-width: 100vw;
           padding: 20px 20px 0; overflow: auto; border-left: 1px solid var(--moxi-border);
           background: var(--moxi-bg); color: var(--moxi-text); font-size: 14px; }
  .ph { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 14px; }
  .ph b { font-size: 16px; font-weight: 600; }
  .close { padding: 0; border: 0; background: none; color: var(--moxi-text-dim); font: inherit; }
  .pfind { display: block; width: 100%; margin: 0 0 12px; padding: 6px 10px; border: 1px solid var(--moxi-border);
           border-radius: 7px; background: none; color: var(--moxi-text); font: inherit; }
  .pfind::placeholder { color: var(--moxi-text-dim); opacity: 1; }
  .cat, .sub { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; width: 100%;
               border: 0; background: none; color: var(--moxi-text); font: inherit; text-align: left; }
  .cat { height: 38px; padding: 0; border-top: 1px solid var(--moxi-border); line-height: 38px; }
  .cat b { font-weight: 600; }
  .sub { height: 31px; padding: 0 0 0 16px; line-height: 31px; }
  .cat > span:first-child, .sub > span:first-child { flex: none; }
  .ar { display: inline-block; width: 16px; color: var(--moxi-text-dim); font-size: 13px; }
  .sum { min-width: 0; overflow: hidden; color: var(--moxi-text-dim); text-overflow: ellipsis; white-space: nowrap; }
  .catbody { padding-bottom: 8px; }
  .subopen { padding: 0 0 4px 32px; }
  .loose { padding: 0 0 4px 16px; }
  .prow { padding: 6px 0; }
  .at { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 2px; color: var(--moxi-text-dim);
        font-size: 13px; line-height: 18px; }
  .tool { display: flex; justify-content: space-between; align-items: center; gap: 12px; min-height: 29px; }
  .tool .nm { min-width: 0; }
  .tool .nm i, .tool .rc { margin-left: 8px; color: var(--moxi-text-dim); font-size: 13px; font-style: normal; }
  .tool.dim .nm { color: var(--moxi-text-dim); }
  .rc { padding: 0; border: 0; background: none; font-family: inherit; }
  .tri { display: inline-flex; flex: none; }
  .tri button { padding: 4px 7px; border: 1px solid transparent; border-radius: 6px; background: none;
                color: var(--moxi-text-dim); font: 13px/1 var(--moxi-font-sans); }
  .tri button.s { border-color: var(--moxi-border-strong); color: var(--moxi-text); }
  .rules { padding: 0 0 4px 16px; }
  .rule { display: flex; align-items: baseline; gap: 10px; font-size: 13px; line-height: 24px; }
  .rule .re { min-width: 0; overflow-wrap: anywhere; font-family: var(--moxi-font-mono); }
  .rule .rl { flex: none; margin-left: auto; color: var(--moxi-text-dim); }
  .rule button { flex: none; padding: 0; border: 0; background: none; color: var(--moxi-text-dim); font: inherit; }
  .others { margin-top: 6px; }
  .others .oh { color: var(--moxi-text-dim); line-height: 29px; }
  .none { margin: 0; padding: 8px 0; border-top: 1px solid var(--moxi-border); color: var(--moxi-text-dim); }
  .said { margin: 10px 2px 0; font-size: 14px; line-height: 20px; overflow-wrap: anywhere; }
  .said p { margin: 0; }
  .said .dim { color: var(--moxi-text-dim); }
  .said a { color: inherit; }
  .said code { display: block; margin-top: 4px; font: 13px/1.5 var(--moxi-font-mono); user-select: all; }
  .again { display: flex; align-items: baseline; margin: 10px 2px 0; color: var(--moxi-text-dim); font-size: 14px;
            line-height: 20px; white-space: nowrap; }
  .again > .k { flex: none; margin-right: 10px; }
  .again .items { display: flex; min-width: 0; }
  .rec { position: relative; flex: 0 1 auto; min-width: 0; margin-right: 14px; }
  .rec:last-child { margin-right: 0; }
  .rec > button { display: inline-flex; align-items: baseline; max-width: 100%; padding: 0; border: 0; background: none;
                  color: inherit; font: inherit; text-align: left; }
  .rec > button b { min-width: 0; overflow: hidden; color: var(--moxi-text); font-weight: 400; text-overflow: ellipsis;
                    white-space: nowrap; }
  .rec > button.on b { text-decoration: underline; text-decoration-color: var(--moxi-text-dim); text-underline-offset: 3px; }
  .rec .t { flex: none; margin-left: 6px; font-variant-numeric: tabular-nums; }
  .card { position: absolute; z-index: 2; left: -14px; top: calc(100% + 8px); width: 400px; max-width: calc(100vw - 32px);
          padding: 11px 14px 10px; border: 1px solid var(--moxi-border-strong); border-radius: 10px;
          background: var(--moxi-bg); color: var(--moxi-text-dim); font-size: 14px; line-height: 20px; white-space: normal; }
  .card p { margin: 0; }
  .card .q { margin-bottom: 5px; color: var(--moxi-text); overflow-wrap: anywhere; }
  .card .r { display: flex; justify-content: space-between; gap: 12px; white-space: nowrap; }
  .card .r > span:first-child { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
  .card .l { overflow-wrap: anywhere; }
  .card .l .k { margin-right: 8px; }
  .card .l b { color: var(--moxi-text); font-weight: 400; }
  .card .l b.dg { color: var(--moxi-danger); }
  .card hr { margin: 9px -14px 8px; border: 0; border-top: 1px solid var(--moxi-border); }
  .card .act { color: var(--moxi-text); }
  .card .cant { color: var(--moxi-text); overflow-wrap: anywhere; }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
  textarea:focus-visible, .find:focus-visible { outline: 0; }
  [hidden] { display: none; }
  @media (max-width: 720px) {
    .where { position: relative; margin-bottom: 10px; white-space: normal; overflow-wrap: anywhere; }
    .where .k { white-space: nowrap; }
    .box { height: auto; min-height: 150px; }
    textarea { min-height: 64px; }
    .boxrow { flex-wrap: wrap; justify-content: flex-end; row-gap: 10px; }
    .left { flex: 1 0 100%; }
    .run .row { grid-template-columns: 20px 96px 1fr; }
    .presets .row { grid-template-columns: 20px 1fr auto; }
    .presets .row .d { grid-row: 2; grid-column: 2 / 4; }
    .fr { grid-template-columns: 1fr auto; }
    .fr .d { grid-row: 2; grid-column: 1 / 3; }
    .again { position: relative; display: grid; grid-template-columns: auto 1fr; margin-top: 6px; }
    .again > .k { padding: 6px 0; }
    .again .items { display: block; }
    .rec { position: static; display: block; margin-right: 0; }
    .rec > button { display: flex; justify-content: space-between; width: 100%; padding: 6px 0; }
    .rec .card { left: 0; width: auto; right: 0; max-width: none; }
  }
`;

export class LauncherPrompt extends LitElement {
  static styles = STYLES;

  static properties = {
    brief: { state: true },
    pick: { state: true },
    places: { state: true },
    menu: { state: true },
    typed: { state: true },
    fields: { state: true },
    chosen: { state: true },
    older: { state: true },
    targets: { state: true },
    picked: { state: true },
    flight: { state: true },
    unread: { state: true },
    panel: { state: true },
    query: { state: true },
    opened: { state: true },
    options: { state: true },
    pending: { state: true },
    presets: { state: true },
    preset: { state: true },
    baseFields: { state: true },
    filled: { state: true },
    last: { state: true },
    recents: { state: true },
    pointed: { state: true },
    card: { state: true },
    named: { state: true },
    draft: { state: true },
    nameSaid: { state: true },
    saving: { state: true },
    briefFile: { state: true },
    fileField: { state: true },
    pasts: { state: true },
  };

  /** The box's text. */
  brief = "";
  /** Where the launch starts; null until the places are read. */
  pick: Pick | null = null;
  places: Place[] = [];
  /** The menu that is open: the line's (`where`), the chip's (`chip`), the button's
   * (`run`), or none. */
  menu: "" | "where" | "chip" | "run" | "preset" | "fill" | "name-add" | "name-date" | "plus" = "";
  /** A typed folder path the menu did not take. */
  typed = "";
  /** The form's fields for the harness, filled from `/api/form`; empty until both are read. */
  fields: FieldDescriptor[] = [];
  /** The model and the effort set in the chip's menu, null for one emptied there. */
  chosen: Record<string, FormValue> = {};
  /** The field for a model's full name is open. */
  older = false;
  /** Where a launch may run, as last read; null until the first read answers. */
  targets: Targets | null = null;
  /** The choice made in the button's menu; null is the listing's default. */
  picked: Run | null = null;
  /** The launch the button started, and the lines under the box. */
  flight: Flight = IDLE;
  /** Why the launch form could not be read, when it could not. */
  unread = "";
  /** The Options panel is open. */
  panel = false;
  /** The text in the panel's find field. */
  query = "";
  /** The panel's open groups, by key, and open subgroups, by `<group key>/<subgroup>`. */
  opened: ReadonlySet<string> = new Set();
  /** The values set in the panel, by row; a row not named here keeps what the form is
   * filled with. */
  options: Record<string, FormValue> = {};
  /** A value that can do harm, picked in its row and waiting for `Turn on`. */
  pending: Pending | null = null;
  /** The seats and project presets `/api/presets` lists; empty until it answers. */
  presets: Preset[] = [];
  /** The preset the launch starts from: a seat, a project preset, or none. */
  preset: Choice = NO_PRESET;
  /** The same harness's form read with no preset, whose fill is each row's usual value;
   * the form's own fields while no preset is applied. */
  baseFields: FieldDescriptor[] = [];
  /** The values filled from the last launch and left as they were, by row; `harness` when the
   * harness was filled too. */
  filled: Record<string, FormValue> = {};
  /** The last launch the fill was made from, with the key it was read for; null with none. */
  last: (Last & { key: LastKey }) | null = null;
  /** The earlier launches `Run again` lists, newest first; empty until read. */
  recents: Recent[] = [];
  /** The recent the pointer or the focus is on; "" for none. */
  pointed = "";
  /** The latest answer read for a recent's card. */
  card: Card | null = null;
  /** The session name as `/api/name` last answered, for the template being edited while one is. */
  named: Named | null = null;
  /** The template being edited in the name's editor; null while it is closed. */
  draft: Draft | null = null;
  /** Why the last save of a template did not write, in the shell's words; "" for none. */
  nameSaid = "";
  /** A template is being saved. */
  saving = false;
  /** The brief file set through the `+`; "" for none, when the box's text is the first turn. */
  briefFile = "";
  /** The `+` menu's field for a brief file's path is open. */
  fileField = false;
  /** The past sessions the `+` menu read when it opened; null until that read answers. */
  pasts: Past[] | null = null;
  #pastsAsked = 0;
  /** Who the page server launches as, by `/api/places`; "" until it is read. */
  #actor = "";
  /** The brief file sent with the press; null when none was. */
  #sentFile: string | null = null;
  #nameAsked = 0;
  /** The query the latest read of the name was for; null to read it again at the next update. */
  #nameKey: string | null = null;
  #recentsAsked = 0;
  #cardAsked = 0;
  /** A click on a recent is reading what it runs. */
  #again = false;
  /** The query the latest read of the last launch was for; undefined until the piece reads
   * one after it connects. */
  #lastQuery: string | null | undefined = undefined;
  #lastAsked = 0;
  /** The read of the last launch for the line's key has answered. */
  #lastRead = false;
  /** The last launch read, waiting for the form for the applied preset to fill over. */
  #waiting: (Last & { key: LastKey }) | null = null;
  /** The harness the fill asked the form to be read for, while it waits for that form. */
  #fillHarness = "";
  /** When the read that gave the listing was asked, by the page's clock; 0 when the last
   * read failed or left the slots unread, so the next press reads again. */
  #targetsAt = 0;
  #targetsAsked = 0;
  /** The latest read of the listing asked. */
  #targetsRead: Promise<void> = Promise.resolve();
  /** Counts presses, so an answer for an earlier launch is dropped. */
  #pressed = 0;
  /** The box's text when the button was pressed; null for a recent run again, a resume, or a
   * launch with a brief file, which send none of it. */
  #sent: string | null = "";
  /** The values that can do harm turned on when the button was pressed, by row. */
  #sentOn: Record<string, string> = {};
  #followTimer: ReturnType<typeof setInterval> | undefined;
  #followSince = 0;
  #stops: (() => void)[] = [];
  #catalog: Catalog | null = null;
  #form: Form | null = null;
  /** The preset the form was read for. */
  #formChoice: Choice = NO_PRESET;
  /** The form read with no preset for the form's harness; null while it is the form. */
  #base: Form | null = null;
  #presetsRead = false;
  /** Counts `/api/form` reads, so only the latest one's answer is drawn. */
  #asked = 0;
  /** The latest `/api/form` read has not ended yet. */
  #formIn = false;
  /** The comms name the latest `/api/form` read was asked under; "" for none. */
  #identityAsked = "";
  /** Whether projects is there, worked out once when the piece connects. */
  #withProjects = false;
  /** The user's home folder as the page server names it, drawn as `~`. */
  #home = "";
  /** Holds projects' picker, placed the first time the line's menu opens. */
  #picker = document.createElement("div");
  #pickerPlaced = false;
  #opened = 0;

  #shown = (): void => {
    if (document.visibilityState !== "visible") return;
    void this.#readTargets();
    this.#readUnread();
  };

  #outside = (event: Event): void => {
    const open = this.renderRoot.querySelector(".anchor.open");
    if (open !== null && !event.composedPath().includes(open)) this.menu = "";
  };

  constructor() {
    super();
    this.#picker.className = "picker";
    this.#picker.addEventListener(PICKED, (event) => {
      event.stopPropagation();
      const key = (event as CustomEvent).detail?.key;
      if (typeof key === "string" && key !== "") this.#choose({ kind: "project", key });
    });
  }

  connectedCallback(): void {
    super.connectedCallback();
    document.addEventListener("pointerdown", this.#outside);
    document.addEventListener("visibilitychange", this.#shown);
    // Every launch through the launcher writes the log, and may take or free a slot.
    this.#stops.push(subscribe("launcher", "launches", () => {
      void this.#readTargets();
      this.#readRecents(this.#lastQuery ?? null);
      // A name holding the number among live launches takes the next free one.
      this.#nameKey = null;
      this.requestUpdate();
    }));
    this.#lastQuery = undefined;
    void this.#open();
    void this.#readCatalog();
    void this.#readForm("");
    void this.#readTargets();
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    document.removeEventListener("pointerdown", this.#outside);
    document.removeEventListener("visibilitychange", this.#shown);
    for (const stop of this.#stops.splice(0)) stop();
    this.#opened += 1;
    this.#asked += 1;
    this.#targetsAsked += 1;
    this.#lastAsked += 1;
    this.#recentsAsked += 1;
    this.#cardAsked += 1;
    this.#nameAsked += 1;
    this.#pastsAsked += 1;
    this.#nameKey = null;
    this.#pressed += 1;
    this.#stopFollow();
    if (this.panel) {
      this.panel = false;
      this.#side(0);
    }
  }

  /** Lets go of each value set on a row the panel no longer draws, once the place the
   * button launches in, or the form, is another. */
  protected willUpdate(changed: Map<string, unknown>): void {
    const query = lastQuery(this.preset, this.pick);
    if (query !== this.#lastQuery) {
      this.#readLast(query);
      this.#readRecents(query);
    }
    if (!["fields", "targets", "picked", "options"].some((name) => changed.has(name))) return;
    const mode = this.#mode;
    if (mode === undefined || this.fields.length === 0) return;
    const kept = keptOptions(this.options, this.fields as PanelField[], mode);
    if (Object.keys(kept).length !== Object.keys(this.options).length) this.options = kept;
    const held = this.pending?.name;
    if (held !== undefined && !panelFields(this.fields as PanelField[], mode).some((field) => field.name === held)) {
      this.pending = null;
    }
  }

  protected updated(changed: Map<string, unknown>): void {
    if (changed.has("panel") && changed.get("panel") !== undefined) this.#side(this.panel ? PANEL_WIDTH_PX : 0);
    // The modes of a launch under a comms name are not those of one under none.
    if (this.#form !== null && this.fields.length > 0 && this.#identity !== this.#identityAsked) {
      void this.#readForm(this.harness);
    }
    // The name follows what it is rendered for, while the panel that shows it is open.
    if (this.panel && this.fields.length > 0) {
      const query = nameQuery(this.#nameContext, this.draft);
      if (query !== this.#nameKey) this.#readName(query);
    } else {
      this.#nameKey = null;
    }
  }

  /** The comms name the form holds now; "" for none. */
  get #identity(): string {
    return valueOf(this.fields, this.set, "identity");
  }

  /** The command mode of the place the button launches in now, by the form's last read;
   * undefined until the places a launch may run in are read. */
  get #mode(): Mode {
    if (this.targets === null || this.#form === null) return undefined;
    return modeOf(this.#form.modes, runOf(this.targets, this.picked).target);
  }

  /** Tells Home how wide the panel beside its column is now; 0 is no panel. */
  #side(width: number): void {
    this.dispatchEvent(new CustomEvent(SIDE_EVENT, { bubbles: true, composed: true, detail: { width } }));
  }

  protected firstUpdated(): void {
    this.#box()?.focus();
  }

  #box(): HTMLTextAreaElement | null {
    return this.renderRoot.querySelector("textarea");
  }

  /** Whether a launch has all it needs from this piece: a place. */
  get ready(): boolean {
    return this.pick !== null && placed(this.pick);
  }

  /** The harness the form is drawn for; "" until `/api/form` has answered. */
  get harness(): string {
    return this.#form?.harness ?? "";
  }

  /** The rows this piece sets, over what `/api/form` fills: the brief, a brief file, the place, the
   * model and the effort once the chip's menu set them, each value set in the panel, and
   * under those the values filled from the last launch. */
  get set(): Record<string, FormValue> {
    return { ...this.#fill, ...this.options, ...this.chosen, brief: this.brief === "" ? null : this.brief,
             ...(this.briefFile === "" ? {} : { brief_file: this.briefFile }),
             ...(this.pick === null ? {} : pickValues(this.pick)) };
  }

  /** The filled values a launch in the button's place takes now. */
  get #fill(): Record<string, FormValue> {
    return fillIn(this.filled, this.fields as PanelField[], this.#mode);
  }

  /** Each panel row's value now: the one set in the panel, else the one filled, else the
   * form's fill. */
  get #values(): Record<string, FormValue> {
    return { ...this.#fill, ...this.options };
  }

  async #readCatalog(): Promise<void> {
    const mine = this.#opened;
    const answer = await api("launcher", "api/catalog");
    if (mine !== this.#opened) return;
    this.#catalog = answer.status === 200 ? parseCatalog(answer.data) : null;
    if (this.#catalog === null || this.#catalog.state === "error") this.unread = unreadWhy(answer, this.#catalog);
    this.#refill();
  }

  /** Reads `/api/form` for the applied preset, for `harness` when one is asked, and with a
   * preset applied reads it again with none for the same harness. An answer that can't be
   * read leaves the form as it was. A read the fill asks for (`fill`) takes no form that is
   * refused; another harness read for any other reason lets go of the filled values, which
   * are the first harness's own. */
  async #readForm(harness: string, fill = false): Promise<void> {
    const asked = (this.#asked += 1);
    this.#formIn = true;
    try {
      await this.#formRead(asked, harness, fill);
    } finally {
      // The fill waits for the latest read of the form, however it ended.
      if (asked === this.#asked) {
        this.#formIn = false;
        this.#settle();
      }
    }
  }

  async #formRead(asked: number, harness: string, fill: boolean): Promise<void> {
    const identity = this.fields.length === 0 ? "" : this.#identity;
    this.#identityAsked = identity;
    const choice = this.preset;
    const reads = async (path: string): Promise<Form | null> => {
      const answer = await api("launcher", path);
      const form = answer.status === 200 ? parseForm(answer.data) : null;
      if (form !== null && form.state !== "error") return form;
      if (asked === this.#asked && (this.#form === null || !sameChoice(this.#formChoice, choice))) {
        this.unread = unreadWhy(answer, form);
      }
      return null;
    };
    const form = await reads(formPath(choice, harness, identity));
    if (asked !== this.#asked) return;
    const base = form === null || choice.kind === "" ? null : await reads(formPath(NO_PRESET, form.harness, identity));
    if (asked !== this.#asked) return;
    if (form === null || (choice.kind !== "" && base === null) || (fill && form.refused !== null)) {
      if (fill) this.#filledNothing();
      return;
    }
    const other = this.#form !== null && form.harness !== this.#form.harness;
    if (other) this.chosen = {};
    if (other && !fill) this.filled = {};
    this.#form = form;
    this.#formChoice = choice;
    this.#base = base;
    this.#refill();
    // A row the other harness has no place for in the panel is no longer set.
    if (other && this.fields.length > 0) {
      this.options = keptOptions(this.options, this.fields);
      this.pending = null;
    }
  }

  /** Reads the catalog and the form again, whichever the last read of it left unread. */
  #readUnread(): void {
    if (this.#catalog === null || this.#catalog.state === "error") void this.#readCatalog();
    if (this.#form === null || !sameChoice(this.#formChoice, this.preset)) void this.#readForm(this.harness);
    if (!this.#presetsRead) void this.#readPresets();
  }

  #refill(): void {
    if (this.#form === null) return;
    if (this.#catalog !== null && this.#catalog.state === "up") this.unread = "";
    this.fields = filledFields(fieldsFor(this.#catalog, this.#form.harness), this.#form, this.#formChoice);
    this.baseFields = this.#base === null ? this.fields
      : filledFields(fieldsFor(this.#catalog, this.#base.harness), this.#base, NO_PRESET);
    this.#settle();
  }

  // --- the fill from the last launch -----------------------------------------------------------

  /** Reads the last launch of what the line names now, `query`, and lets go of what was filled
   * from another; null names nothing to read. Only the latest read's answer fills. */
  #readLast(query: string | null): void {
    const asked = (this.#lastAsked += 1);
    this.#lastQuery = query;
    // A harness the last key's launch gave goes with it: the layers' is read again.
    if (Object.hasOwn(this.filled, HARNESS_ROW)) void this.#readForm("");
    this.filled = {};
    this.last = null;
    this.#waiting = null;
    this.#fillHarness = "";
    this.#lastRead = query === null;
    if (query === null) return;
    const key = lastKey(query) as LastKey;
    void api("launcher", `api/last?${query}`).then((answer) => {
      if (asked !== this.#lastAsked) return;
      const read = answer.status === 200 ? parseLast(answer.data) : null;
      this.#lastRead = true;
      if (read !== null && read.state === "up" && read.at !== null) this.#waiting = { ...read, key };
      this.#settle();
      this.requestUpdate();
    });
  }

  /** Fills from the last launch waiting, once the form for the applied preset is read; else
   * keeps of the filled values those the form still leaves at their shipped defaults. */
  #settle(): void {
    const form = this.#form;
    if (this.#formIn || form === null || this.fields.length === 0 || !sameChoice(this.#formChoice, this.preset)) return;
    const last = this.#waiting;
    if (last === null) {
      const kept = keptFill(this.filled, this.fields as PanelField[], form);
      if (Object.keys(kept).length !== Object.keys(this.filled).length) this.filled = kept;
      return;
    }
    const step = harnessStep(last, form);
    if (step === "none" || (step === "read" && this.#fillHarness === last.harness)) {
      this.#filledNothing();
      return;
    }
    if (step === "read") {
      this.#fillHarness = last.harness as string;
      void this.#readForm(this.#fillHarness, true);
      return;
    }
    const held = new Set([...Object.keys(this.options), ...Object.keys(this.chosen), this.pending?.name ?? ""]);
    const values = fillOf(last, this.fields as PanelField[], form, held);
    this.filled = this.#fillHarness === form.harness ? { [HARNESS_ROW]: form.harness, ...values } : values;
    this.last = last;
    this.#waiting = null;
    this.#fillHarness = "";
  }

  /** The last launch waiting fills nothing. */
  #filledNothing(): void {
    this.#waiting = null;
    this.#fillHarness = "";
    this.requestUpdate();
  }

  /** `Clear` on a filled value: the row is back at what the form is filled with. The harness
   * takes every filled value with it, and the form is read again for the layers' harness. */
  #clear(name: string): void {
    if (name === HARNESS_ROW) {
      this.filled = {};
      void this.#readForm("");
    } else {
      this.filled = withoutFill(this.filled, name);
    }
    if (Object.keys(this.filled).length > 0) return;
    this.menu = this.menu === "fill" ? "" : this.menu;
    void this.updateComplete.then(() => this.#box()?.focus());
  }

  /** A filled row the user edits is his change from now on, no longer filled. */
  #edited(...names: string[]): void {
    if (names.some((name) => Object.hasOwn(this.filled, name))) this.filled = withoutFill(this.filled, ...names);
  }

  /** Reads the presets; a read that fails leaves the ones read before. */
  async #readPresets(): Promise<void> {
    const mine = this.#opened;
    const answer = await api("launcher", "api/presets");
    const read = answer.status === 200 ? parsePresets(answer.data) : null;
    if (mine !== this.#opened || read === null || read.state !== "up") return;
    this.#presetsRead = true;
    this.presets = read.presets;
  }

  /** The listed preset `choice` names, if any. */
  #presetOf(choice: Choice): Preset | null {
    return this.presets.find((preset) => preset.kind === choice.kind && preset.name === choice.name) ?? null;
  }

  /** Applies a preset, or none, and reads the form for it. The chip's choices go, since the
   * preset gives the model and the effort; values set in the panel stay. */
  #apply(choice: Choice): void {
    if (sameChoice(choice, this.preset)) return;
    this.preset = choice;
    this.chosen = {};
    this.pending = null;
    void this.#readForm(this.#presetOf(choice)?.harness ? "" : this.harness);
  }

  /** Reads the places and sets the line, once for each time the piece is connected. */
  async #open(): Promise<void> {
    const mine = (this.#opened += 1);
    const picker = components("projects").some((item) => item.name === "picker");
    const [where] = await Promise.all([openWhere(api, picker), this.#readPresets()]);
    if (mine !== this.#opened) return;
    this.#withProjects = where.withProjects;
    this.#home = where.home;
    this.#actor = where.actor;
    this.places = where.places;
    // A place he picked while the read was under way stands; the one opened on counts as
    // picked, so a project's own preset is applied.
    if (this.pick !== null) return;
    this.pick = where.pick;
    this.#apply(atLoad(where.pick, this.presets));
  }

  #toggleChip(): void {
    this.menu = this.menu === "chip" ? "" : "chip";
    this.older = false;
  }

  /** Sets the model or the effort; an empty value is the row's empty state. */
  #setChip(name: string, value: string): void {
    this.#edited(name);
    this.chosen = { ...this.chosen, [name]: value === "" ? null : value };
    this.older = false;
  }

  #setHarness(value: string): void {
    this.older = false;
    if (value !== this.harness) void this.#readForm(value);
  }

  #openOlder(): void {
    this.older = true;
    void this.updateComplete.then(() => this.renderRoot.querySelector<HTMLInputElement>(".model .find")?.focus());
  }

  #olderKey(event: KeyboardEvent): void {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const name = (event.target as HTMLInputElement).value.trim();
    if (name !== "") this.#setChip("model", name);
  }

  #toggleWhere(): void {
    this.menu = this.menu === "where" ? "" : "where";
    if (this.menu !== "where") return;
    this.typed = "";
    if (this.#withProjects && !this.#pickerPlaced) {
      this.#pickerPlaced = true;
      void place(this.#picker, "projects", "picker");
    }
    void this.#readPlaces();
    void this.updateComplete.then(() => this.renderRoot.querySelector<HTMLInputElement>(".find")?.focus());
  }

  async #readPlaces(): Promise<void> {
    const mine = this.#opened;
    const answer = await api("launcher", "api/places");
    const read = answer.status === 200 ? parsePlaces(answer.data) : null;
    if (read === null || mine !== this.#opened) return;
    this.places = read.places;
    if (read.actor !== "") this.#actor = read.actor;
  }

  /** Takes a pick, with the preset it applies, closes the menu and puts the caret back in
   * the box. */
  #choose(pick: Pick): void {
    this.pick = pick;
    this.menu = "";
    this.#apply(afterPick(this.preset, pick, this.presets));
    void this.updateComplete.then(() => this.#box()?.focus());
  }

  #togglePreset(): void {
    this.menu = this.menu === "preset" ? "" : "preset";
    if (this.menu === "preset") void this.#readPresets();
  }

  /** Applies a preset picked in the menu, or none, and moves the line to the place it names. */
  #pickPreset(choice: Choice): void {
    this.menu = "";
    const preset = this.#presetOf(choice);
    const place = preset === null ? null : presetPlace(preset, this.#withProjects);
    if (place !== null) this.pick = place;
    this.#apply(choice);
    void this.updateComplete.then(() => this.#box()?.focus());
  }

  #typedKey(event: KeyboardEvent): void {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const field = event.target as HTMLInputElement;
    const path = typedFolder(field.value);
    if (path === null) this.typed = field.value.trim();
    else this.#choose({ kind: "folder", path });
  }

  #keys(event: KeyboardEvent): void {
    if (event.key !== "Escape" || (this.menu === "" && !this.panel)) return;
    event.stopPropagation();
    if (this.menu === "") {
      this.#closePanel();
      return;
    }
    const face = this.renderRoot.querySelector<HTMLElement>(".anchor.open > button");
    this.menu = "";
    face?.focus();
  }

  render() {
    return html`<div class="piece" @keydown=${this.#keys}>
      ${this.pick === null ? nothing : this.#where(this.pick)}
      <div class="box">
        <textarea aria-label="What should the agent do?" placeholder="What should the agent do?"
          .value=${this.brief} ?disabled=${this.briefFile !== ""}
          @input=${(event: Event) => this.#edit((event.target as HTMLTextAreaElement).value)}></textarea>
        <div class="boxrow"><div class="left">${this.#plus()}${this.#chip()}${this.#optionsWord()}</div>${this.#button()}</div>
      </div>
      ${this.#fileLine()}
      ${this.#changed()}
      ${this.#presetLine()}
      ${this.#said()}
      ${this.#recentLine()}
      ${this.panel ? this.#panel() : nothing}
    </div>`;
  }

  #where(pick: Pick) {
    const face = whereFace(pick, this.#home);
    const open = this.menu === "where";
    return html`<div class="where" data-word=${face.word}><span class="k">${face.word}</span><span
      class=${open ? "anchor open" : "anchor"}><button type="button" class="face" aria-haspopup="true"
        aria-expanded=${open ? "true" : "false"} @click=${this.#toggleWhere}>${this.#folder(face.dim, face.name)}<span
        class="v" aria-hidden="true">▾</span></button>${open ? this.#whereMenu(pick) : nothing}</span>${
        this.presets.length > 0 ? this.#presetItem() : nothing}${this.#fillItem()}</div>`;
  }

  /** When the last launch the fill came from was made, as the page says it. */
  get #lastWhen(): string {
    return this.last === null ? "" : lastWhen(this.last.at ?? "", new Date());
  }

  /** `· From last launch <label>` on the line, once a value is filled, and its list while
   * it is open: each value with `Clear`, then where the values came from. */
  #fillItem() {
    const rows = fillRows(this.#fill, this.fields as PanelField[], this.#home);
    const face = fillFace(rows);
    if (face === null || this.last === null) return nothing;
    const open = this.menu === "fill";
    return html`<span class="sep" aria-hidden="true">·</span><span class="k">${FROM_LAST}</span><span
      class=${open ? "anchor open" : "anchor"} data-item="fill"><button type="button" class="pre" aria-haspopup="true"
        aria-expanded=${open ? "true" : "false"} aria-label=${`${FROM_LAST} ${face.text}`}
        @click=${() => { this.menu = open ? "" : "fill"; }}><span class=${face.danger ? "dg" : ""}>${face.text}</span><span
        class="v" aria-hidden="true">▾</span></button>${open ? html`<div class="menu fill" data-menu="fill">
        ${rows.map((row) => html`<div class=${row.danger ? "fr dg" : "fr"} data-fill=${row.name}><span>${row.label}</span><span
          class="d">${row.text}</span><button type="button" aria-label=${`${CLEAR} ${row.label}`}
          @click=${() => this.#clear(row.name)}>${CLEAR}</button></div>`)}
        <p class="fnote">${fillNote(this.last.key, this.#lastWhen, rows.length)}</p></div>` : nothing}</span>`;
  }

  /** `· Preset <name>` on the line, and its menu while it is open. */
  #presetItem() {
    const face = presetFace(this.preset);
    const open = this.menu === "preset";
    return html`<span class="sep" aria-hidden="true">·</span><span class="k">${PRESET}</span><span
      class=${open ? "anchor open" : "anchor"} data-item="preset"><button type="button" class="pre" aria-haspopup="true"
        aria-expanded=${open ? "true" : "false"} aria-label=${`${PRESET} ${face.name}`} @click=${this.#togglePreset}>${
        face.dim ? html`<span class="p">${face.name}</span>` : face.name}<span class="v" aria-hidden="true">▾</span></button>${
        open ? this.#presetMenu() : nothing}</span>`;
  }

  #presetMenu() {
    const fieldsOf = (harness: string | null) => fieldsFor(this.#catalog, harness ?? this.harness) as PanelField[];
    const sections = presetMenu(this.presets, this.preset, this.pick, this.#withProjects, this.#home, fieldsOf);
    return html`<div class="menu presets" data-menu="preset">
      ${this.#presetRow(NO_PRESET, NO_PRESET_ROW, [], "", this.preset.kind === "")}<hr>
      ${sections.map((section) => html`<div class="hd">${section.title}</div>${section.rows.map((row) =>
        this.#presetRow(row.choice, row.name, row.sets, row.place, row.current))}`)}
    </div>`;
  }

  #presetRow(choice: Choice, name: string, sets: Word[], place: string, current: boolean) {
    return html`<button type="button" class="row" data-preset=${choiceKey(choice)}
      data-current=${current ? "true" : "false"} @click=${() => this.#pickPreset(choice)}><span
      aria-hidden="true">${current ? "✓" : ""}</span><span class="name">${name}</span><span class="d">${
      sets.map((word, at) => html`${at === 0 ? "" : " · "}<span class=${word.danger ? "dg" : ""}>${word.text}</span>`)}</span><span
      class="t">${place}</span></button>`;
  }

  #folder(dim: string, name: string) {
    return html`${dim === "" ? nothing : html`<span class="p">${dim}</span>`}${name}`;
  }

  #whereMenu(pick: Pick) {
    const rows = placeRows(this.places, this.#withProjects, pick, this.#home, new Date());
    return html`<div class="menu" data-menu="where">
      <input class="find" type="text" aria-label="Type a folder path" placeholder="Type a folder path"
        autocomplete="off" spellcheck="false" @keydown=${this.#typedKey}>
      ${this.typed === "" ? nothing
        : html`<p class="hint" role="status">Give an absolute path, or one starting with ~</p>`}
      <div class="places">${rows.map((row) => this.#placeRow(row))}</div>
      ${this.#withProjects ? html`<p class="all">All projects</p>${this.#picker}` : nothing}
    </div>`;
  }

  #placeRow(row: PlaceRow) {
    return html`<button type="button" class="row" data-current=${row.current ? "true" : "false"}
      @click=${() => this.#choose(row.pick)}><span aria-hidden="true">${row.current ? "✓" : ""}</span><span
      class="name">${this.#folder(row.dim, row.name)}</span><span class="t">${row.time}</span></button>`;
  }

  #chip() {
    if (this.fields.length === 0) return nothing;
    const chosen = { ...this.#fill, ...this.chosen };
    const model = valueOf(this.fields, chosen, "model");
    const effort = hasEffort(this.fields) ? valueOf(this.fields, chosen, "effort") : null;
    const open = this.menu === "chip";
    return html`<span class=${open ? "anchor open" : "anchor"}><button type="button" class="chip"
      aria-haspopup="true" aria-expanded=${open ? "true" : "false"} aria-label="Harness and model"
      @click=${this.#toggleChip}>${chipFace(this.harness, model, effort)}<span class="v"
      aria-hidden="true">▾</span></button>${open ? this.#chipMenu(model, effort ?? "") : nothing}</span>`;
  }

  #chipMenu(model: string, effort: string) {
    const menu = chipMenu(this.fields, this.harness, model, effort);
    const list = (items: ChipItem[], pick: (value: string) => void) => items.map((item) =>
      html`<button type="button" class="row" data-current=${item.current ? "true" : "false"}
        @click=${() => pick(item.value)}><span aria-hidden="true">${item.current ? "✓" : ""}</span><span
        class="name">${item.label}</span></button>`);
    return html`<div class="menu model" data-menu="chip">
      <div class="cols">
        <div data-list="harness"><div class="hd">Harness</div>${list(menu.harness, (value) => this.#setHarness(value))}</div>
        <div data-list="model"><div class="hd">Model</div>${list(menu.model, (value) => this.#setChip("model", value))}
          <button type="button" class="row d" data-current=${menu.typed === "" ? "false" : "true"}
            @click=${this.#openOlder}><span aria-hidden="true">${menu.typed === "" ? "" : "✓"}</span><span
            class="name">older…</span></button></div>
        ${menu.effort === null ? nothing : html`<div data-list="effort"><div class="hd">Effort</div>${
          list(menu.effort, (value) => this.#setChip("effort", value))}</div>`}
      </div>
      ${this.older ? html`<input class="find" type="text" aria-label="A model's full name"
        placeholder="A model's full name" autocomplete="off" spellcheck="false" .value=${menu.typed}
        @keydown=${this.#olderKey}>` : nothing}
    </div>`;
  }

  // --- the `+` ------------------------------------------------------------------------------

  /** The dim `+` at the left of the chip, and its menu while it is open. */
  #plus() {
    if (this.fields.length === 0) return nothing;
    const open = this.menu === "plus";
    return html`<span class=${open ? "anchor open" : "anchor"}><button type="button" class="plus" aria-haspopup="true"
      aria-expanded=${open ? "true" : "false"} aria-label=${PLUS_LABEL} @click=${this.#togglePlus}>+</button>${
      open ? this.#plusMenu() : nothing}</span>`;
  }

  /** Opens or closes the `+` menu; each open reads the past sessions again. */
  #togglePlus(): void {
    this.menu = this.menu === "plus" ? "" : "plus";
    this.fileField = false;
    if (this.menu === "plus") void this.#readPasts();
  }

  async #readPasts(): Promise<void> {
    const asked = (this.#pastsAsked += 1);
    this.pasts = null;
    const answer = await api("launcher", SESSIONS_READ);
    if (asked !== this.#pastsAsked) return;
    const read = answer.status === 200 ? parsePasts(answer.data) : null;
    this.pasts = read !== null && read.state === "up" ? read.sessions : [];
  }

  #plusMenu() {
    const last = lastSession(this.pasts ?? [], this.pick, this.#actor, this.#home);
    const file = takesBriefFile(this.fields);
    return html`<div class="menu plusm" data-menu="plus">
      ${file ? html`<button type="button" class="row" data-plus="brief-file"
        data-current=${this.briefFile === "" ? "false" : "true"} @click=${this.#openFileField}><span
        aria-hidden="true">${this.briefFile === "" ? "" : "✓"}</span><span class="name">${BRIEF_FROM_FILE}</span></button>`
        : nothing}
      ${last === null ? nothing : html`<button type="button" class="row" data-plus="continue"
        @click=${() => void this.#continue(last)}><span aria-hidden="true"></span><span class="name">${CONTINUE_LAST}<span
        class="d">${continueWords(last)}</span></span></button>`}
      ${this.fileField ? html`<input class="find" type="text" aria-label=${BRIEF_FILE_FIELD}
        placeholder=${BRIEF_FILE_FIELD} autocomplete="off" spellcheck="false" .value=${this.briefFile}
        @keydown=${this.#fileKey}>` : nothing}
    </div>`;
  }

  #openFileField(): void {
    this.fileField = true;
    void this.updateComplete.then(() => this.renderRoot.querySelector<HTMLInputElement>(".plusm .find")?.focus());
  }

  /** Enter in the path field sets the brief file, or takes it off when the field is empty. */
  #fileKey(event: KeyboardEvent): void {
    if (event.key !== "Enter") return;
    event.preventDefault();
    this.#setFile((event.target as HTMLInputElement).value.trim());
    this.menu = "";
    this.fileField = false;
    void this.updateComplete.then(() => (this.renderRoot.querySelector<HTMLElement>(this.briefFile === "" ? "textarea" : ".go"))
      ?.focus());
  }

  /** Sets the brief file, "" for none. The lines of a launch that has ended go, as at an edit
   * of the box. */
  #setFile(path: string): void {
    this.briefFile = path;
    const next = edited(this.flight);
    if (next !== this.flight) {
      this.flight = next;
      this.#stopFollow();
      this.#pressed += 1;
    }
  }

  /** The dim line under the box while a brief file is set: the file, and `Clear`. */
  #fileLine() {
    if (this.briefFile === "") return nothing;
    return html`<div class="changed" data-brief-file><span><span class="k">${BRIEF_FROM_FILE}</span><b
      class="file">${this.briefFile}</b></span><button type="button" aria-label=${`${CLEAR_FILE} the brief file`}
      @click=${() => {
        this.#setFile("");
        void this.updateComplete.then(() => this.#box()?.focus());
      }}>${CLEAR_FILE}</button></div>`;
  }

  /** A click on `Continue the last session`: the session resumed at once, its lines under the
   * box, or its command copied. The box, the panel and the fill stay as they are. */
  async #continue(past: Past): Promise<void> {
    if (inFlight(this.flight)) return;
    const mine = (this.#pressed += 1);
    this.#stopFollow();
    this.#sent = null;
    this.#sentOn = {};
    this.#sentFile = null;
    this.menu = "";
    this.flight = PRESSED;
    const sent = resumeSent(await api("launcher", "api/resume", resumeBody(past)));
    if (mine !== this.#pressed) return;
    if (sent.copy !== null) {
      await this.#copy(mine, sent.copy, []);
      return;
    }
    this.flight = sent.flight;
    if (following(this.flight)) this.#follow(mine);
  }

  // --- the Options panel ----------------------------------------------------------------------

  #togglePanel(): void {
    if (this.panel) this.#closePanel();
    else {
      this.panel = true;
      this.menu = "";
    }
  }

  /** Closes the panel, and the name's editor with it, and gives the focus back to the word
   * that opens it. */
  #closePanel(): void {
    this.panel = false;
    this.query = "";
    this.draft = null;
    this.nameSaid = "";
    void this.updateComplete.then(() => this.renderRoot.querySelector<HTMLElement>(".opt")?.focus());
  }

  /** Opens or closes a group or a subgroup, by its key. */
  #toggleOpen(key: string): void {
    const next = new Set(this.opened);
    if (!next.delete(key)) next.add(key);
    this.opened = next;
  }

  /** Takes one change of a row from the form renderer that draws it. A value that can do
   * harm is held back until `Turn on` is pressed under it; any other value is set, and
   * lets go of one held back for the row. */
  #optionChanged(field: PanelField, event: CustomEvent<FormChange>): void {
    event.stopPropagation();
    const now = valueNow(field, this.#values);
    const next = changedValue(field, now, event.detail);
    if (asksFirst(field, now, next)) {
      this.pending = { name: field.name, value: next as string };
      return;
    }
    if (this.pending?.name === field.name) this.pending = null;
    this.#edited(field.name);
    this.options = withOption(this.options, field, next, this.fields as PanelField[]);
  }

  /** Sets the value held back for a row. */
  #turnOn(field: PanelField): void {
    if (this.pending?.name !== field.name) return;
    this.#edited(field.name);
    this.options = withOption(this.options, field, this.pending.value, this.fields as PanelField[]);
    this.pending = null;
  }

  /** Puts every changed row back to what the form is filled with. The box's text, the
   * place, the chip's choices and where the launch runs are not changed rows and stay. */
  #reset(): void {
    this.options = {};
    this.pending = null;
  }

  /** The rows changed in the panel, as the line under the box names them. */
  get #changes(): Change[] {
    return changes(this.fields as PanelField[], this.options);
  }

  /** The word beside the chip that opens the panel, once there is a form to draw in it,
   * with the number of changed rows. */
  #optionsWord() {
    if (this.fields.length === 0) return nothing;
    const count = this.#changes.length;
    return html`<button type="button" class=${count > 0 ? "opt on" : "opt"} aria-expanded=${this.panel ? "true" : "false"}
      @click=${this.#togglePanel}>${optionsWord(count)}</button>`;
  }

  /** The line under the box that names each changed row, with `Reset`; absent with none. */
  #changed() {
    const rows = this.#changes;
    if (rows.length === 0) return nothing;
    return html`<div class="changed" data-line="changed"><span><span class="k">${CHANGED}</span>${rows.map((row, at) =>
      html`${at === 0 ? nothing : html`<span class="sep" aria-hidden="true">·</span>`}<b class=${row.danger ? "dg" : ""}
        data-change=${row.name}>${row.label}</b>`)}</span><button type="button" @click=${this.#reset}>${RESET}</button></div>`;
  }

  /** The line under the box naming the permission rows the applied preset sets off the
   * user's usual values; absent with none. */
  #presetLine() {
    const fields = this.fields as PanelField[];
    const rows = offRows(fields, this.baseFields as PanelField[], this.options, this.#fill);
    const off = presetOff(this.#formChoice, fields, this.options, rows);
    if (off === null) return nothing;
    return html`<div class="changed" data-line="preset"><span>${off.lead} ${off.labels.map((word, at) =>
      html`${at === 0 ? "" : ", "}<b class=${word.danger ? "dg" : ""} data-off=${word.text}>${word.text}</b>`)} ${
      off.tail}</span></div>`;
  }

  /** A closed group's or subgroup's summary: its values, the ones that can do harm in the
   * warning colour. */
  #summary(words: Word[]) {
    if (words.length === 0) return DEFAULTS;
    return words.map((word, at) => html`${at === 0 ? "" : " · "}<span class=${word.danger ? "dg" : ""}>${word.text}</span>`);
  }

  #panel() {
    const fields = this.fields as PanelField[];
    const finding = this.query.trim() !== "";
    return html`<aside class="panel" aria-label="Options" data-panel>
      <div class="ph"><b>Options</b><button type="button" class="close" @click=${this.#closePanel}>Close</button></div>
      <input class="pfind" type="text" aria-label="Find an option" placeholder="Find an option" autocomplete="off"
        spellcheck="false" .value=${this.query}
        @input=${(event: Event) => { this.query = (event.target as HTMLInputElement).value; }}>
      ${finding ? this.#found(fields) : panelGroups(fields, this.#values, this.#mode).map((group) => this.#group(group))}
    </aside>`;
  }

  #found(fields: PanelField[]) {
    const rows = findRows(fields, this.query, this.#values, this.#mode);
    if (rows.length === 0) return html`<p class="none" role="status">${NO_MATCH}</p>`;
    return html`<div class="found">${rows.map((row) => this.#optionRow(row, row.where))}</div>`;
  }

  #group(group: PanelGroup) {
    const open = this.opened.has(group.key);
    return html`<button type="button" class="cat" data-group=${group.key} aria-expanded=${open ? "true" : "false"}
        @click=${() => this.#toggleOpen(group.key)}><span><span class="ar" aria-hidden="true">${open ? "▾" : "▸"}</span><b>${
        group.title}</b></span><span class="sum">${open ? "" : this.#summary(group.words)}</span></button>
      ${open ? html`<div class="catbody" data-body=${group.key}>${group.subs.map((sub) => this.#sub(group, sub))}</div>` : nothing}`;
  }

  /** A subgroup: its line, and its rows while it is open. The rows with no subgroup are
   * drawn under the group itself. */
  #sub(group: PanelGroup, sub: PanelSub) {
    if (sub.name === "") return html`<div class="loose">${sub.rows.map((row) => this.#optionRow(row))}</div>`;
    const key = `${group.key}/${sub.name}`;
    const open = this.opened.has(key);
    return html`<button type="button" class="sub" data-sub=${key} aria-expanded=${open ? "true" : "false"}
        @click=${() => this.#toggleOpen(key)}><span><span class="ar" aria-hidden="true">${open ? "▾" : "▸"}</span>${
        sub.name}</span><span class="sum">${open ? "" : this.#summary(sub.words)}</span></button>
      ${open ? html`<div class="subopen">${isToolsView(group.key, sub) ? this.#tools(sub)
        : sub.rows.map((row) => this.#optionRow(row))}</div>` : nothing}`;
  }

  // --- Tools > Built-in ----------------------------------------------------------------------

  /** Writes the two rows of rules as `change` leaves the three rows read now. */
  #setTools(change: (now: ToolLists) => ToolLists): void {
    const fields = this.fields as PanelField[];
    const now = toolLists(fields, this.#values);
    const next = change(now);
    this.#edited(...([ALLOWED, DENIED] as const).filter((name) => !same(now[name], next[name])));
    this.options = withTools(this.options, fields, next, now);
  }

  /** The Tools view: the set of built-in tools as its own row, a line per tool the page
   * names, and every rule no line draws. */
  #tools(sub: PanelSub) {
    const now = toolLists(this.fields as PanelField[], this.#values);
    const others = otherRules(now);
    return html`${sub.rows.filter((row) => row.name !== ALLOWED && row.name !== DENIED).map((row) => this.#optionRow(row))}
      <div data-view="tools">${toolLines(now).map((line) => this.#toolLine(line))}</div>
      ${others.length === 0 ? nothing : html`<div class="others" data-rules="other"><div class="oh">${OTHER_RULES}</div>${
        this.#rules(others)}</div>`}`;
  }

  #toolLine(line: ToolLine) {
    const key = `tool/${line.tool}`;
    const open = line.rules.length > 0 && this.opened.has(key);
    const word = (said: ToolWord) => html`<button type="button" class=${line.boxed === said ? "s" : ""}
      aria-pressed=${line.boxed === said ? "true" : "false"}
      @click=${() => this.#setTools((now) => pressTool(now, line.tool, said))}>${said}</button>`;
    return html`<div class=${line.dim ? "tool dim" : "tool"} data-tool=${line.tool}><span class="nm">${line.tool}${
        line.rules.length === 0 ? nothing : html`<button type="button" class="rc" aria-expanded=${open ? "true" : "false"}
          @click=${() => this.#toggleOpen(key)}>${rulesWord(line.rules.length)}</button>`}${
        line.dim ? html`<i>${NOT_IN_SET}</i>` : nothing}</span><span class="tri" role="group"
        aria-label=${line.tool}>${word("allow")}${word("deny")}</span></div>
      ${open ? html`<div class="rules" data-rules=${line.tool}>${this.#rules(line.rules)}</div>` : nothing}`;
  }

  /** Rules, each with the row it is in and `Remove`. */
  #rules(rules: ToolRule[]) {
    const fields = this.fields as PanelField[];
    const label = (name: string) => {
      const field = fields.find((row) => row.name === name);
      return field === undefined ? name : rowLabel(field);
    };
    return rules.map((rule) => html`<div class="rule" data-rule=${rule.entry}><span class="re">${rule.entry}</span><span
      class="rl">${label(rule.row)}</span><button type="button" aria-label=${`Remove ${rule.entry}`}
      @click=${() => this.#setTools((now) => withoutRule(now, rule))}>Remove</button></div>`);
  }

  /** One row: where it sits when the find field found it, the mark of a hidden flag, and
   * the form renderer's control for its kind, showing the value held back for the row while
   * one is. A row whose value can do harm has its name and control in the warning colour. */
  #optionRow(row: PanelRow, where = "") {
    if (row.name === NAME_ROW) return this.#nameRow(row, where);
    const field = row.field;
    const held = this.pending?.name === row.name ? this.pending.value : null;
    return html`<div class=${row.danger ? "prow dg" : "prow"} data-row=${row.name}>
      ${where === "" && !row.hidden ? nothing : html`<div class="at"><span>${where}</span>${
        row.hidden ? html`<span data-mark="hidden">${HIDDEN_FLAG}</span>` : nothing}</div>`}
      <moxi-ui-form mode="each" .fields=${[drawnField(field, held ?? valueNow(field, this.#values))]}
        @moxi-change=${(event: CustomEvent<FormChange>) => this.#optionChanged(field, event)}></moxi-ui-form>
      ${held === null ? this.#fromLast(row) : nothing}
      ${this.#ask(row, held)}
    </div>`;
  }

  /** Under a value filled from the last launch: when that launch was made, and `Clear`; in
   * the warning colour for a value that can do harm. */
  #fromLast(row: PanelRow) {
    const fill = this.#fill;
    if (!Object.hasOwn(fill, row.name) || this.last === null) return nothing;
    return html`<div class=${isDanger(row.field, fill[row.name]) ? "came dg" : "came"} data-from=${row.name}><span>${
      fromLast(this.#lastWhen)}</span><button type="button" aria-label=${`${CLEAR} ${row.label}`}
      @click=${() => this.#clear(row.name)}>${CLEAR}</button></div>`;
  }

  /** Under a value that can do harm: what it does and that it holds for this launch only.
   * While the value is held back, `Turn on` sets it and `Cancel` lets it go; once the user
   * set it the sentences stay until a launch with it starts or its command is copied. A
   * value the form was filled with says nothing here. */
  #ask(row: PanelRow, held: string | null) {
    const field = row.field;
    const set = Object.hasOwn(this.options, row.name) && row.danger ? this.options[row.name] : null;
    const value = held ?? (typeof set === "string" ? set : null);
    if (value === null) return nothing;
    const [does, lasts] = dangerSentences(field, value);
    return html`<div class="ask" data-ask=${row.name}><p>${does} ${lasts}</p>${held === null ? nothing
      : html`<div><button type="button" class="on" @click=${() => this.#turnOn(field)}>Turn on</button><button
          type="button" @click=${() => { this.pending = null; }}>Cancel</button></div>`}</div>`;
  }

  // --- the session name ------------------------------------------------------------------------

  /** What the name is rendered for now: the seat applied, the place, and the model, the comms
   * name and scope and the agent role the form holds. */
  get #nameContext(): NameContext {
    const pick = this.pick;
    const set = this.set;
    return {
      seat: this.preset.kind === "seat" ? this.preset.name : "",
      project: pick?.kind === "project" ? pick.key : "",
      cwd: pick?.kind === "folder" ? pick.path : "",
      model: valueOf(this.fields, set, "model"),
      identity: this.#identity,
      scope: valueOf(this.fields, set, "scope"),
      agent: valueOf(this.fields, set, "agent"),
      harness: this.harness,
    };
  }

  /** Reads `/api/name` for `query`; only the latest read's answer is kept. */
  #readName(query: string): void {
    const asked = (this.#nameAsked += 1);
    this.#nameKey = query;
    void api("launcher", `api/name?${query}`).then((answer) => {
      if (asked !== this.#nameAsked) return;
      const why = answer.status === 0 ? "no answer from the shell" : `the launcher's page server answered ${answer.status}`;
      this.named = parseName(answer.status === 200 ? answer.data : null, why);
    });
  }

  /** The name typed over the template, or emptied to take it. */
  #typeName(field: PanelField, value: string): void {
    this.options = withOption(this.options, field, value === "" ? null : value, this.fields as PanelField[]);
  }

  /** Opens the editor on the template the name is rendered from, kept for the project the answer
   * names, else the line's; or closes it. */
  #toggleEditor(): void {
    this.nameSaid = "";
    if (this.draft !== null) {
      this.draft = null;
      return;
    }
    this.draft = draftOf(this.named, this.#nameContext.project);
  }

  /** An edit of the template being edited; it is rendered again unsaved. */
  #editTemplate(template: string): void {
    if (this.draft === null) return;
    this.draft = { ...this.draft, template };
    this.nameSaid = "";
  }

  /** Saves the template being edited through the shell's settings route; the name is read
   * again once it is written. A save the shell refuses says why and keeps the editor open. */
  async #saveTemplate(): Promise<void> {
    const save = this.named?.save ?? null;
    if (this.draft === null || save === null || this.saving) return;
    this.saving = true;
    const asked = this.#nameAsked;
    let said: string | null;
    try {
      said = saveSaid(await api("ui", "api/settings/set", saveBody(save)));
    } finally {
      this.saving = false;
    }
    if (!this.isConnected) return;
    if (said !== null) {
      this.nameSaid = said;
      return;
    }
    if (asked === this.#nameAsked) this.named = null;
    this.draft = null;
    this.nameSaid = "";
    this.#nameKey = null;
  }

  /** The session name: the name the launch takes, typed over or from its template, its caret
   * opening the editor, and under it whose template it is or that it was typed. */
  #nameRow(row: PanelRow, where: string) {
    const field = row.field;
    const value = valueNow(field, this.#values);
    const typed = typeof value === "string" ? value : "";
    const named = this.named;
    const open = this.draft !== null;
    return html`<div class="prow" data-row=${row.name}>
      ${where === "" ? nothing : html`<div class="at"><span>${where}</span></div>`}
      <div class="lab" aria-hidden="true">${row.label}</div>
      <div class=${open ? "nfield on" : "nfield"}><input type="text" data-control=${row.name} aria-label=${row.label}
        autocomplete="off" spellcheck="false" .value=${typed} placeholder=${named?.name ?? ""}
        @input=${(event: Event) => this.#typeName(field, (event.target as HTMLInputElement).value)}><button
        type="button" aria-label="Edit the name template" aria-expanded=${open ? "true" : "false"}
        @click=${this.#toggleEditor}><span aria-hidden="true">▾</span></button></div>
      ${open ? this.#editor(this.draft as Draft) : this.#nameNote(typed, named)}
    </div>`;
  }

  /** The dim line under the name: typed, with `Use the template`; else whose template it is,
   * or why it can't be read. */
  #nameNote(typed: string, named: Named | null) {
    if (typed !== "") {
      const mine = Object.hasOwn(this.options, NAME_ROW);
      return html`<p class="tnote" data-note="typed">${mine ? TYPED : "Set by the preset or your defaults."} <button
        type="button" @click=${() => this.#typeName(this.#nameField, "")}>${USE_TEMPLATE}</button></p>`;
    }
    if (named === null) return nothing;
    if (named.failed !== "") return html`<p class="tnote" data-note="failed">The name can't be read: ${named.failed}.</p>`;
    if (named.refused !== null) {
      return html`<p class="tnote" data-note="refused">${named.refused.problem}. ${named.refused.hint}</p>`;
    }
    return html`<p class="tnote" data-note="template">${nameNote(named, this.#nameContext.project)}</p>`;
  }

  get #nameField(): PanelField {
    return this.fields.find((field) => field.name === NAME_ROW) as PanelField;
  }

  /** The template's editor: its pieces with the ones to add, the date's format, the template
   * as written, where it is kept, and `Cancel` and `Save template`. */
  #editor(draft: Draft) {
    const named = this.named;
    const project = draft.project;
    const date = dateFormat(draft.template);
    const adds = addList(this.fields, draft.template);
    const refused = named?.refused ?? null;
    const savable = !this.saving && named !== null && named.save !== null && refused === null;
    const now = new Date();
    return html`<div class="tpl" data-editor>
      <div class="tl"><span class="k">${PIECES_WORD}</span><span class="toks">${tokens(draft.template).map((token, at) =>
        html`<button type="button" class="tok" aria-label=${`Take out ${token.piece}`}
          @click=${() => this.#editTemplate(withoutPiece(draft.template, at))}>${token.piece}</button>`)}${
        adds.length === 0 ? nothing : this.#nameMenu("name-add", html`${ADD}`, ADD, html`${adds.map((piece: Piece) =>
          html`<button type="button" class="row" data-piece=${piece} @click=${() => this.#pickName(withPiece(draft.template,
            piece))}><span class="name">${piece}</span></button>`)}`)}</span></div>
      ${date === null ? nothing : html`<div class="tl"><span class="k">${DATE_WORD}</span><span>${this.#nameMenu("name-date",
        html`${dateSample(date, now)}`, `${DATE_WORD} ${dateSample(date, now)}`, html`${DATE_FORMATS.map((format) =>
          html`<button type="button" class="row" data-format=${format} @click=${() => this.#pickName(withDate(draft.template,
            format))}><span class="name">${dateSample(format, now)}</span><span class="t">${format}</span></button>`)}`)}</span></div>`}
      <div class="tl"><span class="k">${WRITTEN}</span><input class="written" type="text" aria-label="The template as written"
        autocomplete="off" spellcheck="false" .value=${draft.template}
        @input=${(event: Event) => this.#editTemplate((event.target as HTMLInputElement).value)}></div>
      ${refused === null ? nothing : html`<p class="nref" data-refused>${refused.problem}. ${refused.hint}</p>`}
      ${keepShown(this.#withProjects, project) ? html`<div class="tl"><span class="k">${KEEP_FOR}</span><span class="tri"
        role="group" aria-label=${KEEP_FOR}>${([["project", project], ["global", EVERY_PROJECT]] as const).map(([keep, word]) =>
          html`<button type="button" class=${draft.keep === keep ? "s" : ""} aria-pressed=${draft.keep === keep ? "true" : "false"}
            @click=${() => { this.draft = { ...draft, keep }; this.nameSaid = ""; }}>${word}</button>`)}</span></div>` : nothing}
      ${this.nameSaid === "" ? nothing : html`<p class="nref" data-saved role="status">${this.nameSaid}</p>`}
      <div class="tl"><span></span><span class="btns"><button type="button" @click=${this.#toggleEditor}>${CANCEL}</button><button
        type="button" class="save" ?disabled=${!savable} @click=${() => void this.#saveTemplate()}>${SAVE}</button></span></div>
    </div>`;
  }

  /** One of the editor's two menus: its face, and its list while it is open. */
  #nameMenu(which: "name-add" | "name-date", face: TemplateResult, label: string, items: TemplateResult) {
    const open = this.menu === which;
    return html`<span class=${open ? "anchor open" : "anchor"}><button type="button" class=${which === "name-add" ? "pk d" : "pk"}
      aria-haspopup="true" aria-expanded=${open ? "true" : "false"} aria-label=${label}
      @click=${() => { this.menu = open ? "" : which; }}>${face}<span class="v" aria-hidden="true">▾</span></button>${
      open ? html`<div class="menu nmenu" data-menu=${which}>${items}</div>` : nothing}</span>`;
  }

  /** A piece added or a date's format picked in one of the editor's menus. */
  #pickName(template: string): void {
    this.menu = "";
    this.#editTemplate(template);
  }

  // --- recents ---------------------------------------------------------------------------------

  /** Reads the recent launches of what the line names, `query`; null names none. Only the latest
   * read's answer is kept, and one that fails leaves the list as it was. */
  #readRecents(query: string | null): void {
    const asked = (this.#recentsAsked += 1);
    if (query === null) {
      this.recents = [];
      return;
    }
    void api("launcher", `api/recents?${query}`).then((answer) => {
      const read = answer.status === 200 ? parseRecents(answer.data) : null;
      if (asked !== this.#recentsAsked || read === null) return;
      this.recents = read;
      if (this.pointed !== "" && !read.some((recent) => recent.id === this.pointed)) this.pointed = "";
    });
  }

  /** The pointer or the focus is on a recent: its card is read, unless the one held is for it,
   * fresh, and read with the default the button has now. */
  #point(recent: Recent): void {
    this.pointed = recent.id;
    if (this.targets === null) return;
    const card = this.card;
    if (card !== null && card.id === recent.id && Date.now() - card.at <= CARD_FRESH_MS
        && sameDefault(card.run, this.targets.default)) return;
    void this.#readCard(recent);
  }

  #unpoint(recent: Recent): void {
    if (this.pointed === recent.id) this.pointed = "";
  }

  /** Reads `/api/recent` for a recent with the button's default now; only the latest read's
   * answer is held. Null when there is no listing to take the default from. */
  async #readCard(recent: Recent): Promise<Card | null> {
    if (this.targets === null) return null;
    const asked = (this.#cardAsked += 1);
    const run = this.targets.default;
    const at = Date.now();
    const answer = await api("launcher", `api/recent?${recentQuery(recent.id, run)}`);
    const why = answer.status === 0 ? "no answer from the shell"
      : `the launcher's page server answered ${answer.status}`;
    const card: Card = { id: recent.id, answer: parseRecent(answer.status === 200 ? answer.data : null, why), run, at };
    if (asked === this.#cardAsked) this.card = card;
    return card;
  }

  /** A click on a recent: its request previewed and launched, or its command copied, where the
   * button's default places it. The card's answer is used when it is fresh and was read with
   * the default the button has now, else it is read again; a recent that can't run again
   * does nothing. */
  async #runAgain(recent: Recent): Promise<void> {
    if (inFlight(this.flight) || this.targets === null || this.#again) return;
    this.#again = true;
    let card = this.card;
    try {
      if (Date.now() - this.#targetsAt > TARGETS_FRESH_MS) await this.#readTargets();
      const run = (this.targets as Targets).default;
      if (card === null || card.id !== recent.id || Date.now() - card.at > CARD_FRESH_MS || !sameDefault(card.run, run)) {
        card = await this.#readCard(recent);
      }
    } finally {
      this.#again = false;
    }
    if (!this.isConnected || inFlight(this.flight) || card === null || card.answer.refused !== null) return;
    const mine = (this.#pressed += 1);
    this.#stopFollow();
    this.#sent = null;
    this.#sentOn = {};
    this.#sentFile = null;
    this.menu = "";
    this.flight = PRESSED;
    await this.#send(mine, recentBody(card.answer.request), againRun(card.run));
  }

  /** `Run again` and the recents under the box; absent with none. */
  #recentLine() {
    if (this.recents.length === 0) return nothing;
    const now = new Date();
    return html`<div class="again" data-recents><span class="k">${RUN_AGAIN}</span><span class="items">${
      this.recents.map((recent) => this.#recent(recent, now))}</span></div>`;
  }

  #recent(recent: Recent, now: Date) {
    const on = this.pointed === recent.id;
    const card = on && this.card !== null && this.card.id === recent.id ? this.card : null;
    return html`<span class="rec" data-recent=${recent.id} @mouseenter=${() => this.#point(recent)}
      @mouseleave=${() => this.#unpoint(recent)}><button type="button" class=${on ? "on" : ""}
        aria-describedby=${card === null ? nothing : CARD_ID} @focus=${() => this.#point(recent)}
        @blur=${() => this.#unpoint(recent)} @click=${() => void this.#runAgain(recent)}><b>${recent.head}</b><span
        class="t">${placeTime(recent.at, now)}</span></button>${card === null ? nothing : this.#card(recent, card)}</span>`;
  }

  /** The card under a recent: what a click runs and where, then what the click does. A recent
   * that can't run again shows its head and when, and why. */
  #card(recent: Recent, card: Card) {
    const when = lastWhen(recent.at, new Date());
    const act = cardAct(card.answer, card.run, recent, this.targets);
    const answer = card.answer;
    const top = answer.refused === null ? this.#cardRuns(recent, answer.request, answer.shows, when)
      : html`<div class="r"><span class="q">${recent.head}</span><span>${when}</span></div>`;
    return html`<div class="card" id=${CARD_ID} role="tooltip" data-card=${recent.id}>${top}<hr>${act.runs
      ? html`<div class="r"><span class="act" data-act>${act.text}</span>${
        act.beside === "" ? nothing : html`<span data-beside>${act.beside}</span>`}</div>`
      : html`<p class="cant" data-act>${act.text}</p>`}</div>`;
  }

  #cardRuns(recent: Recent, request: RecentRequest, shows: Shows, when: string) {
    const fields = fieldsFor(this.#catalog, shows.harness) as PanelField[];
    const posture = postureWords(shows, request, fields, this.#home);
    const place = placeWords(shows, this.pick, this.#home);
    const left = leftOutWords(shows, fields);
    const words = (lead: string, name: string, list: Word[]): TemplateResult | typeof nothing => list.length === 0 ? nothing
      : html`<p class="l" data-posture=${name}><span class="k">${lead}</span>${list.map((word, at) =>
        html`${at === 0 ? "" : ", "}<b class=${word.danger ? "dg" : ""}>${word.text}</b>`)}</p>`;
    return html`${shows.prompt === null ? nothing : html`<p class="q" data-prompt>${shows.prompt}</p>`}
      <div class="r"><span data-runs>${runsWith(shows)}</span><span data-preset>${presetWords(request)}</span></div>
      <div class="r"><span data-ran>${ranWhere(recent)}</span><span>${when}</span></div>
      ${place === "" ? nothing : html`<p data-place>${place}</p>`}
      ${words(OFF_DEFAULTS, "off", posture.off)}${words(OWN_DEFAULTS, "own", posture.own)}
      ${left === null ? nothing : html`<p data-left-out>${left}</p>`}`;
  }

  // --- the button, its menu and the launch ---------------------------------------------------

  /** Reads where a launch may run. Only the latest read's answer is kept; one that fails
   * reads as no slots, unread where slots was there before. Done when the latest read
   * asked by then has been answered, so a caller never goes on with an older listing. */
  #readTargets(): Promise<void> {
    const asked = (this.#targetsAsked += 1);
    const at = Date.now();
    const read: Promise<void> = api("launcher", "api/targets").then((answer) => {
      if (asked !== this.#targetsAsked) return this.#targetsRead === read ? undefined : this.#targetsRead;
      this.targets = answer.status === 200 ? parseTargets(answer.data) : afterNoAnswer(this.targets);
      this.#targetsAt = answer.status === 200 && !this.targets.unread ? at : 0;
    });
    this.#targetsRead = read;
    return read;
  }

  #toggleRun(): void {
    this.menu = this.menu === "run" ? "" : "run";
    if (this.menu !== "run") return;
    void this.#readTargets();
    this.#readUnread();
  }

  #pickRun(run: Run): void {
    this.picked = run;
    this.menu = "";
    void this.updateComplete.then(() => this.renderRoot.querySelector<HTMLElement>(".go")?.focus());
  }

  /** Whether the button can start a launch: a place, the form for the applied preset, a
   * listing, and the last launch read and filled from are there. */
  get #able(): boolean {
    return this.ready && this.fields.length > 0 && this.targets !== null && sameChoice(this.#formChoice, this.preset)
      && this.#lastRead && this.#waiting === null;
  }

  /** The press: the preview, then the launch with its ticket or the command copied. */
  async #press(): Promise<void> {
    if (inFlight(this.flight) || !this.#able || this.targets === null) return;
    const mine = (this.#pressed += 1);
    this.#stopFollow();
    // What the button says at the press is what is sent, whatever a later read says.
    const shown = runOf(this.targets, this.picked);
    const body = launchRequest(this.fields, this.#form, this.set, shown, this.baseFields, this.#formChoice,
                               Object.keys(this.#fill));
    // A brief file is the first turn in place of the box's text, which then stays.
    this.#sentFile = this.briefFile === "" ? null : this.briefFile;
    this.#sent = this.#sentFile === null ? this.brief : null;
    this.#sentOn = turnedOn(this.fields as PanelField[], this.options);
    this.menu = "";
    this.flight = PRESSED;
    let said = shown;
    if (Date.now() - this.#targetsAt > TARGETS_FRESH_MS) {
      await this.#readTargets();
      if (mine !== this.#pressed) return;
      // A slot the launch does not ask for is named by the newest read.
      const now = runOf(this.targets, this.picked);
      if (shown.target === "slot" && !shown.named && now.target === "slot" && !now.named) said = now;
    }
    await this.#send(mine, body, said);
  }

  /** The preview of `body`, then the launch with its ticket, or its command copied; `said` is
   * where the lines say it runs. */
  async #send(mine: number, body: LaunchBody, said: Run): Promise<void> {
    const answer = previewed(await api("launcher", "api/preview", body));
    if (mine !== this.#pressed) return;
    if (answer.next === "end") {
      this.flight = answer.flight;
      return;
    }
    if (answer.next === "copy") {
      await this.#copy(mine, answer.command, answer.notes);
      return;
    }
    const sent = launched(await api("launcher", "api/launch", { ...body, ticket: answer.ticket }), said,
                          answer.notes);
    if (mine !== this.#pressed) return;
    if (sent.copy !== null) {
      await this.#copy(mine, sent.copy, answer.notes);
      return;
    }
    this.flight = sent.flight;
    if (following(this.flight)) this.#follow(mine);
  }

  /** Copies a terminal launch's command, or shows it to copy by hand. Either way the
   * command is the launch: each value that can do harm it was built with is let go of, as
   * a launch that started lets go of it. */
  async #copy(mine: number, command: string, notes: Line[]): Promise<void> {
    let done = true;
    try {
      await navigator.clipboard.writeText(command);
    } catch {
      done = false;
    }
    if (mine !== this.#pressed) return;
    this.flight = copied(command, done, notes);
    this.options = afterLaunch(this.options, this.#sentOn);
    this.#sentOn = {};
  }

  /** Reads the launch's status path now and then on a timer, until it ends. */
  #follow(mine: number): void {
    this.#followSince = Date.now();
    void this.#check(mine);
    this.#followTimer = setInterval(() => {
      if (Date.now() - this.#followSince > FOLLOW_FOR_MS) this.#landed(stopped(this.flight));
      else void this.#check(mine);
    }, FOLLOW_MS);
  }

  async #check(mine: number): Promise<void> {
    const status = this.flight.status;
    if (status === null || !following(this.flight)) return;
    const answer = await api("launcher", status.path);
    if (mine === this.#pressed) this.#landed(followed(this.flight, answer));
  }

  /** Takes the followed launch's next state. A launch that started empties the box, unless
   * its text was changed since the press, lets go of the brief file it was launched with,
   * unless it was changed since, and lets go of each value that can do harm it was
   * launched with, unless its row was changed since the press. */
  #landed(next: Flight): void {
    if (next === this.flight) return;
    this.flight = next;
    if (!following(next)) this.#stopFollow();
    if (!next.started) return;
    if (this.#sent !== null && this.brief === this.#sent) this.brief = "";
    if (this.#sentFile !== null && this.briefFile === this.#sentFile) this.briefFile = "";
    this.#sentFile = null;
    this.options = afterLaunch(this.options, this.#sentOn);
    this.#sentOn = {};
  }

  #stopFollow(): void {
    clearInterval(this.#followTimer);
    this.#followTimer = undefined;
  }

  /** An edit of the box. The lines of a launch that has ended go with it. */
  #edit(text: string): void {
    this.brief = text;
    const next = edited(this.flight);
    if (next !== this.flight) {
      this.flight = next;
      this.#stopFollow();
      this.#pressed += 1;
    }
  }

  /** The split button at the right of the box's last line: the act, and a caret that opens
   * where it runs. Until the listing is read it says `Launch` and is off. */
  #button() {
    const busy = inFlight(this.flight);
    const run = this.targets === null ? null : runOf(this.targets, this.picked);
    const open = this.menu === "run" && this.targets !== null && run !== null;
    return html`<span class="split"><button type="button" class="go" data-target=${run?.target ?? nothing}
        ?disabled=${busy || !this.#able} @click=${() => void this.#press()}>${
        busy ? STARTING : run === null ? "Launch" : buttonWords(run)}</button><span
      class=${open ? "anchor open" : "anchor"}><button type="button" class="more" aria-label="Where it runs"
        aria-haspopup="true" aria-expanded=${open ? "true" : "false"} ?disabled=${busy || this.targets === null}
        @click=${this.#toggleRun}><span aria-hidden="true">▾</span></button>${
        open ? this.#runMenu(this.targets as Targets, run as Run) : nothing}</span></span>`;
  }

  #runMenu(targets: Targets, run: Run) {
    const menu = runMenu(targets, run);
    return html`<div class="menu run" data-menu="run">
      ${menu.slots.map((row) => this.#runRow(row))}
      ${menu.slots.length > 0 ? html`<hr>` : nothing}
      ${menu.others.map((row) => this.#runRow(row))}
    </div>`;
  }

  #runRow(row: RunRow) {
    const inner = html`<span aria-hidden="true">${row.current ? "✓" : ""}</span><span>${row.lead}</span><span
      class="d">${row.words}</span>`;
    const pick = row.run;
    if (pick === null) return html`<div class="row taken" data-run=${row.lead}>${inner}</div>`;
    return html`<button type="button" class="row" data-run=${row.lead}
      data-current=${row.current ? "true" : "false"} @click=${() => this.#pickRun(pick)}>${inner}</button>`;
  }

  /** A line's text, with the launcher's page a link where the line names it. */
  #words(line: Line) {
    const around = aroundPage(line);
    if (around === null) return line.text;
    return html`${around[0]}<a href="/m/launcher/" @click=${(event: MouseEvent) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      navigate("launcher");
    }}>${LAUNCHER_PAGE}</a>${around[1]}`;
  }

  /** The lines under the box: the launch's, or why the launch form can't be read, then a
   * dim one while the slots can't be read. */
  #said() {
    const lines: Line[] = [...this.flight.lines, ...this.flight.notes];
    return html`<div class="said" role="status">${
      lines.length === 0 && this.unread !== ""
        ? html`<p data-line="unread">The launch form can't be read: ${this.unread}.</p>` : nothing}${
      lines.map((line) => html`<p class=${line.tone} data-line=${line.name}>${this.#words(line)}</p>${
        line.command === undefined ? nothing : html`<code data-line="command">${line.command}</code>`}`)}${
      this.targets?.unread === true ? html`<p class="dim" data-line="slots-unread">${SLOTS_UNREAD}</p>` : nothing}</div>`;
  }
}

/** Why a read of the launch form gave nothing to draw. */
function unreadWhy(answer: ApiResult, read: { detail: string | null } | null): string {
  if (answer.status === 0) return "no answer from the shell";
  if (answer.status !== 200) return `the launcher's page server answered ${answer.status}`;
  return read?.detail ?? "the launcher's page server sent something else";
}

if (!customElements.get(TAG)) customElements.define(TAG, LauncherPrompt);
