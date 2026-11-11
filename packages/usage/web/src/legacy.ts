// <moxi-usage-legacy .address=... .filters=... .open=...>: the views the page had before
// its frame was redrawn, kept mounted under the page's chart until each has its place in
// the new page. It draws what serves the page's address (`legacyRoute`) and is not yet
// in the opened session: a session's agents as a treemap and a table, and a subagent's
// requests charted. For any other address it draws nothing. A link here opens its piece
// through the page, which writes the address. Every view reads under the page's range and
// filters, which the page passes in. A row whose figures aren't whole carries a flag
// saying why. The session's own lines, chart, table and bill are the page's.

import { css, html, LitElement, nothing, svg } from "lit";
import type { TemplateResult } from "lit";
import { api, subscribe } from "/m/ui/client.js";

import { Feed, parseIndexEvent, parseReport } from "./feed.ts";
import type { AgentRow, Report, RequestRow, RequestsTotal } from "./feed.ts";
import { addressPath, NOTHING_OPEN, sessionAddress } from "./address-model.ts";
import type { Address } from "./address-model.ts";
import { BASE, flag, SERIES } from "./parts.ts";
import {
  agents, agentTreemap, cellLabel, contextChart, costText, filterKeys, fmtMoment,
  fmtTokens, legacyRoute, localDay, MARGIN, NO_FILTERS, parseRoute,
  readout, REQUEST_CLASSES,
  requestNotes, requestRules, requestsChart, requestsPath, reportState, seriesClass,
  tickText, viewPath,
} from "./views.ts";
import type { ContextChart, Filters, PageRoute, RequestMeasure, RequestsChart, Rules } from "./views.ts";

const TAG = "moxi-usage-legacy";
const REQUESTS_HEIGHT = 200;
const TREEMAP_HEIGHT = 300;

// The request charts' measure outlives one placement of the page.
let requestMeasure: RequestMeasure = "tokens";

const STYLES = [BASE, SERIES, css`
  :host { margin-top: calc(var(--moxi-gap) * 2); }
  h2 { margin: calc(var(--moxi-gap) * 2) 0 var(--moxi-gap); font-size: 1.1em; }
  section { margin-bottom: calc(var(--moxi-gap) * 2); }
  button { font: inherit; padding: 4px 10px; border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius);
           background: var(--moxi-bg); color: var(--moxi-text); cursor: pointer; }
  button:hover:not(:disabled) { background: var(--moxi-bg-hover); }
  button:disabled { color: var(--moxi-text-faint); cursor: default; }
  button[aria-pressed="true"] { background: var(--moxi-selected-soft); border-color: var(--moxi-selected); }
  .controls { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: var(--moxi-gap); }
  .seg { display: inline-flex; flex-wrap: wrap; gap: 2px; }
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
  .legend { display: flex; flex-wrap: wrap; gap: 4px 12px; margin: 4px 0 var(--moxi-gap); font-size: var(--moxi-font-size-sm); }
  .swatch { display: inline-block; width: 10px; height: 10px; margin-right: 4px; border-radius: 2px; }
`];

type AnyReport = Report<unknown>;

export class UsageLegacy extends LitElement {
  static styles = STYLES;

  static properties = {
    address: { attribute: false },
    open: { attribute: false },
    filters: { attribute: false },
    said: { type: Boolean },
    width: { state: true },
  };

  /** What the page has open. */
  address: Address = NOTHING_OPEN;
  /** Opens a piece of the page; the page gives it. */
  open: ((address: Address) => void) | null = null;
  /** The page's range and filters, as the views' reads take them. */
  filters: Filters = { ...NO_FILTERS };
  /** The page has said that nothing was used in the range, so a view with no row says
   * nothing more. */
  said = false;
  width = 640;

  #on = false;
  #view: Feed<AnyReport> | null = null;
  /** A subagent's requests. */
  #requests: Feed<Report<RequestRow, RequestsTotal>> | null = null;
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
    this.#loadedFor = null;
    for (const stop of this.#stops) stop();
    this.#stops = [];
    this.#resize?.disconnect();
    this.#resize = null;
  }

  protected willUpdate(): void {
    if (!this.#on) return;
    const today = localDay(new Date());
    const key = `${this.#route() ?? ""}|${JSON.stringify(filterKeys(this.filters, today))}`;
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
    const found = this.#route();
    if (found === null) {
      this.#view = null;
      return;
    }
    const route = parseRoute(found);
    const filters = this.filters;
    const path = viewPath(route, filters, today);
    this.#view = path === null ? null : new Feed<AnyReport>(() => api("usage", path), parseReport, update);
    if (route.view === "session" && (route.agent ?? "main") !== "main") {
      const requests = requestsPath(route, filters, today);
      this.#requests = new Feed(() => api("usage", requests), parseReport<RequestRow, RequestsTotal>, update);
    }
    void this.#requests?.refresh();
    void this.#view?.refresh();
  }

  #closeExtras(): void {
    this.#requests?.close();
    this.#requests = null;
    this.#hover = null;
  }

  /** A pass ended: the reports are read again. */
  #indexEvent(data: unknown): void {
    const event = parseIndexEvent(data);
    if (event === null || event.running) return;
    void this.#view?.refresh();
    void this.#requests?.refresh();
  }

  render() {
    if (!this.#on) return nothing;
    const found = this.#route();
    if (found === null) return nothing;
    const route = parseRoute(found);
    return html`<section data-part="view" data-view=${route.view}>${this.#body(route)}</section>`;
  }

  /** The route of the view that serves the page's address; null when none does. */
  #route(): string | null {
    return legacyRoute(this.address);
  }

  /** The harness of the session the address names. */
  #harness(): string {
    return this.address.piece === "sessions" ? this.address.harness ?? "" : "";
  }

  /** A link that opens a piece of the page. */
  #link(address: Address, label: string | TemplateResult) {
    return html`<a href=${addressPath(address)} class="link" @click=${(event: MouseEvent) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      this.open?.(address);
    }}>${label}</a>`;
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
      if (this.said && !(detail && /moxi usage index/.test(detail))) return nothing;
      return html`<p class="dim" data-state="empty">${detail && /moxi usage index/.test(detail)
        ? html`${detail}` : "Nothing was used in this range."}</p>`;
    }
    const report = feed.value;
    return html`${stale}${this.#view_(route, report)}`;
  }

  #view_(route: PageRoute, report: AnyReport) {
    switch (route.view) {
      case "session": return this.#session(report as Report<AgentRow>, route);
      default: return nothing;
    }
  }

  #session(report: Report<AgentRow>, route: PageRoute) {
    const list = agents(report.rows);
    const width = Math.max(280, this.width);
    const map = agentTreemap(report.rows, width, TREEMAP_HEIGHT);
    const agent = route.agent ?? "main";
    return html`
      ${agent === "main" ? nothing : html`<h2>Requests of <code>${agent}</code></h2>${this.#requestCharts(route)}`}
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
              : this.#link(sessionAddress(this.#harness(), route.session ?? "", agent.agent === "main" ? null : agent.agent),
                           html`<code>${agent.agent}</code>`)}
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

  /** What a view's second feed shows while it has no value: reading, refused or failed. */
  #extraState(feed: Feed<Report<RequestRow, RequestsTotal>> | null) {
    if (feed === null) return nothing;
    const { state, detail } = reportState(feed as Feed<Report<unknown>>);
    if (state === "loading") return html`<p class="dim" data-state="loading">Reading…</p>`;
    if (state === "refused") return html`<p class="problem" data-state="refused">${detail}</p>`;
    if (state === "failed" || state === "gone") return html`<p class="problem" data-state="failed">This can't be read: ${detail ?? "the module went away"}.</p>`;
    return nothing;
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
}

if (!customElements.get(TAG)) customElements.define(TAG, UsageLegacy);
