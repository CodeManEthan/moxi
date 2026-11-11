/*! Bundled with d3-hierarchy, under the ISC licence:
 *
 * d3-hierarchy 3.1.2: Copyright 2010-2021 Mike Bostock
 *
 * Permission to use, copy, modify, and/or distribute this software for any purpose
 * with or without fee is hereby granted, provided that the above copyright notice and
 * this permission notice appear in all copies.
 *
 * THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO
 * THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO
 * EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL
 * DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER
 * IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN
 * CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
 */

// <moxi-usage-page route="...">: usage's page. Its frame, top to bottom:
//
//   p[data-part=reading]        `Reading…`, until the first read answers
//   div[data-part=warning]      the one line while a plan limit fills before it resets
//   div[data-part=top]          the groups above the filter line:
//     section[data-group=limits]   one per harness with a window to draw, a row per window;
//                                  a row that opens is a link, and the opened one has its
//                                  lines under it (div[data-part=opened])
//     section[data-group=cost]     today, 7 days and 30 days, each with the period before
//   div[data-part=nothing]      in place of Cost and all under it while nothing is counted
//   div[data-part=filters]      the filter line: Range, Project (or Folder), Harness,
//                               Model, each a menu, and Cost / Tokens
//   div[data-part=below]        the groups under the filter line:
//     section[data-group=chart]    one bar a day or a month, its sentence and its table
//     section[data-group=sessions] the range's dearest session and the count, and opened
//                                  the week's dearest as two-line rows (moxi-ui-row), what
//                                  they leave out, the earlier days, and the table; an
//                                  opened row holds its session (div[data-part=session]):
//                                  its lines, its chart and its acts; shown, its
//                                  subagents (div[data-part=agents]): a map and a row
//                                  each, the opened one with its requests under it
//     section[data-group=projects|folders]  the dearest projects (or folders), and opened
//                                  each with its share and cost, a row filtering Sessions
//     section[data-group=models]   the dearest models, and opened each with share and cost
//     section[data-group=kinds]    the input read from cache and the dearest classes, and
//                                  opened the four classes and what caching saved
//     section[data-group=prices]   how many models are priced and the table's date, and
//                                  opened their prices and the acts that open under them
//   div[data-part=foot]         when the sessions were last read, the files, the act
//   div[data-part=lines]        dim lines: what the figures leave out, reads that failed
//
// The view places what its models return and words nothing itself: a piece whose fact
// no read carries is not in the model and is not drawn. The range and the filters are
// one state (`filter-model.ts`), held for the document's life; every read under the
// filter line is asked with it.
//
// One piece is open at a time, a limit row or a section, and the address names it
// (`address-model.ts`). The page is placed with the route the address gave. When a piece
// opens or closes the page rewrites the address of the history entry it is on and adds no
// entry, so Back leaves the page, and a reload or a new placement opens the same piece.

import { css, html, LitElement, nothing, svg } from "lit";
import { api, installed, navigate, subscribe } from "/m/ui/client.js";

import {
  addressPath, addressWrite, limitAddress, NOTHING_OPEN, parseAddress, sameAddress, sessionAddress, SESSIONS, settled,
} from "./address-model.ts";
import type { Address } from "./address-model.ts";
import { chartDraw, chartGroup } from "./chart-model.ts";
import type { ChartDraw, ChartGroup, Table } from "./chart-model.ts";
import { mostTo, sumRows } from "./cost-model.ts";
import type { Sum } from "./cost-model.ts";
import { LOCAL } from "./days.ts";
import {
  Feed, parseIndexEvent, parseLimits, parsePrices, parseReport, parseRestartPoint, parseStatus, parseWindow,
  passOutcome,
} from "./feed.ts";
import type {
  AgentRow, DayRow, IndexEvent, Limits, ModelRow, OpenedWindow, Prices, ProjectRow, Report, RequestRow, RequestsTotal,
  RestartPoint, SessionRow, Status, TermsRow, TermsTotal,
} from "./feed.ts";
import {
  allTimePath, chartPath, customRange, figuresPath, filterLine, heldState, holdState, modelsPath,
  modelsSectionPath, placesPath, placesSectionPath, START, termsSectionPath, watchState,
} from "./filter-model.ts";
import type { FilterLine, Menu, MenuItem, PageState } from "./filter-model.ts";
import {
  COPIED, COPY, costPath, frameModel, hasBelow, newestDay, passLine, READING, shouldPass,
} from "./frame-model.ts";
import type { Cost, Foot, FootLine, Nothing, OtherRead } from "./frame-model.ts";
import type { LimitGroup, LimitRow, Warning } from "./limits-model.ts";
import { fmtCost, layerLine, overrideLine, RATE_FIELDS, rateLine, reconcileLines, searchPath, TERM_CLASSES } from "./views.ts";
import { CLOSE, HIDE_TABLE, modelsSection, placesSection, SECTION_LABELS, TABLE as SECTION_TABLE } from "./sections-model.ts";
import type { Section, SectionKey, SentencePart } from "./sections-model.ts";
import {
  BILL, DETAILS, FIND, KINDS_LABEL, PRICE_HEADS, PRICES_LABEL, pricesFor, pricesLeft, pricesSection, kindsSection,
} from "./terms-model.ts";
import type { Kinds, PricesModel, PricesPart } from "./terms-model.ts";
import { OPEN, SessionList, sessionLine, sessionsClosed, sessionsTable, SESSIONS_LABEL, SHOW } from "./sessions-section.ts";
import type { SessionLine, SessionsClosed, TablePart } from "./sessions-section.ts";
import {
  earlierLine, FIRST_TOP, firstHeading, PAGE, remainder, sessionLists, sessionName, sessionsPath,
} from "./sessions-model.ts";
import type { ListLine, SessionLists } from "./sessions-model.ts";
import { openedLimit, openedRow, readingKey, windowPath } from "./window-model.ts";
import {
  COPY_ID, newestModel, OPEN_IN_SESSIONS, openedKeys, openedPaths, openedSession, placedRow, restartPath, SESSION_CHART,
  sessionChart,
} from "./opened-session.ts";
import type { Item, OpenedSession, Part, SessionChart } from "./opened-session.ts";
import {
  AGENTS_PAGE, agentMap, agentPath, HIDE, MAIN, MAP_HEIGHT, moreLine, openedAgent, shownCount, subagentLines,
} from "./subagents.ts";
import type { AgentLine, AgentMap } from "./subagents.ts";
import type { OpenedLimit } from "./window-model.ts";

const TAG = "moxi-usage-page";
const TICK_MS = 15_000;
/** The thin line beside a percent, in px: its length and its height. */
const METER = { width: 96, height: 9 };
/** How long a search of the model table waits for the typing to stop, in ms. */
const SEARCH_MS = 200;
/** The fewest characters a search of the model table takes. */
const SEARCH_MIN = 2;

// When this document last asked for a pass as a page opened, in epoch seconds.
let askedAt = 0;
// The read of a window that was refused and so closed its row, kept for its line at the
// foot until another piece opens. It outlives one placement: the shell may place the page
// again just after the row closed and the address was rewritten.
let refusedWindow: Feed<OpenedWindow> | null = null;

const STYLES = css`
  :host { display: block; max-width: 680px; min-width: 0; margin: 0 auto; padding: 4px 0 16px;
          color: var(--moxi-text); font-family: var(--moxi-font-sans); }
  * { box-sizing: border-box; }
  p { margin: 0; }
  a, button.act { color: var(--moxi-text); text-decoration: none; cursor: pointer; }
  button.act { padding: 0; border: 0; background: none; font: inherit; }
  :focus-visible { outline: 2px solid var(--moxi-text); outline-offset: 2px; }
  .dim { color: var(--moxi-text-dim); }
  .reading { padding: 16px 2px 0; font-size: 14px; line-height: 20px; }
  .warnline { padding: 0 2px 14px; font-size: 14px; line-height: 20px; color: var(--moxi-text-dim);
              white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .warnline b { font-weight: 600; color: var(--moxi-text); }
  .sep { margin: 0 8px; }
  .grps { margin-top: 16px; border-top: 1px solid var(--moxi-border); }
  .grp { display: grid; grid-template-columns: 96px 1fr; padding: 5px 2px; border-bottom: 1px solid var(--moxi-border); }
  .gk { padding-top: 7px; font-size: 14px; line-height: 20px; color: var(--moxi-text-dim); }
  .gk small { display: block; font-size: 12px; line-height: 16px; font-variant-numeric: tabular-nums; }
  .gl { min-width: 0; }
  .lim { display: grid; grid-template-columns: 96px 112px 84px 1fr; align-items: baseline; padding: 7px 0;
         font-size: 14px; line-height: 20px; white-space: nowrap; }
  .lim.reset { display: block; color: var(--moxi-text-dim); font-variant-numeric: tabular-nums; }
  .lim .u { font-variant-numeric: tabular-nums; }
  .lim .w { display: flex; justify-content: space-between; min-width: 0; color: var(--moxi-text-dim);
            font-variant-numeric: tabular-nums; }
  .lim .w b { font-weight: 400; color: var(--moxi-text); }
  a.lim { cursor: pointer; }
  .lim.open { padding-bottom: 3px; }
  .lim .w .x { font-size: var(--moxi-font-size-sm); }
  .det { margin: 4px 0 8px; padding: 8px 0 0; border-top: 1px solid var(--moxi-border);
         font-size: var(--moxi-font-size-sm); line-height: 18px; overflow-wrap: anywhere; }
  .det .r { display: grid; grid-template-columns: 76px 1fr auto; padding: 3px 0; align-items: baseline; }
  .det .k, .det .d { color: var(--moxi-text-dim); }
  .det .f { min-width: 0; }
  .det p { padding: 3px 0; }
  .det .ul { display: flex; justify-content: space-between; gap: 18px; padding: 0 0 3px;
             font-variant-numeric: tabular-nums; }
  .det .ul > :last-child { flex: none; white-space: nowrap; }
  .det .ul .n { color: var(--moxi-text); }
  .m { display: block; align-self: center; width: 96px; height: 9px; }
  .m .base { fill: var(--moxi-border-strong); }
  .m .fill { fill: var(--moxi-text); }
  .m .tick { fill: var(--moxi-text-dim); }
  .one { display: flex; justify-content: space-between; align-items: baseline; min-width: 0; padding: 7px 0;
         font-size: 14px; line-height: 20px; }
  .one > span:first-child { min-width: 0; padding-right: 18px; white-space: nowrap; overflow: hidden;
                            text-overflow: ellipsis; }
  .one .t { color: var(--moxi-text-dim); white-space: nowrap; font-variant-numeric: tabular-nums; }
  .one .pk { display: inline-block; width: 64px; color: var(--moxi-text-dim); }
  [title] { cursor: help; }
  .one2 { display: block; margin: -6px 0 0; padding-bottom: 7px; font-size: var(--moxi-font-size-sm);
          line-height: 18px; color: var(--moxi-text-dim); white-space: nowrap; overflow: hidden;
          text-overflow: ellipsis; }
  .one2.ind { padding-left: 64px; }
  .capl { display: flex; justify-content: space-between; padding: 3px 0 7px; font-size: var(--moxi-font-size-sm);
          line-height: 18px; color: var(--moxi-text-dim); }
  .note { display: flex; justify-content: space-between; padding: 12px 2px 0; font-size: var(--moxi-font-size-sm);
          line-height: 18px; color: var(--moxi-text-dim); }
  .lines { padding: 6px 2px 0; font-size: var(--moxi-font-size-sm); line-height: 18px; color: var(--moxi-text-dim);
           overflow-wrap: anywhere; }
  .lines p + p { margin-top: 2px; }
  .filt { position: relative; display: flex; justify-content: space-between; margin: 14px 0 12px; padding: 0 2px;
          font-size: 14px; line-height: 20px; color: var(--moxi-text-dim); white-space: nowrap; }
  .filt .fl { display: flex; flex-wrap: wrap; row-gap: 4px; min-width: 0; }
  .filt .k { margin-right: 8px; }
  .filt .sep { margin: 0 12px; }
  .filt button { padding: 0; border: 0; background: none; font: inherit; color: inherit; cursor: pointer; }
  .filt button.set, .filt .tog button[aria-pressed="true"] { color: var(--moxi-text); }
  .filt .v { position: relative; top: 1px; margin-left: 6px; font-size: 15px; line-height: 0; color: var(--moxi-text-dim); }
  .filt .tog button { margin-left: 14px; }
  .anchor { position: relative; display: inline-block; }
  .menu { position: absolute; z-index: 2; left: 0; top: calc(100% + 8px); width: max-content; min-width: 240px;
          max-width: 400px;
          padding: 6px 0; border: 1px solid var(--moxi-border-strong); border-radius: 10px; background: var(--moxi-bg);
          font-size: 14px; line-height: 1.5; color: var(--moxi-text); text-align: left; white-space: normal; }
  .menu hr { margin: 6px 0; border: 0; border-top: 1px solid var(--moxi-border); }
  .menu button.it { display: grid; grid-template-columns: 20px 1fr auto; column-gap: 0; align-items: baseline; width: 100%;
                    padding: 7px 14px; color: var(--moxi-text); text-align: left; overflow-wrap: anywhere; }
  .menu button.it:hover { background: var(--moxi-bg-hover); }
  .menu .t { padding-left: 16px; color: var(--moxi-text-dim); font-variant-numeric: tabular-nums; white-space: nowrap; }
  .menu .custom { display: flex; flex-wrap: wrap; gap: 6px 8px; align-items: center; padding: 2px 14px 8px 34px;
                  color: var(--moxi-text-dim); }
  .menu .custom input { max-width: 100%; padding: 2px 4px; border: 1px solid var(--moxi-border-strong);
                        border-radius: var(--moxi-radius); background: var(--moxi-bg); color: var(--moxi-text);
                        font: inherit; font-size: var(--moxi-font-size-sm); }
  .menu .custom button { color: var(--moxi-text); }
  .grps.below { margin-top: 0; }
  .chartl { display: flex; justify-content: space-between; gap: 18px; padding: 7px 0 2px;
            font-size: var(--moxi-font-size-sm); line-height: 18px; color: var(--moxi-text-dim); }
  .chartl button.act { flex: none; color: inherit; }
  .none { padding: 7px 0; font-size: 14px; line-height: 20px; color: var(--moxi-text-dim); }
  svg.ch { display: block; margin: 8px 0 6px; overflow: visible; }
  svg.ch text { fill: var(--moxi-text-dim); font-size: 11px; font-family: var(--moxi-font-sans);
                font-variant-numeric: tabular-nums; }
  svg.ch text.v { fill: var(--moxi-text); }
  svg.ch .bar { fill: var(--moxi-text); fill-opacity: 0.36; }
  svg.ch .bar.now { fill-opacity: 1; }
  svg.ch .ax { stroke: var(--moxi-border-strong); stroke-width: 1; }
  svg.ch .gr { stroke: var(--moxi-border); stroke-width: 1; }
  svg.ch .dash { fill: none; stroke: var(--moxi-text-dim); stroke-width: 1; stroke-dasharray: 3 3; }
  svg.ch .hit { fill: none; pointer-events: all; }
  svg.ch .ln { fill: none; stroke: var(--moxi-text); stroke-width: 1.5; stroke-linejoin: round; }
  svg.ch .dot { fill: var(--moxi-text); stroke: var(--moxi-bg); stroke-width: 2; }
  svg.ch.ses-ch { max-width: 100%; height: auto; margin: 0; }
  svg.ch.map { max-width: 100%; height: auto; margin: 4px 0 6px; }
  svg.ch.map .cell { fill: var(--moxi-text); fill-opacity: 0.1; }
  svg.ch.map .cell.on { fill-opacity: 0.3; }
  .det .x { padding-left: 18px; color: var(--moxi-text-dim); }
  .det .x.act { color: var(--moxi-text); }
  .ses .det { margin: 9px 0 3px; }
  .det .cw { padding: 4px 0 2px; }
  .det .foot { display: flex; flex-wrap: wrap; gap: 4px 18px; padding: 9px 0 0 76px; }
  .det .foot .act { color: var(--moxi-text); }
  .det .foot .act.d { color: var(--moxi-text-dim); }
  .det .under { padding-top: 6px; }
  .scroll { max-width: 100%; overflow-x: auto; padding: 2px 0 6px; }
  .tbl { width: 100%; border-collapse: collapse; font-size: var(--moxi-font-size-sm); line-height: 18px;
         white-space: nowrap; font-variant-numeric: tabular-nums; }
  .tbl th, .tbl td { padding: 5px 0 5px 16px; text-align: left; font-weight: 400; }
  .tbl th:first-child, .tbl td:first-child { padding-left: 0; }
  .tbl th { color: var(--moxi-text-dim); }
  .tbl .n { text-align: right; }
  .quietline { padding: 150px 0 14px; font-size: 22px; line-height: 32px; text-align: center;
               letter-spacing: -0.005em; }
  .grp.is-open .gk { color: var(--moxi-text); }
  .rows { padding: 2px 0 6px; }
  .tr2 { display: grid; grid-template-columns: minmax(0, 1fr) 150px minmax(64px, auto); padding: 5px 0;
         font-size: 14px; line-height: 20px; white-space: nowrap; }
  .tr2 .nm { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
  .tr2 .d { color: var(--moxi-text-dim); }
  .tr2 .n { text-align: right; font-variant-numeric: tabular-nums; }
  .rows p { max-width: 520px; padding: 4px 0 6px; font-size: var(--moxi-font-size-sm); line-height: 18px;
            color: var(--moxi-text-dim); overflow-wrap: anywhere; }
  .rows p a { color: var(--moxi-text); }
  .rows p.acts { padding-top: 0; }
  .rows .act { color: var(--moxi-text-dim); }
  .acts .act + .act { margin-left: 18px; }
  .one .d { color: var(--moxi-text-dim); }
  .sub1 { padding: 7px 0 0; font-size: var(--moxi-font-size-sm); line-height: 18px; color: var(--moxi-text-dim); }
  .ses { min-width: 0; }
  .ses .do { font-size: var(--moxi-font-size-sm); line-height: 18px; color: var(--moxi-text-dim); }
  .more { display: flex; justify-content: space-between; gap: 18px; margin-top: 5px; padding: 9px 0 8px;
          border-top: 1px solid var(--moxi-border); font-size: var(--moxi-font-size-sm); line-height: 18px;
          color: var(--moxi-text-dim); }
  .more .act { flex: none; color: var(--moxi-text); }
  .more + .more { margin-top: 0; padding-top: 0; border-top: 0; }
  .tr2.p5 { grid-template-columns: minmax(0, 1fr) 74px 74px 86px 82px; font-size: var(--moxi-font-size-sm); }
  .tr2.th { color: var(--moxi-text-dim); align-items: end; }
  .tr2.th span { white-space: normal; }
  .tbl .d { color: var(--moxi-text-dim); }
  .tbl td.wrap { min-width: 8em; white-space: normal; overflow-wrap: anywhere; }
  .tbl tr[data-line="subtotal"] td, .tbl tr[data-line="total"] td { font-weight: 600; }
  /* The bill and the rates keep every column and scroll inside their wrapper; the Model
     column stays in view, opaque over the cells that scroll under it. */
  .tbl.bill :is(th, td):first-child { position: sticky; left: 0; z-index: 1; padding-right: 12px;
                                      background: var(--moxi-bg); }
  .under { padding: 2px 0 6px; }
  .under .sub { color: var(--moxi-text); padding-bottom: 0; }
  .under ul { margin: 0; padding: 0 0 6px 18px; font-size: var(--moxi-font-size-sm); line-height: 18px;
              color: var(--moxi-text-dim); overflow-wrap: anywhere; }
  .find { display: flex; gap: 8px; align-items: baseline; padding: 4px 0 6px; font-size: var(--moxi-font-size-sm);
          color: var(--moxi-text-dim); }
  .find input { min-width: 0; flex: 1; max-width: 260px; font: inherit; color: var(--moxi-text); padding: 2px 6px;
                border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius); background: var(--moxi-bg); }
  .grps + .nothing .quietline { padding-top: 56px; }
  .first { font-size: 14px; line-height: 1.6; color: var(--moxi-text-dim); text-align: center; }
  .first p { max-width: 520px; margin: 0 auto; }
  .first p + p { margin-top: 14px; }
  .first .x { border-bottom: 1px solid var(--moxi-border); }
  .first code { font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); color: var(--moxi-text);
                margin-right: 12px; }
  @media (max-width: 720px) {
    :host { padding-top: 10px; }
    .warnline { white-space: normal; }
    .grp { display: block; padding: 4px 0 6px; }
    .gk { padding-top: 9px; font-size: var(--moxi-font-size-sm); }
    .gk small { display: inline; margin-left: 8px; }
    .lim { grid-template-columns: 96px 1fr 96px; row-gap: 0; }
    .lim .m { grid-column: 3; grid-row: 1; }
    .lim .u { grid-column: 2; grid-row: 1; }
    .lim .w { grid-column: 1 / 4; font-size: var(--moxi-font-size-sm); line-height: 18px; white-space: normal; }
    .lim.reset { white-space: normal; }
    .one { display: block; }
    .one > span:first-child { display: block; padding-right: 0; white-space: normal; }
    .one .t { display: block; padding-left: 64px; font-size: var(--moxi-font-size-sm); line-height: 18px; }
    .one2 { margin-top: 0; white-space: normal; }
    .filt .fl { column-gap: 18px; }
    .filt .fl .sep { display: none; }
    .filt .anchor { position: static; }
    .menu { max-width: calc(100% - 4px); }
    .menu button.it { grid-template-columns: 20px 1fr auto; }
    .tr2 { grid-template-columns: minmax(0, 1fr) 64px minmax(64px, auto); }
    .tr2.p5 { grid-template-columns: minmax(0, 1fr) 48px 52px 84px 52px; }
    .det .foot { padding-left: 0; }
    .det .r[data-part=agents] > .f { grid-column: 1 / -1; }
    .capl, .note { display: block; }
    .capl span, .note span { display: block; }
    .quietline { padding-top: 56px; white-space: normal; }
  }
`;

export class UsagePage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
    tick: { state: true },
    copied: { state: true },
    menu: { state: true },
    custom: { state: true },
    asTable: { state: true },
    sectionTable: { state: true },
    pricesPart: { state: true },
    searchText: { state: true },
    sessionPart: { state: true },
    copiedId: { state: true },
    agentsShown: { state: true },
    agentPart: { state: true },
    width: { state: true },
  };

  route = "";
  tick = 0;
  /** The command was just copied. */
  copied = false;
  /** The filter whose menu is open. */
  menu: Menu["key"] | null = null;
  /** The Range menu shows the two days of `From … to …`. */
  custom = false;
  /** The chart's table stands in its place. */
  asTable = false;
  /** The opened section whose table is shown under its rows. */
  sectionTable: SectionKey | "sessions" | null = null;
  /** What is open under the price table. */
  pricesPart: PricesPart | null = null;
  /** What `Find a model` searches for. */
  searchText = "";
  /** What is open under the opened session's lines: its table or its bill. */
  sessionPart: "table" | "bill" | null = null;
  /** The opened session's id was just copied. */
  copiedId = false;
  /** How many of the opened session's subagents `Show` has asked for; none until it is
   * pressed. */
  agentsShown = 0;
  /** What is open under the opened subagent's lines: its table. */
  agentPart: "table" | null = null;
  /** The element's own width, in px. */
  width = 680;

  #limits: Feed<Limits> | null = null;
  #status: Feed<Status> | null = null;
  /** The Cost rows' report. */
  #daily: Feed<Report<DayRow>> | null = null;
  #event: IndexEvent | null = null;
  /** How many `index` events have come. */
  #events = 0;
  /** What a pass the page asked for came to, when it did not start. */
  #pass: string | null = null;
  /** The reads under the filter line, opened for the state they were asked with. */
  #chart: Feed<Report<DayRow>> | null = null;
  #figures: Feed<Report<DayRow>> | null = null;
  #allTime: Feed<Report<DayRow>> | null = null;
  #places: Feed<Report<ProjectRow>> | null = null;
  #models: Feed<Report<ModelRow>> | null = null;
  /** The sections' reads, under every filter: the menus' own reads when no filter of
   * theirs is set. */
  #placesSection: Feed<Report<ProjectRow>> | null = null;
  #modelsSection: Feed<Report<ModelRow>> | null = null;
  /** The Sessions section's closed row: the range's dearest session and the count. */
  #sessionsTop: Feed<Report<SessionRow>> | null = null;
  /** The opened section's lists: the first list, the earlier days' count and total from
   * one row, and their rows once shown; each for the days and filters in `#listsKey`. */
  #lists: SessionLists | null = null;
  #listsKey: string | null = null;
  #firstList: SessionList | null = null;
  #earlierTop: Feed<Report<SessionRow>> | null = null;
  #earlierList: SessionList | null = null;
  /** The session the address named as the page was placed: its row comes first in the
   * section, read over the session's whole life. */
  #placed: { harness: string; id: string } | null = null;
  /** The list the opened session was opened from; null when the address named it. */
  #openedIn: "first" | "earlier" | null = null;
  /** The opened session's reads, each with the query its row was read with, and the
   * restart point for the model of its newest request. */
  #openedReads: { requests: Feed<Report<RequestRow, RequestsTotal>>; terms: Feed<Report<TermsRow, TermsTotal>>;
             session: Feed<Report<AgentRow>>; restart: Feed<RestartPoint> | null; model: string | null } | null = null;
  #openedKey: string | null = null;
  /** The opened subagent's requests, read with the session's query. */
  #agentRead: Feed<Report<RequestRow, RequestsTotal>> | null = null;
  #agentPath: string | null = null;
  /** The named session's own read, for its row, while the address keeps it first. */
  #placedRead: Feed<Report<AgentRow>> | null = null;
  #placedPath: string | null = null;
  /** Today's two dearest sessions under no filter, for the Cost row's `most to`. */
  #mostToday: Feed<Report<SessionRow>> | null = null;
  #mostPath: string | null = null;
  /** Token kinds' and Prices' cost terms, under every filter, and the price table's rates
   * of their models, read once the terms have answered. */
  #terms: Feed<Report<TermsRow, TermsTotal>> | null = null;
  #prices: Feed<Prices> | null = null;
  #pricesPath: string | null = null;
  /** `Find a model`'s search, and the text it was asked with. */
  #search: Feed<Prices> | null = null;
  #searched = "";
  #searchTimer: ReturnType<typeof setTimeout> | undefined;
  /** What is open: the piece the address names. */
  #at: Address = NOTHING_OPEN;
  /** The opened limit row's read, with the window it reads and the reading it was last
   * asked for. */
  #window: Feed<OpenedWindow> | null = null;
  #windowPath: string | null = null;
  #windowKey: string | null = null;
  #asked: string | null = null;
  #askedAllTime: string | null = null;
  #resize: ResizeObserver | null = null;
  #stops: (() => void)[] = [];
  #timer: ReturnType<typeof setInterval> | undefined;
  #shown = (): void => {
    if (document.visibilityState !== "visible") return;
    void this.#limits?.refresh();
    void this.#status?.refresh();
    void this.#window?.refresh();
  };

  /** The newest day the page's answers carried, else this browser's. */
  #today(): string {
    return newestDay(this.#limits?.value?.today, this.#status?.value?.today, this.#daily?.value?.today)
      ?? LOCAL.day(Date.now() / 1000);
  }

  connectedCallback(): void {
    super.connectedCallback();
    const update = () => this.requestUpdate();
    const limits = new Feed<Limits>(() => api("usage", "api/limits"), parseLimits, update);
    const status = new Feed<Status>(() => api("usage", "api/status"), parseStatus, update);
    const daily = new Feed<Report<DayRow>>(() => api("usage", costPath(this.#today())), parseReport, update);
    this.#limits = limits;
    this.#status = status;
    this.#daily = daily;
    void limits.refresh().then(() => {
      if (this.#limits === limits) this.#stops.push(subscribe("usage", "limits", (data) => limits.topic(data)));
    });
    void status.refresh().then(() => {
      if (this.#status !== status) return;
      this.#stops.push(subscribe("usage", "index", (data) => this.#indexEvent(data)));
      const now = limits.now();
      if (shouldPass(status.value, now) && now - askedAt > 60) {
        askedAt = now;
        void this.#askPass();
      }
    });
    void daily.refresh();
    this.#stops.push(watchState(update));
    this.#resize = new ResizeObserver((entries) => {
      const found = Math.floor(entries[0]?.contentRect.width ?? 0);
      if (found > 0 && found !== this.width) this.width = found;
    });
    this.#resize.observe(this);
    document.addEventListener("visibilitychange", this.#shown);
    document.addEventListener("pointerdown", this.#outside);
    this.#timer = setInterval(() => { this.tick += 1; }, TICK_MS);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    for (const feed of [this.#limits, this.#status, this.#daily, this.#mostToday]) feed?.close();
    this.#limits = this.#status = this.#daily = this.#mostToday = null;
    this.#mostPath = null;
    this.#closeLists();
    this.#closeOpened();
    this.#placedRead?.close();
    this.#placedRead = null;
    this.#placedPath = null;
    this.#closeWindow();
    this.#closeBelow();
    for (const stop of this.#stops) stop();
    this.#stops = [];
    this.#resize?.disconnect();
    this.#resize = null;
    document.removeEventListener("visibilitychange", this.#shown);
    document.removeEventListener("pointerdown", this.#outside);
    clearInterval(this.#timer);
  }

  /** The page's range, filters and measure: one state for every piece under the filter
   * line, held for the document's life. */
  get state(): PageState {
    return heldState();
  }

  /** Changes the range, a filter or the measure; every piece that reads under them
   * reads again. */
  setState(patch: Partial<PageState>): void {
    holdState(patch);
  }

  /** What is open: the piece the address names. */
  get address(): Address {
    return this.#at;
  }

  /** Opens a piece, closing the one that was open; `{ piece: "none" }` closes it. The
   * address of the history entry the page is on is rewritten to name it. */
  open(address: Address): void {
    const next = settled(address, this.#folders());
    if (sameAddress(next, this.#at)) return;
    refusedWindow = null;
    this.sectionTable = null;
    const was = this.#at;
    const sameSession = was.piece === "sessions" && next.piece === "sessions" && was.session !== null
      && was.harness === next.harness && was.session === next.session;
    if (!sameSession) {
      this.sessionPart = null;
      this.copiedId = false;
      this.agentsShown = 0;
    }
    this.agentPart = null;
    if (next.piece !== "sessions") this.#placed = null;
    if (next.piece !== "sessions" || next.session === null) this.#openedIn = null;
    this.#closePart();
    this.#setAt(next);
    this.requestUpdate();
  }

  #setAt(address: Address): void {
    this.#at = address;
    const path = addressWrite(address, location.pathname);
    if (path !== null) history.replaceState(history.state, "", path);
  }

  #closeWindow(): void {
    this.#window?.close();
    this.#window = null;
    this.#windowPath = this.#windowKey = null;
  }

  /** The opened limit row's read follows the address and the limits: it is asked when
   * the row opens and again when the row's reading changes. The row closes when the
   * limits no longer hold it as fresh and when its read is refused. */
  #followWindow(): void {
    const at = this.#at;
    const limits = this.#limits;
    if (at.piece !== "limit" || limits === null) {
      this.#closeWindow();
      return;
    }
    if (this.#window !== null && this.#window.refused !== null) {
      refusedWindow = this.#window;
      this.#window = null;
      this.#closeWindow();
      this.#setAt(NOTHING_OPEN);
      return;
    }
    const rows = limits.value?.rows;
    if (rows === undefined) return;
    const row = openedRow(rows, at);
    if (row === null) {
      this.#closeWindow();
      this.#setAt(NOTHING_OPEN);
      return;
    }
    const path = windowPath(at);
    const key = readingKey(row);
    if (this.#window === null || this.#windowPath !== path) {
      this.#closeWindow();
      this.#window = new Feed<OpenedWindow>(() => api("usage", path), parseWindow, () => this.requestUpdate());
      this.#windowPath = path;
      void this.#window.refresh();
    } else if (key !== this.#windowKey) {
      void this.#window.refresh();
    }
    this.#windowKey = key;
  }

  /** A press outside an open menu closes it. */
  #outside = (event: Event): void => {
    if (this.menu === null) return;
    const inside = event.composedPath().some((node) => node instanceof Element && node.classList.contains("anchor"));
    if (!inside) this.#closeMenu();
  };

  #closeMenu(): void {
    this.menu = null;
    this.custom = false;
  }

  /** Every read under the filter line that is open, each once. */
  #belowFeeds(): { close(): void; refresh(): Promise<void> }[] {
    return [...new Set([this.#chart, this.#figures, this.#allTime, this.#places, this.#models, this.#placesSection,
                        this.#modelsSection, this.#terms, this.#prices, this.#sessionsTop])].filter((feed) => feed !== null);
  }

  #closeBelow(): void {
    for (const feed of this.#belowFeeds()) feed.close();
    this.#chart = this.#figures = this.#allTime = this.#places = this.#models = null;
    this.#placesSection = this.#modelsSection = this.#terms = this.#prices = this.#sessionsTop = null;
    this.#pricesPath = null;
    this.#closeLists();
    this.#closePart();
    this.#asked = this.#askedAllTime = null;
  }

  /** Whether the project filter is by folder: projects is not installed. */
  #folders(): boolean {
    return !installed("projects");
  }

  protected willUpdate(changed: Map<PropertyKey, unknown>): void {
    if (changed.has("route")) {
      // A new placement opens the piece its route names. Projects and Folders are one
      // section, and the address says the name this install shows.
      const named = parseAddress(this.route);
      const at = settled(named, this.#folders());
      if (sameAddress(at, named)) this.#at = at;
      else this.#setAt(at);
      if (at.piece !== "none") refusedWindow = null;
      this.#openedIn = null;
      this.agentsShown = 0;
      this.agentPart = null;
      this.#placed = at.piece === "sessions" && at.session !== null && at.harness !== null
        ? { harness: at.harness, id: at.session } : null;
    }
    this.#followWindow();
    const status = this.#status;
    if (status === null || !hasBelow(status)) return;
    const update = () => this.requestUpdate();
    const state = heldState();
    const today = this.#today();
    const paths = { chart: chartPath(state, today), figures: figuresPath(state, today),
                    places: placesPath(state, today, this.#folders()), models: modelsPath(state, today),
                    placesSection: placesSectionPath(state, today, this.#folders()),
                    modelsSection: modelsSectionPath(state, today), terms: termsSectionPath(state, today),
                    sessions: sessionsPath(state, today, null, 1) };
    const asked = JSON.stringify(paths);
    if (asked !== this.#asked) {
      this.#asked = asked;
      const allTime = this.#allTime;
      for (const feed of this.#belowFeeds()) if (feed !== allTime && feed !== this.#prices) feed.close();
      this.#chart = new Feed<Report<DayRow>>(() => api("usage", paths.chart), parseReport, update);
      const figures = paths.figures;
      this.#figures = figures === null ? null : new Feed<Report<DayRow>>(() => api("usage", figures), parseReport, update);
      this.#places = new Feed<Report<ProjectRow>>(() => api("usage", paths.places), parseReport, update);
      this.#models = new Feed<Report<ModelRow>>(() => api("usage", paths.models), parseReport, update);
      this.#placesSection = paths.placesSection === paths.places ? this.#places
        : new Feed<Report<ProjectRow>>(() => api("usage", paths.placesSection), parseReport, update);
      this.#modelsSection = paths.modelsSection === paths.models ? this.#models
        : new Feed<Report<ModelRow>>(() => api("usage", paths.modelsSection), parseReport, update);
      this.#terms = new Feed<Report<TermsRow, TermsTotal>>(() => api("usage", paths.terms), parseReport, update);
      this.#sessionsTop = new Feed<Report<SessionRow>>(() => api("usage", paths.sessions), parseReport, update);
      for (const feed of this.#belowFeeds()) if (feed !== allTime && feed !== this.#prices) void feed.refresh();
    }
    this.#followLists(state, today);
    this.#followOpened(state, today);
    // Today's two dearest sessions, for the Cost row, under no filter.
    const mostPath = sessionsPath(START, today, { since: today, until: today }, 2);
    if (mostPath !== this.#mostPath) {
      this.#mostPath = mostPath;
      this.#mostToday?.close();
      this.#mostToday = new Feed<Report<SessionRow>>(() => api("usage", mostPath), parseReport, update);
      void this.#mostToday.refresh();
    }
    // The price table is read for the models the terms answer holds, again when they change.
    const terms = this.#terms?.value ?? null;
    const pricesPath = terms === null ? null : pricesFor(terms);
    if (pricesPath !== null && pricesPath !== this.#pricesPath) {
      this.#pricesPath = pricesPath;
      this.#prices?.close();
      this.#prices = new Feed<Prices>(() => api("usage", pricesPath), parsePrices, update);
      void this.#prices.refresh();
    }
    // All time's figure is asked when the Range menu first opens, and again under new filters.
    const allTime = allTimePath(state);
    if ((this.menu === "range" || this.#askedAllTime !== null) && allTime !== this.#askedAllTime) {
      this.#askedAllTime = allTime;
      this.#allTime?.close();
      this.#allTime = new Feed<Report<DayRow>>(() => api("usage", allTime), parseReport, update);
      void this.#allTime.refresh();
    }
  }

  /** The opened Sessions section's lists follow the address, the range and the filters:
   * read when the section opens, again under new ones, and closed with the section. */
  #followLists(state: PageState, today: string): void {
    const lists = this.#at.piece === "sessions" ? sessionLists(state.range, today) : null;
    if (lists === null) {
      this.#closeLists();
      return;
    }
    const firstPath = (top: number, skip: number): string => sessionsPath(state, today, lists.first, top, skip);
    const earlier = lists.earlier;
    const key = JSON.stringify([firstPath(1, 0), earlier === null ? null : sessionsPath(state, today, earlier, 1)]);
    if (key === this.#listsKey) return;
    this.#closeLists();
    const update = () => this.requestUpdate();
    const ask = (path: string) => api("usage", path);
    this.#lists = lists;
    this.#listsKey = key;
    this.#firstList = new SessionList(ask, firstPath, FIRST_TOP, update);
    void this.#firstList.refresh();
    if (earlier !== null) {
      const top = sessionsPath(state, today, earlier, 1);
      this.#earlierTop = new Feed<Report<SessionRow>>(() => api("usage", top), parseReport, update);
      void this.#earlierTop.refresh();
    }
  }

  #closeLists(): void {
    for (const feed of [this.#firstList, this.#earlierTop, this.#earlierList]) feed?.close();
    this.#firstList = this.#earlierTop = this.#earlierList = null;
    this.#lists = this.#listsKey = null;
  }

  /** The opened session's reads follow the address, the list it was opened from, the
   * range and the filters; the restart point is asked once the requests name a model. */
  #followOpened(state: PageState, today: string): void {
    const placed = this.#placed;
    const placedPath = placed === null ? null : openedPaths(placed.id, openedKeys(state, today, null, placed.harness)).session;
    if (placedPath !== this.#placedPath) {
      this.#placedRead?.close();
      this.#placedRead = null;
      this.#placedPath = placedPath;
      if (placedPath !== null) {
        this.#placedRead = new Feed<Report<AgentRow>>(() => api("usage", placedPath), parseReport, () => this.requestUpdate());
        void this.#placedRead.refresh();
      }
    }
    const at = this.#at;
    if (at.piece !== "sessions" || at.session === null || at.harness === null) {
      this.#closeOpened();
      return;
    }
    const from = this.#openedIn;
    const days = from === null ? null : from === "first" ? this.#lists?.first ?? null : this.#lists?.earlier ?? null;
    if (from !== null && days === null) {
      this.#closeOpened();
      return;
    }
    const keys = openedKeys(state, today, days, at.harness);
    const paths = openedPaths(at.session, keys);
    const key = JSON.stringify(paths);
    const update = () => this.requestUpdate();
    if (key !== this.#openedKey) {
      this.#closeOpened();
      this.#openedKey = key;
      this.#openedReads = {
        requests: new Feed<Report<RequestRow, RequestsTotal>>(() => api("usage", paths.requests), parseReport, update),
        terms: new Feed<Report<TermsRow, TermsTotal>>(() => api("usage", paths.terms), parseReport, update),
        session: new Feed<Report<AgentRow>>(() => api("usage", paths.session), parseReport, update),
        restart: null, model: null,
      };
      for (const feed of [this.#openedReads.requests, this.#openedReads.terms, this.#openedReads.session]) void feed.refresh();
    }
    const opened = this.#openedReads;
    const rows = opened?.requests.value?.rows ?? null;
    const model = rows === null ? null : newestModel(rows);
    if (opened !== null && model !== null && model !== opened.model) {
      opened.restart?.close();
      opened.model = model;
      const path = restartPath(model);
      opened.restart = new Feed<RestartPoint>(() => api("usage", path), parseRestartPoint, update);
      void opened.restart.refresh();
    }
    // The opened subagent's requests, with the session's query.
    const agent = at.agent === null || at.agent === MAIN ? null : agentPath(at.session, at.agent, keys);
    if (agent !== this.#agentPath) {
      this.#closeAgent();
      this.#agentPath = agent;
      if (agent !== null) {
        this.#agentRead = new Feed<Report<RequestRow, RequestsTotal>>(() => api("usage", agent), parseReport, update);
        void this.#agentRead.refresh();
      }
    }
  }

  #closeAgent(): void {
    this.#agentRead?.close();
    this.#agentRead = null;
    this.#agentPath = null;
  }

  #openedFeeds(): { close(): void; refresh(): Promise<void> }[] {
    const opened = this.#openedReads;
    return [...(opened === null ? [] : [opened.requests, opened.terms, opened.session, opened.restart]),
            this.#placedRead, this.#agentRead].filter((feed) => feed !== null);
  }

  #closeOpened(): void {
    const opened = this.#openedReads;
    if (opened !== null) for (const feed of [opened.requests, opened.terms, opened.session, opened.restart]) feed?.close();
    this.#openedReads = null;
    this.#openedKey = null;
    this.#closeAgent();
  }

  /** `Show` under the earlier line: its rows, thirty at a time. */
  #showEarlier(): void {
    const lists = this.#lists;
    const earlier = lists?.earlier ?? null;
    if (earlier === null || this.#earlierList !== null) return;
    const state = heldState();
    const today = lists?.today ?? this.#today();
    this.#earlierList = new SessionList((path) => api("usage", path),
      (top, skip) => sessionsPath(state, today, earlier, top, skip), PAGE, () => this.requestUpdate());
    void this.#earlierList.refresh();
    this.requestUpdate();
  }

  #indexEvent(data: unknown): void {
    const event = parseIndexEvent(data);
    if (event === null) return;
    this.#event = event;
    this.#events += 1;
    if (!event.running) {
      void this.#status?.refresh();
      void this.#daily?.refresh();
      for (const feed of [...this.#belowFeeds(), this.#window, this.#mostToday, this.#firstList, this.#earlierTop,
                          this.#earlierList, ...this.#openedFeeds()]) void feed?.refresh();
    }
    this.requestUpdate();
  }

  async #askPass(): Promise<void> {
    this.#pass = null;
    const seen = this.#events;
    const outcome = passOutcome(await api("usage", "api/pass", {}));
    if (this.#status === null) return;
    this.#pass = passLine(outcome);
    if (outcome.outcome === "started" && this.#events === seen) {
      // The topic hasn't said yet that the pass began; a short pass may have begun and
      // ended before this answer came, and then its events already told.
      this.#event = { running: true, last_pass: this.#event?.last_pass ?? null, note: null };
    }
    this.requestUpdate();
  }

  async #copyId(text: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      this.copiedId = true;
    } catch {
      this.copiedId = false;
    }
  }

  async #copy(text: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      this.copied = true;
    } catch {
      this.copied = false;
    }
  }

  render() {
    const limits = this.#limits;
    const status = this.#status;
    const daily = this.#daily;
    if (limits === null || status === null || daily === null) return nothing;
    const state = heldState();
    const today = this.#today();
    const chart = this.#chart;
    const report = chart?.value ?? null;
    // The range's total, for the line that says it is not whole.
    const range: Sum | null = report === null || report.rows.length === 0 ? null
      : sumRows([{ ...report.total, unpriced_models: report.unpriced_models }]);
    const others: OtherRead[] = [];
    const other = (what: string, read: OtherRead["read"] | null): void => {
      if (read !== null) others.push({ what, read, report: true });
    };
    other("The chart", chart);
    other("The range's figures", this.#figures);
    other(this.#folders() ? "The folders" : "The projects", this.#places);
    other("The models", this.#models);
    if (this.#placesSection !== this.#places) {
      other(this.#folders() ? "The Folders section" : "The Projects section", this.#placesSection);
    }
    if (this.#modelsSection !== this.#models) other("The Models section", this.#modelsSection);
    other("Token kinds and Prices", this.#terms);
    other("The Sessions section", this.#sessionsTop);
    other("The sessions listed", this.#firstList?.feed ?? null);
    other("The earlier sessions", this.#earlierTop);
    other("The earlier sessions listed", this.#earlierList?.feed ?? null);
    other("Today's dearest sessions", this.#mostToday);
    other("The opened session's requests", this.#openedReads?.requests ?? null);
    other("The opened session's cost terms", this.#openedReads?.terms ?? null);
    other("The opened session's agents", this.#openedReads?.session ?? null);
    other("The session the address names", this.#placedRead);
    other("The opened subagent's requests", this.#agentRead);
    const restart = this.#openedReads?.restart ?? null;
    if (restart !== null) others.push({ what: "The restart point", read: restart, report: false });
    if (this.#prices !== null) others.push({ what: "The price table", read: this.#prices, report: false });
    const windowRead = this.#window ?? refusedWindow;
    if (windowRead !== null) others.push({ what: "The opened limit", read: windowRead, report: false });
    const at = this.#at;
    const openedSource = at.piece === "limit" ? openedRow(limits.value?.rows ?? [], at) : null;
    const opened = at.piece !== "limit" || openedSource === null ? null : {
      key: `${at.harness}/${at.limit}/${at.minutes}`,
      model: openedLimit({ window: this.#window?.value ?? null, loading: this.#window?.loading ?? true,
                           row: openedSource, now: limits.now(), name: (ref) => sessionName(ref, today).name }),
    };
    const frame = frameModel({
      limits, status, daily, event: this.#event, pass: this.#pass, most: this.#most(today), range, others,
      unknownAddress: at.piece === "unknown", now: limits.now(),
    });
    const groups = frame.limits.groups;
    const line = !frame.below ? null : filterLine({
      state, today, figures: this.#figures === null ? daily.value : this.#figures.value,
      allTime: this.#allTime?.value ?? null, places: this.#places?.value ?? null, folders: this.#folders(),
      models: this.#models?.value ?? null, lanes: status.value?.lanes ?? null,
    });
    const group = !frame.below || report === null ? null
      : chartGroup(report, state.range, state.measure, today, this.asTable);
    // With nothing in the range or under the filters no section is drawn.
    const sections = !frame.below ? [] : group?.nothing ? (this.#placed === null ? [] : [this.#sessions(null, today)]) : [
      this.#sessions(sessionsClosed(this.#sessionsTop?.value ?? null, today), today),
      this.#section(this.#folders() ? "folders" : "projects", this.#placesSection,
                    placesSection(this.#placesSection?.value ?? null, this.#folders())),
      this.#section("models", this.#modelsSection, modelsSection(this.#modelsSection?.value ?? null)),
      this.#kinds(kindsSection(this.#terms?.value ?? null)),
      this.#pricesGroup(pricesSection(this.#terms?.value ?? null, this.#prices?.value ?? null, today)),
    ].filter((drawn) => drawn !== nothing);
    return html`
      ${frame.reading === null ? nothing : html`<p class="reading dim" data-part="reading">${frame.reading}</p>`}
      ${frame.limits.warning === null ? nothing : this.#warning(frame.limits.warning)}
      ${groups.length || frame.cost ? html`<div class="grps" data-part="top">
        ${groups.map((group) => this.#group(group, opened))}
        ${frame.cost === null ? nothing : this.#cost(frame.cost)}
      </div>` : nothing}
      ${frame.nothing === null ? nothing : this.#nothing(frame.nothing)}
      ${line === null ? nothing : this.#filters(line)}
      ${group === null && sections.length === 0 ? nothing : html`<div class="grps below" data-part="below">${
        group === null ? nothing : this.#chartGroup(group)}${sections}</div>`}
      ${frame.foot === null ? nothing : this.#foot(frame.foot)}
      ${frame.lines.length ? this.#lines(frame.lines) : nothing}`;
  }

  #warning(warning: Warning) {
    return html`<div class="warnline" data-part="warning"><b>${warning.lead}</b>${
      warning.before === null ? nothing : html`, ${warning.before}`}${
      warning.more === null ? nothing : html`<span class="sep">·</span>${warning.more}`}</div>`;
  }

  #group(group: LimitGroup, opened: { key: string; model: OpenedLimit } | null) {
    return html`<section class="grp" data-group="limits" data-harness=${group.harness}>
      <div class="gk">${group.name}${group.read === null ? nothing : html`<small>${group.read}</small>`}</div>
      <div class="gl">${group.rows.map((row) =>
        this.#limit(row, opened !== null && opened.key === row.key ? opened.model : null))}</div>
    </section>`;
  }

  /** One window's row. A row that opens is a link to its address; the opened one is a
   * link that closes it, says so at its right, and has its lines under it. */
  #limit(row: LimitRow, opened: OpenedLimit | null) {
    if (row.state === "reset") {
      return html`<div class="lim reset" data-limit=${row.key} data-state="reset">${row.label}${
        row.resets === null ? nothing : html`<span class="sep">·</span>${row.resets}`}</div>`;
    }
    const meter = row.meter;
    const when = [row.fills === null ? null : html`<b>${row.fills}</b>`, row.resets]
      .filter((part) => part !== null);
    const cells = html`
      <span>${row.label}</span>
      ${meter === null ? html`<span></span>` : html`<svg class="m" role="img" aria-label=${meter.title}
        viewBox="0 0 ${METER.width} ${METER.height}">${svg`<title>${meter.title}</title>
        <rect class="base" x="0" y="4" width=${METER.width} height="1"></rect>
        <rect class="fill" x="0" y="3" width=${METER.width * meter.fill / 100} height="3"></rect>
        ${meter.tick === null ? nothing : svg`<rect class="tick" x=${Math.min(METER.width - 1, METER.width * meter.tick / 100)}
          y="0" width="1" height=${METER.height}></rect>`}`}</svg>`}
      <span class="u">${row.used ?? nothing}</span>
      <span class="w"><span>${when.map((part, i) => html`${i ? ", " : nothing}${part}`)}${
        row.read === null ? nothing : html`${when.length ? html`<span class="sep">·</span>` : nothing}${row.read}`}</span>${
        opened === null ? nothing : html`<span class="x" data-part="close">${opened.close}</span>`}</span>`;
    if (row.route === null || row.minutes === null) {
      return html`<div class="lim" data-limit=${row.key} data-state=${row.state}>${cells}</div>`;
    }
    const target = opened === null ? limitAddress(row.harness, row.limit_id, row.minutes) : NOTHING_OPEN;
    return html`<a class=${opened === null ? "lim" : "lim open"} href=${addressPath(target)} data-limit=${row.key}
      data-state=${row.state} aria-expanded=${String(opened !== null)}
      @click=${(event: MouseEvent) => this.#follow(event, target)}>${cells}</a>${
      opened === null ? nothing : this.#opened(opened)}`;
  }

  /** The lines under an opened limit row. */
  #opened(model: OpenedLimit) {
    const used = model.used;
    if (model.reading === null && model.lines.length === 0 && used === null && model.notes.length === 0) return nothing;
    return html`<div class="det" data-part="opened">
      ${model.reading === null ? nothing : html`<p class="d" data-opened="reading">${model.reading}</p>`}
      ${model.lines.map((line) => html`<div class="r" data-opened=${line.key}>
        <span class="k">${line.label}</span>
        <span class="f">${line.text}${line.dim === null ? nothing : html` <span class="d">· ${line.dim}</span>`}</span>
      </div>`)}
      ${used === null ? nothing : html`<div class="r" data-opened="used">
        <span class="k">${used.label}</span>
        <span class="f">${used.rows.map((row) => html`<div class="ul" data-part="used-row">
          <span>${row.name} <span class="d">· ${row.requests}</span></span><span class="n">${row.cost}</span></div>`)}
          <div class="ul d" data-part="used-total"><span>${used.total}</span><span>${used.cost}</span></div></span>
      </div>`}
      ${model.notes.map((note) => html`<p class="d" data-opened="note">${note}</p>`)}
    </div>`;
  }

  /** A plain press on a link of the page opens its piece in place. */
  #follow(event: MouseEvent, address: Address): void {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    this.open(address);
  }

  #cost(cost: Cost) {
    return html`<section class="grp" data-group="cost">
      <div class="gk">${cost.label}</div>
      <div class="gl">
        ${cost.rows.map((row, i) => html`
          <div class="one" data-cost=${row.key}>
            <span><span class="pk">${row.label}</span><span class="fig" title=${row.why ?? nothing}>${row.figure}</span></span>
            <span class="t" title=${row.beforeWhy ?? nothing}>${row.before}</span>
          </div>
          ${i === 0 && cost.most !== null ? html`<span class="one2 ind" data-part="most">${cost.most}</span>` : nothing}`)}
        <div class="capl" data-part="caption">
          <span>${cost.caption} <a href=${addressPath({ piece: "prices" })}
            @click=${(event: MouseEvent) => this.#follow(event, { piece: "prices" })}>${cost.prices}</a></span>
          ${cost.read === null ? nothing : html`<span data-part="read">${cost.read}</span>`}
        </div>
      </div>
    </section>`;
  }

  /** The filter line. */
  #filters(line: FilterLine) {
    const menus = [line.range, line.place, line.harness, line.model].filter((menu): menu is Menu => menu !== null);
    return html`<div class="filt" data-part="filters" @keydown=${(event: KeyboardEvent) => {
      if (event.key !== "Escape" || this.menu === null) return;
      const open = this.menu;
      this.#closeMenu();
      void this.updateComplete.then(() =>
        this.renderRoot.querySelector<HTMLElement>(`[data-filter="${open}"] button.pick`)?.focus());
    }}>
      <span class="fl">${menus.map((menu, i) => html`${i ? html`<span class="sep">·</span>` : nothing}${
        this.#filter(menu, line)}`)}</span>
      <span class="tog" role="group" data-part="measure">${line.measures.map((measure) => html`<button type="button"
        data-measure=${measure.key} aria-pressed=${String(measure.picked)}
        @click=${() => { holdState({ measure: measure.key }); }}>${measure.label}</button>`)}</span>
    </div>`;
  }

  /** One filter: its name, the button that says what it is set to, and its menu. */
  #filter(menu: Menu, line: FilterLine) {
    const open = this.menu === menu.key;
    return html`<span class="f" data-filter=${menu.key}><span class="k">${menu.label}</span><span class="anchor">
      <button type="button" class=${menu.set ? "pick set" : "pick"} aria-haspopup="menu" aria-expanded=${String(open)}
        @click=${() => { if (open) this.#closeMenu(); else { this.menu = menu.key; this.custom = false; } }}>${
        menu.value}<span class="v" aria-hidden="true">▾</span></button>
      ${open ? html`<div class="menu" role="menu" aria-label=${menu.label} data-menu=${menu.key}>
        ${menu.items.map((item) => html`${item.ruled ? html`<hr>` : nothing}${this.#item(item)}${
          item.patch === null && this.custom ? this.#custom(line) : nothing}`)}
      </div>` : nothing}</span></span>`;
  }

  #item(item: MenuItem) {
    const patch = item.patch;
    return html`<button type="button" class="it" role="menuitemradio" aria-checked=${String(item.picked)}
      data-item=${item.key} @click=${() => {
        if (patch === null) { this.custom = !this.custom; return; }
        this.#closeMenu();
        holdState(patch);
      }}><span aria-hidden="true">${item.picked ? "✓" : nothing}</span><span>${item.label}</span>${
      item.figure === null ? html`<span></span>` : html`<span class="t">${item.figure}</span>`}</button>`;
  }

  /** The two days of `From … to …`. */
  #custom(line: FilterLine) {
    const days = line.custom;
    const set = (event: Event) => {
      event.preventDefault();
      const form = event.currentTarget as HTMLFormElement;
      const day = (name: string) => (form.elements.namedItem(name) as HTMLInputElement).value;
      const range = customRange(day("since"), day("until"));
      if (range === null) return;
      this.#closeMenu();
      holdState({ range });
    };
    return html`<form class="custom" data-part="custom" @submit=${set}>
      <label>${days.from} <input type="date" name="since" required .value=${days.since}></label>
      <label>${days.to} <input type="date" name="until" required .value=${days.until}></label>
      <button type="submit">${days.act}</button>
    </form>`;
  }

  /** The chart's group: its sentence and one of the chart, its table, or the sentence
   * that nothing was used. */
  #chartGroup(group: ChartGroup) {
    // The group's label column is beside the chart only on a wide page.
    const wide = !window.matchMedia("(max-width: 720px)").matches;
    const width = Math.max(120, this.width - (wide ? 100 : 0));
    // Beside the label column the first day's name may reach left of the chart.
    const hang = wide ? 24 : 0;
    return html`<section class="grp" data-group="chart">
      <div class="gk">${group.label}</div>
      <div class="gl">
        ${group.nothing === null ? nothing : html`<p class="none" data-part="none">${group.nothing}</p>`}
        ${group.sentence === null && group.toggle === null ? nothing : html`<div class="chartl">
          <span data-part="sentence">${group.sentence ?? nothing}</span>
          ${group.toggle === null ? nothing : html`<button type="button" class="act" data-part="table-toggle"
            @click=${() => { this.asTable = !this.asTable; }}>${group.toggle}</button>`}</div>`}
        ${group.chart === null ? nothing : this.#chartSvg(chartDraw(group.chart, width, hang), group.chart.aria)}
        ${group.table === null ? nothing : this.#table(group.table)}
      </div>
    </section>`;
  }

  #chartSvg(draw: ChartDraw, aria: string) {
    return html`<svg class="ch" data-part="chart" role="img" aria-label=${aria} width=${draw.width} height=${draw.height}
      viewBox="0 0 ${draw.width} ${draw.height}">${svg`
      ${draw.grid === null ? nothing : svg`<line class="gr" x1="0" x2=${draw.width} y1=${draw.grid.y} y2=${draw.grid.y}></line>
        <text data-part="top" x="0" y="4">${draw.grid.words}</text>`}
      ${draw.bars.map((bar) => svg`<g data-period=${bar.period} data-whole=${String(bar.outline === null)}>
        <title>${bar.hover}</title>
        <rect class=${bar.now ? "bar now" : "bar"} x=${bar.x} y=${bar.y} width=${bar.width} height=${bar.height} rx="1"></rect>
        ${bar.outline === null ? nothing : svg`<rect class="dash" x=${bar.outline.x} y=${bar.outline.y}
          width=${bar.outline.width} height=${bar.outline.height}></rect>`}
        <rect class="hit" x=${bar.slot.x} y="0" width=${bar.slot.width} height=${draw.axis}></rect>
      </g>`)}
      <line class="ax" x1="0" x2=${draw.width} y1=${draw.axis} y2=${draw.axis}></line>
      ${draw.labels.map((label) => svg`<text class=${label.now ? "v" : ""} x=${label.x} y=${draw.labelY}
        text-anchor=${label.anchor}>${label.text}</text>`)}
      ${draw.value === null ? nothing : svg`<text class="v" data-part="value" x=${draw.value.x} y=${draw.value.y}
        text-anchor=${draw.value.anchor}>${draw.value.text}</text>`}`}</svg>`;
  }

  #table(table: Table) {
    return html`<div class="scroll"><table class="tbl" data-part="table">
      <thead><tr>${table.columns.map((column) => html`<th class=${column.num ? "n" : ""}>${column.label}</th>`)}</tr></thead>
      <tbody>${table.rows.map((row) => html`<tr data-row=${row.key}>${row.cells.map((cell, i) =>
        html`<td class=${table.columns[i]?.num ? "n" : ""}>${cell}</td>`)}</tr>`)}</tbody>
    </table></div>`;
  }

  /** Whether the address opens this section. */
  #isOpen(key: SectionKey): boolean {
    const piece = this.#at.piece;
    return piece === key || (key !== "models" && (piece === "projects" || piece === "folders"));
  }

  /** A section: one row stating its answer, with its count as the link that opens it;
   * opened, its rows, the sentence under them and its table. While its read is out an
   * opened section says so; a closed one is absent until the read has answered. */
  #section(key: SectionKey, feed: Feed<unknown> | null, model: Section | null) {
    const open = this.#isOpen(key);
    if (model === null) {
      if (!open || feed === null || feed.value !== null || !feed.loading) return nothing;
      return html`<section class="grp is-open" data-group=${key}>
        <div class="gk">${SECTION_LABELS[key]}</div>
        <div class="gl"><p class="none" data-part="reading">${READING}</p></div>
      </section>`;
    }
    const target = open ? NOTHING_OPEN : { piece: key } as Address;
    const shown = open && this.sectionTable === key;
    return html`<section class=${open ? "grp is-open" : "grp"} data-group=${key}>
      <div class="gk">${model.label}</div>
      <div class="gl">
        <div class="one"><span data-part="closed">${model.closed}</span><a class="t" data-part="count"
          href=${addressPath(target)} aria-expanded=${String(open)}
          @click=${(event: MouseEvent) => this.#follow(event, target)}>${open ? CLOSE : model.count}</a></div>
        ${open ? html`<div class="rows" data-part="rows">${model.rows.map((row) => {
          const cells = html`<span class="nm">${row.name}</span><span class="n d">${row.share ?? nothing}</span>
            <span class="n" title=${row.why ?? nothing}>${row.cost}</span>`;
          const patch = row.patch;
          return patch === null ? html`<div class="tr2" data-row=${row.key}>${cells}</div>`
            : html`<a class="tr2" data-row=${row.key} href=${addressPath(SESSIONS)}
                @click=${(event: MouseEvent) => this.#filterTo(event, patch)}>${cells}</a>`;
        })}
          ${model.sentence === null ? nothing : html`<p data-part="sentence">${model.sentence.map((part) => this.#part(part))}</p>`}
          <p class="acts"><button type="button" class="act" data-part="table-toggle" aria-expanded=${String(shown)}
            @click=${() => { this.sectionTable = shown ? null : key; }}>${shown ? HIDE_TABLE : SECTION_TABLE}</button></p>
          ${shown ? this.#table(model.table) : nothing}
        </div>` : nothing}
      </div>
    </section>`;
  }

  /** `most to <session> $4.21, <session> $3.67`: today's two dearest sessions, named. */
  #most(today: string): string | null {
    const rows = this.#mostToday?.value?.rows ?? [];
    return mostTo(rows.map((row) => ({ name: sessionName(row, today).name, cost: row.cost })));
  }

  /** Sessions: the range's dearest session and how many there are; opened, the range's
   * last seven days dearest first, what they leave out, the earlier days, and the table. */
  #sessions(model: SessionsClosed | null, today: string) {
    const open = this.#at.piece === "sessions";
    if (model === null && open && this.#placed !== null && !(this.#sessionsTop?.loading ?? false)) {
      // The range holds no session, and the address names one: its row stands alone.
      return html`<section class="grp is-open" data-group="sessions">
        <div class="gk">${SESSIONS_LABEL}</div>
        <div class="gl">
          <div class="one"><span data-part="closed"></span><a class="t" data-part="count" href=${addressPath(NOTHING_OPEN)}
            aria-expanded="true" @click=${(event: MouseEvent) => this.#follow(event, NOTHING_OPEN)}>${CLOSE}</a></div>
          ${this.#placedRow(today)}
        </div>
      </section>`;
    }
    if (model === null) {
      const feed = this.#sessionsTop;
      if (!open || feed === null || feed.value !== null || !feed.loading) return nothing;
      return html`<section class="grp is-open" data-group="sessions">
        <div class="gk">${SESSIONS_LABEL}</div>
        <div class="gl"><p class="none" data-part="reading">${READING}</p></div>
      </section>`;
    }
    const target = open ? NOTHING_OPEN : SESSIONS;
    return html`<section class=${open ? "grp is-open" : "grp"} data-group="sessions">
      <div class="gk">${SESSIONS_LABEL}</div>
      <div class="gl">
        <div class="one"><span data-part="closed">${model.closed ?? nothing}</span><a class="t" data-part="count"
          href=${addressPath(target)} aria-expanded=${String(open)}
          @click=${(event: MouseEvent) => this.#follow(event, target)}>${open ? CLOSE : model.count}</a></div>
        ${open ? this.#sessionLists(today) : nothing}
      </div>
    </section>`;
  }

  /** The row of the session the address named, first in the section, with the
   * session's whole figures; `Reading…` in its place while its read is out. */
  #placedRow(today: string) {
    const placed = this.#placed;
    const session = this.#placedRead;
    if (placed === null || session === null) return nothing;
    if (session.value === null) return session.loading ? html`<p class="none" data-part="reading">${READING}</p>` : nothing;
    // The models of its requests are known when it is the session opened from it.
    const own = this.#at.piece === "sessions" && this.#at.session === placed.id && this.#openedIn === null;
    const row = placedRow(session.value, own ? this.#openedReads?.requests.value?.rows ?? null : null);
    if (row === null) return nothing;
    return html`<div class="ses" data-list="placed">${this.#sessionRow(sessionLine(row, today, !this.#folders()), "placed")}</div>`;
  }

  #sessionLists(today: string) {
    const lists = this.#lists;
    const first = this.#firstList?.feed ?? null;
    const placedRow = this.#placedRow(today);
    if (lists === null || first === null) return placedRow;
    if (first.value === null) {
      return html`${placedRow}${first.loading ? html`<p class="none" data-part="reading">${READING}</p>` : nothing}`;
    }
    const projects = !this.#folders();
    // The session the address named shows once, first.
    const placed = this.#placed;
    const other = (row: SessionRow): boolean => placed === null || row.harness !== placed.harness
      || row.session_id !== placed.id;
    const firstRows = first.value.rows.filter(other);
    const earlier = earlierLine(lists, this.#earlierTop?.value ?? null);
    const shown = this.#earlierList?.feed ?? null;
    const heading = firstHeading(lists);
    const earlierRows = (shown?.value?.rows ?? []).filter(other);
    const parts: TablePart[] = [{ label: heading.replace(/, dearest first$/, ""), rows: firstRows },
                                { label: "Earlier", rows: earlierRows }];
    const table = this.sectionTable === "sessions";
    return html`
      ${placedRow}
      ${firstRows.length ? html`<div class="sub1" data-part="heading">${heading}</div>` : nothing}
      <div class="ses" data-list="first">${firstRows.map((row) => this.#sessionRow(sessionLine(row, today, projects), "first"))}</div>
      ${this.#more(remainder(first.value, lists.week), "first", () => { void this.#firstList?.show(); })}
      ${earlier === null ? nothing : this.#more(earlier, "earlier", shown === null ? () => this.#showEarlier() : null)}
      ${shown === null ? nothing : shown.value === null
        ? (shown.loading ? html`<p class="none" data-part="reading">${READING}</p>` : nothing)
        : html`<div class="ses" data-list="earlier">${earlierRows.map((row) =>
            this.#sessionRow(sessionLine(row, today, projects), "earlier"))}</div>
          ${this.#more(remainder(shown.value, false), "earlier-more", () => { void this.#earlierList?.show(); })}`}
      <p class="acts"><button type="button" class="act" data-part="table-toggle" aria-expanded=${String(table)}
        @click=${() => { this.sectionTable = table ? null : "sessions"; }}>${table ? HIDE_TABLE : SECTION_TABLE}</button></p>
      ${table ? this.#table(sessionsTable(parts, today, projects,
        this.#places?.value?.home ?? this.#placesSection?.value?.home ?? null)) : nothing}`;
  }

  /** A line under a list: what it leaves out, and `Show`; with no act, the line alone. */
  #more(line: ListLine | null, key: string, show: (() => void) | null) {
    if (line === null) return nothing;
    return html`<div class="more" data-part="more" data-more=${key}><span>${line.text}</span>${show === null ? nothing
      : html`<button type="button" class="act" data-act="show" @click=${show}>${SHOW}</button>`}</div>`;
  }

  /** One session as a two-line row. A row with a session opens it under its lines and
   * its act says so; a row with no session id has no act and does not open. A session in
   * both lists opens in the one it was opened from, and reads that list's days. */
  #sessionRow(line: SessionLine, list: "first" | "earlier" | "placed") {
    const at = this.#at;
    const session = line.session;
    const open = session !== null && at.piece === "sessions" && at.harness === session.harness
      && at.session === session.id && (this.#openedIn ?? "placed") === list;
    const target = session === null ? null : open ? SESSIONS : sessionAddress(session.harness, session.id);
    const go = (): void => {
      if (target === null) return;
      this.open(target);
      if (!open) this.#openedIn = list === "placed" ? null : list;
      this.requestUpdate();
    };
    return html`<moxi-ui-row ?toggles=${target !== null} ?open=${open} data-key=${line.key} name=${line.name}
        where=${line.where ?? ""} fact=${line.cost} lead=${line.requests} lead-more=${line.span ?? ""}
        rest=${line.models ?? ""} tail=${line.context ?? ""} @moxi-row-toggle=${go}>
      ${target === null ? nothing : html`<a slot="acts" class="do" data-action="open" href=${addressPath(target)}
        aria-expanded=${String(open)} @click=${(event: MouseEvent) => {
          if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          event.preventDefault();
          go();
        }}>${open ? CLOSE : OPEN}</a>`}
      ${open && session !== null ? this.#openedBody(session.harness, session.id) : nothing}
    </moxi-ui-row>`;
  }

  /** The opened session under its row: its lines, its chart, the notes under the chart,
   * and its acts, with the table or the bill opened under them. */
  #openedBody(harness: string, id: string) {
    const opened = this.#openedReads;
    if (opened === null) return nothing;
    const today = this.#today();
    const model: OpenedSession = openedSession({ harness, id, requests: opened.requests, terms: opened.terms,
                                                 session: opened.session, restart: opened.restart, today });
    const requests = opened.requests.value;
    const width = this.#detWidth();
    const chart = requests === null ? null : sessionChart(requests, heldState().measure, width, today);
    const part = this.sessionPart;
    const agents = opened.session.value;
    const lines = agents === null ? [] : subagentLines(agents, today);
    const shown = shownCount(lines, this.agentsShown, this.#at.piece === "sessions" ? this.#at.agent : null);
    const act = (key: "table" | "bill", label: string) => html`<button type="button" class="act d" data-act=${key}
      aria-expanded=${String(part === key)} @click=${() => { this.sessionPart = part === key ? null : key; }}>${label}</button>`;
    const sessions = model.sessions;
    const route = sessions === null ? "" : `${encodeURIComponent(sessions.harness)}/${encodeURIComponent(sessions.id)}`;
    const toggle = () => {
      if (shown > 0) {
        this.agentsShown = 0;
        if (this.#at.piece === "sessions" && this.#at.agent !== null) this.open(sessionAddress(harness, id));
      } else {
        this.agentsShown = 1;
      }
    };
    return html`<div class="det" data-part="session">
      ${model.items.map((item) => item.kind === "line" && item.key === "subagents" && lines.length
        ? html`${this.#line(item, html`<button type="button" class="x act" data-act="agents"
            aria-expanded=${String(shown > 0)} @click=${toggle}>${shown > 0 ? HIDE : SHOW}</button>`)}${
            shown > 0 && agents !== null ? this.#agents(harness, id, agents, lines, shown) : nothing}`
        : this.#detItem(item, chart === null ? nothing : html`<div class="cw">${this.#sessionChart(chart)}</div>`, model.notes))}
      <div class="foot" data-part="session-acts">
        ${sessions === null ? nothing : html`<a class="act" href=${`/m/sessions/${route}`} @click=${(event: MouseEvent) => {
          if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          event.preventDefault();
          navigate("sessions", route);
        }}>${OPEN_IN_SESSIONS}</a>`}
        ${model.table === null ? nothing : act("table", SECTION_TABLE)}
        ${model.bill ? act("bill", BILL) : nothing}
        <button type="button" class="act d" data-act="copy-id" @click=${() => { void this.#copyId(id); }}>${
          this.copiedId ? COPIED : COPY_ID}</button>
      </div>
      ${part === "table" && model.table !== null ? html`<div class="under" data-part="session-table">${this.#table(model.table)}</div>`
        : part === "bill" && opened.terms.value !== null ? this.#billOf(opened.terms.value) : nothing}
    </div>`;
  }

  /** The width the opened session's lines leave a chart; with `agent`, the width an
   * opened subagent's lines leave one. On a phone the subagents take the whole width. */
  #detWidth(agent = false): number {
    const wide = !window.matchMedia("(max-width: 720px)").matches;
    const left = wide ? 96 + 76 * (agent ? 2 : 1) : 76;
    return Math.max(200, this.width - left);
  }

  /** The width the subagents' map takes: their column, the whole width on a phone. */
  #mapWidth(): number {
    return window.matchMedia("(max-width: 720px)").matches ? Math.max(200, this.width) : this.#detWidth();
  }

  /** One item of an opened session's or subagent's lines: a line, `Reading…`, or the
   * chart with the notes under it. */
  #detItem(item: Item, chart: unknown, notes: string[]) {
    if (item.kind === "reading") return html`<p class="d" data-part="reading" data-reading=${item.key}>${READING}</p>`;
    if (item.kind === "chart") {
      return chart === nothing ? nothing : html`<div class="r" data-line="chart"><span class="k"></span>
        <span class="f">${chart}${notes.map((note) => html`<p class="d" data-part="note">${note}</p>`)}</span></div>`;
    }
    return this.#line(item, nothing);
  }

  #line(item: Extract<Item, { kind: "line" }>, act: unknown) {
    return html`<div class="r" data-line=${item.key}><span class="k">${item.label}</span>
      <span class="f">${item.parts.map((one: Part) => one.dim ? html`<span class="d">${one.text}</span>` : one.text)}</span>${
      act}</div>`;
  }

  /** The opened session's subagents, shown: the session's agents as a map, then a row
   * each, dearest first, as many as `Show` asked for, and what they leave out. */
  #agents(harness: string, id: string, report: Report<AgentRow>, lines: AgentLine[], shown: number) {
    const map = agentMap(report, this.#mapWidth());
    const opened = this.#at.piece === "sessions" ? this.#at.agent : null;
    const more = moreLine(lines, shown);
    return html`<div class="r" data-part="agents"><span class="k"></span><span class="f">
      ${map === null ? nothing : this.#agentMap(map, opened)}
      <div class="ses" data-list="agents">${lines.slice(0, shown).map((line) =>
        this.#agentRow(harness, id, line, line.agent === opened))}</div>
      ${more === null ? nothing : html`<div class="more" data-part="more" data-more="agents"><span>${more}</span>
        <button type="button" class="act" data-act="show" @click=${() => {
          this.agentsShown = shown + AGENTS_PAGE;
        }}>${SHOW}</button></div>`}
    </span></div>`;
  }

  /** The agents as a map: a cell each, its area its cost, named where the words fit. */
  #agentMap(map: AgentMap, opened: string | null) {
    const width = this.#mapWidth();
    return html`<svg class="ch map" data-part="agent-map" role="img" aria-label=${map.aria} width=${width}
      height=${MAP_HEIGHT} viewBox="0 0 ${width} ${MAP_HEIGHT}">${map.cells.map((cell) => svg`<g data-agent=${cell.agent}>
        <title>${cell.title}</title>
        <rect class=${cell.agent === opened ? "cell on" : "cell"} x=${cell.x} y=${cell.y} width=${cell.width}
          height=${cell.height}></rect>
        ${cell.words.map((text, i) => svg`<text class=${i === 0 ? "v" : ""} x=${cell.x + 4} y=${cell.y + 13 + i * 14}>${text}</text>`)}
      </g>`)}</svg>`;
  }

  /** One subagent as a two-line row; opened, its requests under it as a session's are. */
  #agentRow(harness: string, id: string, line: AgentLine, open: boolean) {
    const target = open ? sessionAddress(harness, id) : sessionAddress(harness, id, line.agent);
    const go = (): void => this.open(target);
    return html`<moxi-ui-row toggles ?open=${open} data-agent=${line.agent} name=${line.agent} where=${line.kind}
        fact=${line.cost} lead=${line.models} lead-more=${line.first ?? ""} rest=${line.figures}
        tail=${line.share ?? ""} @moxi-row-toggle=${go}>
      <a slot="acts" class="do" data-action="open" href=${addressPath(target)} aria-expanded=${String(open)}
        @click=${(event: MouseEvent) => {
          if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          event.preventDefault();
          go();
        }}>${open ? CLOSE : OPEN}</a>
      ${open ? this.#agentBody() : nothing}
    </moxi-ui-row>`;
  }

  /** The opened subagent's requests: its requests line, its context, its chart and the
   * notes under it, and `Table`. */
  #agentBody() {
    const read = this.#agentRead;
    if (read === null) return nothing;
    const today = this.#today();
    const model = openedAgent(read, today);
    const requests = read.value;
    const chart = requests === null ? null : sessionChart(requests, heldState().measure, this.#detWidth(true), today);
    const table = this.agentPart === "table";
    return html`<div class="det" data-part="agent">
      ${model.items.map((item) => this.#detItem(item,
        chart === null ? nothing : html`<div class="cw">${this.#sessionChart(chart)}</div>`, model.notes))}
      ${model.table === null ? nothing : html`<div class="foot" data-part="agent-acts">
        <button type="button" class="act d" data-act="table" aria-expanded=${String(table)}
          @click=${() => { this.agentPart = table ? null : "table"; }}>${SECTION_TABLE}</button></div>
        ${table ? html`<div class="under" data-part="agent-table">${this.#table(model.table)}</div>` : nothing}`}
    </div>`;
  }

  /** The opened session's chart: a bar per request above, the context it sent below. */
  #sessionChart(chart: SessionChart) {
    const S = SESSION_CHART;
    const after = chart.end + 6;
    return html`<svg class="ch ses-ch" data-part="session-chart" role="img" aria-label=${chart.aria} width=${chart.width}
      height=${S.height} viewBox="0 0 ${chart.width} ${S.height}">${svg`
      <text x="0" y=${S.barTop + 5} data-part="top">${chart.top}</text>
      <line class="gr" x1=${S.left} x2=${chart.end} y1=${S.barTop - 0.5} y2=${S.barTop - 0.5}></line>
      ${chart.turns.map((x) => svg`<line class="gr" data-mark="turn" x1=${x} x2=${x} y1=${S.barTop} y2=${S.barBase}></line>`)}
      ${chart.bars.map((bar) => svg`<rect class=${bar.dearest ? "bar now" : "bar"} data-n=${bar.n} x=${bar.x} y=${bar.y}
        width=${bar.width} height=${bar.height}></rect>`)}
      <line class="ax" x1=${S.left} x2=${chart.end} y1=${S.barBase + 0.5} y2=${S.barBase + 0.5}></line>
      <text x=${after} y=${S.barBase}>a request</text>
      ${chart.window === null ? nothing : svg`<path class="dash" data-part="window" d=${chart.window.d}></path>
        <text x="0" y=${chart.window.y + 3.5}>${chart.window.words}</text><text x=${after} y=${chart.window.y + 3.5}>window</text>`}
      <path class="ln" data-part="context" d=${chart.line}></path>
      ${chart.dot === null ? nothing : svg`<circle class="dot" cx=${chart.dot.x} cy=${chart.dot.y} r="3.5"></circle>
        <text class="v" data-part="last-context" x=${after} y=${chart.dot.y + 4}>${chart.dot.words}</text>`}
      <line class="ax" x1=${S.left} x2=${chart.end} y1=${S.contextBase + 0.5} y2=${S.contextBase + 0.5}></line>
      ${chart.compactions.map((mark) => svg`<g data-mark="compaction"><title>${mark.title}</title>
        <line class="dash" x1=${mark.x} x2=${mark.x} y1=${S.barTop} y2=${S.contextBase}></line>
        <text x=${mark.labelX} y=${S.contextTop - 8} text-anchor=${mark.anchor}>${mark.label}</text></g>`)}
      ${chart.labels.map((label) => svg`<text x=${label.x} y=${S.labels} text-anchor=${label.anchor}>${label.text}</text>`)}
      ${chart.hits.map((hit) => svg`<rect class="hit" data-n=${hit.n} x=${hit.x} y="0" width=${hit.width}
        height=${S.contextBase}><title>${hit.title}</title></rect>`)}`}</svg>`;
  }

  /** A section's name and its closed row: the answer, and the count that opens it, which
   * reads `Close` while it is open. */
  #head(key: "kinds" | "prices", label: string, closed: unknown, count: string, open: boolean) {
    const target = open ? NOTHING_OPEN : { piece: key } as Address;
    return html`<div class="gk">${label}</div>
      <div class="one"><span data-part="closed">${closed}</span><a class="t" data-part="count"
        href=${addressPath(target)} aria-expanded=${String(open)}
        @click=${(event: MouseEvent) => this.#follow(event, target)}>${open ? CLOSE : count}</a></div>`;
  }

  /** An opened section whose read is still out. */
  #sectionReading(key: "kinds" | "prices", label: string, feed: Feed<unknown> | null) {
    if (this.#at.piece !== key || feed === null || feed.value !== null || !feed.loading) return nothing;
    return html`<section class="grp is-open" data-group=${key}>
      <div class="gk">${label}</div>
      <div class="gl"><p class="none" data-part="reading">${READING}</p></div>
    </section>`;
  }

  /** Token kinds: the input read from cache and the dearest classes; opened, the four
   * classes with their tokens and cost, then what caching saved. */
  #kinds(model: Kinds | null) {
    if (model === null) return this.#sectionReading("kinds", KINDS_LABEL, this.#terms);
    const open = this.#at.piece === "kinds";
    const closed = html`${model.lead ?? nothing}${model.lead !== null && model.dearest !== null ? " " : nothing}${
      model.dearest === null ? nothing : html`<span class="d">${model.lead === null ? "" : "· "}${model.dearest}</span>`}`;
    return html`<section class=${open ? "grp is-open" : "grp"} data-group="kinds">
      ${this.#head("kinds", model.label, closed, model.count, open)}
      ${open ? html`<div class="rows" data-part="rows">${model.rows.map((row) => html`<div class="tr2" data-row=${row.key}>
          <span class="nm">${row.name}${row.note === null ? nothing : html` <span class="d">· ${row.note}</span>`}</span>
          <span class="n d">${row.tokens}</span><span class="n" title=${row.why ?? nothing}>${row.cost}</span></div>`)}
        ${model.saving === null ? nothing : html`<p data-part="saving">${model.saving}</p>`}
      </div>` : nothing}
    </section>`;
  }

  /** Prices: how many of the range's models are priced and the table's date; opened, their
   * prices, the paragraph, and the three acts, each opening in place under the table. */
  #pricesGroup(model: PricesModel | null) {
    // The closed row waits for the table's date as well, unless that read has failed.
    const prices = this.#prices;
    if (model === null || (prices !== null && prices.value === null && prices.loading)) {
      return this.#sectionReading("prices", PRICES_LABEL, model === null ? this.#terms : prices);
    }
    const open = this.#at.piece === "prices";
    const closed = html`${model.closed} <span class="d">· ${model.note}</span>`;
    const part = this.pricesPart;
    const act = (key: PricesPart, label: string) => html`<button type="button" class="act" data-act=${key}
      aria-expanded=${String(part === key)} @click=${() => this.#togglePart(key)}>${label}</button>`;
    return html`<section class=${open ? "grp is-open" : "grp"} data-group="prices">
      ${this.#head("prices", model.label, closed, model.count, open)}
      ${open ? html`<div class="rows" data-part="rows">
        ${model.rows === null ? nothing : html`<div class="pt" role="table" aria-label="Prices per million tokens" data-part="price-table">
          <div class="tr2 p5 th" role="row">${PRICE_HEADS.map((head, i) =>
            html`<span role="columnheader" class=${i ? "n" : ""}>${head}</span>`)}</div>
          ${model.rows.map((row) => html`<div class="tr2 p5" role="row" data-price=${row.key}>
            <span class="nm" role="cell">${row.name}</span>${row.cells.map((cell) => html`<span class="n" role="cell">${cell}</span>`)}</div>`)}
        </div>`}
        ${model.unpriced === null ? nothing : html`<p data-part="unpriced">${model.unpriced}</p>`}
        <p data-part="paragraph">${model.paragraph}</p>
        <p class="acts">${act("find", FIND)}${act("bill", BILL)}${act("details", DETAILS)}</p>
        ${part === "find" ? this.#find() : part === "bill" ? this.#bill() : part === "details" ? this.#details() : nothing}
      </div>` : nothing}
    </section>`;
  }

  #togglePart(key: PricesPart): void {
    const next = this.pricesPart === key ? null : key;
    this.#closePart();
    this.pricesPart = next;
  }

  /** Closes what is open under the price table, and its search. */
  #closePart(): void {
    clearTimeout(this.#searchTimer);
    this.#search?.close();
    this.#search = null;
    this.#searched = "";
    this.searchText = "";
    this.pricesPart = null;
  }

  /** `Find a model`: a search of the whole model table, its matches as the details' table. */
  #find() {
    const type = (event: Event) => {
      const text = (event.target as HTMLInputElement).value.trim();
      this.searchText = text;
      clearTimeout(this.#searchTimer);
      this.#searchTimer = setTimeout(() => {
        if (text === this.#searched) return;
        this.#searched = text;
        this.#search?.close();
        this.#search = null;
        if (text.length >= SEARCH_MIN) {
          const path = searchPath(text);
          this.#search = new Feed<Prices>(() => api("usage", path), parsePrices, () => this.requestUpdate());
          void this.#search.refresh();
        }
        this.requestUpdate();
      }, SEARCH_MS);
    };
    const feed = this.#search;
    const found = feed?.value ?? null;
    return html`<div class="under" data-part="find">
      <label class="find">${FIND} <input type="search" aria-label=${FIND} .value=${this.searchText} @input=${type}></label>
      ${feed === null ? nothing
        : found === null ? html`<p class="d">${feed.loading ? READING : `The model table can't be read: ${feed.refused ?? feed.failure?.why}.`}</p>`
        : found.models.length === 0 ? html`<p class="d" data-part="no-match">No model matches.</p>`
        : html`${this.#rateTable(found)}${found.matched > found.models.length
          ? html`<p class="d" data-part="more">${(found.matched - found.models.length).toLocaleString("en-US")} more</p>` : nothing}`}
    </div>`;
  }

  /** `Check against a bill`: a line per price group with each class's dollars, a subtotal
   * under a model of two or more, and the total. */
  #bill() {
    const report = this.#terms?.value ?? null;
    return report === null ? nothing : this.#billOf(report);
  }

  #billOf(report: Report<TermsRow, TermsTotal>) {
    const lines = reconcileLines(report);
    const flagged = lines.some((line) => line.flags.length > 0);
    return html`<div class="under" data-part="bill">
      <div class="scroll"><table class="tbl bill" data-part="bill-lines">
        <thead><tr><th>Model</th><th>Priced by</th>${TERM_CLASSES.map(([, label]) => html`<th class="n">${label}</th>`)}<th class="n">Cost</th>${
          flagged ? html`<th>Not whole</th>` : nothing}</tr></thead>
        <tbody>${lines.map((line) => html`<tr data-line=${line.kind} data-model=${line.model}>
          <td>${line.model}</td><td class="wrap">${line.pricedBy}</td>
          ${line.cells.map((cell) => html`<td class="n" data-class=${cell.cls} title=${cell.title}>${cell.text}</td>`)}
          <td class="n" data-cost=${line.cost === null ? "" : String(line.cost)}>${line.cost === null ? "no price" : fmtCost(line.cost)}</td>
          ${flagged ? html`<td class="wrap">${line.flags.join("; ")}</td>` : nothing}
        </tr>`)}</tbody>
      </table></div>
      <p>The terms on each line add up to its cost. Point at a figure for its tokens times its price.</p>
    </div>`;
  }

  /** `Details`: each listed model's prices with where they came from, its window, tiers
   * and period; your `[[model]]` rows, the table's layers and the rows it skipped. */
  #details() {
    const prices = this.#prices?.value ?? null;
    const report = this.#terms?.value ?? null;
    if (prices === null || report === null) return nothing;
    const left = pricesLeft(report);
    return html`<div class="under" data-part="details">
      ${prices.models.length ? this.#rateTable(prices) : nothing}
      ${left ? html`<p>${left.toLocaleString("en-US")} more models of the range aren't listed; find one by name.</p>` : nothing}
      <p class="sub">Your [[model]] rows</p>
      ${prices.overrides.length ? html`<ul data-part="overrides">${prices.overrides.map((row) => html`<li>${overrideLine(row)}</li>`)}</ul>`
        : html`<p>None: every price comes from the model table.</p>`}
      <p data-part="layers">${layerLine(prices)}${prices.layers.length > 1
        ? `; layers ${prices.layers.map((layer) => `${layer.layer} ${layer.rows.toLocaleString("en-US")} rows${layer.date ? ` of ${layer.date}` : ""}`).join(", ")}`
        : ""}</p>
      ${prices.problems.map((problem) => html`<p data-part="skipped">skipped: ${problem}</p>`)}
    </div>`;
  }

  /** The models' rates in full: each price, marked where a provider default or your
   * config gave it, the window, tiers, period and source. */
  #rateTable(prices: Prices) {
    return html`<div class="scroll"><table class="tbl bill" data-part="rate-lines">
      <thead><tr><th>Model</th>${RATE_FIELDS.map(([, label]) => html`<th class="n">${label}</th>`)}
        <th class="n">Window</th><th>Tiers</th><th>Period</th><th>Where from</th></tr></thead>
      <tbody>${prices.models.map(rateLine).map((line) => html`
        <tr data-rate=${line.model} data-provider=${line.provider ?? ""}>
          <td>${line.model}${line.provider ? html`<div class="d">${line.provider}</div>` : nothing}
            ${line.known ? nothing : html`<div class="d">not in the model table</div>`}</td>
          ${line.cells.map((cell) => html`<td class="n">${cell.text}${cell.mark ? html`<div class="d">${cell.mark}</div>` : nothing}</td>`)}
          <td class="n">${line.window}</td>
          <td class="wrap">${line.tiers.map((tier) => html`<div>${tier}</div>`)}</td>
          <td>${line.period}</td>
          <td class="wrap">${line.from}</td>
        </tr>`)}</tbody>
    </table></div>`;
  }

  #part(part: SentencePart) {
    const patch = part.patch;
    return patch === null ? part.text : html`<a href=${addressPath(SESSIONS)} data-folder=${patch.folder ?? ""}
      @click=${(event: MouseEvent) => this.#filterTo(event, patch)}>${part.text}</a>`;
  }

  /** A row or folder of a section: sets its filter and opens Sessions. */
  #filterTo(event: MouseEvent, patch: Partial<PageState>): void {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    holdState(patch);
    this.open(SESSIONS);
  }

  #nothing(block: Nothing) {
    return html`<div class="nothing" data-part="nothing" data-state=${block.state}>
      <p class="quietline">${block.title}</p>
      <div class="first">
        <p>${block.text}</p>
        ${block.act === null ? nothing : html`<p><button type="button" class="act x"
          @click=${() => { void this.#askPass(); }}>${block.act}</button></p>`}
        ${block.running === null ? nothing : html`<p data-part="running">${block.running}</p>`}
        ${block.command === null ? nothing : html`<p><code>${block.command}</code><button type="button" class="act x"
          @click=${() => { void this.#copy(block.command as string); }}>${this.copied ? COPIED : COPY}</button></p>`}
      </div>
    </div>`;
  }

  #foot(foot: Foot) {
    return html`<div class="note" data-part="foot" data-state=${foot.running ? "running" : "up"}>
      <span class="last" data-last-pass=${foot.lastPass === null ? "" : String(foot.lastPass)}>${foot.line ?? nothing}</span>
      ${foot.act === null ? nothing : html`<button type="button" class="act"
        @click=${() => { void this.#askPass(); }}>${foot.act}</button>`}
    </div>`;
  }

  #lines(lines: FootLine[]) {
    return html`<div class="lines" data-part="lines">${lines.map((line) =>
      html`<p data-line=${line.key}>${line.text}</p>`)}</div>`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, UsagePage);
