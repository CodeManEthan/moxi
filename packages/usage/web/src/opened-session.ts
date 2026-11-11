// One session opened in the Sessions section, with no DOM: the paths it reads, its lines
// in words, its chart's shapes, and its table. It reads `/api/requests` (the main agent),
// `/api/terms?by=agent` and `/api/session`, each with the query its row was read with plus
// the session, so its figures add up to the row; a session the address named is read over
// no range and under no filter, so its row shows the session's whole figures. The restart
// point is read for the model of the session's newest request once the requests answer.
// A line whose fact no answer carries is not in the model; a read still out puts one
// `Reading…` in the place of the first line it fills.

import type { Table } from "./chart-model.ts";
import { costWords, countWords, sumRows } from "./cost-model.ts";
import { dayPart, LOCAL, whenWord } from "./days.ts";
import type { Clock } from "./days.ts";
import type {
  AgentRow, Context, Report, RequestRow, RequestsTotal, RestartPoint, SessionRef, SessionRow, TermsRow, TermsTotal,
} from "./feed.ts";
import { stateKeys } from "./filter-model.ts";
import type { PageState } from "./filter-model.ts";
import { percentWords } from "./limits-model.ts";
import type { ListDays } from "./sessions-model.ts";
import { KINDS, kindSum } from "./terms-model.ts";
import { fmtCost, readout, REQUEST_CLASSES, requestNotes, TERM_CLASSES, tierPrice, UNKNOWN_MODEL } from "./views.ts";

export const OPEN_IN_SESSIONS = "Open in Sessions";
export const COPY_ID = "Copy id";
export const PAST_IT = "this session is past it";
export const LABELS = {
  requests: "Requests", context: "Context", went: "Went to", subagents: "Subagents", session: "Session",
  restart: "Restart point",
} as const;

type Keys = [string, string][];

/** The query an opened session reads with: its list's days, the page's filters and the
 * row's own harness; for a session the address named, its harness alone. */
export function openedKeys(state: PageState, today: string, days: ListDays | null, harness: string): Keys {
  if (days === null) return [["harness", harness]];
  const own: Keys = [...(days.since === null ? [] : [["since", days.since] as [string, string]]), ["until", days.until]];
  return [...own, ...stateKeys(state, today, ["since", "until", "harness"]), ["harness", harness]];
}

export interface OpenedPaths {
  requests: string;
  terms: string;
  session: string;
}

/** The three reads of one session under `keys`. */
export function openedPaths(id: string, keys: Keys): OpenedPaths {
  const query = (head: Keys): string => new URLSearchParams([...head, ...keys]).toString();
  return {
    requests: `api/requests?${query([["session", id], ["agent", "main"]])}`,
    terms: `api/terms?${query([["by", "agent"], ["session", id]])}`,
    session: `api/session?${query([["session", id]])}`,
  };
}

/** The restart point's read for one model. */
export function restartPath(model: string): string {
  return `api/restart-point?${new URLSearchParams([["model", model]]).toString()}`;
}

/** The model of the session's newest request, as the index holds it; null with none. */
export function newestModel(rows: RequestRow[]): string | null {
  for (let i = rows.length - 1; i >= 0; i -= 1) {
    const model = rows[i].model;
    if (model !== null && model !== "") return model;
  }
  return null;
}

/** A token count as the opened session words it: `268k`, `1,000k`, `950`. */
export function kWords(n: number): string {
  return n >= 1000 ? `${Math.round(n / 1000).toLocaleString("en-US")}k` : String(Math.round(n));
}

/** Words of a line: each part in the text colour, or dim. */
export interface Part {
  text: string;
  dim: boolean;
}

export type Item =
  | { kind: "line"; key: keyof typeof LABELS; label: string; parts: Part[] }
  | { kind: "chart" }
  | { kind: "reading"; key: string };

/** What a read gives the model: its answer, or that it is still out. */
export interface Read<V> {
  value: V | null;
  loading: boolean;
}

export interface OpenedFacts {
  harness: string;
  id: string;
  requests: Read<Report<RequestRow, RequestsTotal>>;
  terms: Read<Report<TermsRow, TermsTotal>>;
  session: Read<Report<AgentRow>>;
  /** Null until the restart point is asked. */
  restart: Read<RestartPoint> | null;
  today: string | null;
}

export interface OpenedSession {
  items: Item[];
  /** Dim lines under the chart: what the requests' answer cannot show. */
  notes: string[];
  /** The session's place on the Sessions page, when its record was found. */
  sessions: { harness: string; id: string } | null;
  /** The requests, a row each, with the compactions between them; null with none. */
  table: Table | null;
  /** Whether `Check against a bill` has lines to show. */
  bill: boolean;
}

const dim = (text: string): Part => ({ text, dim: true });
const plain = (text: string): Part => ({ text, dim: false });

/** `34 requests from 12:45 to 19:09 · the dearest $0.28 at 18:34, the start of turn 9`. */
export function requestsLine(report: Report<RequestRow, RequestsTotal>, today: string | null,
                             clock: Clock = LOCAL): Part[] | null {
  const rows = report.rows;
  if (rows.length === 0) return null;
  const count = countWords(report.total.requests || rows.length, "request");
  const times = rows.map((row) => row.ts).filter((ts): ts is number => ts !== null);
  const first = times.length ? Math.min(...times) : null;
  const last = times.length ? Math.max(...times) : null;
  // A day is said only where the line holds another day, so the first moment's day names them all.
  const named = (first !== null && dayPart(first, today, clock) !== null)
    || (last !== null && dayPart(last, today, clock) !== null);
  const when = (at: number): string => whenWord(at, today, clock, named);
  let span = "";
  if (first !== null && last !== null) {
    span = first === last || clock.time(first) === clock.time(last) && clock.day(first) === clock.day(last)
      ? ` at ${when(first)}`
      : ` from ${when(first)} to ${clock.day(first) === clock.day(last) ? clock.time(last) : when(last)}`;
  }
  const parts = [plain(`${count}${span}`)];
  const priced = rows.filter((row) => row.cost !== null && row.cost > 0);
  if (rows.length > 1 && priced.length) {
    const dear = priced.reduce((best, row) => ((row.cost as number) > (best.cost as number) ? row : best));
    const at = dear.ts === null ? "" : ` at ${when(dear.ts)}`;
    const turn = dear.turn_start && dear.turn !== null && (report.total.turns ?? 0) >= 2 ? `, the start of turn ${dear.turn}` : "";
    parts.push(dim(` · the dearest ${fmtCost(dear.cost)}${at}${turn}`));
  }
  return parts;
}

/** `268k of 1,000k tokens on the last request, 27% full`, `268k tokens on the last request`. */
export function contextLine(context: Context | null): Part[] | null {
  if (context === null) return null;
  const window = context.window;
  if (window === null || !(window > 0)) return [plain(`${kWords(context.tokens)} tokens on the last request`)];
  return [plain(`${kWords(context.tokens)} of ${kWords(window)} tokens on the last request, `
    + `${percentWords((context.tokens / window) * 100)} full`)];
}

/** What the session's cost went to: each class with a cost, dearest first, then the
 * harness's own figure where it wrote one. */
export function wentLine(report: Report<TermsRow, TermsTotal>): Part[] | null {
  if (report.rows.length === 0) return null;
  const total = report.total;
  const found = KINDS.flatMap((kind) => {
    const tokens = total[kind.tokens];
    const sum = kindSum(report, kind.terms, Number.isFinite(tokens) ? tokens : 0);
    if (sum.cost === 0 && sum.unpriced_tokens === 0) return [];
    const notes = [...sum.tiers].map(([tier, cost]) => `${fmtCost(cost)} at ${tierPrice(tier)}`);
    return [{ cost: sum.cost, text: `${kind.name.toLowerCase()} ${costWords(sum)}`, notes }];
  });
  if (found.length === 0) return null;
  found.sort((one, other) => {
    if ((one.cost === null) !== (other.cost === null)) return one.cost === null ? 1 : -1;
    return (other.cost ?? 0) - (one.cost ?? 0);
  });
  const parts: Part[] = [];
  found.forEach((kind, i) => {
    if (i) parts.push(dim(" · "));
    parts.push(plain(kind.text));
    for (const note of kind.notes) parts.push(dim(` · ${note}`));
  });
  if (total.harness_cost !== null) parts.push(dim(` · harness ${fmtCost(total.harness_cost)}`));
  return parts;
}

/** `2 subagents · $0.74 of the $3.67`; null when the session has no agent but its main. */
export function subagentsLine(report: Report<AgentRow>): Part[] | null {
  const others = report.rows.filter((row) => row.agent !== "main");
  const count = new Set(others.map((row) => row.agent)).size;
  if (count === 0) return null;
  const theirs = sumRows(others);
  const whole = sumRows([{ ...report.total, unpriced_models: report.unpriced_models }]);
  const parts = [plain(countWords(count, "subagent"))];
  if (theirs.cost !== null && whole.cost !== null) parts.push(dim(` · ${costWords(theirs)} of the ${costWords(whole)}`));
  return parts;
}

/** `claude · opus · started today 12:45`: the harness, the newest request's model and the
 * session's start, each absent when not known. */
export function sessionLine(harness: string, model: string | null, started: number | null, today: string | null,
                            clock: Clock = LOCAL): Part[] {
  const parts = [plain(model === null ? harness : `${harness} · ${model}`)];
  if (started !== null) parts.push(dim(` · started ${whenWord(started, today, clock, true)}`));
  return parts;
}

/** `101k tokens for opus`, then ` · this session is past it` in the text colour when the
 * last request's context is at or over it; null when the measure gives no point. */
export function restartLine(point: RestartPoint, model: string, context: number | null): Part[] | null {
  if (point.tokens === null) return null;
  const parts = [dim(`${kWords(point.tokens)} tokens for ${model}`)];
  if (context !== null && context >= point.tokens) parts.push(plain(` · ${PAST_IT}`));
  return parts;
}

/** The context of the session's last request: the session answer's, else the newest
 * request's that has one. */
function lastContext(session: Report<AgentRow> | null, requests: RequestRow[]): number | null {
  const context = session?.context ?? null;
  if (context !== null) return context.tokens;
  for (let i = requests.length - 1; i >= 0; i -= 1) if (requests[i].context !== null) return requests[i].context;
  return null;
}

/** The opened session's lines in order, a `Reading…` in place of the first line a read
 * still out will fill, and its acts. */
export function openedSession(facts: OpenedFacts, clock: Clock = LOCAL): OpenedSession {
  const items: Item[] = [];
  const reading = (key: string): void => {
    if (items[items.length - 1]?.kind !== "reading") items.push({ kind: "reading", key });
  };
  const line = (key: keyof typeof LABELS, parts: Part[] | null): void => {
    if (parts !== null) items.push({ kind: "line", key, label: LABELS[key], parts });
  };
  const requests = facts.requests.value;
  const session = facts.session.value;
  const terms = facts.terms.value;
  const rows = requests?.rows ?? [];
  if (requests === null) {
    if (facts.requests.loading) reading("requests");
  } else {
    line("requests", requestsLine(requests, facts.today, clock));
  }
  if (session === null) {
    if (facts.session.loading) reading("session");
  } else {
    line("context", contextLine(session.context ?? null));
  }
  if (rows.length) items.push({ kind: "chart" });
  if (terms === null) {
    if (facts.terms.loading) reading("terms");
  } else {
    line("went", wentLine(terms));
  }
  const ref: SessionRef | null = session?.ref ?? null;
  const model = newestModel(rows);
  if (session !== null) {
    line("subagents", subagentsLine(session));
    line("session", sessionLine(ref?.harness ?? facts.harness, model, ref?.started ?? null, facts.today, clock));
  }
  const restart = facts.restart;
  if (restart !== null && model !== null) {
    if (restart.value === null) {
      if (restart.loading) reading("restart");
    } else {
      line("restart", restartLine(restart.value, model, lastContext(session, rows)));
    }
  }
  const notes = requests === null ? [] : requestNotes(rows, requests.total);
  const unknown = [...new Set(rows.filter((row) => row.window === null).map((row) => row.model ?? UNKNOWN_MODEL))];
  if (unknown.length) notes.push(`window unknown for ${unknown.join(", ")}`);
  return {
    items,
    notes,
    sessions: ref !== null && ref.known === true && ref.session_id !== null ? { harness: ref.harness, id: ref.session_id } : null,
    table: requests === null || rows.length === 0 ? null : requestsTable(requests, facts.today, clock),
    bill: terms !== null && terms.rows.length > 0,
  };
}

/** The requests as a table: a row each, with its tokens and cost by class; a compaction
 * is a row of its own after the request it follows. */
export function requestsTable(report: Report<RequestRow, RequestsTotal>, today: string | null,
                              clock: Clock = LOCAL): Table {
  const columns = [
    ...["#", "Time", "Turn", "Model"].map((label) => ({ label, num: false })),
    { label: "Context", num: true },
    ...REQUEST_CLASSES.map(([, label]) => ({ label: label[0].toUpperCase() + label.slice(1), num: true })),
    { label: "Cost", num: true },
  ];
  const when = (at: number | null): string => (at === null ? "" : whenWord(at, today, clock, true));
  const blank = REQUEST_CLASSES.map(() => "");
  const rows: Table["rows"] = [];
  const marks = [...report.total.marks].sort((a, b) => a.after - b.after);
  const markRows = (after: number): void => {
    for (const mark of marks.filter((one) => one.after === after)) {
      const what = mark.trigger ? `compaction (${mark.trigger})` : "compaction";
      rows.push({ key: `c:${after}`, cells: ["", when(mark.ts), "", what,
        mark.pre_tokens === null ? "" : `${mark.pre_tokens.toLocaleString("en-US")} before`, ...blank, ""] });
    }
  };
  markRows(0);
  for (const row of report.rows) {
    const cells = REQUEST_CLASSES.map(([key], i) => {
      const tokens = row.unknown.includes(UNKNOWN_COLUMN[key]) ? "?" : row[key].toLocaleString("en-US");
      const cost = row.split.find((item) => item.class === TERM_CLASSES[i][0])?.cost ?? null;
      return cost === null || row[key] === 0 ? tokens : `${tokens} · ${fmtCost(cost)}`;
    });
    rows.push({ key: `r:${row.n}`, cells: [String(row.n), when(row.ts), row.turn === null ? "" : String(row.turn),
      `${row.model ?? UNKNOWN_MODEL}${row.synthetic ? " (synthetic)" : ""}`,
      row.context === null ? "" : row.context.toLocaleString("en-US"), ...cells,
      row.cost === null ? "no price" : fmtCost(row.cost)] });
    markRows(row.n);
  }
  return { columns, rows };
}

/** The column a request's `unknown` names for each class it doesn't know. */
const UNKNOWN_COLUMN: Record<(typeof REQUEST_CLASSES)[number][0], string> = {
  fresh_input: "input", cache_read: "cache_read", cache_write_5m: "cache_write",
  cache_write_1h: "cache_write_1h", output: "output",
};

/** The row an addressed session stands as, first in the section, from `/api/session`
 * over its whole life; null until it answers and when it holds no such session. */
export function placedRow(report: Report<AgentRow> | null, requests: RequestRow[] | null): SessionRow | null {
  const ref = report?.ref ?? null;
  if (report === null || ref === null || report.rows.length === 0) return null;
  const firsts = report.rows.map((row) => row.first_ts).filter((ts): ts is number => ts !== null);
  const lasts = report.rows.map((row) => row.last_ts).filter((ts): ts is number => ts !== null);
  const models = [...new Set((requests ?? []).map((row) => row.model)
    .concat(report.rows.filter((row) => row.agent === "main").map((row) => row.model))
    .filter((model): model is string => model !== null && model !== ""))];
  return {
    ...report.total, ...ref, unpriced_models: report.unpriced_models, rules: report.rules,
    cwd: ref.folder, project: null, models,
    first_ts: firsts.length ? Math.min(...firsts) : null, last_ts: lasts.length ? Math.max(...lasts) : null,
    context: report.context ?? null,
  };
}

// --- the chart --------------------------------------------------------------------------------

/** The chart's sizes, in px, as drawn: the request bars above, the context below, on one
 * time axis, with the words for each band's top at the left and its name at the right. */
export const SESSION_CHART = { height: 158, left: 46, right: 58, barTop: 8, barBase: 50, contextTop: 78,
                               contextBase: 136, labels: 152, barWidth: 3 };

export interface SessionChart {
  width: number;
  /** Where the plot ends; the band names sit after it. */
  end: number;
  aria: string;
  /** The tallest bar's value, at the top of the bars. */
  top: string;
  bars: { n: number; x: number; y: number; height: number; width: number; dearest: boolean }[];
  /** A column per request that gives its readout on hover. */
  hits: { n: number; x: number; width: number; title: string }[];
  /** The window each request ran against, stepping where it changes; the last level and
   * its size at the left. Null when no request knows its window. */
  window: { d: string; y: number; words: string } | null;
  line: string;
  /** The last request's context, its size at the right. */
  dot: { x: number; y: number; words: string } | null;
  /** The start of each turn, with two turns or more. */
  turns: number[];
  compactions: { x: number; title: string; label: string; anchor: "start" | "end"; labelX: number }[];
  labels: { x: number; text: string; anchor: "start" | "middle" | "end" }[];
}

/**
 * The opened session's chart: a bar per request, its cost or its tokens as the page's
 * measure says, the dearest in the text colour; under it the context each request sent
 * against the window. Requests sit at their times, or one per column when a time is
 * missing. Null with no request.
 */
export function sessionChart(report: Report<RequestRow, RequestsTotal>, measure: "cost" | "tokens", width: number,
                             today: string | null, clock: Clock = LOCAL): SessionChart | null {
  const rows = report.rows;
  if (rows.length === 0) return null;
  const S = SESSION_CHART;
  const left = S.left;
  const end = Math.max(left + 40, width - S.right);
  const span = end - left;
  const times = rows.map((row) => row.ts);
  const t0 = Math.min(...times.map((ts) => ts ?? Infinity));
  const t1 = Math.max(...times.map((ts) => ts ?? -Infinity));
  const timed = times.every((ts) => ts !== null) && t1 > t0;
  const xs = rows.map((row, i) => (timed ? left + (((row.ts as number) - t0) / (t1 - t0)) * span
    : left + (rows.length === 1 ? span / 2 : (i / (rows.length - 1)) * span)));
  const value = (row: RequestRow): number => (measure === "cost" ? Math.max(0, row.cost ?? 0)
    : REQUEST_CLASSES.reduce((sum, [key]) => sum + Math.max(0, row[key]), 0));
  const values = rows.map(value);
  const top = Math.max(...values);
  const barWidth = Math.min(S.barWidth, Math.max(1, (span / rows.length) * 0.8));
  const dearest = top > 0 ? values.indexOf(top) : -1;
  const bars = rows.map((row, i) => {
    const height = top > 0 ? (values[i] / top) * (S.barBase - S.barTop) : 0;
    return { n: row.n, x: xs[i] - barWidth / 2, y: S.barBase - height, height, width: barWidth, dearest: i === dearest };
  });
  const gap = rows.length > 1 ? Math.max(4, Math.min(24, span / rows.length)) : 24;
  const hits = rows.map((row, i) => ({ n: row.n, x: xs[i] - gap / 2, width: gap, title: readout(row) }));
  // The context band reaches the window, or the largest context when that is over it.
  const shown = rows.map((row) => (row.context !== null && !row.synthetic ? row.context : null));
  const levels = rows.map((row) => (row.window !== null && row.window > 0 ? row.window : null));
  const windows = levels.filter((w): w is number => w !== null);
  const windowSize = windows.length ? windows[windows.length - 1] : null;
  const most = Math.max(...windows, ...shown.map((c) => c ?? 0));
  const cy = (tokens: number): number => S.contextBase - (most > 0 ? (tokens / most) : 0) * (S.contextBase - S.contextTop);
  let windowLine = "";
  let level: number | null = null;
  levels.forEach((w, i) => {
    const x0 = i === 0 ? left : (xs[i - 1] + xs[i]) / 2;
    const x1 = i === rows.length - 1 ? end : (xs[i] + xs[i + 1]) / 2;
    if (w === null) {
      level = null;
      return;
    }
    const y = round(cy(w) + 0.5);
    windowLine += level === null ? `M${round(x0)},${y}` : w !== level ? `V${y}` : "";
    windowLine += `H${round(x1)}`;
    level = w;
  });
  let line = "";
  let drawing = false;
  shown.forEach((c, i) => {
    if (c === null) {
      drawing = false;
      return;
    }
    line += `${drawing ? "L" : "M"}${round(xs[i])},${round(cy(c))}`;
    drawing = true;
  });
  let lastShown = -1;
  shown.forEach((c, i) => { if (c !== null) lastShown = i; });
  const dot = lastShown < 0 ? null
    : { x: xs[lastShown], y: cy(shown[lastShown] as number), words: kWords(shown[lastShown] as number) };
  const turns = (report.total.turns ?? 0) >= 2
    ? rows.flatMap((row, i) => (row.turn_start && i > 0 ? [round((xs[i - 1] + xs[i]) / 2)] : [])) : [];
  const at = (n: number): number => {
    const i = rows.findIndex((row) => row.n === n);
    if (i < 0) return n <= 0 ? left : end;
    return i + 1 < rows.length ? (xs[i] + xs[i + 1]) / 2 : Math.min(end, xs[i] + 3);
  };
  const compactions = report.total.marks.map((mark) => {
    const label = mark.trigger ? `compaction (${mark.trigger})` : "compaction";
    const x = round(mark.after <= 0 ? left : at(mark.after));
    const before = mark.pre_tokens === null ? "" : `, ${mark.pre_tokens.toLocaleString("en-US")} tokens before`;
    const fits = x + 3 + label.length * 6.5 <= end;
    return { x, label, title: `${label}${before}${mark.ts === null ? "" : ` at ${whenWord(mark.ts, today, clock)}`}`,
             anchor: fits ? "start" as const : "end" as const, labelX: fits ? x + 3 : x - 3 };
  });
  return {
    width, end, aria: measure === "cost" ? "Cost of each request, and the context each one sent, over the session"
      : "Tokens of each request, and the context each one sent, over the session",
    top: measure === "cost" ? fmtCost(top) : kWords(top),
    bars, hits,
    window: windowSize === null ? null : { d: windowLine, y: round(cy(windowSize)), words: kWords(windowSize) },
    line, dot, turns, compactions,
    labels: timed ? timeLabels(t0, t1, left, end, today, clock) : [],
  };
}

/** The time axis' words: the first and last request's times at its ends, and whole hours
 * between where they fit. */
function timeLabels(t0: number, t1: number, left: number, end: number, today: string | null,
                    clock: Clock): SessionChart["labels"] {
  const x = (at: number): number => left + ((at - t0) / (t1 - t0)) * (end - left);
  const named = dayPart(t0, today, clock) !== null || dayPart(t1, today, clock) !== null;
  const out: SessionChart["labels"] = [
    { x: round(left), text: whenWord(t0, today, clock, named), anchor: "start" },
    { x: round(end), text: clock.day(t0) === clock.day(t1) ? clock.time(t1) : whenWord(t1, today, clock, named),
      anchor: "end" },
  ];
  if (t1 - t0 > 2 * 86_400) return out;
  const hours: number[] = [];
  for (let at = Math.ceil(t0 / 900) * 900; at < t1; at += 900) if (/:00(?!\d)/.test(clock.time(at))) hours.push(at);
  const room = 56;
  for (const step of [1, 2, 3, 4, 6, 12, 24]) {
    const picked = hours.filter((_, i) => i % step === 0)
      .filter((at) => x(at) - left >= room && end - x(at) >= room);
    const apart = picked.every((at, i) => i === 0 || x(at) - x(picked[i - 1]) >= room);
    if (picked.length <= 3 && apart) {
      out.splice(1, 0, ...picked.map((at) => ({ x: round(x(at)), text: clock.time(at), anchor: "middle" as const })));
      break;
    }
  }
  return out;
}

function round(n: number): number {
  return Math.round(n * 10) / 10;
}
