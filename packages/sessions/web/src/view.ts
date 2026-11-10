// The one session view sessions' page and its window block both draw: the header, the
// last turns oldest first, "Earlier" to page back, and the session's subagents.
//
// It reads `/api/window` and `/api/subagents` when a session is opened, when its tab is
// shown again, and on "Refresh": sessions keeps no process that could push a session's
// changes. A refresh keeps the earlier pages read while the newest window still starts
// where it did, and drops them once a new turn has moved it.
//
// Each message's text is cut at 2,000 characters, with a button that shows the rest the
// page server sent, and a line when the server itself cut the text at 64 KiB. A refusal
// (a session the page server can't find or read, a `before` it can't use) shows its
// reason. A tool result that failed and a message its harness wrote for a failed request
// each start with a word that says so, in the heavier weight.
//
// What selecting a subagent or the parent session does is the component's: the page
// navigates to its route, the block opens it in place. The page also draws its own header
// over the turns, so the turns and the subagents are each a part it can place.

import { html, LitElement, nothing } from "lit";
import { repeat } from "lit/directives/repeat.js";
import { api } from "/m/ui/client.js";

import { Feed, failureWhy, parseListing, parseWindow, refusedText } from "./feed.ts";
import type { Listing, WindowAnswer } from "./feed.ts";
import {
  headerFields, headingOf, keepEarlier, listingView, pages, subagentsPath, textView, unknownLine,
  windowPath, windowView,
} from "./views.ts";
import type { EntryView, Field, Item, Target } from "./views.ts";

export abstract class SessionView extends LitElement {
  #target: Target | null = null;
  #window: Feed<WindowAnswer> | null = null;
  #listing: Feed<Listing> | null = null;
  /** The newest window the earlier pages were read before. */
  #latest: WindowAnswer | null = null;
  /** Pages read by "Earlier", oldest first. */
  #earlier: WindowAnswer[] = [];
  #paging = false;
  #pagingLine: string | null = null;
  /** The keys of the messages shown whole. */
  #expanded = new Set<string>();
  #shown = (): void => {
    if (document.visibilityState === "visible") this.refreshSession();
  };

  /** Opens a subagent the listing offers. */
  protected abstract pick(entry: EntryView): void;
  /** Opens the session this one is a subagent or child of. */
  protected abstract openParent(parent: Target): void;

  connectedCallback(): void {
    super.connectedCallback();
    document.addEventListener("visibilitychange", this.#shown);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#close();
    document.removeEventListener("visibilitychange", this.#shown);
  }

  protected get target(): Target | null {
    return this.#target;
  }

  /** Shows `target`, read afresh; null shows none. */
  protected openSession(target: Target | null): void {
    this.#close();
    this.#target = target;
    this.#latest = null;
    this.#earlier = [];
    this.#pagingLine = null;
    this.#expanded = new Set();
    if (target !== null) {
      this.#window = new Feed<WindowAnswer>(() => api("sessions", windowPath(target)), parseWindow,
                                            () => this.#windowRead());
      // A Claude Code subagent's listing would be its parent's; it lists none of its own.
      if (target.agent === null) {
        this.#listing = new Feed<Listing>(() => api("sessions", subagentsPath(target)), parseListing,
                                          () => this.requestUpdate());
      }
      this.refreshSession();
    }
    this.requestUpdate();
  }

  /** Reads the session's routes again. */
  protected refreshSession(): void {
    void this.#window?.refresh();
    void this.#listing?.refresh();
  }

  #close(): void {
    this.#window?.close();
    this.#listing?.close();
    this.#window = this.#listing = null;
  }

  #windowRead(): void {
    const value = this.#window?.value ?? null;
    if (value !== null && value !== this.#latest) {
      if (!keepEarlier(this.#latest, value)) {
        this.#earlier = [];
        this.#pagingLine = null;
      }
      this.#latest = value;
    }
    this.requestUpdate();
  }

  async #readEarlier(before: string): Promise<void> {
    const target = this.#target;
    const window = this.#window;
    if (target === null || this.#paging) return;
    this.#paging = true;
    this.#pagingLine = null;
    this.requestUpdate();
    try {
      const answer = await api("sessions", windowPath(target, before));
      if (this.#window !== window) return;
      const value = answer.status === 200 ? parseWindow(answer.data) : null;
      if (value !== null) this.#earlier = [value, ...this.#earlier];
      else if (answer.status === 422) this.#pagingLine = refusedText(answer.data) ?? "sessions' page server refused the read";
      else this.#pagingLine = failureWhy(answer.status, answer.data);
    } finally {
      this.#paging = false;
      this.requestUpdate();
    }
  }

  #toggle(key: string): void {
    if (this.#expanded.has(key)) this.#expanded.delete(key);
    else this.#expanded.add(key);
    this.requestUpdate();
  }

  // -- rendering ---------------------------------------------------------------------------

  /** The read of the open session's turns; null when no session is open. */
  protected get windowFeed(): Feed<WindowAnswer> | null {
    return this.#window;
  }

  /** Whether sessions went away while the view was open. */
  protected get sessionGone(): boolean {
    return this.#window?.gone === true || this.#listing?.gone === true;
  }

  /** The view of the open session, with `actions` beside Refresh. */
  protected renderSession(actions: unknown = nothing) {
    const target = this.#target;
    const feed = this.#window;
    if (target === null || feed === null) return nothing;
    const view = windowView(feed);
    if (view.state === "gone") return html`<p class="problem" data-line="gone">sessions is no longer installed.</p>`;
    const answer = view.answer;
    return html`<section data-part="window" data-state=${view.state}>
      <div class="bar">
        <h2>${headingOf(answer, target)}</h2>
        ${actions}
        <button type="button" data-action="refresh" ?disabled=${feed.reading} @click=${() => this.refreshSession()}>Refresh</button>
      </div>
      ${view.state === "loading" ? html`<div class="placeholder"></div>` : nothing}
      ${view.line !== null ? html`<p class="problem" data-line=${view.state} role="status">${view.line}</p>` : nothing}
      ${answer !== null ? this.renderTurns(target, answer) : nothing}
    </section>
    ${this.renderSubagents()}`;
  }

  /** The header's facts as `fields` (none when null), the link to the parent session,
   * "Earlier" and the records. */
  protected renderTurns(target: Target, answer: WindowAnswer, fields: Field[] | null = headerFields(answer)) {
    const header = answer.header;
    const parent: Target | null = target.agent !== null ? { ...target, agent: null }
      : typeof header.parent === "string" && header.parent !== "" ? { harness: target.harness, session: header.parent, agent: null }
      : null;
    const shown = pages(answer, this.#earlier);
    return html`${fields !== null ? html`<dl data-part="header">${fields.map((field) => html`
        <dt>${field.label}</dt><dd data-field=${field.name}>${field.code ? html`<code>${field.value}</code>` : field.value}</dd>`)}</dl>`
        : nothing}
      ${parent !== null ? html`<p><button type="button" class="link" data-action="parent"
        @click=${() => this.openParent(parent)}>Open the parent session</button></p>` : nothing}
      <div class="bar" data-part="earlier">
        ${shown.hasEarlier && shown.before !== null ? html`<button type="button" data-action="earlier"
          ?disabled=${this.#paging} @click=${() => void this.#readEarlier(shown.before as string)}>Earlier turns</button>` : nothing}
        ${shown.startLine !== null ? html`<span class="dim" data-line="start">${shown.startLine}</span>` : nothing}
        ${shown.limitLine !== null ? html`<span class="warn" data-line="limit">${shown.limitLine}</span>` : nothing}
        ${this.#pagingLine !== null ? html`<span class="problem" data-line="earlier" role="status">${this.#pagingLine}</span>` : nothing}
      </div>
      ${shown.items.length ? html`<ol data-part="records" aria-label="Records, oldest first">
        ${repeat(shown.items, (item) => item.key, (item) => this.#item(item))}</ol>`
        : html`<p class="dim" data-line="no-records">No records in these turns.</p>`}`;
  }

  /** A record's word for what went wrong, in the heavier weight, before its line. */
  #word(word: string | null) {
    return word !== null ? html`<strong class="word" data-line="word">${word}</strong> ` : nothing;
  }

  #item(item: Item) {
    const unknown = unknownLine(item.unknown) ?? nothing;
    switch (item.type) {
      case "message": {
        const open = this.#expanded.has(item.key);
        const text = textView(item.row, open);
        return html`<li class="msg" data-kind="message" data-key=${item.key} data-role=${item.role}
          data-reasoning=${item.reasoning ? "true" : "false"} title=${unknown}>
          <div class="who">${this.#word(item.word)}<span>${item.label}</span>${item.tags.map((tag) => html`<span class="tag">${tag}</span>`)}</div>
          ${text.shown !== null ? html`<p class="text" data-line="text">${text.shown}</p>`
            : html`<p class="dim" data-line="text">(hidden)</p>`}
          ${text.long ? html`<p><button type="button" class="link" data-action="expand" aria-expanded=${open ? "true" : "false"}
            @click=${() => this.#toggle(item.key)}>${open ? "Show less" : `Show ${text.rest.toLocaleString("en-US")} more characters`}</button></p>` : nothing}
          ${text.sentLine !== null ? html`<p class="warn" data-line="sent">${text.sentLine}</p>` : nothing}
        </li>`;
      }
      case "call":
        return html`<li class="line call" data-kind="tool_call" title=${unknown}><code>${item.line}</code></li>`;
      case "result":
        return html`<li class="line result" data-kind="tool_result" data-error=${item.error ? "true" : "false"}
          title=${unknown}>${this.#word(item.word)}${item.line}</li>`;
      case "rule":
        return html`<li class="rule" data-kind="marker" data-marker=${item.marker} title=${unknown}><span>${this.#word(item.word)}${item.line}</span></li>`;
      case "request":
        return html`<li class="line request" data-kind="request" title=${unknown}>${item.line}</li>`;
      default:
        return html`<li class="line note" data-kind="note" title=${unknown}>${item.line}</li>`;
    }
  }

  /** The session's subagents, each a button that opens it. */
  protected renderSubagents() {
    const feed = this.#listing;
    if (feed === null) return nothing;
    const view = listingView(feed);
    if (view.state === "gone") return nothing;
    const tone = view.state === "none" || view.state === "listed" ? "dim" : "problem";
    return html`<section data-part="subagents" data-state=${view.state}>
      <h2>Subagents</h2>
      ${view.state === "loading" ? html`<div class="placeholder"></div>` : nothing}
      ${view.line !== null ? html`<p class=${tone} data-line=${view.state}>${view.line}</p>` : nothing}
      ${view.parts.length ? html`<ul aria-label="Parts that could not be read">${view.parts.map((part) =>
        html`<li class="problem" data-line="part">${part}</li>`)}</ul>` : nothing}
      ${view.entries.length ? html`<ul aria-label="Subagents">${view.entries.map((entry) => html`
        <li class="entry" data-agent=${entry.agentId}>
          <div class="first">
            <button type="button" class="link" data-action="subagent" @click=${() => this.pick(entry)}>${entry.type}</button>
            <code class="dim">${entry.agentId}</code>
            ${entry.size !== null ? html`<span class="dim">${entry.size}</span>` : nothing}
          </div>
          ${entry.description !== null ? html`<p data-line="description">${entry.description}</p>` : nothing}
        </li>`)}</ul>` : nothing}
    </section>`;
  }
}
