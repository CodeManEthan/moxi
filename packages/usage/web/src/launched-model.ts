// What launcher says of the sessions it runs now, as the page draws it, with no DOM so the
// Node tests can drive it. With launcher installed the page reads launcher's
// `/api/running` and, at the desk, follows its topic `running`, which carries the same
// value. A usage session is matched to a running launch by its harness and session id; a
// session with no id, or one no running launch holds, is matched to nothing.
//
// A matched session takes three things from its launch: the launch's name where no name
// was typed in the harness, `slot 4` or `terminal` before its `where`, and the effort it
// was launched with on the opened `Session` line. Without launcher, or with its read
// failing, nothing is matched and the page draws none of them.
//
// Every string here goes into the page as text.

/** The route the page reads the running launches from, relative to launcher's page. */
export const RUNNING_PATH = "api/running";
/** The topic of launcher's stream that carries the same value. */
export const RUNNING_TOPIC = "running";

/** What the page draws of one running launch. */
export interface Launch {
  /** What launcher calls the launch wherever it is listed. */
  name: string;
  /** Where it runs, the first half of Home's `where`: `slot 4`, `terminal`, `headless`. */
  runs: string;
  /** The effort it was launched with; null where it set none. */
  effort: string | null;
}

/** The running launches by `<harness>/<session id>`. */
export type Launched = ReadonlyMap<string, Launch>;

const isTable = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const text = (value: unknown): string | null =>
  (typeof value === "string" && value.trim() !== "" ? value.trim() : null);

const keyOf = (harness: string, session: string): string => `${harness}/${session}`;

/** Where a launch runs as Home's running rows begin it: `slot N` for a slot (`slot` with
 * no number), else its target's own word; null with no target. */
export function runsWord(where: unknown): string | null {
  if (!isTable(where)) return null;
  const target = text(where.target);
  if (target === null) return null;
  if (target !== "slot") return target;
  const slot = text(where.slot);
  return slot === null ? "slot" : `slot ${slot}`;
}

/**
 * The running launches from a value of `/api/running` or of the topic `running`, by
 * harness and session id. A launch with no session id, no name or no target is left
 * out; so is a second launch that claims the same session. A value whose state is not
 * `up` gives none. Null for a body that is not such a value.
 */
export function parseLaunched(data: unknown): Launched | null {
  if (!isTable(data) || !Array.isArray(data.sessions)) return null;
  const found = new Map<string, Launch>();
  if (data.state !== "up") return found;
  for (const entry of data.sessions) {
    if (!isTable(entry)) continue;
    const harness = text(entry.harness);
    const session = text(entry.session_id);
    const name = text(entry.name);
    const runs = runsWord(entry.where);
    if (harness === null || session === null || name === null || runs === null) continue;
    const key = keyOf(harness, session);
    if (found.has(key)) continue;
    found.set(key, { name: name.replace(/\s+/g, " "), runs, effort: text(entry.effort) });
  }
  return found;
}

/** The running launch that holds a session; null with no session id, with nothing read,
 * and when no running launch holds it. */
export function launchOf(launched: Launched | null,
                         ref: { harness: string; session_id: string | null }): Launch | null {
  if (launched === null || ref.session_id === null) return null;
  return launched.get(keyOf(ref.harness, ref.session_id)) ?? null;
}
