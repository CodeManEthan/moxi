// What usage's page and blocks show, worked out with no DOM so the Node tests can read it:
// the formatters, the routes and their query strings, the range presets, each view's
// state, the daily chart's scales, the session treemap's layout, the cost terms (the class
// split, the Reconcile lines and the rates), a session's request and context charts, the
// limits meters, the cost block and the index strip. Every string here goes into the page
// as text, and every chart path is a plain SVG path string built here.

import { max, min, sum } from "d3-array";
import { hierarchy, treemap, treemapSquarify } from "d3-hierarchy";
import { scaleBand, scaleLinear } from "d3-scale";

import type { Address } from "./address-model.ts";

import type {
  AgentRow, DayRow, Failure, Group, IndexEvent, Limits, Mark, Override, Prices, Provenance,
  Rate, Report, RequestRow, RequestsTotal, SplitItem, Status, Sums, Term, TermsRow, Window,
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

export type ViewName = "daily" | "sessions" | "projects" | "models" | "terms" | "session";

/** The page's route, the part of the URL after `/m/usage/`. */
export interface PageRoute {
  view: ViewName | "unknown";
  /** With `session`, its id. */
  session?: string;
  /** With `session`, the agent whose requests it charts: `main` unless the route names one. */
  agent?: string;
}

export const VIEWS: readonly [ViewName, string][] = [
  ["daily", "Daily"], ["sessions", "Sessions"], ["projects", "Projects"], ["models", "Models"],
  ["terms", "Cost terms"],
];

export function parseRoute(route: string): PageRoute {
  const path = route.replace(/^\/+|\/+$/g, "").split(/[?#]/)[0];
  if (path === "" || path === "daily") return { view: "daily" };
  if (path === "sessions" || path === "projects" || path === "models" || path === "terms") return { view: path };
  if (path.startsWith("session/") && path.length > "session/".length) {
    const parts = path.slice("session/".length).split("/");
    if (parts.length > 2 || parts.some((part) => part === "")) return { view: "unknown" };
    try {
      const [session, agent] = parts.map((part) => decodeURIComponent(part));
      return { view: "session", session, agent: agent ?? "main" };
    } catch {
      return { view: "unknown" };
    }
  }
  return { view: "unknown" };
}

/** The route of one session's view, at one of its agents (`main` when none is named). */
export function sessionRoute(id: string, agent = "main"): string {
  const base = `session/${encodeURIComponent(id)}`;
  return agent === "main" ? base : `${base}/${encodeURIComponent(agent)}`;
}

/** The view kept from before the page's sections that serves `address` until its
 * section is built: one session's view. Null for an address no
 * such view serves. */
export function legacyRoute(address: Address): string | null {
  switch (address.piece) {
    case "sessions":
      return address.session === null ? null : sessionRoute(address.session, address.agent ?? "main");
    default:
      return null;
  }
}

/** The filters a sessions row's link opens its session with: the row's own harness, so
 * that two harnesses' sessions of one id stay apart and the view sums to the row. */
export function sessionFilters(filters: Filters, row: { harness: string }): Filters {
  return { ...filters, harness: row.harness };
}

/** The page's filters, as the user set them. */
export interface Filters {
  preset: Preset;
  harness: string;
  model: string;
  project: string;
  /** A folder's whole path, in place of a project. */
  folder?: string;
  /** The days the page's range gives, in place of the preset's; empty for all time. */
  days?: { since?: string; until?: string };
}

export const HARNESSES = ["claude", "codex", "opencode"] as const;
export const NO_FILTERS: Filters = { preset: "30d", harness: "", model: "", project: "" };

/** How many sessions the sessions view asks for. */
export const TOP = 200;

/** The query keys the filters give, in a fixed order; `leave` names a key to leave out. */
export function filterKeys(filters: Filters, today: string, leave: string[] = []): [string, string][] {
  const range = filters.days ?? rangeFor(filters.preset, today);
  const keys: [string, string][] = [];
  if (range.since) keys.push(["since", range.since]);
  if (range.until) keys.push(["until", range.until]);
  if (filters.harness) keys.push(["harness", filters.harness]);
  if (filters.project) keys.push(["project", filters.project]);
  if (filters.folder) keys.push(["folder", filters.folder]);
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
    case "terms": return withQuery("api/terms", [["by", "model"], ...keys]);
    default: return null;
  }
}

/** The session view's request rows for its agent. */
export function requestsPath(route: PageRoute, filters: Filters, today: string): string {
  return withQuery("api/requests", [["session", route.session ?? ""], ["agent", route.agent ?? "main"],
                                    ...filterKeys(filters, today)]);
}

/** The session view's cost terms, by model. */
export function sessionTermsPath(route: PageRoute, filters: Filters, today: string): string {
  return withQuery("api/terms", [["by", "model"], ["session", route.session ?? ""], ...filterKeys(filters, today)]);
}

/** The daily view's cost by class: the terms by day or month. */
export function classPath(filters: Filters, today: string, by: "day" | "month"): string {
  return withQuery("api/terms", [["by", by], ...filterKeys(filters, today)]);
}

/** The rates of some models, each looked up under the provider its requests gave. */
export function pricesPath(asks: { model: string; provider: string | null }[]): string {
  return withQuery("api/prices", [["models", asks.map((ask) => ask.model).join(",")],
                                  ["providers", asks.map((ask) => ask.provider ?? "").join(",")]]);
}

/** A search of the whole model table. */
export function searchPath(q: string): string {
  return withQuery("api/prices", [["q", q]]);
}

/** The paths that fill the project and model menus: every project and model under the
 * other filters. */
export function menuPaths(filters: Filters, today: string): { projects: string; models: string } {
  return {
    projects: withQuery("api/projects", filterKeys(filters, today, ["project"])),
    models: withQuery("api/models", filterKeys(filters, today, ["model"])),
  };
}

/** The project menu's entries: the project keys the rows carry, with `current` first when
 * no row has it. A row with no key has only its `cwd`, a path, which is shown and never
 * sent back in a request, so it isn't offered. */
export function projectMenu(rows: { project: string | null }[], current: string): string[] {
  const keys = [...new Set(rows.map((row) => row.project).filter((key): key is string => !!key))];
  return current && !keys.includes(current) ? [current, ...keys] : keys;
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
export const SERIES_TOKENS = ["series-1", "series-2", "series-3", "series-4", "series-5", "series-6"] as const;

/** A series' colour class: `s` and the series' index, wrapped at the token count. */
export function seriesClass(i: number): string {
  return `s${i % SERIES_TOKENS.length}`;
}

export type Measure = "tokens" | "cost";
export const UNKNOWN_MODEL = "unknown model";

/** One segment of a bar: `index` is its colour's place, `hatch` marks a price tier's part. */
export interface Segment { key: string; index: number; value: number; y: number; height: number; hatch?: boolean }
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
  const stacks = periods.map((period) => {
    const mine = dated.filter((row) => row.period === period);
    const values = keys.map((key) => measure === "tokens"
      ? sum(mine, (row) => Number(row[key as keyof DayRow]) || 0)
      : sum(mine.filter((row) => (row.model ?? UNKNOWN_MODEL) === key), (row) => row.cost ?? 0));
    return { period, values, incomplete: mine.some((row) => !row.complete) };
  });
  return stackBars(stacks, keys, (i) => i, () => false, width, height, rows.length - dated.length);
}

/** The daily view's "Cost by class": each period's cost split by token class and price
 * tier, a tier's segment in its class's colour with a hatch. */
export function classChart(rows: TermsRow[], width: number, height: number): Chart {
  const dated = rows.filter((row) => row.period !== null);
  const periods = [...new Set(dated.map((row) => row.period as string))].sort();
  const keys = splitKeys(dated.flatMap((row) => row.split));
  const stacks = periods.map((period) => {
    const mine = dated.filter((row) => row.period === period);
    const values = keys.map((key) => sum(mine.flatMap((row) => row.split).filter((item) => splitKey(item) === key),
                                         (item) => item.cost));
    return { period, values, incomplete: mine.some((row) => !row.complete) };
  });
  return stackBars(stacks, keys, (i) => classIndex(keys[i]), (i) => keys[i].includes(":"), width, height,
                   rows.length - dated.length);
}

function stackBars(stacks: { period: string; values: number[]; incomplete: boolean }[], keys: string[],
                   colour: (i: number) => number, hatch: (i: number) => boolean,
                   width: number, height: number, undated: number): Chart {
  const periods = stacks.map((stack) => stack.period);
  const x = scaleBand<string>().domain(periods).range([MARGIN.left, width - MARGIN.right])
    .paddingInner(periods.length > 40 ? 0.1 : 0.2).paddingOuter(0.1);
  const top = max(stacks, (stack) => sum(stack.values)) ?? 0;
  const y = scaleLinear().domain([0, top > 0 ? top : 1]).nice(4).range([height - MARGIN.bottom, MARGIN.top]);
  const bars = stacks.map(({ period, values, incomplete }) => {
    let base = 0;
    const segments: Segment[] = [];
    values.forEach((value, i) => {
      if (value > 0) {
        segments.push({ key: keys[i], index: colour(i), value, y: y(base + value), height: y(base) - y(base + value),
                        hatch: hatch(i) });
      }
      base += value;
    });
    const width = Math.min(x.bandwidth(), BAR_MAX);
    return { period, x: (x(period) ?? 0) + (x.bandwidth() - width) / 2, width, total: base, incomplete, segments };
  });
  const ticks = y.ticks(4).map((value) => ({ value, y: y(value) }));
  return { width, height, keys, bars, ticks, undated };
}

/** What one segment of a bar holds, as its own `<title>` says it. */
export function segmentTitle(period: string, key: string, value: number, measure: Measure | "class"): string {
  if (measure === "class") return `${period}, ${splitLabel(key)}: ${fmtCost(value)}`;
  if (measure === "cost") return `${period}, ${key}: ${fmtCost(value)}`;
  const label = CLASSES.find(([name]) => name === key)?.[1] ?? key;
  return `${period}, ${label}: ${fmtTokens(value)} tokens`;
}

/** A total by token class: its name in the report, its label, and its sum, or null when
 * the index can't say it. */
export interface ClassTotal { key: string; label: string; value: number | null }

/** The daily view's sums by token class: the whole prompt, its fresh part where the
 * report gives it, cache writes, cache reads and output. */
export function classTotals(total: Sums): ClassTotal[] {
  const known = (key: keyof Sums): number | null =>
    total.unknown.includes(key) || !Number.isFinite(total[key]) ? null : total[key] as number;
  const out: ClassTotal[] = [{ key: "input", label: "Input", value: known("input") }];
  const fresh = known("fresh_input");
  if (fresh !== null) out.push({ key: "fresh_input", label: "Fresh input", value: fresh });
  out.push({ key: "cache_write", label: "Cache write", value: known("cache_write") },
           { key: "cache_read", label: "Cache read", value: known("cache_read") },
           { key: "output", label: "Output", value: known("output") });
  return out;
}

/** An axis label for `value` of `measure`. */
export function tickText(value: number, measure: Measure | "class"): string {
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

// --- the cost terms ---------------------------------------------------------------------------

/** The five token classes a cost's terms price, in order, with their labels. A request
 * row names the first `fresh_input`. */
export const TERM_CLASSES = [
  ["input", "fresh input"], ["cache_read", "cache read"], ["cache_write_5m", "cache write 5m"],
  ["cache_write_1h", "cache write 1h"], ["output", "output"],
] as const;

/** A split entry's key: its class, then `:tier` for a price tier's part. */
export function splitKey(item: { class: string; tier: string | null }): string {
  return item.tier ? `${item.class}:${item.tier}` : item.class;
}

/** A class's place in `TERM_CLASSES`, which gives its colour. */
export function classIndex(key: string): number {
  const found = TERM_CLASSES.findIndex(([name]) => name === key.split(":")[0]);
  return found < 0 ? TERM_CLASSES.length : found;
}

/** A split key as the legend names it: `output`, `output, fast`. */
export function splitLabel(key: string): string {
  const [cls, tier] = key.split(/:(.*)/s);
  const label = TERM_CLASSES.find(([name]) => name === cls)?.[1] ?? cls;
  return tier ? `${label}, ${tier}` : label;
}

/** The split keys in class order, the row's own prices before each tier. */
export function splitKeys(items: SplitItem[]): string[] {
  const keys = [...new Set(items.map(splitKey))];
  return keys.sort((a, b) => classIndex(a) - classIndex(b) || (a.includes(":") ? 1 : 0) - (b.includes(":") ? 1 : 0)
    || (a < b ? -1 : a > b ? 1 : 0));
}

/** Each class's dollars over a split, its tiers added in: the totals strip's figures. */
export function classCosts(split: SplitItem[]): { key: string; label: string; value: number }[] {
  return TERM_CLASSES.map(([key, label]) => ({
    key, label, value: sum(split.filter((item) => item.class === key), (item) => item.cost),
  })).filter((item) => item.value > 0);
}

/** One horizontal bar of the class split: a row's cost, its segments its `split`. */
export interface SplitBar {
  label: string;
  total: number;
  segments: { key: string; index: number; hatch: boolean; x: number; width: number; title: string }[];
}

/** One bar per row, each as long as its cost against the dearest row's, over `width`. */
export function splitBars(rows: TermsRow[], width: number): SplitBar[] {
  const totals = rows.map((row) => sum(row.split, (item) => item.cost));
  const top = max(totals) ?? 0;
  const scale = top > 0 ? width / top : 0;
  return rows.map((row, i) => {
    let at = 0;
    const label = row.model ?? row.period ?? row.agent ?? UNKNOWN_MODEL;
    const segments = row.split.filter((item) => item.cost > 0).map((item) => {
      const key = splitKey(item);
      const share = totals[i] > 0 ? (item.cost / totals[i]) * 100 : 0;
      const segment = { key, index: classIndex(key), hatch: item.tier !== null, x: at, width: item.cost * scale,
                        title: `${label}, ${splitLabel(key)}: ${fmtTokens(item.tokens)} tokens, ${fmtCost(item.cost)}, ${fmtPercent(share)}` };
      at += segment.width;
      return segment;
    });
    return { label, total: totals[i], segments };
  });
}

/** Dollars per million tokens, as short as the price allows: `$3`, `$0.3`, `$3.75`. */
export function fmtRate(price: number | null | undefined): string {
  if (price === null || price === undefined || !Number.isFinite(price)) return DASH;
  return `$${Number(price.toPrecision(6))}`;
}

/** One cell of a Reconcile line: the term's dollars, and `tokens x price` in its title. */
export interface TermCell { cls: string; text: string; title: string }

/** One line of the Reconcile table. A `group` line is one price group; a `subtotal` sums a
 * model's groups when it has two or more; the `total` sums every line. */
export interface ReconcileLine {
  kind: "group" | "subtotal" | "total";
  model: string;
  pricedBy: string;
  cells: TermCell[];
  cost: number | null;
  flags: string[];
}

/** A term's cell: `1,234,567 x $8/M = $9.88`. */
export function termCell(term: Term): TermCell {
  const tokens = term.tokens.toLocaleString("en-US");
  const label = TERM_CLASSES.find(([name]) => name === term.class)?.[1] ?? term.class;
  const priced = term.price === null ? "no price" : `${fmtRate(term.price)}/M`;
  const dollars = term.cost === null ? "not priced" : fmtCost(term.cost);
  const from = term.price_from === "tier-input" ? " (from the tier's input price)" : "";
  return { cls: term.class, text: term.cost === null ? DASH : fmtCost(term.cost),
           title: `${label}: ${tokens} x ${priced} = ${dollars}${from}` };
}

function summedCells(groups: Group[], what: string): TermCell[] {
  return TERM_CLASSES.map(([cls, label]) => {
    const terms = groups.map((group) => group.terms.find((term) => term.class === cls))
      .filter((term): term is Term => term !== undefined);
    const known = terms.filter((term) => term.cost !== null);
    const cost = known.length ? sum(known, (term) => term.cost as number) : null;
    const tokens = sum(terms, (term) => term.tokens);
    return { cls, text: cost === null ? DASH : fmtCost(cost),
             title: `${label}: ${tokens.toLocaleString("en-US")} tokens over ${what} = ${cost === null ? "not priced" : fmtCost(cost)}` };
  });
}

/** How a group was priced: where its prices came from, its tier and its provider. */
export function pricedBy(group: Group): string {
  const parts = [provenanceLine(group.provenance)];
  if (group.tier) parts.push(`tier ${group.tier}`);
  if (group.above) parts.push(`above ${fmtTokens(group.above)}`);
  if (group.provider) parts.push(group.provider);
  return parts.join(" · ");
}

function groupFlags(group: Group): string[] {
  const out: string[] = [];
  if (group.cost === null) out.push(group.why ? `not priced: ${group.why}` : "not priced");
  if (group.unknown_rows > 0) out.push(`${group.unknown_rows} request${group.unknown_rows === 1 ? "" : "s"} with a token count the harness didn't report`);
  if (group.rule) out.push(group.rule);
  return out;
}

/** The Reconcile table's lines: each model's groups, a subtotal under a model of two or
 * more, then the total. The terms on each line add up to its cost. */
export function reconcileLines(report: Report<TermsRow>): ReconcileLine[] {
  const lines: ReconcileLine[] = [];
  for (const row of report.rows) {
    const model = row.model ?? UNKNOWN_MODEL;
    for (const group of row.groups) {
      lines.push({ kind: "group", model, pricedBy: pricedBy(group), cells: group.terms.map(termCell),
                   cost: group.cost, flags: groupFlags(group) });
    }
    if (row.groups.length > 1) {
      lines.push({ kind: "subtotal", model: `${model} total`, pricedBy: `${row.groups.length} price groups`,
                   cells: summedCells(row.groups, `${row.groups.length} groups`), cost: row.cost, flags: flags(row) });
    }
  }
  const all = report.rows.flatMap((row) => row.groups);
  lines.push({ kind: "total", model: "Total", pricedBy: "", cells: summedCells(all, "every group"),
               cost: report.total.cost, flags: flags({ ...report.total, unpriced_models: report.unpriced_models }) });
  return lines;
}

// --- the rates ---------------------------------------------------------------------------------

/** The most models the Rates section asks for at once (the route's own cap). */
export const RATES_MAX = 100;

/** The models and providers the Reconcile lines priced, for the Rates section: each
 * looked up under its requests' own provider, so the rates are the ones those groups'
 * lookup gives. */
export function rateAsks(rows: TermsRow[]): { asks: { model: string; provider: string | null }[]; more: number } {
  const seen = new Map<string, { model: string; provider: string | null }>();
  for (const group of rows.flatMap((row) => row.groups)) {
    if (group.model === null) continue;
    const key = `${group.model}\u0000${group.provider ?? ""}`;
    if (!seen.has(key)) seen.set(key, { model: group.model, provider: group.provider });
  }
  const asks = [...seen.values()];
  return { asks: asks.slice(0, RATES_MAX), more: Math.max(0, asks.length - RATES_MAX) };
}

/** A price cell of the Rates section, marked where a provider default or your config gave it. */
export interface RateCell { text: string; mark: string | null }

export const RATE_FIELDS = [
  ["input", "Input"], ["output", "Output"], ["cache_read", "Cache read"], ["cache_write_5m", "Write 5m"],
  ["cache_write_1h", "Write 1h"],
] as const;

export function rateCell(rate: Rate, field: (typeof RATE_FIELDS)[number][0]): RateCell {
  const value = rate[field];
  const mark = value === null ? null : rate.provenance.filled.includes(field) ? "provider default"
    : rate.provenance.overridden.includes(field) ? "your config" : null;
  return { text: fmtRate(value), mark };
}

/** A tier's conditions in words: `speed fast`, `service tier priority`, `above 200k`. */
export function conditionsText(c: { above_tokens: number | null; service_tier: string | null; speed: string | null }): string {
  const parts: string[] = [];
  if (c.speed) parts.push(`speed ${c.speed}`);
  if (c.service_tier) parts.push(`service tier ${c.service_tier}`);
  if (c.above_tokens) parts.push(`above ${fmtTokens(c.above_tokens)}`);
  return parts.join(", ");
}

/** A tier as the Tiers cell gives it: `fast (speed fast): $30 / $150, cache from tier input`. */
export function tierText(tier: Rate["tiers"][number]): string {
  const base = `${tier.name} (${conditionsText(tier.conditions)}): ${fmtRate(tier.prices.input)} / ${fmtRate(tier.prices.output)}`;
  return tier.from_tier_input.length ? `${base}, cache from tier input` : base;
}

/** One line of the Rates section. */
export interface RateLine {
  model: string;
  provider: string | null;
  known: boolean;
  cells: RateCell[];
  window: string;
  tiers: string[];
  period: string;
  from: string;
}

export function rateLine(rate: Rate): RateLine {
  const p = rate.provenance;
  const from = [p.layer ? `${p.layer} layer` : null, p.rule, p.snapshot ? `snapshot ${p.snapshot}` : null,
                p.row && p.row !== rate.model ? `row ${p.row}` : null].filter((part): part is string => !!part);
  return {
    model: rate.model ?? UNKNOWN_MODEL,
    provider: rate.asked_provider,
    known: p.step !== 7,
    cells: RATE_FIELDS.map(([field]) => rateCell(rate, field)),
    window: rate.window === null ? DASH : rate.window.toLocaleString("en-US"),
    tiers: rate.tiers.map(tierText),
    period: p.period && (p.period.from || p.period.to) ? `${p.period.from ?? "the start"} to ${p.period.to ?? "now"}` : "",
    from: from.join(" · "),
  };
}

/** A `[[model]]` row of your config, in a line. */
export function overrideLine(row: Override): string {
  const prices = RATE_FIELDS.filter(([field]) => row.prices[field] !== null)
    .map(([field, label]) => `${label.toLowerCase()} ${fmtRate(row.prices[field])}`);
  const parts = [`${row.id}${row.match === "exact" ? "" : ` (${row.match})`}`, ...prices];
  if (row.window !== null) parts.push(`window ${row.window.toLocaleString("en-US")}`);
  if (row.provider) parts.push(`provider ${row.provider}`);
  for (const tier of row.tiers) parts.push(`tier ${tier.name} (${conditionsText(tier.conditions)})`);
  if (row.from || row.to) parts.push(`from ${row.from ?? "the start"} to ${row.to ?? "now"}`);
  if (row.note) parts.push(row.note);
  return parts.join(" · ");
}

/** The model table's line: `model table in effect: 3,328 models, snapshot of 2026-10-30`. */
export function layerLine(prices: Prices): string {
  return `model table in effect: ${prices.rows.toLocaleString("en-US")} models, snapshot of ${prices.effective_date ?? "an unknown date"}`;
}

// --- one agent's requests ---------------------------------------------------------------------

/** A request row's five classes, in the order of `TERM_CLASSES`. */
export const REQUEST_CLASSES = [
  ["fresh_input", "fresh input"], ["cache_read", "cache read"], ["cache_write_5m", "cache write 5m"],
  ["cache_write_1h", "cache write 1h"], ["output", "output"],
] as const;

/** Up to this many requests the chart draws bars; past it, a stacked area. */
export const BARS_UP_TO = 120;

/** The request axis: request `n` (from 1) sits in the `n`th of `count` equal columns. */
export interface Axis {
  left: number;
  right: number;
  step: number;
  /** The middle of request `n`'s column. */
  x(n: number): number;
  /** The edge after `k` requests: the gap between request `k` and `k + 1`. */
  edge(k: number): number;
}

export function requestAxis(count: number, width: number): Axis {
  const left = MARGIN.left;
  const right = width - MARGIN.right;
  const step = (right - left) / Math.max(1, count);
  return { left, right, step, x: (n) => left + (n - 0.5) * step, edge: (k) => left + k * step };
}

/** A line through `points`, broken where a point's `y` is null. */
export function linePath(points: { x: number; y: number | null }[]): string {
  let d = "";
  let drawing = false;
  for (const { x, y } of points) {
    if (y === null) {
      drawing = false;
      continue;
    }
    d += `${drawing ? "L" : "M"}${round(x)},${round(y)}`;
    drawing = true;
  }
  return d;
}

/** The band between `lower` and `upper` over `xs`, closed. */
export function areaPath(xs: number[], lower: number[], upper: number[]): string {
  if (xs.length === 0) return "";
  const top = xs.map((x, i) => `${i === 0 ? "M" : "L"}${round(x)},${round(upper[i])}`).join("");
  const bottom = xs.map((_, i) => xs.length - 1 - i).map((i) => `L${round(xs[i])},${round(lower[i])}`).join("");
  return `${top}${bottom}Z`;
}

/** A level over each column `[x0, x1]`, stepping where it changes and broken where it is null. */
export function stepPath(columns: { x0: number; x1: number; y: number | null }[]): string {
  let d = "";
  let last: number | null = null;
  for (const { x0, x1, y } of columns) {
    if (y === null) {
      last = null;
      continue;
    }
    d += last === null ? `M${round(x0)},${round(y)}` : y !== last ? `V${round(y)}` : "";
    d += `H${round(x1)}`;
    last = y;
  }
  return d;
}

function round(n: number): number {
  return Math.round(n * 100) / 100;
}

export type RequestMeasure = "tokens" | "cost";

/** A request's value of one class: its tokens, or the dollars of that class's term. */
function classValue(row: RequestRow, i: number, measure: RequestMeasure): number {
  if (measure === "tokens") return row[REQUEST_CLASSES[i][0]];
  return row.split.find((item) => item.class === TERM_CLASSES[i][0])?.cost ?? 0;
}

/** The requests chart: bars up to `BARS_UP_TO` requests, a stacked area past it. */
export interface RequestsChart {
  mode: "bars" | "area";
  axis: Axis;
  bars: { n: number; x: number; width: number; segments: Segment[] }[];
  areas: { key: string; index: number; d: string }[];
  ticks: { value: number; y: number }[];
  /** The y of each request's stack top, for the readout's marker. */
  tops: number[];
}

export function requestsChart(rows: RequestRow[], measure: RequestMeasure, width: number, height: number): RequestsChart {
  const axis = requestAxis(rows.length, width);
  const stacks = rows.map((row) => REQUEST_CLASSES.map((_, i) => Math.max(0, classValue(row, i, measure))));
  const top = max(stacks, (values) => sum(values)) ?? 0;
  const y = scaleLinear().domain([0, top > 0 ? top : 1]).nice(4).range([height - MARGIN.bottom, MARGIN.top]);
  const ticks = y.ticks(4).map((value) => ({ value, y: y(value) }));
  const tops = stacks.map((values) => y(sum(values)));
  if (rows.length <= BARS_UP_TO) {
    const barWidth = Math.max(1, axis.step * 0.8);
    const bars = rows.map((row, r) => {
      let base = 0;
      const segments: Segment[] = [];
      stacks[r].forEach((value, i) => {
        if (value > 0) segments.push({ key: REQUEST_CLASSES[i][0], index: i, value, y: y(base + value), height: y(base) - y(base + value) });
        base += value;
      });
      return { n: row.n, x: axis.x(row.n) - barWidth / 2, width: barWidth, segments };
    });
    return { mode: "bars", axis, bars, areas: [], ticks, tops };
  }
  const xs = rows.map((row) => axis.x(row.n));
  const base = rows.map(() => 0);
  const areas = REQUEST_CLASSES.map(([key], i) => {
    const lower = base.map((value) => y(value));
    stacks.forEach((values, r) => { base[r] += values[i]; });
    return { key, index: i, d: areaPath(xs, lower, base.map((value) => y(value))) };
  });
  return { mode: "area", axis, bars: [], areas, ticks, tops };
}

/** A chart label's height (the 11px of the page's `svg text`), and a width per character
 * at least as wide as the sans face's average, so a label placed by them stays inside. */
export const LABEL_HEIGHT = 11;
export const LABEL_CHAR = 6.5;

/** Where a label beside the vertical line at `x` goes: 3px right of it, or, where that
 * runs past `right`, 3px left of it, anchored at its end. */
export function labelBeside(x: number, text: string, right: number): { x: number; anchor: "start" | "end" } {
  return x + 3 + text.length * LABEL_CHAR <= right ? { x: x + 3, anchor: "start" } : { x: x - 3, anchor: "end" };
}

/** The context chart: the whole prompt per request against the window. */
export interface ContextChart {
  axis: Axis;
  /** The context line, broken where it is unknown or the request synthetic. */
  line: string;
  /** How many requests the line has a point for. */
  points: number;
  /** The window's step line, and a label where each level starts: its baseline above the
   * line, or below it where the line is too near the top for the label to fit above. */
  window: string;
  windowLabels: { x: number; y: number; anchor: "start" | "end"; text: string }[];
  /** "window unknown for <model>", or null. */
  unknownWindow: string | null;
  ticks: { value: number; y: number }[];
  /** The y of each request's context, null where there is no point. */
  ys: (number | null)[];
}

export function contextChart(rows: RequestRow[], width: number, height: number): ContextChart {
  const axis = requestAxis(rows.length, width);
  const shown = (row: RequestRow) => row.context !== null && !row.synthetic;
  const top = Math.max(max(rows, (row) => (shown(row) ? row.context as number : 0)) ?? 0,
                       max(rows, (row) => row.window ?? 0) ?? 0);
  const y = scaleLinear().domain([0, top > 0 ? top : 1]).nice(4).range([height - MARGIN.bottom, MARGIN.top]);
  const ys = rows.map((row) => (shown(row) ? y(row.context as number) : null));
  const line = linePath(rows.map((row, i) => ({ x: axis.x(row.n), y: ys[i] })));
  const window = stepPath(rows.map((row) => ({ x0: axis.edge(row.n - 1), x1: axis.edge(row.n),
                                               y: row.window === null ? null : y(row.window) })));
  const windowLabels: ContextChart["windowLabels"] = [];
  rows.forEach((row, i) => {
    if (row.window !== null && (i === 0 || rows[i - 1].window !== row.window)) {
      const text = `window ${row.window.toLocaleString("en-US")}`;
      const at = y(row.window);
      windowLabels.push({ ...labelBeside(axis.edge(row.n - 1), text, axis.right),
                          y: at - 4 - LABEL_HEIGHT >= 0 ? at - 4 : at + LABEL_HEIGHT + 2, text });
    }
  });
  const unknown = [...new Set(rows.filter((row) => row.window === null).map((row) => row.model ?? UNKNOWN_MODEL))];
  return {
    axis, line, points: ys.filter((value) => value !== null).length, window, windowLabels,
    unknownWindow: unknown.length ? `window unknown for ${unknown.join(", ")}` : null,
    ticks: y.ticks(4).map((value) => ({ value, y: y(value) })), ys,
  };
}

/** The rules both charts draw: a thin one where each turn starts (with two turns or more)
 * and a dashed one at each compaction, between request `after` and the next. */
export interface Rules {
  turns: { n: number; x: number }[];
  compactions: { after: number; x: number; label: string; labelX: number; anchor: "start" | "end";
                 title: string; dot: number | null }[];
}

export function requestRules(rows: RequestRow[], total: RequestsTotal, axis: Axis): Rules {
  const turns = (total.turns ?? 0) >= 2
    ? rows.filter((row) => row.turn_start).map((row) => ({ n: row.n, x: axis.edge(row.n - 1) }))
    : [];
  const compactions = total.marks.map((mark: Mark) => {
    const label = mark.trigger ? `compaction (${mark.trigger})` : "compaction";
    const before = mark.pre_tokens === null ? "" : `, ${mark.pre_tokens.toLocaleString("en-US")} before`;
    const x = axis.edge(mark.after);
    const beside = labelBeside(x, label, axis.right);
    return { after: mark.after, x, label, labelX: beside.x, anchor: beside.anchor,
             title: `${label}${before}${mark.ts === null ? "" : ` at ${fmtMoment(mark.ts)}`}`,
             dot: mark.after < rows.length ? mark.after + 1 : null };
  });
  return { turns, compactions };
}

/** The column a request's `unknown` names for each class it doesn't know. */
const UNKNOWN_COLUMN: Record<(typeof REQUEST_CLASSES)[number][0], string> = {
  fresh_input: "input", cache_read: "cache_read", cache_write_5m: "cache_write",
  cache_write_1h: "cache_write_1h", output: "output",
};

/** The readout line for one request. */
export function readout(row: RequestRow): string {
  const parts = [`#${row.n}`, fmtMoment(row.ts)];
  if (row.turn !== null) parts.push(`turn ${row.turn}`);
  parts.push(row.model ?? UNKNOWN_MODEL);
  if (row.synthetic) parts.push("synthetic");
  parts.push(`context ${fmtTokens(row.context)}`);
  if (row.context !== null && row.window) parts.push(`${fmtPercent((row.context / row.window) * 100)} of ${row.window.toLocaleString("en-US")}`);
  parts.push(REQUEST_CLASSES.map(([key, label]) => `${label} ${row.unknown.includes(UNKNOWN_COLUMN[key]) ? "?" : fmtTokens(row[key])}`).join(", "));
  parts.push(fmtCost(row.cost));
  return parts.join(" · ");
}

/** The notes under the charts: what the report can't show. */
export function requestNotes(rows: RequestRow[], total: RequestsTotal): string[] {
  const out: string[] = [];
  const n = total.claimed_elsewhere;
  if (n) out.push(`${n} request${n === 1 ? "" : "s"} of this agent ${n === 1 ? "is" : "are"} counted under another file`);
  const c = total.copied_marks;
  if (c) out.push(`${c} compaction${c === 1 ? "" : "s"} copied from another file ${c === 1 ? "isn't" : "aren't"} drawn`);
  if (!total.marks_known) out.push("compactions aren't read yet for a file of this agent; the next pass that can read it brings them in");
  if (rows.length && total.turns === null) out.push("this harness wrote no turns here");
  return out;
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

/** How old the cost block's figures are: the index's last pass at `now`, flagged once it
 * is over `STALE_AFTER` old, since only the usage page and the CLI read new sessions in. */
export function costAge(lastPass: number | null, now: number): { text: string; stale: boolean } {
  if (lastPass === null) {
    return { text: "The index has never been read, so these leave out every session; opening the usage page reads them in.", stale: true };
  }
  const age = `as of ${fmtAgo(lastPass, now)}`;
  if (now - lastPass <= STALE_AFTER) return { text: age, stale: false };
  return { text: `${age}, so these may leave out what came since; opening the usage page reads it in.`, stale: true };
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
  /** Whether the page can run a pass: an index this usage reads, at its version. */
  canPass: boolean;
}

/** Whether the index's note says only `moxi usage index` can bring it up: there is none,
 * or an older usage wrote it, and the page server refuses a pass over it. */
function needsCommand(note: string | null | undefined): boolean {
  return /run `moxi usage index`/.test(note ?? "");
}

/** The strip from `/api/status` and the last `index` event, at `now`. */
export function strip(status: FeedState<Status>, event: IndexEvent | null, now: number): Strip {
  const base = { lastPass: "", running: event?.running ?? false, lanes: [], held: 0, heldReasons: [], note: null,
                 command: null, canPass: false };
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
    command: needsCommand(index.note) ? INDEX_COMMAND : null,
    canPass: !needsCommand(index.note),
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

/** The line a refused pass adds to the strip, or null when the strip's note already says
 * it, as it does over an index only `moxi usage index` can bring up. */
export function refusedLine(refused: string | null, view: Strip): string | null {
  return refused !== null && refused !== view.note ? refused : null;
}

/** Whether the page starts a pass as it opens: there is an index at this usage's version,
 * and its last pass is over `STALE_AFTER` old or never ran. */
export function shouldPass(status: Status | null, now: number): boolean {
  if (status === null || status.total.index_version === 0 || needsCommand(status.index.note)) return false;
  const last = status.total.last_pass;
  return last === null || now - last > STALE_AFTER;
}
