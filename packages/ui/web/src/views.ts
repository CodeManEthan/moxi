// The words the modules view, the servers block and the shell's error panel show, worked
// out with no DOM so the tests can read them. Every line here is put into the page as
// text, never as HTML.

/** How a line or a row reads at a glance, as the doctor's states do. */
export type Tone = "ok" | "off" | "warn" | "fail";

/** One page server's row of the topic `ui.servers`. */
export interface ServerRow {
  module: string;
  state: string;
  server_id?: string | null;
  version?: string | null;
  transport?: string | null;
  pid?: number | null;
  child?: boolean;
  log?: string;
  starts?: number;
  failed_starts?: number;
  failed_in_row?: number;
  crashes?: number;
  last_exit?: number | null;
  last_exit_at?: string | null;
  last_crash_at?: string | null;
  reason?: string | null;
}

/** A row as the servers block draws it. */
export interface ServerView {
  module: string;
  tone: Tone;
  /** The state in words. */
  state: string;
  lines: Array<{ tone: Tone | null; text: string }>;
}

const HOUR_MS = 3_600_000;
/** As the doctor reads it: this many failed starts in a row is a fail. */
export const FAILS_IN_ROW = 3;

/** When `at` (`YYYY-MM-DDTHH:MM:SSZ`) was, from `now` (ms), in words; "" for no time. */
export function ago(at: string | null | undefined, now: number): string {
  if (typeof at !== "string") return "";
  const then = Date.parse(at);
  if (Number.isNaN(then)) return at;
  const secs = Math.max(0, Math.round((now - then) / 1000));
  if (secs < 60) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

function count(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** One page server's row in words: its state, the counts, its last exit, a crash in the
 * last hour, and three failed starts in a row, as the doctor's `page:<module>` row. */
export function serverView(row: ServerRow, now: number): ServerView {
  const lines: ServerView["lines"] = [];
  let tone: Tone;
  let state: string;
  switch (row.state) {
    case "running": {
      tone = "ok";
      state = "running";
      const bits = [row.pid != null ? `pid ${row.pid}` : "", row.version ? `version ${row.version}` : "",
                    row.transport ? `over ${row.transport === "tcp" ? "TCP" : "a private socket"}` : ""];
      const said = bits.filter(Boolean).join(", ");
      if (said) lines.push({ tone: null, text: said });
      if (row.child === false) lines.push({ tone: null, text: "started outside this shell, which uses it" });
      break;
    }
    case "starting":
      tone = "off";
      state = "starting";
      break;
    case "draining":
      tone = "warn";
      state = "restarting";
      lines.push({ tone: null, text: "Another version is installed: open requests finish, then the next request starts it." });
      break;
    case "failed":
      tone = "fail";
      state = "failed to start";
      if (row.reason) lines.push({ tone: "fail", text: row.reason });
      lines.push({ tone: null, text: "The next request after 10 s tries again." });
      break;
    case "idle":
      tone = "off";
      state = "not running";
      lines.push({ tone: null, text: "It starts on the next request to its panel." });
      break;
    default:
      tone = "off";
      state = row.state;
  }
  const starts = row.starts ?? 0;
  const crashes = row.crashes ?? 0;
  const failed = row.failed_starts ?? 0;
  if (starts || crashes || failed) {
    const bits = [count(starts, "start", "starts")];
    if (failed) bits.push(count(failed, "failed start", "failed starts"));
    if (crashes) bits.push(count(crashes, "crash", "crashes"));
    lines.push({ tone: null, text: `${bits.join(", ")} since the shell started` });
  }
  if (row.last_exit != null && row.state !== "running") {
    const when = ago(row.last_exit_at, now);
    lines.push({ tone: null, text: `last exited with code ${row.last_exit}${when ? `, ${when}` : ""}` });
  }
  const crashed = typeof row.last_crash_at === "string" ? Date.parse(row.last_crash_at) : NaN;
  if (!Number.isNaN(crashed) && now - crashed < HOUR_MS) {
    lines.push({ tone: "warn", text: `crashed ${ago(row.last_crash_at, now)}` });
    if (tone === "ok" || tone === "off") tone = "warn";
  }
  const inRow = row.failed_in_row ?? 0;
  if (inRow >= FAILS_IN_ROW) {
    lines.push({ tone: "fail", text: `the last ${inRow} starts failed` });
    tone = "fail";
  }
  if ((tone === "fail" || tone === "warn") && row.log) {
    lines.push({ tone: null, text: `log: ${row.log}` });
  }
  return { module: row.module, tone, state, lines };
}

/** The servers block's rows, by module name. */
export function serverViews(value: unknown, now: number): ServerView[] {
  const rows = (value as { servers?: unknown } | null)?.servers;
  if (!Array.isArray(rows)) return [];
  return rows.filter((row): row is ServerRow => typeof row?.module === "string" && typeof row?.state === "string")
    .sort((a, b) => (a.module < b.module ? -1 : a.module > b.module ? 1 : 0))
    .map((row) => serverView(row, now));
}

/** One module of the topic `ui.modules`, as the modules view reads it. */
export interface ModuleView {
  name: string;
  state: string;
  reason: string;
  version: string;
  summary: string;
  page: boolean;
  mounted: boolean;
  export_error: string | null;
  components: Array<{ name: string; title: string; doc: string; place: string; home: boolean; contract: number }>;
  refused: Array<{ name: string; reason: string }>;
}

/** A module's state in words, with its tone. */
export function moduleState(mod: ModuleView): { tone: Tone; text: string } {
  if (mod.state === "present") return { tone: "ok", text: "installed" };
  return { tone: "fail", text: mod.reason ? `${mod.state}: ${mod.reason}` : mod.state };
}

/** What the modules view says of a module's page and components, one line each. */
export function moduleLines(mod: ModuleView): Array<{ tone: Tone | null; text: string }> {
  const lines: Array<{ tone: Tone | null; text: string }> = [];
  if (mod.page && mod.export_error) {
    lines.push({ tone: "fail", text: `Its page can't be served: ${mod.export_error}` });
  } else if (mod.page && !mod.mounted && mod.state === "present") {
    lines.push({ tone: "warn", text: "Its page is not mounted yet." });
  }
  const placed = mod.components.map((comp) => comp.title || comp.name);
  if (placed.length) lines.push({ tone: null, text: `Components: ${placed.join(", ")}` });
  for (const item of mod.refused) {
    lines.push({ tone: "warn", text: `${item.name} is not shown: ${item.reason}` });
  }
  return lines;
}

/** The modules view's list: present modules first, the shell's own first of those. */
export function moduleOrder(modules: readonly ModuleView[]): ModuleView[] {
  const rank = (mod: ModuleView): number => (mod.name === "ui" ? 0 : mod.state === "present" ? 1 : 2);
  return [...modules].sort((a, b) => rank(a) - rank(b) || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}

/** The home blocks the modules view places: each present module's `block` components
 * that ask for home, the shell's own first, then by module and name. */
export function homeBlocks(modules: readonly ModuleView[]): Array<{ module: string; name: string; title: string }> {
  const out: Array<{ module: string; name: string; title: string }> = [];
  for (const mod of moduleOrder(modules)) {
    if (mod.state !== "present") continue;
    for (const comp of [...mod.components].sort((a, b) => (a.name < b.name ? -1 : 1))) {
      if (comp.home && comp.place === "block") out.push({ module: mod.name, name: comp.name, title: comp.title });
    }
  }
  return out;
}

/** One doctor row, as `/m/ui/api/doctor` gives it. */
export interface DoctorRow {
  module: string;
  name: string;
  state: string;
  detail: string;
  fix: string | null;
}

const ORDER: Record<string, number> = { fail: 0, warn: 1, off: 2, ok: 3 };

/** The doctor's rows, the worst first; a state the view doesn't know reads as warn. */
export function doctorRows(rows: readonly DoctorRow[]): Array<DoctorRow & { tone: Tone }> {
  return rows.map((row) => ({ ...row, tone: (row.state in ORDER ? row.state : "warn") as Tone }))
    .sort((a, b) => ORDER[a.tone] - ORDER[b.tone]);
}

/**
 * The shell's error panel for a page whose script did not load, from the answer a fetch
 * of that script got: a start that failed (the proxy's 502 `{"error": "start", "module",
 * "exit", "log"}`) and the proxy's other errors. Null when the answer names none of
 * them, so the shell says only that the panel did not load.
 */
export function failurePanel(module: string, status: number, data: unknown): { title: string; lines: string[] } | null {
  const body = (typeof data === "object" && data !== null ? data : {}) as Record<string, unknown>;
  const log = typeof body.log === "string" && body.log ? body.log : null;
  const tail = log ? [`Its log: ${log}`] : [];
  switch (status === 0 ? "network" : body.error) {
    case "start": {
      const exit = typeof body.exit === "number" ? body.exit : null;
      return {
        title: `${module}'s page server did not start`,
        lines: [exit === null ? "It gave no ready line within 10 s and was stopped." : `It exited with code ${exit}.`,
                ...tail, "The next try is at the first request after 10 s."],
      };
    }
    case "unreachable":
      return { title: `${module}'s page server can't be reached`,
               lines: ["The shell started it but could not connect to it.", ...tail] };
    case "upstream":
      return { title: `${module}'s page server gave no answer`, lines: [...tail] };
    case "timeout":
      return { title: `${module}'s page server is not answering`, lines: ["No answer came within 30 s."] };
    case "stopping":
      return { title: "The shell is stopping", lines: ["Run moxi ui open to start it again."] };
    case "network":
      return { title: "No answer from the shell", lines: ["The shell may have stopped. Run moxi ui open to start it again."] };
    default:
      return null;
  }
}
