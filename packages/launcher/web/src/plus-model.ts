// What the `+` at the left of the chip offers, worked out with no DOM so the Node tests can
// drive it. prompt.ts draws it.
//
// The `+` has three entries. `Brief from a file` takes a path for the row `brief_file`:
// while one is set the box is off and the launch's first turn is that file
// (`launchRequest`). `Handoff from a file` takes a path for the row `handoff`, which the
// launch sends in its first turn beside the brief; a handoff the user's defaults or the
// applied preset fill is shown the same way, and `Clear` sends none (`handoffOf`).
// `Continue the last session` resumes the newest past session of the place the line above
// the box names, made by the user the page server launches as; it is read from
// `/api/sessions` each time the menu opens, never when the piece loads. A click on it is
// `POST /api/resume` with that session and the box's text as its brief, or the brief file
// in its place while one is set, as a launch sends them. An empty box sends no brief and
// `plain`, which asks for a resume with no first turn, a `go` session's too. A scratch
// folder has no past sessions: with Scratch on the line the entry says there is none to
// continue, and no other place's session is offered.
//
// Every string here goes into the page as text.

import type { FieldDescriptor } from "/m/ui/client.js";

import { resumeBody as pastResumeBody } from "./panel.ts";
import type { Past, ResumeBody } from "./panel.ts";
import { tilde } from "./prompt-model.ts";
import type { Pick } from "./prompt-model.ts";

export const PLUS_LABEL = "More ways to start";
export const BRIEF_FROM_FILE = "Brief from a file";
export const BRIEF_FILE_FIELD = "Path to a file holding the brief";
export const CONTINUE_LAST = "Continue the last session";
export const RESUMES_IN_SLOT = "resumes in a slot";
export const COPIES_RESUME = "copies the resume command";
/** The entry's dim line while the line names a scratch folder. */
export const NONE_TO_CONTINUE = "none to continue in a throwaway folder";
export const HANDOFF_FROM_FILE = "Handoff from a file";
export const HANDOFF_FILE_FIELD = "Path to a handoff file";
/** The word that takes the brief file or the handoff off, on its line under the box. */
export const CLEAR_FILE = "Clear";

/** The read the menu makes when it opens: the twenty newest past sessions. */
export const SESSIONS_READ = "api/sessions?limit=20";

/** Whether the harness's form has the row `brief_file`, so a file can be sent. */
export function takesBriefFile(fields: readonly FieldDescriptor[]): boolean {
  return fields.some((field) => field.name === "brief_file" && field.needs_absent !== true);
}

/** Whether the harness's form has the row `handoff`, so a handoff can be sent. */
export function takesHandoff(fields: readonly FieldDescriptor[]): boolean {
  return fields.some((field) => field.name === "handoff" && field.needs_absent !== true);
}

/** Whether a past session is of the place `pick` names: by its project for a project, by
 * its folder, however the home folder is written, for a folder. None is of a scratch
 * folder. */
export function ofPlace(past: Past, pick: Pick, home: string): boolean {
  if (pick.kind === "project") return (past.project_key ?? null) === pick.key;
  if (pick.kind === "folder") return typeof past.cwd === "string" && tilde(past.cwd, home) === tilde(pick.path, home);
  return false;
}

/**
 * The session `Continue the last session` resumes: the newest of `pasts` (newest first, as
 * `/api/sessions` lists them) that can be resumed now, is of the place the line names, and
 * was made by `actor`, the user the page server launches as. Another actor's session in the
 * same place, an agent's worker among them, is never the user's last. Null when none is,
 * so the entry is not shown.
 */
export function lastSession(pasts: readonly Past[], pick: Pick | null, actor: string, home: string): Past | null {
  if (pick === null || actor === "") return null;
  return pasts.find((past) => past.resumable && typeof past.session_id === "string" && past.session_id !== ""
    && past.actor === actor && ofPlace(past, pick, home)) ?? null;
}

/** Whether the entry says there is no session to continue: the line names a scratch folder. */
export function noneToContinue(pick: Pick | null): boolean {
  return pick !== null && pick.kind === "scratch";
}

/** The entry's dim line: what a click does with the session. */
export function continueWords(past: Past): string {
  return past.target === "slot" ? RESUMES_IN_SLOT : COPIES_RESUME;
}

/** The body of the click's `POST /api/resume`: the session, and `brief`, the box's text, as
 * its brief, or `briefFile` in its place while one is set, as a launch sends them. Each goes
 * only when it holds more than spaces, as a past session's resume on the launcher page
 * sends them. With neither, `plain` goes: Home's continue resumes the session with no
 * first turn, where the page's resume refuses a `go` session with no brief. */
export function resumeBody(past: Past, brief: string, briefFile: string): ContinueBody {
  const session = past.session_id ?? "";
  const body: ContinueBody = briefFile === "" ? pastResumeBody(session, brief, "")
    : pastResumeBody(session, "", briefFile);
  if (body.brief === undefined && body.brief_file === undefined) body.plain = true;
  return body;
}

/** The body of Home's continue: a past session's resume body, with `plain` when it sends
 * no brief. */
export interface ContinueBody extends ResumeBody {
  plain?: true;
}
