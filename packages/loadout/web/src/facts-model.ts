// What loadout's `project-facts` component reports to the Projects page, as plain data: the
// opened project's `Loadout` row, one line of the items its section lists. No DOM and no
// Lit here, so the Node tests check it directly. projects' README, "Facts on the Projects
// page", is the contract it fills.
//
// The join is loadout's own. An item is in a section when its `scope` is the section's
// name, and the project's section is the one the Loadout page's filtered view shows for it
// (`sectionFor`): the key section named by the project's key ignoring case, else the path
// section whose root is the project's root exactly. A path section under the root but not
// at it is not the project's here. Loadout's judgment is the answer's `problem`: set, the
// list can't be read, and nothing is reported, as on a failed read.

import { routeOf, sectionFor } from "./address-model.ts";
import type { Item, Listing } from "./feed.ts";

/** The event a facts component dispatches, with its report as the `detail`. */
export const FACTS_EVENT = "moxi-project-facts";
/** The contract's version. */
export const VERSION = 1;
/** Where loadout's section sits among other modules'. */
export const ORDER = 80;
/** The opened project's row, and its link's words. */
export const LABEL = "Loadout";
/** The longest text the contract takes. */
export const TEXT_MAX_CHARS = 200;
/** The most parts one line may have. */
export const PARTS_MAX = 8;

/** A project as the Projects page gives it, in the `projects` attribute: the key and root
 * are all this component reads. */
export interface Given {
  key: string;
  root: string;
}

export interface Part {
  text: string;
}

export interface Line {
  parts: Part[];
}

export interface Section {
  label: string;
  lines: Line[];
  link: { text: string; module: string; route: string };
}

export interface ProjectFacts {
  sections: Section[];
}

export interface Report {
  version: number;
  order: number;
  projects: Record<string, ProjectFacts>;
}

const isObj = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** The `projects` attribute's projects; those that aren't one are left out, and a value
 * that isn't a JSON list gives none. */
export function parseGiven(text: string | null): Given[] {
  let list: unknown;
  try {
    list = JSON.parse(text ?? "[]");
  } catch {
    return [];
  }
  if (!Array.isArray(list)) return [];
  const given: Given[] = [];
  for (const item of list) {
    if (isObj(item) && typeof item.key === "string" && typeof item.root === "string") {
      given.push({ key: item.key, root: item.root });
    }
  }
  return given;
}

/** `text` cut to the contract's longest, ending `…` when cut. */
export function clip(text: string): string {
  const chars = Array.from(text);
  return chars.length <= TEXT_MAX_CHARS ? text : `${chars.slice(0, TEXT_MAX_CHARS - 1).join("")}…`;
}

/** The items' parts, `<kind> <name>` each, in the order loadout lists them; past eight,
 * the first seven and `<n> more`. */
export function itemParts(items: readonly Item[]): Part[] {
  const parts = items.map((item) => ({ text: clip(`${item.kind} ${item.name}`) }));
  if (parts.length <= PARTS_MAX) return parts;
  return [...parts.slice(0, PARTS_MAX - 1), { text: `${parts.length - (PARTS_MAX - 1)} more` }];
}

/** The project's `Loadout` row, or null when no section of `listing` is the project's or
 * its section lists nothing. Its link opens the Loadout page filtered to the project. */
export function sectionOf(project: Given, listing: Listing): Section | null {
  const name = sectionFor(project, listing);
  if (name === null) return null;
  const items = listing.items.filter((item) => item.scope === name);
  if (!items.length) return null;
  return { label: LABEL, lines: [{ parts: itemParts(items) }],
           link: { text: LABEL, module: "loadout", route: routeOf({ piece: "project", key: project.key }) } };
}

/**
 * What the component reports for `listing` (loadout's `/api/list` value, or null when the
 * last read failed), with `given` the Projects page's projects and `open` the opened key:
 * null unless the read is good and loadout could read its list (`problem` null). Only the
 * opened project, when it is one of `given`, gets a row; a project with no items in loadout
 * gets none.
 */
export function factsReport(listing: Listing | null, given: readonly Given[], open: string | null): Report | null {
  if (listing === null || listing.problem !== null) return null;
  const projects: Record<string, ProjectFacts> = {};
  const project = open === null ? undefined : given.find((one) => one.key === open);
  if (project !== undefined) {
    const section = sectionOf(project, listing);
    if (section !== null) projects[project.key] = { sections: [section] };
  }
  return { version: VERSION, order: ORDER, projects };
}
