// <moxi-ui-gateway>: the modules view's Gateway section, at the desk. It pairs a phone,
// drawing the pairing URL the shell mints as a QR code with the URL as text, lists the
// paired devices with each one's sign-ins and a revoke, and shows the audit log's last rows.
// Its routes are desk-only: the phone never reaches them, so the view leaves this section
// out there.

import { css, html, LitElement, nothing } from "lit";
import { api } from "/m/ui/client.js";

import { encode, svgPath } from "./qr.ts";

const TAG = "moxi-ui-gateway";
/** A pairing code's life, as the shell mints it. */
const PAIR_MS = 120_000;
/** How often the device list is read while a code is shown, to see the phone arrive. */
const WATCH_MS = 3_000;

/** One paired device, as `api/gateway/devices` lists it. */
export interface DeviceRow {
  id: string;
  name: string;
  created: number;
  used: number;
  passkey: boolean;
  stale: boolean;
  host: string;
  provider: string;
}

/** One audit row, as `api/gateway/log` lists it: `detail` is its other fields. */
export interface LogRow {
  at: string;
  kind: string;
  device: string | null;
  detail: string;
}

interface Code {
  url: string;
  path: string;
  size: number;
  until: number;
}

function when(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "never";
  return new Date(seconds * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

/** An audit row's UTC time, in this browser's own. */
function stamp(at: string): string {
  const time = Date.parse(at);
  if (!Number.isFinite(time)) return at;
  return new Date(time).toLocaleString([], { dateStyle: "medium", timeStyle: "medium" });
}

function said(status: number, data: any, what: string): string {
  if (status === 0) return `No answer from the shell, so ${what} failed.`;
  if (status === 403) return "This browser is signed out.";
  const text = data?.refused ?? data?.conflict ?? data?.error;
  return typeof text === "string" && text ? text : `The shell answered ${status} for ${what}.`;
}

export class UiGateway extends LitElement {
  static styles = css`
    :host { display: block; min-width: 0; }
    * { box-sizing: border-box; }
    h2 { margin: 0 0 var(--moxi-gap); font-size: 1.1em; }
    p { margin: 0; overflow-wrap: anywhere; }
    code { font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
    .card { display: grid; gap: var(--moxi-gap); padding: 10px 12px; border: 1px solid var(--moxi-border);
            border-radius: var(--moxi-radius-lg); background: var(--moxi-bg-raised); min-width: 0; }
    .dim { color: var(--moxi-text-dim); }
    .small { font-size: var(--moxi-font-size-sm); }
    .warn { color: var(--moxi-warn); }
    .fail { color: var(--moxi-error); }
    .actions { display: flex; flex-wrap: wrap; gap: var(--moxi-gap); align-items: center; }
    button { padding: 2px 10px; border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius);
             background: var(--moxi-bg); color: var(--moxi-text); font: inherit; cursor: pointer; }
    button:hover:not(:disabled) { background: var(--moxi-bg-hover); }
    button:disabled { cursor: progress; }
    :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
    .qr { display: grid; gap: 6px; justify-items: start; }
    .qr svg { width: min(100%, 18rem); height: auto; display: block; }
    ul { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; }
    li { display: grid; gap: 2px; padding: 4px 8px; border-left: 3px solid var(--moxi-border); }
    li.stale { border-left-color: var(--moxi-warn); }
    .head { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--moxi-gap); }
    .head .grow { flex: 1; }
    details.log summary { cursor: pointer; }
    details.log ul { margin-top: 6px; gap: 2px; }
    details.log li { display: block; padding: 0; border-left: none; }
  `;

  static properties = {
    devices: { state: true },
    listError: { state: true },
    code: { state: true },
    note: { state: true },
    busy: { state: true },
    rows: { state: true },
    signins: { state: true },
    logError: { state: true },
  };

  devices: DeviceRow[] | null = null;
  listError: string | null = null;
  code: Code | null = null;
  note: { text: string; tone: string } | null = null;
  busy = false;
  rows: LogRow[] | null = null;
  signins = new Map<string, string[]>();
  logError: string | null = null;
  #timer: ReturnType<typeof setInterval> | null = null;
  #known = new Set<string>();

  connectedCallback(): void {
    super.connectedCallback();
    void this.#read();
    void this.#readLog();
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#stopWatching();
  }

  render() {
    const devices = this.devices ?? [];
    return html`<section class="card" data-section="gateway">
      <h2>Gateway</h2>
      <p class="dim small">Pair a phone to use Moxi from it through the tunnel. The phone scans
        the code with its camera, then makes a passkey.</p>
      <div class="actions">
        <button type="button" ?disabled=${this.busy} @click=${() => this.#pair()}>
          ${this.code ? "New pairing code" : "Pair a phone"}</button>
      </div>
      ${this.code ? this.#qr(this.code) : nothing}
      ${this.note ? html`<p class="small ${this.note.tone}" role="status">${this.note.text}</p>` : nothing}
      ${this.listError ? html`<p class="small fail">${this.listError}</p>` : nothing}
      ${this.devices !== null && devices.length === 0 ? html`<p class="dim small">No phone is paired.</p>` : nothing}
      ${devices.length ? html`<ul>${devices.map((row) => this.#device(row))}</ul>` : nothing}
      ${this.#log()}
    </section>`;
  }

  #log() {
    if (this.logError) return html`<p class="small fail">${this.logError}</p>`;
    if (this.rows === null) return nothing;
    return html`<details class="log" @toggle=${(e: Event) => {
      if ((e.target as HTMLDetailsElement).open) void this.#readLog();
    }}>
      <summary class="small">Activity: the audit log's last ${this.rows.length} rows, from today and yesterday</summary>
      ${this.rows.length ? html`<ul class="small">${this.rows.map((row) => html`<li>
          <span class="dim">${stamp(row.at)}</span> <strong>${row.kind}</strong> <code>${row.detail}</code></li>`)}</ul>`
        : html`<p class="dim small">Nothing yet.</p>`}
    </details>`;
  }

  #qr(code: Code) {
    const side = code.size + 8;
    return html`<div class="qr">
      ${code.size ? html`<svg viewBox="-4 -4 ${side} ${side}" role="img" aria-label="QR code of the pairing link"
          shape-rendering="crispEdges">
        <rect x="-4" y="-4" width=${side} height=${side} fill="#ffffff"></rect><!-- colour-fixed: scanners need dark on light -->
        <path d=${code.path} fill="#000000"></path><!-- colour-fixed: scanners need dark on light --></svg>` : nothing}
      <p class="small"><code>${code.url}</code></p>
      <p class="dim small">It works once, for two minutes.</p>
    </div>`;
  }

  #device(row: DeviceRow) {
    return html`<li class=${row.stale ? "stale" : ""} data-device=${row.id}>
      <div class="head"><strong>${row.name}</strong><span class="grow"></span>
        <button type="button" ?disabled=${this.busy} @click=${() => this.#revoke(row)}>Revoke</button></div>
      <p class="small dim">Paired ${when(row.created)}; last signed in ${when(row.used)}.</p>
      ${this.#signins(row.id)}
      ${row.passkey ? nothing
        : html`<p class="small dim">No passkey: it signs in again only by a new QR pairing.</p>`}
      ${row.stale ? html`<p class="small warn">The gateway's address changed since it paired, so it
        signs in to nothing. Pair it again, then revoke this entry.</p>` : nothing}
    </li>`;
  }

  #signins(id: string) {
    const times = this.signins.get(id) ?? [];
    if (!times.length) return nothing;
    return html`<p class="small dim">Sign-ins: ${times.map(stamp).join(", ")}.</p>`;
  }

  async #readLog(): Promise<void> {
    const answer = await api("ui", "api/gateway/log");
    if (answer.status === 200 && Array.isArray(answer.data?.rows) && Array.isArray(answer.data?.signins)) {
      this.rows = answer.data.rows;
      this.signins = new Map(answer.data.signins.map((entry: { device: string; at: string[] }) => [entry.device, entry.at]));
      this.logError = null;
    } else {
      this.logError = said(answer.status, answer.data, "reading the audit log");
    }
  }

  async #read(): Promise<void> {
    const answer = await api("ui", "api/gateway/devices");
    if (answer.status === 200 && Array.isArray(answer.data?.devices)) {
      const rows: DeviceRow[] = answer.data.devices;
      if (this.code !== null) {
        const fresh = rows.find((row) => !this.#known.has(row.id));
        if (fresh !== undefined) {
          this.code = null;
          this.#stopWatching();
          this.note = { text: `Paired: ${fresh.name}.`, tone: "" };
        }
      }
      this.devices = rows;
      this.listError = null;
    } else {
      this.listError = said(answer.status, answer.data, "reading the devices");
    }
  }

  async #pair(): Promise<void> {
    this.busy = true;
    this.note = null;
    const answer = await api("ui", "api/gateway/pair", {});
    this.busy = false;
    if (answer.status !== 200 || typeof answer.data?.url !== "string") {
      this.code = null;
      this.#stopWatching();
      this.note = { text: said(answer.status, answer.data, "pairing"), tone: "fail" };
      return;
    }
    const url: string = answer.data.url;
    let qr;
    try {
      qr = encode(url);
    } catch {
      this.note = { text: "The pairing link is too long for a QR code; open it on the phone by hand.", tone: "warn" };
      this.code = { url, path: "", size: 0, until: Date.now() + PAIR_MS };
      return;
    }
    this.#known = new Set((this.devices ?? []).map((row) => row.id));
    this.code = { url, path: svgPath(qr), size: qr.size, until: Date.now() + PAIR_MS };
    this.#stopWatching();
    this.#timer = setInterval(() => {
      if (this.code === null || Date.now() > this.code.until) {
        if (this.code !== null) this.note = { text: "That pairing code has expired.", tone: "dim" };
        this.code = null;
        this.#stopWatching();
        return;
      }
      void this.#read();
    }, WATCH_MS);
  }

  async #revoke(row: DeviceRow): Promise<void> {
    this.busy = true;
    const answer = await api("ui", "api/gateway/revoke", { id: row.id });
    this.busy = false;
    if (answer.status === 200) {
      this.note = {
        text: row.passkey
          ? `Revoked ${row.name}. Its passkey stays on the phone until you remove it in the phone's settings.`
          : `Revoked ${row.name}.`,
        tone: "",
      };
    } else {
      this.note = { text: said(answer.status, answer.data, "revoking"), tone: "fail" };
    }
    await this.#read();
    await this.#readLog();
  }

  #stopWatching(): void {
    if (this.#timer !== null) clearInterval(this.#timer);
    this.#timer = null;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, UiGateway);
