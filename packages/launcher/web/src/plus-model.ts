// What the `+` at the left of the chip offers, worked out with no DOM so the Node tests can
// drive it. prompt.ts draws it.
//
// The `+` has two entries. `Brief from a file` takes a path for the row `brief_file`: while
// one is set the box is off and the launch's first turn is that file (`launchRequest`).
// `Continue the last session` resumes the newest past session of the place the line above
// the box names, made by the user the page server launches as; it is read from
// `/api/sessions` each time the menu opens, never when the piece loads. A click on it is
// `POST /api/resume` with that session and nothing else: the box keeps its text.
//
// Every string here goes into the page as text.

import type { FieldDescriptor } from "/m/ui/client.js";

import type { Past } from "./panel.ts";
import { tilde } from "./prompt-model.ts";
import type { Pick } from "./prompt-model.ts";

export const PLUS_LABEL = "More ways to start";
export const BRIEF_FROM_FILE = "Brief from a file";
export const BRIEF_FILE_FIELD = "Path to a file holding the brief";
export const CONTINUE_LAST = "Continue the last session";
export const RESUMES_IN_SLOT = "resumes in a slot";
export const COPIES_RESUME = "copies the resume command";
/** The word that takes the brief file off, on the line under the box. */
export const CLEAR_FILE = "Clear";

/** The read the menu makes when it opens: the twenty newest past sessions. */
export const SESSIONS_READ = "api/sessions?limit=20";

/** Whether the harness's form has the row `brief_file`, so a file can be sent. */
export function takesBriefFile(fields: readonly FieldDescriptor[]): boolean {
  return fields.some((field) => field.name === "brief_file" && field.needs_absent !== true);
}

/** Whether a past session is of the place `pick` names: by its project for a project, by
 * its folder, however the home folder is written, for a folder. */
export function ofPlace(past: Past, pick: Pick, home: string): boolean {
  if (pick.kind === "project") return (past.project_key ?? null) === pick.key;
  if (pick.kind === "folder") return typeof past.cwd === "string" && tilde(past.cwd, home) === tilde(pick.path, home);
  return false;
}

/**
 * The session `Continue the last session` resumes: the newest of `pasts` (newest first, as
 * `/api/sessions` lists them) that can be resumed now, is of the place the line names, and
 * was made by `actor`, the user the page server launches as. Another actor's session in the
 * same place, an agent's worker among them, is never his last. Null when none is, so the
 * entry is not shown.
 */
export function lastSession(pasts: readonly Past[], pick: Pick | null, actor: string, home: string): Past | null {
  if (pick === null || actor === "") return null;
  return pasts.find((past) => past.resumable && typeof past.session_id === "string" && past.session_id !== ""
    && past.actor === actor && ofPlace(past, pick, home)) ?? null;
}

/** The entry's dim line: what a click does with the session. */
export function continueWords(past: Past): string {
  return past.target === "slot" ? RESUMES_IN_SLOT : COPIES_RESUME;
}

/** The body of the click's `POST /api/resume`. */
export function resumeBody(past: Past): { session: string } {
  return { session: past.session_id ?? "" };
}
