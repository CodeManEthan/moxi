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

// <moxi-usage-page route="...">: usage's page. Its frame, top to bottom:
//
//   p[data-part=reading]        `Reading…`, until the first read answers
//   div[data-part=warning]      the one line while a plan limit fills before it resets
//   div[data-part=top]          the groups above the filter line:
//     section[data-group=limits]   one per harness with a window to draw, a row per window
//     section[data-group=cost]     today, 7 days and 30 days, each with the period before
//   div[data-part=nothing]      in place of Cost and all under it while nothing is counted
//   moxi-usage-legacy           the views the page had before, while the frame holds an
//                               index with requests: the place of the filter line, the
//                               chart and the sections
//   div[data-part=foot]         when the sessions were last read, the files, the act
//   div[data-part=lines]        dim lines: what the figures leave out, reads that failed
//
// The view places what `frame-model.ts` returns and words nothing itself: a piece whose
// fact no read carries is not in the model and is not drawn.

import { css, html, LitElement, nothing, svg } from "lit";
import { api, navigate, subscribe } from "/m/ui/client.js";

import { LOCAL } from "./days.ts";
import { Feed, parseIndexEvent, parseLimits, parseReport, parseStatus, passOutcome } from "./feed.ts";
import type { DayRow, IndexEvent, Limits, Report, Status } from "./feed.ts";
import { COPIED, COPY, costPath, frameModel, newestDay, passLine, shouldPass } from "./frame-model.ts";
import type { Cost, Foot, FootLine, Nothing } from "./frame-model.ts";
import "./legacy.ts";
import type { LimitGroup, LimitRow, Warning } from "./limits-model.ts";
import { parseRoute } from "./views.ts";

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
  };

  route = "";
  tick = 0;
  /** The command was just copied. */
  copied = false;

  #limits: Feed<Limits> | null = null;
  #status: Feed<Status> | null = null;
  /** The Cost rows' report. */
  #daily: Feed<Report<DayRow>> | null = null;
  #event: IndexEvent | null = null;
  /** How many `index` events have come. */
  #events = 0;
  /** What a pass the page asked for came to, when it did not start. */
  #pass: string | null = null;
  #stops: (() => void)[] = [];
  #timer: ReturnType<typeof setInterval> | undefined;
  #shown = (): void => {
    if (document.visibilityState !== "visible") return;
    void this.#limits?.refresh();
    void this.#status?.refresh();
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
    document.addEventListener("visibilitychange", this.#shown);
    this.#timer = setInterval(() => { this.tick += 1; }, TICK_MS);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    for (const feed of [this.#limits, this.#status, this.#daily]) feed?.close();
    this.#limits = this.#status = this.#daily = null;
    for (const stop of this.#stops) stop();
    this.#stops = [];
    document.removeEventListener("visibilitychange", this.#shown);
    clearInterval(this.#timer);
  }

  #indexEvent(data: unknown): void {
    const event = parseIndexEvent(data);
    if (event === null) return;
    this.#event = event;
    this.#events += 1;
    if (!event.running) {
      void this.#status?.refresh();
      void this.#daily?.refresh();
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
    const frame = frameModel({
      limits, status, daily, event: this.#event, pass: this.#pass, most: null, range: null, others: [],
      unknownAddress: parseRoute(this.route).view === "unknown", now: limits.now(),
    });
    const groups = frame.limits.groups;
    return html`
      ${frame.reading === null ? nothing : html`<p class="reading dim" data-part="reading">${frame.reading}</p>`}
      ${frame.limits.warning === null ? nothing : this.#warning(frame.limits.warning)}
      ${groups.length || frame.cost ? html`<div class="grps" data-part="top">
        ${groups.map((group) => this.#group(group))}
        ${frame.cost === null ? nothing : this.#cost(frame.cost)}
      </div>` : nothing}
      ${frame.nothing === null ? nothing : this.#nothing(frame.nothing)}
      ${frame.below ? html`<moxi-usage-legacy .route=${this.route}></moxi-usage-legacy>` : nothing}
      ${frame.foot === null ? nothing : this.#foot(frame.foot)}
      ${frame.lines.length ? this.#lines(frame.lines) : nothing}`;
  }

  #warning(warning: Warning) {
    return html`<div class="warnline" data-part="warning"><b>${warning.lead}</b>${
      warning.before === null ? nothing : html`, ${warning.before}`}${
      warning.more === null ? nothing : html`<span class="sep">·</span>${warning.more}`}</div>`;
  }

  #group(group: LimitGroup) {
    return html`<section class="grp" data-group="limits" data-harness=${group.harness}>
      <div class="gk">${group.name}${group.read === null ? nothing : html`<small>${group.read}</small>`}</div>
      <div class="gl">${group.rows.map((row) => this.#limit(row))}</div>
    </section>`;
  }

  /** One window's row, closed. */
  #limit(row: LimitRow) {
    if (row.state === "reset") {
      return html`<div class="lim reset" data-limit=${row.key} data-state="reset">${row.label}${
        row.resets === null ? nothing : html`<span class="sep">·</span>${row.resets}`}</div>`;
    }
    const meter = row.meter;
    const when = [row.fills === null ? null : html`<b>${row.fills}</b>`, row.resets]
      .filter((part) => part !== null);
    return html`<div class="lim" data-limit=${row.key} data-state=${row.state}>
      <span>${row.label}</span>
      ${meter === null ? html`<span></span>` : html`<svg class="m" role="img" aria-label=${meter.title}
        viewBox="0 0 ${METER.width} ${METER.height}">${svg`<title>${meter.title}</title>
        <rect class="base" x="0" y="4" width=${METER.width} height="1"></rect>
        <rect class="fill" x="0" y="3" width=${METER.width * meter.fill / 100} height="3"></rect>
        ${meter.tick === null ? nothing : svg`<rect class="tick" x=${Math.min(METER.width - 1, METER.width * meter.tick / 100)}
          y="0" width="1" height=${METER.height}></rect>`}`}</svg>`}
      <span class="u">${row.used ?? nothing}</span>
      <span class="w"><span>${when.map((part, i) => html`${i ? ", " : nothing}${part}`)}${
        row.read === null ? nothing : html`${when.length ? html`<span class="sep">·</span>` : nothing}${row.read}`}</span></span>
    </div>`;
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
          <span>${cost.caption} ${this.#link("terms", cost.prices)}</span>
          ${cost.read === null ? nothing : html`<span data-part="read">${cost.read}</span>`}
        </div>
      </div>
    </section>`;
  }

  /** A link to one of the page's routes. */
  #link(route: string, label: string) {
    return html`<a href=${`/m/usage/${route}`} @click=${(event: MouseEvent) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      navigate("usage", route);
    }}>${label}</a>`;
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
