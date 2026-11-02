// <moxi-usage-cost>: today and the last 7 days, cost and tokens, each flagged when its
// figures aren't whole. It reads `/api/cost`, and again when a pass the page server ran
// ends (the topic `index`).

import { css, html, LitElement, nothing } from "lit";
import { api, subscribe } from "/m/ui/client.js";

import { Feed, parseIndexEvent, parseReport } from "./feed.ts";
import type { DayRow, Report } from "./feed.ts";
import { BASE, flag } from "./parts.ts";
import { costDays, fmtCost, fmtTokens } from "./views.ts";
import type { CostDay } from "./views.ts";

const TAG = "moxi-usage-cost";

const STYLES = [BASE, css`
  h2 { margin: 0 0 var(--moxi-gap); font-size: 1.1em; }
  .totals { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 9em), 1fr)); gap: var(--moxi-gap); }
  .total { padding: 8px 10px; border: 1px solid var(--moxi-border); border-radius: var(--moxi-radius); background: var(--moxi-bg-raised); }
  .total .what { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  .total .cost { font-size: 1.3em; font-weight: 600; color: var(--moxi-accent); font-variant-numeric: tabular-nums; }
  table { width: 100%; margin-top: var(--moxi-gap); border-collapse: collapse; font-size: var(--moxi-font-size-sm); }
  td, th { padding: 2px 4px; border-bottom: 1px solid var(--moxi-border); }
  th { text-align: left; font-weight: normal; color: var(--moxi-text-dim); }
`];

/** The block's report, by day and model, with the server's `today`. */
const parseCost = (data: unknown): Report<DayRow> | null => {
  const found = parseReport<DayRow>(data);
  return found !== null && typeof found.today === "string" ? found : null;
};

export class UsageCost extends LitElement {
  static styles = STYLES;

  #feed: Feed<Report<DayRow>> | null = null;
  #stop: (() => void) | null = null;
  #shown = (): void => {
    if (document.visibilityState === "visible") void this.#feed?.refresh();
  };

  connectedCallback(): void {
    super.connectedCallback();
    const feed = new Feed<Report<DayRow>>(() => api("usage", "api/cost"), parseCost, () => this.requestUpdate());
    this.#feed = feed;
    void feed.refresh().then(() => {
      if (this.#feed !== feed) return;
      this.#stop = subscribe("usage", "index", (data) => {
        const event = parseIndexEvent(data);
        if (event !== null && !event.running) void feed.refresh();
      });
    });
    document.addEventListener("visibilitychange", this.#shown);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#feed?.close();
    this.#feed = null;
    this.#stop?.();
    this.#stop = null;
    document.removeEventListener("visibilitychange", this.#shown);
  }

  render() {
    const feed = this.#feed;
    if (feed === null || feed.gone) return nothing;
    const found = feed.value === null ? null : costDays(feed.value);
    if (found === null) {
      const line = feed.loading ? html`<p class="dim">Reading the cost…</p>`
        : html`<p class="problem">The cost can't be read: ${feed.refused ?? feed.failure?.why ?? "no answer"}.</p>`;
      return html`<h2>Cost</h2><div data-state=${feed.loading ? "loading" : "failed"}>${line}</div>`;
    }
    const note = feed.value?.index.note;
    const none = feed.value?.rows.length === 0 && note && /moxi usage index/.test(note);
    return html`
      <h2>Cost</h2>
      <div data-state="up">${none ? html`<p class="dim small">${note}</p>` : nothing}</div>
      <div class="totals">
        ${this.#total("Today", found.today)}
        ${this.#total("Last 7 days", found.week)}
      </div>
      <table>
        <thead><tr><th>Day</th><th class="num">Cost</th><th class="num">Tokens</th></tr></thead>
        <tbody>${found.days.slice().reverse().map((day) => html`
          <tr data-day=${day.day}><td>${day.day}</td>
            <td class="num">${fmtCost(day.cost)}${this.#flag(day)}</td>
            <td class="num">${fmtTokens(day.tokens)}</td></tr>`)}</tbody>
      </table>`;
  }

  #total(what: string, day: CostDay) {
    return html`<div class="total"><div class="what">${what}</div>
      <div class="cost">${fmtCost(day.cost)}${this.#flag(day)}</div>
      <div class="dim small">${fmtTokens(day.tokens)} tokens</div></div>`;
  }

  #flag(day: CostDay) {
    if (day.complete) return nothing;
    return flag([day.cost === null ? "nothing on this day could be priced"
      : "some of these figures are unknown or unpriced, so the cost is at least what it shows"]);
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, UsageCost);
