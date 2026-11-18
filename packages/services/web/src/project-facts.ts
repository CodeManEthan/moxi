// <moxi-services-project-facts projects="[...]" open="key">: services' facts for the
// Projects page. It draws nothing; it reports, in a bubbling, composed
// `moxi-project-facts` event, what `factsReport` makes of services' rows for the projects
// it was given, and the `Services` row of the one in `open`.
//
// It reads `/api/rows` when it is connected, then follows the topic `rows`, which carries
// the same value each time it changes, and reads nothing of another module's. While the
// document is hidden it follows nothing; it reads once when the document shows again. A
// read that fails, a 404, or an answer whose `state` isn't `up` reports null; the next
// good read puts the facts back. Once a minute while shown it words its times again, so
// `18:00` turns `yesterday 18:00` after midnight. It reports only when what it would say
// changed, and always once after its first read since it was connected.

import { api, subscribe } from "/m/ui/client.js";

import { FACTS_EVENT, factsReport, parseGiven } from "./facts.ts";
import { Feed, parseRows } from "./feed.ts";
import type { Rows } from "./feed.ts";

const TAG = "moxi-services-project-facts";
/** How often the times are worded again while the document shows, in milliseconds. */
const REWORD_MS = 60_000;

export class ServicesProjectFacts extends HTMLElement {
  static observedAttributes = ["projects", "open"];

  #feed: Feed<Rows> | null = null;
  #stop: (() => void) | null = null;
  #timer: ReturnType<typeof setInterval> | null = null;
  /** The last report sent, as JSON; undefined until the first read since connecting. */
  #last: string | undefined = undefined;
  #shown = (): void => {
    if (document.visibilityState === "visible") this.#follow();
    else this.#pause();
  };

  connectedCallback(): void {
    if (this.shadowRoot === null) {
      // A constructed sheet, which the shell's content policy lets in where a <style>
      // element is refused: the element takes no room.
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(":host { display: none; }");
      this.attachShadow({ mode: "open" }).adoptedStyleSheets = [sheet];
    }
    this.#last = undefined;
    this.#feed = new Feed<Rows>(() => api("services", "api/rows"), parseRows, () => this.#report());
    document.addEventListener("visibilitychange", this.#shown);
    if (document.visibilityState === "visible") this.#follow();
  }

  disconnectedCallback(): void {
    document.removeEventListener("visibilitychange", this.#shown);
    this.#pause();
    this.#feed?.close();
    this.#feed = null;
  }

  attributeChangedCallback(): void {
    if (this.isConnected && this.#last !== undefined) this.#report();
  }

  /** Reads once, then follows the topic and words the times again on a timer. */
  #follow(): void {
    const feed = this.#feed;
    if (feed === null) return;
    void feed.refresh().then(() => {
      if (this.#feed === feed && this.#stop === null && document.visibilityState === "visible") {
        this.#stop = subscribe("services", "rows", (data) => feed.topic(data));
      }
    });
    if (this.#timer === null) this.#timer = setInterval(() => this.#report(), REWORD_MS);
  }

  #pause(): void {
    this.#stop?.();
    this.#stop = null;
    if (this.#timer !== null) clearInterval(this.#timer);
    this.#timer = null;
  }

  #report(): void {
    const feed = this.#feed;
    if (feed === null || feed.loading) return;
    const good = feed.failure === null && !feed.gone ? feed.value : null;
    const detail = factsReport(good, parseGiven(this.getAttribute("projects")), this.getAttribute("open"),
                               { now: Date.now() / 1000 });
    const text = JSON.stringify(detail);
    if (text === this.#last) return;
    this.#last = text;
    this.dispatchEvent(new CustomEvent(FACTS_EVENT, { detail, bubbles: true, composed: true }));
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, ServicesProjectFacts);
