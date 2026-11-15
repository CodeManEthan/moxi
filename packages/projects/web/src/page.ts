// <moxi-projects-page route="...">: projects' panel.
//
// - The address: the route opens the list (`""`), a project (`open/<key>`, scrolled to),
//   a project for its edit (`edit/<key>`) or its remove (`remove/<key>`), or the add
//   (`add`); a key is matched ignoring case (`address.ts`). One thing is open at a time,
//   and the page rewrites the browser's address with `history.replaceState` whenever what
//   is open changes, so Back leaves the page. A route is opened once the list is read; a
//   key that is no project opens the list, with one dim line at its foot.
// - The list: each project a `<moxi-ui-row>` (`list-model.ts` gives every word), under a
//   dim count with `Add a project`. Line one is the key, its folder in `~` form and its
//   kind's word, at the right when it was last active, then `Open` (`Close` while open) in
//   the row's `acts-one` slot. Line two shows only when there is something to say: a
//   folder that doesn't exist, with `Change folder`, then the facts modules report. The
//   rows are by key until every facts component has reported once, or for 3 s; then, with
//   an activity source, bad news first and the rest by latest activity. Past twelve
//   projects, a find field, a kind filter and the `Earlier` fold.
// - The list's states: nothing for 400 ms, then `Reading…`; while projects' page server
//   isn't answering, a first row saying so over the last list, dim and with no acts; a
//   store that refuses, its reason and fix and nothing else; no project, the empty
//   sentence.
// - The facts: every present module that declares a `project-facts` component has it
//   placed, once, into one hidden host, with the `projects` attribute (every project the
//   page holds) and `open` (the opened one's key). Each report replaces that module's
//   last; reports heard within one frame are drawn together. A module that goes takes
//   its component and its report with it. The contract is projects' README, "Facts on the
//   Projects page".
// - The panel (`panel.ts`): `Add a project`, and the `add` address, open the Add panel at
//   the window's right edge, and the column moves aside where the window has the room.
// - Until the panel's Edit and the remove step are drawn, an opened project's `Edit`
//   shows its edit form, lead and agents, and `Remove` the remove through the shell's
//   confirm sheet.
//
// Every act sends what the same `moxi projects` verb takes, and every control that acts is
// off while one is on its way. After an act that was done or may have been, the page
// sends `CHANGED` on the document, so it and every projects component read again. The
// page reads the list and the kinds when it opens, when its tab is shown again and on
// `CHANGED`.

import { css, html, LitElement, noChange, nothing, unsafeCSS } from "lit";
import { Directive, directive, PartType } from "lit/directive.js";
import type { ElementPart, PartInfo } from "lit/directive.js";
import { live } from "lit/directives/live.js";
import { repeat } from "lit/directives/repeat.js";
import { api, components, confirm, installed, navigate, place, subscribe } from "/m/ui/client.js";

import { keyIn, listState, noSuchProject, NOTHING_OPEN, parseRoute, pathToWrite, READING_AFTER_MS } from "./address.ts";
import type { Address, ListState } from "./address.ts";
import { FACTS_COMPONENT, FACTS_EVENT } from "./facts.ts";
import type { Given } from "./facts.ts";
import { CHANGED, Feed, parseKinds, parseListing, parseOne, parseRemoveStatus, parseWritten } from "./feed.ts";
import type { Kinds, Listed, Listing, Project, RemoveStatus } from "./feed.ts";
import { factsModules, FirstOrder, givenOf, HeldReports, presentModules } from "./frame.ts";
import { HOLD_MS, JOIN, listView, rowAttributes } from "./list-model.ts";
import type { ListView, ProjectRow } from "./list-model.ts";
import { emptySentence, NO_PROJECTS, openedRows } from "./opened-model.ts";
import type { OpenedRow } from "./opened-model.ts";
import { PANEL_WIDTH_PX } from "./panel.ts";
import type { PanelAsk } from "./panel.ts";
import { BASE } from "./styles.ts";
import {
  actedOf, actSaid, agentBody, clearLeadBody, editBody, editing, kindChoices, leadBody, MAY_HAVE_ACTED, removedSaid, removeSheet, removeStatusPath, reread, STALE_LINE,
} from "./views.ts";
import type { EditForm, Editing, Said } from "./views.ts";

const TAG = "moxi-projects-page";

/** The list's words the page draws itself. */
const ADD_A_PROJECT = "Add a project";
const CHANGE_FOLDER = "Change folder";
const EDIT = "Edit";
const REMOVE = "Remove";
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

/** The window's width from which the panel moves the column; under it the panel lies over
 * the page. */
const SIDE_FROM_PX = 1100;
/** Half of the column's width. */
const HALF_COLUMN_PX = 340;

// The list is the Sessions page's rows under a dim count at the left, at its width; no
// accent and no warning colour on it. What is not yet drawn anew keeps its old look.
const STYLES = [BASE, css`
  :host { max-width: 680px; margin: 0 auto; padding: 4px 0 16px; font-size: 14px; line-height: 20px; }
  /* Beside the panel, where the window has the room, the column stands at the centre of
     what the panel leaves. */
  @media (min-width: ${unsafeCSS(SIDE_FROM_PX)}px) {
    :host([data-side]) { margin-left: calc(50% - ${unsafeCSS(HALF_COLUMN_PX + PANEL_WIDTH_PX / 2)}px); }
  }
  h3 { margin: 0; font-size: 1em; }
  .bar { display: flex; flex-wrap: wrap; align-items: center; gap: var(--moxi-gap); min-width: 0; }

  p.state, p.foot { margin: 16px 0 0; padding: 0 2px; overflow-wrap: anywhere; }
  p.state.dim, p.foot { color: var(--moxi-text-dim); }
  /* The last list kept while projects' page server isn't answering: every word dim, the
     rows' own included, since each row draws its words in the text token. */
  .grps.kept { --moxi-text: var(--moxi-text-dim); color: var(--moxi-text-dim); }
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
  /* The opened project: one labelled row each, the label dim at the left, the link dim at
     the right; heavy words in the text's own colour, only heavier. */
  .opened { display: grid; gap: 6px; min-width: 0; }
  .orow { display: grid; grid-template-columns: 96px minmax(0, 1fr) auto; gap: 2px 14px; align-items: baseline;
          min-width: 0; }
  .orow .ok { color: var(--moxi-text-dim); }
  .orow .ol { min-width: 0; overflow-wrap: anywhere; }
  .orow .ol p { margin: 0; }
  .orow .ol strong { font-weight: 600; }
  .orow .oa { color: var(--moxi-text-dim); text-decoration: none; white-space: nowrap; }
  .orow .oa:hover { text-decoration: underline; }
  .orow button.oa { padding: 0; margin: 0; border: 0; background: none; font: inherit; cursor: pointer; color: var(--moxi-text); }
  .foot { display: flex; gap: 14px; }
  .foot button { padding: 0; margin: 0; border: 0; background: none; font: inherit; cursor: pointer;
                 color: var(--moxi-text-dim); }
  .foot button[aria-pressed="true"] { color: var(--moxi-text); }
  @media (max-width: 720px) {
    .orow { grid-template-columns: minmax(0, 1fr) auto; }
    .orow .ok { grid-column: 1 / -1; font-size: 13px; }
  }
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
  /** The key of the project that is open, as stored. */
  #open: string | null = null;
  /** What the open project was opened for: its address's piece. */
  #openFor: "open" | "edit" | "remove" = "open";
  /** `Add a project` was pressed, or the address said `add`: the add is what is open, in
   * the panel, which opens with this. */
  #adding: PanelAsk | null = null;
  /** The route given, opened once the list is read; null once it is. */
  #pending: Address | null = null;
  /** The key of an address that named no project, said at the list's foot. */
  #unknown: string | null = null;
  /** The opened row takes the view once drawn. */
  #scrollTo: "open" | null = null;
  /** When the page was connected, for the first read's `Reading…`. */
  #since = Date.now();
  #readingTimer: ReturnType<typeof setTimeout> | null = null;
  #edit: Editing | null = null;
  #lead = "";
  #agent = "";
  /** An act is being sent, or the confirm sheet is open. */
  #sending = false;
  #told: Told | null = null;

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
    this.#readAll();
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
    this.#since = Date.now();
    this.#readingTimer = setTimeout(() => this.requestUpdate(), READING_AFTER_MS);
    this.#pending = parseRoute(this.route);
    this.#list = new Feed<Listing>(() => api("projects", "api/projects"), parseListing, () => this.#listed());
    this.#kinds = new Feed<Kinds>(() => api("projects", "api/kinds"), parseKinds, changed);
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
    for (const feed of [this.#list, this.#kinds]) feed?.close();
    this.#list = this.#kinds = null;
    this.#stop?.();
    this.#stop = null;
    for (const timer of [this.#holdTimer, this.#readingTimer]) if (timer !== null) clearTimeout(timer);
    this.#holdTimer = this.#readingTimer = null;
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
  }

  protected willUpdate(changed: Map<PropertyKey, unknown>): void {
    if (changed.has("route") && this.isConnected) {
      this.#pending = parseRoute(this.route);
      this.#openPending();
    }
  }

  /** The list was read: the facts components are given what changed, an open project
   * that is no longer listed closes, the open project's edit form takes in what was read,
   * and a route given before the list was read is opened. */
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
        this.#close();
        this.#write();
      } else if (this.#edit !== null) {
        this.#edit = reread(this.#edit, open);
      }
    }
    this.#openPending();
    this.#seat();
    this.requestUpdate();
  }

  // -- the address ---------------------------------------------------------------------------

  /** What is open, as an address. */
  #address(): Address {
    if (this.#open !== null) return { piece: this.#openFor, key: this.#open };
    return this.#adding !== null ? { piece: "add" } : NOTHING_OPEN;
  }

  /** Puts what is open in the browser's address, rewriting the entry the page is on. */
  #write(): void {
    const path = pathToWrite(this.#address(), location.pathname);
    if (path !== null) history.replaceState(history.state, "", path);
  }

  /** Opens the route given, once the list has been read. */
  #openPending(): void {
    const address = this.#pending;
    const list = this.#list;
    if (address === null || list?.value == null || list.failure !== null) return;
    this.#pending = null;
    this.#unknown = null;
    if (address.piece === "list") {
      this.#close();
    } else if (address.piece === "add") {
      this.#close();
      this.#adding = {};
    } else {
      const key = keyIn(list.value.projects, address.key);
      const project = key === null ? null : this.#find(key);
      if (project === null) {
        this.#close();
        this.#unknown = address.key;
      } else {
        this.#openProject(project, address.piece);
        this.#scrollTo = "open";
        if (address.piece === "edit") this.#focusRoot = true;
      }
    }
    this.#write();
    this.requestUpdate();
  }

  /** Nothing is open. */
  #close(): void {
    this.#setOpen(null);
    this.#edit = null;
    this.#openFor = "open";
    this.#adding = null;
  }

  /** `project` is open, for `piece`; its edit form starts from it when it wasn't open. */
  #openProject(project: Listed, piece: "open" | "edit" | "remove"): void {
    if (this.#open !== project.key) {
      this.#setOpen(project.key);
      this.#edit = editing(project);
      this.#lead = "";
      this.#agent = "";
    }
    this.#openFor = piece;
    this.#adding = null;
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
    const state = listState(list, Date.now() - this.#since);
    if (state.state === "gone") return html`<p class="problem">projects is no longer installed.</p>`;
    return html`${this.#listPart(list, state)}
      ${this.#adding !== null ? html`<moxi-projects-panel .ask=${this.#adding} .listing=${list.value}
        .kinds=${this.#kinds?.value ?? null} ?hidden=${!state.acts}
        @moxi-projects-panel-close=${() => this.#closePanel()}></moxi-projects-panel>` : nothing}
      ${this.#host}`;
  }

  #listPart(list: Feed<Listing>, state: ListState) {
    const told = this.#told;
    const loose = told !== null && (told.key === null || this.#find(told.key) === null) ? told.said : null;
    const listing = state.rows ? list.value : null;
    const reports = this.#reports.reports();
    const placed = this.#modules === undefined ? null : this.#facts.size + this.#placing.size;
    const held = this.#first.held(placed, this.#reports.reported, Date.now());
    const open = state.acts ? this.#open : null;
    return html`<section data-part="list" data-state=${state.state}>
      ${state.line !== null ? html`<p class="state ${state.state === "loading" ? "dim" : ""}"
        data-line=${state.state === "loading" ? "reading" : "failed"} role="status">${state.line}</p>` : nothing}
      ${loose !== null ? this.#saidPart(loose) : nothing}
      ${listing === null ? nothing : listing.projects.length === 0 ? state.acts ? html`<div class="first" data-part="empty">
          <p data-line="empty">${NO_PROJECTS}</p><p data-line="sentence">${emptySentence(reports)}</p>
          <button type="button" class="x" data-action="add" @click=${() => this.#add()}>${ADD_A_PROJECT}</button></div>` : nothing
        : this.#rows(listing, listView(listing.projects, { home: listing.home, reports, open, now: Date.now() / 1000 },
                                       { find: this.#typed, kind: this.#kind, held, previous: this.#order }), state.acts)}
      ${this.#unknown !== null && state.acts ? html`<p class="foot" data-line="unknown">${noSuchProject(this.#unknown)}</p>`
        : nothing}
    </section>`;
  }

  /** The rows, with their acts when `acts`; without, the last list kept, dim. */
  #rows(listing: Listing, view: ListView, acts: boolean) {
    this.#order = view.order;
    const byKey = new Map(listing.projects.map((project) => [project.key, project]));
    const row = (item: ProjectRow) => this.#row(byKey.get(item.key) as Listed, item, acts);
    return html`${view.long ? this.#findPart(view) : nothing}
      <div class=${acts ? "grps" : "grps kept"}>
        <section class="grp" data-part="projects"><div class="gk"><span data-line="count">${view.count}</span>${acts
          ? html`<small><button type="button" data-action="add" @click=${() => this.#add()}>${ADD_A_PROJECT}</button></small>`
          : nothing}</div>
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

  #row(project: Listed, row: ProjectRow, acts: boolean) {
    const open = row.act === "Close";
    if (!acts) {
      return html`<moxi-ui-row data-key=${row.key} ${attributes({ ...rowAttributes(row), toggles: null })}></moxi-ui-row>`;
    }
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

  /** `Open` or `Close`, or the row's own line: the row opens, or closes, and the address
   * says so. */
  #toggle(project: Listed): void {
    if (this.#open === project.key) this.#close();
    else this.#openProject(project, "open");
    this.#unknown = null;
    if (this.#told?.key !== null && this.#told?.key !== this.#open) this.#told = null;
    this.#write();
    this.requestUpdate();
  }

  /** `Change folder`: the project opens for its edit, with its folder field focused. */
  #changeFolder(project: Listed): void {
    this.#openProject(project, "edit");
    this.#unknown = null;
    this.#focusRoot = true;
    this.#write();
    this.requestUpdate();
  }

  /** `Edit` in the opened project's foot: it is open for its edit, with its folder field
   * focused. */
  #editOpen(project: Listed): void {
    this.#changeFolder(project);
  }

  /** `Remove` in the opened project's foot: it is open for its remove. */
  #removeOpen(project: Listed): void {
    this.#openProject(project, "remove");
    this.#write();
    this.requestUpdate();
  }

  /** `Add a project`: the add is what is open, in the panel. */
  #add(): void {
    this.#close();
    this.#unknown = null;
    this.#adding = {};
    this.#write();
    this.requestUpdate();
  }

  /** The panel's `Close`, `Cancel` or Escape: nothing is open, and the focus goes back to
   * `Add a project`. */
  #closePanel(): void {
    this.#adding = null;
    this.#write();
    this.requestUpdate();
    void this.updateComplete.then(() => this.renderRoot.querySelector<HTMLElement>('[data-action="add"]')?.focus());
  }

  /** The opened project: its labelled rows, then the foot, `Edit` and `Remove`. Until the
   * panel and the remove step, `Edit` shows the edit form, the lead and the agents under
   * the foot, and `Remove` the remove through the shell's confirm sheet. */
  #detail(project: Listed) {
    const told = this.#told?.key === project.key ? this.#told.said : null;
    const listing = this.#list?.value;
    const rows = openedRows(project, { home: listing?.home ?? null, notesInstalled: listing?.notes_installed ?? false,
                                       reports: this.#reports.reports(), installed: presentModules(this.#modules) });
    return html`<div class="detail">
      <div class="opened" data-part="opened">${rows.map((item) => this.#openedRow(project, item))}</div>
      <div class="foot" data-part="foot">
        <button type="button" data-action="edit" aria-pressed=${this.#openFor === "edit" ? "true" : "false"}
          @click=${() => this.#editOpen(project)}>${EDIT}</button>
        <button type="button" data-action="remove" aria-pressed=${this.#openFor === "remove" ? "true" : "false"}
          @click=${() => this.#removeOpen(project)}>${REMOVE}</button>
      </div>
      ${told !== null ? this.#saidPart(told) : nothing}
      ${this.#openFor === "edit" ? this.#editParts(project) : nothing}
      ${this.#openFor === "remove" ? html`<div class="part" data-part="remove"><div class="bar">
        <button type="button" data-action="remove-sheet" ?disabled=${this.#sending}
          @click=${() => void this.#remove(project)}>Remove…</button>
        <span class="dim">Takes it off the list; its files stay.</span>
      </div></div>` : nothing}
    </div>`;
  }

  /** One labelled row: the label dim at the left, its lines, and at the right its link or
   * `Change folder`. */
  #openedRow(project: Listed, item: OpenedRow) {
    const link = item.link;
    return html`<div class="orow" data-label=${item.label}><span class="ok">${item.label}</span>
      <div class="ol">${item.lines.map((one) => html`<p>${one.parts.map((part, n) => html`${n > 0 ? JOIN : ""}${
        part.heavy ? html`<strong>${part.text}</strong>` : part.text}`)}</p>`)}</div>
      ${link !== null ? html`<a class="oa" href=${`/m/${link.module}/${link.route}`} data-action="link"
          @click=${(event: MouseEvent) => {
            if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            event.preventDefault();
            navigate(link.module, link.route);
          }}>${link.text}</a>`
        : item.changeFolder ? html`<button type="button" class="oa fix" data-action="change-folder"
          @click=${() => this.#changeFolder(project)}>${CHANGE_FOLDER}</button>` : nothing}
    </div>`;
  }

  /** The edit form, the lead and the agents, until the panel is drawn. */
  #editParts(project: Listed) {
    const busy = this.#sending;
    const edit = this.#edit ?? editing(project);
    const form = edit.form;
    const notes = installed("notes");
    const kinds = this.#kinds?.value ?? null;
    const changes = editBody(edit.base, form, { notes });
    return html`
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
    if (this.#scrollTo !== null) {
      const target = this.#open === null ? null
        : this.renderRoot.querySelector(`moxi-ui-row[data-key="${CSS.escape(this.#open)}"]`);
      this.#scrollTo = null;
      target?.scrollIntoView({ block: "nearest" });
    }
    const panel = this.renderRoot.querySelector<HTMLElement>("moxi-projects-panel");
    this.toggleAttribute("data-side", panel !== null && !panel.hidden);
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, ProjectsPage);
