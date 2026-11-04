// What sessions' page and window block show for each state of their data, worked out with
// no DOM so the Node tests can read it. Every string here goes into the page as text.

import type { Entry, Failure, Header, Listing, Row, WindowAnswer } from "./feed.ts";

/** The harnesses whose sessions the page reads. */
export const HARNESSES = ["claude", "codex", "opencode"] as const;
export type Harness = (typeof HARNESSES)[number];

/** Each harness's name as the page shows it. */
export const HARNESS_LABELS: Record<Harness, string> = {
  claude: "Claude Code",
  codex: "Codex",
  opencode: "OpenCode",
};

/** A message's text is shown up to this many characters until it is expanded. */
export const CUT = 2000;
/** The most of a message's text the page server sends, in UTF-8 bytes. */
export const SENT_MAX = 65536;

/** A data shape the view reads from: what a `Feed` holds. */
export interface FeedState<V> {
  loading: boolean;
  gone: boolean;
  failure: Failure | null;
  refused: string | null;
  value: V | null;
}

/** One session: a harness, its own id and, for a Claude Code subagent, the agent's id. */
export interface Target {
  harness: Harness;
  session: string;
  agent: string | null;
}

export const isHarness = (value: unknown): value is Harness =>
  typeof value === "string" && (HARNESSES as readonly string[]).includes(value);

// -- routes ---------------------------------------------------------------------------------

/** What the page's route names: the id form, one session, or nothing it knows. */
export type PageRoute =
  | { view: "form" }
  | { view: "session"; target: Target }
  | { view: "unknown"; line: string };

/** The line for a route the page has no view for. */
export const UNKNOWN_ROUTE = "There is no such view here. Open a session by its harness and id.";
/** The line for a route that names an agent under a harness whose subagents are sessions
 * of their own. */
export const AGENT_ONLY_CLAUDE = "Only a Claude Code subagent opens under its parent's session. "
  + "An OpenCode or Codex subagent is a session of its own: open it by its own id.";

/** The route `route` (the part of the URL after `/m/sessions/`) names: `""` for the id
 * form, `<harness>/<session>`, or `claude/<session>/<agent>`. Each part is
 * percent-decoded. */
export function parseRoute(route: string): PageRoute {
  const path = route.split(/[?#]/)[0].replace(/^\/+|\/+$/g, "");
  if (path === "") return { view: "form" };
  const parts = path.split("/");
  if (parts.length < 2 || parts.length > 3 || parts.some((part) => part === "")) {
    return { view: "unknown", line: UNKNOWN_ROUTE };
  }
  let decoded: string[];
  try {
    decoded = parts.map((part) => decodeURIComponent(part));
  } catch {
    return { view: "unknown", line: UNKNOWN_ROUTE };
  }
  const [harness, session, agent] = decoded;
  if (!isHarness(harness)) return { view: "unknown", line: UNKNOWN_ROUTE };
  if (agent !== undefined && harness !== "claude") return { view: "unknown", line: AGENT_ONLY_CLAUDE };
  return { view: "session", target: { harness, session, agent: agent ?? null } };
}

/** The page's route for `target`, each part percent-encoded. */
export function routeOf(target: Target): string {
  const parts = [target.harness, target.session, ...(target.agent !== null ? [target.agent] : [])];
  return parts.map((part) => encodeURIComponent(part)).join("/");
}

// -- the id form ----------------------------------------------------------------------------

export const NO_ID = "Type a session id.";
export const NOT_AN_ID = "A session id holds no /, ? or #. Type the id alone, not a path or a link.";
/** What the id form says about the lookup it doesn't do. */
export const FORM_LINE = "Open one session by the id its harness gave it: a Claude Code session id, "
  + "a Codex thread id, or an OpenCode session id (ses_…). This page lists no sessions.";

/** The id form's answer: the route to open, or the problem to show instead. */
export type FormOutcome = { route: string } | { problem: string };

/** The route the id form opens for `harness` and the typed `id`, or why it opens none: an
 * empty id, or one holding `/`, `?` or `#`, which would name another route. */
export function formRoute(harness: string, id: string): FormOutcome {
  const session = id.trim();
  if (!isHarness(harness)) return { problem: "Choose a harness." };
  if (session === "") return { problem: NO_ID };
  if (/[/?#]/.test(session)) return { problem: NOT_AN_ID };
  return { route: routeOf({ harness, session, agent: null }) };
}

// -- the routes' queries --------------------------------------------------------------------

/** `/api/window`'s query for `target`, with `before` for the turns before an earlier
 * answer. */
export function windowPath(target: Target, before: string | null = null): string {
  const query = new URLSearchParams({ harness: target.harness, session: target.session });
  if (target.agent !== null) query.set("agent", target.agent);
  if (before !== null) query.set("before", before);
  return `api/window?${query}`;
}

/** `/api/subagents`' query for `target`'s session. */
export function subagentsPath(target: Target): string {
  return `api/subagents?${new URLSearchParams({ harness: target.harness, session: target.session })}`;
}

// -- numbers and times ----------------------------------------------------------------------

/** A count with thousands separators: `1,234,567`. */
export function fmtCount(n: number): string {
  return Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** A size in bytes: `512 B`, `1.2 KB`, `3.4 MB`. */
export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

const two = (n: number): string => String(n).padStart(2, "0");

/** Epoch seconds as this browser's local `YYYY-MM-DD HH:MM:SS`. */
export function fmtTime(epoch: number): string {
  const d = new Date(epoch * 1000);
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} `
    + `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
}

/** A duration in milliseconds: `850 ms`, `1.2 s`, `3 min 4 s`. */
export function fmtDuration(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)} min ${s % 60} s`;
}

/** What a field the page server sent as null shows: it was hidden where it came from. */
export const HIDDEN = "(hidden)";

// -- the header -----------------------------------------------------------------------------

/** One line of the header: its label and its value as text. */
export interface Field {
  name: string;
  label: string;
  value: string;
  /** Shown as code: an id or a path. */
  code: boolean;
}

/** The header's lines for an answer: harness, id, agent, title, directory, last activity,
 * version, parent, entrypoint and the file read. A value the harness did not say is left
 * out. */
export function headerFields(answer: WindowAnswer): Field[] {
  const header: Header = answer.header;
  const fields: Field[] = [
    { name: "harness", label: "Harness", value: isHarness(answer.harness) ? HARNESS_LABELS[answer.harness] : answer.harness, code: false },
    { name: "session", label: "Session", value: answer.session, code: true },
  ];
  if (answer.agent !== null) fields.push({ name: "agent", label: "Subagent", value: answer.agent, code: true });
  const text = (name: keyof Header, label: string, code: boolean): void => {
    if (!(name in header)) return;
    const value = header[name];
    fields.push({ name, label, value: typeof value === "string" ? value : HIDDEN, code });
  };
  text("title", "Title", false);
  text("cwd", "Directory", true);
  fields.push({ name: "last_activity", label: "Last activity",
                value: answer.last_activity !== null ? fmtTime(answer.last_activity) : "none in these turns", code: false });
  text("version", "Version", false);
  text("parent", "Parent session", true);
  text("entrypoint", "Entrypoint", false);
  fields.push({ name: "path", label: "Read from", value: answer.path ?? HIDDEN, code: true });
  return fields;
}

/** The view's heading: the session's title, else its harness and id. */
export function headingOf(answer: WindowAnswer | null, target: Target): string {
  const title = answer?.header.title;
  if (typeof title === "string" && title !== "" && target.agent === null) return title;
  const who = `${HARNESS_LABELS[target.harness]} session ${target.session}`;
  return target.agent !== null ? `Subagent ${target.agent} of ${who}` : who;
}

// -- a message's text -----------------------------------------------------------------------

/** `text` cut to its first `n` characters, a character being a code point, so no
 * surrogate pair is split. */
export function cutAt(text: string, n: number): string {
  let count = 0;
  let index = 0;
  while (index < text.length && count < n) {
    const code = text.codePointAt(index) as number;
    index += code > 0xffff ? 2 : 1;
    count += 1;
  }
  return text.slice(0, index);
}

/** How many characters (code points) `text` holds. */
export function charCount(text: string): number {
  let count = 0;
  for (const _ of text) count += 1;
  return count;
}

/** A message's text as it is drawn. */
export interface TextView {
  /** The text shown now; null when the text was hidden. */
  shown: string | null;
  /** The text is longer than the cut, so it can be expanded or cut again. */
  long: boolean;
  /** Characters not shown now. */
  rest: number;
  /** When the page server cut the text, the line saying so. */
  sentLine: string | null;
}

/** The line for a text the page server cut. */
export function sentLine(textBytes: number): string {
  return `The page server sent only the first 64 KiB of this message's ${fmtCount(textBytes)} bytes.`;
}

/** `row`'s text, cut at `CUT` characters unless `expanded`, which shows all that was
 * sent. */
export function textView(row: Row, expanded: boolean): TextView {
  const sent = row.truncated === true && typeof row.text_bytes === "number" ? sentLine(row.text_bytes) : null;
  if (typeof row.text !== "string") return { shown: null, long: false, rest: 0, sentLine: sent };
  const total = charCount(row.text);
  const long = total > CUT;
  const shown = long && !expanded ? cutAt(row.text, CUT) : row.text;
  return { shown, long, rest: long && !expanded ? total - CUT : 0, sentLine: sent };
}

// -- records --------------------------------------------------------------------------------

/** One record as the view draws it. `key` is stable for the record within a session. */
export type Item =
  | { type: "message"; key: string; role: string; label: string; reasoning: boolean; tags: string[]; row: Row; unknown: string[] }
  | { type: "call"; key: string; name: string; line: string; unknown: string[] }
  | { type: "result"; key: string; line: string; error: boolean; unknown: string[] }
  | { type: "rule"; key: string; marker: string; line: string; unknown: string[] }
  | { type: "request"; key: string; line: string; unknown: string[] }
  | { type: "note"; key: string; line: string; unknown: string[] };

/** A record's key: where it was read. */
export function rowKey(row: Row): string {
  const at = row.at;
  if (at.table !== undefined || at.id !== undefined) return `${at.table ?? ""}:${at.id ?? ""}:${row.kind}:${row.raw_kind}`;
  return `${at.offset ?? ""}:${at.line ?? ""}:${row.kind}:${row.raw_kind}`;
}

const ROLE_LABELS: Record<string, string> = {
  user: "User",
  assistant: "Assistant",
  system: "System",
  developer: "Developer",
};

/** The line a request row shows: its model and token counts, as the harness wrote them. */
export function requestLine(row: Row): string {
  const parts: string[] = [row.model ?? row.response_model ?? "an unnamed model"];
  const add = (value: number | null | undefined, label: string): void => {
    if (typeof value === "number") parts.push(`${fmtCount(value)} ${label}`);
  };
  add(row.input_tokens, "in");
  add(row.cache_read_input_tokens, "cache read");
  add(row.cache_creation_input_tokens, "cache write");
  add(row.output_tokens, "out");
  add(row.reasoning_output_tokens, "reasoning");
  if (typeof row.cost_usd === "number") parts.push(`$${row.cost_usd.toFixed(4)}`);
  if (row.synthetic === true) parts.push("synthetic");
  return parts.join(" · ");
}

/** The label a marker's rule carries. */
export function markerLine(row: Row): string {
  switch (row.marker) {
    case "compaction": {
      const how = [row.trigger, typeof row.pre_tokens === "number" ? `${fmtCount(row.pre_tokens)} tokens before` : null]
        .filter((part): part is string => typeof part === "string");
      return how.length ? `Compacted (${how.join(", ")})` : "Compacted";
    }
    case "turn_start": return "Turn started";
    case "turn_end": return typeof row.duration_ms === "number" ? `Turn ended after ${fmtDuration(row.duration_ms)}` : "Turn ended";
    case "turn_aborted": return "Turn aborted";
    case "rollback": return "Rolled back";
    case "settings": {
      const what = [row.model ? `model ${row.model}` : null, row.provider ? `provider ${row.provider}` : null,
                    row.service_tier ? `tier ${row.service_tier}` : null]
        .filter((part): part is string => part !== null);
      return what.length ? `Settings: ${what.join(", ")}` : "Settings";
    }
    case "title": {
      const title = typeof row.title === "string" ? row.title : HIDDEN;
      return row.title_source ? `Title (${row.title_source}): ${title}` : `Title: ${title}`;
    }
    case "session_meta": return "Session started";
    default: return `Marker ${row.marker ?? row.raw_kind}`;
  }
}

/** The line a limits row shows: each window's use. */
export function limitsLine(row: Row): string {
  const name = row.limit_name ?? row.limit_id ?? "limits";
  const windows = (row.windows ?? []).map((w) =>
    `${w.name} ${typeof w.used_percent === "number" ? `${w.used_percent}%` : "unknown"}`);
  return windows.length ? `Limits (${name}): ${windows.join(", ")}` : `Limits (${name})`;
}

/** What one record draws as. A message is text; a synthetic message is a labelled rule; a
 * tool call is its one-line summary; a tool result is its size and error flag; a marker is
 * a labelled rule; a request is its model and token counts. */
export function itemOf(row: Row): Item {
  const key = rowKey(row);
  const unknown = row.unknown;
  switch (row.kind) {
    case "message": {
      if (row.synthetic === true) {
        const text = typeof row.text === "string" ? cutAt(row.text.split("\n")[0], 200) : HIDDEN;
        return { type: "rule", key, marker: "synthetic", line: `Synthetic: ${text}`, unknown };
      }
      const role = row.role ?? "unknown";
      const reasoning = row.reasoning === true;
      const label = reasoning ? "Thinking" : (ROLE_LABELS[role] ?? (row.role ? row.role : "Message"));
      const tags = [...(row.flags ?? [])];
      if (typeof row.model === "string") tags.push(row.model);
      return { type: "message", key, role, label, reasoning, tags, row, unknown };
    }
    case "tool_call": {
      const name = row.name ?? "tool";
      const line = typeof row.summary === "string" ? row.summary : HIDDEN;
      return { type: "call", key, name, line, unknown };
    }
    case "tool_result": {
      const size = typeof row.size_bytes === "number" ? fmtBytes(row.size_bytes) : "size unknown";
      const error = row.is_error === true;
      return { type: "result", key, line: error ? `Result: error, ${size}` : `Result: ${size}`, error, unknown };
    }
    case "marker":
      return { type: "rule", key, marker: row.marker ?? row.raw_kind, line: markerLine(row), unknown };
    case "request":
      return { type: "request", key, line: requestLine(row), unknown };
    case "limits":
      return { type: "note", key, line: limitsLine(row), unknown };
    case "malformed":
      return { type: "note", key, line: `A line that can't be read (${row.why ?? "malformed"}${row.unterminated ? ", unterminated" : ""})`, unknown };
    default:
      return { type: "note", key, line: `${row.recognised === false ? "Unrecognised record" : "Record"} ${row.raw_kind}`, unknown };
  }
}

/** The line naming what a record's harness did not say. */
export function unknownLine(unknown: string[]): string | null {
  return unknown.length ? `Not said: ${unknown.join(", ")}` : null;
}

// -- the window, with the pages read before it ----------------------------------------------

/** The window as it is drawn. */
export interface WindowView {
  /** `loading`, `up`, `refused` (the page server said why it can't read this session),
   * `failed` (the read failed; the last window read is kept), or `gone` (sessions went
   * away; nothing is drawn). */
  state: "loading" | "up" | "refused" | "failed" | "gone";
  answer: WindowAnswer | null;
  line: string | null;
}

export function windowView(data: FeedState<WindowAnswer>): WindowView {
  if (data.gone) return { state: "gone", answer: null, line: null };
  if (data.refused !== null) return { state: "refused", answer: null, line: data.refused };
  if (data.failure !== null) return { state: "failed", answer: data.value, line: data.failure.why };
  if (data.loading || data.value === null) return { state: "loading", answer: null, line: null };
  return { state: "up", answer: data.value, line: null };
}

/** The records of the newest window and the earlier pages read before it (oldest page
 * first), oldest record first, and where "Earlier" reads from next. */
export interface Pages {
  items: Item[];
  hasEarlier: boolean;
  before: string | null;
  /** The line for a backward read that stopped at its byte limit, else null. */
  limitLine: string | null;
  /** The line for a window that reaches the session's first record, else null. */
  startLine: string | null;
}

export const LIMIT_LINE = "The read stopped at its size limit, so the oldest turn shown may be cut short.";
export const START_LINE = "This is the start of the session.";

export function pages(latest: WindowAnswer, earlier: WindowAnswer[]): Pages {
  const oldest = earlier[0] ?? latest;
  const rows = [...earlier, latest].flatMap((answer) => answer.records);
  // One line can hold several records of one kind (two text blocks of one assistant
  // line): the second and later of a key are told apart by their count.
  const seen = new Map<string, number>();
  const items = rows.map((row) => {
    const item = itemOf(row);
    const n = seen.get(item.key) ?? 0;
    seen.set(item.key, n + 1);
    return n === 0 ? item : { ...item, key: `${item.key}#${n}` };
  });
  return {
    items,
    hasEarlier: oldest.has_earlier && oldest.before !== null,
    before: oldest.has_earlier ? oldest.before : null,
    limitLine: oldest.ended === "limit" ? LIMIT_LINE : null,
    startLine: !oldest.has_earlier ? START_LINE : null,
  };
}

/** Whether the earlier pages read before a refresh still sit right before the newly read
 * window: the same session, and a window that starts where the last one started. When it
 * starts elsewhere (a new turn pushed the oldest one out), they are dropped. */
export function keepEarlier(last: WindowAnswer | null, next: WindowAnswer): boolean {
  return last !== null && last.harness === next.harness && last.session === next.session
    && last.agent === next.agent && last.before === next.before;
}

// -- subagents ------------------------------------------------------------------------------

/** One subagent as the listing draws it. */
export interface EntryView {
  agentId: string;
  /** Its type, else "subagent". */
  type: string;
  /** Its description; null when it has none, `HIDDEN` when it was hidden. */
  description: string | null;
  route: string;
  /** What the window block opens it with. */
  target: Target | null;
  size: string | null;
}

/** The listing as it is drawn. */
export interface ListingView {
  /** The listing's own state, or `loading`, `refused`, `failed` or `gone` as for the
   * window. */
  state: "loading" | "listed" | "none" | "unreadable" | "unsupported" | "refused" | "failed" | "gone";
  entries: EntryView[];
  /** The line to show: the reason for `unreadable`, `unsupported`, `refused` and
   * `failed`, the line for `none`. */
  line: string | null;
  /** Each part that could not be read, as `where: reason`. */
  parts: string[];
}

export const NO_SUBAGENTS = "This session has no subagents.";

export function entryView(entry: Entry, harness: string): EntryView {
  const description = "description" in entry
    ? (typeof entry.description === "string" ? entry.description : HIDDEN) : null;
  return {
    agentId: entry.agent_id,
    type: typeof entry.agent_type === "string" && entry.agent_type !== "" ? entry.agent_type : "subagent",
    description,
    route: entry.route,
    target: isHarness(harness) ? { harness, session: entry.session, agent: entry.agent ?? null } : null,
    size: typeof entry.size === "number" ? fmtBytes(entry.size) : null,
  };
}

export function listingView(data: FeedState<Listing>): ListingView {
  const base = { entries: [] as EntryView[], line: null, parts: [] as string[] };
  if (data.gone) return { ...base, state: "gone" };
  if (data.refused !== null) return { ...base, state: "refused", line: data.refused };
  const listing = data.value;
  const kept = listing !== null ? listing.entries.map((entry) => entryView(entry, listing.harness)) : [];
  if (data.failure !== null) return { ...base, state: "failed", entries: kept, line: data.failure.why };
  if (data.loading || listing === null) return { ...base, state: "loading" };
  const parts = listing.unreadable_parts.map((part) =>
    `${part.where ?? HIDDEN}: ${part.reason ?? HIDDEN}`);
  const reason = listing.state === "unreadable" || listing.state === "unsupported"
    ? (listing.reason ?? HIDDEN) : listing.state === "none" ? NO_SUBAGENTS : null;
  return { state: listing.state, entries: kept, line: reason, parts };
}
