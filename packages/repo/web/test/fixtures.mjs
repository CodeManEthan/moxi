// Rows of `/api/repos` for the web tests, each with every key the route sends, and a clock
// that reads UTC so the words are the same in any zone.

const pad = (n) => String(n).padStart(2, "0");

/** Hour and minute, and the day, in UTC. */
export const UTC = {
  time: (at) => { const d = new Date(at * 1000); return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`; },
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};

/** Epoch seconds of a UTC time on 2026-11-18 (`"16:40"`), or of a full ISO time. */
export const T = (hhmm, day = "2026-11-18") => Date.parse(`${day}T${hhmm}:00Z`) / 1000;
/** A record's `at` for the same time. */
export const AT = (hhmm, day = "2026-11-18") => `${day}T${hhmm}:00Z`;

export const TODAY = "2026-11-18";
export const HOME = "/home/jp";

export const command = (label) => ({ label, command: `moxi repo ${label.toLowerCase()}` });

export function event(over = {}) {
  return { event: "pre-commit", via: "config entry", pieces: [], existing: null, existing_hook: null,
           chain_line: null, hook: null, line: null, ...over };
}

export function piece(over = {}) {
  const id = over.id ?? "repo/commit-rules";
  return { id, event: "commit-msg", enabled: true, fail: "closed", order: 50, settings: [], declared: true,
           recorded: false, toggle: { label: "Turn off", command: `moxi repo git-hooks disable /r ${id}` },
           remove: { label: "Remove", command: `moxi repo git-hooks remove /r ${id}` }, ...over };
}

export function problem(over = {}) {
  return { state: "fail", detail: "something is wrong", fix: null, code: null, piece: null, event: null,
           refresh: false, ...over };
}

export function spec(over = {}) {
  return { name: "notes/snapshot", saved: true, problem: null, status: "committed", line: "committed abc1234",
           time: T("18:30"), refused_since: null, refused_passes: null, error_since: null, error_passes: null,
           index_busy_since: null, index_stale: false, last_commit: "abc1234def5678900",
           last_commit_time: T("18:30"), remote: null, pushed: [], push_failed: [], unpushed: [],
           run: command("Run"), forget: command("Forget"), ...over };
}

export function record(over = {}) {
  return { kind: "piece", piece: null, spec: null, branch: null, text: "it broke", fix: null, at: AT("16:40"),
           ...over };
}

export function repo(over = {}) {
  return { root: `${HOME}/code/atlas-api`, project: null, indexed: true, git_version: "2.56.0", mode: "config",
           pinned: false, events: [], own_hooks: [], pieces: [], problems: [], specs: [], specs_problem: null,
           records: [], rules: [], proof: false, commands: [command("List"), command("Refresh"), command("Specs")],
           ...over };
}

export function offered(over = {}) {
  const id = over.id ?? "repo/commit-rules";
  return { id, module: id.split("/")[0], name: id.split("/")[1], event: "commit-msg", fail: "closed", order: 50,
           doc: "checks a message's trailers", settings: [], ...over };
}

export function value(over = {}) {
  return { state: "up", detail: null, today: TODAY, repos: [], offered: [], refused: [], unindexed: null, ...over };
}

/** One item of a plan's `writes`. */
export function write(place, key, from, to, over = {}) {
  return { place, piece: null, event: null, key, from, to, seed: false, ...over };
}

/** A planned piece. */
export function planned(over = {}) {
  return { id: "repo/commit-rules", event: "commit-msg", new: true, implied_by: null, seeds: [], ...over };
}

/** A plan of `/api/plan`, with every key the route sends. */
export function plan(over = {}) {
  return { root: `${HOME}/code/atlas-api`, name: "atlas-api", is_repo: true, git_version: "2.56.0", mode: "config",
           indexed: true, pieces: [], mode_change: null, own_hooks: [], in_progress: null, writes: [], refusal: null,
           digest: "d1", ...over };
}

/** An act's answer, with every key the route sends. */
export function answer(over = {}) {
  return { outcome: "done", lines: [], problems: [], chain: [], plan: null, refusal: null, ...over };
}
