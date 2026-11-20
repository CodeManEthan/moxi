// <moxi-sessions-project-facts projects="[...]" open="key">: sessions' facts for the
// Projects page. It draws nothing; it reports, in a bubbling, composed
// `moxi-project-facts` event, what `factsReport` makes of `/api/projects` for the projects
// it was given, and the `Sessions` row and `Stays` line of the one in `open`.
//
// It reads `/api/projects` when it is connected and then once a minute, and reads nothing
// of another module's. While the document is hidden its timer stops; it reads once when
// the document shows again. A read that fails, is refused, finds sessions gone, or answers
// a `state` other than `up` reports null; the next good read puts the facts back. It
// reports only when what it would say changed, and always once after its first read since
// it was connected.

import { api } from "/m/ui/client.js";

import { FACTS_EVENT, factsReport, goodValue, parseGiven, parseProjects, READ_MS } from "./facts.ts";
import type { ProjectsAnswer } from "./facts.ts";
import { Feed } from "./feed.ts";

const TAG = "moxi-sessions-project-facts";

export class SessionsProjectFacts extends HTMLElement {
  static observedAttributes = ["projects", "open"];

  #feed: Feed<ProjectsAnswer> | null = null;
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
    this.#feed = new Feed<ProjectsAnswer>(() => api("sessions", "api/projects"), parseProjects,
                                          () => this.#report());
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

  /** Reads once, then again on the timer. */
  #follow(): void {
    const feed = this.#feed;
    if (feed === null) return;
    void feed.refresh();
    if (this.#timer === null) this.#timer = setInterval(() => void this.#feed?.refresh(), READ_MS);
  }

  #pause(): void {
    if (this.#timer !== null) clearInterval(this.#timer);
    this.#timer = null;
  }

  #report(): void {
    const feed = this.#feed;
    if (feed === null || feed.loading) return;
    const detail = factsReport(goodValue(feed), parseGiven(this.getAttribute("projects")),
                               this.getAttribute("open"));
    const text = JSON.stringify(detail);
    if (text === this.#last) return;
    this.#last = text;
    this.dispatchEvent(new CustomEvent(FACTS_EVENT, { detail, bubbles: true, composed: true }));
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, SessionsProjectFacts);
