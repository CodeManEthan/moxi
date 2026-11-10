// <moxi-usage-legacy route="..." .filters=...>: the views the page had before its frame
// was redrawn, kept mounted under the page's chart until each has its place in the new
// page: the tab line and the view the route names: `sessions`, `projects`, `models`,
// `terms` (the cost terms: the class split, the Reconcile table and the rates), or one
// session, `session/<id>` with its main agent's requests charted or `session/<id>/<agent>`
// with another's. `""` and `daily` name the page's own chart, so only the tab line shows
// for them. Every view reads under the page's range and filters, which the page passes
// in. A row whose figures aren't whole carries a flag saying why. The limits, the filter
// line, the chart, the index's state and "Read new sessions" are the page's.

import { css, html, LitElement, nothing, svg } from "lit";
import type { TemplateResult } from "lit";
import { api, navigate, subscribe } from "/m/ui/client.js";

import { Feed, parseIndexEvent, parsePrices, parseReport } from "./feed.ts";
import type {
  AgentRow, ModelRow, Prices, ProjectRow, Report, RequestRow, RequestsTotal, SessionRow,
  TermsRow, TermsTotal,
} from "./feed.ts";
import { holdState } from "./filter-model.ts";
import { BASE, flag, SERIES } from "./parts.ts";
import {
  agents, agentTreemap, cellLabel, contextChart, costText, filterKeys, flags, fmtCost, fmtMoment,
  fmtTokens, layerLine, localDay, MARGIN, NO_FILTERS, overrideLine, parseRoute, pricesPath,
  provenanceLine, RATE_FIELDS, rateAsks, rateLine, readout, reconcileLines, REQUEST_CLASSES,
  requestNotes, requestRules, requestsChart, requestsPath, reportState, searchPath, seriesClass,
  sessionRoute, sessionTermsPath, splitBars, splitKeys, splitLabel, classIndex, TERM_CLASSES,
  tickText, UNKNOWN_MODEL, viewPath, VIEWS,
} from "./views.ts";
import type { ContextChart, Filters, PageRoute, RequestMeasure, RequestsChart, Rules } from "./views.ts";

const TAG = "moxi-usage-legacy";
const REQUESTS_HEIGHT = 200;
const SPLIT_HEIGHT = 16;
const TREEMAP_HEIGHT = 300;
const SEARCH_MS = 200;

// The request charts' measure outlives one placement: the shell places a new page for
// each route.
let requestMeasure: RequestMeasure = "tokens";

/** The hatch a price tier's part of a class's segment carries over its colour. */
const HATCH = svg`<defs><pattern id="usage-hatch" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
  <rect class="hatch-line" x="0" y="0" width="2" height="6"></rect></pattern></defs>`;

const STYLES = [BASE, SERIES, css`
  :host { margin-top: calc(var(--moxi-gap) * 2); }
  h2 { margin: calc(var(--moxi-gap) * 2) 0 var(--moxi-gap); font-size: 1.1em; }
  section { margin-bottom: calc(var(--moxi-gap) * 2); }
  button { font: inherit; padding: 4px 10px; border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius);
           background: var(--moxi-bg); color: var(--moxi-text); cursor: pointer; }
  button:hover:not(:disabled) { background: var(--moxi-bg-hover); }
  button:disabled { color: var(--moxi-text-faint); cursor: default; }
  button[aria-pressed="true"] { background: var(--moxi-selected-soft); border-color: var(--moxi-selected); }
  nav, .controls { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: var(--moxi-gap); }
  nav a { padding: 4px 10px; border-radius: var(--moxi-radius); color: var(--moxi-text); text-decoration: none; }
  nav a[aria-current="page"] { background: var(--moxi-selected-soft); color: var(--moxi-selected); font-weight: 600; }
  .seg { display: inline-flex; flex-wrap: wrap; gap: 2px; }
  label { display: inline-flex; gap: 4px; align-items: center; min-width: 0; max-width: 100%; }
  .scroll { overflow-x: auto; max-width: 100%; }
  table { width: 100%; border-collapse: collapse; font-size: var(--moxi-font-size-sm); }
  th, td { padding: 4px 6px; border-bottom: 1px solid var(--moxi-border); text-align: left; vertical-align: top; }
  th { color: var(--moxi-text-dim); font-weight: normal; white-space: nowrap; }
  td.wrap { overflow-wrap: anywhere; min-width: 8em; }
  @media (max-width: 640px) { .wide { display: none; } }
  /* Reconcile and Rates keep every column on a phone and scroll inside their wrapper; the
     Model column stays in view, opaque over the cells that scroll under it. */
  table:is([data-part="reconcile"], [data-part="rate-lines"]) :is(th, td):first-child {
    position: sticky; left: 0; z-index: 1; background: var(--moxi-bg); }
  a.link { color: var(--moxi-accent); }
  svg { display: block; width: 100%; height: auto; margin-bottom: var(--moxi-gap); }
  svg text { fill: var(--moxi-text-dim); font-size: 11px; font-family: var(--moxi-font-sans); }
  svg .axis { stroke: var(--moxi-border); }
  svg .cell-label { fill: var(--moxi-bg); font-size: 11px; }
  svg .incomplete { stroke: var(--moxi-warn); stroke-width: 2; stroke-dasharray: 3 2; }
  svg .hatch-line { fill: var(--moxi-bg); opacity: 0.7; }
  svg .area { opacity: 0.85; }
  svg .context { fill: none; stroke: var(--moxi-accent); stroke-width: 1.5; }
  svg .window { fill: none; stroke: var(--moxi-error); stroke-width: 1; stroke-dasharray: 6 3; }
  svg .turn { stroke: var(--moxi-text-faint); stroke-width: 1; }
  svg .compaction { stroke: var(--moxi-warn); stroke-width: 1.5; stroke-dasharray: 4 3; }
  svg .compaction-dot { fill: var(--moxi-warn); }
  svg .hit { fill: transparent; }
  svg .hit:hover, svg .hit[data-on] { fill: var(--moxi-text-faint); opacity: 0.25; }
  .readout { min-height: 1.4em; font-size: var(--moxi-font-size-sm); font-variant-numeric: tabular-nums; }
  .ends { display: flex; justify-content: space-between; font-size: var(--moxi-font-size-sm); color: var(--moxi-text-dim); }
  .split { display: grid; grid-template-columns: minmax(6em, 12em) 1fr; gap: 4px var(--moxi-gap); align-items: center; }
  .split svg { margin: 0; }
  tr[data-line="subtotal"] td { font-weight: 600; }
  tr[data-line="total"] td { font-weight: 600; border-top: 2px solid var(--moxi-border-strong); }
  details > summary { cursor: pointer; font-weight: 600; margin: var(--moxi-gap) 0; }
  input[type="search"] { font: inherit; max-width: 100%; padding: 3px 6px; border: 1px solid var(--moxi-border-strong);
                         border-radius: var(--moxi-radius); background: var(--moxi-bg); color: var(--moxi-text); }
  .legend { display: flex; flex-wrap: wrap; gap: 4px 12px; margin: 4px 0 var(--moxi-gap); font-size: var(--moxi-font-size-sm); }
  .swatch { display: inline-block; width: 10px; height: 10px; margin-right: 4px; border-radius: 2px; }
  .totals { display: flex; flex-wrap: wrap; gap: 4px 16px; margin-bottom: var(--moxi-gap); }
  .totals b { font-variant-numeric: tabular-nums; }
`];

type AnyReport = Report<unknown>;

export class UsageLegacy extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
    filters: { attribute: false },
    said: { type: Boolean },
    width: { state: true },
  };

  route = "";
  /** The page's range and filters, as the views' reads take them. */
  filters: Filters = { ...NO_FILTERS };
  /** The page has said that nothing was used in the range, so a view with no row says
   * nothing more. */
  said = false;
  width = 640;

  #on = false;
  #view: Feed<AnyReport> | null = null;
  /** The session view's requests and cost terms. */
  #requests: Feed<Report<RequestRow, RequestsTotal>> | null = null;
  #sessionTerms: Feed<Report<TermsRow, TermsTotal>> | null = null;
  /** The Rates section's models, opened with the section, and its search. */
  #rates: Feed<Prices> | null = null;
  #search: Feed<Prices> | null = null;
  #searchText = "";
  #searchTimer: ReturnType<typeof setTimeout> | undefined;
  /** The request the charts' readout shows. */
  #hover: number | null = null;
  #stops: (() => void)[] = [];
  #resize: ResizeObserver | null = null;
  #loadedFor: string | null = null;

  connectedCallback(): void {
    super.connectedCallback();
    this.#on = true;
    this.#stops.push(subscribe("usage", "index", (data) => this.#indexEvent(data)));
    this.#resize = new ResizeObserver((entries) => {
      const found = Math.floor(entries[0]?.contentRect.width ?? 0);
      if (found > 0 && Math.abs(found - this.width) > 8) this.width = found;
    });
    this.#resize.observe(this);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#on = false;
    this.#view?.close();
    this.#view = null;
    this.#closeExtras();
    clearTimeout(this.#searchTimer);
    this.#loadedFor = null;
    for (const stop of this.#stops) stop();
    this.#stops = [];
    this.#resize?.disconnect();
    this.#resize = null;
  }

  protected willUpdate(): void {
    if (!this.#on) return;
    const today = localDay(new Date());
    const key = `${this.route}|${JSON.stringify(filterKeys(this.filters, today))}`;
    if (key !== this.#loadedFor) {
      this.#loadedFor = key;
      this.#load(today);
    }
  }

  /** Opens the feeds of the current route and filters, closing the last ones. */
  #load(today: string): void {
    const update = () => this.requestUpdate();
    this.#view?.close();
    this.#closeExtras();
    const route = parseRoute(this.route);
    const filters = this.filters;
    // The page draws the daily chart itself.
    const path = route.view === "daily" ? null : viewPath(route, filters, today);
    this.#view = path === null ? null : new Feed<AnyReport>(() => api("usage", path), parseReport, update);
    if (route.view === "session") {
      const requests = requestsPath(route, filters, today);
      const terms = sessionTermsPath(route, filters, today);
      this.#requests = new Feed(() => api("usage", requests), parseReport<RequestRow, RequestsTotal>, update);
      this.#sessionTerms = new Feed(() => api("usage", terms), parseReport<TermsRow, TermsTotal>, update);
    }
    for (const feed of [this.#requests, this.#sessionTerms]) void feed?.refresh();
    void this.#view?.refresh();
  }

  #closeExtras(): void {
    for (const feed of [this.#requests, this.#sessionTerms, this.#rates, this.#search]) feed?.close();
    this.#requests = this.#sessionTerms = this.#rates = this.#search = null;
    this.#searchText = "";
    this.#hover = null;
  }

  /** A pass ended: the reports are read again. */
  #indexEvent(data: unknown): void {
    const event = parseIndexEvent(data);
    if (event === null || event.running) return;
    void this.#view?.refresh();
    for (const feed of [this.#requests, this.#sessionTerms]) void feed?.refresh();
  }

  render() {
    if (!this.#on) return nothing;
    const route = parseRoute(this.route);
    // An address that names no view has its line at the frame's foot.
    if (route.view === "unknown") return nothing;
    return html`
      ${this.#nav(route)}
      ${route.view === "daily" ? nothing
        : html`<section data-part="view" data-view=${route.view}>${this.#body(route)}</section>`}`;
  }

  /** A link to one of the page's routes; `follow` runs just before the page moves there. */
  #link(route: string, label: string | TemplateResult, current = false, follow?: () => void) {
    return html`<a href=${`/m/usage/${route}`} class=${current ? "" : "link"} aria-current=${current ? "page" : "false"}
      @click=${(event: MouseEvent) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        follow?.();
        navigate("usage", route);
      }}>${label}</a>`;
  }

  #nav(route: PageRoute) {
    const at = route.view === "session" ? "sessions" : route.view;
    return html`<nav aria-label="Views">${VIEWS.map(([name, label]) =>
      this.#link(name === "daily" ? "" : name, label, at === name))}</nav>`;
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
      if (this.said && !(detail && /moxi usage index/.test(detail))) return this.#heading(route);
      return html`${this.#heading(route)}<p class="dim" data-state="empty">${detail && /moxi usage index/.test(detail)
        ? html`${detail}` : "Nothing was used in this range."}</p>`;
    }
    const report = feed.value;
    return html`${stale}${this.#heading(route)}${this.#totals(report)}${this.#view_(route, report)}`;
  }

  #heading(route: PageRoute) {
    if (route.view !== "session") return nothing;
    return html`<p>${this.#link("sessions", "All sessions")} › <code>${route.session}</code></p>`;
  }

  #totals(report: AnyReport) {
    const total = report.total;
    return html`<div class="totals" data-part="totals">
      <span>Cost <b>${costText(total)}</b>${flag(flags({ ...total, unpriced_models: report.unpriced_models }))}</span>
      <span>Tokens <b>${fmtTokens(total.tokens)}</b></span>
      <span>Requests <b>${total.requests.toLocaleString("en-US")}</b></span>
    </div>`;
  }

  #view_(route: PageRoute, report: AnyReport) {
    switch (route.view) {
      case "sessions": return this.#sessions(report as Report<SessionRow>);
      case "session": return this.#session(report as Report<AgentRow>, route);
      case "projects": return this.#projectsView(report as Report<ProjectRow>);
      case "models": return this.#modelsView(report as Report<ModelRow>);
      case "terms": return this.#termsView(report as Report<TermsRow, TermsTotal>);
      default: return nothing;
    }
  }

  #sessions(report: Report<SessionRow>) {
    return html`<div class="scroll"><table>
      <thead><tr><th>Session</th><th class="wide">Harness</th><th class="wide">Project</th><th class="wide">Last used</th>
        <th class="num wide">Requests</th><th class="num">Tokens</th><th class="num">Cost</th></tr></thead>
      <tbody>${report.rows.map((row) => html`
        <tr data-session=${row.session_id ?? ""}>
          <td class="wrap">${row.session_id === null ? html`<span class="dim">no session</span>`
            : this.#link(sessionRoute(row.session_id), row.session_id, false,
              () => { holdState({ harness: row.harness }); })}
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

  #session(report: Report<AgentRow>, route: PageRoute) {
    const list = agents(report.rows);
    const width = Math.max(280, this.width);
    const map = agentTreemap(report.rows, width, TREEMAP_HEIGHT);
    const agent = route.agent ?? "main";
    const terms = this.#sessionTerms;
    return html`
      <h2>Requests of <code>${agent}</code></h2>
      ${this.#requestCharts(route)}
      <h2>Cost terms</h2>
      ${terms?.value ? html`${this.#splitBars(terms.value.total.split.length
        ? [{ ...terms.value.rows[0], model: "this session", split: terms.value.total.split }] : [])}
        ${this.#reconcile(terms.value)}` : this.#extraState(terms)}
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
          <tr data-agent=${agent.agent} ?data-current=${agent.agent === (route.agent ?? "main")}>
            <td class="wrap"><span class="swatch ${seriesClass(i)}"></span>${agent.agent === (route.agent ?? "main")
              ? html`<code aria-current="page">${agent.agent}</code>`
              : this.#link(sessionRoute(route.session ?? "", agent.agent), html`<code>${agent.agent}</code>`)}
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

  /** What a view's second feed shows while it has no value: reading, refused or failed. */
  #extraState(feed: Feed<Report<TermsRow, TermsTotal>> | Feed<Report<RequestRow, RequestsTotal>> | null) {
    if (feed === null) return nothing;
    const { state, detail } = reportState(feed as Feed<Report<unknown>>);
    if (state === "loading") return html`<p class="dim" data-state="loading">Reading…</p>`;
    if (state === "refused") return html`<p class="problem" data-state="refused">${detail}</p>`;
    if (state === "failed" || state === "gone") return html`<p class="problem" data-state="failed">This can't be read: ${detail ?? "the module went away"}.</p>`;
    return nothing;
  }

  /** One horizontal bar per row: its cost split by class and tier. */
  #splitBars(rows: TermsRow[]) {
    const width = Math.max(160, this.width - 200);
    const bars = splitBars(rows, width);
    const keys = splitKeys(rows.flatMap((row) => row.split));
    return html`<div class="legend">${keys.map((key) => html`
        <span data-key=${key}><span class="swatch ${seriesClass(classIndex(key))}"></span>${splitLabel(key)}</span>`)}</div>
      <div class="split" data-part="split">${bars.map((bar) => html`
        <span class="small wrap"><code>${bar.label}</code> ${fmtCost(bar.total)}</span>
        <svg viewBox="0 0 ${width} ${SPLIT_HEIGHT}" preserveAspectRatio="none" role="img" aria-label=${`${bar.label}: cost by class`}>
          ${HATCH}
          ${bar.segments.map((seg) => svg`<rect class=${seriesClass(seg.index)} data-key=${seg.key} x=${seg.x} y="0" width=${Math.max(0, seg.width)} height=${SPLIT_HEIGHT}>
            <title>${seg.title}</title></rect>
            ${seg.hatch ? svg`<rect fill="url(#usage-hatch)" pointer-events="none" x=${seg.x} y="0" width=${Math.max(0, seg.width)} height=${SPLIT_HEIGHT}></rect>` : nothing}`)}
        </svg>`)}</div>`;
  }

  /** The Reconcile table: a line per price group, a subtotal under a model of two or more,
   * and the total. */
  #reconcile(report: Report<TermsRow, TermsTotal>) {
    const lines = reconcileLines(report);
    return html`<div class="scroll"><table data-part="reconcile">
      <thead><tr><th>Model</th><th>Priced by</th>${TERM_CLASSES.map(([, label]) => html`<th class="num">${label}</th>`)}<th class="num">Cost</th></tr></thead>
      <tbody>${lines.map((line) => html`
        <tr data-line=${line.kind} data-model=${line.model}>
          <td class="wrap"><code>${line.model}</code></td>
          <td class="wrap small">${line.pricedBy}</td>
          ${line.cells.map((cell) => html`<td class="num" data-class=${cell.cls} title=${cell.title}>${cell.text}</td>`)}
          <td class="num" data-cost=${line.cost === null ? "" : String(line.cost)}>${fmtCost(line.cost)}${flag(line.flags)}</td>
        </tr>`)}</tbody>
    </table></div>
    <p class="dim small">The terms on each line add up to its cost.</p>`;
  }

  #termsView(report: Report<TermsRow, TermsTotal>) {
    return html`
      <h2>Cost by class</h2>
      ${this.#splitBars(report.rows)}
      <h2>Reconcile</h2>
      ${this.#reconcile(report)}
      ${this.#ratesSection(report)}`;
  }

  /** The Rates section: closed until opened, then the rates of the models the Reconcile
   * lines priced, a search over the whole model table, your `[[model]]` rows and the
   * table's layers. */
  #ratesSection(report: Report<TermsRow, TermsTotal>) {
    const { asks, more } = rateAsks(report.rows);
    const toggle = (event: Event) => {
      const open = (event.target as HTMLDetailsElement).open;
      if (open && this.#rates === null && asks.length) {
        const path = pricesPath(asks);
        this.#rates = new Feed<Prices>(() => api("usage", path), parsePrices, () => this.requestUpdate());
        void this.#rates.refresh();
      }
    };
    const search = (event: Event) => {
      const text = (event.target as HTMLInputElement).value.trim();
      clearTimeout(this.#searchTimer);
      this.#searchTimer = setTimeout(() => {
        this.#searchText = text;
        this.#search?.close();
        this.#search = null;
        if (text.length >= 2) {
          const path = searchPath(text);
          this.#search = new Feed<Prices>(() => api("usage", path), parsePrices, () => this.requestUpdate());
          void this.#search.refresh();
        }
        this.requestUpdate();
      }, SEARCH_MS);
    };
    const feed = this.#searchText.length >= 2 ? this.#search : this.#rates;
    const value = feed?.value ?? null;
    return html`<details data-part="rates" @toggle=${toggle}>
      <summary>Rates</summary>
      <p><label>Search the model table <input type="search" aria-label="Search the model table" @input=${search}></label></p>
      ${feed === null ? html`<p class="dim small">${asks.length ? "Reading…" : "No model here has a rate to show."}</p>`
        : feed.value === null ? html`<p class=${feed.loading ? "dim" : "problem"}>${feed.loading ? "Reading…" : `The rates can't be read: ${feed.refused ?? feed.failure?.why}.`}</p>`
        : this.#rateTable(feed.value)}
      ${feed === this.#search && value && value.matched > value.models.length
        ? html`<p class="dim small" data-part="more">${value.matched - value.models.length} more</p>` : nothing}
      ${feed === this.#rates && more ? html`<p class="dim small">${more} more models in these lines aren't listed; search for one.</p>` : nothing}
      ${value ? html`
        <h3>Your [[model]] rows</h3>
        ${value.overrides.length ? html`<ul data-part="overrides">${value.overrides.map((row) => html`<li>${overrideLine(row)}</li>`)}</ul>`
          : html`<p class="dim small">None: every price comes from the model table.</p>`}
        <p class="dim small" data-part="layers">${layerLine(value)}</p>
        ${value.problems.map((problem) => html`<p class="dim small">skipped: ${problem}</p>`)}` : nothing}
    </details>`;
  }

  #rateTable(prices: Prices) {
    const lines = prices.models.map(rateLine);
    if (!lines.length) return html`<p class="dim small">No model matches.</p>`;
    return html`<div class="scroll"><table data-part="rate-lines">
      <thead><tr><th>Model</th>${RATE_FIELDS.map(([, label]) => html`<th class="num">${label}</th>`)}
        <th class="num">Window</th><th>Tiers</th><th>Period</th><th>Where from</th></tr></thead>
      <tbody>${lines.map((line) => html`
        <tr data-rate=${line.model} data-provider=${line.provider ?? ""}>
          <td class="wrap"><code>${line.model}</code>${line.provider ? html`<div class="dim small">${line.provider}</div>` : nothing}
            ${line.known ? nothing : html`<div class="dim small">not in the model table</div>`}</td>
          ${line.cells.map((cell) => html`<td class="num">${cell.text}${cell.mark ? html`<div class="dim small">${cell.mark}</div>` : nothing}</td>`)}
          <td class="num">${line.window}</td>
          <td class="wrap small">${line.tiers.map((tier) => html`<div>${tier}</div>`)}</td>
          <td class="small">${line.period}</td>
          <td class="wrap small">${line.from}</td>
        </tr>`)}</tbody>
    </table></div>`;
  }

  /** The rules both request charts draw, and the hover columns over them. */
  #rules(rules: Rules, height: number, labelled: boolean) {
    return svg`
      ${rules.turns.map((rule) => svg`<line class="turn" data-rule="turn" x1=${rule.x} x2=${rule.x} y1=${MARGIN.top} y2=${height - MARGIN.bottom}></line>`)}
      ${rules.compactions.map((rule) => svg`<g data-rule="compaction" data-after=${rule.after}><title>${rule.title}</title>
        <line class="compaction" x1=${rule.x} x2=${rule.x} y1=${MARGIN.top} y2=${height - MARGIN.bottom}></line>
        ${labelled ? svg`<text x=${rule.labelX} y=${MARGIN.top + 10} text-anchor=${rule.anchor}>${rule.label}</text>` : nothing}</g>`)}`;
  }

  #hits(rows: RequestRow[], chart: { axis: RequestsChart["axis"] }, height: number) {
    return svg`${rows.map((row) => svg`<rect class="hit" data-n=${row.n} ?data-on=${this.#hover === row.n}
      x=${chart.axis.edge(row.n - 1)} y=${MARGIN.top} width=${chart.axis.step} height=${height - MARGIN.top - MARGIN.bottom}
      @mouseenter=${() => { this.#hover = row.n; this.requestUpdate(); }}></rect>`)}`;
  }

  /** The session's agent's requests: tokens (or cost) by class per request, and its
   * context against the window, on one request axis. */
  #requestCharts(route: PageRoute) {
    const feed = this.#requests;
    if (feed === null) return nothing;
    if (feed.value === null) return this.#extraState(feed);
    const { rows, total } = feed.value;
    const agent = route.agent ?? "main";
    if (rows.length === 0) return html`<p class="dim" data-state="empty">No request of <code>${agent}</code> is in this range.</p>`;
    const width = Math.max(280, this.width);
    const chart = requestsChart(rows, requestMeasure, width, REQUESTS_HEIGHT);
    const context: ContextChart = contextChart(rows, width, REQUESTS_HEIGHT);
    const rules = requestRules(rows, total, chart.axis);
    const set = (value: RequestMeasure) => { requestMeasure = value; this.requestUpdate(); };
    const hovered = rows.find((row) => row.n === this.#hover) ?? null;
    const ends = html`<div class="ends"><span>${fmtMoment(rows[0].ts)}</span><span>${fmtMoment(rows[rows.length - 1].ts)}</span></div>`;
    return html`
      <div class="controls">
        <span class="seg" role="group" aria-label="Request measure">
          <button type="button" aria-pressed=${String(requestMeasure === "tokens")} @click=${() => set("tokens")}>Tokens</button>
          <button type="button" aria-pressed=${String(requestMeasure === "cost")} @click=${() => set("cost")}>Cost</button>
        </span>
      </div>
      <div class="legend">${REQUEST_CLASSES.map(([key, label], i) => html`
        <span><span class="swatch ${seriesClass(i)}"></span>${label}</span>`)}</div>
      <svg data-chart="requests" viewBox="0 0 ${width} ${REQUESTS_HEIGHT}" role="img"
        aria-label=${requestMeasure === "tokens" ? "Tokens by class per request" : "Cost by class per request"}
        @mouseleave=${() => { this.#hover = null; this.requestUpdate(); }}>
        ${chart.ticks.map((tick) => svg`
          <line class="axis" x1=${MARGIN.left} x2=${width - MARGIN.right} y1=${tick.y} y2=${tick.y}></line>
          <text x=${MARGIN.left - 4} y=${tick.y + 4} text-anchor="end">${tickText(tick.value, requestMeasure)}</text>`)}
        ${chart.bars.map((bar) => svg`<g data-n=${bar.n}>${bar.segments.map((seg) => svg`
          <rect class=${seriesClass(seg.index)} data-key=${seg.key} x=${bar.x} y=${seg.y} width=${bar.width} height=${Math.max(0, seg.height)}></rect>`)}</g>`)}
        ${chart.areas.map((area) => svg`<path class="area ${seriesClass(area.index)}" data-key=${area.key} d=${area.d}></path>`)}
        ${this.#rules(rules, REQUESTS_HEIGHT, true)}
        ${this.#hits(rows, chart, REQUESTS_HEIGHT)}
      </svg>
      ${ends}
      <svg data-chart="context" viewBox="0 0 ${width} ${REQUESTS_HEIGHT}" role="img" aria-label="Context per request against the window"
        @mouseleave=${() => { this.#hover = null; this.requestUpdate(); }}>
        ${context.ticks.map((tick) => svg`
          <line class="axis" x1=${MARGIN.left} x2=${width - MARGIN.right} y1=${tick.y} y2=${tick.y}></line>
          <text x=${MARGIN.left - 4} y=${tick.y + 4} text-anchor="end">${fmtTokens(tick.value)}</text>`)}
        ${context.window ? svg`<path class="window" data-part="window" d=${context.window}></path>` : nothing}
        ${context.windowLabels.map((label) => svg`<text class="window-label" x=${label.x} y=${label.y} text-anchor=${label.anchor}>${label.text}</text>`)}
        <path class="context" data-part="context" data-points=${context.points} d=${context.line}></path>
        ${rules.compactions.map((rule) => {
          const y = rule.dot === null ? null : context.ys[rule.dot - 1];
          return y === null || y === undefined ? nothing
            : svg`<circle class="compaction-dot" cx=${context.axis.x(rule.dot as number)} cy=${y} r="3"></circle>`;
        })}
        ${this.#rules(rules, REQUESTS_HEIGHT, false)}
        ${this.#hits(rows, context, REQUESTS_HEIGHT)}
      </svg>
      ${ends}
      ${context.unknownWindow ? html`<p class="dim small" data-part="unknown-window">${context.unknownWindow}</p>` : nothing}
      <p class="readout" data-part="readout">${hovered ? readout(hovered) : html`<span class="dim">Point at a request to read it.</span>`}</p>
      ${requestNotes(rows, total).map((note) => html`<p class="dim small" data-part="note">${note}</p>`)}`;
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
    <p class="dim small">Every term of these costs, and the rates of these models: ${this.#link("terms", "Cost terms")}.
      The full price table: <code>moxi usage prices</code>.</p>`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, UsageLegacy);
