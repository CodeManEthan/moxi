// <moxi-ui-phones>: the phones in the Phone group of Settings › ui, under `Phone access` and
// `Phone port`. Each paired phone with when it paired, its last sign-in and its passkey, and
// `Revoke`, which asks in place under the phone and acts at once: a revoke is an act, not a
// setting, so it waits for no Save. `Pair a phone`, while the gateway serves, opens the
// pairing code in place as a QR code with its words and `Copy the link instead`; the device
// list is read every 3 s while the code lives, and a new phone closes it. Last, tucked,
// `Activity`: the audit log's last rows and each phone's sign-ins.
//
// The Settings page gives it the gateway's state (`state`) and whether its address opens
// the pairing code (`pairing`); it asks the page to write that address with a `phone-pair`
// event, `detail.open` true or false. A code still live when the shell draws the page again
// is taken over by the element placed next.

import { css, html, LitElement, nothing } from "lit";
import type { PropertyValues } from "lit";
import { api } from "/m/ui/client.js";

import {
  ACTIVITY, arrived, COPY_LINK, deviceWords, EXPIRED, failedWords, NO_ACTIVITY, NO_PHONE, PAIR, PAIR_THEN,
  pairedWords, pairWords, REREAD_MS, revokeAsk, revokedWords, signinLines, TOO_LONG, WATCH_MS,
} from "./phone-model.ts";
import type { DeviceRow, GatewayState, LogRow } from "./phone-model.ts";
import { encode, svgPath } from "./qr.ts";

const TAG = "moxi-ui-phones";
/** How long `Copied` shows after the link is copied. */
const COPIED_MS = 2000;

/** A pairing code: its URL, its QR code's path and size (0 when the link is too long for
 * one), and when it ends, in epoch seconds. */
interface Code {
  url: string;
  path: string;
  size: number;
  until: number;
}

/** A code still live when the element was taken out, and the phones known when it was made. */
let kept: { code: Code; known: Set<string> } | null = null;

/** An audit row's UTC time, in this browser's own. */
function stamp(at: string): string {
  const time = Date.parse(at);
  if (!Number.isFinite(time)) return at;
  return new Date(time).toLocaleString([], { dateStyle: "medium", timeStyle: "medium" });
}

export class UiPhones extends LitElement {
  static styles = css`
    :host { display: block; min-width: 0; }
    * { box-sizing: border-box; }
    p { margin: 0; overflow-wrap: anywhere; }
    button { font: inherit; color: inherit; }
    :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
    .link { padding: 0; border: 0; background: none; color: var(--moxi-text-dim); cursor: pointer; }
    .link.x { color: var(--moxi-text); }
    .link:disabled { opacity: 0.6; cursor: not-allowed; }
    .dev { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline; column-gap: 18px;
           font-size: 14px; line-height: 20px; padding: 7px 0; }
    .dev .d { color: var(--moxi-text-dim); margin-left: 10px; }
    .dev > button { margin-left: auto; }
    .dim { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); padding: 0 0 9px; }
    .devc { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 8px 18px;
            font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); padding: 0 0 9px; }
    .devc .msg { flex: 1 1 240px; min-width: 0; }
    .devc b.w { font-weight: 600; color: var(--moxi-warn); }
    .devc .btns { display: flex; align-items: center; gap: 18px; flex: none; }
    .btn { font-weight: 600; font-size: 14px; line-height: 1; border: 1px solid var(--moxi-warn); border-radius: var(--moxi-radius);
           padding: 8px 16px; background: none; color: var(--moxi-warn); cursor: pointer; }
    .btn:disabled { opacity: 0.6; cursor: progress; }
    .pair { display: grid; grid-template-columns: 132px minmax(0, 1fr); gap: 18px; padding: 6px 0 12px; font-size: 13px;
            line-height: 18px; color: var(--moxi-text-dim); }
    .pair svg { display: block; width: 132px; height: 132px; }
    .pair p { margin-bottom: 8px; max-width: 330px; }
    .pair b { font-weight: 400; color: var(--moxi-text); }
    .pair .link { color: var(--moxi-text); }
    .pair.bare { grid-template-columns: minmax(0, 1fr); }
    .pair code { font-family: var(--moxi-font-mono); font-size: 12px; color: var(--moxi-text); overflow-wrap: anywhere; }
    .act { display: flex; flex-wrap: wrap; justify-content: space-between; column-gap: 18px; font-size: 13px; line-height: 18px;
           color: var(--moxi-text-dim); border-top: 1px solid var(--moxi-border); margin-top: 6px; padding: 11px 0 8px; }
    .log { display: grid; gap: 2px; margin: 0 0 8px; padding: 0; list-style: none; font-size: 12px; line-height: 16px;
           color: var(--moxi-text-dim); }
    .log b { font-weight: 400; color: var(--moxi-text); }
    .log code { font-family: var(--moxi-font-mono); overflow-wrap: anywhere; }
    @media (max-width: 720px) {
      .pair { grid-template-columns: minmax(0, 1fr); }
    }
  `;

  static properties = {
    state: { attribute: false },
    pairing: { type: Boolean },
  };

  /** The gateway's state, as the page last read it. */
  state: GatewayState | null = null;
  /** The page's address opens the pairing code. */
  pairing = false;

  #devices: DeviceRow[] | null = null;
  #devicesError: string | null = null;
  #code: Code | null = null;
  #known = new Set<string>();
  /** A pair was asked for and has had its answer; cleared when the address closes it. */
  #tried = false;
  #minting = false;
  /** Under `Pair a phone`: a failure, the code expired, a phone paired, the link too long. */
  #pairNote: { text: string; tone: "" | "fail" } | null = null;
  /** The phone whose Revoke asks, and whether the revoke runs. */
  #asking: string | null = null;
  #revoking = false;
  /** A revoke's failure, under the phone's ask. */
  #revokeError: string | null = null;
  /** A revoke done, in the phones' place. */
  #revoked: string | null = null;
  #activity = false;
  #rows: LogRow[] | null = null;
  #signins = new Map<string, string[]>();
  #logError: string | null = null;
  #copied = false;
  #copiedTimer: ReturnType<typeof setTimeout> | null = null;
  #watch: ReturnType<typeof setInterval> | null = null;
  #reread: ReturnType<typeof setInterval> | null = null;
  readonly #onVisible = (): void => {
    if (document.visibilityState === "visible") void this.#read();
  };

  connectedCallback(): void {
    super.connectedCallback();
    document.addEventListener("visibilitychange", this.#onVisible);
    if (kept !== null && kept.code.until * 1000 > Date.now()) {
      this.#code = kept.code;
      this.#known = kept.known;
      this.#tried = true;
      this.#startWatching();
    }
    kept = null;
    this.#reread = setInterval(() => void this.#read(), REREAD_MS);
    void this.#read();
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    document.removeEventListener("visibilitychange", this.#onVisible);
    if (this.#reread !== null) clearInterval(this.#reread);
    this.#reread = null;
    if (this.#copiedTimer !== null) clearTimeout(this.#copiedTimer);
    this.#stopWatching();
    kept = this.#code !== null && this.#code.until * 1000 > Date.now() ? { code: this.#code, known: this.#known } : null;
  }

  protected willUpdate(changed: PropertyValues): void {
    if (changed.has("pairing") && !this.pairing) {
      this.#tried = false;
      if (this.#code !== null) {
        this.#code = null;
        this.#stopWatching();
      }
    }
  }

  protected updated(changed: PropertyValues): void {
    super.updated(changed);
    if (this.pairing && this.#code === null && !this.#tried && !this.#minting && this.state?.on === true) {
      void this.#mint();
    }
  }

  render() {
    const devices = this.#devices ?? [];
    return html`<div data-part="phones">
      ${this.#devicesError !== null ? html`<p class="dim" data-line="devices-failed">${this.#devicesError}
          <button type="button" class="link x" @click=${() => void this.#read()}>Try again</button></p>`
        : this.#devices !== null && devices.length === 0 ? html`<p class="dim" data-line="no-phone">${NO_PHONE}</p>`
        : devices.map((row) => this.#device(row))}
      ${this.#revoked !== null ? html`<p class="dim" role="status" data-line="revoked">${this.#revoked}</p>` : nothing}
      ${this.state?.on === true ? this.#pair() : nothing}
      ${this.#activityPart()}
    </div>`;
  }

  #device(row: DeviceRow) {
    const asking = this.#asking === row.id;
    const ask = revokeAsk(row.name);
    return html`<div class="dev" data-device=${row.id}>
        <span><span data-line="name">${row.name}</span><span class="d" data-line="device">${deviceWords(row, new Date())}</span></span>
        <button type="button" class=${asking ? "link x" : "link"} data-act="revoke" ?disabled=${this.#revoking}
          @click=${() => this.#ask(row.id)}>Revoke</button>
      </div>
      ${asking ? html`<div class="devc" data-part="revoke" data-device=${row.id}>
          <span class="msg"><b class="w">${ask.ask}</b> ${ask.why}
            ${this.#revokeError !== null ? html`<span data-line="revoke-failed">${this.#revokeError}</span>` : nothing}</span>
          <span class="btns"><button type="button" class="link x" ?disabled=${this.#revoking}
              @click=${() => this.#keep()}>${ask.keep}</button>
            <button type="button" class="btn" ?disabled=${this.#revoking} @click=${() => void this.#revoke(row)}
              >${ask.button}</button></span>
        </div>` : nothing}`;
  }

  #pair() {
    const code = this.#code;
    const note = this.#pairNote !== null
      ? html`<p class="dim" role="status" data-line="pair-note">${this.#pairNote.text}</p>` : nothing;
    if (code === null) {
      return html`<div class="dev"><button type="button" class="link x" data-act="pair" ?disabled=${this.#minting}
          @click=${() => this.#openPair()}>${PAIR}</button><span></span></div>${note}`;
    }
    const words = pairWords(code.until, this.state?.exposure ?? null);
    const side = code.size + 8;
    return html`<div class="dev"><span>${PAIR}</span>
        <button type="button" class="link" data-act="close" @click=${() => this.#close(null)}>Close</button></div>
      <div class=${code.size ? "pair" : "pair bare"} data-part="pair">
        ${code.size ? html`<svg viewBox="-4 -4 ${side} ${side}" role="img" aria-label="Pairing code"
            shape-rendering="crispEdges">
          <rect x="-4" y="-4" width=${side} height=${side} fill="#ffffff"></rect><!-- colour-fixed: scanners need dark on light -->
          <path d=${code.path} fill="#000000"></path><!-- colour-fixed: scanners need dark on light --></svg>` : nothing}
        <div>
          ${code.size ? nothing : html`<p data-line="too-long">${TOO_LONG}</p><p><code>${code.url}</code></p>`}
          <p data-line="scan"><b>${words.lead}</b> ${words.rest}</p>
          <p data-line="then">${PAIR_THEN}</p>
          <p>${this.#copied ? html`<span data-line="copied">Copied</span>`
            : html`<button type="button" class="link" data-act="copy-link" @click=${() => this.#copyLink(code.url)}
              >${COPY_LINK}</button>`}</p>
        </div>
      </div>${note}`;
  }

  #activityPart() {
    return html`<div class="act" data-part="activity"><span>${ACTIVITY}</span>
        <button type="button" class="link" aria-expanded=${String(this.#activity)} @click=${() => this.#toggleActivity()}
          >${this.#activity ? "Hide" : "Show"}</button></div>
      ${this.#logError !== null ? html`<p class="dim" data-line="log-failed">${this.#logError}</p>` : nothing}
      ${this.#activity && this.#logError === null && this.#rows !== null ? this.#log(this.#rows) : nothing}`;
  }

  #log(rows: LogRow[]) {
    const signins = signinLines(this.#signins, this.#devices ?? [], stamp);
    if (rows.length === 0 && signins.length === 0) return html`<p class="dim" data-line="no-activity">${NO_ACTIVITY}</p>`;
    return html`<ul class="log" data-part="log">
      ${signins.map((line) => html`<li data-line="signins">${line}</li>`)}
      ${rows.map((row) => html`<li data-line="row">${stamp(row.at)} <b>${row.kind}</b> <code>${row.detail}</code></li>`)}
    </ul>`;
  }

  // --- reading ----------------------------------------------------------------------------

  async #read(): Promise<void> {
    const answer = await api("ui", "api/gateway/devices");
    if (answer.status === 200 && Array.isArray(answer.data?.devices)) {
      const rows: DeviceRow[] = answer.data.devices;
      if (this.#code !== null) {
        const fresh = arrived(rows, this.#known);
        if (fresh !== null) this.#close(pairedWords(fresh.name));
      }
      this.#devices = rows;
      this.#devicesError = null;
    } else {
      this.#devicesError = failedWords(answer.status, answer.data, "reading the phones");
    }
    this.requestUpdate();
  }

  async #readLog(): Promise<void> {
    const answer = await api("ui", "api/gateway/log");
    if (answer.status === 200 && Array.isArray(answer.data?.rows) && Array.isArray(answer.data?.signins)) {
      this.#rows = answer.data.rows;
      this.#signins = new Map(answer.data.signins.map((entry: { device: string; at: string[] }) => [entry.device, entry.at]));
      this.#logError = null;
    } else {
      this.#logError = failedWords(answer.status, answer.data, "reading the activity");
    }
    this.requestUpdate();
  }

  #toggleActivity(): void {
    this.#activity = !this.#activity;
    if (!this.#activity) this.#logError = null;
    this.requestUpdate();
    if (this.#activity) void this.#readLog();
  }

  // --- pairing ------------------------------------------------------------------------------

  #openPair(): void {
    this.#pairNote = null;
    this.#tried = false;
    this.requestUpdate();
    this.#tell(true);
  }

  /** Mints a code; a failure says why under the button and closes the address. */
  async #mint(): Promise<void> {
    this.#tried = true;
    this.#minting = true;
    this.#pairNote = null;
    this.requestUpdate();
    const answer = await api("ui", "api/gateway/pair", {});
    this.#minting = false;
    const url = answer.data?.url;
    if (answer.status !== 200 || typeof url !== "string") {
      this.#pairNote = { text: failedWords(answer.status, answer.data, "pairing"), tone: "fail" };
      this.requestUpdate();
      this.#tell(false);
      return;
    }
    if (!this.pairing) return;
    const until = typeof answer.data?.until === "number" ? answer.data.until : Date.now() / 1000 + 120;
    let path = "";
    let size = 0;
    try {
      const qr = encode(url);
      path = svgPath(qr);
      size = qr.size;
    } catch {
      // Too long for a QR code: the link shows as text, with what to do with it.
    }
    this.#known = new Set((this.#devices ?? []).map((row) => row.id));
    this.#code = { url, path, size, until };
    this.#startWatching();
    this.requestUpdate();
  }

  /** Closes the code, with `note` under `Pair a phone` when there is one to say. */
  #close(note: string | null): void {
    this.#code = null;
    this.#stopWatching();
    this.#pairNote = note !== null ? { text: note, tone: "" } : null;
    this.requestUpdate();
    this.#tell(false);
  }

  #tell(open: boolean): void {
    this.dispatchEvent(new CustomEvent("phone-pair", { detail: { open } }));
  }

  #startWatching(): void {
    this.#stopWatching();
    this.#watch = setInterval(() => {
      const code = this.#code;
      if (code === null) {
        this.#stopWatching();
      } else if (Date.now() > code.until * 1000) {
        this.#close(EXPIRED);
      } else {
        void this.#read();
      }
    }, WATCH_MS);
  }

  #stopWatching(): void {
    if (this.#watch !== null) clearInterval(this.#watch);
    this.#watch = null;
  }

  #copyLink(url: string): void {
    void navigator.clipboard?.writeText(url).catch(() => undefined);
    this.#copied = true;
    if (this.#copiedTimer !== null) clearTimeout(this.#copiedTimer);
    this.#copiedTimer = setTimeout(() => {
      this.#copiedTimer = null;
      this.#copied = false;
      this.requestUpdate();
    }, COPIED_MS);
    this.requestUpdate();
  }

  // --- revoking -----------------------------------------------------------------------------

  #ask(id: string): void {
    this.#asking = id;
    this.#revokeError = null;
    this.#revoked = null;
    this.requestUpdate();
  }

  #keep(): void {
    this.#asking = null;
    this.#revokeError = null;
    this.requestUpdate();
  }

  async #revoke(row: DeviceRow): Promise<void> {
    this.#revoking = true;
    this.#revokeError = null;
    this.requestUpdate();
    const answer = await api("ui", "api/gateway/revoke", { id: row.id });
    this.#revoking = false;
    if (answer.status === 200) {
      this.#asking = null;
      this.#revoked = revokedWords(row);
    } else {
      this.#revokeError = failedWords(answer.status, answer.data, "revoking");
    }
    this.requestUpdate();
    await this.#read();
    if (this.#activity) await this.#readLog();
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, UiPhones);
