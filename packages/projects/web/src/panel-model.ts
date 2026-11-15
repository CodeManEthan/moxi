// The words and the bodies of the page's panel, worked out with no DOM so the Node tests
// can read them: what the Add panel says under its folder field, the name it fills from
// the folder, the `More` fold's line, the body `/api/add` is sent, and what an add came to.

import type { Acted } from "./views.ts";
import { DEFAULT_KIND, kindOf, warningLine } from "./views.ts";
import type { Added, Kinds, Listed } from "./feed.ts";
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
  git: boolean | null;
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
  if (!isObj(data) || !isStrOrNull(data.path) || !isBoolOrNull(data.exists) || !isBoolOrNull(data.git)
      || !isStrOrNull(data.project) || !HOWS.has(data.how) || !isStrOrNull(data.key)
      || !isBoolOrNull(data.taken)) return null;
  return { path: data.path, exists: data.exists, git: data.git, project: data.project, how: data.how as How | null,
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
    if (answer.git === true) said.push("It is a git repository.");
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
export function moreLine(form: AddPanel, { notes }: { notes: boolean }): string {
  const lead = form.lead.trim();
  const said = [kindPhrase(form.kind), lead === "" ? "no lead" : `${lead} leads`];
  if (notes && form.notesRoot.trim() !== "") said.push(`notes in ${form.notesRoot.trim()}`);
  return said.join(" · ");
}

/**
 * `/api/add`'s body for `form`. The folder and the notes folder are sent as typed, `~`
 * included, which the server takes as its home; an empty one is left out, so the kind's
 * module may give it. The notes folder goes only while notes is installed, and create
 * only while the box is offered (`offered`) and ticked.
 */
export function addPanelBody(form: AddPanel, { notes, offered }: { notes: boolean; offered: boolean }):
    Record<string, string | boolean> {
  const body: Record<string, string | boolean> = { key: form.name.trim() };
  if (form.folder !== "") body.root = form.folder;
  if (form.kind !== "") body.kind = form.kind;
  if (form.lead.trim() !== "") body.lead = form.lead.trim();
  if (notes && form.notesRoot !== "") body.notes_root = form.notesRoot;
  if (offered && form.create) body.create = true;
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
