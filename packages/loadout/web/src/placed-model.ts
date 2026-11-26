// What the page's Placed group says, from `/api/view`: each problem in three lines (what;
// section, harnesses and why; the fix and its act), in order, the verdict over them, and
// the counts per harness. No DOM, so the Node tests read it.
//
// A problem is one item and one state word: the item's rows that read that word, their
// harnesses gathered. A section that can't be placed is one problem and its items' rows
// are not counted again; the Codex rules file is one problem naming the items it holds;
// the global file's part is one problem whatever harnesses it gathers. A removal and an
// edited ignore file are problems too.

import type { View, ViewRow, ViewSection } from "./feed.ts";
import { FINE } from "./feed.ts";
import {
  countsLine, harnessCounts, isRulesRow, itemKey, itemsView, refusedNames, scopeOf,
} from "./items-model.ts";
import type { ItemsView } from "./items-model.ts";
import {
  counted, forHarnesses, harnessesLine, homeForm, joinAnd, kindWord, namesLine,
  parentOf, sectionWord, stateWord, wordRank,
} from "./words.ts";

/** The sections an act runs under: the user section, one project section, or a whole run
 * (no section named). */
export type ActScope = { user: true } | { project: string } | { whole: true };

/** The act a problem's fix offers. */
export interface ProblemAct {
  verb: "install" | "replace";
  scope: ActScope;
  /** What `--replace` names: `<kind>:<name>@<harness>`, `@*` for every harness of the
   * problem, or the target path; null for `install`. */
  id: string | null;
  /** The section the Replace confirm opens under (`user` or a project section's name);
   * null for `install`. */
  section: string | null;
}

/** One problem under Placed. */
export interface Problem {
  /** Unique on the page: `[section, kind, name, word]` as JSON. */
  key: string;
  /** The item the problem is about, `itemKey`'s form; a refused section's is its own. */
  item: string;
  /** `user`, a project section's name, or null for a removal whose record names none. */
  section: string | null;
  kind: string | null;
  name: string | null;
  /** The state word drawn at the right, weight 600. */
  word: string;
  /** Line one. */
  sentence: string;
  /** Line two: `<section> · <harnesses> · <why>`. */
  where: string;
  /** Line three. */
  fix: string;
  /** A dim note after the fix, or null. */
  fixNote: string | null;
  act: ProblemAct | null;
  /** The text `Copy` copies, for a refused section's path fix; else null. */
  copy: string | null;
  harnesses: string[];
  rows: ViewRow[];
  /** The problem is a section that can't be placed. */
  refused: ViewSection | null;
}

/** The scope a row's `applies` runs under, or null when no act applies it. */
export function actScope(applies: string | null, scope: string | null): ActScope | null {
  if (applies === "whole") return { whole: true };
  if (applies !== "section" || scope === null) return null;
  return scope === "user" ? { user: true } : { project: scope };
}

/** The flags a scope gives the verb: `--user`, `--project=<name>`, or none. */
export function scopeFlags(scope: ActScope): string[] {
  if ("user" in scope) return ["--user"];
  if ("project" in scope) return [`--project=${scope.project}`];
  return [];
}

/** The id `--replace` names for one row: `<kind>:<name>@<harness>`, else its target. The
 * Codex rules file is named by an item it holds, since any of their ids names it. */
export function rowId(row: ViewRow): string | null {
  if (row.covers !== null && row.covers.length > 0 && row.harness) {
    return `${row.kind}:${row.covers[0]}@${row.harness}`;
  }
  if (row.kind && row.name && row.harness && row.harness !== "*") return `${row.kind}:${row.name}@${row.harness}`;
  return row.target;
}

/** The argv of a problem's act after `moxi loadout`. */
export function actArgv(act: ProblemAct): string[] {
  if (act.verb === "install") return ["install", ...scopeFlags(act.scope)];
  return ["install", `--replace=${act.id}`, ...scopeFlags(act.scope)];
}

type Kind = "item" | "removal" | "rules" | "global" | "ignore";

function kindOf(row: ViewRow, listed: ReadonlySet<string>): Kind {
  if (row.kind === "global") return "global";
  if (row.kind === "ignore") return "ignore";
  if (isRulesRow(row)) return "rules";
  return listed.has(itemKey(row.scope, row.kind, row.name)) ? "item" : "removal";
}

/** What line one names: `The skill tidy`, `The Codex rules file`, `The global file`. */
function subjectOf(kind: Kind, row: ViewRow, home: string | null): string {
  if (kind === "global") return "The global file";
  if (kind === "rules") return "The Codex rules file";
  if (row.kind === null || row.name === null) {
    return `What loadout placed at ${homeForm(row.target ?? "", home)}`;
  }
  return `The ${kindWord(row.kind)} ${row.name}`;
}

const lower = (text: string): string => text.charAt(0).toLowerCase() + text.slice(1);

function sentenceOf(kind: Kind, word: string, row: ViewRow, sectionLabel: string, harnesses: string,
                    home: string | null): string {
  const subject = subjectOf(kind, row, home);
  if (kind === "ignore") {
    return `loadout's ignore file in ${homeForm(parentOf(row.target ?? row.name ?? ""), home)} was changed by hand`;
  }
  switch (word) {
    case "not placed": return `${subject} is listed and not placed`;
    case "changed":
      if (kind === "global") return `loadout's part of ${harnesses}'s global file was changed by hand`;
      return `${subject} was changed by hand`;
    case "in the way": return `Something else is where ${lower(subject)} goes`;
    case "out of date":
      return kind === "removal" ? `${subject} is no longer listed and still placed` : `${subject} is out of date`;
    case "would be committed": return `git would commit ${lower(subject)} in ${sectionLabel}`;
    case "can't be read": return `${subject} can't be read from your folder`;
    default: return `${subject} reads ${word}`;
  }
}

/** Line two's `why` for one row, or null where the table gives none. */
function whyOf(kind: Kind, row: ViewRow, harnesses: string, home: string | null): string | null {
  const target = homeForm(row.target ?? "", home);
  if (kind === "rules" && row.state === "modified") return `${target} holds ${joinAnd(row.covers ?? [])}`;
  if (kind === "global" && row.state === "modified") return target;
  if (row.state === "modified" && row.form === "generated") {
    return `the file ${harnesses} reads, made from the source, was edited`;
  }
  if (row.state === "modified" && row.form === "entry") {
    return `its entry in ${target} was edited after loadout wrote it`;
  }
  if (row.state === "blocked" && row.edited && row.form === "copy") {
    return `${kindWord(row.kind ?? "")}s are copied on this machine, and ${harnesses}'s copy was edited`;
  }
  if (row.state === "blocked" && row.edited) return "a file now stands where loadout's link was";
  if (row.state === "blocked") return `${target} isn't loadout's`;
  return null;
}

/** The fix line and the act it offers, by the table's row for the problem's first row. */
function fixOf(kind: Kind, word: string, row: ViewRow, harnesses: readonly string[]): {
  fix: string; act: "install" | "replace" | null;
} {
  if (kind === "ignore") return { fix: row.reason, act: null };
  if (word === "not placed") return { fix: `Install places it ${forHarnesses(harnesses)}`, act: "install" };
  if (word === "out of date") {
    if (kind !== "removal") return { fix: "Install places the new version", act: "install" };
    if (row.applies === null) return { fix: "loadout takes it out once every section can be placed", act: null };
    return { fix: "Install takes it out", act: "install" };
  }
  if (word === "would be committed") {
    if (row.replaceable) return { fix: "git tracks what is there; Replace moves it aside and places yours", act: "replace" };
    if (row.ignores) return { fix: "Install adds it to loadout's ignore file", act: "install" };
    return { fix: row.reason, act: null };
  }
  if (word === "in the way") return { fix: "Replace moves it aside and places yours", act: "replace" };
  if (word === "changed") {
    if (row.state === "blocked") {
      return { fix: "Copy your edit into the folder to keep it, or put the folder's version back", act: "replace" };
    }
    if (kind === "rules") {
      return { fix: "Change the rules in loadout.toml instead, or put loadout's file back", act: "replace" };
    }
    if (kind === "global") return { fix: "Change it in global.md instead, or put loadout's part back", act: "replace" };
    if (row.form === "generated") {
      return { fix: "Make the change in the source instead, or put loadout's version back", act: "replace" };
    }
    if (row.form === "entry") return { fix: "Change it in loadout.toml instead, or put loadout's entry back", act: "replace" };
  }
  return { fix: row.reason, act: null };
}

/** The act for a problem's rows. Install runs under the widest `applies` among them; a
 * Replace names its one row, or `@*` when it gathers every replaceable row of the item in
 * that section. None when no run applies them. */
function actOf(verb: "install" | "replace" | null, rows: readonly ViewRow[], all: readonly ViewRow[]): ProblemAct | null {
  if (verb === null) return null;
  const usable = rows.filter((row) => row.applies !== null && (verb === "install" || row.replaceable));
  if (usable.length === 0) return null;
  const first = usable[0];
  const whole = usable.find((row) => row.applies === "whole");
  const scope = actScope((whole ?? first).applies, first.scope);
  if (scope === null) return null;
  if (verb === "install") return { verb, scope, id: null, section: null };
  let id = rowId(first);
  if (usable.length > 1 && first.kind !== null && first.name !== null) {
    const same = all.filter((row) => row.replaceable && row.scope === first.scope && row.kind === first.kind
                                     && row.name === first.name);
    if (same.every((row) => usable.includes(row))) id = `${first.kind}:${first.name}@*`;
  }
  if (id === null) return null;
  return { verb, scope, id, section: first.scope ?? "user" };
}

function sectionProblem(section: ViewSection, view: View): Problem {
  const refusal = section.refused!;
  const scope = scopeOf(section);
  const names = view.items.filter((item) => item.scope === scope).map((item) => item.name);
  const rows = view.rows.filter((row) => row.scope === scope);
  const used = new Set(rows.filter((row) => row.state !== "off").map((row) => row.harness ?? ""));
  const harnesses = view.harnesses.filter((h) => used.has(h));
  const label = sectionWord(section, view.home);
  const keyed = refusal.cause === "no-projects";
  const sentence = keyed ? `The section ${label} is named by a project key, so nothing in it is placed`
    : `Nothing in the section ${label} is placed`;
  let fix = refusal.line;
  let fixNote: string | null = null;
  let copy: string | null = null;
  if (keyed && refusal.last_root !== null) {
    copy = `[project."${homeForm(refusal.last_root, view.home)}"]`;
    fix = `Name the section by its path: ${copy}`;
    fixNote = "where loadout last placed it";
  } else if (keyed) {
    fix = "Name the section by its path";
  }
  const where = ["loadout.toml", namesLine(names), harnessesLine(harnesses)].filter((part) => part !== "").join(" · ");
  return { key: JSON.stringify([section.name, null, null, "not placed"]), item: JSON.stringify(["section", section.name]),
           section: section.name, kind: null, name: null, word: "not placed", sentence, where, fix, fixNote,
           act: null, copy, harnesses, rows, refused: section };
}

/** Each problem, in order: by word (`changed` first), then section (Your items first, then
 * the manifest's order, a removal naming none last), then name. */
export function problemsOf(view: View): Problem[] {
  const refused = refusedNames(view);
  const listed = new Set(view.items.map((item) => itemKey(item.scope, item.kind, item.name)));
  const groups = new Map<string, { kind: Kind; word: string; rows: ViewRow[] }>();
  for (const row of view.rows) {
    if (FINE.has(row.state)) continue;
    if (row.scope !== null && refused.has(row.scope)) continue;
    const kind = kindOf(row, listed);
    const word = kind === "ignore" ? "changed" : stateWord(row);
    const key = JSON.stringify([row.scope, row.kind, row.name, word]);
    const found = groups.get(key);
    if (found) found.rows.push(row);
    else groups.set(key, { kind, word, rows: [row] });
  }
  const sections = new Map(view.sections.map((s) => [scopeOf(s), s]));
  const out: Problem[] = [];
  for (const [key, { kind, word, rows }] of groups) {
    const row = rows[0];
    const section = row.scope === null ? null : sections.get(row.scope) ?? null;
    const label = section !== null ? sectionWord(section, view.home)
      : row.scope === "user" ? "Your items" : row.scope ?? "";
    const harnesses = [...new Set(rows.map((r) => r.harness).filter((h): h is string => h !== null && h !== "*"))];
    const named = harnessesLine(harnesses);
    const whys = [...new Set(rows.map((r) => whyOf(kind, r, named, view.home)).filter((w): w is string => w !== null))];
    const where = [label, named, whys.join("; ")].filter((part) => part !== "").join(" · ");
    const sentence = sentenceOf(kind, word, row, label, named, view.home);
    const { fix, act } = fixOf(kind, word, row, harnesses);
    out.push({ key, item: kind === "ignore" ? JSON.stringify(["ignore", row.name]) : itemKey(row.scope, row.kind, row.name),
               section: row.scope, kind: row.kind, name: row.name, word, sentence, where, fix, fixNote: null,
               act: actOf(act, rows, view.rows), copy: null, harnesses, rows, refused: null });
  }
  for (const section of view.sections) {
    if (section.where !== "user" && section.refused !== null) out.push(sectionProblem(section, view));
  }
  const rank = (section: string | null): number => {
    if (section === "user") return -1;
    const at = view.sections.findIndex((s) => s.where !== "user" && s.name === section);
    return at < 0 ? view.sections.length : at;
  };
  return out.sort((a, b) => wordRank(a.word) - wordRank(b.word) || rank(a.section) - rank(b.section)
    || (a.name ?? "").localeCompare(b.name ?? ""));
}

/** The verdict over the problems: items counted once each (the rules file once, the global
 * part once), sections apart. */
export function verdictLine(problems: readonly Problem[]): string {
  const sections = problems.filter((p) => p.refused !== null).length;
  const items = new Set(problems.filter((p) => p.refused === null).map((p) => p.item)).size;
  if (items === 0 && sections === 0) return "Everything is placed and unchanged";
  if (sections === 0) return `${counted(items, "item")} ${items === 1 ? "needs" : "need"} you`;
  if (items === 0) return `${counted(sections, "section")} ${sections === 1 ? "needs" : "need"} you`;
  return `${counted(items, "item")} and ${counted(sections, "section")} need you`;
}

/** Placed as the page draws it. */
export interface PlacedView {
  verdict: string;
  /** The verdict is heavy: something needs the user. */
  heavy: boolean;
  problems: Problem[];
  /** The counts line: at the verdict's right with nothing needing the user, else the dim line
   * under the problems. */
  counts: string;
  /** Where `Install all` sits: at the verdict's right when two or more problems' act is
   * Install, else dim at the counts line's right. */
  installAll: "verdict" | "counts";
}

/** Install all: a whole install. */
export const INSTALL_ALL: ProblemAct = { verb: "install", scope: { whole: true }, id: null, section: null };

export function placedView(view: View, items: ItemsView = itemsView(view)): PlacedView {
  const problems = problemsOf(view);
  const fine = problems.length === 0;
  const installs = problems.filter((p) => p.act?.verb === "install").length;
  return { verdict: verdictLine(problems), heavy: !fine, problems,
           counts: countsLine(harnessCounts(items), fine), installAll: installs >= 2 ? "verdict" : "counts" };
}
