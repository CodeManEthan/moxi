// What repo's page shows for each state of its data, worked out with no DOM so the Node
// tests can read it. Every string here goes into the page as text.

import type { EventRow, Failure, PieceRow, RecordRow, RepoRow, Repos, SpecRow } from "./feed.ts";

/** The page's data as its `Feed` holds it. */
export interface FeedState {
  loading: boolean;
  gone: boolean;
  failure: Failure | null;
  value: Repos | null;
}

export interface PageView {
  /** `loading`, `up`, `empty`, `error`, `failed` (the last repos read are kept) or `gone`
   * (a 404: repo went away; nothing is drawn). */
  state: "loading" | "up" | "empty" | "error" | "failed" | "gone";
  repos: RepoRow[];
  detail: string | null;
}

export const SERVER_ERROR = "repo's page server hit an error";

export function pageView(data: FeedState): PageView {
  const repos = data.value?.state === "up" ? data.value.repos : [];
  if (data.gone) return { state: "gone", repos: [], detail: null };
  if (data.failure !== null) return { state: "failed", repos, detail: data.failure.why };
  if (data.loading || data.value === null) return { state: "loading", repos, detail: null };
  if (data.value.state === "error") {
    return { state: "error", repos: [], detail: data.value.detail ?? SERVER_ERROR };
  }
  return { state: repos.length ? "up" : "empty", repos, detail: null };
}

/** A length of time in whole units: `45s`, `41m`, `1h4m`, `3d`. */
export function span(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  if (total < 60) return `${total}s`;
  if (total < 3600) return `${Math.floor(total / 60)}m`;
  if (total < 86400) {
    const minutes = Math.floor((total % 3600) / 60);
    return `${Math.floor(total / 3600)}h${minutes ? `${minutes}m` : ""}`;
  }
  return `${Math.floor(total / 86400)}d`;
}

/** "5m ago" for an epoch time at the server's `now`; "at <UTC time>" before the first
 * read gives a clock. */
export function ago(when: number, now: number | null): string {
  if (now === null) return `at ${new Date(when * 1000).toISOString().slice(0, 19)}Z`;
  return `${span(now - when)} ago`;
}

/** The repo's heading line under its path: project, git and mode. */
export function repoFacts(repo: RepoRow): string[] {
  const out: string[] = [];
  if (repo.project) out.push(`project ${repo.project}`);
  out.push(repo.git_version ? `git ${repo.git_version}` : "git version unknown");
  if (repo.mode) out.push(`${repo.mode} mode${repo.pinned ? " (pinned)" : ""}`);
  return out;
}

/** How git reaches one event's pieces, in a few words. */
export function viaText(event: EventRow): string {
  if (event.via === null) return "every piece disabled, so nothing of repo's runs";
  if (event.via === "config entry") return "run by git from a config entry";
  if (event.via === "shim") return "run by repo's shim";
  return "run through the launcher, from the existing git hook's chain line";
}

/** The chain line's state in launcher mode, or null in another mode. */
export function chainText(event: EventRow): string | null {
  if (event.chain_line === "present") return `The chain line is in ${event.hook ?? "the git hook"}.`;
  if (event.chain_line === "needed") {
    return `Chain line needed: add this line to ${event.hook ?? "the git hook"}, before any line that reads stdin:`;
  }
  return null;
}

/** The existing git hook's line. */
export function existingText(event: EventRow): string {
  return event.existing ? `Existing git hook: ${event.existing}` : "No existing git hook.";
}

/** Each event's pieces in the order they run (`pieces` comes sorted by event, order, id). */
export function piecesOf(repo: RepoRow, event: EventRow): PieceRow[] {
  return repo.pieces.filter((piece) => piece.event === event.event);
}

/** A piece's tags: fail policy, then what is wrong or off. */
export function pieceTags(piece: PieceRow): string[] {
  const out = [`fail ${piece.fail}`, `order ${piece.order}`];
  if (!piece.enabled) out.push("disabled");
  if (!piece.declared) out.push("no installed module declares it");
  if (piece.recorded) out.push("doctor record open");
  return out;
}

export function settingText(setting: { key: string; values: string[] }): string {
  return `${setting.key} = ${setting.values.join(", ")}`;
}

/** A spec's lines: its last pass, its last commit, its pushes. */
export function specLines(spec: SpecRow, now: number | null): string[] {
  const out: string[] = [];
  if (spec.problem) out.push(`The saved spec can't be read: ${spec.problem}`);
  if (spec.status === null) out.push("No pass has run.");
  else {
    const when = spec.time === null ? "" : `, ${ago(spec.time, now)}`;
    out.push(`Last pass: ${spec.status}${when}${spec.line ? `: ${spec.line}` : ""}`);
  }
  if (spec.refused_since !== null) {
    out.push(`Refused since ${ago(spec.refused_since, now)}, ${spec.refused_passes ?? 0} passes.`);
  }
  if (spec.last_commit) {
    const when = spec.last_commit_time === null ? "" : `, ${ago(spec.last_commit_time, now)}`;
    out.push(`Last commit: ${spec.last_commit.slice(0, 12)}${when}`);
  } else if (spec.saved) out.push("Last commit: none by its author.");
  for (const pushed of spec.pushed) {
    out.push(`Last push of ${pushed.branch}${spec.remote ? ` to ${spec.remote}` : ""}: ${pushed.sha.slice(0, 12)}`);
  }
  for (const failed of spec.push_failed) {
    const when = failed.failed_at === null ? "" : ` ${ago(failed.failed_at, now)}`;
    out.push(`Push of ${failed.branch} failed${when}: ${failed.error ?? "no reason given"}`);
  }
  if (spec.saved && !spec.remote) out.push("It doesn't push.");
  return out;
}

/** What a record is about, in a few words. */
export function recordSubject(record: RecordRow): string {
  if (record.kind === "piece") return `git hook piece ${record.piece ?? "?"}`;
  if (record.kind === "push") return `spec ${record.spec ?? "?"}, push of ${record.branch ?? "?"}`;
  return `spec ${record.spec ?? "?"}`;
}
