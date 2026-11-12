// <moxi-usage-row>: usage's row on Home. It says on one line how full the plan windows of
// the harness read last are and what today cost, from `/api/summary`, and reports that
// line to Home with a `moxi-home-row` event. It draws nothing itself: Home draws the row,
// as a link to the usage page.
//
// It reads the summary when it is connected, when the document is shown again, and every
// 30 s while the document is visible, at the desk and on the phone alike. A read that
// fails keeps the last line.

import { api } from "/m/ui/client.js";

import { parseSummary, usageLine } from "./row-model.ts";

const TAG = "moxi-usage-row";
/** How often the summary is read while the document is visible. */
const READ_MS = 30_000;

export class UsageRow extends HTMLElement {
  #timer: ReturnType<typeof setInterval> | undefined;
  #reading = false;
  /** The last line reported, as JSON; null before the first. */
  #said: string | null = null;
  #shown = (): void => {
    if (document.visibilityState === "visible") void this.#read();
  };

  connectedCallback(): void {
    document.addEventListener("visibilitychange", this.#shown);
    this.#timer = setInterval(this.#shown, READ_MS);
    void this.#read();
  }

  disconnectedCallback(): void {
    document.removeEventListener("visibilitychange", this.#shown);
    clearInterval(this.#timer);
    this.#said = null;
  }

  async #read(): Promise<void> {
    if (this.#reading) return;
    this.#reading = true;
    try {
      const answer = await api("usage", "api/summary");
      if (answer.status !== 200 || !this.isConnected) return;
      const value = parseSummary(answer.data);
      if (value !== null) this.#report(usageLine(value));
    } finally {
      this.#reading = false;
    }
  }

  /** Tells Home the row's line when it changed. */
  #report(line: ReturnType<typeof usageLine>): void {
    const said = JSON.stringify(line);
    if (said === this.#said) return;
    this.#said = said;
    this.dispatchEvent(new CustomEvent("moxi-home-row", { detail: line, bubbles: true, composed: true }));
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, UsageRow);
