// <moxi-loadout-project-facts projects="[...]" open="key">: loadout's facts for the
// Projects page. It draws nothing; it reports, in a bubbling, composed
// `moxi-project-facts` event, the opened project's `Loadout` row as `factsReport` makes it.
//
// It reads `/api/list` when it is connected and every minute after while the document
// shows, and nothing of another module's. While the document is hidden it reads nothing;
// it reads once when the document shows again. A read that fails, a 404, or a list loadout
// can't read reports null; the next good read puts the facts back. It reports only when
// what it would say changed, and always once after its first read since it was connected.

import { api } from "/m/ui/client.js";

import { FACTS_EVENT } from "./facts-model.ts";
import { FactsReads } from "./facts-reads.ts";

const TAG = "moxi-loadout-project-facts";

export class LoadoutProjectFacts extends HTMLElement {
  static observedAttributes = ["projects", "open"];

  #reads: FactsReads | null = null;
  #shown = (): void => {
    if (document.visibilityState === "visible") void this.#reads?.start();
    else this.#reads?.pause();
  };

  connectedCallback(): void {
    if (this.shadowRoot === null) {
      // A constructed sheet, which the shell's content policy lets in where a <style>
      // element is refused: the element takes no room.
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(":host { display: none; }");
      this.attachShadow({ mode: "open" }).adoptedStyleSheets = [sheet];
    }
    const reads = new FactsReads(() => api("loadout", "api/list"), (detail) => {
      if (this.#reads === reads) {
        this.dispatchEvent(new CustomEvent(FACTS_EVENT, { detail, bubbles: true, composed: true }));
      }
    });
    reads.setGiven(this.getAttribute("projects"), this.getAttribute("open"));
    this.#reads = reads;
    document.addEventListener("visibilitychange", this.#shown);
    if (document.visibilityState === "visible") void reads.start();
  }

  disconnectedCallback(): void {
    document.removeEventListener("visibilitychange", this.#shown);
    this.#reads?.close();
    this.#reads = null;
  }

  attributeChangedCallback(): void {
    this.#reads?.setGiven(this.getAttribute("projects"), this.getAttribute("open"));
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, LoadoutProjectFacts);
