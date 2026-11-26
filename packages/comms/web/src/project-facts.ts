// <moxi-comms-project-facts projects="[...]" open="key">: comms' facts for the Projects
// page. It draws nothing; it reports, in a bubbling, composed `moxi-project-facts` event,
// which of the opened project's agents no session holds now, as `factsReport` makes it.
//
// It reads `/api/roster` when it is connected, then follows the topic `roster`, and reads
// nothing of another module's. While the document is hidden it follows nothing; it reads
// once when the document shows again. A read that fails, a 404, or a roster comms can't say
// (its daemon down, or an error) reports null; the next good read puts the facts back. It
// reports only when what it would say changed, and always once after its first read since
// it was connected.

import { api, subscribe } from "/m/ui/client.js";

import { FACTS_EVENT } from "./facts-model.ts";
import { FactsReads } from "./facts-reads.ts";

const TAG = "moxi-comms-project-facts";

export class CommsProjectFacts extends HTMLElement {
  static observedAttributes = ["projects", "open"];

  #reads: FactsReads | null = null;
  #onVisibility = (): void => {
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
    const reads = new FactsReads(() => api("comms", "api/roster"), (topic, listen) => subscribe("comms", topic, listen),
                                 (detail) => {
                                   if (this.#reads === reads) {
                                     this.dispatchEvent(new CustomEvent(FACTS_EVENT, { detail, bubbles: true, composed: true }));
                                   }
                                 });
    reads.setGiven(this.getAttribute("projects"), this.getAttribute("open"));
    this.#reads = reads;
    document.addEventListener("visibilitychange", this.#onVisibility);
    if (document.visibilityState === "visible") void reads.start();
  }

  disconnectedCallback(): void {
    document.removeEventListener("visibilitychange", this.#onVisibility);
    this.#reads?.close();
    this.#reads = null;
  }

  attributeChangedCallback(): void {
    this.#reads?.setGiven(this.getAttribute("projects"), this.getAttribute("open"));
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, CommsProjectFacts);
