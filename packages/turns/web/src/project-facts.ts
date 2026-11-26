// <moxi-turns-project-facts projects="[...]" open="key">: turns' facts for the Projects
// page. It draws nothing; it reports, in a bubbling, composed `moxi-project-facts` event,
// what `factsReport` makes of `/api/sessions`: by session claim, whether each session turns
// can read is in a turn. It names no project, so the attributes the page gives it change
// nothing it says.
//
// It reads `/api/sessions` when it is connected, then follows turns' topic `sessions` and
// reads again every 30 seconds. It reads nothing of another module's. While the document
// is hidden its timer and topic stop; it reads once when the document shows again. A read
// that fails, is refused or finds turns gone reports null; the next good read puts the
// facts back. It reports only when what it would say changed, and always once after its
// first read since it was connected.

import { api, subscribe } from "/m/ui/client.js";

import { FACTS_EVENT, factsReport, goodValue, READ_MS } from "./facts-model.ts";
import { Feed, parseList } from "./feed.ts";
import type { SessionList } from "./feed.ts";

const TAG = "moxi-turns-project-facts";

export class TurnsProjectFacts extends HTMLElement {
  #feed: Feed<SessionList> | null = null;
  #timer: ReturnType<typeof setInterval> | null = null;
  #stop: (() => void) | null = null;
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
    this.#feed = new Feed<SessionList>(() => api("turns", "api/sessions"), parseList, () => this.#report());
    document.addEventListener("visibilitychange", this.#shown);
    if (document.visibilityState === "visible") this.#follow();
  }

  disconnectedCallback(): void {
    document.removeEventListener("visibilitychange", this.#shown);
    this.#pause();
    this.#feed?.close();
    this.#feed = null;
  }

  /** Reads once, then follows the topic and the timer. */
  #follow(): void {
    const feed = this.#feed;
    if (feed === null) return;
    void feed.refresh().then(() => {
      if (this.#feed === feed && this.#stop === null && document.visibilityState === "visible") {
        this.#stop = subscribe("turns", "sessions", (data) => feed.topic(data));
      }
    });
    if (this.#timer === null) this.#timer = setInterval(() => void this.#feed?.refresh(), READ_MS);
  }

  #pause(): void {
    if (this.#timer !== null) clearInterval(this.#timer);
    this.#timer = null;
    this.#stop?.();
    this.#stop = null;
  }

  #report(): void {
    const feed = this.#feed;
    if (feed === null || feed.loading) return;
    const detail = factsReport(goodValue(feed));
    const text = JSON.stringify(detail);
    if (text === this.#last) return;
    this.#last = text;
    this.dispatchEvent(new CustomEvent(FACTS_EVENT, { detail, bubbles: true, composed: true }));
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, TurnsProjectFacts);
