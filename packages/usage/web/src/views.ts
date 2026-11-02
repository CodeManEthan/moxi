// What usage's page and blocks show, worked out with no DOM so the Node tests can read it:
// the formatters, the routes and their query strings, the range presets, each view's
// state, the daily chart's scales, the session treemap's layout, the limits meters, the
// cost block and the index strip. Every string here goes into the page as text.

import { max, min, sum } from "d3-array";
import { hierarchy, treemap, treemapSquarify } from "d3-hierarchy";
import { scaleBand, scaleLinear } from "d3-scale";

import type {
  AgentRow, DayRow, Failure, IndexEvent, Limits, Provenance, Report, Status, Sums, Window,
} from "./feed.ts";

// --- formatters ---------------------------------------------------------------------------

const DASH = "—";

/** A token count: `950`, `12.3k`, `4.56M`, `1.20B`. */
export function fmtTokens(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return DASH;
  const size = Math.abs(n);
  if (size < 1000) return String(Math.round(n));
  const [div, unit] = size < 1e6 ? [1e3, "k"] : size < 1e9 ? [1e6, "M"] : [1e9, "B"];
  const value = n / div;
  const digits = Math.abs(value) < 10 ? 2 : Math.abs(value) < 100 ? 1 : 0;
  return `${value.toFixed(digits)}${unit}`;
}

/** Dollars: `$12.34`, `<$0.01`, `$1,234`; a dash when nothing could be priced. */
export function fmtCost(cost: number | null | undefined): string {
  if (cost === null || cost === undefined || !Number.isFinite(cost)) return DASH;
  if (cost === 0) return "$0.00";
  if (cost > 0 && cost < 0.01) return "<$0.01";
  if (cost >= 1000) return `$${Math.round(cost).toLocaleString("en-US")}`;
  return `$${cost.toFixed(2)}`;
}

/** A percent: `42%`, `4.5%` below 10. */
export function fmtPercent(p: number | null | undefined): string {
  if (p === null || p === undefined || !Number.isFinite(p)) return DASH;
  return p < 10 && p !== 0 ? `${p.toFixed(1)}%` : `${Math.round(p)}%`;
}

/** A length of time: `45s`, `12m`, `3h 05m`, `2d 4h`. */
export function fmtSpan(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${String(m % 60).padStart(2, "0")}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

/** How long ago `at` was, at `now` (both epoch seconds). */
export function fmtAgo(at: number | null | undefined, now: number): string {
  if (at === null || at === undefined) return "never";
  if (now - at < 5) return "just now";
  return `${fmtSpan(now - at)} ago`;
}

/** An ISO 8601 time as epoch seconds, or null. */
export function isoSeconds(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms / 1000 : null;
}

/** A moment in this browser's zone, `YYYY-MM-DD HH:MM`. */
export function fmtMoment(at: number | null | undefined): string {
  if (at === null || at === undefined) return DASH;
  const d = new Date(at * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// --- days and ranges ----------------------------------------------------------------------

/** `day` (`YYYY-MM-DD`) moved by `n` days. */
export function shiftDay(day: string, n: number): string {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** The day of `date` in this browser's zone, `YYYY-MM-DD`. */
export function localDay(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export type Preset = "7d" | "30d" | "90d" | "all";
export const PRESETS: readonly [Preset, string][] = [
  ["7d", "7 days"], ["30d", "30 days"], ["90d", "90 days"], ["all", "All time"],
];

/** A preset's days, `since` and `until` both included; empty for all time. */
export function rangeFor(preset: Preset, today: string): { since?: string; until?: string } {
  if (preset === "all") return {};
  const days = preset === "7d" ? 7 : preset === "90d" ? 90 : 30;
  return { since: shiftDay(today, -(days - 1)), until: today };
}

// --- routes and their queries -------------------------------------------------------------

export type ViewName = "daily" | "sessions" | "projects" | "models" | "session";

/** The page's route, the part of the URL after `/m/usage/`. */
export interface PageRoute {
  view: ViewName | "unknown";
  /** With `session`, its id. */
  session?: string;
}

export const VIEWS: readonly [ViewName, string][] = [
  ["daily", "Daily"], ["sessions", "Sessions"], ["projects", "Projects"], ["models", "Models"],
];

export function parseRoute(route: string): PageRoute {
  const path = route.replace(/^\/+|\/+$/g, "").split(/[?#]/)[0];
  if (path === "" || path === "daily") return { view: "daily" };
  if (path === "sessions" || path === "projects" || path === "models") return { view: path };
  if (path.startsWith("session/") && path.length > "session/".length) {
    try {
      return { view: "session", session: decodeURIComponent(path.slice("session/".length)) };
    } catch {
      return { view: "unknown" };
    }
  }
  return { view: "unknown" };
}

/** The route of one session's view. */
export function sessionRoute(id: string): string {
  return `session/${encodeURIComponent(id)}`;
}

/** The page's filters, as the user set them. */
export interface Filters {
  preset: Preset;
  harness: string;
  model: string;
  project: string;
}

export const HARNESSES = ["claude", "codex", "opencode"] as const;
export const NO_FILTERS: Filters = { preset: "30d", harness: "", model: "", project: "" };

/** How many sessions the sessions view asks for. */
export const TOP = 200;

/** The query keys the filters give, in a fixed order; `leave` names a key to leave out. */
export function filterKeys(filters: Filters, today: string, leave: string[] = []): [string, string][] {
  const range = rangeFor(filters.preset, today);
  const keys: [string, string][] = [];
  if (range.since) keys.push(["since", range.since]);
  if (range.until) keys.push(["until", range.until]);
  if (filters.harness) keys.push(["harness", filters.harness]);
  if (filters.project) keys.push(["project", filters.project]);
  if (filters.model) keys.push(["model", filters.model]);
  return keys.filter(([key]) => !leave.includes(key));
}

function withQuery(path: string, keys: [string, string][]): string {
  const query = new URLSearchParams(keys).toString();
  return query ? `${path}?${query}` : path;
}

/** The path a view reads, relative to `/m/usage/`, sending only the keys its route reads. */
export function viewPath(route: PageRoute, filters: Filters, today: string,
                         by: "day" | "month" = "day"): string | null {
  const keys = filterKeys(filters, today);
  switch (route.view) {
    case "daily": return withQuery("api/daily", [["by", by], ...keys]);
    case "sessions": return withQuery("api/sessions", [["top", String(TOP)], ...keys]);
    case "session": return withQuery("api/session", [["session", route.session ?? ""], ...keys]);
    case "projects": return withQuery("api/projects", keys);
    case "models": return withQuery("api/models", keys);
    default: return null;
  }
}

/** The paths that fill the project and model menus: every project and model under the
 * other filters. */
export function menuPaths(filters: Filters, today: string): { projects: string; models: string } {
  return {
    projects: withQuery("api/projects", filterKeys(filters, today, ["project"])),
    models: withQuery("api/models", filterKeys(filters, today, ["model"])),
  };
}

// --- completeness -------------------------------------------------------------------------

/** Why a row's figures are not whole, one line each; empty for a complete row. */
export function flags(row: Sums & { unpriced_models?: string[] }): string[] {
  const out: string[] = [];
  if (row.unknown_rows > 0) {
    out.push(`${row.unknown_rows} request${row.unknown_rows === 1 ? "" : "s"} with a token count the harness didn't report, so the sums are at least what they show`);
  }
  if (row.cost === null) {
    out.push("nothing here could be priced");
  } else if (row.unpriced_tokens > 0) {
    const models = row.unpriced_models?.length ? ` (${row.unpriced_models.join(", ")})` : "";
    out.push(`${fmtTokens(row.unpriced_tokens)} tokens have no price${models}, so the cost is the priced part`);
  }
  if (out.length === 0 && !row.complete) out.push("some of these figures are unknown");
  return out;
}

/** The cost as shown: usage's figure, and the harness's own where it wrote one. */
export function costText(row: Sums): string {
  const ours = fmtCost(row.cost);
  return row.harness_cost === null ? ours : `${ours} (harness ${fmtCost(row.harness_cost)})`;
}

// --- states -------------------------------------------------------------------------------

/** A feed as the views read it. */
export interface FeedState<V> {
  loading: boolean;
  gone: boolean;
  failure: Failure | null;
  refused: string | null;
  value: V | null;
}

export type ViewState = "loading" | "failed" | "refused" | "gone" | "empty" | "up";

/** A report view's state and the line it shows when there are no rows to draw. */
export function reportState<R>(feed: FeedState<Report<R>>): { state: ViewState; detail: string | null } {
  if (feed.gone) return { state: "gone", detail: null };
  if (feed.refused !== null) return { state: "refused", detail: feed.refused };
  if (feed.value === null) {
    if (feed.failure !== null) return { state: "failed", detail: feed.failure.why };
    return { state: "loading", detail: null };
  }
  if (feed.value.rows.length === 0) return { state: "empty", detail: feed.value.index.note };
  if (feed.failure !== null) return { state: "failed", detail: feed.failure.why };
  return { state: "up", detail: null };
}

// --- the daily chart ----------------------------------------------------------------------

/** The token classes a bar stacks, bottom first: they add up to `tokens`. */
export const CLASSES = [
  ["fresh_input", "fresh input"], ["cache_read", "cache read"], ["cache_write", "cache write"],
  ["output", "output"],
] as const;

/** The theme colours series take, in turn: series `i` takes `SERIES_TOKENS[seriesClass(i)]`. */
export const SERIES_TOKENS = ["accent", "text-dim", "warn", "ok", "error", "live"] as const;

/** A series' colour class, `s0` to `s5`. */
export function seriesClass(i: number): string {
  return `s${i % SERIES_TOKENS.length}`;
}

export type Measure = "tokens" | "cost";
export const UNKNOWN_MODEL = "unknown model";

export interface Segment { key: string; index: number; value: number; y: number; height: number }
export interface Bar {
  period: string;
  x: number;
  width: number;
  total: number;
  /** Any row in the bar is not whole. */
  incomplete: boolean;
  segments: Segment[];
}
export interface Chart {
  width: number;
  height: number;
  /** The series, in stacking order. */
  keys: string[];
  bars: Bar[];
  ticks: { value: number; y: number }[];
  /** Rows with no time, which no bar shows. */
  undated: number;
}

/** The widest a bar is drawn, in px. */
export const BAR_MAX = 56;

/** The chart's inner margins: the left one holds the axis labels. */
export const MARGIN = { top: 8, right: 4, bottom: 20, left: 44 };

/** The daily view's bars: tokens split by class, or cost split by model. */
export function dailyChart(rows: DayRow[], measure: Measure, width: number, height: number): Chart {
  const dated = rows.filter((row) => row.period !== null);
  const periods = [...new Set(dated.map((row) => row.period as string))].sort();
  const keys = measure === "tokens" ? CLASSES.map(([key]) => key as string)
    : [...new Set(dated.map((row) => row.model ?? UNKNOWN_MODEL))].sort();
  const x = scaleBand<string>().domain(periods).range([MARGIN.left, width - MARGIN.right])
    .paddingInner(periods.length > 40 ? 0.1 : 0.2).paddingOuter(0.1);
  const stacks = periods.map((period) => {
    const mine = dated.filter((row) => row.period === period);
    const values = keys.map((key) => measure === "tokens"
      ? sum(mine, (row) => Number(row[key as keyof DayRow]) || 0)
      : sum(mine.filter((row) => (row.model ?? UNKNOWN_MODEL) === key), (row) => row.cost ?? 0));
    return { period, values, incomplete: mine.some((row) => !row.complete) };
  });
  const top = max(stacks, (stack) => sum(stack.values)) ?? 0;
  const y = scaleLinear().domain([0, top > 0 ? top : 1]).nice(4).range([height - MARGIN.bottom, MARGIN.top]);
  const bars = stacks.map(({ period, values, incomplete }) => {
    let base = 0;
    const segments: Segment[] = [];
    values.forEach((value, index) => {
      if (value > 0) {
        segments.push({ key: keys[index], index, value, y: y(base + value), height: y(base) - y(base + value) });
      }
      base += value;
    });
    const width = Math.min(x.bandwidth(), BAR_MAX);
    return { period, x: (x(period) ?? 0) + (x.bandwidth() - width) / 2, width, total: base, incomplete, segments };
  });
  const ticks = y.ticks(4).map((value) => ({ value, y: y(value) }));
  return { width, height, keys, bars, ticks, undated: rows.length - dated.length };
}

/** An axis label for `value` of `measure`. */
export function tickText(value: number, measure: Measure): string {
  if (measure === "tokens") return fmtTokens(value);
  return value >= 1000 ? `$${fmtTokens(value)}` : `$${value % 1 === 0 ? value : value.toFixed(2)}`;
}

/** Which bars carry a period label, so labels don't collide: about one per 48 px. */
export function labelEvery(bars: number, width: number): number {
  const room = Math.max(1, Math.floor((width - MARGIN.left - MARGIN.right) / 48));
  return Math.max(1, Math.ceil(bars / room));
}

// --- one session: its agents ---------------------------------------------------------------

/** One agent of a session, its models' rows summed. */
export interface Agent {
  agent: string;
  kind: string;
  title: string | null;
  models: string[];
  cost: number | null;
  tokens: number;
  requests: number;
  harness_cost: number | null;
  first_ts: number | null;
  /** Why its figures aren't whole; empty when they are. */
  flags: string[];
  rows: AgentRow[];
}

/** The session's agents in the report's order (`main` first), each with its rows. */
export function agents(rows: AgentRow[]): Agent[] {
  const byAgent = new Map<string, AgentRow[]>();
  for (const row of rows) byAgent.set(row.agent, [...(byAgent.get(row.agent) ?? []), row]);
  return [...byAgent].map(([agent, mine]) => {
    const priced = mine.filter((row) => row.cost !== null);
    const harness = mine.filter((row) => row.harness_cost !== null);
    return {
      agent,
      kind: mine[0].kind,
      title: mine.find((row) => row.title)?.title ?? null,
      models: [...new Set(mine.map((row) => row.model ?? UNKNOWN_MODEL))],
      cost: priced.length ? sum(priced, (row) => row.cost as number) : null,
      tokens: sum(mine, (row) => row.tokens),
      requests: sum(mine, (row) => row.requests),
      harness_cost: harness.length ? sum(harness, (row) => row.harness_cost as number) : null,
      first_ts: min(mine, (row) => row.first_ts ?? undefined) ?? null,
      flags: [...new Set(mine.flatMap((row) => flags(row)))],
      rows: mine,
    };
  });
}

/** One rectangle of the treemap: an agent's use of one model. */
export interface Cell {
  agent: string;
  model: string;
  /** The agent's place in `agents()`, for its colour. */
  index: number;
  value: number;
  x: number;
  y: number;
  width: number;
  height: number;
  incomplete: boolean;
}

/** The session's agents as a squarified treemap, sized by cost, or by tokens when
 * nothing could be priced. */
export function agentTreemap(rows: AgentRow[], width: number, height: number): { measure: Measure; cells: Cell[] } {
  const list = agents(rows);
  const measure: Measure = rows.some((row) => (row.cost ?? 0) > 0) ? "cost" : "tokens";
  const weight = (row: AgentRow) => measure === "cost" ? row.cost ?? 0 : row.tokens;
  type Node = { name: string; index: number; row?: AgentRow; children?: Node[] };
  const data: Node = {
    name: "", index: -1,
    children: list.map((agent, index) => ({
      name: agent.agent, index,
      children: agent.rows.map((row) => ({ name: row.model ?? UNKNOWN_MODEL, index, row })),
    })),
  };
  const root = hierarchy<Node>(data).sum((node) => node.row ? Math.max(0, weight(node.row)) : 0)
    .sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
  if ((root.value ?? 0) <= 0) return { measure, cells: [] };
  treemap<Node>().tile(treemapSquarify).size([width, height]).paddingInner(2).round(true)(root);
  const cells = root.leaves().filter((leaf) => (leaf.value ?? 0) > 0).map((leaf) => {
    const box = leaf as typeof leaf & { x0: number; y0: number; x1: number; y1: number };
    return {
      agent: leaf.parent?.data.name ?? "",
      model: leaf.data.name,
      index: leaf.data.index,
      value: leaf.value ?? 0,
      x: box.x0, y: box.y0, width: box.x1 - box.x0, height: box.y1 - box.y0,
      incomplete: leaf.data.row ? !leaf.data.row.complete : false,
    };
  });
  return { measure, cells };
}

/** A cell's label, or null when it is too small for one. */
export function cellLabel(cell: Cell, measure: Measure): string[] | null {
  if (cell.width < 56 || cell.height < 30) return null;
  const value = measure === "cost" ? fmtCost(cell.value) : `${fmtTokens(cell.value)} tokens`;
  return cell.height < 44 ? [cell.agent] : [cell.agent, `${cell.model} ${value}`];
}

// --- models ---------------------------------------------------------------------------------

/** One price source, in a line. */
export function provenanceLine(p: Provenance): string {
  const parts = [p.rule];
  if (p.layer) parts.push(`layer ${p.layer}`);
  if (p.row) parts.push(`row ${p.row}`);
  if (p.variant) parts.push(`variant ${p.variant}`);
  if (p.snapshot) parts.push(`snapshot ${p.snapshot}`);
  if (p.period && (p.period.from || p.period.to)) parts.push(`from ${p.period.from ?? "the start"} to ${p.period.to ?? "now"}`);
  if (p.overridden.length) parts.push(`overrides ${p.overridden.join(", ")}`);
  if (p.filled.length) parts.push(`filled ${p.filled.join(", ")}`);
  if (p.note) parts.push(p.note);
  return parts.join(" · ");
}

// --- the limits meters ----------------------------------------------------------------------

export type Tone = "ok" | "warn" | "error" | "muted";

/** A window's meter as it is drawn. */
export interface Meter {
  key: string;
  title: string;
  state: string;
  /** The bar's fill, 0 to 1. */
  fill: number;
  percent: string;
  tone: Tone;
  /** When it resets: `resets in 2h 05m (2026-11-02 18:00)`, or null. */
  reset: string | null;
  /** How old the reading is, or null. */
  age: string | null;
  /** When it fills, or why that isn't known. */
  full: string | null;
  /** The state's reason, and any note. */
  reason: string | null;
}

/** Where a meter turns to warn and to error, in percent. */
export const WARN_AT = 75;
export const ERROR_AT = 90;

export function meter(row: Window, now: number): Meter {
  const used = row.used_percent;
  const fresh = row.state === "fresh" && used !== null;
  const tone: Tone = !fresh ? "muted" : used >= ERROR_AT ? "error" : used >= WARN_AT ? "warn" : "ok";
  const resets = isoSeconds(row.resets_at);
  const observed = isoSeconds(row.observed_at);
  const full = isoSeconds(row.full_at);
  const reason = [row.reason, row.note].filter((line): line is string => !!line).join("; ");
  return {
    key: `${row.harness}/${row.limit_id}/${row.window_minutes ?? ""}`,
    title: `${row.harness} ${row.label}`,
    state: row.state,
    fill: used === null ? 0 : Math.min(1, Math.max(0, used / 100)),
    percent: fmtPercent(used),
    tone,
    reset: resets === null ? null
      : resets > now ? `resets in ${fmtSpan(resets - now)} (${fmtMoment(resets)})` : `reset ${fmtAgo(resets, now)}`,
    age: observed === null ? null : `read ${fmtAgo(observed, now)}`,
    full: full !== null ? `full in ${fmtSpan(full - now)} (${fmtMoment(full)})`
      : fresh && row.projection ? `no fill time: ${row.projection}` : null,
    reason: reason || null,
  };
}

/** The meters, and the lines that apply to all of them. */
export function meters(limits: Limits | null, now: number): { meters: Meter[]; notes: string[] } {
  if (limits === null) return { meters: [], notes: [] };
  return { meters: limits.rows.map((row) => meter(row, now)), notes: limits.notes };
}

// --- the cost block -------------------------------------------------------------------------

export interface CostDay {
  day: string;
  cost: number | null;
  tokens: number;
  complete: boolean;
}

/** A plain sum, so the cost block's bundle carries no d3. */
const add = (values: number[]): number => values.reduce((a, b) => a + b, 0);

/** Today and the 6 days before it, oldest first, the rows of `/api/cost` summed by day. */
export function costDays(report: Report<DayRow>): { today: CostDay; week: CostDay; days: CostDay[] } | null {
  const today = report.today;
  if (!today) return null;
  const days = Array.from({ length: 7 }, (_, i) => shiftDay(today, i - 6)).map((day) => {
    const mine = report.rows.filter((row) => row.period === day);
    const priced = mine.filter((row) => row.cost !== null);
    return {
      day,
      cost: priced.length ? add(priced.map((row) => row.cost as number)) : mine.length ? null : 0,
      tokens: add(mine.map((row) => row.tokens)),
      complete: mine.every((row) => row.complete),
    };
  });
  const priced = days.filter((day) => day.cost !== null);
  const week = {
    day: `${days[0].day} to ${today}`,
    cost: priced.length ? add(priced.map((day) => day.cost as number)) : null,
    tokens: add(days.map((day) => day.tokens)),
    complete: days.every((day) => day.complete),
  };
  return { today: days[6], week, days };
}

// --- the index strip ------------------------------------------------------------------------

/** How old the last pass may be when the page opens before the page starts one, in s. */
export const STALE_AFTER = 600;
export const INDEX_COMMAND = "moxi usage index";

export interface Strip {
  /** `loading`, `failed`, `none` (no index yet), or `up`. */
  state: "loading" | "failed" | "none" | "up";
  lastPass: string;
  running: boolean;
  /** Lanes a pass didn't find online, with why. */
  lanes: { lane: string; state: string; reason: string | null }[];
  held: number;
  /** Why files were held back, each reason with how many, the most first. */
  heldReasons: { reason: string; count: number }[];
  note: string | null;
  /** The command to run when the page can't, or null. */
  command: string | null;
}

/** The strip from `/api/status` and the last `index` event, at `now`. */
export function strip(status: FeedState<Status>, event: IndexEvent | null, now: number): Strip {
  const base = { lastPass: "", running: event?.running ?? false, lanes: [], held: 0, heldReasons: [], note: null,
                 command: null };
  if (status.value === null) {
    if (status.failure !== null || status.refused !== null || status.gone) {
      return { ...base, state: "failed", note: status.refused ?? status.failure?.why ?? null };
    }
    return { ...base, state: "loading" };
  }
  const { total, lanes, index } = status.value;
  if (total.index_version === 0) {
    return { ...base, state: "none", note: index.note, command: INDEX_COMMAND };
  }
  const last = event !== null && event.last_pass !== null && (total.last_pass === null || event.last_pass > total.last_pass)
    ? event.last_pass : total.last_pass;
  const note = event !== null && !event.running && event.note ? event.note : index.note;
  return {
    ...base,
    state: "up",
    lastPass: `last read ${fmtAgo(last, now)}`,
    lanes: lanes.filter((lane) => lane.state !== "online")
      .map((lane) => ({ lane: lane.lane, state: lane.state ?? "not read yet", reason: lane.reason })),
    held: total.held,
    heldReasons: heldReasons(status.value),
    note: note && !/^no pass ran first/.test(note) ? note : null,
    command: /run `moxi usage index`/.test(index.note ?? "") ? INDEX_COMMAND : null,
  };
}

/** Why files were held back: each lane's reasons summed, or, when no lane gives any, the
 * reasons of the held files `/api/status` names; the most files first. */
export function heldReasons(status: Status): { reason: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const lane of status.lanes) {
    for (const { name, value } of lane.held_reasons ?? []) counts.set(name, (counts.get(name) ?? 0) + value);
  }
  if (counts.size === 0) {
    for (const row of status.total.held_rows) counts.set(row.reason, (counts.get(row.reason) ?? 0) + 1);
  }
  return [...counts].map(([reason, count]) => ({ reason, count }))
    .sort((a, b) => b.count - a.count || (a.reason < b.reason ? -1 : a.reason > b.reason ? 1 : 0));
}

/** The strip's line about held files, or null when none are held. */
export function heldLine(view: Strip): string | null {
  if (view.held === 0) return null;
  const files = `${view.held} file${view.held === 1 ? "" : "s"} held back`;
  if (view.heldReasons.length === 0) return files;
  if (view.heldReasons.length === 1 && view.heldReasons[0].count === view.held) return `${files}: ${view.heldReasons[0].reason}`;
  return `${files}: ${view.heldReasons.map(({ reason, count }) => `${reason} (${count})`).join("; ")}`;
}

/** Whether the page starts a pass as it opens: there is an index, and its last pass is
 * over `STALE_AFTER` old or never ran. */
export function shouldPass(status: Status | null, now: number): boolean {
  if (status === null || status.total.index_version === 0) return false;
  const last = status.total.last_pass;
  return last === null || now - last > STALE_AFTER;
}
