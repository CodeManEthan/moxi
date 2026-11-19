// Which projects an idle name leads, from projects, worked out with no DOM so the Node tests
// can drive it. With projects installed the page reads projects' `/api/projects` while
// `Other names` holds an idle name. Each project names its lead, or none; an entry of
// `Other names` that speaks for an idle name (`OtherName.speaksFor`) is matched to the projects
// whose lead is that name, and its shown row says `leads <key>` on line two. A name that
// leads nothing, a read that failed or isn't back, or projects not installed: the row says
// nothing of it.

import type { OtherName } from "./agents-model.ts";
import { namesWords } from "./mail-model.ts";

/** The route the page reads the projects from, relative to projects' page. */
export const PROJECTS_PATH = "api/projects";

/** The keys of the projects each name leads, in the list's order (by key), or null when
 * the value isn't projects' list. A project with no lead, or an entry that isn't a
 * project, leads nothing. */
export function projectsLedBy(data: unknown): ReadonlyMap<string, readonly string[]> | null {
  if (typeof data !== "object" || data === null || Array.isArray(data)) return null;
  const { projects } = data as Record<string, unknown>;
  if (!Array.isArray(projects)) return null;
  const leads = new Map<string, string[]>();
  for (const project of projects) {
    if (typeof project !== "object" || project === null) continue;
    const { key, lead } = project as Record<string, unknown>;
    if (typeof key !== "string" || key === "" || typeof lead !== "string" || lead === "") continue;
    const keys = leads.get(lead);
    if (keys === undefined) leads.set(lead, [key]);
    else keys.push(key);
  }
  return leads;
}

/** What an entry of `Other names` says on its line two: `leads <key>` for the projects its
 * idle name leads, the first few by key and how many more (`leads atlas-api and
 * harbor-infra`); null for an entry that speaks for no idle name, a name that leads
 * nothing, and no read. */
export function leadsWords(other: OtherName, leads: ReadonlyMap<string, readonly string[]> | null): string | null {
  if (leads === null || other.speaksFor === null) return null;
  const keys = leads.get(other.speaksFor);
  return keys === undefined || keys.length === 0 ? null : `leads ${namesWords([...keys])}`;
}

/** Whether `others` holds an entry that speaks for an idle name, so the page reads projects. */
export function wantsLeads(others: readonly OtherName[]): boolean {
  return others.some((other) => other.speaksFor !== null);
}
