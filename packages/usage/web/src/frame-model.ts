// The page's frame as the view places it: the first `Reading…`, the limits, the block
// that stands in for everything else while nothing is counted, Cost, the foot and the dim
// lines under it. Worked out with no DOM from the page's reads as they stand. Every
// piece is the words the page shows, or null (or an empty list) when no read carries
// its fact: the view places what it is given and decides nothing.

import { CAPTION, COST_DAYS, costRows, countWords, PRICES_LINK, sessionsRead, whyNotWhole } from "./cost-model.ts";
import type { CostRow, Sum } from "./cost-model.ts";
import { addDays, dayPart, isDay, LOCAL, whenWord } from "./days.ts";
import type { Clock } from "./days.ts";
import type { DayRow, IndexEvent, Lane, Limits, PassOutcome, Report, Status } from "./feed.ts";
import { harnessName, inSentence, limitLabel, limitsModel } from "./limits-model.ts";
import type { LimitsModel } from "./limits-model.ts";

/** One read as the frame takes it; a `Feed` is one. A failed read keeps its last value. */
export interface Read<V> {
  value: V | null;
  /** No read has finished yet. */
  loading: boolean;
  failure: { why: string } | null;
  /** The page server's own words when it refused the read. */
  refused: string | null;
  /** The route answered that the module is gone. */
  gone: boolean;
}

/** A read of the page other than the frame's own three, for the line it gets at the
 * foot when it fails. `what` leads the line: `The chart`. A report is refused while
 * there is no index, which the page already says, so its refusal then has no line. */
export interface OtherRead {
  what: string;
  read: Read<unknown>;
  report: boolean;
}

/** What the frame is worked out from. */
export interface FrameFacts {
  limits: Read<Limits>;
  status: Read<Status>;
  /** The daily report over the 90 days to today under no filter: the Cost rows'. */
  daily: Read<Report<DayRow>>;
  /** The last `index` event, null before the first. */
  event: IndexEvent | null;
  /** What became of the last pass the page asked for, when it did not start. */
  pass: string | null;
  /** Today's dearest sessions, already worded (`most to planner $4.21`), or null. */
  most: string | null;
  /** The sum of the range in force under its filters; null while no range is read. */
  range: Sum | null;
  others: OtherRead[];
  /** The address names nothing the page has. */
  unknownAddress: boolean;
  /** The page server's time, epoch seconds. */
  now: number;
}

/** What shows in place of Cost and everything under it while nothing is counted. */
export interface Nothing {
  /** `empty`: an index with no request in it. `none`: no index this usage reads. */
  state: "empty" | "none";
  title: string;
  text: string;
  /** `Read new sessions`; null while a pass runs and when the page cannot run one. */
  act: string | null;
  /** `Reading new sessions…`, in the act's place while a pass runs. */
  running: string | null;
  /** The command that builds the index, to copy; null when the page can run a pass. */
  command: string | null;
}

/** The Cost group. */
export interface Cost {
  /** The group's name at its left: `Cost`. */
  label: string;
  rows: CostRow[];
  /** Under the first row: `most to planner $4.21, reviewer $3.67`. */
  most: string | null;
  caption: string;
  /** The link beside the caption. */
  prices: string;
  /** `sessions read 19:10`. */
  read: string | null;
}

/** The foot's own line and its act. */
export interface Foot {
  /** `Sessions read 19:10 · 81 files: Claude 72, Codex 8`; while a pass runs it leads
   * with `Reading new sessions…`. Null when neither a pass nor a file is known. */
  line: string | null;
  running: boolean;
  /** The last pass, epoch seconds. */
  lastPass: number | null;
  /** `Read new sessions`; null while a pass runs. */
  act: string | null;
}

/** One dim line under the foot: something the page's figures leave out, or a read
 * that failed. */
export interface FootLine {
  key: string;
  text: string;
}

export interface Frame {
  /** `Reading…`, until the first of the page's reads has answered. */
  reading: string | null;
  limits: LimitsModel;
  nothing: Nothing | null;
  cost: Cost | null;
  /** Whether the pieces under Cost (the filter line, the chart, the sections) have a
   * place: an index that holds requests, or a status that could not be read. */
  below: boolean;
  foot: Foot | null;
  lines: FootLine[];
  /** The newest day any answer carried, which every time is worded against. */
  today: string | null;
}

export const READING = "Reading…";
export const COST = "Cost";
export const READ_NEW = "Read new sessions";
export const READING_NEW = "Reading new sessions…";
export const NOTHING_COUNTED = "Nothing counted yet";
export const WHAT_USAGE_DOES = "Usage adds up what your sessions cost from the transcripts on this machine, "
  + "and shows your plan limits once a session has reported them.";
export const INDEX_COMMAND = "moxi usage index";
export const COPY = "Copy";
export const COPIED = "Copied";
export const NO_SUCH_VIEW = "There is no such view here.";
/** The reason a window carries when turns is not installed: said nowhere on the page. */
export const NO_TURNS = "turns is not installed";
/** How old the last pass may be when the page opens before the page asks for one, in s. */
export const STALE_AFTER = 600;

/** The newest of the days the page's answers carried; a value that is not a day is
 * left out. */
export function newestDay(...days: (string | null | undefined)[]): string | null {
  const held = days.filter(isDay).sort();
  return held.length ? held[held.length - 1] : null;
}

/** Whether the pieces under Cost have a place: an index that holds requests, or a
 * status that could not be read. False until the status has answered. */
export function hasBelow(status: Read<Status>): boolean {
  const state = status.value;
  if (state === null) return !status.loading;
  return !needsCommand(state) && state.total.requests > 0;
}

/** The path of the Cost rows' report: the daily report over the 90 days to `today`. */
export function costPath(today: string): string {
  return `api/daily?since=${addDays(today, 1 - COST_DAYS) ?? today}&until=${today}`;
}

/** Whether only `moxi usage index` can bring the index up: there is none, or another
 * version of usage wrote it, and the page server refuses a pass over it. */
function needsCommand(status: Status): boolean {
  return status.total.index_version === 0 || /run `moxi usage index`/.test(status.index.note ?? "");
}

/** Whether the page asks for a pass as it opens: an index it can pass over whose last
 * pass is over ten minutes old or never ran. */
export function shouldPass(status: Status | null, now: number): boolean {
  if (status === null || needsCommand(status)) return false;
  const last = status.total.last_pass;
  return last === null || now - last > STALE_AFTER;
}

/** The line a pass that did not start leaves at the foot, or null. */
export function passLine(outcome: PassOutcome): string | null {
  if (outcome.outcome === "refused") return outcome.text;
  if (outcome.outcome === "failed") return `The pass could not be started: ${outcome.why}.`;
  return null;
}

/** The last pass: the newer of the status's and the last event's. */
function lastPass(status: Status | null, event: IndexEvent | null): number | null {
  const times = [status?.total.last_pass ?? null, event?.last_pass ?? null].filter((at): at is number => at !== null);
  return times.length ? Math.max(...times) : null;
}

const ORDER = ["claude", "codex", "opencode"];
const place = (harness: string): number => (ORDER.indexOf(harness) + ORDER.length + 1) % (ORDER.length + 1);

/** How many files each harness's lanes hold, for those that hold any: Claude, Codex,
 * OpenCode, then any other by name. */
export function filesByHarness(lanes: Lane[]): { harness: string; files: number }[] {
  const counts = new Map<string, number>();
  for (const lane of lanes) {
    const harness = lane.harness ?? lane.lane;
    counts.set(harness, (counts.get(harness) ?? 0) + lane.files);
  }
  return [...counts].filter(([, files]) => files > 0).map(([harness, files]) => ({ harness, files }))
    .sort((a, b) => place(a.harness) - place(b.harness) || (a.harness < b.harness ? -1 : 1));
}

/** `81 files: Claude 72, Codex 8, OpenCode 1`, or null with no file. */
function filesWords(lanes: Lane[]): string | null {
  const found = filesByHarness(lanes);
  if (found.length === 0) return null;
  const all = found.reduce((total, item) => total + item.files, 0);
  return `${countWords(all, "file")}: ${found.map((item) => `${harnessName(item.harness)} ${item.files.toLocaleString("en-US")}`).join(", ")}`;
}

/** A lane as a line names it: its harness, with the lane's own id after it for a lane
 * that is not the harness's main one. */
function laneName(lane: Lane): string {
  const harness = lane.harness ?? lane.lane;
  return lane.lane === harness ? harnessName(harness) : `${harnessName(harness)} (${lane.lane})`;
}

/** Whether a lane's folder was never there: the pass could not list it, the index holds
 * nothing for it, and the reason it gave is that the folder (or the database) does not
 * exist. A pass also calls a lane `absent` when its folder is there and could not be
 * read while the index holds nothing for it; that one's reason says something else. */
function neverThere(lane: Lane): boolean {
  return lane.state === "absent" && (!lane.reason || /does not exist|is missing/.test(lane.reason));
}

/** `Codex not read: offline, <reason>` for each lane the last pass did not read. A lane
 * whose folder was never there leaves nothing out and has no line; a folder that is
 * there and could not be read has its line, whatever the index holds for it. */
function laneLines(status: Status): FootLine[] {
  return status.lanes.filter((lane) => lane.state !== "online" && !neverThere(lane)).map((lane) => ({
    key: `lane:${lane.lane}`,
    text: lane.state === null ? `${laneName(lane)} not read yet`
      : `${laneName(lane)} not read: ${lane.state}${lane.reason ? `, ${lane.reason}` : ""}`,
  }));
}

/** `3 files held back: <why>`, or with several reasons each with its count. */
function heldLine(status: Status): FootLine | null {
  const held = status.total.held;
  if (!held) return null;
  const counts = new Map<string, number>();
  for (const lane of status.lanes) {
    for (const { name, value } of lane.held_reasons ?? []) counts.set(name, (counts.get(name) ?? 0) + value);
  }
  if (counts.size === 0) {
    for (const row of status.total.held_rows) counts.set(row.reason, (counts.get(row.reason) ?? 0) + 1);
  }
  const reasons = [...counts].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1));
  const files = `${countWords(held, "file")} held back`;
  const text = reasons.length === 0 ? files
    : reasons.length === 1 && reasons[0][1] === held ? `${files}: ${reasons[0][0]}`
      : `${files}: ${reasons.map(([reason, count]) => `${reason} (${count})`).join("; ")}`;
  return { key: "held", text };
}

/** A window that could not be read, for a harness whose lanes hold files and for any
 * reason but turns not being installed: `Codex 5-hour limit not read: <reason>`. */
function windowLines(limits: Limits | null, status: Status | null): FootLine[] {
  if (limits === null || status === null) return [];
  const used = new Set(filesByHarness(status.lanes).map((item) => item.harness));
  const out: FootLine[] = [];
  for (const row of limits.rows) {
    if (row.state !== "unavailable" || !row.reason || row.reason === NO_TURNS || !used.has(row.harness)) continue;
    const text = `${harnessName(row.harness)} ${inSentence(limitLabel(row))} limit not read: ${row.reason}`;
    if (!out.some((line) => line.text === text)) out.push({ key: `window:${out.length}`, text });
  }
  return out;
}

/** The line of a read that failed, was refused, or whose route is gone; null otherwise. */
function readLine(what: string, read: Read<unknown>): string | null {
  const why = read.refused ?? read.failure?.why ?? (read.gone ? "usage's page server has no such read" : null);
  return why === null ? null : `${what} can't be read: ${why.replace(/\.$/, "")}.`;
}

/**
 * The frame at `facts.now`.
 *
 * - `Reading…` shows until one of the limits, the status and the Cost report has answered.
 * - The limits are drawn whenever their answer holds a row to draw, in every state.
 * - Until the status has answered nothing else is drawn. With no index this usage reads,
 *   or one with no request in it, the `Nothing counted yet` block stands in for Cost,
 *   the pieces under it and the foot's own line; the dim lines still show.
 * - A status that cannot be read hides nothing: Cost and the pieces under it are drawn
 *   from their own reads, and a line says the status could not be read.
 */
export function frameModel(facts: FrameFacts, clock: Clock = LOCAL): Frame {
  const { limits, status, daily, event, now } = facts;
  const state = status.value;
  const today = newestDay(limits.value?.today, state?.today, daily.value?.today);
  const running = event?.running ?? false;
  const last = lastPass(state, event);
  const none = state !== null && needsCommand(state);
  const empty = state !== null && !none && state.total.requests === 0;
  const up = hasBelow(status);

  let nothing: Nothing | null = null;
  if (none) {
    nothing = { state: "none", title: NOTHING_COUNTED, text: WHAT_USAGE_DOES, act: null, running: null,
                command: INDEX_COMMAND };
  } else if (empty) {
    const day = last === null ? null : dayPart(last, today, clock);
    const looked = last === null ? ""
      : ` It found no sessions when it last looked, ${day === null ? "" : `${day} `}at ${clock.time(last)}.`;
    nothing = { state: "empty", title: NOTHING_COUNTED, text: `${WHAT_USAGE_DOES}${looked}`,
                act: running ? null : READ_NEW, running: running ? READING_NEW : null, command: null };
  }

  const rows = up ? costRows(daily.value) : [];
  const cost: Cost | null = rows.length === 0 ? null
    : { label: COST, rows, most: facts.most, caption: CAPTION, prices: PRICES_LINK, read: sessionsRead(last, today, clock) };

  let foot: Foot | null = null;
  if (up && state !== null) {
    const files = filesWords(state.lanes);
    const lead = running ? READING_NEW : last === null ? null : `Sessions read ${whenWord(last, today, clock)}`;
    const parts = [lead, files].filter((part): part is string => part !== null);
    foot = { line: parts.length ? parts.join(" · ") : null, running, lastPass: last, act: running ? null : READ_NEW };
  }

  const lines: FootLine[] = [];
  if (state !== null && !none) {
    lines.push(...laneLines(state));
    const held = heldLine(state);
    if (held !== null) lines.push(held);
  }
  if (up && facts.range !== null) {
    const why = whyNotWhole(facts.range);
    if (why !== null) lines.push({ key: "range", text: `This range's total is not whole: ${why}` });
  }
  (limits.value?.notes ?? []).forEach((note, i) => lines.push({ key: `note:${i}`, text: note }));
  lines.push(...windowLines(limits.value, state));
  const reads: OtherRead[] = [
    { what: "The limits", read: limits, report: false },
    { what: "The index's status", read: status, report: false },
    { what: "Cost", read: daily, report: true },
    ...facts.others,
  ];
  reads.forEach(({ what, read, report }, i) => {
    const text = report && (none || state === null && status.loading) ? null : readLine(what, read);
    if (text !== null) lines.push({ key: `read:${i}`, text });
  });
  // What a pass that ended left to say, and the pass the page asked for that did not start.
  const ended = event !== null && !event.running && event.note ? event.note : state?.index.note ?? null;
  const note = none || ended === null || /^no pass ran first/.test(ended) ? null : ended;
  if (note !== null) lines.push({ key: "index", text: note });
  if (facts.pass !== null && facts.pass !== note && !(none && /moxi usage index/.test(facts.pass))) {
    lines.push({ key: "pass", text: facts.pass });
  }
  if (facts.unknownAddress) lines.push({ key: "address", text: NO_SUCH_VIEW });

  return {
    reading: limits.loading && status.loading && daily.loading ? READING : null,
    limits: limitsModel(limits.value, now, clock),
    nothing,
    cost,
    below: up,
    foot,
    lines,
    today,
  };
}
