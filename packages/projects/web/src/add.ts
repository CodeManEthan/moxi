// <moxi-projects-add kind="...">: the add form, as `moxi projects add` takes it: the key,
// the root (an absolute path; it may be left empty, and a kind's module may give it),
// the kind (starting at `kind` when it is listed), the lead, the notes root (only while
// notes is installed), and "create", which makes the root: hidden for a kind whose mint
// is `refuse`. While a contribution won't load, it says which: an add of a kind that
// module may declare is refused, and any other add goes ahead without it.
//
// It sends `/api/add` once per press and shows what came back: the project and its
// notes, with any warning beside them (a contribution skipped since it won't load, and
// the fix), or the refusal with its fix. When no answer comes back it can't know whether the
// add was made, so it says so, and that the directory may exist after a create. After
// every add that was made or may have been, it sends `CHANGED` on the document, so every
// projects component on the page reads its route again.
//
// It reads `/api/kinds` when it connects and when its tab is shown again.

import { css, html, LitElement, nothing } from "lit";
import { live } from "lit/directives/live.js";
import { api, installed } from "/m/ui/client.js";

import { CHANGED, Feed, parseAdded, parseKinds } from "./feed.ts";
import type { Kinds } from "./feed.ts";
import { BASE } from "./styles.ts";
import {
  actedOf, addBody, addSaid, brokenLines, createLine, createShown, emptyForm, failureLine, kindLabel,
  startKind,
} from "./views.ts";
import type { AddForm, AddSaid } from "./views.ts";

const TAG = "moxi-projects-add";

const STYLES = [BASE, css`
  form { display: grid; gap: 8px; min-width: 0; padding: 10px 12px; border: 1px solid var(--moxi-border);
         border-radius: var(--moxi-radius-lg); background: var(--moxi-bg-raised); }
  h3 { margin: 0; font-size: 1.05em; }
  label { display: grid; gap: 2px; min-width: 0; }
  label > span { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  label.check { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px 6px; }
  input[type=text], select { width: 100%; }
  .bar { display: flex; flex-wrap: wrap; align-items: center; gap: var(--moxi-gap); }
  .broken { padding-left: 8px; border-left: 3px solid var(--moxi-error); font-size: var(--moxi-font-size-sm); }
  .said { display: grid; gap: 2px; padding-left: 8px; border-left: 3px solid var(--moxi-border-strong); }
  .said.ok { border-left-color: var(--moxi-ok); color: var(--moxi-text); }
  .said.error { border-left-color: var(--moxi-error); color: var(--moxi-error); }
  .said.warn { border-left-color: var(--moxi-warn); color: var(--moxi-text); }
`];

export class ProjectsAdd extends LitElement {
  static styles = STYLES;

  static properties = {
    form: { state: true },
    sending: { state: true },
    said: { state: true },
  };

  form: AddForm = emptyForm("");
  sending = false;
  said: AddSaid | null = null;
  #wanted: string | null = null;
  #feed: Feed<Kinds> | null = null;
  #shown = (): void => {
    if (document.visibilityState === "visible") void this.#feed?.refresh();
  };

  connectedCallback(): void {
    super.connectedCallback();
    this.#wanted = this.getAttribute("kind") || null;
    const feed = new Feed<Kinds>(() => api("projects", "api/kinds"), parseKinds, () => {
      if (this.form.kind === "" || !feed.value?.found.some((kind) => kind.name === this.form.kind)) {
        this.form = { ...this.form, kind: startKind(feed.value, this.#wanted) ?? "" };
      }
      this.requestUpdate();
    });
    this.#feed = feed;
    void feed.refresh();
    document.addEventListener("visibilitychange", this.#shown);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#feed?.close();
    this.#feed = null;
    document.removeEventListener("visibilitychange", this.#shown);
  }

  render() {
    const feed = this.#feed;
    if (feed === null || feed.gone) return nothing;
    const kinds = feed.value;
    const form = this.form;
    const create = createShown(kinds, form.kind);
    const notes = installed("notes");
    const broken = brokenLines(kinds);
    const state = feed.loading ? "loading" : kinds === null ? "failed" : "up";
    return html`<form data-state=${state} @submit=${this.#submit} aria-busy=${this.sending ? "true" : "false"}>
      <h3>Add a project</h3>
      ${state === "loading" ? html`<div class="placeholder"></div>` : nothing}
      ${feed.failure !== null ? html`<p class="problem" data-line="failed">${failureLine(feed.failure)}</p>` : nothing}
      ${broken.map((line) => html`<p class="broken" data-line="broken">${line}</p>`)}
      <label><span>Key</span>
        <input type="text" name="key" required autocomplete="off" spellcheck="false"
          .value=${live(form.key)} @input=${this.#field("key")}></label>
      <label><span>Root, an absolute path (leave it empty when the kind's module gives one)</span>
        <input type="text" name="root" autocomplete="off" spellcheck="false"
          .value=${live(form.root)} @input=${this.#field("root")}></label>
      <label><span>Kind</span>
        <select name="kind" .value=${live(form.kind)} @change=${this.#field("kind")}
          ?disabled=${kinds === null}>
          ${(kinds?.found ?? []).map((kind) => html`<option value=${kind.name} title=${kind.doc}
            ?selected=${kind.name === form.kind}>${kindLabel(kind)}</option>`)}
        </select></label>
      <label><span>Lead agent (optional)</span>
        <input type="text" name="lead" autocomplete="off" spellcheck="false"
          .value=${live(form.lead)} @input=${this.#field("lead")}></label>
      ${notes ? html`<label><span>Notes root, an absolute path (optional)</span>
        <input type="text" name="notes_root" autocomplete="off" spellcheck="false"
          .value=${live(form.notesRoot)} @input=${this.#field("notesRoot")}></label>` : nothing}
      ${create ? html`<label class="check" data-part="create">
        <input type="checkbox" name="create" .checked=${live(form.create)} @change=${this.#tick}>
        Create the root <span class="dim">${createLine(kinds, form.kind)}</span></label>` : nothing}
      <div class="bar">
        <button type="submit" class="go" ?disabled=${this.sending || kinds === null}>Add</button>
      </div>
      ${this.said ? html`<div class="said ${this.said.tone}" data-line="said" data-tone=${this.said.tone}
        role="status">${this.said.lines.map((line) => html`<p>${line}</p>`)}</div>` : nothing}
      ${this.said?.warnings?.length ? html`<div class="said warn" data-line="warning" role="status">
        ${this.said.warnings.map((line) => html`<p>${line}</p>`)}</div>` : nothing}
    </form>`;
  }

  #field(name: "key" | "root" | "kind" | "lead" | "notesRoot") {
    return (event: Event): void => {
      const value = (event.target as HTMLInputElement | HTMLSelectElement).value;
      this.form = { ...this.form, [name]: value };
    };
  }

  #tick = (event: Event): void => {
    this.form = { ...this.form, create: (event.target as HTMLInputElement).checked };
  };

  #submit = async (event: Event): Promise<void> => {
    event.preventDefault();
    if (this.sending) return;
    const kinds = this.#feed?.value ?? null;
    const body = addBody(this.form, { kinds, notes: installed("notes") });
    const created = body.create === true;
    this.sending = true;
    this.said = null;
    try {
      const answer = await api("projects", "api/add", body);
      const acted = actedOf(answer.status, answer.data, parseAdded);
      this.said = addSaid(acted, created);
      if (acted.outcome === "done") this.form = emptyForm(this.form.kind);
      if (acted.outcome !== "refused") document.dispatchEvent(new Event(CHANGED));
    } finally {
      this.sending = false;
    }
  };
}

if (!customElements.get(TAG)) customElements.define(TAG, ProjectsAdd);
