// What comms' `project-facts` component reports to the Projects page, as plain data: for
// the opened project's lead and associated agents, which names no session holds now. No
// DOM and no Lit here, so the Node tests check it directly. projects' README, "Facts on the
// Projects page", is the contract it fills.
//
// Comms' judgment is the roster's `state` and each row's `live`. Only while `state` is
// `up` does comms say which names are held; `offline` and `error` answer no rows, so it
// can't say, and nothing is reported, as on a failed read. A name no row of which is live
// is `not running now`. A live name says nothing: only Moxi's launches count as running,
// and a live address may be a session started by hand, so comms never says an agent runs.

import type { Roster } from "./feed.ts";

/** The event a facts component dispatches, with its report as the `detail`. */
export const FACTS_EVENT = "moxi-project-facts";
/** The contract's version. */
export const VERSION = 1;
/** Where comms' words sit among other modules'. */
export const ORDER = 20;
/** Comms' words for a name no session holds. */
export const NOT_RUNNING = "not running now";

/** A project as the Projects page gives it, in the `projects` attribute: its key and the
 * agents' names are all this component reads. */
export interface Given {
  key: string;
  lead: string | null;
  associated: string[];
}

/** What a module says of an agent: comms only ever says one does not run. */
export interface Say {
  runs: false;
  state: string;
}

export interface ProjectFacts {
  agents: Record<string, Say>;
}

export interface Report {
  version: number;
  order: number;
  projects: Record<string, ProjectFacts>;
}

const isObj = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** The `projects` attribute's projects; those that aren't one are left out, and a value
 * that isn't a JSON list gives none. A lead that isn't a name is none, and associated
 * agents that aren't names are left out. */
export function parseGiven(text: string | null): Given[] {
  let list: unknown;
  try {
    list = JSON.parse(text ?? "[]");
  } catch {
    return [];
  }
  if (!Array.isArray(list)) return [];
  return list.filter((item): item is Record<string, unknown> => isObj(item) && typeof item.key === "string")
    .map((item) => ({
      key: item.key as string,
      lead: typeof item.lead === "string" && item.lead !== "" ? item.lead : null,
      associated: Array.isArray(item.associated)
        ? item.associated.filter((name): name is string => typeof name === "string" && name !== "") : [],
    }));
}

/** The project's agents by name, the lead first, each once. */
export function agentNames(project: Given): string[] {
  return [...new Set([...(project.lead === null ? [] : [project.lead]), ...project.associated])];
}

/**
 * What the component reports for `roster` (comms' `/api/roster` value, or null when the
 * last read failed), with `given` the Projects page's projects and `open` the opened key:
 * null unless the read is good and comms says which names are held (`state` `up`). Only
 * the opened project, when it is one of `given`, gets `agents`: each of its names that no
 * live row holds is `not running now`; a held name is left out.
 */
export function factsReport(roster: Roster | null, given: readonly Given[], open: string | null): Report | null {
  if (roster === null || roster.state !== "up") return null;
  const projects: Record<string, ProjectFacts> = {};
  const project = open === null ? undefined : given.find((one) => one.key === open);
  if (project !== undefined) {
    const held = new Set(roster.rows.filter((row) => row.live).map((row) => row.name));
    const agents: Record<string, Say> = {};
    for (const name of agentNames(project)) {
      if (!held.has(name)) agents[name] = { runs: false, state: NOT_RUNNING };
    }
    if (Object.keys(agents).length) projects[project.key] = { agents };
  }
  return { version: VERSION, order: ORDER, projects };
}
