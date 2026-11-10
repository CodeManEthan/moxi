/*! Bundled with d3-array, d3-color, d3-format, d3-hierarchy, d3-interpolate and d3-scale,
 * each under the ISC licence:
 *
 * d3-array 3.2.4: Copyright 2010-2023 Mike Bostock
 * d3-color 3.1.0: Copyright 2010-2022 Mike Bostock
 * d3-format 3.1.2: Copyright 2010-2026 Mike Bostock
 * d3-hierarchy 3.1.2: Copyright 2010-2021 Mike Bostock
 * d3-interpolate 3.0.1: Copyright 2010-2021 Mike Bostock
 * d3-scale 4.0.2: Copyright 2010-2021 Mike Bostock
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
//   moxi-usage-legacy           the views the page had before, each serving its section's
//                               address until the section has its place under the filter line
//   div[data-part=foot]         when the sessions were last read, the files, the act
//   div[data-part=lines]        dim lines: what the figures leave out, reads that failed
//
// The view places what its models return and words nothing itself: a piece whose fact
// no read carries is not in the model and is not drawn. The range and the filters are
// one state (`filter-model.ts`), held for the document's life; every read under the
// filter line is asked with it, and today's views are handed it.
//
// One piece is open at a time, a limit row or a section, and the address names it
// (`address-model.ts`). The page is placed with the route the address gave. When a piece
// opens or closes the page rewrites the address of the history entry it is on and adds no
// entry, so Back leaves the page, and a reload or a new placement opens the same piece.

import { css, html, LitElement, nothing, svg } from "lit";
import { api, installed, subscribe } from "/m/ui/client.js";

import {
  addressPath, addressWrite, limitAddress, NOTHING_OPEN, parseAddress, sameAddress, settled,
} from "./address-model.ts";
import type { Address } from "./address-model.ts";
import { chartDraw, chartGroup } from "./chart-model.ts";
import type { ChartDraw, ChartGroup, Table } from "./chart-model.ts";
import { rangeDays, sumRows } from "./cost-model.ts";
import type { Sum } from "./cost-model.ts";
import { LOCAL } from "./days.ts";
import { Feed, parseIndexEvent, parseLimits, parseReport, parseStatus, parseWindow, passOutcome } from "./feed.ts";
import type { DayRow, IndexEvent, Limits, ModelRow, OpenedWindow, ProjectRow, Report, Status } from "./feed.ts";
import {
  allTimePath, chartPath, customRange, figuresPath, filterLine, heldState, holdState, modelsPath,
  placesPath, watchState,
} from "./filter-model.ts";
import type { FilterLine, Menu, MenuItem, PageState } from "./filter-model.ts";
import { COPIED, COPY, costPath, frameModel, hasBelow, newestDay, passLine, shouldPass } from "./frame-model.ts";
import type { Cost, Foot, FootLine, Nothing, OtherRead } from "./frame-model.ts";
import "./legacy.ts";
import type { LimitGroup, LimitRow, Warning } from "./limits-model.ts";
import { NO_FILTERS } from "./views.ts";
import type { Filters } from "./views.ts";
import { openedLimit, openedRow, readingKey, windowPath } from "./window-model.ts";
import type { OpenedLimit } from "./window-model.ts";

const TAG = "moxi-usage-page";
const TICK_MS = 15_000;
/** The thin line beside a percent, in px: its length and its height. */
const METER = { width: 96, height: 9 };

// When this document last asked for a pass as a page opened, in epoch seconds.
let askedAt = 0;

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
  .det .r { display: grid; grid-template-columns: 76px 1fr; padding: 3px 0; align-items: baseline; }
  .det .k, .det .d { color: var(--moxi-text-dim); }
  .det .f { min-width: 0; }
  .det p { padding: 3px 0; }
  .det .ul { display: flex; justify-content: space-between; gap: 18px; padding: 0 0 3px;
             font-variant-numeric: tabular-nums; }
  .det .ul .n { color: var(--moxi-text); }
  .m { display: block; align-self: center; width: 96px; height: 9px; }
  .m .base { fill: var(--moxi-border-strong); }
  .m .fill { fill: var(--moxi-text); }
  .m .tick { fill: var(--moxi-text-dim); }
  .one { display: flex; justify-content: space-between; align-items: baseline; padding: 7px 0;
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
  .scroll { max-width: 100%; overflow-x: auto; padding: 2px 0 6px; }
  .tbl { width: 100%; border-collapse: collapse; font-size: var(--moxi-font-size-sm); line-height: 18px;
         white-space: nowrap; font-variant-numeric: tabular-nums; }
  .tbl th, .tbl td { padding: 5px 0 5px 16px; text-align: left; font-weight: 400; }
  .tbl th:first-child, .tbl td:first-child { padding-left: 0; }
  .tbl th { color: var(--moxi-text-dim); }
  .tbl .n { text-align: right; }
  .quietline { padding: 150px 0 14px; font-size: 22px; line-height: 32px; text-align: center;
               letter-spacing: -0.005em; }
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
  /** What is open: the piece the address names. */
  #at: Address = NOTHING_OPEN;
  /** The opened limit row's read, with the window it reads and the reading it was last
   * asked for. */
  #window: Feed<OpenedWindow> | null = null;
  #windowPath: string | null = null;
  #windowKey: string | null = null;
  /** The read of a window that was refused and so closed its row, kept for its line at
   * the foot until another piece opens. */
  #refused: Feed<OpenedWindow> | null = null;
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
    for (const feed of [this.#limits, this.#status, this.#daily]) feed?.close();
    this.#limits = this.#status = this.#daily = null;
    this.#closeWindow();
    this.#refused = null;
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
    this.#refused = null;
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
      this.#refused = this.#window;
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

  #closeBelow(): void {
    for (const feed of [this.#chart, this.#figures, this.#allTime, this.#places, this.#models]) feed?.close();
    this.#chart = this.#figures = this.#allTime = this.#places = this.#models = null;
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
    }
    this.#followWindow();
    const status = this.#status;
    if (status === null || !hasBelow(status)) return;
    const update = () => this.requestUpdate();
    const state = heldState();
    const today = this.#today();
    const paths = { chart: chartPath(state, today), figures: figuresPath(state, today),
                    places: placesPath(state, today, this.#folders()), models: modelsPath(state, today) };
    const asked = JSON.stringify(paths);
    if (asked !== this.#asked) {
      this.#asked = asked;
      for (const feed of [this.#chart, this.#figures, this.#places, this.#models]) feed?.close();
      this.#chart = new Feed<Report<DayRow>>(() => api("usage", paths.chart), parseReport, update);
      const figures = paths.figures;
      this.#figures = figures === null ? null : new Feed<Report<DayRow>>(() => api("usage", figures), parseReport, update);
      this.#places = new Feed<Report<ProjectRow>>(() => api("usage", paths.places), parseReport, update);
      this.#models = new Feed<Report<ModelRow>>(() => api("usage", paths.models), parseReport, update);
      for (const feed of [this.#chart, this.#figures, this.#places, this.#models]) void feed?.refresh();
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

  #indexEvent(data: unknown): void {
    const event = parseIndexEvent(data);
    if (event === null) return;
    this.#event = event;
    this.#events += 1;
    if (!event.running) {
      void this.#status?.refresh();
      void this.#daily?.refresh();
      for (const feed of [this.#chart, this.#figures, this.#allTime, this.#places, this.#models, this.#window]) {
        void feed?.refresh();
      }
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
    const windowRead = this.#window ?? this.#refused;
    if (windowRead !== null) others.push({ what: "The opened limit", read: windowRead, report: false });
    const at = this.#at;
    const openedSource = at.piece === "limit" ? openedRow(limits.value?.rows ?? [], at) : null;
    const opened = at.piece !== "limit" || openedSource === null ? null : {
      key: `${at.harness}/${at.limit}/${at.minutes}`,
      model: openedLimit({ window: this.#window?.value ?? null, loading: this.#window?.loading ?? true,
                           row: openedSource, now: limits.now() }),
    };
    const frame = frameModel({
      limits, status, daily, event: this.#event, pass: this.#pass, most: null, range, others,
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
    const days = rangeDays(state.range, today);
    // A session's address names its harness, and its view is read under it.
    const harness = at.piece === "sessions" && at.harness !== null ? at.harness : state.harness;
    const filters: Filters = { ...NO_FILTERS, harness, model: state.model, project: state.project,
                               folder: state.folder, days: days ?? {} };
    return html`
      ${frame.reading === null ? nothing : html`<p class="reading dim" data-part="reading">${frame.reading}</p>`}
      ${frame.limits.warning === null ? nothing : this.#warning(frame.limits.warning)}
      ${groups.length || frame.cost ? html`<div class="grps" data-part="top">
        ${groups.map((group) => this.#group(group, opened))}
        ${frame.cost === null ? nothing : this.#cost(frame.cost)}
      </div>` : nothing}
      ${frame.nothing === null ? nothing : this.#nothing(frame.nothing)}
      ${line === null ? nothing : this.#filters(line)}
      ${group === null ? nothing : html`<div class="grps below" data-part="below">${this.#chartGroup(group)}</div>`}
      ${frame.below ? html`<moxi-usage-legacy .address=${at} .open=${(address: Address) => this.open(address)} .filters=${filters}
        .said=${group !== null && group.nothing !== null}></moxi-usage-legacy>` : nothing}
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
