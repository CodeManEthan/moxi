// The opened session's subagents, with no DOM: the rows `Show` lists under the
// `Subagents` line, dearest first, ten and then thirty more at a time; the session's
// agents as a map; and one agent's requests, opened under its row as a session's are:
// its requests line, its context, its chart and its table. The agents are `/api/session`'s
// rows, an agent's models summed; an agent's requests are `/api/requests?agent=`, read
// with the query the session was read with, so they add up to its row.

import { hierarchy, treemap, treemapSquarify } from "d3-hierarchy";

import type { Table } from "./chart-model.ts";
import { costWords, countWords, sumRows } from "./cost-model.ts";
import type { Sum } from "./cost-model.ts";
import { LOCAL, whenWord } from "./days.ts";
import type { Clock } from "./days.ts";
import type { AgentRow, Report, RequestRow, RequestsTotal } from "./feed.ts";
import { contextLine, requestsLine, requestsTable } from "./opened-session.ts";
import type { Item, Part, Read } from "./opened-session.ts";
import { shareWords } from "./sessions-model.ts";
import { fmtTokens, requestNotes, UNKNOWN_MODEL } from "./views.ts";

/** How many rows `Show` lists first, and how many more each `Show` under them adds. */
export const AGENTS_FIRST = 10;
export const AGENTS_PAGE = 30;
/** The act that takes the rows away again. */
export const HIDE = "Hide";
/** The agent whose requests are the opened session's own. */
export const MAIN = "main";

/** One agent of the session, its models' rows summed. */
export interface AgentLine {
  agent: string;
  kind: string;
  /** Its models as the index holds them, joined by commas. */
  models: string;
  /** `first 12:45`; null with no time. */
  first: string | null;
  /** `2 requests, 11.2k tokens`. */
  figures: string;
  /** `$0.74`, `at least $0.74`, `no price`. */
  cost: string;
  /** `18% of the session`; null when it cannot be said. */
  share: string | null;
  sum: Sum;
}

/** Each agent's rows summed, in the answer's order. */
function byAgent(rows: AgentRow[]): { agent: string; kind: string; rows: AgentRow[]; sum: Sum }[] {
  const found = new Map<string, AgentRow[]>();
  for (const row of rows) found.set(row.agent, [...(found.get(row.agent) ?? []), row]);
  return [...found].map(([agent, mine]) => ({ agent, kind: mine[0].kind, rows: mine, sum: sumRows(mine) }));
}

/** Dearest first; an agent with no price after every priced one, then by tokens and id. */
function dearestFirst(one: { agent: string; sum: Sum }, other: { agent: string; sum: Sum }): number {
  if ((one.sum.cost === null) !== (other.sum.cost === null)) return one.sum.cost === null ? 1 : -1;
  return (other.sum.cost ?? 0) - (one.sum.cost ?? 0) || other.sum.tokens - one.sum.tokens
    || one.agent.localeCompare(other.agent);
}

/** The session's agents other than `main`, dearest first, each with its share of the
 * session's cost. */
export function subagentLines(report: Report<AgentRow>, today: string | null, clock: Clock = LOCAL): AgentLine[] {
  const whole = report.total.cost;
  return byAgent(report.rows).filter((one) => one.agent !== MAIN).sort(dearestFirst).map((one) => {
    const firsts = one.rows.map((row) => row.first_ts).filter((ts): ts is number => ts !== null);
    const models = [...new Set(one.rows.map((row) => row.model ?? UNKNOWN_MODEL))].join(", ");
    const share = shareWords(one.sum.cost, whole);
    return {
      agent: one.agent, kind: one.kind, models,
      first: firsts.length ? `first ${whenWord(Math.min(...firsts), today, clock)}` : null,
      figures: `${countWords(one.sum.requests, "request")}, ${fmtTokens(one.sum.tokens)} tokens`,
      cost: costWords(one.sum), share: share === null ? null : `${share} of the session`, sum: one.sum,
    };
  });
}

/** How many rows are shown: none until `Show`, then ten and thirty more a `Show`, and
 * always as many as reach the opened agent's row. */
export function shownCount(lines: AgentLine[], asked: number, opened: string | null): number {
  const at = opened === null ? -1 : lines.findIndex((line) => line.agent === opened);
  const reach = at < 0 ? 0 : at + 1;
  if (asked <= 0 && reach === 0) return 0;
  return Math.min(lines.length, Math.max(asked, AGENTS_FIRST, reach));
}

/** `14 more` under the rows, with its `Show`; null when every row is shown. */
export function moreLine(lines: AgentLine[], shown: number): string | null {
  const more = lines.length - shown;
  return more > 0 ? `${more.toLocaleString("en-US")} more` : null;
}

/** One agent's requests, under the query the session was read with. */
export function agentPath(id: string, agent: string, keys: [string, string][]): string {
  return `api/requests?${new URLSearchParams([["session", id], ["agent", agent], ...keys]).toString()}`;
}

// --- the map ----------------------------------------------------------------------------------

/** The map's height, in px. */
export const MAP_HEIGHT = 96;
/** The width of one character of a cell's words, and the room around them, in px. */
const CHAR = 6.5;
const ROOM = 8;

export interface MapCell {
  agent: string;
  x: number;
  y: number;
  width: number;
  height: number;
  /** The words drawn in the cell, as many lines as fit; empty when none does. */
  words: string[];
  title: string;
}

export interface AgentMap {
  measure: "cost" | "tokens";
  aria: string;
  cells: MapCell[];
}

/** The session's agents, `main` among them, as a squarified map sized by cost, or by
 * tokens when nothing could be priced; null when nothing has either. */
export function agentMap(report: Report<AgentRow>, width: number, height: number = MAP_HEIGHT): AgentMap | null {
  const agents = byAgent(report.rows);
  const measure = agents.some((one) => (one.sum.cost ?? 0) > 0) ? "cost" : "tokens";
  const value = (sum: Sum): number => Math.max(0, measure === "cost" ? sum.cost ?? 0 : sum.tokens);
  type Node = { agent: string; sum: Sum | null; children?: Node[] };
  const root = hierarchy<Node>({ agent: "", sum: null, children: agents.map((one) => ({ agent: one.agent, sum: one.sum })) })
    .sum((node) => (node.sum === null ? 0 : value(node.sum)))
    .sort((a, b) => (b.value ?? 0) - (a.value ?? 0) || a.data.agent.localeCompare(b.data.agent));
  if ((root.value ?? 0) <= 0) return null;
  treemap<Node>().tile(treemapSquarify).size([width, height]).paddingInner(2).round(true)(root);
  const whole = report.total.cost;
  const cells = root.leaves().filter((leaf) => (leaf.value ?? 0) > 0).map((leaf) => {
    const box = leaf as typeof leaf & { x0: number; y0: number; x1: number; y1: number };
    const sum = leaf.data.sum as Sum;
    const figure = measure === "cost" ? costWords(sum) : `${fmtTokens(sum.tokens)} tokens`;
    const share = measure === "cost" ? shareWords(sum.cost, whole) : null;
    const cell = { agent: leaf.data.agent, x: box.x0, y: box.y0, width: box.x1 - box.x0, height: box.y1 - box.y0 };
    const fits = (text: string): boolean => text.length * CHAR + ROOM <= cell.width;
    const words = cell.height < 18 || !fits(cell.agent) ? [] : cell.height < 34 || !fits(figure) ? [cell.agent]
      : [cell.agent, figure];
    return { ...cell, words, title: `${cell.agent}: ${figure}${share === null ? "" : `, ${share} of the session`}` };
  });
  return { measure, aria: `The session's agents by ${measure}`, cells };
}

// --- one agent opened -------------------------------------------------------------------------

export interface OpenedAgent {
  items: Item[];
  /** Dim lines under the chart: what the requests' answer cannot show. */
  notes: string[];
  /** Its requests, a row each; null with none. */
  table: Table | null;
}

/** The context of an agent's last request, against its window; null when the index
 * does not know it. A synthetic request sent nothing and is passed over. */
function lastContext(rows: RequestRow[]): { tokens: number; window: number | null } | null {
  const last = rows.filter((row) => !row.synthetic).at(-1);
  return last === undefined || last.context === null ? null : { tokens: last.context, window: last.window };
}

/** One agent's requests opened under its row: the requests line, its last context, the
 * chart, the notes and the table; `Reading…` while the read is out. */
export function openedAgent(read: Read<Report<RequestRow, RequestsTotal>>, today: string | null,
                            clock: Clock = LOCAL): OpenedAgent {
  const report = read.value;
  if (report === null) return { items: read.loading ? [{ kind: "reading", key: "agent" }] : [], notes: [], table: null };
  const items: Item[] = [];
  const line = (key: "requests" | "context", label: string, parts: Part[] | null): void => {
    if (parts !== null) items.push({ kind: "line", key, label, parts });
  };
  const rows = report.rows;
  line("requests", "Requests", requestsLine(report, today, clock));
  line("context", "Context", contextLine(lastContext(rows)));
  if (rows.length) items.push({ kind: "chart" });
  const notes = requestNotes(rows, report.total);
  const unknown = [...new Set(rows.filter((row) => row.window === null).map((row) => row.model ?? UNKNOWN_MODEL))];
  if (unknown.length) notes.push(`window unknown for ${unknown.join(", ")}`);
  return { items, notes, table: rows.length ? requestsTable(report, today, clock) : null };
}

