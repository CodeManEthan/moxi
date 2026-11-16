// What `Not listed` and Add show, from `/api/folder`, `/api/view` and `/api/projects`: a
// line per skill or agent file in the folder that no section lists, and Add's section
// picker with the dim line that says why projects without a section aren't offered. No
// DOM, so the Node tests read it.

import type { Projects, SourceDir, View } from "./feed.ts";
import { sectionFor } from "./address-model.ts";
import { homeForm, kindWord, sectionWord, YOUR_ITEMS } from "./words.ts";

/** Under Add's buttons, dim. */
export const ADD_NOTE = "Lists it in loadout.toml and places it at once.";
/** The way from Add on a file to Add by name. */
export const BY_NAME = "Add something by its name instead";
/** The kinds Add by name offers, in the kind words' order. */
export const ADD_KINDS = ["skill", "agent", "hook", "permissions", "mcp"] as const;

/** One file Not listed shows. */
export interface NotListedRow {
  /** `<kind>/<name>`: what Add's address names. */
  key: string;
  kind: string;
  name: string;
  /** `skill · in your folder, in no section`. */
  line: string;
  /** `From ~/my-loadout/skills/bump-version`. */
  from: string;
}

/** The files Not listed shows; none until the folder is read. */
export function notListed(dir: SourceDir | null, view: View): NotListedRow[] {
  if (dir === null) return [];
  return dir.unlisted.map((u) => ({ key: `${u.kind}/${u.name}`, kind: u.kind, name: u.name,
                                    line: `${kindWord(u.kind)} · in your folder, in no section`,
                                    from: `From ${homeForm(u.source, view.home)}` }));
}

/** One choice of Add's section picker: `section` is what the act names, null for Your
 * items. */
export interface PickOption {
  value: string;
  label: string;
  section: string | null;
}

export interface Picker {
  options: PickOption[];
  /** Why projects without a section aren't offered, dim; null when there is nothing to
   * say (they are offered, projects isn't installed, or it isn't read yet). */
  line: string | null;
}

/** Each `cause` of an empty projects answer, in the picker's words. */
export const PICKER_LINES: Readonly<Record<string, string>> = {
  busy: "Projects is busy, so projects without a section aren't offered; open Add again shortly",
  unreadable: "Projects can't be read, so projects without a section aren't offered",
  error: "Projects can't be read, so projects without a section aren't offered",
};

/** Add's picker: Your items, each project section, and with projects each project that has
 * no section yet. */
export function pickerOf(view: View, projects: Projects | null): Picker {
  const options: PickOption[] = [{ value: "", label: YOUR_ITEMS, section: null }];
  for (const section of view.sections) {
    if (section.where === "user") continue;
    options.push({ value: section.name, label: sectionWord(section, view.home), section: section.name });
  }
  const taken = new Set(options.map((o) => o.value));
  for (const project of projects?.projects ?? []) {
    if (sectionFor(project, view) !== null || taken.has(project.key)) continue;
    taken.add(project.key);
    options.push({ value: project.key, label: project.key, section: project.key });
  }
  const cause = projects !== null && projects.projects.length === 0 ? projects.cause : null;
  return { options, line: cause === null ? null : PICKER_LINES[cause] ?? null };
}
