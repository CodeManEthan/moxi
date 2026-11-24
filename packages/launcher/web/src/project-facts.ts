// <moxi-launcher-project-facts projects="[...]" open="key">: the launcher's facts for the
// Projects page. It draws nothing; it reports, in a bubbling, composed `moxi-project-facts`
// event, what `factsReport` makes of the launcher's routes for the projects it was given
// and the one in `open`.
//
// Its reads are `FactsReads`': they run while it is connected and the document shows,
// pause while the document is hidden, and read once when it shows again. It reads nothing
// of another module's. It reports only when what it would say changed, and always once
// after its first read since it was connected.

import { api, subscribe } from "/m/ui/client.js";

import { FACTS_EVENT, factsReport, parseGiven } from "./facts-model.ts";
import { FactsReads } from "./facts-reads.ts";

const TAG = "moxi-launcher-project-facts";

export class LauncherProjectFacts extends HTMLElement {
  static observedAttributes = ["projects", "open"];

  #reads: FactsReads | null = null;
  /** The last report sent, as JSON; undefined until the first read since connecting. */
  #last: string | undefined = undefined;
  #shown = (): void => {
    if (document.visibilityState === "visible") this.#reads?.start();
    else this.#reads?.pause();
  };

  connectedCallback(): void {
    if (this.shadowRoot === null) {
      // A constructed sheet, which the shell's content policy lets in where a <style>
      // element is refused: the element takes no room. The root holds an empty slot,
      // since it draws nothing.
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(":host { display: none; }");
      const root = this.attachShadow({ mode: "open" });
      root.adoptedStyleSheets = [sheet];
      root.append(document.createElement("slot"));
    }
    this.#last = undefined;
    this.#reads = new FactsReads({
      read: (path) => api("launcher", path),
      subscribe: (topic, fn) => subscribe("launcher", topic, fn),
      every: (ms, fn) => {
        const timer = setInterval(fn, ms);
        return () => clearInterval(timer);
      },
    }, () => this.#report());
    this.#reads.setOpen(this.getAttribute("open"));
    document.addEventListener("visibilitychange", this.#shown);
    if (document.visibilityState === "visible") this.#reads.start();
  }

  disconnectedCallback(): void {
    document.removeEventListener("visibilitychange", this.#shown);
    this.#reads?.close();
    this.#reads = null;
  }

  attributeChangedCallback(name: string): void {
    if (!this.isConnected || this.#reads === null) return;
    if (name === "open") this.#reads.setOpen(this.getAttribute("open"));
    else this.#report();
  }

  #report(): void {
    const reads = this.#reads;
    if (reads === null || !reads.read) return;
    const detail = factsReport(reads.reads(), parseGiven(this.getAttribute("projects")), reads.open,
                               { now: new Date() });
    const text = JSON.stringify(detail);
    if (text === this.#last) return;
    this.#last = text;
    this.dispatchEvent(new CustomEvent(FACTS_EVENT, { detail, bubbles: true, composed: true }));
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, LauncherProjectFacts);
