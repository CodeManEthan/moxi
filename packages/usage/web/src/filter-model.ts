// The filter line: the range, the project (or folder), harness and model filters, the
// Cost / Tokens switch and the By class / By model split, as one state with the reads it asks for and the menus it shows.
// No DOM here. The state is held for the document's life, so it outlives one placement
// of the page: the page's element reads it, writes it and passes it to every piece that
// reads under the range and the filters.

import type { Measure, Split } from "./chart-model.ts";
import { COST_DAYS, countWords, DEFAULT_RANGE, rangeBy, rangeDays, rangeLabel, rangeMenu } from "./cost-model.ts";
import type { Range } from "./cost-model.ts";
import { addDays, daysFrom } from "./days.ts";
import type { DayRow, Lane, ModelRow, ProjectRow, Report } from "./feed.ts";
import { filesByHarness } from "./frame-model.ts";
import { heatSince } from "./heat-model.ts";
import { harnessName } from "./limits-model.ts";

/** What the filter line is set to. An empty string is a filter that is not set. */
export interface PageState {
  range: Range;
  /** The project keys picked; none is every project. */
  projects: string[];
  /** The folders picked, each a whole path. At most one of `projects` and `paths` holds
   * any. */
  paths: string[];
  harness: string;
  model: string;
  measure: Measure;
  /** What the chart by cost is stacked by; by tokens it is by class whatever this says. */
  split: Split;
}

export const START: PageState = {
  range: DEFAULT_RANGE, projects: [], paths: [], harness: "", model: "", measure: "cost", split: "class",
};

let held: PageState = { ...START };
const watchers = new Set<(state: PageState) => void>();

/** The state as it stands. */
export function heldState(): PageState {
  return held;
}

/** Changes the state and tells every watcher. Picking a project clears the folders and
 * picking a folder clears the projects, since a report takes the two as one place. */
export function holdState(patch: Partial<PageState>): PageState {
  const next = { ...held, ...patch };
  if (patch.projects?.length) next.paths = [];
  else if (patch.paths?.length) next.projects = [];
  held = next;
  for (const tell of [...watchers]) tell(held);
  return held;
}

/** Calls `tell` after each change; the returned function stops it. */
export function watchState(tell: (state: PageState) => void): () => void {
  watchers.add(tell);
  return () => { watchers.delete(tell); };
}

/** Whether any filter but the range is set. */
export function filtered(state: PageState): boolean {
  return Boolean(state.projects.length || state.paths.length || state.harness || state.model);
}

// --- the reads ----------------------------------------------------------------------------

export type FilterKey = "since" | "until" | "harness" | "project" | "folder" | "model";

/** The query keys the state gives a report, in a fixed order; `leave` names keys to
 * leave out. Several projects or folders go one to a line. All time sends no day, and
 * with the days left out `today` is not read. */
export function stateKeys(state: PageState, today: string, leave: FilterKey[] = []): [string, string][] {
  const days = leave.includes("since") && leave.includes("until") ? null : rangeDays(state.range, today);
  const keys: [FilterKey, string][] = [];
  if (days !== null) keys.push(["since", days.since], ["until", days.until]);
  if (state.harness) keys.push(["harness", state.harness]);
  if (state.projects.length) keys.push(["project", state.projects.join("\n")]);
  if (state.paths.length) keys.push(["folder", state.paths.join("\n")]);
  if (state.model) keys.push(["model", state.model]);
  return keys.filter(([key]) => !leave.includes(key));
}

function withQuery(path: string, keys: [string, string][]): string {
  const query = new URLSearchParams(keys).toString();
  return query ? `${path}?${query}` : path;
}

/** The chart's report: the range by day, or by month for all time and past 92 days. */
export function chartPath(state: PageState, today: string): string {
  return withQuery("api/daily", [["by", rangeBy(state.range, today)], ...stateKeys(state, today)]);
}

/** The chart's cost terms: each period's cost by token class, by the chart's period,
 * under the same range and filters. */
export function chartTermsPath(state: PageState, today: string): string {
  return withQuery("api/terms", [["by", rangeBy(state.range, today)], ...stateKeys(state, today)]);
}

/** The heat map's report: each day of the 53 weeks to today, under the project (or
 * folder) filter alone. */
export function heatPath(state: PageState, today: string): string {
  return withQuery("api/daily", [["by", "day"], ["since", heatSince(today)], ["until", today],
                                 ...stateKeys(state, today, ["since", "until", "harness", "model"])]);
}

/** The report the Range menu's preset figures are summed from while a filter is set:
 * the 90 days to today under the filters. Null with no filter, when the figures are the
 * Cost rows' own and their report serves. */
export function figuresPath(state: PageState, today: string): string | null {
  if (!filtered(state)) return null;
  return withQuery("api/daily", [["since", addDays(today, 1 - COST_DAYS) ?? today], ["until", today],
                                 ...stateKeys(state, today, ["since", "until"])]);
}

/** The report behind `All time`'s figure: every month, under the filters. */
export function allTimePath(state: PageState): string {
  return withQuery("api/daily", [["by", "month"], ...stateKeys(state, "", ["since", "until"])]);
}

/** The projects of the range under the other filters, or its folders. */
export function placesPath(state: PageState, today: string, folders: boolean): string {
  return withQuery("api/projects", [...(folders ? [["by", "folder"] as [string, string]] : []),
                                    ...stateKeys(state, today, ["project", "folder"])]);
}

/** The models of the range under the other filters. */
export function modelsPath(state: PageState, today: string): string {
  return withQuery("api/models", stateKeys(state, today, ["model"]));
}

/** The Projects (or Folders) section's report: the range under every filter, as
 * everything under the filter line follows them. */
export function placesSectionPath(state: PageState, today: string, folders: boolean): string {
  return withQuery("api/projects", [...(folders ? [["by", "folder"] as [string, string]] : []), ...stateKeys(state, today)]);
}

/** The Models section's report: the range under every filter. */
export function modelsSectionPath(state: PageState, today: string): string {
  return withQuery("api/models", stateKeys(state, today));
}

/** The cost terms of the range under every filter, by model: Token kinds' classes and
 * saving, the models Prices lists and the bill's lines. */
export function termsSectionPath(state: PageState, today: string): string {
  return withQuery("api/terms", [["by", "model"], ...stateKeys(state, today)]);
}

// --- the line -----------------------------------------------------------------------------

/** One entry of a menu. */
export interface MenuItem {
  key: string;
  label: string;
  /** The range's cost at the right of a Range entry; null where the menu shows none. */
  figure: string | null;
  /** The entry in force, which carries the menu's check. */
  picked: boolean;
  /** One of several the menu can pick together: picking it adds it to those picked, or
   * takes it away, and leaves the menu open. */
  several: boolean;
  /** A rule is drawn above this entry. */
  ruled: boolean;
  /** What picking it sets; null for `From … to …`, which opens its two days instead. */
  patch: Partial<PageState> | null;
}

/** One filter: its name, what it is set to, and its entries. */
export interface Menu {
  key: "range" | "place" | "harness" | "model";
  /** `Range`, `Project`, `Folder`, `Harness`, `Model`. */
  label: string;
  /** The button's words: `last 30 days`, `all`, a project, `~/code/atlas-api`,
   * `2 projects`. */
  value: string;
  /** Set to something other than what the page opens with: drawn in the text colour. */
  set: boolean;
  items: MenuItem[];
}

/** The two days under `From … to …`, with the words around them. */
export interface CustomDays {
  from: string;
  to: string;
  act: string;
  since: string;
  until: string;
}

export interface FilterLine {
  range: Menu;
  custom: CustomDays;
  /** The project filter, or the folder filter where projects is not installed; null
   * until its list has answered, unless one is set. */
  place: Menu | null;
  /** Each null unless it has more than one to choose or one is set. */
  harness: Menu | null;
  model: Menu | null;
  measures: { key: Measure; label: string; picked: boolean }[];
  /** `By class` and `By model`; under Tokens both are off, with `off` saying why. */
  splits: { key: Split; label: string; picked: boolean; off: string | null }[];
}

/** Why the split is off under Tokens. */
export const SPLIT_OFF = "Token mode always splits by class";

/** What the line is worked out from. Each report is null until it has answered. */
export interface FilterFacts {
  state: PageState;
  today: string;
  /** The 90-day daily report under the filters in force: the Cost rows' own with none. */
  figures: Report<DayRow> | null;
  /** The monthly report over no range under the filters; asked when the menu first opens. */
  allTime: Report<DayRow> | null;
  /** `/api/projects` over the range under the other filters, by project or by folder. */
  places: Report<ProjectRow> | null;
  /** Projects is not installed: the filter is by folder. */
  folders: boolean;
  /** `/api/models` over the range under the other filters. */
  models: Report<ModelRow> | null;
  /** The index's lanes; null until the status has answered. */
  lanes: Lane[] | null;
}

export const ALL = "all";
const item = (key: string, label: string, picked: boolean, patch: Partial<PageState>): MenuItem =>
  ({ key, label, figure: null, picked, ruled: false, several: false, patch });
const severalItem = (key: string, label: string, picked: boolean, patch: Partial<PageState>): MenuItem =>
  ({ ...item(key, label, picked, patch), several: true });

/** A folder as the page writes it whole: under the home folder, `~/code/atlas-api`. */
export function tilde(path: string, home: string | null | undefined): string {
  if (!home) return path;
  const base = home.replace(/\/+$/, "");
  if (path === base) return "~";
  return base !== "" && path.startsWith(`${base}/`) ? `~${path.slice(base.length)}` : path;
}

/** A menu of `all` and then `entries`, with the one in force first when no entry is it. */
function chooser(key: Menu["key"], label: string, current: string, entries: [string, string][],
                 patch: (value: string) => Partial<PageState>, name: (value: string) => string): Menu {
  const listed = current && !entries.some(([value]) => value === current)
    ? [[current, name(current)] as [string, string], ...entries] : entries;
  return {
    key, label, value: current ? name(current) : ALL, set: current !== "",
    items: [item("", ALL, current === "", patch("")),
            ...listed.map(([value, words]) => item(value, words, value === current, patch(value)))],
  };
}

/** Each of `values` once, `value` added when it is not among them and taken away when it is. */
function toggled(values: string[], value: string): string[] {
  return values.includes(value) ? values.filter((other) => other !== value) : [...values, value];
}

/** The project (or folder) menu: `all`, then the entries, any number of them picked, those
 * picked that are not entries first. The button counts those picked by `noun`, one
 * included (`1 project`). */
function places(label: string, noun: string, current: string[], entries: [string, string][],
                patch: (values: string[]) => Partial<PageState>, name: (value: string) => string): Menu {
  const listed = [...current.filter((value) => !entries.some(([key]) => key === value)).map((value): [string, string] => [value, name(value)]),
                  ...entries];
  return {
    key: "place", label,
    value: current.length === 0 ? ALL : countWords(current.length, noun),
    set: current.length > 0,
    items: [item("", ALL, current.length === 0, patch([])),
            ...listed.map(([value, words]) => severalItem(value, words, current.includes(value), patch(toggled(current, value))))],
  };
}

/** The first day a custom range opens with: the range in force, or the last 30 days. */
function customDays(range: Range, today: string): CustomDays {
  const days = rangeDays(range, today) ?? rangeDays(DEFAULT_RANGE, today) as { since: string; until: string };
  return { from: "From", to: "to", act: "Set", since: days.since, until: days.until };
}

/** The range two typed days make, or null when they are not two days in order. */
export function customRange(since: string, until: string): Range | null {
  const span = daysFrom(since, until);
  return Number.isNaN(span) || span < 0 ? null : { kind: "custom", since, until };
}

/**
 * The filter line. `Range`, `Cost / Tokens` and `By class / By model` are always there. The project (or
 * folder) filter is there once its list has answered. Harness and Model are there only
 * with more than one to choose, or when one is set: the harnesses are those whose lanes
 * hold files, the models those of the range under the other filters.
 */
export function filterLine(facts: FilterFacts): FilterLine {
  const { state, today } = facts;
  const range: Menu = {
    key: "range", label: "Range", value: rangeLabel(state.range, today), set: state.range.kind !== DEFAULT_RANGE.kind,
    items: rangeMenu(state.range, facts.figures, facts.allTime).map((entry): MenuItem => ({
      ...entry, several: false, patch: entry.key === "custom" ? null : { range: { kind: entry.key } },
    })),
  };

  let place: Menu | null = null;
  const home = facts.places?.home;
  const folder = (path: string): string => tilde(path, home);
  if (facts.places !== null || state.projects.length || state.paths.length) {
    const rows = facts.places?.rows ?? [];
    if (facts.folders || state.paths.length) {
      // By folder, and by project while a folder is picked: the folders lead the list.
      const paths = facts.folders ? [...new Set(rows.map((row) => row.cwd).filter((path): path is string => !!path))] : [];
      const keys = facts.folders ? [] : [...new Set(rows.map((row) => row.project).filter((key): key is string => !!key))];
      const menu = places(facts.folders ? "Folder" : "Project", "folder", state.paths,
                          paths.map((path) => [path, folder(path)]), (values) => ({ paths: values, projects: [] }), folder);
      menu.items.push(...keys.map((key) => severalItem(`project:${key}`, key, false, { projects: [key], paths: [] })));
      place = menu;
    } else {
      const keys = [...new Set(rows.map((row) => row.project).filter((key): key is string => !!key))];
      place = places("Project", "project", state.projects, keys.map((key) => [key, key]),
                     (values) => ({ projects: values, paths: [] }), (value) => value);
    }
  }

  const harnesses = facts.lanes === null ? [] : filesByHarness(facts.lanes).map((found) => found.harness);
  const harness = harnesses.length > 1 || state.harness
    ? chooser("harness", "Harness", state.harness, harnesses.map((name) => [name, harnessName(name)]),
              (value) => ({ harness: value }), harnessName) : null;

  const ids = [...new Set((facts.models?.rows ?? []).map((row) => row.model).filter((id): id is string => !!id))];
  const model = ids.length > 1 || state.model
    ? chooser("model", "Model", state.model, ids.map((id) => [id, id]), (value) => ({ model: value }), (value) => value)
    : null;

  return {
    range, custom: customDays(state.range, today), place, harness, model,
    measures: [{ key: "cost", label: "Cost", picked: state.measure === "cost" },
               { key: "tokens", label: "Tokens", picked: state.measure === "tokens" }],
    splits: (["class", "model"] as const).map((key) => ({
      key, label: key === "class" ? "By class" : "By model", picked: state.split === key,
      off: state.measure === "tokens" ? SPLIT_OFF : null,
    })),
  };
}
