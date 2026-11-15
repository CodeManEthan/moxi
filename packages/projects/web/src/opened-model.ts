// The Projects page's opened project, its remove step, the empty list's sentence and the
// folders that are not projects, worked out with no DOM so the Node tests can read them.
// Every string here goes into the page as text.

import { claimedBelow, inOrder, partText } from "./claims.ts";
import type { Link, Report, Say } from "./facts.ts";
import type { Listed, Refusal, RemoveStatus } from "./feed.ts";
import { JOIN } from "./list-model.ts";
import { homeForm, kindPhrase, lastActive, LOCAL } from "./words.ts";
import type { Clock } from "./words.ts";

/** One phrase of an opened row's line, drawn in order and joined by ` · `. */
export interface Shown {
  text: string;
  heavy: boolean;
}

/** One line of an opened row. */
export interface ShownLine {
  parts: Shown[];
}

/** One labelled row of the opened project: label at the left, lines, link or act at the right. */
export interface OpenedRow {
  label: string;
  lines: ShownLine[];
  /** Drawn at the right; only when its module is installed. */
  link: Link | null;
  /** `Change folder` at the right, on the `Folder` row of a missing folder. */
  changeFolder: boolean;
}

/** What the opened project is drawn from, beside the project. */
export interface OpenedFacts {
  home: string | null;
  notesInstalled: boolean;
  /** Every report held now, null ones left out, in the order the page placed them. */
  reports: readonly Report[];
  /** The modules installed now, for links. */
  installed: ReadonlySet<string>;
}

/** The `Folder` row's words for a root that is not a directory. */
export const MISSING_SAYS =
  "Moxi keeps the project's name and settings. Point it at where the folder went, or remove the project.";

const line = (...texts: string[]): ShownLine => ({ parts: texts.map((text) => ({ text, heavy: false })) });

/** `a`, `a and b`, `a, b and c`. */
export function joinNames(names: readonly string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/**
 * The phrase after an agent's name, from every report's `Say` for it, or null. It is
 * drawn only when they all agree on `runs`: the `state` of the lowest order, then, with
 * `runs` true, the `where` of the lowest order that gives one, joined by a space. Where
 * they disagree, it can't say which is true, and says nothing.
 */
export function agentPhrase(reports: readonly Report[], key: string, name: string): string | null {
  const says: Say[] = [];
  for (const report of inOrder(reports)) {
    const say = report.projects.get(key)?.agents.get(name);
    if (say !== undefined) says.push(say);
  }
  if (says.length === 0) return null;
  if (says.some((say) => say.runs !== says[0].runs)) return null;
  const where = says[0].runs ? says.find((say) => say.where !== null)?.where ?? null : null;
  return where === null ? says[0].state : `${says[0].state} ${where}`;
}

/** The `Agents` row's line: `<lead> leads`, the lead's phrase, then who also works here. */
export function agentsLine(project: Listed, reports: readonly Report[]): ShownLine {
  const texts: string[] = [];
  if (project.lead !== null) {
    texts.push(`${project.lead} leads`);
    const phrase = agentPhrase(reports, project.key, project.lead);
    if (phrase !== null) texts.push(phrase);
  }
  const others = project.associated;
  if (others.length > 0) {
    texts.push(`${joinNames(others)} ${others.length === 1 ? "also works here" : "also work here"}`);
  }
  if (texts.length === 0) texts.push("no lead");
  return line(...texts);
}

/** The `Folder` row: the `~` root and its kind, or, while the root is missing, its words. */
export function folderRow(project: Listed, home: string | null): OpenedRow {
  const root = homeForm(project.root, home);
  if (project.exists === false) {
    return { label: "Folder", link: null, changeFolder: true,
             lines: [{ parts: [{ text: `${root} doesn't exist`, heavy: true }] }, line(MISSING_SAYS)] };
  }
  return { label: "Folder", lines: [line(root, kindPhrase(project.kind))], link: null, changeFolder: false };
}

interface Merged {
  label: string;
  rank: number | null;
  /** The lowest order among its reporters, and the place of its first listing. */
  order: number;
  seq: number;
  lines: ShownLine[];
  link: Link | null;
}

/**
 * The reports' sections for `key`, one row per label: lines in order, the first link in
 * order whose module is installed, the lowest rank. A counted part counts the sessions no
 * lower order claimed, with its after-lines words once a line is drawn above it; a line
 * with nothing left to draw is left out, and so is a section.
 */
export function mergedSections(reports: readonly Report[], key: string,
                               installed: ReadonlySet<string>): Merged[] {
  const byLabel = new Map<string, Merged>();
  let seq = 0;
  for (const report of inOrder(reports)) {
    const facts = report.projects.get(key);
    if (facts === undefined) continue;
    const claimed = claimedBelow(reports, key, report.order);
    for (const section of facts.sections) {
      let merged = byLabel.get(section.label);
      if (merged === undefined) {
        merged = { label: section.label, rank: null, order: report.order, seq: seq++, lines: [], link: null };
        byLabel.set(section.label, merged);
      }
      if (section.rank !== null && (merged.rank === null || section.rank < merged.rank)) merged.rank = section.rank;
      if (merged.link === null && section.link !== null && installed.has(section.link.module)) {
        merged.link = section.link;
      }
      for (const raw of section.lines) {
        const parts: Shown[] = [];
        for (const part of raw.parts) {
          const text = partText(part, claimed, { afterLines: merged.lines.length > 0 });
          if (text !== null) parts.push({ text, heavy: part.heavy });
        }
        if (parts.length > 0) merged.lines.push({ parts });
      }
    }
  }
  return [...byLabel.values()].filter((merged) => merged.lines.length > 0);
}

/**
 * The opened project's rows, each only when it has something: the ranked sections, lowest
 * rank first; `Folder`; `Agents`; the other sections by the lowest order among each
 * label's reporters, a tie in the order listed; `Notes` while notes is installed and the
 * project has a notes folder.
 */
export function openedRows(project: Listed, facts: OpenedFacts): OpenedRow[] {
  const { home, notesInstalled, reports, installed } = facts;
  const sections = mergedSections(reports, project.key, installed);
  const row = (merged: Merged): OpenedRow =>
    ({ label: merged.label, lines: merged.lines, link: merged.link, changeFolder: false });
  const ranked = sections.filter((merged) => merged.rank !== null)
    .sort((a, b) => (a.rank as number) - (b.rank as number) || a.order - b.order || a.seq - b.seq);
  const rest = sections.filter((merged) => merged.rank === null).sort((a, b) => a.order - b.order || a.seq - b.seq);
  const rows = [...ranked.map(row), folderRow(project, home),
                { label: "Agents", lines: [agentsLine(project, reports)], link: null, changeFolder: false },
                ...rest.map(row)];
  if (notesInstalled && project.notes_root !== null) {
    rows.push({ label: "Notes", lines: [line(homeForm(project.notes_root, home))], link: null, changeFolder: false });
  }
  return rows;
}

// -- remove -----------------------------------------------------------------------------------

/** What the remove step says before anything is sent. */
export interface RemoveStep {
  title: string;
  goes: string;
  stays: string[];
  foot: string;
}

/** The step's `Goes` line. */
export const GOES = "the project's entry in Moxi: its name and its kind. Other pages stop grouping by it.";

/**
 * The remove step. What stays: the folder while it is not known to be missing, the notes
 * folder while notes is installed and set, then each report's lines in order.
 */
export function removeStep(project: Listed, facts: OpenedFacts): RemoveStep {
  const { home, notesInstalled, reports } = facts;
  const root = homeForm(project.root, home);
  const stays: string[] = [];
  if (project.exists !== false) stays.push(`the folder ${root} and every file in it`);
  if (notesInstalled && project.notes_root !== null) stays.push(`the notes in ${homeForm(project.notes_root, home)}`);
  for (const report of inOrder(reports)) stays.push(...(report.projects.get(project.key)?.stays ?? []));
  return {
    title: `Remove ${project.key} from Moxi?`,
    goes: GOES,
    stays,
    foot: `Nothing on disk is deleted. Adding ${root} again under the same name puts it back.`,
  };
}


/** One row of the refused step: its label, its words, and at the right that module's page or `Edit`. */
export interface RefusedRow {
  label: string;
  lines: string[];
  /** The module whose page is drawn at the right; null for none. */
  page: string | null;
  /** `Edit` at the right: projects' own row, the associated agents. */
  edit: boolean;
}

/** The refused step's title. */
export const refusedTitle = (key: string): string => `${key} can't be removed yet`;
/** What the refused step says under its rows. */
export const NOTHING_CHANGED = "Nothing was changed.";

/**
 * The refused step's rows, one per refusal, as `/api/remove-check` gives them: projects'
 * own row is `Agents`, in the page's words for the agents it holds, with `Edit`; any
 * other is labelled with the module's page title (`titleOf`, else the module's name), its
 * sentence and its fix, with that page when the module has one.
 */
export function refusedRows(project: Listed, refusals: readonly Refusal[],
                            titleOf: (module: string) => string | null): RefusedRow[] {
  return refusals.map((refusal) => {
    if (refusal.module === "projects" && project.associated.length > 0) {
      const many = project.associated.length > 1;
      return {
        label: "Agents", page: null, edit: true,
        lines: [`${joinNames(project.associated)} ${many ? "also work here" : "also works here"}. `
                + `Take ${many ? "them" : "it"} off the project first.`],
      };
    }
    const title = titleOf(refusal.module);
    return {
      label: title ?? refusal.module, page: title === null ? null : refusal.module, edit: false,
      lines: refusal.fix === null ? [refusal.sentence] : [refusal.sentence, refusal.fix],
    };
  });
}

/** The row a removed project becomes, with `Add it back`. */
export const removedLine = (key: string): string => `${key} was removed from Moxi. Nothing on disk was deleted.`;

/** What a remove whose answer was lost, and whose kept outcome can't be read, says. */
export const MAY_BE_REMOVED = "It may have been removed.";

/** A remove's `job`: 32 hexadecimal characters, minted for each press of `Remove from Moxi`. */
export function mintJob(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** What a press of `Remove from Moxi` came to, from the remove's own answer or, when it
 * was lost, the outcome kept under its `job`. */
export type Pressed =
  /** The project is gone from Moxi. */
  | { outcome: "removed" }
  /** Refused for something that changed since the check: the check is read again, and
   * `reason` shows under the button when it then finds nothing to refuse. */
  | { outcome: "held"; reason: string }
  /** Refused before the remove was tried (the comparison, the store): its text. */
  | { outcome: "refused"; text: string }
  /** No outcome can be read: it may have been removed. */
  | { outcome: "unknown" };

/** A kept remove outcome as a press's. */
export function pressedOf(status: RemoveStatus): Pressed {
  return status.state === "removed" ? { outcome: "removed" } : { outcome: "held", reason: status.reason ?? "" };
}

/** `/api/add`'s body that puts a removed project back as the page held it: its key,
 * folder, kind and lead, and its notes folder while notes is installed. */
export function addBackBody(project: Listed, { notesInstalled }: { notesInstalled: boolean }):
    Record<string, string> {
  const body: Record<string, string> = { key: project.key, root: project.root, kind: project.kind };
  if (project.lead !== null) body.lead = project.lead;
  if (notesInstalled && project.notes_root !== null) body.notes_root = project.notes_root;
  return body;
}

/** Where a removed project's line goes among the rows drawn now: after the row of
 * `after`, the key drawn before it, or first when that row isn't drawn. */
export function removedAt(keys: readonly string[], after: string | null): number {
  return after === null ? 0 : keys.indexOf(after) + 1;
}

// -- the empty list ---------------------------------------------------------------------------

/**
 * The empty list's sentence: `A project is a folder with a short name.`, followed, when
 * any report says what it gathers, by ` Its <gathers> are then gathered under that name.`,
 * the words joined in order as names are.
 */
export function emptySentence(reports: readonly Report[]): string {
  const gathers: string[] = [];
  for (const report of inOrder(reports)) {
    if (report.gathers !== null && !gathers.includes(report.gathers)) gathers.push(report.gathers);
  }
  const base = "A project is a folder with a short name.";
  return gathers.length === 0 ? base : `${base} Its ${joinNames(gathers)} are then gathered under that name.`;
}

/** The empty list's first line. */
export const NO_PROJECTS = "No projects yet.";

// -- folders that are not projects ------------------------------------------------------------

/** How many folder rows show before `Show`. */
export const FOLDERS_SHOWN = 5;

/** One folder row: one line, the `~` folder, its parts, the time and `Add`. */
export interface FolderRow {
  folder: string;
  /** `name`: the folder in `~` form. */
  name: string;
  /** `where`: the parts joined ` · `, or null. */
  where: string | null;
  /** `fact`: `last active <when>`. */
  fact: string;
  at: number;
}

/** The group of folder rows under the list. */
export interface FolderGroup {
  /** `Not a project`, or `Worked in this week` with no projects. */
  label: string;
  rows: FolderRow[];
  /** `<n> more folders`, after `Show` (`canShow`) or alone and dim; null for none. */
  more: string | null;
  /** `Show` follows `more`: rows are held past those shown. */
  canShow: boolean;
}

/** `<n> more folder(s)`. */
export const moreFolders = (n: number): string => `${n} more ${n === 1 ? "folder" : "folders"}`;

/** A folder row's `Add`: the panel's folder in `~` form, and the name filled from the
 * folder's last part, as the folder's read will fill it. */
export function folderAsk(row: FolderRow): { mode: "add"; folder: string; name: string } {
  const parts = row.folder.split(/[\\/]+/).filter((part) => part !== "");
  return { mode: "add", folder: row.name, name: parts.length === 0 ? "" : parts[parts.length - 1] };
}

/**
 * The folders no project holds. Rows of one folder from several reports are one row, parts
 * in order and the newest `at`; a row shows only once some report gives it an `at`, which
 * only an activity source may. Newest first, then by folder. Five rows, then `<n> more
 * folders · Show`, `<n>` the rows held past five plus the largest `more_folders` of any
 * activity source; with `showAll`, every row held, and a dim `<n> more folders` for those
 * no report listed. A folder that is a held project's root (`roots`) is no row: an add
 * holds it before its module reports again. Null when no row shows.
 */
export function folderGroup(reports: readonly Report[], { home, now, hasProjects, showAll, roots, clock = LOCAL }:
                            { home: string | null; now: number; hasProjects: boolean; showAll: boolean;
                              roots: readonly string[]; clock?: Clock }): FolderGroup | null {
  const projects = new Set(roots);
  const held = new Map<string, { at: number | null; texts: string[] }>();
  let unlisted = 0;
  for (const report of inOrder(reports)) {
    if (report.activity !== null) unlisted = Math.max(unlisted, report.more_folders);
    for (const folder of report.folders) {
      let row = held.get(folder.folder);
      if (row === undefined) {
        row = { at: null, texts: [] };
        held.set(folder.folder, row);
      }
      if (folder.at !== null && (row.at === null || folder.at > row.at)) row.at = folder.at;
      for (const part of folder.parts) {
        const text = partText(part, new Set());
        if (text !== null) row.texts.push(text);
      }
    }
  }
  const rows: FolderRow[] = [];
  for (const [folder, row] of held) {
    if (row.at === null || projects.has(folder)) continue;
    rows.push({ folder, name: homeForm(folder, home), where: row.texts.length === 0 ? null : row.texts.join(JOIN),
                fact: lastActive(row.at, now, clock), at: row.at });
  }
  if (rows.length === 0) return null;
  rows.sort((a, b) => b.at - a.at || (a.folder < b.folder ? -1 : a.folder > b.folder ? 1 : 0));
  const label = hasProjects ? "Not a project" : "Worked in this week";
  if (showAll || rows.length <= FOLDERS_SHOWN) {
    return { label, rows, more: unlisted > 0 ? moreFolders(unlisted) : null, canShow: false };
  }
  const past = rows.length - FOLDERS_SHOWN;
  return { label, rows: rows.slice(0, FOLDERS_SHOWN), more: moreFolders(past + unlisted), canShow: true };
}
