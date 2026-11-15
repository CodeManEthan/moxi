// What the page draws above one session's transcript, worked out with no DOM, no Lit and
// no request so the Node tests can read it: the way back to the list and the session's
// name on one line, and the session's facts and `Details` on the next.
//
// The name is the list's (`rowName`), so a session is called the same thing on both
// routes: a name typed for it, else its folder and when it started. The facts are the
// opened row's `Session` line. Both come from the answer an opened row reads
// (`/api/session`), asked for the route's one session beside its turns: it carries the
// session's own row, the day the server calls today and the account's home folder, and
// it does not search every session's first lines as the list's find does, so the name is
// drawn with the turns.
//
// A session the list does not hold has no such answer: a subagent, which is not a session
// of the list, and a thread or child session another one started. Its route keeps the
// viewer's own heading and shows the header's facts in the open, as the window block does.
// A session that is not there is refused the same way, and its turns say why.
//
// Every string here goes into the page as text.

import type { WindowAnswer } from "./feed.ts";
import type { SessionAnswer } from "./list-feed.ts";
import { folderWords, LOCAL, rowName, sessionPath } from "./list.ts";
import type { Zone } from "./list.ts";
import { sessionLine } from "./opened.ts";
import { headerFields, HIDDEN } from "./views.ts";
import type { FeedState, Field, Target } from "./views.ts";

/** The link back to the list. */
export const BACK = "Sessions";
export const DETAILS = "Details";
export const REFRESH = "Refresh";

/** The request for the facts of the one session `target` names: the opened row's.
 * Null for a subagent, which is not a session of the list. */
export function headPath(target: Target): string | null {
  return target.agent === null ? sessionPath(target.harness, target.session) : null;
}

/** What stands above a transcript. A null is a piece that is not drawn. */
export interface HeadView {
  /**
   * `lines`: the list holds the session, so its name and facts are drawn and the
   * header's other facts are behind `Details`. `plain`: it does not (a subagent, a
   * session another one started, a session that is not there) or its facts could not be
   * read, so the viewer's own heading and facts are drawn. `waiting`: the facts are on
   * their way, and neither is drawn yet.
   */
  mode: "waiting" | "lines" | "plain";
  /** The link back to the list: its words and the route it goes to. */
  back: { text: string; route: string };
  /** The session as the list names it. */
  name: string | null;
  /** Dim, after a typed name: the folder's last segment. */
  where: string | null;
  /** Dim, the second line: `claude · model-1 · today 07:40 to 08:55`. */
  facts: string | null;
  /** The word that shows and hides the header's other facts. */
  details: string | null;
  refresh: string;
  /** The account's home folder, for wording a folder among the details. */
  home: string | null;
}

/**
 * The header of `target`'s transcript, from the opened row's answer for that one session.
 *
 * The name is never the session's id and never a title its harness made up: it is the
 * name the list gives the row. The second line names the harness by its id and the
 * model, then from the start to the last change, each with its day; a part that was not
 * read is left out.
 */
export function headView(target: Target, feed: FeedState<SessionAnswer> | null, zone: Zone = LOCAL): HeadView {
  const answer = feed?.value ?? null;
  const view: HeadView = {
    mode: "plain",
    back: { text: BACK, route: "" },
    name: null,
    where: null,
    facts: null,
    details: null,
    refresh: REFRESH,
    home: answer?.home ?? null,
  };
  if (target.agent !== null || feed === null) return view;
  if (answer === null) {
    const none = feed.gone || feed.failure !== null || feed.refused !== null;
    return { ...view, mode: none ? "plain" : "waiting" };
  }
  if (answer.harness !== target.harness || answer.session !== target.session) return view;
  const { name, where } = rowName(answer, answer.today, zone);
  const line = sessionLine(answer, answer.today, zone);
  return {
    ...view,
    mode: "lines",
    name,
    where,
    facts: line.rest !== null ? `${line.lead} · ${line.rest}` : line.lead,
    details: DETAILS,
  };
}

/** The header's facts as `Details` lists them: the viewer's own lines, with the
 * session's folder worded against the account's home folder. */
export function detailFields(answer: WindowAnswer, home: string | null = null): Field[] {
  return headerFields(answer).map((field) =>
    (field.name === "cwd" && field.value !== HIDDEN ? { ...field, value: folderWords(field.value, home) } : field));
}
