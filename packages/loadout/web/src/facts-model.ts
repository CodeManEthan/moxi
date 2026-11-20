// What loadout's `project-facts` component reports to the Projects page, as plain data: the
// opened project's `Loadout` row, one line of the items its section lists. No DOM and no
// Lit here, so the Node tests check it directly. projects' README, "Facts on the Projects
// page", is the contract it fills.
//
// The join is loadout's own. An item is in a section when its `scope` is the section's
// name. A section is the project's when it is the key section named by the project's key
// ignoring case, or the path section whose root is the project's root exactly; loadout
// places both into the project's folder, so the row names the items of each, the key
// section's first. A path section under the root but not at it is not the project's here.
// Loadout's judgment is the answer's `problem`: set, the list can't be read, and nothing is
// reported, as on a failed read.

import { routeOf } from "./address-model.ts";
import type { Item, Listing } from "./feed.ts";
import { kindWord } from "./words.ts";

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

/** The items' parts, `<kind> <name>` each with the kind as the Loadout page words it
 * (`permission rule`, `MCP server`), in the order loadout lists them; past eight, the first
 * seven and `<n> more`. */
export function itemParts(items: readonly Pick<Item, "kind" | "name">[]): Part[] {
  const parts = items.map((item) => ({ text: clip(`${kindWord(item.kind)} ${item.name}`) }));
  if (parts.length <= PARTS_MAX) return parts;
  return [...parts.slice(0, PARTS_MAX - 1), { text: `${parts.length - (PARTS_MAX - 1)} more` }];
}

/** The names of the project's sections in `listing`: the key section named by its key
 * ignoring case, then the path section whose root is its root exactly; either may be
 * missing. */
export function projectSections(project: Given, listing: Pick<Listing, "sections">): string[] {
  const lower = project.key.toLowerCase();
  const keyed = listing.sections.find((s) => s.where === "key" && s.name.toLowerCase() === lower);
  const rooted = listing.sections.find((s) => s.where === "path" && s.root === project.root);
  const names: string[] = [];
  for (const found of [keyed, rooted]) {
    if (found !== undefined && !names.includes(found.name)) names.push(found.name);
  }
  return names;
}

/** The items the project's sections list, each section's in loadout's order, the key
 * section's first; an item (its kind and name) both list is the first section's, once. */
export function projectItems(project: Given, listing: Listing): Item[] {
  const seen = new Set<string>();
  const items: Item[] = [];
  for (const name of projectSections(project, listing)) {
    for (const item of listing.items) {
      if (item.scope !== name) continue;
      const id = `${item.kind}:${item.name}`;
      if (seen.has(id)) continue;
      seen.add(id);
      items.push(item);
    }
  }
  return items;
}

/** The project's `Loadout` row, or null when no section of `listing` is the project's or
 * its sections list nothing. Its link opens the Loadout page filtered to the project. */
export function sectionOf(project: Given, listing: Listing): Section | null {
  const items = projectItems(project, listing);
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
