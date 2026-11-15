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
// - Folders that are not projects: under the list, `Not a project` (`Worked in this week`
//   when there is no project), each folder an activity source said was worked in, one
//   line, newest first, with `Add` at the right, which opens the Add panel with the folder
//   and the name filled. Five, then how many more, with `Show`.
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
//   An opened project's `Edit`, its `Change folder` and the `edit/<key>` address open the
//   Edit panel over it, its folder field focused; closing it leaves the project open.
// - The remove step: an opened project's `Remove`, and the `remove/<key>` address, read
//   `/api/remove-check` and draw under the foot either what goes and what stays, with
//   `Remove from Moxi`, or each thing that refuses it now, with the button off. The step
//   is the confirmation: the press sends `/api/remove` with a `job` minted for it, and is
//   answered at once. A removed project's row becomes one line with `Add it back`, which
//   adds it again as the page held it. Refused since the check, the step is drawn again
//   from a fresh check; refused before the remove was tried, its text shows under the
//   button. With no answer, the outcome kept under the `job` is read once, else the page
//   says it may have been removed. While `/api/where` says a change would be refused, its
//   note shows dim above the button.
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
import { keyed } from "lit/directives/keyed.js";
import { repeat } from "lit/directives/repeat.js";
import { api, components, installed, navigate, place, subscribe } from "/m/ui/client.js";

import { keyIn, listState, noSuchProject, NOTHING_OPEN, parseRoute, pathToWrite, READING_AFTER_MS } from "./address.ts";
import type { Address, ListState } from "./address.ts";
import { FACTS_COMPONENT, FACTS_EVENT } from "./facts.ts";
import type { Given } from "./facts.ts";
import { CHANGED, Feed, parseAdded, parseKinds, parseListing, parseRemoveCheck, parseRemoveStatus, parseWhere } from "./feed.ts";
import type { Kinds, Listed, Listing, Refusal } from "./feed.ts";
import { factsModules, FirstOrder, givenOf, HeldReports, presentModules } from "./frame.ts";
import { HOLD_MS, JOIN, listView, rowAttributes } from "./list-model.ts";
import type { ListView, ProjectRow } from "./list-model.ts";
import {
  addBackBody, emptySentence, folderAsk, folderGroup, GOES, MAY_BE_REMOVED, mintJob, NO_PROJECTS, NOTHING_CHANGED,
  openedRows, pressedOf, refusedRows, refusedTitle, removedIndex, removedLine, removeStep,
} from "./opened-model.ts";
import type { FolderGroup, FolderRow, Pressed } from "./opened-model.ts";
import type { OpenedRow } from "./opened-model.ts";
import { PANEL_WIDTH_PX } from "./panel.ts";
import type { PanelAsk } from "./panel.ts";
import { BASE } from "./styles.ts";
import { MAY_BE_ADDED } from "./panel-model.ts";
import { actedOf } from "./views.ts";

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
const GOES_LABEL = "Goes";
const STAYS_LABEL = "Stays";
const REMOVE_FROM_MOXI = "Remove from Moxi";
const CANCEL = "Cancel";
const CLOSE = "Close";
const ADD_IT_BACK = "Add it back";
const ADD = "Add";

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
  p.said { margin: 16px 0 0; padding: 0 2px; overflow-wrap: anywhere; }
  /* A removed project's one line, in its place: dim, with its act in the text colour. */
  .gone { padding: 9px 0; color: var(--moxi-text-dim); overflow-wrap: anywhere; }
  .gone button { margin-left: 14px; color: var(--moxi-text); }
  .gone p { margin: 2px 0 0; }

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
  /* The remove step, under the foot and in line with the rows' words: its question
     heavier, its rows labelled as the opened project's are, then its buttons. */
  .step { display: grid; gap: 6px; margin: 4px 0 2px 110px; padding-top: 10px; border-top: 1px solid var(--moxi-border);
          min-width: 0; }
  .step .orow { grid-template-columns: 76px minmax(0, 1fr) auto; }
  .step .q { margin: 0; font-weight: 600; overflow-wrap: anywhere; }
  .step p.dim { margin: 0; color: var(--moxi-text-dim); overflow-wrap: anywhere; }
  .step .then { padding-left: 90px; }
  .step .btns { display: flex; align-items: center; gap: 18px; padding: 4px 0 2px; }
  .step button.btn { padding: 5px 13px; border: 1px solid var(--moxi-border-strong); border-radius: 7px; background: none;
                     color: var(--moxi-text); font: inherit; cursor: pointer; }
  .step button.btn:hover:not(:disabled) { background: var(--moxi-bg-hover); }
  .step button.btn:disabled { color: var(--moxi-text-dim); border-color: var(--moxi-border); cursor: default; }
  .step button.lnk { padding: 0; border: 0; background: none; color: var(--moxi-text-dim); font: inherit; cursor: pointer; }
  @media (max-width: 720px) {
    .orow { grid-template-columns: minmax(0, 1fr) auto; }
    .orow .ok { grid-column: 1 / -1; font-size: 13px; }
    .step { margin-left: 0; }
    .step .orow { grid-template-columns: minmax(0, 1fr) auto; }
    .step .then { padding-left: 0; }
  }
  .part { display: grid; gap: 6px; min-width: 0; padding-top: var(--moxi-gap); border-top: 1px solid var(--moxi-border); }

  @media (max-width: 720px) {
    :host { padding-top: 10px; }
    .grp { display: block; padding: 4px 0 6px; }
    /* The count above the rows, with Add a project beside it at the right. */
    .grp .gk { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; padding-top: 9px;
               font-size: 13px; }
    .grp .gk small { margin-top: 0; }
  }
`];

/** The open project's remove step. */
interface Step {
  key: string;
  /** `/api/remove-check`'s refusals; null until it is read. */
  refusals: Refusal[] | null;
  /** The check couldn't be read: what its answer said. */
  failed: string | null;
  /** `/api/where`'s note: what a change would be refused with now. */
  note: string | null;
  sending: boolean;
  /** Under the button: a refusal's text, or that it may have been removed. */
  said: string | null;
}

/** A project removed from the page, drawn as one line in its place until it is back. */
interface Removed {
  project: Listed;
  /** The key drawn before it when it went. */
  after: string | null;
  sending: boolean;
  said: string | null;
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
  #step: Step | null = null;
  /** Counts the steps opened, so an answer for one left behind is dropped. */
  #steps = 0;
  #removed: Removed | null = null;
  /** A line at the list's top: what a step said of a project no longer listed. */
  #told: string | null = null;

  // The list's choices: the find field, the kind filter, the `Earlier` fold shown, and the
  // keys in the order last drawn, so the open row keeps its place.
  #typed = "";
  #kind: string | null = null;
  #earlierShown = false;
  /** `Show` was pressed under the folders that are not projects. */
  #foldersShown = false;
  #order: string[] = [];

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
   * that is no longer listed closes, and a route given before the list was read is
   * opened. */
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
        // What its step said is said at the list's top.
        if (this.#step?.said != null) this.#told = this.#step.said;
        this.#close();
        this.#write();
      }
    }
    const removed = this.#removed;
    if (removed !== null && keyIn(projects, removed.project.key) !== null) this.#removed = null;
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
      this.#adding = { mode: "add" };
    } else {
      const key = keyIn(list.value.projects, address.key);
      const project = key === null ? null : this.#find(key);
      if (project === null) {
        this.#close();
        this.#unknown = address.key;
      } else {
        this.#openProject(project, address.piece);
        this.#scrollTo = "open";
      }
    }
    this.#write();
    this.requestUpdate();
  }

  /** Nothing is open. */
  #close(): void {
    this.#setOpen(null);
    this.#openFor = "open";
    this.#adding = null;
    this.#step = null;
  }

  /** `project` is open, for `piece`; for its remove, the step is read anew. */
  #openProject(project: Listed, piece: "open" | "edit" | "remove"): void {
    this.#setOpen(project.key);
    if (piece !== "remove") this.#step = null;
    else if (this.#openFor !== "remove" || this.#step?.key !== project.key) this.#openStep(project.key);
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

  // -- the remove step ----------------------------------------------------------------------

  /** Opens the step for `key`: what a remove would meet now, and where a change would be
   * refused, are read. */
  #openStep(key: string): void {
    this.#steps += 1;
    this.#step = { key, refusals: null, failed: null, note: null, sending: false, said: null };
    void this.#readCheck(this.#steps);
    void this.#readWhere(this.#steps);
  }

  /** The step `mine`, while it is still the one open. */
  #stepOf(mine: number): Step | null {
    return mine === this.#steps ? this.#step : null;
  }

  async #readCheck(mine: number): Promise<void> {
    const key = this.#stepOf(mine)?.key;
    if (key === undefined) return;
    let answer;
    try {
      answer = await api("projects", `api/remove-check?key=${encodeURIComponent(key)}`);
    } catch {
      answer = { status: 0, data: null };
    }
    const step = this.#stepOf(mine);
    if (step === null) return;
    const check = actedOf(answer.status, answer.data, parseRemoveCheck);
    this.#step = check.outcome === "done" ? { ...step, refusals: check.value.refusals, failed: null }
      : { ...step, failed: check.text };
    this.requestUpdate();
  }

  async #readWhere(mine: number): Promise<void> {
    let answer;
    try {
      answer = await api("projects", "api/where");
    } catch {
      return;
    }
    const step = this.#stepOf(mine);
    if (step === null) return;
    this.#step = { ...step, note: answer.status === 200 ? parseWhere(answer.data)?.note ?? null : null };
    this.requestUpdate();
  }

  /** `Remove from Moxi`: the remove is sent under a `job` of its own, and its answer drawn. */
  async #press(project: Listed): Promise<void> {
    const mine = this.#steps;
    const step = this.#stepOf(mine);
    if (step === null || step.sending || step.refusals === null || step.refusals.length > 0) return;
    this.#step = { ...step, sending: true, said: null };
    this.requestUpdate();
    const job = mintJob();
    const pressed = await this.#sendRemove(project.key, job);
    if (pressed.outcome === "removed") {
      const index = this.#order.indexOf(project.key);
      this.#removed = { project, after: index > 0 ? this.#order[index - 1] : null, sending: false, said: null };
      if (this.#open === project.key) {
        this.#close();
        this.#write();
      }
      this.#changed();
    } else if (pressed.outcome === "held") {
      // Something changed since the check: the step is drawn from a fresh one.
      await this.#readCheck(mine);
      const now = this.#stepOf(mine);
      if (now !== null) {
        const held = now.refusals !== null && now.refusals.length > 0;
        this.#step = { ...now, sending: false, said: held ? null : pressed.reason };
      }
    } else {
      const now = this.#stepOf(mine);
      if (now !== null) {
        this.#step = { ...now, sending: false, said: pressed.outcome === "refused" ? pressed.text : MAY_BE_REMOVED };
      }
      if (pressed.outcome === "unknown") this.#changed();
    }
    this.requestUpdate();
  }

  /** Sends the remove of `key` under `job`; with no answer, reads the outcome kept under
   * `job` once. */
  async #sendRemove(key: string, job: string): Promise<Pressed> {
    let answer;
    try {
      answer = await api("projects", "api/remove", { key, job });
    } catch {
      answer = { status: 0, data: null };
    }
    const acted = actedOf(answer.status, answer.data, parseRemoveStatus);
    if (acted.outcome === "done") return pressedOf(acted.value);
    if (acted.outcome === "refused") return { outcome: "refused", text: acted.text };
    try {
      const kept = await api("projects", `api/remove-status?job=${job}`);
      const status = kept.status === 200 ? parseRemoveStatus(kept.data) : null;
      if (status !== null) return pressedOf(status);
    } catch {
      // Nothing more can be read: it may have been removed.
    }
    return { outcome: "unknown" };
  }

  /** `Add it back`: the removed project is added again as the page held it. */
  async #addBack(): Promise<void> {
    const removed = this.#removed;
    if (removed === null || removed.sending) return;
    this.#removed = { ...removed, sending: true, said: null };
    this.requestUpdate();
    let answer;
    try {
      answer = await api("projects", "api/add",
                         addBackBody(removed.project, { notesInstalled: this.#list?.value?.notes_installed ?? false }));
    } catch {
      answer = { status: 0, data: null };
    }
    const acted = actedOf(answer.status, answer.data, parseAdded);
    if (this.#removed?.project.key === removed.project.key) {
      this.#removed = acted.outcome === "done" ? null
        : { ...this.#removed, sending: false, said: acted.outcome === "refused" ? acted.text : MAY_BE_ADDED };
    }
    if (acted.outcome !== "refused") this.#changed();
    this.requestUpdate();
  }

  // -- rendering ---------------------------------------------------------------------------

  render() {
    const list = this.#list;
    if (list === null) return nothing;
    const state = listState(list, Date.now() - this.#since);
    if (state.state === "gone") return html`<p class="problem">projects is no longer installed.</p>`;
    const ask = this.#panelAsk();
    // A panel of its own for each thing it opens for: what it holds is that thing's alone.
    return html`${this.#listPart(list, state)}
      ${ask !== null ? keyed(JSON.stringify(ask), html`<moxi-projects-panel .ask=${ask} .listing=${list.value}
        .kinds=${this.#kinds?.value ?? null} ?hidden=${!state.acts}
        @moxi-projects-panel-close=${() => this.#closePanel()}></moxi-projects-panel>`) : nothing}
      ${this.#host}`;
  }

  /** What the panel is open for: the add, or the open project's edit; null while it is
   * closed. */
  #panelAsk(): PanelAsk | null {
    if (this.#adding !== null) return this.#adding;
    return this.#open !== null && this.#openFor === "edit" ? { mode: "edit", key: this.#open } : null;
  }

  #listPart(list: Feed<Listing>, state: ListState) {
    const told = this.#told;
    const listing = state.rows ? list.value : null;
    const reports = this.#reports.reports();
    const placed = this.#modules === undefined ? null : this.#facts.size + this.#placing.size;
    const held = this.#first.held(placed, this.#reports.reported, Date.now());
    const open = state.acts ? this.#open : null;
    const now = Date.now() / 1000;
    const folders = listing === null ? null : folderGroup(reports, {
      home: listing.home, now, hasProjects: listing.projects.length > 0, showAll: this.#foldersShown,
      roots: listing.projects.map((project) => project.root) });
    return html`<section data-part="list" data-state=${state.state}>
      ${state.line !== null ? html`<p class="state ${state.state === "loading" ? "dim" : ""}"
        data-line=${state.state === "loading" ? "reading" : "failed"} role="status">${state.line}</p>` : nothing}
      ${told !== null && state.acts ? html`<p class="said" data-line="said" role="status">${told}</p>` : nothing}
      ${listing === null ? nothing : listing.projects.length === 0 ? state.acts ? html`${
          this.#removed !== null ? this.#removedPart(this.#removed) : nothing}<div class="first" data-part="empty">
          <p data-line="empty">${NO_PROJECTS}</p><p data-line="sentence">${emptySentence(reports)}</p>
          <button type="button" class="x" data-action="add" @click=${() => this.#add()}>${ADD_A_PROJECT}</button></div>
          ${folders !== null ? html`<div class="grps">${this.#foldersPart(folders, true)}</div>` : nothing}` : nothing
        : this.#rows(listing, listView(listing.projects, { home: listing.home, reports, open, now },
                                       { find: this.#typed, kind: this.#kind, held, previous: this.#order }), folders,
                     state.acts)}
      ${this.#unknown !== null && state.acts ? html`<p class="foot" data-line="unknown">${noSuchProject(this.#unknown)}</p>`
        : nothing}
    </section>`;
  }

  /** The rows, then the folders that are not projects, with their acts when `acts`;
   * without, the last list kept, dim. */
  #rows(listing: Listing, view: ListView, folders: FolderGroup | null, acts: boolean) {
    this.#order = view.order;
    const byKey = new Map(listing.projects.map((project) => [project.key, project]));
    const row = (item: ProjectRow) => this.#row(byKey.get(item.key) as Listed, item, acts);
    // A project removed here keeps a line where its row was.
    const removed = acts ? this.#removed : null;
    const shown: (ProjectRow | Removed)[] = [...view.rows];
    if (removed !== null) shown.splice(removedIndex(view.rows.map((item) => item.key), removed.after), 0, removed);
    const drawn = (item: ProjectRow | Removed) => ("project" in item ? this.#removedPart(item) : row(item));
    return html`${view.long ? this.#findPart(view) : nothing}
      <div class=${acts ? "grps" : "grps kept"}>
        <section class="grp" data-part="projects"><div class="gk"><span data-line="count">${view.count}</span>${acts
          ? html`<small><button type="button" data-action="add" @click=${() => this.#add()}>${ADD_A_PROJECT}</button></small>`
          : nothing}</div>
          <div class="gl">${repeat(shown, (item) => ("project" in item ? `\n${item.project.key}` : item.key), drawn)}</div></section>
        ${view.earlierLine !== null ? html`<section class="grp" data-part="earlier"><div class="gk">${EARLIER}</div>
          <div class="gl"><div class="one"><span data-line="earlier">${view.earlierLine}</span><button type="button"
            data-action="earlier" aria-expanded=${this.#earlierShown ? "true" : "false"}
            @click=${() => { this.#earlierShown = !this.#earlierShown; this.requestUpdate(); }}>${
            this.#earlierShown ? HIDE : SHOW}</button></div>
            ${this.#earlierShown ? repeat(view.earlier, (item) => item.key, row) : nothing}</div></section>` : nothing}
        ${folders !== null ? this.#foldersPart(folders, acts) : nothing}
      </div>`;
  }

  /** The folders worked in that are not projects: one line each, `Add` at the right, and
   * how many more there are, with `Show` while some are held back. */
  #foldersPart(group: FolderGroup, acts: boolean) {
    return html`<section class="grp" data-part="folders"><div class="gk" data-line="folders">${group.label}</div>
      <div class="gl">${repeat(group.rows, (item) => item.folder, (item) => this.#folderRow(item, acts))}
        ${group.more !== null ? html`<div class="one"><span data-line="more-folders">${group.more}</span>${
          group.canShow && acts ? html`<button type="button" data-action="show-folders"
            @click=${() => { this.#foldersShown = true; this.requestUpdate(); }}>${SHOW}</button>` : nothing}</div>`
          : nothing}</div></section>`;
  }

  #folderRow(item: FolderRow, acts: boolean) {
    return html`<moxi-ui-row data-folder=${item.folder} ${attributes({ name: item.name, where: item.where, fact: item.fact })}>
      ${acts ? html`<button type="button" slot="acts-one" class="fix" data-action="add-folder"
        @click=${() => this.#addFolder(item)}>${ADD}</button>` : nothing}
    </moxi-ui-row>`;
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
        @click=${() => this.#editOpen(project)}>${CHANGE_FOLDER}</button>` : nothing}
      ${open ? this.#detail(project) : nothing}
    </moxi-ui-row>`;
  }

  /** A removed project's one line, with `Add it back`, and what that came to. */
  #removedPart(removed: Removed) {
    return html`<div class="gone" data-part="removed" data-key=${removed.project.key}>
      <span data-line="removed">${removedLine(removed.project.key)}</span><button type="button" data-action="add-back"
        ?disabled=${removed.sending} @click=${() => void this.#addBack()}>${ADD_IT_BACK}</button>
      ${removed.said !== null ? html`<p data-line="said" role="status">${removed.said}</p>` : nothing}
    </div>`;
  }

  /** `Open` or `Close`, or the row's own line: the row opens, or closes, and the address
   * says so. */
  #toggle(project: Listed): void {
    if (this.#open === project.key) this.#close();
    else this.#openProject(project, "open");
    this.#unknown = null;
    this.#told = null;
    this.#write();
    this.requestUpdate();
  }

  /** `Edit` in the opened project's foot, or `Change folder`: the project opens with the
   * Edit panel over it, its folder field focused. */
  #editOpen(project: Listed): void {
    this.#openProject(project, "edit");
    this.#unknown = null;
    this.#write();
    this.requestUpdate();
    // A panel already open for it keeps its values, and takes the focus again.
    void this.updateComplete.then(() => this.renderRoot.querySelector<HTMLElement>("moxi-projects-panel")?.focus());
  }

  /** `Remove` in the opened project's foot: it is open for its remove. */
  #removeOpen(project: Listed): void {
    this.#openProject(project, "remove");
    this.#unknown = null;
    this.#told = null;
    this.#write();
    this.requestUpdate();
  }

  /** `Add a project`: the add is what is open, in the panel. */
  #add(): void {
    this.#close();
    this.#unknown = null;
    this.#told = null;
    this.#adding = { mode: "add" };
    this.#write();
    this.requestUpdate();
  }

  /** A folder row's `Add`: the add is open, with that folder and its name filled. The
   * address says `add`: what is filled lives only in the panel. */
  #addFolder(row: FolderRow): void {
    this.#close();
    this.#unknown = null;
    this.#told = null;
    this.#adding = folderAsk(row);
    this.#write();
    this.requestUpdate();
  }

  /** The panel's `Close`, `Cancel` or Escape, or a save that saved everything. The add
   * closes to nothing open, the focus back on `Add a project`; the edit to its project
   * open, the focus on its `Edit`. */
  #closePanel(): void {
    let focus = '[data-action="add"]';
    if (this.#adding !== null) {
      this.#adding = null;
    } else if (this.#open !== null && this.#openFor === "edit") {
      this.#openFor = "open";
      focus = `moxi-ui-row[data-key="${CSS.escape(this.#open)}"] [data-part="foot"] [data-action="edit"]`;
    }
    this.#write();
    this.requestUpdate();
    void this.updateComplete.then(() => this.renderRoot.querySelector<HTMLElement>(focus)?.focus());
  }

  /** The opened project: its labelled rows, then the foot, `Edit` and `Remove`, and under
   * it the remove step while it is open for its remove. */
  #detail(project: Listed) {
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
      ${this.#openFor === "remove" && this.#step?.key === project.key ? this.#stepPart(project, this.#step) : nothing}
    </div>`;
  }

  /** The remove step: nothing until the check is read; then what goes and what stays,
   * with `Remove from Moxi` and `Cancel`, or what refuses it now, with the button off and
   * `Close`. */
  #stepPart(project: Listed, step: Step) {
    if (step.refusals === null) {
      return step.failed === null ? nothing : html`<div class="step" data-part="remove" data-state="failed">
        <p class="dim" data-line="said" role="status">${step.failed}</p>
        <div class="btns"><button type="button" class="lnk" data-action="cancel"
          @click=${() => this.#leaveStep(project)}>${CLOSE}</button></div></div>`;
    }
    const listing = this.#list?.value;
    const facts = { home: listing?.home ?? null, notesInstalled: listing?.notes_installed ?? false,
                    reports: this.#reports.reports(), installed: presentModules(this.#modules) };
    const refused = step.refusals.length > 0;
    const busy = step.sending;
    const tail = html`
      ${step.note !== null ? html`<p class="dim then" data-line="note">${step.note}</p>` : nothing}
      <div class="btns then">
        <button type="button" class="btn" data-action="remove-press" ?disabled=${refused || busy}
          @click=${() => void this.#press(project)}>${REMOVE_FROM_MOXI}</button>
        <button type="button" class="lnk" data-action="cancel" @click=${() => this.#leaveStep(project)}>${
          refused ? CLOSE : CANCEL}</button>
      </div>
      ${step.said !== null ? html`<p class="dim then" data-line="said" role="status">${step.said}</p>` : nothing}`;
    if (refused) {
      const rows = refusedRows(project, step.refusals, (module) => this.#pageTitle(module));
      return html`<div class="step" data-part="remove" data-state="refused">
        <p class="q" data-line="title">${refusedTitle(project.key)}</p>
        ${rows.map((item) => html`<div class="orow" data-label=${item.label}><span class="ok">${item.label}</span>
          <div class="ol">${item.lines.map((line) => html`<p>${line}</p>`)}</div>
          ${item.page !== null ? this.#pageLink(item.page, item.label)
            : item.edit ? html`<button type="button" class="oa" data-action="edit"
              @click=${() => this.#editOpen(project)}>${EDIT}</button>` : nothing}</div>`)}
        <p class="dim then" data-line="unchanged">${NOTHING_CHANGED}</p>
        ${tail}</div>`;
    }
    const words = removeStep(project, facts);
    return html`<div class="step" data-part="remove" data-state="ask">
      <p class="q" data-line="title">${words.title}</p>
      <div class="orow" data-label=${GOES_LABEL}><span class="ok">${GOES_LABEL}</span><div class="ol"><p>${GOES}</p></div></div>
      ${words.stays.length > 0 ? html`<div class="orow" data-label=${STAYS_LABEL}><span class="ok">${STAYS_LABEL}</span>
        <div class="ol">${words.stays.map((line) => html`<p>${line}</p>`)}</div></div>` : nothing}
      <p class="dim then" data-line="foot">${words.foot}</p>
      ${tail}</div>`;
  }

  /** A module's page title, or null when it has no page. */
  #pageTitle(module: string): string | null {
    if (!installed(module)) return null;
    return components(module).find((item) => item.name === "page")?.title ?? null;
  }

  /** A link to `module`'s page, at a row's right. */
  #pageLink(module: string, text: string, route = "") {
    return html`<a class="oa" href=${`/m/${module}/${route}`} data-action="link" @click=${(event: MouseEvent) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        navigate(module, route);
      }}>${text}</a>`;
  }

  /** The step's `Cancel` or `Close`: the project stays open, its step gone. */
  #leaveStep(project: Listed): void {
    this.#openProject(project, "open");
    this.#write();
    this.requestUpdate();
    void this.updateComplete.then(() => this.renderRoot.querySelector<HTMLElement>(
      `moxi-ui-row[data-key="${CSS.escape(project.key)}"] [data-part="foot"] [data-action="remove"]`)?.focus());
  }

  /** One labelled row: the label dim at the left, its lines, and at the right its link or
   * `Change folder`. */
  #openedRow(project: Listed, item: OpenedRow) {
    const link = item.link;
    return html`<div class="orow" data-label=${item.label}><span class="ok">${item.label}</span>
      <div class="ol">${item.lines.map((one) => html`<p>${one.parts.map((part, n) => html`${n > 0 ? JOIN : ""}${
        part.heavy ? html`<strong>${part.text}</strong>` : part.text}`)}</p>`)}</div>
      ${link !== null ? this.#pageLink(link.module, link.text, link.route)
        : item.changeFolder ? html`<button type="button" class="oa fix" data-action="change-folder"
          @click=${() => this.#editOpen(project)}>${CHANGE_FOLDER}</button>` : nothing}
    </div>`;
  }

  updated(): void {
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
