// <moxi-usage-limits>: one meter per plan window, with its label, percent, reset time,
// the reading's age and when it fills (or why that isn't known), and its state and
// reason. It reads `/api/limits`, then follows the topic `limits`.

import { css, html, LitElement, nothing } from "lit";
import { api, subscribe } from "/m/ui/client.js";

import { Feed, parseLimits } from "./feed.ts";
import type { Limits } from "./feed.ts";
import { BASE, meterList } from "./parts.ts";
import { meters } from "./views.ts";

const TAG = "moxi-usage-limits";
const TICK_MS = 15_000;

const STYLES = [BASE, css`
  h2 { margin: 0 0 var(--moxi-gap); font-size: 1.1em; }
  .notes { margin: var(--moxi-gap) 0 0; padding: 0; list-style: none; }
`];

export class UsageLimits extends LitElement {
  static styles = STYLES;

  static properties = { tick: { state: true } };

  tick = 0;
  #feed: Feed<Limits> | null = null;
  #stop: (() => void) | null = null;
  #timer: ReturnType<typeof setInterval> | undefined;
  #shown = (): void => {
    if (document.visibilityState === "visible") void this.#feed?.refresh();
  };

  connectedCallback(): void {
    super.connectedCallback();
    const feed = new Feed<Limits>(() => api("usage", "api/limits"), parseLimits, () => this.requestUpdate());
    this.#feed = feed;
    void feed.refresh().then(() => {
      if (this.#feed === feed) this.#stop = subscribe("usage", "limits", (data) => feed.topic(data));
    });
    document.addEventListener("visibilitychange", this.#shown);
    this.#timer = setInterval(() => { this.tick += 1; }, TICK_MS);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#feed?.close();
    this.#feed = null;
    this.#stop?.();
    this.#stop = null;
    document.removeEventListener("visibilitychange", this.#shown);
    clearInterval(this.#timer);
  }

  render() {
    const feed = this.#feed;
    if (feed === null || feed.gone) return nothing;
    const view = meters(feed.value, feed.now());
    let status = nothing as unknown;
    if (feed.loading) status = html`<p class="dim">Reading the limits…</p>`;
    else if (feed.value === null) status = html`<p class="problem">The limits can't be read: ${feed.refused ?? feed.failure?.why}.</p>`;
    else if (feed.failure !== null) status = html`<p class="dim small">Showing the last limits read: ${feed.failure.why}.</p>`;
    else if (view.meters.length === 0) status = html`<p class="dim">No plan window has a reading yet.</p>`;
    return html`
      <h2>Limits</h2>
      <div data-state=${feed.loading ? "loading" : feed.value === null ? "failed" : "up"}>${status}</div>
      ${meterList(view.meters)}
      ${view.notes.length ? html`<ul class="notes">${view.notes.map((note) => html`<li class="dim small">${note}</li>`)}</ul>` : nothing}`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, UsageLimits);
