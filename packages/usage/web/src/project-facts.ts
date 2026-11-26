// <moxi-usage-project-facts projects="[...]" open="key">: usage's facts for the Projects
// page. It draws nothing; it reports, in a bubbling, composed `moxi-project-facts` event,
// what `factsReport` makes of usage's cost by project: each project's cost today, the
// opened project's `Cost` row and `Stays` line, and the cost of folders in no project.
//
// It reads `/api/projects` from today and from 7 days back when it is connected and every
// minute after while the document shows, and from 30 days back too while a project is
// open; nothing of another module's. While the document is hidden it reads nothing; it
// reads once when the document shows again. A read that fails drops what it fed; with all
// of them failing it reports null, and the next good read puts the facts back. It reports
// only when what it would say changed, and always once after its first reads since it was
// connected.

import { api } from "/m/ui/client.js";

import { FACTS_EVENT } from "./facts-model.ts";
import { FactsReads } from "./facts-reads.ts";

const TAG = "moxi-usage-project-facts";

export class UsageProjectFacts extends HTMLElement {
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
    const reads = new FactsReads((path) => api("usage", path), (detail) => {
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

if (!customElements.get(TAG)) customElements.define(TAG, UsageProjectFacts);
