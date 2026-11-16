// What Replace's confirm says, in place under its problem or harness line, from the rows
// it replaces and `/api/replace-preview`'s answer. Every case keeps what it replaces, so
// nothing here takes the warning colour. No DOM, so the Node tests read it.

import type { ReplacePreview, View, ViewRow } from "./feed.ts";
import { actScope } from "./placed-model.ts";
import type { ActScope } from "./placed-model.ts";
import { sectionInLine } from "./opened-model.ts";
import { harnessesLine, homeForm, joinAnd } from "./words.ts";

/** The diff's lines the confirm draws before `Show all`. */
export const DIFF_SHOWN = 200;

/** Each `skipped` reason in words. */
export const SKIPPED_WORDS: Readonly<Record<string, string>> = {
  link: "a link", large: "too large", binary: "not text", many: "past 200 files",
};

export interface ConfirmView {
  question: string;
  /** What happens to what is there now: `Moves …` or `Keeps …`; null with no case. */
  what: string | null;
  /** The lines shown only when their fact is set: tracked, the rules file, the others,
   * each skipped path. */
  notes: string[];
  /** The target in `~` form. */
  target: string | null;
  legend: string;
  /** The diff's first `DIFF_SHOWN` lines, and the rest behind `Show all`. */
  diff: string[];
  more: string[];
  /** `Show all <n> lines`, or null when every line is drawn. */
  showAll: string | null;
  /** `Stays …`: what the Replace leaves as it is. */
  stays: string | null;
  /** The act runs under this scope; null when nothing can be replaced. */
  scope: ActScope | null;
  /** The confirm offers `Replace`: the preview found a case. */
  canReplace: boolean;
}

type Form = "generated" | "entry" | "rules" | "global" | "placement";

function formOf(row: ViewRow): Form {
  if (row.kind === "global") return "global";
  if (row.covers !== null) return "rules";
  if (row.form === "generated") return "generated";
  if (row.form === "entry") return "entry";
  return "placement";
}

/** The confirm's question for `rows`, the rows the Replace names. */
export function questionOf(rows: readonly ViewRow[]): string {
  const row = rows[0];
  const harnesses = harnessesLine([...new Set(rows.map((r) => r.harness ?? "").filter((h) => h !== "" && h !== "*"))]);
  switch (formOf(row)) {
    case "rules": return "Put loadout's rules file back for Codex?";
    case "global": return `Put loadout's part of ${harnesses}'s global file back?`;
    case "generated":
    case "entry": return `Put loadout's version of ${row.name} back for ${harnesses}?`;
    default: return `Put the folder's version of ${row.name} back for ${harnesses}?`;
  }
}

/** What moves aside, by the row's form and `edited`. */
function movesWhat(row: ViewRow): string {
  if (row.edited && row.form === "copy") return "your edited copy";
  if (row.edited) return "the file that stands where loadout's link was";
  return "what is there";
}

/** Where a folder path sits under the loadout folder, else in `~` form. */
function inFolder(path: string, view: View): string {
  const folder = view.folder;
  if (folder !== null && path.startsWith(folder) && /^[\\/]/.test(path.slice(folder.length))) {
    return path.slice(folder.length + 1);
  }
  return homeForm(path, view.home);
}

/** `Stays …`: the folder's item or its source, and what the other harnesses have of it. */
function staysOf(rows: readonly ViewRow[], form: Form, view: View, target: string | null): string | null {
  const row = rows[0];
  const replaced = new Set(rows.map((r) => r.harness));
  const others = view.rows.filter((r) => r.scope === row.scope && r.kind === row.kind && r.name === row.name
                                         && r.state === "ok" && !replaced.has(r.harness));
  const names = harnessesLine(others.map((r) => r.harness ?? ""));
  if (form === "generated") {
    const item = view.items.find((i) => i.scope === row.scope && i.kind === row.kind && i.name === row.name);
    const source = item === undefined ? "the source" : `the source, ${inFolder(item.source, view)}`;
    return others.length === 0 ? `Stays ${source}` : `Stays ${source}, and the files ${names} read`;
  }
  if (form === "placement") {
    const kept = others.map((r) => `${harnessesLine([r.harness ?? ""])}'s ${r.form === "copy" ? "copy" : "link"}`);
    return kept.length === 0 ? `Stays the folder's ${row.name}` : `Stays the folder's ${row.name}, and ${joinAnd(kept)}`;
  }
  return target === null ? null : `Stays everything else in ${target}`;
}

/**
 * The confirm for a Replace of `rows` (the rows its id names, in one section), with the
 * preview's answer. Before the preview answers, `preview` is null: the question shows and
 * nothing else.
 */
export function confirmView(rows: readonly ViewRow[], preview: ReplacePreview | null, view: View): ConfirmView {
  const row = rows[0];
  const form = formOf(row);
  const scope = actScope(row.applies, row.scope);
  const question = questionOf(rows);
  const empty = { what: null, notes: [], target: null, legend: "", diff: [], more: [], showAll: null, stays: null };
  if (preview === null || preview.problem !== null || preview.case === null) {
    return { question, ...empty, scope, canReplace: false };
  }
  const keptIn = homeForm(preview.kept_in ?? "", view.home);
  const target = preview.target === null ? null : homeForm(preview.target, view.home);
  const until = `${keptIn}, where it stays until you delete it.`;
  let what: string;
  if (preview.case === "moves") what = `Moves ${movesWhat(row)} to ${until}`;
  else if (form === "generated") what = `Keeps the edited file as it is now in ${until}`;
  else what = `Keeps a copy of ${target ?? "the file"} as it is now in ${until}`;
  const notes: string[] = [];
  if (preview.tracked) {
    notes.push("git tracks it, so git will show it deleted, and the row reads would be committed until you commit that.");
  }
  if (form === "rules") notes.push("The whole file is written again, with every rule in it.");
  if (preview.others > 0) {
    notes.push(scope !== null && "whole" in scope
      ? `This runs a whole install, so ${preview.others} other changes waiting are made too.`
      : `This runs Install for ${sectionInLine(row.scope ?? "user", view)}, so ${preview.others} other changes waiting there are made too.`);
  }
  for (const skipped of preview.skipped) {
    notes.push(`Not shown here: ${homeForm(skipped.path, view.home)} (${SKIPPED_WORDS[skipped.why] ?? skipped.why})`);
  }
  const legend = preview.label === "entry"
    ? `the ${row.name} server's entry as it is now (−) and as loadout will send it (+)`
    : preview.case === "moves" && row.form === "copy"
      ? "lines with − are in the copy now, lines with + are your folder's"
      : "lines with − are in the file now, lines with + are what loadout writes";
  const all = preview.diff === "" ? [] : preview.diff.replace(/\n$/, "").split("\n");
  return { question, what, notes, target, legend, diff: all.slice(0, DIFF_SHOWN), more: all.slice(DIFF_SHOWN),
           showAll: all.length > DIFF_SHOWN ? `Show all ${preview.lines} lines` : null,
           stays: staysOf(rows, form, view, target), scope, canReplace: true };
}
