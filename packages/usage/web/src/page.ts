/*! Bundled with d3-array, d3-color, d3-format, d3-hierarchy, d3-interpolate, d3-scale and
 * internmap, each under the ISC licence:
 *
 * d3-array 3.2.4: Copyright 2010-2023 Mike Bostock
 * d3-color 3.1.0: Copyright 2010-2022 Mike Bostock
 * d3-format 3.1.2: Copyright 2010-2026 Mike Bostock
 * d3-hierarchy 3.1.2: Copyright 2010-2021 Mike Bostock
 * d3-interpolate 3.0.1: Copyright 2010-2021 Mike Bostock
 * d3-scale 4.0.2: Copyright 2010-2021 Mike Bostock
 * internmap 2.0.3: Copyright 2021 Mike Bostock
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

// <moxi-usage-page route="...">: usage's panel. Above its views sit the limits meters and
// the index strip (the last pass, any lane not online, held files, unpriced models, the
// index's note, and "Read new sessions"); then the filters (a range preset worked out in
// the browser, harness, model and project); then the view the route names: `""` or
// `daily`, `sessions`, `projects`, `models`, or one session's agents, `session/<id>`.
// A row whose figures aren't whole carries a flag saying why.

import { css, html, LitElement, nothing, svg } from "lit";
import type { TemplateResult } from "lit";
import { api, navigate, subscribe } from "/m/ui/client.js";

import { Feed, parseIndexEvent, parseLimits, parseReport, parseStatus, passOutcome } from "./feed.ts";
import type {
  AgentRow, DayRow, IndexEvent, Limits, ModelRow, ProjectRow, Report, SessionRow, Status,
} from "./feed.ts";
import { BASE, flag, meterList, SERIES } from "./parts.ts";
import {
  agents, agentTreemap, CLASSES, cellLabel, classTotals, costText, dailyChart, filterKeys, flags,
  fmtMoment, fmtTokens, HARNESSES, heldLine, labelEvery, localDay, MARGIN, menuPaths, meters,
  NO_FILTERS, parseRoute, PRESETS, provenanceLine, reportState, segmentTitle, seriesClass, sessionRoute,
  shouldPass, strip, tickText, UNKNOWN_MODEL, viewPath, VIEWS,
} from "./views.ts";
import type { Filters, Measure, PageRoute, Preset } from "./views.ts";

const TAG = "moxi-usage-page";
const TICK_MS = 15_000;
const CHART_HEIGHT = 240;
const TREEMAP_HEIGHT = 300;

// The filters and the daily view's choices outlive one placement: the shell places a new
// page for each route, and the user's filters carry over.
let filters: Filters = { ...NO_FILTERS };
let by: "day" | "month" = "day";
let measure: Measure = "tokens";
// When this document last asked for a pass as a page opened, in epoch seconds.
let askedAt = 0;

const STYLES = [BASE, SERIES, css`
  h1 { margin: 0 0 var(--moxi-gap); font-size: 1.4em; }
  h2 { margin: calc(var(--moxi-gap) * 2) 0 var(--moxi-gap); font-size: 1.1em; }
  section { margin-bottom: calc(var(--moxi-gap) * 2); }
  .strip { display: flex; flex-wrap: wrap; gap: 4px var(--moxi-gap); align-items: center; padding: 8px 10px;
           border: 1px solid var(--moxi-border); border-radius: var(--moxi-radius); background: var(--moxi-bg-raised); }
  .strip ul { flex-basis: 100%; margin: 0; padding: 0 0 0 1.2em; }
  .strip .grow { flex: 1 1 8em; }
  button { font: inherit; padding: 4px 10px; border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius);
           background: var(--moxi-bg); color: var(--moxi-text); cursor: pointer; }
  button:hover:not(:disabled) { background: var(--moxi-bg-hover); }
  button:disabled { color: var(--moxi-text-faint); cursor: default; }
  button.primary { border-color: var(--moxi-accent); color: var(--moxi-accent); }
  button[aria-pressed="true"] { background: var(--moxi-accent-soft); border-color: var(--moxi-accent); }
  nav, .filters, .controls { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: var(--moxi-gap); }
  nav a { padding: 4px 10px; border-radius: var(--moxi-radius); color: var(--moxi-text); text-decoration: none; }
  nav a[aria-current="page"] { background: var(--moxi-accent-soft); color: var(--moxi-accent); font-weight: 600; }
  .seg { display: inline-flex; flex-wrap: wrap; gap: 2px; }
  select { font: inherit; max-width: 100%; padding: 3px 6px; border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius);
           background: var(--moxi-bg); color: var(--moxi-text); }
  label { display: inline-flex; gap: 4px; align-items: center; min-width: 0; max-width: 100%; }
  .scroll { overflow-x: auto; max-width: 100%; }
  table { width: 100%; border-collapse: collapse; font-size: var(--moxi-font-size-sm); }
  th, td { padding: 4px 6px; border-bottom: 1px solid var(--moxi-border); text-align: left; vertical-align: top; }
  th { color: var(--moxi-text-dim); font-weight: normal; white-space: nowrap; }
  td.wrap { overflow-wrap: anywhere; min-width: 8em; }
  @media (max-width: 640px) { .wide { display: none; } }
  a.link { color: var(--moxi-accent); }
  svg { display: block; width: 100%; height: auto; margin-bottom: var(--moxi-gap); }
  svg text { fill: var(--moxi-text-dim); font-size: 11px; font-family: var(--moxi-font-sans); }
  svg .axis { stroke: var(--moxi-border); }
  svg .cell-label { fill: var(--moxi-bg); font-size: 11px; }
  svg .incomplete { stroke: var(--moxi-warn); stroke-width: 2; stroke-dasharray: 3 2; }
  .legend { display: flex; flex-wrap: wrap; gap: 4px 12px; margin: 4px 0 var(--moxi-gap); font-size: var(--moxi-font-size-sm); }
  .swatch { display: inline-block; width: 10px; height: 10px; margin-right: 4px; border-radius: 2px; }
  .totals { display: flex; flex-wrap: wrap; gap: 4px 16px; margin-bottom: var(--moxi-gap); }
  .totals b { font-variant-numeric: tabular-nums; }
`];

type AnyReport = Report<unknown>;

export class UsagePage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
    tick: { state: true },
    width: { state: true },
  };

  route = "";
  tick = 0;
  width = 640;

  #limits: Feed<Limits> | null = null;
  #status: Feed<Status> | null = null;
  #view: Feed<AnyReport> | null = null;
  #projects: Feed<Report<ProjectRow>> | null = null;
  #models: Feed<Report<ModelRow>> | null = null;
  #event: IndexEvent | null = null;
  /** How many `index` events have come. */
  #events = 0;
  #passNote: string | null = null;
  #stops: (() => void)[] = [];
  #timer: ReturnType<typeof setInterval> | undefined;
  #resize: ResizeObserver | null = null;
  #loadedFor: string | null = null;
  #shown = (): void => {
    if (document.visibilityState !== "visible") return;
    void this.#limits?.refresh();
    void this.#status?.refresh();
  };

  connectedCallback(): void {
    super.connectedCallback();
    const update = () => this.requestUpdate();
    const limits = new Feed<Limits>(() => api("usage", "api/limits"), parseLimits, update);
    const status = new Feed<Status>(() => api("usage", "api/status"), parseStatus, update);
    this.#limits = limits;
    this.#status = status;
    void limits.refresh().then(() => {
      if (this.#limits === limits) this.#stops.push(subscribe("usage", "limits", (data) => limits.topic(data)));
    });
    void status.refresh().then(() => {
      if (this.#status !== status) return;
      this.#stops.push(subscribe("usage", "index", (data) => this.#indexEvent(data)));
      const now = limits.now();
      if (shouldPass(status.value, now) && now - askedAt > 60) {
        askedAt = now;
        void this.#pass();
      }
    });
    document.addEventListener("visibilitychange", this.#shown);
    this.#timer = setInterval(() => { this.tick += 1; }, TICK_MS);
    this.#resize = new ResizeObserver((entries) => {
      const found = Math.floor(entries[0]?.contentRect.width ?? 0);
      if (found > 0 && Math.abs(found - this.width) > 8) this.width = found;
    });
    this.#resize.observe(this);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    for (const feed of [this.#limits, this.#status, this.#view, this.#projects, this.#models]) feed?.close();
    this.#limits = this.#status = this.#view = this.#projects = this.#models = null;
    this.#loadedFor = null;
    for (const stop of this.#stops) stop();
    this.#stops = [];
    document.removeEventListener("visibilitychange", this.#shown);
    clearInterval(this.#timer);
    this.#resize?.disconnect();
    this.#resize = null;
  }

  protected willUpdate(): void {
    if (this.#limits === null) return;
    const today = localDay(new Date());
    const key = `${this.route}|${by}|${JSON.stringify(filterKeys(filters, today))}`;
    if (key !== this.#loadedFor) {
      this.#loadedFor = key;
      this.#load(today);
    }
  }

  /** Opens the feeds of the current route and filters, closing the last ones. */
  #load(today: string): void {
    const update = () => this.requestUpdate();
    this.#view?.close();
    this.#projects?.close();
    this.#models?.close();
    const path = viewPath(parseRoute(this.route), filters, today, by);
    this.#view = path === null ? null : new Feed<AnyReport>(() => api("usage", path), parseReport, update);
    const menus = menuPaths(filters, today);
    this.#projects = new Feed<Report<ProjectRow>>(() => api("usage", menus.projects), parseReport, update);
    this.#models = new Feed<Report<ModelRow>>(() => api("usage", menus.models), parseReport, update);
    void this.#view?.refresh();
    void this.#projects.refresh();
    void this.#models.refresh();
  }

  #indexEvent(data: unknown): void {
    const event = parseIndexEvent(data);
    if (event === null) return;
    this.#event = event;
    this.#events += 1;
    if (!event.running) {
      void this.#status?.refresh();
      void this.#view?.refresh();
      void this.#projects?.refresh();
      void this.#models?.refresh();
    }
    this.requestUpdate();
  }

  async #pass(): Promise<void> {
    this.#passNote = null;
    const seen = this.#events;
    const outcome = passOutcome(await api("usage", "api/pass", {}));
    if (this.#status === null) return;
    if (outcome.outcome === "refused") this.#passNote = outcome.text;
    else if (outcome.outcome === "failed") this.#passNote = `the pass could not be started: ${outcome.why}`;
    else if (this.#events === seen) {
      // The topic hasn't said yet that the pass began; a short pass may have begun and
      // ended before this answer came, and then its events already told.
      this.#event = { running: true, last_pass: this.#event?.last_pass ?? null, note: null };
    }
    this.requestUpdate();
  }

  #setFilters(patch: Partial<Filters>): void {
    filters = { ...filters, ...patch };
    this.requestUpdate();
  }

  render() {
    if (this.#limits === null || this.#status === null) return nothing;
    const route = parseRoute(this.route);
    const now = this.#limits.now();
    return html`
      <h1>Usage</h1>
      <section data-part="limits">${this.#meters(now)}</section>
      <section data-part="strip">${this.#strip(now)}</section>
      ${this.#nav(route)}
      ${route.view === "unknown" ? html`<p class="dim" data-state="unknown">There is no such view here.</p>` : html`
        ${this.#filters()}
        <section data-part="view" data-view=${route.view}>${this.#body(route)}</section>`}`;
  }

  #meters(now: number) {
    const feed = this.#limits as Feed<Limits>;
    const view = meters(feed.value, now);
    if (feed.loading) return html`<p class="dim">Reading the limits…</p>`;
    if (feed.value === null) return html`<p class="dim small">The limits can't be read: ${feed.refused ?? feed.failure?.why}.</p>`;
    return html`${meterList(view.meters)}
      ${view.notes.map((note) => html`<p class="dim small">${note}</p>`)}`;
  }

  #strip(now: number) {
    const view = strip(this.#status as Feed<Status>, this.#event, now);
    const report = this.#view?.value ?? null;
    const unpriced = report?.unpriced_models ?? [];
    const last = this.#event?.last_pass ?? this.#status?.value?.total.last_pass ?? null;
    if (view.state === "loading") return html`<div class="strip" data-state="loading"><span class="dim">Reading the index…</span></div>`;
    if (view.state === "failed") return html`<div class="strip" data-state="failed"><span class="problem">The index's status can't be read: ${view.note}.</span></div>`;
    if (view.state === "none") {
      return html`<div class="strip" data-state="none"><span class="grow">There is no index yet. Build it once from a terminal:
        <code>${view.command}</code></span></div>`;
    }
    return html`<div class="strip" data-state=${view.running ? "running" : "up"}>
      <span class="grow last" data-last-pass=${last === null ? "" : String(last)}>${view.running ? "Reading new sessions…" : view.lastPass}</span>
      <button type="button" class="primary" ?disabled=${view.running} @click=${() => { void this.#pass(); }}>Read new sessions</button>
      ${view.lanes.length || view.held || unpriced.length || view.note || this.#passNote ? html`<ul>
        ${view.lanes.map((lane) => html`<li data-lane=${lane.lane}>${lane.lane}: ${lane.state}${lane.reason ? html`, ${lane.reason}` : nothing}</li>`)}
        ${view.held ? html`<li data-part="held">${heldLine(view)}</li>` : nothing}
        ${unpriced.length ? html`<li>No price for ${unpriced.join(", ")}: their tokens count, their cost doesn't</li>` : nothing}
        ${view.note ? html`<li>${view.note}${view.command ? html` <code>${view.command}</code>` : nothing}</li>` : nothing}
        ${this.#passNote ? html`<li class="problem" data-state="refused">${this.#passNote}</li>` : nothing}
      </ul>` : nothing}
    </div>`;
  }

  #link(route: string, label: string | TemplateResult, current = false) {
    return html`<a href=${`/m/usage/${route}`} class=${current ? "" : "link"} aria-current=${current ? "page" : "false"}
      @click=${(event: MouseEvent) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        navigate("usage", route);
      }}>${label}</a>`;
  }

  #nav(route: PageRoute) {
    const at = route.view === "session" ? "sessions" : route.view;
    return html`<nav aria-label="Views">${VIEWS.map(([name, label]) =>
      this.#link(name === "daily" ? "" : name, label, at === name))}</nav>`;
  }

  #filters() {
    const projects = (this.#projects?.value?.rows ?? [])
      .map((row) => ({ value: row.project ?? row.cwd ?? "", label: row.project ?? row.cwd ?? "" }))
      .filter((item) => item.value !== "");
    const models = (this.#models?.value?.rows ?? []).map((row) => row.model).filter((m): m is string => !!m);
    if (filters.project && !projects.some((item) => item.value === filters.project)) {
      projects.unshift({ value: filters.project, label: filters.project });
    }
    if (filters.model && !models.includes(filters.model)) models.unshift(filters.model);
    const pick = (event: Event) => (event.target as HTMLSelectElement).value;
    return html`<div class="filters" data-part="filters">
      <span class="seg" role="group" aria-label="Range">${PRESETS.map(([preset, label]) => html`
        <button type="button" aria-pressed=${String(filters.preset === preset)}
          @click=${() => this.#setFilters({ preset: preset as Preset })}>${label}</button>`)}</span>
      <label>Harness <select aria-label="Harness" @change=${(e: Event) => this.#setFilters({ harness: pick(e) })}>
        <option value="" ?selected=${filters.harness === ""}>all</option>
        ${HARNESSES.map((name) => html`<option value=${name} ?selected=${filters.harness === name}>${name}</option>`)}
      </select></label>
      <label>Model <select aria-label="Model" @change=${(e: Event) => this.#setFilters({ model: pick(e) })}>
        <option value="" ?selected=${filters.model === ""}>all</option>
        ${models.map((name) => html`<option value=${name} ?selected=${filters.model === name}>${name}</option>`)}
      </select></label>
      <label>Project <select aria-label="Project" @change=${(e: Event) => this.#setFilters({ project: pick(e) })}>
        <option value="" ?selected=${filters.project === ""}>all</option>
        ${projects.map((item) => html`<option value=${item.value} ?selected=${filters.project === item.value}>${item.label}</option>`)}
      </select></label>
    </div>`;
  }

  #body(route: PageRoute) {
    const feed = this.#view;
    if (feed === null) return nothing;
    const { state, detail } = reportState(feed);
    const stale = state === "up" || state === "empty" ? nothing
      : html`<p class=${state === "loading" ? "dim" : "problem"} data-state=${state}>${
        state === "loading" ? "Reading…" : state === "refused" ? detail : `This view can't be read: ${detail}.`}</p>`;
    if (feed.value === null) return html`${stale}`;
    if (state === "empty") {
      return html`${this.#heading(route)}<p class="dim" data-state="empty">${detail && /moxi usage index/.test(detail)
        ? html`${detail}` : "Nothing was used in this range."}</p>`;
    }
    const report = feed.value;
    return html`${stale}${this.#heading(route)}${this.#totals(report, route)}${this.#view_(route, report)}`;
  }

  #heading(route: PageRoute) {
    if (route.view !== "session") return nothing;
    return html`<p>${this.#link("sessions", "All sessions")} › <code>${route.session}</code></p>`;
  }

  #totals(report: AnyReport, route: PageRoute) {
    const total = report.total;
    return html`<div class="totals" data-part="totals">
      <span>Cost <b>${costText(total)}</b>${flag(flags({ ...total, unpriced_models: report.unpriced_models }))}</span>
      <span>Tokens <b>${fmtTokens(total.tokens)}</b></span>
      <span>Requests <b>${total.requests.toLocaleString("en-US")}</b></span>
    </div>
    ${route.view === "daily" ? html`<div class="totals small" data-part="classes">${classTotals(total).map((item) =>
      html`<span data-class=${item.key}>${item.label} <b>${fmtTokens(item.value)}</b></span>`)}</div>` : nothing}`;
  }

  #view_(route: PageRoute, report: AnyReport) {
    switch (route.view) {
      case "daily": return this.#daily(report as Report<DayRow>);
      case "sessions": return this.#sessions(report as Report<SessionRow>);
      case "session": return this.#session(report as Report<AgentRow>);
      case "projects": return this.#projectsView(report as Report<ProjectRow>);
      case "models": return this.#modelsView(report as Report<ModelRow>);
      default: return nothing;
    }
  }

  #daily(report: Report<DayRow>) {
    const width = Math.max(280, this.width);
    const chart = dailyChart(report.rows, measure, width, CHART_HEIGHT);
    const every = labelEvery(chart.bars.length, width);
    const names = new Map<string, string>(CLASSES.map(([key, label]) => [key, label]));
    const set = (patch: () => void) => { patch(); this.requestUpdate(); };
    return html`
      <div class="controls">
        <span class="seg" role="group" aria-label="By">
          <button type="button" aria-pressed=${String(by === "day")} @click=${() => set(() => { by = "day"; })}>By day</button>
          <button type="button" aria-pressed=${String(by === "month")} @click=${() => set(() => { by = "month"; })}>By month</button>
        </span>
        <span class="seg" role="group" aria-label="Measure">
          <button type="button" aria-pressed=${String(measure === "tokens")} @click=${() => set(() => { measure = "tokens"; })}>Tokens by class</button>
          <button type="button" aria-pressed=${String(measure === "cost")} @click=${() => set(() => { measure = "cost"; })}>Cost by model</button>
        </span>
      </div>
      <div class="legend">${chart.keys.map((key, i) => html`
        <span><span class="swatch ${seriesClass(i)}"></span>${names.get(key) ?? key}</span>`)}</div>
      <svg viewBox="0 0 ${width} ${CHART_HEIGHT}" role="img" aria-label=${measure === "tokens" ? "Tokens by class per period" : "Cost by model per period"}>
        ${chart.ticks.map((tick) => svg`
          <line class="axis" x1=${MARGIN.left} x2=${width - MARGIN.right} y1=${tick.y} y2=${tick.y}></line>
          <text x=${MARGIN.left - 4} y=${tick.y + 4} text-anchor="end">${tickText(tick.value, measure)}</text>`)}
        ${chart.bars.map((bar, i) => svg`
          <g data-period=${bar.period}>
            <title>${bar.period}: ${measure === "tokens" ? `${fmtTokens(bar.total)} tokens` : tickText(bar.total, "cost")}${bar.incomplete ? " (not whole)" : ""}</title>
            ${bar.segments.map((seg) => svg`<rect class=${seriesClass(seg.index)} data-key=${seg.key} x=${bar.x} y=${seg.y} width=${bar.width} height=${Math.max(0, seg.height)}>
              <title>${segmentTitle(bar.period, seg.key, seg.value, measure)}</title></rect>`)}
            ${bar.incomplete && bar.segments.length ? svg`<rect class="incomplete" fill="none" x=${bar.x} y=${bar.segments[bar.segments.length - 1].y} width=${bar.width}
              height=${Math.max(0, CHART_HEIGHT - MARGIN.bottom - bar.segments[bar.segments.length - 1].y)}></rect>` : nothing}
            ${i % every === 0 ? svg`<text x=${bar.x + bar.width / 2} y=${CHART_HEIGHT - 6} text-anchor="middle">${by === "day" ? bar.period.slice(5) : bar.period}</text>` : nothing}
          </g>`)}
      </svg>
      ${chart.undated ? html`<p class="dim small">${chart.undated} row${chart.undated === 1 ? "" : "s"} with no time ${chart.undated === 1 ? "isn't" : "aren't"} drawn; the totals count them.</p>` : nothing}
      ${chart.bars.some((bar) => bar.incomplete) ? html`<p class="dim small">A dashed outline marks a period whose figures aren't whole.</p>` : nothing}`;
  }

  #sessions(report: Report<SessionRow>) {
    return html`<div class="scroll"><table>
      <thead><tr><th>Session</th><th class="wide">Harness</th><th class="wide">Project</th><th class="wide">Last used</th>
        <th class="num wide">Requests</th><th class="num">Tokens</th><th class="num">Cost</th></tr></thead>
      <tbody>${report.rows.map((row) => html`
        <tr data-session=${row.session_id ?? ""}>
          <td class="wrap">${row.session_id === null ? html`<span class="dim">${row.title ?? "no session"}</span>`
            : this.#link(sessionRoute(row.session_id), row.title ?? row.session_id)}
            <div class="dim small">${row.models.join(", ")}</div></td>
          <td class="wide">${row.harness}</td>
          <td class="wrap wide">${row.project ?? row.cwd ?? ""}</td>
          <td class="wide">${fmtMoment(row.last_ts)}</td>
          <td class="num wide">${row.requests.toLocaleString("en-US")}</td>
          <td class="num">${fmtTokens(row.tokens)}</td>
          <td class="num">${costText(row)}${flag(flags(row))}</td>
        </tr>`)}</tbody>
    </table></div>
    ${report.more ? html`<p class="dim small">${report.more} more session${report.more === 1 ? "" : "s"} cost less than these.</p>` : nothing}`;
  }

  #session(report: Report<AgentRow>) {
    const list = agents(report.rows);
    const width = Math.max(280, this.width);
    const map = agentTreemap(report.rows, width, TREEMAP_HEIGHT);
    return html`
      <h2>Agents by ${map.measure}</h2>
      ${map.cells.length ? html`<svg viewBox="0 0 ${width} ${TREEMAP_HEIGHT}" role="img" aria-label="The session's agents as a treemap">
        ${map.cells.map((cell) => {
          const label = cellLabel(cell, map.measure);
          return svg`<g data-agent=${cell.agent}>
            <title>${cell.agent}, ${cell.model}: ${map.measure === "cost" ? costText({ ...report.total, cost: cell.value, harness_cost: null }) : `${fmtTokens(cell.value)} tokens`}${cell.incomplete ? " (not whole)" : ""}</title>
            <rect class="${seriesClass(cell.index)}${cell.incomplete ? " incomplete" : ""}" x=${cell.x} y=${cell.y} width=${cell.width} height=${cell.height}></rect>
            ${label ? label.map((line, i) => svg`<text class="cell-label" x=${cell.x + 6} y=${cell.y + 16 + i * 14}>${line}</text>`) : nothing}
          </g>`;
        })}
      </svg>` : html`<p class="dim">Nothing here has a cost or tokens to draw.</p>`}
      <div class="scroll"><table>
        <thead><tr><th>Agent</th><th class="wide">Kind</th><th class="wide">Models</th><th class="wide">First request</th>
          <th class="num wide">Requests</th><th class="num">Tokens</th><th class="num">Cost</th></tr></thead>
        <tbody>${list.map((agent, i) => html`
          <tr data-agent=${agent.agent}>
            <td class="wrap"><span class="swatch ${seriesClass(i)}"></span><code>${agent.agent}</code>
              ${agent.title ? html`<div class="dim small">${agent.title}</div>` : nothing}</td>
            <td class="wide">${agent.kind}</td>
            <td class="wrap wide">${agent.models.join(", ")}</td>
            <td class="wide">${fmtMoment(agent.first_ts)}</td>
            <td class="num wide">${agent.requests.toLocaleString("en-US")}</td>
            <td class="num">${fmtTokens(agent.tokens)}</td>
            <td class="num">${costText({ ...report.total, cost: agent.cost, harness_cost: agent.harness_cost })}${flag(agent.flags)}</td>
          </tr>`)}</tbody>
      </table></div>`;
  }

  #projectsView(report: Report<ProjectRow>) {
    return html`<div class="scroll"><table>
      <thead><tr><th>Project</th><th class="wide">Models</th><th class="num wide">Requests</th><th class="num">Tokens</th><th class="num">Cost</th></tr></thead>
      <tbody>${report.rows.map((row) => html`
        <tr data-project=${row.project ?? row.cwd ?? ""}>
          <td class="wrap">${row.project ?? html`<span class="dim">no project</span>`}
            ${row.cwd && row.cwd !== row.project ? html`<div class="dim small"><code>${row.cwd}</code></div>` : nothing}</td>
          <td class="wrap wide">${row.models.join(", ")}</td>
          <td class="num wide">${row.requests.toLocaleString("en-US")}</td>
          <td class="num">${fmtTokens(row.tokens)}</td>
          <td class="num">${costText(row)}${flag(flags(row))}</td>
        </tr>`)}</tbody>
    </table></div>`;
  }

  #modelsView(report: Report<ModelRow>) {
    return html`<div class="scroll"><table>
      <thead><tr><th>Model</th><th>Priced by</th><th class="num wide">Requests</th><th class="num">Tokens</th><th class="num">Cost</th></tr></thead>
      <tbody>${report.rows.map((row) => html`
        <tr data-model=${row.model ?? ""}>
          <td class="wrap"><code>${row.model ?? UNKNOWN_MODEL}</code></td>
          <td class="wrap small">${row.provenance.length ? row.provenance.map((p) => html`<div>${provenanceLine(p)}</div>`)
            : html`<span class="dim">no price</span>`}
            ${row.rules.length ? html`<div class="dim">${row.rules.join(", ")}</div>` : nothing}</td>
          <td class="num wide">${row.requests.toLocaleString("en-US")}</td>
          <td class="num">${fmtTokens(row.tokens)}</td>
          <td class="num">${costText(row)}${flag(flags(row))}</td>
        </tr>`)}</tbody>
    </table></div>
    <p class="dim small">The full price table: <code>moxi usage prices</code>.</p>`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, UsagePage);
