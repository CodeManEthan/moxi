// The Comms page's words that need no row: a day and a time against the page's `today`,
// what a message wants back, a recipient's read state, and what the send box and `Take back`
// say for every answer of comms' acts and every way a request can fail to get one. Chosen by
// outcome first and by code within it; a code with no words of its own is named only in the
// one catch-all line. No DOM here; the clock is passed in, so the Node tests read the same
// words in any zone and locale. Every string here goes into the page as text.

import type { Clock } from "./mail-model.ts";
import { LOCAL, namesWords } from "./mail-model.ts";

// --- days and times -------------------------------------------------------------------------

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const DAY_MS = 86_400_000;

/** A day (`YYYY-MM-DD`) as milliseconds at its UTC midnight; NaN when it is not one. */
function stamp(day: string): number {
  return /^\d{4}-\d{2}-\d{2}$/.test(day) ? Date.parse(`${day}T00:00:00Z`) : NaN;
}

/** How many days `day` is after `from`; negative when it is before. */
function daysFrom(from: string, day: string): number {
  return Math.round((stamp(day) - stamp(from)) / DAY_MS);
}

/** A date alone: `Oct 12`; the day as given when it is not one. */
export function dateWord(day: string): string {
  const at = stamp(day);
  if (Number.isNaN(at)) return day;
  const d = new Date(at);
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

/** A day with its weekday, as `back to` names one: `Mon Oct 12`. */
export function fullDayWord(day: string): string {
  const at = stamp(day);
  return Number.isNaN(at) ? day : `${WEEKDAYS[new Date(at).getUTCDay()]} ${dateWord(day)}`;
}

/** A day against `today`, as the Usage page words it: `today`, `yesterday`, `tomorrow`, the
 * weekday within six days either side (`Wed`), else the date (`Nov 3`). */
export function dayWord(day: string, today: string): string {
  const off = daysFrom(today, day);
  if (Number.isNaN(off)) return dateWord(day);
  if (off === 0) return "today";
  if (off === -1) return "yesterday";
  if (off === 1) return "tomorrow";
  if (Math.abs(off) <= 6) return WEEKDAYS[new Date(stamp(day)).getUTCDay()];
  return dateWord(day);
}

/** A moment: the time alone on `today`'s date, else its day and the time (`yesterday 21:15`,
 * `Wed 23:58`, `Nov 3 08:52`). With no `today` held the time stands alone. */
export function whenWords(at: number, today: string | null, clock: Clock = LOCAL): string {
  const time = clock.time(at);
  if (today === null) return time;
  const day = clock.day(at);
  return day === today ? time : `${dayWord(day, today)} ${time}`;
}

/** The newer of two days the page holds; a value that is not a day is passed over. */
export function newestDay(held: string | null, read: string | null): string | null {
  const ok = (day: string | null): day is string => day !== null && !Number.isNaN(stamp(day));
  if (!ok(read)) return ok(held) ? held : null;
  if (!ok(held)) return read;
  return read > held ? read : held;
}

// --- what a message wants ---------------------------------------------------------------------

/** What a message wants back, as a thread says it, from its posture and whether it answers
 * another: `wants one reply`, `replies · wants no reply`, `replies, and asks for one reply`. A
 * posture with no words gives none; a reply with none reads `replies`. */
export function wantsWords(posture: string | null, replyTo: string | null): string | null {
  const wants: Record<string, string> = {
    none: "wants no reply",
    optional: "wants a reply only if it has something to add",
    once: "wants one reply",
    open: "wants replies",
  };
  const said = posture !== null && Object.hasOwn(wants, posture) ? wants[posture] : null;
  if (replyTo === null) return said;
  if (posture === "once") return "replies, and asks for one reply";
  return said === null ? "replies" : `replies · ${said}`;
}

/** The four postures of `Reply wanted`, in the menu's order, with the box's word and the
 * menu's sentence. */
export const POSTURES = [
  { posture: "none", label: "No reply", word: "no reply", says: "It reads this and owes nothing. A reply to it is refused." },
  { posture: "optional", label: "Only if it has something to add", word: "only if it has something to add",
    says: "Nothing is owed." },
  { posture: "once", label: "One reply", word: "one reply", says: "A single answer, which closes the exchange." },
  { posture: "open", label: "Back and forth", word: "back and forth",
    says: "Closes after 8 replies or an hour with no message." },
] as const;

/** The posture the box starts with: back and forth, or no reply when it answers a message. */
export function firstPosture(answering: boolean): "none" | "open" {
  return answering ? "none" : "open";
}

// --- a recipient's read state -----------------------------------------------------------------

/** A recipient's row in the books, as a message read sends it. */
export interface Delivery {
  qualified: string;
  state: string;
  held: boolean;
  takeable: boolean;
}

/** What the page knows of the recipient when it words its row. */
export interface Reader {
  /** A session holds the address. */
  bound: boolean;
  /** The cache module is installed. */
  hasCache: boolean;
  /** In a thread, the hold names whose turn: `held until <name>'s next turn`. */
  inThread?: boolean;
}

/** The name a line gives an address: the part before `@`. */
export const nameOf = (qualified: string): string => qualified.split("@")[0];

/**
 * A recipient's row as the page says it after the names: unread mail `not read yet`, with
 * why while it waits (`held until its next turn` when bound and cache is installed, `<name>
 * isn't running` when unbound); and a message that left the queue undelivered, by why:
 * `taken back`, `<name>'s copy expired`, and `<name> takes no mail` for one dropped when a
 * module reserved the name. A state with no words gives none.
 */
export function readWords(row: Delivery, reader: Reader): string | null {
  const name = nameOf(row.qualified);
  switch (row.state) {
    case "waiting":
      if (!reader.bound) return `not read yet, ${name} isn't running`;
      if (!reader.hasCache) return "not read yet";
      return reader.inThread ? `not read yet, held until ${name}'s next turn` : "not read yet, held until its next turn";
    case "handed":
    case "in_digest":
      return "not read yet";
    case "withdrawn":
      return "taken back";
    case "expired":
      return `${name}'s copy expired`;
    case "reserved":
      return `${name} takes no mail`;
    default:
      return null;
  }
}

// --- the acts ---------------------------------------------------------------------------------

/** One recipient of `check` or `send`. */
export interface Recipient {
  qualified: string;
  delivery: string;
  cold_at: number | null;
}

/** What `check` and `send` answer. */
export interface SendAnswer {
  outcome: string;
  message_id: string | null;
  recipients: Recipient[];
  refusal: { code: string } | null;
}

/** What `take-back` answers. */
export interface TakeAnswer {
  outcome: string;
  withdrawn: string[];
  delivered: string[];
  expired: string[];
  refusal: { code: string } | null;
}

/**
 * What a line says for an act's answer.
 *
 * - `key`: the line's label: `If sent now` for a check, `Sent <time>` for a message sent;
 *   null else.
 * - `heavy`: the lead, set heavy (`Refused: nothing would be sent.`, `Not sent.`, `Not taken
 *   back.`), or null.
 * - `text`: the words after it, in the text colour.
 * - `dim`: what follows, dim: after a send, how many now wait and that it can be taken back.
 * - `again`: `Check again` follows.
 * - `reread`: the page reads the queue again, which shows the message if it was acted on.
 * - `keep`: the box keeps its text.
 */
export interface Said {
  key: string | null;
  heavy: string | null;
  text: string;
  dim: string | null;
  again: boolean;
  reread: boolean;
  keep: boolean;
}

const IF_SENT = "If sent now";
const CREDENTIAL = "Comms can't find your credential. moxi doctor comms says why.";
const VERSIONS = "This page and the comms daemon are different versions. moxi doctor comms says what to restart.";
const OFFLINE = "The comms daemon isn't answering, or asked to be tried again.";
const IN_SESSION = "This page's server runs inside an agent's session, so it can't send as you. Start Moxi's shell from your own terminal.";
const TAKE_IN_SESSION = "This page's server runs inside an agent's session, so it can't take messages back as you. Start Moxi's shell from your own terminal.";

/** The catch-all for a refusal with no words of its own, naming its code. */
const refusedWords = (code: string | null): string => (code === null ? "Comms refused it." : `Comms refused it (${code}).`);

/** How a would-send or a sent message reaches one recipient, by its delivery; null for a
 * delivery with no words. `working` is true when turns says the recipient's session has a
 * turn open now, so a live message waits for that turn to end. */
export function deliveryWords(recipient: Recipient, when: (at: number) => string, working = false): string | null {
  const name = nameOf(recipient.qualified);
  switch (recipient.delivery) {
    case "live":
      return working ? `Delivered. ${name} is working; it reads this when its turn ends.`
        : `Delivered. ${name} reads it at its next turn boundary.`;
    case "held-cold": {
      const at = recipient.cold_at === null ? "" : ` at ${when(recipient.cold_at)}`;
      return `Held until ${name}'s next turn. Its cache went cold${at}, and nothing wakes it for mail. `
        + "It reads this when you next type into it, or at a session start, /clear or compaction.";
    }
    case "held-flag":
      return `Held until ${name}'s next turn, as you chose.`;
    case "waiting":
      return `Held until ${name}'s session can take mail.`;
    case "unbound":
      return `${name} isn't running. It waits until a session takes the name.`;
    default:
      return null;
  }
}

/** The deliveries that hold a message for later. */
const HELD = new Set(["held-cold", "held-flag", "waiting", "unbound"]);

const said = (heavy: string | null, text: string, flags: Partial<Omit<Said, "heavy" | "text">> = {}): Said =>
  ({ key: null, heavy, text, dim: null, again: false, reread: false, keep: false, ...flags });

/** The words for an answer of `check` or `send` that is not `would-send` or `sent`. A
 * refusal carries no recipient, so the name is the address the box writes to, `to`. */
function notSentWords(act: "check" | "send", answer: SendAnswer, to: string | null): Said {
  const check = act === "check";
  const lead = check ? "Refused: nothing would be sent." : "Not sent.";
  const flags = { key: check ? IF_SENT : null, again: check, keep: true };
  const code = answer.refusal?.code ?? null;
  const qualified = answer.recipients[0]?.qualified ?? to;
  const name = qualified !== null ? nameOf(qualified) : "the recipient";
  switch (answer.outcome) {
    case "cache-unread":
      return said(lead, `Comms can't read ${name}'s cache right now, so it can't tell whether this would wake it cold. `
        + "Check again in a moment.", flags);
    case "blocked":
      return said(lead, "Comms' settings block this send.", flags);
    case "offline":
      return said(lead, `${OFFLINE} Check again in a moment.`, flags);
    case "unknown":
      return check ? said(lead, "Comms didn't answer in time. Check again.", flags)
        : said(null, "It may have been sent.", { reread: true, keep: true });
    case "failed":
      if (code === "credential") return said(lead, CREDENTIAL, flags);
      if (code === "protocol-mismatch") return said(lead, VERSIONS, flags);
      return said(lead, `Comms couldn't do it (${code ?? answer.outcome}).`, flags);
    case "refused":
      switch (code) {
        case "too-long":
          return said(lead, "With its address line it is over the 16,000-character cap on one message.", flags);
        case "operator-in-session":
        case "operator-and-claim":
          return said(lead, IN_SESSION, flags);
        case "credential":
        case "operator-unknown":
          return said(lead, CREDENTIAL, flags);
        case "send-only":
          return said(lead, `${name} takes no mail.`, flags);
        default:
          return said(lead, refusedWords(code), flags);
      }
    default:
      return said(lead, `Comms couldn't do it (${answer.outcome}).`, flags);
  }
}

/** The dry-run line after `If sent now`, for an answer of `check` to the address `to`.
 * `working` says whether an address's session has a turn open now, as turns read it. */
export function checkWords(answer: SendAnswer, when: (at: number) => string, to: string | null = null,
                           working: (qualified: string) => boolean = () => false): Said {
  if (answer.outcome !== "would-send") return notSentWords("check", answer, to);
  const phrases = answer.recipients.map((recipient) => deliveryWords(recipient, when, working(recipient.qualified)));
  return said(null, phrases.filter((phrase): phrase is string => phrase !== null).join(" "), { key: IF_SENT });
}

/**
 * The line after Send's answer. `sent` gives `Sent <time>` as the lead and the delivery in
 * the table's words; when it was held, `<n> messages now wait for it`, `waiting` being the
 * address's queue read after the answer (absent until it is read); and that a message can be
 * taken back until the recipient has read it. The box empties. Anything else keeps the text.
 */
export function sendWords(answer: SendAnswer, sentAt: number, when: (at: number) => string,
                          waiting: number | null = null, to: string | null = null): Said {
  if (answer.outcome !== "sent") return notSentWords("send", answer, to);
  const text = answer.recipients.map((recipient) => deliveryWords(recipient, when))
    .filter((phrase): phrase is string => phrase !== null).join(" ");
  const dim: string[] = [];
  if (waiting !== null && waiting > 0 && answer.recipients.some((recipient) => HELD.has(recipient.delivery))) {
    dim.push(waiting === 1 ? "1 message now waits for it." : `${waiting} messages now wait for it.`);
  }
  const names = answer.recipients.map((recipient) => nameOf(recipient.qualified));
  if (names.length > 0) dim.push(`You can take a message back until ${namesWords(names)} has read it.`);
  return said(null, text, { key: `Sent ${when(sentAt)}`, dim: dim.length > 0 ? dim.join(" ") : null });
}

/** The line after `Take back`'s answer. */
export function takeBackWords(answer: TakeAnswer): Said {
  const lead = "Not taken back.";
  const names = (qualified: string[]): string => namesWords(qualified.map(nameOf));
  const code = answer.refusal?.code ?? null;
  switch (answer.outcome) {
    case "deleted": {
      if (answer.delivered.length === 0 && answer.expired.length === 0) return said(null, "Taken back.");
      const parts: string[] = [];
      if (answer.withdrawn.length > 0) parts.push(`Taken back from ${names(answer.withdrawn)}.`);
      if (answer.delivered.length > 0) parts.push(`${names(answer.delivered)} already had it.`);
      if (answer.expired.length > 0) parts.push(`${names(answer.expired)}'s copy had expired.`);
      return said(null, parts.join(" "));
    }
    case "refused":
      switch (code) {
        case "already-delivered":
          if (answer.delivered.length > 0) return said(null, `${names(answer.delivered)} already had it.`);
          return said(lead, refusedWords(code));
        case "not-sender":
          return said(lead, "It isn't your message.");
        case "unknown-message":
          return said(lead, "Comms doesn't hold that message any more.");
        case "no-identity":
          return said(lead, TAKE_IN_SESSION);
        default:
          return said(lead, refusedWords(code));
      }
    case "offline":
      return said(lead, OFFLINE);
    case "unknown":
      return said(null, "Comms didn't answer; it may have been taken back.", { reread: true });
    case "failed":
      if (code === "credential") return said(lead, CREDENTIAL);
      if (code === "protocol-mismatch") return said(lead, VERSIONS);
      return said(lead, `Comms couldn't do it (${code ?? answer.outcome}).`);
    default:
      return said(lead, `Comms couldn't do it (${answer.outcome}).`);
  }
}

/**
 * The line when the request itself got no readable answer from the page server: status 0
 * (no answer from the shell), 502 or 504 (the page server didn't answer, and may have acted),
 * 400, or any other status, a 200 whose body is not the act's among them. Send and `Take back`
 * never read a 502 or 504 as a refusal: the queue is read again.
 */
export function transportWords(act: "check" | "send" | "take-back", status: number): Said {
  const maybe = act === "send" ? "It may have been sent." : "It may have been taken back.";
  if (status === 0) {
    return act === "check" ? said(null, "No answer from the shell. Check again.", { key: IF_SENT, again: true, keep: true })
      : said(null, maybe, { reread: true, keep: act === "send" });
  }
  if (status === 502 || status === 504) {
    return act === "check"
      ? said(null, "Comms' page server didn't answer. Check again.", { key: IF_SENT, again: true, keep: true })
      : said(null, maybe, { reread: true, keep: act === "send" });
  }
  const text = status === 400 ? "The shell refused the request (400)." : `The shell answered ${status}.`;
  return said(null, text, { key: act === "check" ? IF_SENT : null, again: act === "check", keep: act !== "take-back" });
}
