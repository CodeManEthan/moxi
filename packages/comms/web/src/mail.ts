// <moxi-comms-mail>: comms' row on Home. It says on one line how many messages wait for
// names no live session holds, and whose they are, from `/api/mail`, and reports that line
// to Home with a `moxi-home-row` event. It draws nothing itself: Home draws the row, as a
// link to the comms page.
//
// It reads the mail when it is connected, when the document is shown again, and every
// 30 s while the document is visible, at the desk and on the phone alike. A read that
// fails keeps the last line; an answer saying the daemon is offline or erroring reports
// no row.

import { api } from "/m/ui/client.js";

import { mailLine, parseMail } from "./mail-model.ts";

const TAG = "moxi-comms-mail";
/** How often the mail is read while the document is visible. */
const READ_MS = 30_000;

export class CommsMail extends HTMLElement {
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
      const answer = await api("comms", "api/mail");
      if (answer.status !== 200 || !this.isConnected) return;
      const value = parseMail(answer.data);
      if (value !== null) this.#report(mailLine(value, Date.now() / 1000));
    } finally {
      this.#reading = false;
    }
  }

  /** Tells Home the row's line when it changed. */
  #report(line: ReturnType<typeof mailLine>): void {
    const said = JSON.stringify(line);
    if (said === this.#said) return;
    this.#said = said;
    this.dispatchEvent(new CustomEvent("moxi-home-row", { detail: line, bubbles: true, composed: true }));
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, CommsMail);
