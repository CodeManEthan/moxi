// The page's frame as it draws it, from `/api/view` and `/api/folder`: which state the page
// is in, the Folder line, the empty state's sentences, the `This page` group while the
// harness homes' note is set, the dim foot lines, and each act as the user would type it.
// No DOM, so the Node tests read it.

import type { SourceDir, View } from "./feed.ts";
import { FINE, parseView } from "./feed.ts";
import { commandOf } from "./acts-model.ts";
import { actArgv } from "./placed-model.ts";
import type { Problem, ProblemAct } from "./placed-model.ts";
import { modeWord } from "./items-model.ts";
import { harnessesLine, homeForm } from "./words.ts";

/** Nothing shows this long after the page opens, then `Reading…`. */
export const READING_AFTER_MS = 400;
export const READING = "Reading…";
/** How often the page reads again while its tab is shown. */
export const REFRESH_MS = 60_000;
export const GONE = "loadout is no longer installed.";
export const SETTINGS_LINK = "Settings › loadout";
export const SETTINGS_ROUTE = "settings/loadout";
export const COPY = "Copy";
export const COPIED = "Copied";
/** How long a copy act reads `Copied`. */
export const COPIED_MS = 1500;

/** What the page draws: nothing yet, `Reading…`, `loadout is no longer installed.`, the
 * view's `problem` in the first group's place, the empty state (no item listed and no row
 * to act on), or the page, which with no item listed still shows in Placed what loadout
 * placed and no section lists now. A read that failed with nothing read before is
 * `failed`. */
export type PageState = "waiting" | "reading" | "gone" | "failed" | "problem" | "empty" | "up";

/** The view feed as the page holds it. */
export interface ViewRead {
  loading: boolean;
  gone: boolean;
  failure: { why: string } | null;
  value: View | null;
}

export function pageState(read: ViewRead, waitedMs: number): PageState {
  if (read.gone) return "gone";
  const view = read.value;
  if (view === null) {
    if (read.loading) return waitedMs >= READING_AFTER_MS ? "reading" : "waiting";
    return "failed";
  }
  if (view.problem !== null) return "problem";
  return view.items.length === 0 && view.rows.every((row) => FINE.has(row.state)) ? "empty" : "up";
}

/** `view` cut to the harnesses the page draws (`shown`): a harness of the table that isn't
 * on this machine and has nothing of loadout's there loses its column, its rows, its item
 * notes and its global file, and its rows leave the counts, so every part of the page
 * (cells, Placed, the verdict, the opened item) reads only what is drawn. The page cuts
 * each view as it reads it. */
export function shownOnly(view: View): View {
  const shown = new Set(view.shown);
  const hidden = new Set(view.harnesses.filter((harness) => !shown.has(harness)));
  if (hidden.size === 0) return view;
  const kept = (harness: string | null): boolean => harness === null || !hidden.has(harness);
  const counts = { ...view.counts };
  for (const row of view.rows) {
    if (kept(row.harness) || counts[row.state] === undefined) continue;
    counts[row.state] -= 1;
    if (counts[row.state] <= 0) delete counts[row.state];
  }
  return {
    ...view, counts,
    harnesses: view.harnesses.filter((harness) => !hidden.has(harness)),
    rows: view.rows.filter((row) => kept(row.harness)),
    item_notes: view.item_notes.filter((note) => kept(note.harness)),
    global: { ...view.global, files: view.global.files.filter((file) => kept(file.harness)) },
  };
}

/** `/api/view`'s value cut to the harnesses the page draws, or null when it isn't one. */
export function parseShownOnly(data: unknown): View | null {
  const view = parseView(data);
  return view === null ? null : shownOnly(view);
}

/** The line a view that never read shows. */
export function failedLine(why: string): string {
  return `The page can't be read: ${why}.`;
}

/** The Folder group's lines. */
export interface FolderView {
  /** The folder in `~` form. */
  path: string;
  /** `<n> files changed, not committed`, `nothing uncommitted`, or `loadout.toml lists
   * nothing` in the empty state; null outside a work tree, or when git can't be read. */
  line: string | null;
  /** Line two: the names, then `· a commit places them again` with repo; null when there
   * is nothing to say. */
  names: string | null;
}

const RELOADS = "a commit places them again";

export function folderView(view: View, dir: SourceDir | null, { empty = false } = {}): FolderView | null {
  if (view.folder === null) return null;
  const path = homeForm(view.folder, view.home);
  if (empty) return { path, line: "loadout.toml lists nothing", names: null };
  if (dir === null || dir.state !== "ok" || dir.uncommitted === null) return { path, line: null, names: null };
  const count = dir.uncommitted.length + dir.more;
  if (count === 0) return { path, line: "nothing uncommitted", names: null };
  const line = `${count} ${count === 1 ? "file" : "files"} changed, not committed`;
  const names = [...dir.uncommitted];
  if (dir.more > 0) names.push(`and ${dir.more} more`);
  return { path, line, names: `${names.join(", ")}${dir.reinstalls ? ` · ${RELOADS}` : ""}` };
}

/** The empty state: the quiet line and the sentence under it, naming the harnesses of the
 * table. */
export function emptyLines(view: View): { line: string; parts: Array<{ text: string; code: boolean }> } {
  const harnesses = harnessesLine(view.harnesses);
  return {
    line: "Nothing is listed yet",
    parts: [
      { text: "Put a skill in ", code: false }, { text: "skills/<name>/SKILL.md", code: true },
      { text: " or an agent file in ", code: false }, { text: "agents/<name>.md", code: true },
      { text: ` in your loadout folder. It shows here to add, and loadout places it${
        harnesses ? ` for ${harnesses}` : ""}.`, code: false },
    ],
  };
}

/** The Global file's group in the empty state: its mode, and for a mode that touches no
 * file the sentence that says so. */
export function emptyGlobal(view: View): { mode: string; line: string | null } {
  const word = modeWord(view.global.mode, view.global.chosen);
  return { mode: word[0].toUpperCase() + word.slice(1),
           line: word === "off" ? "loadout touches no global file" : null };
}

/** The `This page` group while the harness homes' note is set. */
export interface NoteGroup {
  label: string;
  heading: string;
  /** Dim, at the heading's right. */
  aside: string;
  note: string;
  last: string;
}

export function noteGroup(view: View): NoteGroup | null {
  if (view.note === null) return null;
  return { label: "This page", heading: "Changes from this page are off", aside: "read only",
           note: view.note, last: "Or run each change in a terminal." };
}

/** `text` as a sentence: its first letter capital, and a stop at its end. */
const sentence = (text: string): string => `${text[0].toUpperCase()}${text.slice(1)}${/[.!?]$/.test(text) ? "" : "."}`;

/** The run's sentence that no run has confirmed what loadout writes for a harness begins so. */
const UNCONFIRMED = "not yet confirmed by a run:";

/** Whether `note`, one of the view's notes, is drawn on an item already: the run's sentence
 * about one item (`hook guard-rm: …`), or the sentence that no run has confirmed a harness,
 * when an item the page lists carries the note (`item_notes`) that stands beside it. */
function onAnItem(note: string, view: View): boolean {
  return view.item_notes.some((n) => view.items.some((i) => i.scope === "user" && i.kind === n.kind && i.name === n.name)
    && (n.code === "unconfirmed" ? note.startsWith(UNCONFIRMED) && note.includes(n.harness)
      : note.startsWith(`${n.kind} ${n.name}: `)));
}

/** The dim lines at the page's foot: each of the view's notes that the page doesn't draw on
 * an item, other than the homes' note, a line each as a sentence; and the folder read's
 * failure. */
export function footLines(view: View | null, dir: SourceDir | null): string[] {
  const out: string[] = [];
  for (const note of view?.notes ?? []) {
    if (note !== "" && note !== view?.note && !onAnItem(note, view!)) out.push(sentence(note));
  }
  if (dir !== null && dir.state !== "ok" && dir.detail) out.push(sentence(dir.detail));
  return out;
}

/** A problem's act as the user would type it, with the note set. */
export function actCommand(act: ProblemAct): string {
  return commandOf(actArgv(act));
}

/** A problem's fix line: with the note set and a Replace to type, it ends `with`, since
 * the command follows it. */
export function fixLine(problem: Problem, { typed }: { typed: boolean }): string {
  return typed && problem.act?.verb === "replace" ? `${problem.fix} with` : problem.fix;
}

// -- where the line under an act goes ---------------------------------------------------------

/** The place of an act on the page: a problem's fix (`problem:<key>`), `Install all`, an
 * opened item (`item:<key>`) or the opened global file. */
export const problemPlace = (key: string): string => `problem:${key}`;
export const INSTALL_ALL_PLACE = "install-all";
export const itemPlace = (key: string): string => `item:${key}`;
export const GLOBAL_PLACE = "global";
/** Under Placed's counts, for an act whose place the page no longer draws. */
export const PLACED_PLACE = "placed";

/** Where the line under an act goes: the act's own place when the page draws it, else
 * under Placed when Placed is drawn, else (null) at the page's foot. */
export function jobPlace(place: string | null, drawn: ReadonlySet<string>, { placed }: { placed: boolean }): string | null {
  if (place !== null && drawn.has(place)) return place;
  return placed ? PLACED_PLACE : null;
}
