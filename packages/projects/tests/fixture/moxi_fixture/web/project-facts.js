// <moxi-fixture-project-facts projects="[...]" open="key">: the fixture's facts for the
// Projects page. It reads its own page server's `/api/facts` (the report a test wrote)
// when it is connected, every EVERY_MS while the document shows, once when the document
// shows again, and stops reading when it is disconnected. It reports, in a bubbling,
// composed `moxi-project-facts` event, what that read said: the projects it was given in
// `projects` only, and a project's sections, agents and Remove lines only for the key in
// `open`. A report that is not an object, or has no `projects` object, goes as it is. A read
// that fails reports null; the next good one puts the facts back. It reports only when what
// it would say changed, and always once after it is connected. It draws nothing. Plain
// DOM, so it needs no build.
import { api } from "/m/ui/client.js";

const TAG = "moxi-fixture-project-facts";
const EVENT = "moxi-project-facts";
const EVERY_MS = 300;
/** The fields a report gives the open project only. */
const OPEN_ONLY = ["sections", "agents", "stays"];

const isObject = (value) => typeof value === "object" && value !== null && !Array.isArray(value);

function givenKeys(text) {
  try {
    const list = JSON.parse(text ?? "[]");
    return new Set(Array.isArray(list) ? list.filter(isObject).map((item) => item.key) : []);
  } catch {
    return new Set();
  }
}

/** What the component says of `stored` for the projects `keys` with `open` opened. */
function said(stored, keys, open) {
  if (!isObject(stored) || !isObject(stored.projects)) return stored;
  const projects = {};
  for (const [key, facts] of Object.entries(stored.projects)) {
    if (!keys.has(key)) continue;
    if (key === open || !isObject(facts)) {
      projects[key] = facts;
      continue;
    }
    const kept = { ...facts };
    for (const name of OPEN_ONLY) delete kept[name];
    projects[key] = kept;
  }
  return { ...stored, projects };
}

if (!customElements.get(TAG)) {
  customElements.define(TAG, class extends HTMLElement {
    static observedAttributes = ["projects", "open"];

    constructor() {
      super();
      this.stored = null;
      this.last = undefined;
      this.timer = null;
      this.shown = () => {
        if (document.visibilityState === "visible") {
          this.read();
          this.start();
        } else {
          this.stop();
        }
      };
    }

    connectedCallback() {
      if (this.shadowRoot === null) {
        const style = document.createElement("style");
        style.textContent = ":host { display: none; }";
        this.attachShadow({ mode: "open" }).append(style);
      }
      this.last = undefined;
      document.addEventListener("visibilitychange", this.shown);
      this.read();
      if (document.visibilityState === "visible") this.start();
    }

    disconnectedCallback() {
      document.removeEventListener("visibilitychange", this.shown);
      this.stop();
    }

    attributeChangedCallback() {
      if (this.isConnected && this.last !== undefined) this.report();
    }

    start() {
      if (this.timer === null) this.timer = setInterval(() => this.read(), EVERY_MS);
    }

    stop() {
      if (this.timer !== null) clearInterval(this.timer);
      this.timer = null;
    }

    async read() {
      const answer = await api("fixture", "api/facts");
      if (!this.isConnected) return;
      let stored = null;
      if (answer.status === 200 && typeof answer.data?.report === "string") {
        try {
          stored = JSON.parse(answer.data.report);
        } catch {
          stored = null;
        }
      }
      this.stored = stored;
      this.report();
    }

    report() {
      const detail = said(this.stored, givenKeys(this.getAttribute("projects")),
                          this.getAttribute("open"));
      const text = JSON.stringify(detail);
      if (text === this.last) return;
      this.last = text;
      this.dispatchEvent(new CustomEvent(EVENT, { detail, bubbles: true, composed: true }));
    }
  });
}
