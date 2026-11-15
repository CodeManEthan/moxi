// The words and the bodies of the page's panel, worked out with no DOM so the Node tests
// can read them: what the Add panel says under its folder field, the name it fills from
// the folder, the `More` fold's line, the body `/api/add` is sent, and what an add came to;
// what the Edit panel holds, the requests a save sends and in what order, and what a save
// that stopped part way says.

import type { Acted } from "./views.ts";
import { DEFAULT_KIND, kindOf, warningLine } from "./views.ts";
import type { Added, Kinds, Listed, Project } from "./feed.ts";
import { joinNames } from "./opened-model.ts";
import { homeForm, kindPhrase } from "./words.ts";

/** How long the folder field waits after the last key before it reads `/api/folder`. */
export const FOLDER_WAIT_MS = 250;

/** How a typed path stands to the project `for_path` gives it. */
export type How = "root" | "inside" | "worktree";

/** `/api/folder`: what is at a typed path. */
export interface AtPath {
  /** The typed path with `~` taken as the page server's home, made normal; null for a
   * path the server can't take (relative, `~user`, `~` with no home), with every other
   * field. */
  path: string | null;
  exists: boolean | null;
  has_git: boolean | null;
  project: string | null;
  how: How | null;
  key: string | null;
  taken: boolean | null;
}

type Obj = Record<string, unknown>;
const isObj = (value: unknown): value is Obj => typeof value === "object" && value !== null && !Array.isArray(value);
const isStrOrNull = (value: unknown): value is string | null => value === null || typeof value === "string";
const isBoolOrNull = (value: unknown): value is boolean | null => value === null || typeof value === "boolean";
const HOWS: ReadonlySet<unknown> = new Set(["root", "inside", "worktree", null]);

/** `/api/folder`'s value, or null when it isn't one. */
export function parseAtPath(data: unknown): AtPath | null {
  if (!isObj(data) || !isStrOrNull(data.path) || !isBoolOrNull(data.exists) || !isBoolOrNull(data.has_git)
      || !isStrOrNull(data.project) || !HOWS.has(data.how) || !isStrOrNull(data.key)
      || !isBoolOrNull(data.taken)) return null;
  return { path: data.path, exists: data.exists, has_git: data.has_git, project: data.project, how: data.how as How | null,
           key: data.key, taken: data.taken };
}

/** The route that reads what is at `typed`. */
export function folderRoute(typed: string): string {
  return `api/folder?path=${encodeURIComponent(typed)}`;
}

/** An answer of `/api/folder`, with the field's value it was asked for. */
export interface Asked {
  typed: string;
  answer: AtPath;
}

/** The answer for the folder field's value as it stands, or null: an answer asked for a
 * value the field no longer holds is dropped. */
export function answerNow(asked: Asked | null, typed: string): AtPath | null {
  return asked !== null && asked.typed === typed ? asked.answer : null;
}

/**
 * The words under the folder field for `answer`: whether the folder exists, and is a git
 * repository; or that nothing is there yet; then how it stands to a project that holds
 * it. Empty for a path the server can't take, and for a part it couldn't read in time.
 */
export function folderSays(answer: AtPath): string {
  if (answer.path === null) return "";
  const said: string[] = [];
  if (answer.exists === true) {
    said.push("The folder exists.");
    if (answer.has_git === true) said.push("It is a git repository.");
  } else if (answer.exists === false) {
    said.push("No folder there yet.");
  }
  const key = answer.project;
  if (key !== null) {
    if (answer.how === "root") said.push(`That folder is ${key}'s already.`);
    else if (answer.how === "inside") said.push(`Inside ${key}, a project already.`);
    else if (answer.how === "worktree") said.push(`A worktree of ${key}, a project already.`);
  }
  return said.join(" ");
}

/** The words of the box that makes the folder, for kind `kind`: with `git init` for a
 * kind whose mint is `git`; null for `refuse`, and while the kind is unknown. */
export function createWords(kinds: Kinds | null, kind: string): string | null {
  const mint = kindOf(kinds, kind)?.mint;
  if (mint === "git") return "Create the folder and run git init";
  if (mint === "dir") return "Create the folder";
  return null;
}

/** The box's words while it is offered: only while the answer for the field as it stands
 * says nothing is there; else null. */
export function createOffered(answer: AtPath | null, kinds: Kinds | null, kind: string): string | null {
  return answer !== null && answer.path !== null && answer.exists === false ? createWords(kinds, kind) : null;
}

/** The line under a name filled from the folder. */
export const NAME_FILLED = "Filled from the folder. Change it now if you like: it can't be changed later.";
/** The line under a name a project already has. */
export const NAME_TAKEN = "That name is taken.";

/** Whether a project already has `name`, ignoring case. */
export function nameTaken(name: string, projects: readonly Listed[]): boolean {
  const wanted = name.trim().toLowerCase();
  return wanted !== "" && projects.some((project) => project.key.toLowerCase() === wanted);
}

/** What the Add panel holds, as typed. `named` is true once he has typed in the name;
 * until then it fills from the folder. */
export interface AddPanel {
  folder: string;
  name: string;
  named: boolean;
  kind: string;
  lead: string;
  notesRoot: string;
  create: boolean;
}

/** The empty Add panel, the folder and the name filled when given. */
export function addPanel({ folder = "", name = "" }: { folder?: string; name?: string } = {}): AddPanel {
  return { folder, name, named: false, kind: DEFAULT_KIND, lead: "", notesRoot: "", create: false };
}

/** The form once an answer for the field as it stands has come: the name it fills, until
 * he has typed one. */
export function filled(form: AddPanel, answer: AtPath): AddPanel {
  return form.named ? form : { ...form, name: answer.key ?? "" };
}

/** The form once he has typed `name`: his from then on, unless he empties it. */
export function named(form: AddPanel, name: string): AddPanel {
  return { ...form, name, named: name !== "" };
}

/** The `More` fold's line: the kind, who leads, and the notes folder when one is set. */
export function moreLine(form: AddPanel, { notesInstalled }: { notesInstalled: boolean }): string {
  const lead = form.lead.trim();
  const said = [kindPhrase(form.kind), lead === "" ? "no lead" : `${lead} leads`];
  if (notesInstalled && form.notesRoot.trim() !== "") said.push(`notes in ${form.notesRoot.trim()}`);
  return said.join(" · ");
}

/**
 * `/api/add`'s body for `form`. The folder and the notes folder are sent as typed, `~`
 * included, which the server takes as its home; an empty one is left out, so the kind's
 * module may give it. The notes folder goes only while notes is installed, and create
 * only while the box is offered (`canCreate`) and ticked.
 */
export function addPanelBody(form: AddPanel, { notesInstalled, canCreate }: { notesInstalled: boolean; canCreate: boolean }):
    Record<string, string | boolean> {
  const body: Record<string, string | boolean> = { key: form.name.trim() };
  if (form.folder !== "") body.root = form.folder;
  if (form.kind !== "") body.kind = form.kind;
  if (form.lead.trim() !== "") body.lead = form.lead.trim();
  if (notesInstalled && form.notesRoot !== "") body.notes_root = form.notesRoot;
  if (canCreate && form.create) body.create = true;
  return body;
}

/** What the panel says under its button after an act: its lines, and whether it was
 * refused (the panel keeps its values) or done. */
export interface PanelSaid {
  outcome: "done" | "refused" | "unknown";
  lines: string[];
}

/** The line an add whose answer was lost says. */
export const MAY_BE_ADDED = "It may have been added.";

/** What an add came to: the project and its folder in `~` form, then the answer's notes
 * and warnings; the refusal's own text; or that it may have been added. */
export function addedSaid(acted: Acted<Added>, home: string | null): PanelSaid {
  if (acted.outcome === "done") {
    const { project, notes, warnings } = acted.value;
    return { outcome: "done", lines: [`Added ${project.key} at ${homeForm(project.root, home)}.`, ...notes,
                                      ...warnings.map(warningLine)] };
  }
  if (acted.outcome === "refused") return { outcome: "refused", lines: [acted.text] };
  return { outcome: "unknown", lines: [MAY_BE_ADDED] };
}

// -- Edit ------------------------------------------------------------------------------------------

/** The line under the name, which Edit shows as plain text. */
export const NAME_STAYS = "The name stays as it is: everything filed under this project is filed by its name.";
/** The line an open Edit panel shows once its project has changed under values he typed. */
export const CHANGED_SINCE = "This project has changed since you opened it.";
/** The line a save whose answer was lost says. */
export const MAY_BE_SAVED = "It may have been saved.";

/** What the Edit panel holds, as typed, over `base`, the project as last stored by this
 * panel's own saves, or as it stood when the panel opened. The folders start in `~` form;
 * `off` holds the names taken off, `added` the names added, `agent` what is typed in `Add
 * an agent by name`. `stale`: the project has changed elsewhere under values he typed. */
export interface EditPanel {
  base: Project;
  folder: string;
  kind: string;
  lead: string;
  notesRoot: string;
  off: string[];
  added: string[];
  agent: string;
  stale: boolean;
}

/** The Edit panel opened over `project`, `home` giving the `~` form of its folders. */
export function editPanel(project: Project, home: string | null): EditPanel {
  return { base: project, folder: homeForm(project.root, home), kind: project.kind, lead: project.lead ?? "",
           notesRoot: project.notes_root === null ? "" : homeForm(project.notes_root, home), off: [], added: [],
           agent: "", stale: false };
}

/** The names `Also works here` lists: the project's own, less those taken off, then those
 * added. */
export function alsoHere(form: EditPanel): string[] {
  const off = new Set(form.off);
  return [...form.base.associated.filter((name) => !off.has(name)), ...form.added];
}

/** The form with `name` taken off: one of the project's, or one added here. */
export function takenOff(form: EditPanel, name: string): EditPanel {
  if (form.added.includes(name)) return { ...form, added: form.added.filter((one) => one !== name) };
  return form.off.includes(name) ? form : { ...form, off: [...form.off, name] };
}

/** The form with what is typed in `Add an agent by name` added, once; one taken off comes
 * back. */
export function agentAdded(form: EditPanel): EditPanel {
  const name = form.agent.trim();
  if (name === "") return form;
  if (form.off.includes(name)) return { ...form, off: form.off.filter((one) => one !== name), agent: "" };
  if (form.base.associated.includes(name) || form.added.includes(name)) return { ...form, agent: "" };
  return { ...form, added: [...form.added, name], agent: "" };
}

/** A path field changed from `stored`: not empty, and neither the stored path nor its `~`
 * form. */
function pathChanged(typed: string, stored: string, home: string | null): boolean {
  return typed !== "" && typed !== stored && typed !== homeForm(stored, home);
}

/** `/api/set`'s body for what changed in the form's fields, with the words for each field
 * saved; null when none did. A path is sent as typed; an emptied lead clears it, as an
 * emptied notes folder does while notes is installed. */
export function setChanges(form: EditPanel, { home, notesInstalled }: { home: string | null; notesInstalled: boolean }):
    { body: Record<string, string | boolean>; parts: string[] } | null {
  const base = form.base;
  const body: Record<string, string | boolean> = { key: base.key };
  const parts: string[] = [];
  if (pathChanged(form.folder, base.root, home)) {
    body.root = form.folder;
    parts.push("the folder");
  }
  if (form.kind !== "" && form.kind !== base.kind) {
    body.kind = form.kind;
    parts.push("the kind");
  }
  const lead = form.lead.trim();
  if (lead !== (base.lead ?? "")) {
    if (lead === "") body.no_lead = true;
    else body.lead = lead;
    parts.push("the lead");
  }
  if (notesInstalled) {
    if (form.notesRoot === "" && base.notes_root !== null) {
      body.no_notes_root = true;
      parts.push("the notes folder");
    } else if (base.notes_root === null ? form.notesRoot !== "" : pathChanged(form.notesRoot, base.notes_root, home)) {
      body.notes_root = form.notesRoot;
      parts.push("the notes folder");
    }
  }
  return parts.length > 0 ? { body, parts } : null;
}

/** One request of a save, and the words for what it saves. */
export interface SaveStep {
  route: "api/agents-remove" | "api/set" | "api/agents-add";
  body: Record<string, string | boolean>;
  parts: string[];
}

/**
 * The requests a save sends, in an order that can't trip the rule that a lead is never
 * also one of those who work there: the names taken off, then the fields, then the names
 * added (what is typed in `Add an agent by name` among them). Only what differs from the
 * project as stored is sent, so a save after a partial one sends the rest.
 */
export function saveSteps(form: EditPanel, { home, notesInstalled }: { home: string | null; notesInstalled: boolean }): SaveStep[] {
  const now = agentAdded(form);
  const key = form.base.key;
  const steps: SaveStep[] = now.off.filter((name) => form.base.associated.includes(name)).map((name) => ({
    route: "api/agents-remove", body: { key, name }, parts: [`${name} taken off`] }));
  const set = setChanges(form, { home, notesInstalled });
  if (set !== null) steps.push({ route: "api/set", ...set });
  for (const name of now.added.filter((one) => !form.base.associated.includes(one))) {
    steps.push({ route: "api/agents-add", body: { key, name }, parts: [`${name} added`] });
  }
  return steps;
}

/** Whether he has changed anything in the form from the project it stands over. */
export function editTouched(form: EditPanel, { home, notesInstalled }: { home: string | null; notesInstalled: boolean }): boolean {
  return saveSteps(form, { home, notesInstalled }).length > 0;
}

function sameProject(a: Project, b: Project): boolean {
  return a.root === b.root && a.kind === b.kind && a.lead === b.lead && a.notes_root === b.notes_root
    && a.associated.join("\n") === b.associated.join("\n");
}

/** The form once its project has been read again as `project`: one he hasn't changed starts
 * again from it; any other keeps his values over the project it stood over, and is stale
 * while `project` differs from that one. */
export function editReread(form: EditPanel, project: Project, { home, notesInstalled }: { home: string | null; notesInstalled: boolean }):
    EditPanel {
  if (!editTouched(form, { home, notesInstalled })) return editPanel(project, home);
  return { ...form, stale: !sameProject(project, form.base) };
}

/** What a save that stopped says: the refusal, after what was saved before it. */
export function partlySaved(saved: readonly string[], refusal: string): string {
  return saved.length === 0 ? refusal : `Saved ${joinNames(saved)}. Not saved: ${refusal}`;
}
