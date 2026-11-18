// What needs him, from `/api/repos`: one item per thing he fixes, each word one repo
// states, and the verdict line over them. No DOM, so the Node tests read it.
//
// Per repo, items come from its problems, its events' chain lines, its open records and
// its specs. A record that says the same thing as a problem or a spec is merged into that
// item (its first line and time join it, and `Show` opens its whole text) and makes no
// item of its own. A spec's commit-record states are tried in order (refused, failed,
// held up by git's index lock, any other open commit record) and the first that holds is
// its item; each branch whose push failed is one more.

import type { EventRow, ProblemRow, RecordRow, RepoRow, Repos, SpecRow } from "./feed.ts";
import {
  canRefuse, type Clock, counted, eventWords, firstLine, homeForm, LOCAL, pieceName, recordSeconds,
  refuseWord, repoName, whenWord,
} from "./words.ts";

/** `need` counts as a thing that needs him; `warn` as a warning. */
export type Weight = "need" | "warn";

/** What an item was made from: the row of the item table it comes from. */
export type Source =
  | "no-module" | "gone" | "chain" | "problem" | "piece" | "refused" | "failed" | "held"
  | "commit" | "push" | "specs";

/** An item's fix: a label over a text he copies, one sentence, or the Refresh act. */
export interface Fix {
  /** `Take the piece out`, `Drop it from the list`; null for none. */
  label: string | null;
  /** The command or line shown, or the sentence; null for none. */
  text: string | null;
  /** What its `Copy` puts on the clipboard: the command that runs as it stands, without an
   * `(or: ...)` alternative the text adds; null for no `Copy`. */
  copy: string | null;
  /** The item offers the Refresh act. */
  refresh: boolean;
}

export interface Item {
  weight: Weight;
  source: Source;
  repo: RepoRow;
  /** The repo's name as the page writes it. */
  name: string;
  /** The heavy first line. */
  title: string;
  /** The dim second line's parts, joined ` · `; none for no second line. */
  dim: string[];
  fix: Fix | null;
  /** The time at the right in epoch seconds, or null for none. */
  time: number | null;
  /** The time reads `since <t>` (a run of refused, failed or index-busy passes). */
  since: boolean;
  /** The record the item was made from or merged, for `Show`; null for none. */
  record: RecordRow | null;
  /** The problem it comes from, when it comes from one. */
  problem: ProblemRow | null;
  /** The spec it is about, when it is about one. */
  spec: SpecRow | null;
}

function fixOf(text: string | null, { label = null, refresh = false }: { label?: string | null; refresh?: boolean } = {}
): Fix | null {
  if (text === null && !refresh) return null;
  return { label, text, copy: text === null ? null : runnable(text), refresh };
}

/** A fix's command as it runs: the text before an `(or: ...)` alternative at its end. */
export function runnable(text: string): string {
  const at = text.lastIndexOf(" (or: ");
  return at > 0 && text.endsWith(")") ? text.slice(0, at) : text;
}

function sentence(text: string): Fix {
  return { label: null, text, copy: null, refresh: false };
}

/** Whether a repo's folder is gone: a `gone` problem names it. */
export function isGone(repo: RepoRow): boolean {
  return repo.problems.some((problem) => problem.code === "gone");
}

/** The time a fact says, worded: bare, or `since <t>`. */
export function timeWords(item: Pick<Item, "time" | "since">, today: string | null, clock: Clock = LOCAL): string | null {
  if (item.time === null) return null;
  const when = whenWord(item.time, today, clock);
  return item.since ? `since ${when}` : when;
}

interface Context {
  repo: RepoRow;
  name: string;
  home: string | null;
  today: string | null;
  clock: Clock;
  /** Records merged into an item. */
  merged: Set<RecordRow>;
}

function item(ctx: Context, weight: Weight, source: Source, title: string,
              rest: Partial<Pick<Item, "dim" | "fix" | "time" | "since" | "record" | "problem" | "spec">> = {}): Item {
  return { weight, source, repo: ctx.repo, name: ctx.name, title, dim: [], fix: null, time: null, since: false,
           record: null, problem: null, spec: null, ...rest };
}

const at = (ctx: Context, time: number): string => whenWord(time, ctx.today, ctx.clock);

/** The open record of `kind` that `match` picks, not yet merged. */
function openRecord(ctx: Context, kind: string, match: (record: RecordRow) => boolean): RecordRow | null {
  return ctx.repo.records.find((record) => record.kind === kind && !ctx.merged.has(record) && match(record)) ?? null;
}

function merge(ctx: Context, record: RecordRow | null): RecordRow | null {
  if (record !== null) ctx.merged.add(record);
  return record;
}

function problemItem(ctx: Context, problem: ProblemRow): Item | null {
  if (problem.state !== "fail" && problem.state !== "warn") return null;
  if (problem.code === "no-module" && problem.state === "fail") {
    const piece = problem.piece === null ? "a piece" : pieceName(problem.piece);
    const event = problem.event ?? "";
    const refusing = canRefuse(event);
    const record = merge(ctx, openRecord(ctx, "piece", (r) => r.piece === problem.piece));
    const time = record === null ? null : recordSeconds(record.at);
    const dim = [refusing ? "no installed module declares it" : "no installed module declares it, so it doesn't run"];
    if (record !== null) dim.push(time === null ? "it broke" : `it broke at ${at(ctx, time)}`);
    const title = refusing ? `${ctx.name} refuses every ${refuseWord(event)}: ${piece} has no module`
      : `${ctx.name}: ${piece} has no module`;
    return item(ctx, "need", "no-module", title,
                { dim, fix: fixOf(problem.fix, { label: "Take the piece out" }), time, record, problem });
  }
  if (problem.code === "gone") {
    return item(ctx, "warn", "gone", `${ctx.name}'s folder is gone`, {
      dim: [homeForm(ctx.repo.root, ctx.home), "its hooks went with it; only this list still names it"],
      fix: fixOf(problem.fix, { label: "Drop it from the list" }), problem,
    });
  }
  return item(ctx, problem.state === "fail" ? "need" : "warn", "problem", `${ctx.name}: ${problem.detail}`,
              { fix: fixOf(problem.fix, { refresh: problem.refresh }), problem });
}

function chainItem(ctx: Context, event: EventRow): Item | null {
  if (event.chain_line !== "needed") return null;
  return item(ctx, "warn", "chain", `${ctx.name}: ${eventWords(event.event)} needs a line in another tool's hook`, {
    dim: event.hook === null ? [] : [homeForm(event.hook, ctx.home)],
    fix: fixOf(event.line),
  });
}

function lastCommit(ctx: Context, spec: SpecRow): string[] {
  return spec.last_commit_time === null ? [] : [`last commit ${at(ctx, spec.last_commit_time)}`];
}

/** The spec's one item from its commit record's states, or null when none holds. */
function specItem(ctx: Context, spec: SpecRow): Item | null {
  const label = `Auto-commit ${spec.name}`;
  const commit = (): RecordRow | null => merge(ctx, openRecord(ctx, "commit", (r) => r.spec === spec.name));
  const first = (record: RecordRow | null): string[] => (record === null ? [] : [firstLine(record.text)]);
  if (spec.refused_since !== null) {
    const record = commit();
    const passes = spec.refused_passes === null ? [] : [`refused ${counted(spec.refused_passes, "pass", "passes")}`];
    return item(ctx, "need", "refused", `${label} is refused`, {
      dim: [...first(record), ctx.name, ...passes, ...lastCommit(ctx, spec)],
      fix: sentence("Its next pass that commits clears this."), time: spec.refused_since, since: true, record, spec,
    });
  }
  if (spec.error_since !== null) {
    const record = commit();
    return item(ctx, "need", "failed", `${label} failed`, {
      dim: [...first(record), ctx.name, ...lastCommit(ctx, spec)],
      fix: fixOf(record?.fix ?? null), time: spec.error_since, since: true, record, spec,
    });
  }
  if (spec.index_stale) {
    const record = commit();
    return item(ctx, "need", "held", `${label} is held up by git's index lock`, {
      dim: [...first(record), ctx.name], fix: fixOf(record?.fix ?? null), time: spec.index_busy_since,
      since: spec.index_busy_since !== null, record, spec,
    });
  }
  return null;
}

function commitItem(ctx: Context, record: RecordRow, spec: SpecRow | null): Item {
  return item(ctx, "warn", "commit", firstLine(record.text), {
    dim: [ctx.name], fix: fixOf(record.fix), time: recordSeconds(record.at), record, spec,
  });
}

function pushItems(ctx: Context, spec: SpecRow): Item[] {
  return spec.push_failed.map((failed) => {
    const record = merge(ctx, openRecord(ctx, "push", (r) => r.spec === spec.name && r.branch === failed.branch));
    const gap = spec.unpushed.find((row) => row.branch === failed.branch);
    const dim = [ctx.name];
    if (gap !== undefined && gap.ahead !== null && gap.ahead > 0) {
      dim.push(`${counted(gap.ahead, "commit")} on ${failed.branch} not pushed`);
    }
    const title = `Auto-commit ${spec.name} can't push${failed.error === null ? "" : `: ${failed.error}`}`;
    return item(ctx, "need", "push", title, {
      dim, fix: sentence("The next pass pushes."), time: failed.failed_at, record, spec,
    });
  });
}

function pieceItem(ctx: Context, record: RecordRow): Item {
  const piece = record.piece === null ? "a piece" : pieceName(record.piece);
  return item(ctx, "need", "piece", `${ctx.name}: ${piece} broke`, {
    dim: [firstLine(record.text)], fix: fixOf(record.fix), time: recordSeconds(record.at), record,
  });
}

function specsItem(ctx: Context): Item | null {
  const texts = [ctx.repo.specs_problem, ...ctx.repo.specs.map((spec) => spec.problem)]
    .filter((text): text is string => text !== null);
  if (texts.length === 0) return null;
  return item(ctx, "warn", "specs", `${ctx.name}: its auto-commits can't be read`, { dim: texts });
}

/** One repo's items, in the table's order. */
export function repoItems(repo: RepoRow, { home = null, today = null, clock = LOCAL }:
  { home?: string | null; today?: string | null; clock?: Clock } = {}): Item[] {
  const ctx: Context = { repo, name: repoName(repo, home), home, today, clock, merged: new Set() };
  const out: Item[] = [];
  const push = (found: Item | null): void => {
    if (found !== null) out.push(found);
  };
  for (const problem of repo.problems) push(problemItem(ctx, problem));
  for (const event of repo.events) push(chainItem(ctx, event));
  for (const spec of repo.specs) push(specItem(ctx, spec));
  for (const spec of repo.specs) out.push(...pushItems(ctx, spec));
  for (const record of repo.records) {
    if (ctx.merged.has(record)) continue;
    if (record.kind === "piece") out.push(pieceItem(ctx, record));
    else if (record.kind === "commit") {
      out.push(commitItem(ctx, record, repo.specs.find((spec) => spec.name === record.spec) ?? null));
    }
  }
  push(specsItem(ctx));
  return out;
}

/** Needs before warnings, then by time, oldest first, an item with no time after those
 * with one; a gone repo's items last. Items that tie keep their order. */
export function itemOrder(a: Item, b: Item): number {
  const gone = Number(a.source === "gone") - Number(b.source === "gone");
  if (gone !== 0) return gone;
  const weight = Number(a.weight === "warn") - Number(b.weight === "warn");
  if (weight !== 0) return weight;
  if (a.time === null || b.time === null) return Number(a.time === null) - Number(b.time === null);
  return a.time - b.time;
}

/** A repo's items as its row leads with them: by weight (a need before a warning), then
 * newest first, an item with no time after those of its weight with one. Items that tie
 * keep their order. */
export function leadOrder(a: Pick<Item, "weight" | "time">, b: Pick<Item, "weight" | "time">): number {
  const weight = Number(a.weight === "warn") - Number(b.weight === "warn");
  if (weight !== 0) return weight;
  if (a.time === null || b.time === null) return Number(a.time === null) - Number(b.time === null);
  return b.time - a.time;
}

/** Every repo's items, in the order `Hooks` lists them. */
export function itemsOf(value: Pick<Repos, "repos" | "today">, { home = null, clock = LOCAL }:
  { home?: string | null; clock?: Clock } = {}): Item[] {
  return value.repos.flatMap((repo) => repoItems(repo, { home, today: value.today, clock })).sort(itemOrder);
}

/** Past this many items, the warnings fold to one line. */
export const FOLD_PAST = 5;

export interface Verdict {
  /** The heavy part: `2 things need you`, `Every repo runs its hooks.`, `No repo has a
   * hook yet.` */
  head: string;
  /** What follows it: `, 1 warning`, or `N repos · M pieces in place`; null for none. */
  rest: string | null;
  /** The head is heavy: only when something needs him. */
  heavy: boolean;
  needs: number;
  warnings: number;
  /** The items drawn one by one. */
  shown: Item[];
  /** The warnings folded to one line (`M warnings · Show`); none when nothing folds. */
  folded: Item[];
}

/** The verdict over `items`: with a need, `N things need you[, M warnings]`, heavy; with
 * warnings alone, `M warnings`; with nothing, `Every repo runs its hooks.` and how many repos and pieces are in place
 * (indexed repos whose folder is there, and the pieces on in them); with no repo
 * indexed, `No repo has a hook yet.` Past five items, the warnings fold. */
export function verdict(items: readonly Item[], repos: readonly RepoRow[]): Verdict {
  const needs = items.filter((one) => one.weight === "need").length;
  const warnings = items.length - needs;
  const fold = items.length > FOLD_PAST && warnings > 0;
  const shown = fold ? items.filter((one) => one.weight === "need") : [...items];
  const folded = fold ? items.filter((one) => one.weight === "warn") : [];
  const base = { needs, warnings, shown, folded };
  if (items.length > 0) {
    const head = needs > 0 ? `${needs} ${needs === 1 ? "thing needs" : "things need"} you`
      : counted(warnings, "warning");
    const rest = needs > 0 && warnings > 0 ? `, ${counted(warnings, "warning")}` : null;
    return { head, rest, heavy: needs > 0, ...base };
  }
  const placed = repos.filter((repo) => repo.indexed && !isGone(repo));
  if (placed.length === 0) return { head: "No repo has a hook yet.", rest: null, heavy: false, ...base };
  const pieces = placed.reduce((sum, repo) => sum + repo.pieces.filter((piece) => piece.enabled).length, 0);
  return { head: "Every repo runs its hooks.", rest: `${counted(placed.length, "repo")} · ${counted(pieces, "piece")} in place`,
           heavy: false, ...base };
}

/** The line `M warnings` folded under a Show. */
export function foldedLine(verdictOf: Pick<Verdict, "folded">): string | null {
  return verdictOf.folded.length === 0 ? null : counted(verdictOf.folded.length, "warning");
}
