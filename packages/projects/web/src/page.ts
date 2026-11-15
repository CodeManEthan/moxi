// <moxi-projects-page route="...">: projects' panel.
//
// - The list: each project a `<moxi-ui-row>` (`list-model.ts` gives every word), under a
//   dim count with `Add a project`. Line one is the key, its folder in `~` form and its
//   kind's word, at the right when it was last active, then `Open` (`Close` while open) in
//   the row's `acts-one` slot. Line two shows only when there is something to say: a
//   folder that doesn't exist, with `Change folder`, then the facts modules report. The
//   rows are by key until every facts component has reported once, or for 3 s; then, with
//   an activity source, bad news first and the rest by latest activity. Past twelve
//   projects, a find field, a kind filter and the `Earlier` fold.
// - The facts: every present module that declares a `project-facts` component has it
//   placed, once, into one hidden host, with the `projects` attribute (every project the
//   page holds) and `open` (the opened one's key). Each report replaces that module's
//   last; reports heard within one frame are drawn together. A module that goes takes
//   its component and its report with it. The contract is projects' README, "Facts on the
//   Projects page".
// - Until the panel, the opened project and the remove step are drawn anew: the store
//   file and the git the page server acts with, as `/api/where` gives them, with the
//   note a change would be refused with; an opened project's fields, edit form, lead and
//   agents, and Remove through the shell's confirm sheet; and projects' add form, placed
//   under the list.
//
// Every act sends what the same `moxi projects` verb takes, and every control that acts is
// off while one is on its way. After an act that was done or may have been, the page
// sends `CHANGED` on the document, so it and every projects component read again. The
// page reads its routes when it opens, when its tab is shown again, and on "Read again".

import { css, html, LitElement, noChange, nothing } from "lit";
import { Directive, directive, PartType } from "lit/directive.js";
import type { ElementPart, PartInfo } from "lit/directive.js";
import { live } from "lit/directives/live.js";
import { repeat } from "lit/directives/repeat.js";
import { api, components, confirm, installed, place, subscribe } from "/m/ui/client.js";

import { FACTS_COMPONENT, FACTS_EVENT } from "./facts.ts";
import type { Given } from "./facts.ts";
import { CHANGED, Feed, parseKinds, parseListing, parseOne, parseRemoveStatus, parseWhere, parseWritten } from "./feed.ts";
import type { Kinds, Listed, Listing, Project, RemoveStatus, Where } from "./feed.ts";
import { factsModules, FirstOrder, givenOf, HeldReports } from "./frame.ts";
import { HOLD_MS, listView, rowAttributes } from "./list-model.ts";
import type { ListView, ProjectRow } from "./list-model.ts";
import { emptySentence, NO_PROJECTS } from "./opened-model.ts";
import { BASE } from "./styles.ts";
import {
  actedOf, actSaid, agentBody, clearLeadBody, editBody, editing, failureLine, kindChoices, leadBody, MAY_HAVE_ACTED,
  NO_GIT, projectFields, removedSaid, removeSheet, removeStatusPath, reread, STALE_LINE, WHERE_LINE, whereView,
} from "./views.ts";
import type { EditForm, Editing, Said } from "./views.ts";

const TAG = "moxi-projects-page";

/** The list's words the page draws itself. */
const ADD_A_PROJECT = "Add a project";
const CHANGE_FOLDER = "Change folder";
const EARLIER = "Earlier";
const FIND_LABEL = "Find a project by its name or folder";
const KIND = "Kind";
const ALL = "all";
const CLEAR = "Clear";
const SHOW = "Show";
const HIDE = "Hide";

/** Sets each attribute of a map on the element it is bound to, and removes each one whose
 * value is null. */
class Attributes extends Directive {
  constructor(part: PartInfo) {
    super(part);
    if (part.type !== PartType.ELEMENT) throw new Error("attributes() binds to an element");
  }

  render(_values: Record<string, string | null>): unknown {
    return noChange;
  }

  update(part: ElementPart, [values]: [Record<string, string | null>]): unknown {
    for (const [name, value] of Object.entries(values)) {
      if (value === null) part.element.removeAttribute(name);
      else if (part.element.getAttribute(name) !== value) part.element.setAttribute(name, value);
    }
    return noChange;
  }
}
const attributes = directive(Attributes);

// The list is the Sessions page's rows under a dim count at the left, at its width; no
// accent and no warning colour on it. What is not yet drawn anew keeps its old look.
const STYLES = [BASE, css`
  :host { max-width: 680px; margin: 0 auto; padding: 4px 0 16px; font-size: 14px; line-height: 20px; }
  h1 { margin: 0 0 var(--moxi-gap); font-size: 1.4em; }
  h2 { margin: 0; font-size: 1.1em; }
  h3 { margin: 0; font-size: 1em; }
  .lede { margin-bottom: var(--moxi-gap); padding-left: 8px; border-left: 3px solid var(--moxi-accent);
          color: var(--moxi-text-dim); }
  section[data-part="where"] { display: grid; gap: var(--moxi-gap); min-width: 0; margin-top: calc(var(--moxi-gap) * 2);
            padding: 12px; border: 1px solid var(--moxi-border); border-radius: var(--moxi-radius-lg);
            background: var(--moxi-bg-raised); }
  section[data-part="add"] { margin-top: calc(var(--moxi-gap) * 2); }
  section[data-part="add"] > div { min-width: 0; }
  .bar { display: flex; flex-wrap: wrap; align-items: center; gap: var(--moxi-gap); min-width: 0; }
  .note { margin: 0; padding-left: 8px; border-left: 3px solid var(--moxi-warn); overflow-wrap: anywhere; }

  section[data-part="list"] { margin-top: calc(var(--moxi-gap) * 2); }
  .find { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; margin-top: 16px;
          border: 1px solid var(--moxi-border); border-radius: 10px; padding: 10px 14px; font-size: 15px; line-height: 22px;
          color: var(--moxi-text-dim); }
  .find input { flex: 1 1 auto; min-width: 0; font: inherit; color: var(--moxi-text); background: none; border: 0;
                padding: 0; margin: 0; outline: none; }
  .find input::placeholder { color: var(--moxi-text-dim); opacity: 1; }
  .find:focus-within { border-color: var(--moxi-border-strong); }
  .filt { display: flex; justify-content: space-between; align-items: baseline; gap: 4px 14px; margin-top: 12px;
          padding: 0 2px; color: var(--moxi-text-dim); white-space: nowrap; }
  .filt .k { margin-right: 8px; }
  .filt select { padding: 0; border: 0; background: none; color: var(--moxi-text-dim); font: inherit; }
  .filt select.set { color: var(--moxi-text); }
  .filt .n { margin-left: auto; font-variant-numeric: tabular-nums; }
  .grps { margin-top: 16px; border-top: 1px solid var(--moxi-border); }
  .grp { display: grid; grid-template-columns: 96px minmax(0, 1fr); border-bottom: 1px solid var(--moxi-border);
         padding: 5px 2px; }
  .grp .gk { color: var(--moxi-text-dim); padding-top: 7px; }
  .grp .gk small { display: block; font-size: 13px; line-height: 18px; margin-top: 3px; }
  .grp .gl { grid-column: 2; min-width: 0; }
  .grp button, moxi-ui-row > button { padding: 0; margin: 0; border: 0; background: none; font: inherit; text-align: left;
                                      cursor: pointer; }
  .grp button:hover:not(:disabled), moxi-ui-row > button:hover:not(:disabled) { background: none; }
  .grp .gk small button { color: var(--moxi-text); }
  moxi-ui-row > button.do { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); }
  moxi-ui-row > button.fix { font-size: 13px; line-height: 18px; color: var(--moxi-text); }
  moxi-ui-row[open] + moxi-ui-row { border-top: 1px solid var(--moxi-border); padding-top: 3px; }
  .one { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; padding: 7px 0;
         color: var(--moxi-text-dim); }
  .one button { flex: none; color: var(--moxi-text-dim); }
  .first { color: var(--moxi-text-dim); line-height: 1.6; text-align: center; padding-top: 24px; }
  .first p { max-width: 520px; margin: 0 auto; }
  .first .x { margin-top: 12px; padding: 0; border: 0; background: none; color: var(--moxi-text); }
  .said { display: grid; gap: 2px; padding-left: 8px; border-left: 3px solid var(--moxi-border-strong); }
  .said.ok { border-left-color: var(--moxi-ok); }
  .said.error { border-left-color: var(--moxi-error); color: var(--moxi-error); }
  .said.warn { border-left-color: var(--moxi-warn); }

  .detail { display: grid; gap: var(--moxi-gap); min-width: 0; margin: 9px 0 3px; padding-top: 8px;
            border-top: 1px solid var(--moxi-border); cursor: auto; }
  dl { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 2px var(--moxi-gap); margin: 0; }
  dt { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
  form { display: grid; gap: 6px; min-width: 0; }
  form.line { display: flex; flex-wrap: wrap; align-items: center; gap: var(--moxi-gap); }
  form.line input[type=text] { flex: 1 1 10em; }
  form label { display: grid; gap: 2px; min-width: 0; }
  form label > span { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  form:not(.line) input[type=text], form:not(.line) select { width: 100%; }
  .part { display: grid; gap: 6px; min-width: 0; padding-top: var(--moxi-gap); border-top: 1px solid var(--moxi-border); }
  .agent { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px var(--moxi-gap); min-width: 0; }
  .agent > span { overflow-wrap: anywhere; }
  .detail ul { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; min-width: 0; }

  @media (max-width: 720px) {
    :host { padding-top: 10px; }
    .grp { display: block; padding: 4px 0 6px; }
    .grp .gk { padding-top: 9px; font-size: 13px; }
    .grp .gk small { display: none; }
  }
`];

/** What the page last said, and the project it is about (null for the page itself). */
interface Told {
  key: string | null;
  said: Said;
}

export class ProjectsPage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
  };

  route = "";

  #list: Feed<Listing> | null = null;
  #kinds: Feed<Kinds> | null = null;
  #where: Feed<Where> | null = null;
  /** The key of the project that is open, as stored. */
  #open: string | null = null;
  #edit: Editing | null = null;
  #lead = "";
  #agent = "";
  /** An act is being sent, or the confirm sheet is open. */
  #sending = false;
  #told: Told | null = null;
  #placed = false;
  #missing = false;

  // The list's choices: the find field, the kind filter, the `Earlier` fold shown, and the
  // keys in the order last drawn, so the open row keeps its place.
  #typed = "";
  #kind: string | null = null;
  #earlierShown = false;
  #order: string[] = [];
  /** `Change folder` was pressed: the opened row's folder field takes the focus. */
  #focusRoot = false;

  // The facts: the shell's module list as last heard (undefined before it is), the hidden
  // host, the components placed and being placed by module, each component's element name
  // by module, and the `projects` attribute as last given.
  #modules: unknown = undefined;
  #host: HTMLDivElement;
  #facts = new Map<string, Element>();
  #placing = new Set<string>();
  #wanted: string[] = [];
  #modulesOf = new Map<string, string>();
  #given: Given[] = [];
  #givenText = "[]";
  #reports: HeldReports;
  #first = new FirstOrder(Date.now());
  #holdTimer: ReturnType<typeof setTimeout> | null = null;
  #stop: (() => void) | null = null;

  #again = (): void => {
    void this.#list?.refresh();
  };
  #shown = (): void => {
    if (document.visibilityState === "visible") this.#readAll();
  };

  constructor() {
    super();
    this.#host = document.createElement("div");
    this.#host.hidden = true;
    this.#host.dataset.part = "facts";
    this.#host.addEventListener(FACTS_EVENT, (event) => {
      event.stopPropagation();
      const module = this.#modulesOf.get((event.target as Element).localName);
      if (module !== undefined && this.#wanted.includes(module)) {
        this.#reports.heard(module, (event as CustomEvent).detail, this.#given, this.#open);
      }
    });
    this.#reports = new HeldReports((draw) => { requestAnimationFrame(() => draw()); }, () => this.requestUpdate(),
                                    (module, refused) => console.warn(`moxi: ${module}'s project facts refused in part:\n${
                                      refused.join("\n")}`));
  }

  connectedCallback(): void {
    super.connectedCallback();
    const changed = (): void => this.requestUpdate();
    this.#first = new FirstOrder(Date.now());
    this.#holdTimer = setTimeout(() => this.requestUpdate(), HOLD_MS);
    this.#list = new Feed<Listing>(() => api("projects", "api/projects"), parseListing, () => this.#listed());
    this.#kinds = new Feed<Kinds>(() => api("projects", "api/kinds"), parseKinds, changed);
    this.#where = new Feed<Where>(() => api("projects", "api/where"), parseWhere, changed);
    this.#readAll();
    this.#stop = subscribe("ui", "modules", (data) => {
      this.#modules = data;
      this.#seat();
    });
    document.addEventListener("visibilitychange", this.#shown);
    document.addEventListener(CHANGED, this.#again);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    for (const feed of [this.#list, this.#kinds, this.#where]) feed?.close();
    this.#list = this.#kinds = this.#where = null;
    this.#stop?.();
    this.#stop = null;
    if (this.#holdTimer !== null) clearTimeout(this.#holdTimer);
    this.#holdTimer = null;
    this.#wanted = [];
    for (const [module, element] of this.#facts) {
      element.remove();
      this.#reports.forget(module);
    }
    this.#facts.clear();
    this.#modules = undefined;
    document.removeEventListener("visibilitychange", this.#shown);
    document.removeEventListener(CHANGED, this.#again);
  }

  #readAll(): void {
    void this.#list?.refresh();
    void this.#kinds?.refresh();
    void this.#where?.refresh();
  }

  /** The list was read: the facts components are given what changed, an open project
   * that is no longer listed closes, and the open project's edit form takes in what was
   * read. */
  #listed(): void {
    const projects = this.#list?.value?.projects ?? [];
    if (this.#list?.value != null) {
      const given = givenOf(projects);
      const text = JSON.stringify(given);
      if (text !== this.#givenText) {
        this.#given = given;
        this.#givenText = text;
        for (const element of this.#facts.values()) element.setAttribute("projects", text);
      }
    }
    if (this.#open !== null && this.#list?.failure === null) {
      const open = projects.find((p) => p.key === this.#open) ?? null;
      if (open === null) {
        this.#setOpen(null);
        this.#edit = null;
      } else if (this.#edit !== null) {
        this.#edit = reread(this.#edit, open);
      }
    }
    this.#seat();
    this.requestUpdate();
  }

  #find(key: string | null): Listed | null {
    if (key === null) return null;
    return this.#list?.value?.projects.find((project) => project.key === key) ?? null;
  }

  // -- the facts components --------------------------------------------------------------

  /** Places the facts component of each module now present that declares one and is not
   * placed, and takes away those of modules gone. Nothing is placed before the list is
   * first read, so every component starts with the projects. */
  #seat(): void {
    if (this.#modules === undefined || this.#list?.value == null) return;
    const wanted = factsModules(this.#modules, (module) => this.#facts.has(module)
      || components(module).some((item) => item.name === FACTS_COMPONENT));
    this.#wanted = wanted;
    for (const module of wanted) {
      const element = components(module).find((item) => item.name === FACTS_COMPONENT)?.element;
      if (element !== undefined) this.#modulesOf.set(element, module);
    }
    for (const [module, element] of [...this.#facts]) {
      if (wanted.includes(module)) continue;
      element.remove();
      this.#facts.delete(module);
      this.#reports.forget(module);
    }
    for (const module of wanted) {
      if (!this.#facts.has(module) && !this.#placing.has(module)) void this.#placeFacts(module);
    }
    this.requestUpdate();
  }

  async #placeFacts(module: string): Promise<void> {
    this.#placing.add(module);
    let element: Element | null = null;
    try {
      element = await place(this.#host, module, FACTS_COMPONENT, this.#factsAttributes());
    } catch (exc) {
      console.error(`moxi: ${module}'s project facts could not be placed`, exc);
    }
    this.#placing.delete(module);
    if (element !== null && this.isConnected && this.#wanted.includes(module) && !this.#facts.has(module)) {
      // What changed while its script loaded.
      element.setAttribute("projects", this.#givenText);
      if (this.#open === null) element.removeAttribute("open");
      else element.setAttribute("open", this.#open);
      this.#facts.set(module, element);
    } else {
      element?.remove();
    }
    this.requestUpdate();
  }

  #factsAttributes(): Record<string, string> {
    return this.#open === null ? { projects: this.#givenText } : { projects: this.#givenText, open: this.#open };
  }

  /** Opens `key`'s project, or closes it with null, and tells every facts component. */
  #setOpen(key: string | null): void {
    if (key === this.#open) return;
    this.#open = key;
    for (const element of this.#facts.values()) {
      if (key === null) element.removeAttribute("open");
      else element.setAttribute("open", key);
    }
  }

  // -- acts --------------------------------------------------------------------------------

  /** Something projects keeps may have changed: tell every projects component, this page
   * among them, to read again. */
  #changed(): void {
    document.dispatchEvent(new Event(CHANGED));
  }

  async #act(key: string, path: string, body: object, parse: (data: unknown) => { project: Project; notes?: string[] } | null,
             line: string, done: (project: Project) => void = () => {}): Promise<void> {
    if (this.#sending) return;
    this.#sending = true;
    this.#told = null;
    this.requestUpdate();
    try {
      const answer = await api("projects", path, body);
      const acted = actedOf(answer.status, answer.data, parse);
      this.#told = { key, said: actSaid(acted, line) };
      if (acted.outcome === "done") {
        if (this.#open === key && this.#edit !== null) this.#edit = reread(this.#edit, acted.value.project);
        done(acted.value.project);
      }
      if (acted.outcome !== "refused") this.#changed();
    } finally {
      this.#sending = false;
      this.requestUpdate();
    }
  }

  #save(project: Project): void {
    const edit = this.#edit;
    const body = edit !== null ? editBody(edit.base, edit.form, { notes: installed("notes") }) : null;
    if (body === null) return;
    void this.#act(project.key, "api/set", body, parseWritten, `Saved ${project.key}.`,
                   (saved) => { if (this.#open === project.key) this.#edit = editing(saved); });
  }

  #setLead(project: Project): void {
    const body = leadBody(project.key, this.#lead);
    if (body.lead === "") return;
    void this.#act(project.key, "api/set", body, parseWritten, `${body.lead} leads ${project.key} now.`,
                   () => { this.#lead = ""; });
  }

  #clearLead(project: Project): void {
    void this.#act(project.key, "api/set", clearLeadBody(project.key), parseWritten,
                   `${project.key} has no lead now.`);
  }

  #addAgent(project: Project): void {
    const body = agentBody(project.key, this.#agent);
    if (body.name === "") return;
    void this.#act(project.key, "api/agents-add", body, parseOne, `Added ${body.name} to ${project.key}.`,
                   () => { this.#agent = ""; });
  }

  #removeAgent(project: Project, name: string): void {
    void this.#act(project.key, "api/agents-remove", agentBody(project.key, name), parseOne,
                   `Took ${name} out of ${project.key}.`);
  }

  async #remove(project: Project): Promise<void> {
    if (this.#sending) return;
    this.#sending = true;
    this.#told = null;
    this.requestUpdate();
    try {
      const outcome = await confirm("projects", "api/remove", { key: project.key }, removeSheet(project));
      if (outcome.outcome === "cancelled") return;
      if (outcome.outcome === "refused") {
        this.#told = { key: project.key, said: { tone: "error", lines: [outcome.text] } };
        return;
      }
      // Nothing was sent, and confirm() has already shown where to take the act.
      if (outcome.outcome === "desk") return;
      if (outcome.outcome === "unknown") {
        this.#told = { key: project.key, said: { tone: "warn", lines: [MAY_HAVE_ACTED] } };
        this.#changed();
        return;
      }
      const path = removeStatusPath(outcome.status);
      if (path === null) {
        this.#told = { key: project.key, said: { tone: "warn", lines: [
          "projects' page server took the remove but gave no status link to read; the list has been read again."] } };
        this.#changed();
        return;
      }
      const status = new Feed<RemoveStatus>(() => api("projects", path), parseRemoveStatus, () => {});
      await status.refresh();
      if (status.value !== null) {
        const removed = status.value.state === "removed";
        this.#told = { key: removed ? null : project.key, said: removedSaid(status.value) };
      } else {
        this.#told = { key: project.key, said: { tone: "warn", lines: [
          `The remove was taken, but what it came to can't be read: ${status.failure?.said ?? status.failure?.why ?? "no answer"}.`] } };
      }
      this.#changed();
    } finally {
      this.#sending = false;
      this.requestUpdate();
    }
  }

  // -- rendering ---------------------------------------------------------------------------

  render() {
    const list = this.#list;
    if (list === null) return nothing;
    if (list.gone) return html`<p class="problem">projects is no longer installed.</p>`;
    return html`<h1>Projects</h1>
      <p class="lede">Named places: each project's key, root, kind and agents. Each change here does what the same <code>moxi projects</code> command does in a terminal.</p>
      ${this.#wherePart()}
      ${this.#listPart(list)}
      <section data-part="add"><div data-part="add-form"></div>
        ${this.#missing ? html`<p class="problem" data-line="no-add">The add form can't be shown here. Run <code>moxi projects add</code> in a terminal.</p>` : nothing}
      </section>
      ${this.#host}`;
  }

  #wherePart() {
    const feed = this.#where;
    if (feed === null) return nothing;
    const view = whereView(feed);
    if (view.state === "gone") return nothing;
    return html`<section data-part="where" data-state=${view.state}>
      <h2>Where this page acts</h2>
      ${view.state === "loading" ? html`<div class="placeholder"></div>` : nothing}
      ${view.store !== null ? html`<p>Store: <code data-line="store">${view.store}</code></p>
        <p>Git: ${view.git !== null ? html`<code data-line="git">${view.git}</code>`
          : html`<span class="warn" data-line="git">${NO_GIT}</span>`}</p>
        <p class="dim">${WHERE_LINE}</p>` : nothing}
      ${view.note !== null ? html`<p class="note" data-line="note" role="status">${view.note}</p>` : nothing}
      ${view.line !== null ? html`<p class="problem" data-line="failed">${view.line}</p>` : nothing}
    </section>`;
  }

  /** The list's state: `loading` before the first read, `failed` when the last read
   * failed (the last list kept), `empty` with no project, else `up`. */
  #state(list: Feed<Listing>): "loading" | "failed" | "empty" | "up" {
    if (list.failure !== null) return "failed";
    if (list.value === null) return "loading";
    return list.value.projects.length === 0 ? "empty" : "up";
  }

  #listPart(list: Feed<Listing>) {
    const state = this.#state(list);
    const told = this.#told;
    const loose = told !== null && (told.key === null || this.#find(told.key) === null) ? told.said : null;
    const listing = list.value;
    const reports = this.#reports.reports();
    const placed = this.#modules === undefined ? null : this.#facts.size + this.#placing.size;
    const held = this.#first.held(placed, this.#reports.reported, Date.now());
    return html`<section data-part="list" data-state=${state}>
      <div class="bar"><button type="button" data-action="read" ?disabled=${list.reading}
        @click=${() => this.#readAll()}>Read again</button></div>
      ${loose !== null ? this.#saidPart(loose) : nothing}
      ${list.failure !== null ? html`<p class="problem" data-line="failed">${failureLine(list.failure)}</p>` : nothing}
      ${listing === null ? nothing : listing.projects.length === 0 ? html`<div class="first" data-part="empty">
          <p data-line="empty">${NO_PROJECTS}</p><p data-line="sentence">${emptySentence(reports)}</p>
          <button type="button" class="x" data-action="add" @click=${() => this.#add()}>${ADD_A_PROJECT}</button></div>`
        : this.#rows(listing, listView(listing.projects, { home: listing.home, reports, open: this.#open, now: Date.now() / 1000 },
                                       { find: this.#typed, kind: this.#kind, held, previous: this.#order }))}
    </section>`;
  }

  #rows(listing: Listing, view: ListView) {
    this.#order = view.order;
    const byKey = new Map(listing.projects.map((project) => [project.key, project]));
    const row = (item: ProjectRow) => this.#row(byKey.get(item.key) as Listed, item);
    return html`${view.long ? this.#findPart(view) : nothing}
      <div class="grps">
        <section class="grp" data-part="projects"><div class="gk"><span data-line="count">${view.count}</span><small><button
          type="button" data-action="add" @click=${() => this.#add()}>${ADD_A_PROJECT}</button></small></div>
          <div class="gl">${repeat(view.rows, (item) => item.key, row)}</div></section>
        ${view.earlierLine !== null ? html`<section class="grp" data-part="earlier"><div class="gk">${EARLIER}</div>
          <div class="gl"><div class="one"><span data-line="earlier">${view.earlierLine}</span><button type="button"
            data-action="earlier" aria-expanded=${this.#earlierShown ? "true" : "false"}
            @click=${() => { this.#earlierShown = !this.#earlierShown; this.requestUpdate(); }}>${
            this.#earlierShown ? HIDE : SHOW}</button></div>
            ${this.#earlierShown ? repeat(view.earlier, (item) => item.key, row) : nothing}</div></section>` : nothing}
      </div>`;
  }

  #findPart(view: ListView) {
    return html`<div class="find" data-part="find">
        <input type="text" aria-label=${FIND_LABEL} placeholder=${FIND_LABEL} autocomplete="off" spellcheck="false"
          .value=${live(this.#typed)} @input=${(event: Event) => {
            this.#typed = (event.target as HTMLInputElement).value;
            this.requestUpdate();
          }}>
        ${this.#typed !== "" ? html`<button type="button" class="x" data-action="clear-find"
          @click=${() => { this.#typed = ""; this.requestUpdate(); }}>${CLEAR}</button>` : nothing}
      </div>
      ${view.kinds.length > 0 || view.shownOf !== null ? html`<div class="filt" data-part="filters">
        ${view.kinds.length > 0 ? html`<label><span class="k">${KIND}</span><select class=${this.#kind !== null ? "set" : ""}
          data-action="kind" .value=${live(this.#kind ?? "")} @change=${(event: Event) => {
            const value = (event.target as HTMLSelectElement).value;
            this.#kind = value === "" ? null : value;
            this.requestUpdate();
          }}><option value="">${ALL}</option>${view.kinds.map((kind) => html`<option value=${kind}
            ?selected=${kind === this.#kind}>${kind}</option>`)}</select></label>` : nothing}
        ${view.shownOf !== null ? html`<span class="n" data-line="shown">${view.shownOf}</span>` : nothing}
      </div>` : nothing}`;
  }

  #row(project: Listed, row: ProjectRow) {
    const open = row.act === "Close";
    return html`<moxi-ui-row data-key=${row.key} ${attributes(rowAttributes(row))}
        @moxi-row-toggle=${() => this.#toggle(project)}>
      <button type="button" slot="acts-one" class="do" data-action="open" aria-expanded=${open ? "true" : "false"}
        @click=${() => this.#toggle(project)}>${row.act}</button>
      ${row.changeFolder ? html`<button type="button" slot="acts" class="fix" data-action="change-folder"
        @click=${() => this.#changeFolder(project)}>${CHANGE_FOLDER}</button>` : nothing}
      ${open ? this.#detail(project) : nothing}
    </moxi-ui-row>`;
  }

  #saidPart(said: Said) {
    return html`<div class="said ${said.tone}" data-line="said" data-tone=${said.tone} role="status">
      ${said.lines.map((line) => html`<p>${line}</p>`)}</div>`;
  }

  #toggle(project: Listed): void {
    if (this.#open === project.key) {
      this.#setOpen(null);
      this.#edit = null;
    } else {
      this.#setOpen(project.key);
      this.#edit = editing(project);
      this.#lead = "";
      this.#agent = "";
    }
    if (this.#told?.key !== null && this.#told?.key !== this.#open) this.#told = null;
    this.requestUpdate();
  }

  /** `Change folder`: the project opens with its folder field focused. */
  #changeFolder(project: Listed): void {
    if (this.#open !== project.key) this.#toggle(project);
    this.#focusRoot = true;
    this.requestUpdate();
  }

  /** `Add a project`: the add form under the list, until the panel is drawn. */
  #add(): void {
    this.renderRoot.querySelector('[data-part="add"]')?.scrollIntoView({ block: "nearest" });
  }

  #detail(project: Project) {
    const busy = this.#sending;
    const edit = this.#edit ?? editing(project);
    const form = edit.form;
    const notes = installed("notes");
    const kinds = this.#kinds?.value ?? null;
    const changes = editBody(edit.base, form, { notes });
    const told = this.#told?.key === project.key ? this.#told.said : null;
    return html`<div class="detail">
      <dl>${projectFields(project).map((field) => html`<dt>${field.label}</dt><dd data-field=${field.name}>${
        field.name === "root" || field.name === "notes_root" ? html`<code>${field.value}</code>` : field.value}</dd>`)}</dl>
      ${told !== null ? this.#saidPart(told) : nothing}
      <form class="part" data-part="edit" @submit=${(event: Event) => { event.preventDefault(); this.#save(project); }}>
        <h3>Change</h3>
        <label><span>Root, an absolute path</span>
          <input type="text" name="root" autocomplete="off" spellcheck="false" ?disabled=${busy}
            .value=${live(form.root)} @input=${this.#editField("root")}></label>
        <label><span>Kind</span>
          <select name="kind" ?disabled=${busy || kinds === null} .value=${live(form.kind)} @change=${this.#editField("kind")}>
            ${kindChoices(kinds, edit.base.kind).map((choice) => html`<option value=${choice.value}
              ?selected=${choice.value === form.kind}>${choice.label}</option>`)}
          </select></label>
        ${notes ? html`<label><span>Notes root, an absolute path (empty it to clear it)</span>
          <input type="text" name="notes_root" autocomplete="off" spellcheck="false" ?disabled=${busy}
            .value=${live(form.notesRoot)} @input=${this.#editField("notesRoot")}></label>` : nothing}
        ${edit.stale ? html`<p class="warn" data-line="stale" role="status">${STALE_LINE}</p>` : nothing}
        <div class="bar"><button type="submit" class="go" data-action="save" ?disabled=${busy || changes === null}>Save</button></div>
      </form>
      <div class="part" data-part="agents">
        <h3>Agents</h3>
        <div class="agent" data-part="lead"><span>Lead: <strong data-line="lead">${project.lead ?? "none"}</strong></span>
          ${project.lead !== null ? html`<button type="button" data-action="clear-lead" ?disabled=${busy}
            @click=${() => this.#clearLead(project)}>Clear lead</button>` : nothing}</div>
        <form class="line" data-part="set-lead" @submit=${(event: Event) => { event.preventDefault(); this.#setLead(project); }}>
          <input type="text" name="lead" aria-label="Lead agent" placeholder="agent name" autocomplete="off" spellcheck="false"
            ?disabled=${busy} .value=${live(this.#lead)}
            @input=${(event: Event) => { this.#lead = (event.target as HTMLInputElement).value; this.requestUpdate(); }}>
          <button type="submit" data-action="set-lead" ?disabled=${busy || this.#lead.trim() === ""}>Set lead</button>
        </form>
        ${project.associated.length ? html`<ul aria-label="Associated agents">${project.associated.map((name) => html`
          <li class="agent" data-agent=${name}><span>${name}</span>
            <button type="button" data-action="remove-agent" aria-label=${`Remove ${name}`} ?disabled=${busy}
              @click=${() => this.#removeAgent(project, name)}>Remove</button></li>`)}</ul>`
          : html`<p class="dim" data-line="no-agents">No associated agents.</p>`}
        <form class="line" data-part="add-agent" @submit=${(event: Event) => { event.preventDefault(); this.#addAgent(project); }}>
          <input type="text" name="agent" aria-label="Agent name" placeholder="agent name" autocomplete="off" spellcheck="false"
            ?disabled=${busy} .value=${live(this.#agent)}
            @input=${(event: Event) => { this.#agent = (event.target as HTMLInputElement).value; this.requestUpdate(); }}>
          <button type="submit" data-action="add-agent" ?disabled=${busy || this.#agent.trim() === ""}>Add agent</button>
        </form>
      </div>
      <div class="part" data-part="remove"><div class="bar">
        <button type="button" data-action="remove" ?disabled=${busy} @click=${() => void this.#remove(project)}>Remove…</button>
        <span class="dim">Takes it off the list; its files stay.</span>
      </div></div>
    </div>`;
  }

  #editField(name: keyof EditForm) {
    return (event: Event): void => {
      const value = (event.target as HTMLInputElement | HTMLSelectElement).value;
      if (this.#edit !== null) this.#edit = { ...this.#edit, form: { ...this.#edit.form, [name]: value } };
      this.requestUpdate();
    };
  }

  updated(): void {
    if (this.#focusRoot && this.#open !== null) {
      const field = this.renderRoot.querySelector<HTMLInputElement>(
        `moxi-ui-row[data-key="${CSS.escape(this.#open)}"] input[name="root"]`);
      if (field !== null) {
        this.#focusRoot = false;
        field.focus();
      }
    }
    if (this.#placed) return;
    const host = this.renderRoot.querySelector('[data-part="add-form"]');
    if (host === null) return;
    this.#placed = true;
    const missing = (): void => {
      this.#missing = true;
      this.requestUpdate();
    };
    place(host, "projects", "add").then((element) => { if (element === null) missing(); }, missing);
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, ProjectsPage);
