// What the page says under an act once its job ends: one dim line naming the command and
// how it ended, what the job kept with the way back (shown open), and behind `Show` its
// lines and its rows not fine in Placed's words. A refused section's line is never shown as
// the job wrote it, so the small install never reads the install hint it carries. No DOM,
// so the Node tests read it.

import type { Job, KeptCopy, Row, View, ViewRow } from "./feed.ts";
import { FINE } from "./feed.ts";
import { commandOf } from "./acts-model.ts";
import { problemsOf } from "./placed-model.ts";
import type { Problem } from "./placed-model.ts";
import { harnessWord, homeForm, kindWord, namesLine } from "./words.ts";

export interface JobLine {
  command: string;
  /** `running`, `done`, `done, but the section <name> still can't be placed`, `done, but
   * <what> still needs you`, `refused` or `failed`. */
  ended: string;
  line: string;
  /** `Show` is offered: the job has lines, or rows not fine. */
  hasMore: boolean;
}

/** The sections the view can't place, by name, with their problem. */
function refusedProblems(view: View): Map<string, Problem> {
  const out = new Map<string, Problem>();
  for (const problem of problemsOf(view)) {
    if (problem.refused !== null) out.set(problem.refused.name, problem);
  }
  return out;
}

const notFine = (job: Job): Row[] => (job.outcome?.rows ?? []).filter((row) => !FINE.has(row.state));

/** A row's section, for a row the run states for a whole section (`kind` `section`). */
const sectionOfRow = (row: Row): string | null => row.kind === "section" ? row.name : row.scope;

/** The item an act names, and its section: the id of `install --replace=<id>`, `enable
 * <id>` or `disable <id>`, with `--user` or `--project=<section>`; null for an act over
 * whole sections (an Install). */
function actTarget(argv: readonly string[]): { id: ParsedId; section: string | null } | null {
  let id: ParsedId | null = null;
  let section: string | null = null;
  for (const [at, word] of argv.entries()) {
    if (word.startsWith("--replace=")) id = parseId(word.slice("--replace=".length));
    else if (word === "--user") section = "user";
    else if (word.startsWith("--project=")) section = word.slice("--project=".length);
    else if (at === 1 && (argv[0] === "enable" || argv[0] === "disable")) id = parseId(word);
  }
  return id === null ? null : { id, section };
}

/** Whether `row` is one the act `target` names: its item (the Codex rules file by any rule
 * it holds) for its harness, in its section. */
function actsOn(row: Row, target: { id: ParsedId; section: string | null }, view: View | null): boolean {
  const { id, section } = target;
  if (section !== null && (row.scope ?? "user") !== section) return false;
  if (id.harness !== "*" && row.harness !== id.harness) return false;
  if (row.kind !== id.kind) return false;
  const covers = view?.rows.find((r) => r.scope === row.scope && r.kind === row.kind && r.name === row.name
                                        && r.harness === row.harness)?.covers ?? null;
  return row.name === id.name || (covers ?? []).includes(id.name);
}

/** What still needs the user after an act that did its own work: `the agent
 * code-reviewer`, `the section atlas-api`, past three `and <n> more`, with the verb that
 * agrees. */
function stillNeeds(rows: readonly Row[]): string {
  const names = [...new Set(rows.map((row) => (row.kind === "section" ? `the section ${row.name}`
    : `the ${kindWord(row.kind ?? "")} ${row.name}`)))];
  return `${namesLine(names)} still ${names.length === 1 ? "needs" : "need"} you`;
}

/** The dim line under the act once its job is read. An act that names one item (Replace,
 * Turn off, Turn on) and whose own rows ended fine reads `done`, naming what else in its
 * run still needs the user; it reads `refused` only when its own rows were refused. */
export function jobLine(job: Job, view: View | null): JobLine {
  const command = commandOf((job.argv ?? []).filter((word) => word !== "--json"));
  const rows = notFine(job);
  const lines = [...job.lines, ...(job.outcome?.lines ?? []), ...(job.outcome?.notes ?? [])];
  const hasMore = lines.length > 0 || rows.length > 0;
  let ended: string;
  if (job.state === "running") ended = "running";
  else if (job.state === "interrupted") ended = "failed";
  else if (job.exit === 0) ended = "done";
  else {
    const refused = view === null ? new Map<string, Problem>() : refusedProblems(view);
    const sections = [...new Set(rows.map(sectionOfRow))];
    const only = rows.length > 0 && sections.every((s) => s !== null && refused.has(s));
    const target = actTarget((job.argv ?? []).filter((word) => word !== "--json"));
    if (only) {
      const names = sections.map((s) => refused.get(s as string)!.refused!.name);
      ended = `done, but the section${names.length === 1 ? "" : "s"} ${names.join(", ")} still can't be placed`;
    } else if (job.exit === 3 && target !== null && rows.length > 0 && !rows.some((row) => actsOn(row, target, view))) {
      ended = `done, but ${stillNeeds(rows)}`;
    } else {
      ended = job.exit === 2 || job.exit === 3 || job.exit === 75 ? "refused" : "failed";
    }
  }
  return { command, ended, line: `${command}: ${ended}`, hasMore };
}

/** How the page stopped following a job before it ended: its read was refused (with the
 * answer's line), or it can't be read now (with why). */
export type Unfollowed = { refused: string } | { lost: string } | null;

/**
 * The line under the act for the job the page follows: `<command>: running` until its
 * first read, then `jobLine`'s, with `command` (the act's own) when the job names no argv.
 * A job the page stopped following says why: its read was refused, or it can't be read
 * now and may still run.
 */
export function followLine(job: Job | null, command: string, view: View | null, stopped: Unfollowed = null): JobLine {
  const ending = (ended: string, hasMore = false): JobLine => ({ command, ended, line: `${command}: ${ended}`, hasMore });
  if (stopped !== null && (job === null || job.state === "running")) {
    if ("refused" in stopped) return ending(stopped.refused);
    return ending(`can't be read now (${stopped.lost}); it may still be running`);
  }
  if (job === null) return ending("running");
  const line = jobLine(job, view);
  return job.argv === null ? ending(line.ended, line.hasMore) : line;
}

/** One copy the job kept, as the page shows it under the act. */
export interface KeptLine {
  /** `<target> as it was is kept at <copy>`. */
  text: string;
  /** What its `Copy` copies: the copy's path. */
  copy: string;
  /** How to put it back, dim, around `back`. */
  before: string | null;
  back: string;
  after: string;
}

interface ParsedId {
  kind: string;
  name: string;
  harness: string;
}

function parseId(id: string | null): ParsedId | null {
  const match = id === null ? null : /^([a-z]+):([^@]+)@([^@]+)$/.exec(id);
  return match === null ? null : { kind: match[1], name: match[2], harness: match[3] };
}

/** What the job kept, one entry per copy, with `back` drawn as the verb wrote it. */
export function keptLines(kept: readonly KeptCopy[], home: string | null): KeptLine[] {
  return kept.map((k) => {
    const text = `${homeForm(k.target, home)} as it was is kept at ${homeForm(k.copy, home)}`;
    const base = { text, copy: k.copy, back: k.back };
    if (k.how === "copied") {
      const after = k.id === null ? "puts it back as it was: the whole file, so later changes to it go too"
        : "puts it back as it was: loadout then reads it changed and leaves it";
      return { ...base, before: null, after };
    }
    if (k.how === "replaced") {
      const id = parseId(k.id);
      if (id === null) return { ...base, before: null, after: "puts it back" };
      if (id.kind === "global") {
        return { ...base, before: "This turns the global file off first, then", after:
          "puts it back; loadout leaves the global file off until you choose a mode again" };
      }
      const harness = id.harness === "*" ? "every harness" : harnessWord(id.harness);
      return { ...base, before: `This turns ${id.name} off for ${harness} first, then`,
               after: `puts it back; ${id.name} stays off for ${harness} until you turn it on` };
    }
    return { ...base, before: null, after: "puts it back" };
  });
}

/** What `Show` opens: each row not fine in Placed's words, and each line as the job wrote
 * it, but a line holding a refused section's line, which reads as that section's
 * sentence. */
export interface ShowView {
  problems: Array<Pick<Problem, "sentence" | "word" | "where" | "fix">>;
  lines: string[];
}

/** A job row with the view's facts on it: the view's row of the same item and harness,
 * else none of them. */
function withFacts(row: Row, view: View): ViewRow {
  const found = view.rows.find((r) => r.scope === row.scope && r.kind === row.kind && r.name === row.name
                                      && r.harness === row.harness);
  return { form: found?.form ?? null, edited: found?.edited ?? false, covers: found?.covers ?? null,
           prior: found?.prior ?? false, applies: found?.applies ?? null, ignores: found?.ignores ?? false, ...row };
}

export function showView(job: Job, view: View): ShowView {
  const rows = notFine(job);
  const refused = refusedProblems(view);
  const sections = new Set(rows.map(sectionOfRow).filter((s): s is string => s !== null && refused.has(s)));
  const own = rows.filter((row) => row.kind !== "section").map((row) => withFacts(row, view));
  const problems: ShowView["problems"] = problemsOf({ ...view, rows: own })
    .filter((p) => p.refused === null || sections.has(p.refused.name))
    .map(({ sentence, word, where, fix }) => ({ sentence, word, where, fix }));
  const lines = [...job.lines, ...(job.outcome?.lines ?? []), ...(job.outcome?.notes ?? [])].map((line) => {
    for (const [, problem] of refused) {
      const said = problem.refused?.refused?.line;
      if (said && line.includes(said)) return problem.sentence;
    }
    return line;
  });
  return { problems, lines };
}
