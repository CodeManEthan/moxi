// moxi-turns, the runner: turns' Claude Code adapter in the session.
//
// It spawns the session's endpoint, reports the session's main-loop turns to it, puts in
// what it hands over (a submit, a steer, a /clear, /exit or /compact), and asks it at
// SessionStart, UserPromptSubmit and Stop. The runner reads no setting: its bounds are the
// constants below, and the endpoint's first line carries `stop_wait_ms` and
// `listen_wait_ms`.
//
// The endpoint command: `MOXI_TURNS_ENDPOINT` in the session's environment, which a launch
// through turns' launch additions sets; else the one `endpoint.json` beside this plugin
// names, which turns' setup writes for a user-scope install. A variable that is set but
// not an absolute path is no endpoint, and the file is not read then. A read of the
// variable that throws counts as unset, so the file is read.
//
// The internal leg (not the socket contract; it changes only with the package). The
// endpoint's half, moxi_turns.claude_leg, states it in full; the runner's part:
//
// Endpoint to runner: one JSON object per line on the child's stdout, by `op`:
//   hello    {op, leg: 3, mod_sock, stop_wait_ms, listen_wait_ms, endpoint_version, pid,
//            stop_after_clear, guards}
//            the first line. Before it the runner sends nothing, and it sends nothing to an
//            endpoint whose leg it does not speak. `stop_after_clear` false: after a clear
//            the runner stops no shell (each listed one is left, `not-stopped-after-clear`).
//            `guards` is the guard table (below).
//   guards   {op, table}   the guard table again, at each ready; it replaces the one held
//   failed   {op, leg, error}   the endpoint could not start: its only line
//   submit   {op, delivery, kind, text, nonce}
//            opens the submit's window and calls $.prompt.submit with the line
//            `moxi-turns-window: <nonce>` first (entry, below); one window at a time
//   append   {op, delivery, text}             a steer: $.session.append, a user row
//   live     {op, ask}                        the live facts: answered on POST /live
//   agents   {op, ask}                        $.agent.list(): answered on POST /agents
//   command  {op, request, name: clear|exit|compact, stop_shells}
//            $.command.run, which the gate re-checks; with stop_shells, the shell stop
//            (below) around it
//
// Runner to endpoint: HTTP POST with a JSON body to `mod_sock`, answered with a JSON object.
//   /report  {seq, kind, ...} through one ordered queue, each sent once the one before it
//            landed; `seq` counts from 1 per runner load, in the order sent. Kinds:
//              session        {session_id, mod_version}  first, once the hello came
//              turn.start     {turn_id}
//              turn.complete  {turn_id, reason, is_aborted}
//              submit.close   {delivery, end, reason}  the submit settled: end is resolve,
//                             drop (reason: the gate's code, else `dropped`; `unsupported`
//                             when the gate is not loaded and nothing was submitted) or
//                             fail (reason: words). Sent whenever it settles, after a
//                             bound's report too.
//              submit.bound   {delivery, reason}  the runner's bound (below)
//              append.done    {delivery}  $.session.append resolved without a deny
//              append.failed  {delivery, reason}
//              command.done   {request, state: ran|refused|failed, reason, new_session_id,
//                             shells?}  with stop_shells a `ran` carries
//                             shells: {stopped: [id], left: [{id, why}], complete}
//   /ask/context {event: ready|prompt, source, session_id}  -> {context: string | null}
//   /ask/stop    {turn_id, session_id, stop_hook_active, task_types, crons}
//                                                          -> {reason: string | null}
//   /live        {ask, agents, draft}                      -> {}  (agents null: unreadable)
//   /agents      {ask, entries}                            -> {}  each entry as the engine
//                gives it, {id, type, description, status, parentId?, teammateId?}; the
//                endpoint maps the states (entries null: unreadable)
//   /ask/guard   {call: {tool, tool_use_id, input, agentId, prompts?}, guards: [id]}
//                -> {deny?, lines, broken}   a call judge entries cover (the guards, below)
//   /hook-times  {samples: [{event, ms}]}                   -> {}  at most once a minute
//   The gate asks on the same socket: /gate/entry {nonce, draft, agents} and
//   /gate/command {request, agents, draft} -> {verdict: pass} | {verdict: drop, reason,
//   text}; `draft` and `agents` are the gate's readings.
//
// Hook times: each hook handler times its own work, from entry to return less the time
// spent in `next` (the hooks behind it, and a tool's own run), and the samples go to
// /hook-times once a minute, outside the report queue. The doctor reports them.
//
// The internal leg rule: no hook waits on the endpoint longer than a bound this plugin's
// own timer keeps, and a request that misses its bound counts as unanswered. A reporting
// hook waits at most 1 s for its report; the Stop ask stop_wait_ms + 1 s; a context ask
// listen_wait_ms + 1 s; SessionStart waits at most 2 s for the first line.
//
// The queue: a report made before the first line waits in the queue and is sent after the
// session report once the line comes; one whose hook stopped waiting (the 1 s) stays queued
// and is still sent, in order. A send that fails while the endpoint lives is tried again
// (5 tries, 100 ms apart, the same `seq`), then dropped and logged so the reports behind it
// still go; the endpoint reads the gap in `seq` and fails an open window rather than tie it. When
// the endpoint has ended the queue is dropped.
//
// The tie rule: each plugin submit has one window, which closes exactly once, at the
// first end the endpoint accepts: resolve (the submit resolved with its prompt entered),
// drop (it resolved `{ drop }`), fail (it rejected, or is still unsettled at the runner's
// bound while its prompt is not admitted, or the endpoint ended). The runner reports each
// main-loop turn.start inside its hook before the submit can resolve, and the settle after
// it, through the one queue; the endpoint ties. The endpoint alone decides the close, so
// the runner reports and decides nothing: a new `submit` line means the endpoint closed the
// last window, and the last submit, if still pending, reports its settle when it comes.
// The bound: a submit still pending after 5 s with no main-loop turn open is reported once
// (`submit.bound`). The time counts only while no turn is open: from the call when idle,
// stopped at each turn.start, started again from zero at each turn.complete. While turns'
// own entry hook for the prompt runs (UserPromptSubmit, which asks the listeners), the
// bound is raised by that ask's own bound.
//
// The entry rule: a runner prompt enters only as the prompt of the window now open,
// admitted once by the endpoint. The runner puts the window nonce on the prompt's first
// line and keeps the endpoint's mod.sock path in $.state (`endpoint`), written at the
// first line, at every SessionStart (a /clear empties $.state) and before each submit. The
// gate (moxi-turns-gate) asks the endpoint for every runner prompt, takes the nonce line
// off on a pass and drops everything else. The gate is required: without it in
// $.command.list() the runner submits nothing, and the submit closes as `unsupported`.
//
// The shell stop (a command's stop_shells): the runner stops the session's Bash background
// shells and Monitors, never subagents, teammates or workflows. The mod API has no list
// call for background tasks, so the runner keeps the latest `background_tasks` from every
// classic.Stop and classic.SubagentStop input it sees (an agent's and a headless one's
// too), the entries of type `shell` or `monitor`. Before the run it asks
// `$.tool.check` for a `TaskStop` of each; any answer but `allow` reports `refused`
// `unsupported` and nothing runs. Once `$.command.run` has returned without the gate's
// withdrawal, it calls `TaskStop` for each within 10 s and reports what it stopped and
// what it left (`timeout`, `denied`, or the harness's words); an entry the call says had
// already ended goes in neither list.
//
// The list rule: the runner vouches for its list only when nothing that could start a
// shell has run since the harness gave it. A shell or Monitor starts from a tool call, or
// from a command the person runs with `!` and moves to the background. A `tool.call` hook
// keeps the calls in flight across every loop, by `tool_use_id`, and marks the list at
// each call's start and end. A `!` command raises no tool call, only its rows
// (`session.append`, door `command`), so every such row marks the list too, but for the
// rows of the runner's own command run, unless one is a `!` command's. A Stop or
// SubagentStop carrying `background_tasks`, seen with no call in flight, replaces the list
// and clears the mark. A reload starts with no list and the mark set. The mark is read as
// the stop begins, after the command took effect, and the runner's own `TaskStop` calls
// set nothing. With the mark set the report has `complete` false and the one unnamed entry
// `{id: null, why: "unlisted"}`, so a list turns cannot vouch for never reads as empty.
// A turn's abort drops the main loop's calls from the count (their tools are stopped);
// a call whose hook never returns keeps the mark set, which errs to `complete` false.
//
// The non-interactive rule: `$.session.surfaces()` empty means headless, read in
// whichever of session.start and classic.SessionStart runs first; the first read decides.
// A headless run spawns no endpoint and every hook returns at once.
//
// The guards: the runner matches every tool call against the table the endpoint sent, in
// its own process, and only a call a judge entry covers leaves it. An entry is
//   {id, calls: [pattern], who: any|main|agents, match: {field: regex} | null,
//    match_flags: ""|"i", judge, deny, wants: ["prompts"]?, on_error: allow|deny,
//    deadline_ms}
// An entry covers a call when a pattern matches the whole tool name (`*` any run of
// characters, every other character only itself), `who` takes the loop (an absent or empty
// `agentId` is the main loop), and every `match` regex finds its input field, which must be
// a string. The regexes compile with no flags but `i`, never `u`. The engines differ on a
// few points no shared case can hold: JavaScript's `.` also stops at `\r`, U+2028 and
// U+2029, its `\s` takes Unicode spaces, and its `$` does not match before a final newline.
// For each call, in table order:
//   1. nothing covers it: `next(e)` at once, with no trip;
//   2. a data-only entry (no judge) covers it: refused with its `deny`, and `next` is
//      never called;
//   3. else one POST /ask/guard naming the covering judge entries, bounded by the largest
//      of their `deadline_ms` plus 1 s. A `deny` in the answer refuses; else the tool runs
//      and `lines`, then `broken`, go into the result's `context`, which the model reads
//      after the tool's result.
//   A trip that fails or misses its bound breaks each entry it named: the call is refused
//   with "turns could not check this call with guard <id> (<why>)" when one of them
//   declared `on_error` deny, else it runs with one line per entry, "[turns] guard <id>
//   could not check this call (<why>); it ran unchecked.". An entry whose `match` does not
//   compile here breaks each call its patterns and `who` take, the same way.
// The trip is timed as the hook-time event `guard`, and the tool.call event's own time
// leaves it out. The runner's own tool calls (its TaskStop) are never matched. The hook's
// catch makes no trip, since it gets 1 s: it refuses "turns could not check this call"
// when `next` was not called and a covering entry declared `on_error` deny, else it
// passes the call on.
//
// The prompt list: the prompts of the main turn now running, sent on an ask only when a
// named entry `wants` prompts. A prompt counts in a main turn only when the runner can tie
// it to that turn; one it can't tie counts in no turn, so a list comes out short, never
// long, and a guard reading it fails closed. The `prompt.submit` hook records each prompt
// (not one with `isMeta`) as its origin's kind, its text and its `turnId`, in a waiting set,
// then calls `next(e)`:
//   1. a prompt whose `turnId` is the running main turn's also joins that turn's list;
//   2. when `next(e)` resolves with the prompt entered, one with a `turnId` takes the text
//      that entered (a hook beneath may have rewritten it) and keeps waiting, since typed
//      during a turn's last model step it opens the next turn; one without leaves the
//      waiting set, since any turn it opened started inside its `next(e)`. A drop or a
//      rejection takes the prompt out of the waiting set and the list;
//   3. at each main `turn.start` the list becomes the waiting prompt whose text equals the
//      turn's (two such: the one whose `next(e)` is still in flight, then the latest);
//      else the latest waiting prompt without a `turnId`, all of which are in flight; else
//      nothing. While one of the runner's own submits is in flight, only a text match ties,
//      since the turn may be the runner's own, whose prompt this hook never sees. Then the
//      waiting set is emptied;
//   4. a subagent's or teammate's turn touches none of it.
// The list is never cleared at a turn's end; the next main `turn.start` replaces it.

import type { EngineInterface, Register } from 'claude-code'

type Dollar = EngineInterface

const MOD_VERSION = '0.1.0'
const GATE = 'moxi-turns-gate'
const MARGIN_MS = 1000
const REPORT_WAIT_MS = 1000
const HELLO_WAIT_MS = 2000
const IDLE_FAIL_MS = 5000
const SEND_TRIES = 5
const SEND_RETRY_MS = 100
const QUEUE_MAX = 1000
const BUSY_AGENT = new Set(['pending', 'running', 'waiting'])
const GATE_DROP = /^moxi-turns-gate: .*\(([a-z-]+)\)$/
const NONCE_LINE = 'moxi-turns-window: '
const LEG = 3
const PLUGIN = 'moxi-turns'
/** A guard's bound when its entry names none. */
const GUARD_DEADLINE_MS = 500
/** The catch's refusal: it decides at once and names no guard. */
const CANNOT_CHECK = 'turns could not check this call'
/** The keys of a `tool.call` envelope that are no argument of the tool. */
const NOT_INPUT = new Set(['tool', 'tool_use_id', 'agentId', 'requestMeta', 'consent'])
const SHELL_STOP_MS = 10_000
const STOPPABLE = new Set(['shell', 'monitor'])
const UNLISTED = 'unlisted'
/** A row of a command the person ran with `!`: its input or its output. */
const BASH_ROW = /<bash-(?:input|stdout|stderr)>/
const NOT_STOPPED_AFTER_CLEAR = 'not-stopped-after-clear'
/** What TaskStop says of a task that has already ended. */
const ENDED = /not running|no longer running|already (?:stopped|completed|finished|exited|ended|killed)|has (?:already )?(?:completed|finished|exited|ended)/i
const HOOK_TIMES_MS = 60_000
const HOOK_TIMES_MAX = 1000

const ENDPOINT = { plugin: 'moxi-turns', key: 'endpoint' } as const
const COMMAND = { plugin: 'moxi-turns', key: 'command' } as const

type Hello = { sock: string; stopWaitMs: number; listenWaitMs: number; stopAfterClear: boolean }
type Json = Record<string, unknown>
type Report = { kind: string; fields: Json; seq: number; landed: Promise<void>; land: () => void }
/** One entry of the guard table, ready to match. */
export type Guard = {
  id: string
  /** The tool-name patterns, each anchored at both ends. */
  calls: RegExp[]
  who: 'any' | 'main' | 'agents'
  /** Each input field and the regex that must find it. */
  match: [string, RegExp][]
  judge: boolean
  deny: string
  wantsPrompts: boolean
  onError: 'allow' | 'deny'
  deadlineMs: number
  /** Why the entry can't be matched here (a `match` regex that does not compile), or null. */
  bad: string | null
}
/** A prompt the `prompt.submit` hook saw. */
type Seen = {
  origin: string | null
  text: string
  turnId: string | null
  /** Its `next(e)` has not settled. */
  inFlight: boolean
}
type Win = {
  delivery: string
  /** The submit settled and its settle was reported (or the endpoint ended). */
  closed: boolean
  /** The bound was reported; it is reported once. */
  bounded: boolean
  timer: { cancel: () => void } | null
}

// Module state: a reload starts it over, with a new endpoint.
let headless: boolean | undefined
let spawned = false
let hello: Hello | undefined
let ended = false
let helloSeen: () => void = () => {}
let helloP = new Promise<void>(resolve => { helloSeen = resolve })
let turnOpen: string | null = null
let win: Win | null = null
let seq = 0
let queue: Report[] = []
let sending = false
let samples: { event: string; ms: number }[] = []
let samplesArmed = false
/** The latest background shells and Monitors the harness listed, or null before any. */
let tasks: { id: string; type: string }[] | null = null
/** Set while the list cannot be vouched for: a tool call ran since the harness gave it. */
let tasksMarked = true
/** The tool calls in flight, by `tool_use_id`, with the loop each runs in. */
const inflight = new Map<string, string | undefined>()
/** The task ids the runner's own `TaskStop` calls are stopping now. */
const stopping = new Set<string>()
/** The runner's own commands now running, each until its stop has read the mark. */
let ownCommands = 0
/** The guard table the endpoint sent last, in its order. */
let guards: Guard[] = []
/** The prompts waiting for a main turn, in the order seen. */
let waiting: Seen[] = []
/** The prompts of the main turn now running (or of the last one, until the next starts). */
let turnPrompts: Seen[] = []
/** The runner's own `$.prompt.submit` calls not yet settled. */
let ownSubmits = 0

const log = ($: Dollar, text: string): void => {
  try {
    $.ui.log(`moxi-turns: ${text}`, { to: 'debug' })
  } catch {
    // nowhere to say it
  }
}

/** A promise that resolves undefined after `ms`, on the plugin's own timer. */
function after<T>($: Dollar, ms: number): Promise<T | undefined> {
  return new Promise(resolve => { $.clock.after(ms, () => resolve(undefined)) })
}

async function post($: Dollar, sock: string, path: string, body: Json): Promise<Json> {
  const r = await $.http.fetch(`http://moxi-turns${path}`, {
    method: 'POST',
    socketPath: sock,
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  const v = r.text ? JSON.parse(r.text) : {}
  return v !== null && typeof v === 'object' ? (v as Json) : {}
}

/** An ask: the answer, or undefined when there is no endpoint or it missed `boundMs`. */
async function ask($: Dollar, path: string, body: Json, boundMs: number): Promise<Json | undefined> {
  if (hello === undefined || ended) return undefined
  const sent = post($, hello.sock, path, body).catch(err => {
    log($, `${path} failed: ${String(err)}`)
    return undefined
  })
  return Promise.race([sent, after<Json>($, boundMs)])
}

// -- hook times ----------------------------------------------------------------------------

/** Keeps one hook's time and sends the samples a minute after the first of a batch. */
function sample($: Dollar, event: string, ms: number): void {
  if (ended || headless === true || !Number.isFinite(ms) || ms < 0) return
  if (samples.length >= HOOK_TIMES_MAX) samples.shift()
  samples.push({ event, ms: Math.round(ms * 1000) / 1000 })
  if (samplesArmed) return
  samplesArmed = true
  $.clock.after(HOOK_TIMES_MS, () => {
    samplesArmed = false
    const batch = samples
    samples = []
    if (hello === undefined || ended || batch.length === 0) return
    post($, hello.sock, '/hook-times', { samples: batch }).catch(err => log($, `hook times not sent: ${String(err)}`))
  })
}

/** A hook's watch: its own time is entry to return, less the time spent in `next`. */
type Watch = { start: number; away: number }
const watch = (): Watch => ({ start: performance.now(), away: 0 })

/** `value`, counting the time since `since` as spent behind the hook (in `next`). */
function behind<T>(w: Watch, since: number, value: T): T {
  w.away += performance.now() - since
  return value
}

function timeHook($: Dollar, event: string, w: Watch): void {
  sample($, event, performance.now() - w.start - w.away)
}

// -- the report queue -------------------------------------------------------------------

function report($: Dollar, kind: string, fields: Json): Promise<void> {
  let land: () => void = () => {}
  const landed = new Promise<void>(resolve => { land = resolve })
  if (ended) return Promise.resolve()
  if (queue.length >= QUEUE_MAX) {
    log($, `report queue full; ${kind} dropped`)
    return Promise.resolve()
  }
  queue.push({ kind, fields, seq: 0, landed, land })
  void pump($)
  return landed
}

/** Reports and waits for it to land, at most 1 s; none of that before the first line. */
async function reportAndWait($: Dollar, kind: string, fields: Json): Promise<void> {
  const landed = report($, kind, fields)
  if (hello === undefined) return
  await Promise.race([landed, after($, REPORT_WAIT_MS)])
}

async function pump($: Dollar): Promise<void> {
  if (sending || hello === undefined) return
  sending = true
  try {
    while (queue.length > 0 && !ended && hello !== undefined) {
      const item = queue[0]
      if (item === undefined) break
      let tries = 0
      if (item.seq === 0) {
        seq += 1
        item.seq = seq
      }
      for (;;) {
        try {
          await post($, hello.sock, '/report', { seq: item.seq, kind: item.kind, ...item.fields })
          break
        } catch (err) {
          tries += 1
          if (ended) break
          if (tries >= SEND_TRIES) {
            log($, `report ${item.kind} dropped after ${tries} tries: ${String(err)}`)
            break
          }
          await after($, SEND_RETRY_MS)
        }
      }
      queue.shift()
      item.land()
    }
  } finally {
    sending = false
  }
  if (ended) dropQueue()
}

function dropQueue(): void {
  for (const item of queue) item.land()
  queue = []
}

// -- the window (the tie) -----------------------------------------------------------------

function arm($: Dollar, w: Win, ms: number): void {
  w.timer?.cancel()
  w.timer = null
  if (w.closed || w.bounded || win !== w || turnOpen !== null) return
  w.timer = $.clock.after(ms, () => {
    if (w.closed || w.bounded || win !== w || turnOpen !== null) return
    w.bounded = true
    w.timer = null
    // A report, not a close: the endpoint fails the window by it only while its prompt
    // is not admitted. The submit's settle is still reported when it comes.
    void report($, 'submit.bound', { delivery: w.delivery, reason: `unsettled after ${IDLE_FAIL_MS} ms with no turn open` })
  })
}

function stopTimer(w: Win | null): void {
  w?.timer?.cancel()
  if (w) w.timer = null
}

function settle($: Dollar, w: Win, end: 'resolve' | 'drop' | 'fail', reason: string | null): void {
  if (w.closed) return
  w.closed = true
  stopTimer(w)
  if (win === w) win = null
  void report($, 'submit.close', { delivery: w.delivery, end, reason })
}

/** The refusal reason a drop names: the gate's code, else `dropped`. */
function dropReason(text: string): string {
  const m = GATE_DROP.exec(text)
  return m?.[1] ?? 'dropped'
}

async function gateLoaded($: Dollar): Promise<boolean> {
  try {
    return (await $.command.list()).some(c => c.name === GATE)
  } catch {
    return false
  }
}

/** The endpoint's mod.sock path, kept in $.state for the gate. */
async function writeSock($: Dollar): Promise<void> {
  if (hello === undefined || ended) return
  await $.state.set(ENDPOINT, { sock: hello.sock })
}

async function openWindow($: Dollar, delivery: string, text: string, nonce: string): Promise<void> {
  // A new submit means the endpoint closed the last window. If that submit is still
  // pending it reports its settle when it comes; its bound no longer counts.
  if (win !== null) {
    stopTimer(win)
    win = null
  }
  if (!(await gateLoaded($))) {
    void report($, 'submit.close', { delivery, end: 'drop', reason: 'unsupported' })
    return
  }
  try {
    await writeSock($)
  } catch (err) {
    void report($, 'submit.close', { delivery, end: 'fail', reason: `the mod.sock path was not written: ${String(err)}` })
    return
  }
  const w: Win = { delivery, closed: false, bounded: false, timer: null }
  win = w
  arm($, w, IDLE_FAIL_MS)
  let submitted: ReturnType<Dollar['prompt']['submit']>
  // Counted in flight until the call settles, whatever the window does: a bound or the
  // endpoint's end closes the window while the call still runs.
  let counted = true
  const uncount = (): void => {
    if (counted) {
      counted = false
      ownSubmits -= 1
    }
  }
  ownSubmits += 1
  try {
    submitted = $.prompt.submit({ text: `${NONCE_LINE}${nonce}\n${text}` })
  } catch (err) {
    uncount()
    settle($, w, 'fail', `rejected: ${String(err)}`)
    return
  }
  submitted.then(
    r => {
      uncount()
      if (w.closed) return
      if (w.bounded) log($, `submit ${delivery} settled after its bound`)
      if (typeof r.drop === 'string') settle($, w, 'drop', dropReason(r.drop))
      else settle($, w, 'resolve', null)
    },
    err => {
      uncount()
      if (!w.closed) settle($, w, 'fail', `rejected: ${String(err)}`)
    },
  )
}

// -- the endpoint's lines -------------------------------------------------------------------

const str = (v: unknown): string | undefined => (typeof v === 'string' ? v : undefined)
const num = (v: unknown, d: number): number => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : d)

async function live($: Dollar, askId: unknown): Promise<void> {
  // An unreadable subagent list is unknown, sent as null, which the endpoint counts as
  // busy: never as 0. An unreadable box counts as a draft.
  let agents: number | null = null
  let draft = true
  try {
    agents = (await $.agent.list()).filter(a => BUSY_AGENT.has(String(a.status))).length
  } catch {
    agents = null
  }
  try {
    draft = (await $.prompt.read()).text !== ''
  } catch {
    draft = true // an unreadable box counts as a draft
  }
  await ask($, '/live', { ask: askId, agents, draft }, REPORT_WAIT_MS)
}

async function agentList($: Dollar, askId: unknown): Promise<void> {
  // The list as the engine records it, read on this ask, unmapped: the endpoint maps the
  // states. An unreadable list is sent as null, which the endpoint reads as not complete.
  let entries: Record<string, unknown>[] | null = null
  try {
    entries = (await $.agent.list()).map(a => ({
      id: a.id,
      type: a.type,
      description: a.description,
      status: a.status,
      ...(a.parentId !== undefined ? { parentId: a.parentId } : {}),
      ...(a.teammateId !== undefined ? { teammateId: a.teammateId } : {}),
    }))
  } catch {
    entries = null
  }
  await ask($, '/agents', { ask: askId, entries }, REPORT_WAIT_MS)
}

async function append($: Dollar, delivery: string, text: string): Promise<void> {
  try {
    const r = await $.session.append({ message: { type: 'user', content: [{ type: 'text', text }] } })
    const deny = (r as { deny?: string }).deny
    if (deny !== undefined) void report($, 'append.failed', { delivery, reason: deny })
    else void report($, 'append.done', { delivery })
  } catch (err) {
    void report($, 'append.failed', { delivery, reason: String(err) })
  }
}

type Name = 'clear' | 'exit' | 'compact'
type Left = { id: string | null; why: string }
type ShellReport = { stopped: string[]; left: Left[]; complete: boolean }

/** A Stop or SubagentStop input's `background_tasks`: with no call in flight it replaces
 *  the list and clears the mark; else it changes nothing. */
function noteTasks(list: unknown): void {
  if (!Array.isArray(list) || inflight.size > 0) return
  tasks = list
    .filter(t => t !== null && typeof t === 'object' && typeof (t as Json).id === 'string' && STOPPABLE.has(String((t as Json).type)))
    .map(t => ({ id: (t as Json).id as string, type: String((t as Json).type) }))
  tasksMarked = false
}

/** Step 2: may every listed shell be stopped? `$.tool.check` must answer `allow` for each.
 *  No list, or an empty one, is no refusal. */
async function mayStop($: Dollar): Promise<boolean> {
  const list = tasks ?? []
  const checks = Promise.all(list.map(async t => {
    try {
      return (await $.tool.check({ tool: 'TaskStop', input: { task_id: t.id } })).decision === 'allow'
    } catch (err) {
      log($, `the TaskStop check for ${t.id} failed: ${String(err)}`)
      return false
    }
  }))
  const verdicts = await Promise.race([checks, after<boolean[]>($, SHELL_STOP_MS)])
  return verdicts !== undefined && verdicts.every(v => v)
}

/** Stops one task through `TaskStop`, by `deadline` at the latest: stopped, left with why,
 *  or null when the call says it had already ended. */
async function stopOne($: Dollar, id: string, deadline: Promise<undefined>): Promise<{ stopped: string } | { left: Left } | null> {
  stopping.add(id)
  const call = $.tool.call({ tool: 'TaskStop', task_id: id }).then(
    r => ({ r: r as { deny?: string; isError?: boolean; text?: string } }),
    err => ({ err }),
  )
  try {
    const x = await Promise.race([call, deadline])
    if (x === undefined) return { left: { id, why: 'timeout' } }
    if ('err' in x) return { left: { id, why: String(x.err) } }
    const r = x.r
    if (typeof r.deny === 'string') return { left: { id, why: 'denied' } }
    if (r.isError === true) {
      const text = typeof r.text === 'string' && r.text !== '' ? r.text : 'TaskStop failed'
      return ENDED.test(text) ? null : { left: { id, why: text } }
    }
    return { stopped: id }
  } finally {
    // A call still out at the deadline keeps its id until it returns.
    void call.finally(() => stopping.delete(id))
  }
}

/** Step 4: stops what the list names, once the command has taken effect. The mark is read
 *  here, as the stop begins. */
async function stopShells($: Dollar, name: Name): Promise<ShellReport> {
  const complete = tasks !== null && !tasksMarked
  const list = tasks ?? []
  const report: ShellReport = { stopped: [], left: [], complete }
  if (name === 'clear' && hello?.stopAfterClear === false) {
    for (const t of list) report.left.push({ id: t.id, why: NOT_STOPPED_AFTER_CLEAR })
  } else {
    const deadline = after<undefined>($, SHELL_STOP_MS) as Promise<undefined>
    for (const x of await Promise.all(list.map(t => stopOne($, t.id, deadline)))) {
      if (x === null) continue
      if ('stopped' in x) report.stopped.push(x.stopped)
      else report.left.push(x.left)
    }
  }
  if (!complete) report.left.push({ id: null, why: UNLISTED })
  return report
}

async function command($: Dollar, request: string, name: Name, stopShellsToo: boolean): Promise<void> {
  const done = (fields: Json) => void report($, 'command.done', { request, reason: null, new_session_id: null, ...fields })
  if (!(await gateLoaded($))) {
    done({ state: 'refused', reason: 'unsupported' })
    return
  }
  if (stopShellsToo && !(await mayStop($))) {
    done({ state: 'refused', reason: 'unsupported' })
    return
  }
  try {
    await $.state.set(COMMAND, { request, name, sock: hello!.sock })
  } catch (err) {
    done({ state: 'failed', reason: `the command's mark was not written: ${String(err)}` })
    return
  }
  let text: string | null = null
  let error: string | null = null
  ownCommands += 1
  try {
    try {
      text = (await $.command.run({ command: name })).text ?? null
    } catch (err) {
      error = String(err)
    }
    // The gate's withdrawal is the command's own answer, one line naming the reason.
    const withdrawn = text === null ? null : GATE_DROP.exec(text)
    if (error !== null) {
      done({ state: 'failed', reason: error })
    } else if (withdrawn) {
      done({ state: 'refused', reason: withdrawn[1] ?? 'gone' })
    } else {
      // The command took effect: only now are the shells stopped.
      const shells = stopShellsToo ? await stopShells($, name) : undefined
      let sessionId: string | null = null
      try {
        sessionId = await $.session.id()
      } catch {
        sessionId = null
      }
      done({ state: 'ran', new_session_id: sessionId, ...(shells === undefined ? {} : { shells }) })
    }
  } finally {
    ownCommands -= 1
  }
  void $.state.set(COMMAND, null).catch(() => {})
}

/** A row's text, its text blocks joined. */
function rowText(message: unknown): string {
  const content = (message as { content?: unknown } | null)?.content
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  return content.map(b => (b !== null && typeof b === 'object' && typeof (b as Json).text === 'string' ? (b as Json).text as string : '')).join('\n')
}

/** Does this `door: "command"` row mark the list? Every one does, but for the rows of the
 *  runner's own command, which starts no shell; a `!` command's row always does. */
function commandRowMarks(message: unknown): boolean {
  return ownCommands === 0 || BASH_ROW.test(rowText(message))
}

async function onLine($: Dollar, line: string): Promise<void> {
  let m: Json
  try {
    const v = JSON.parse(line)
    if (v === null || typeof v !== 'object') throw new Error('not an object')
    m = v as Json
  } catch {
    log($, `the endpoint wrote a line that is not a JSON object: ${line.slice(0, 120)}`)
    return
  }
  const op = m.op
  if (op === 'failed') {
    endpointEnded($, `it could not start: ${String(m.error)}`)
    return
  }
  if (op === 'hello') {
    const sock = str(m.mod_sock)
    if (hello !== undefined || sock === undefined) return
    if (m.leg !== LEG) {
      endpointEnded($, `it speaks leg ${String(m.leg)}, the runner leg ${LEG}`)
      return
    }
    let sessionId: string | null = null
    try {
      sessionId = await $.session.id()
    } catch {
      sessionId = null
    }
    // The session report goes first, ahead of anything queued before the line came.
    let land: () => void = () => {}
    const landed = new Promise<void>(resolve => { land = resolve })
    queue.unshift({ kind: 'session', fields: { session_id: sessionId, mod_version: MOD_VERSION }, seq: 0, landed, land })
    hello = { sock, stopWaitMs: num(m.stop_wait_ms, 2000), listenWaitMs: num(m.listen_wait_ms, 2000), stopAfterClear: m.stop_after_clear !== false }
    if (m.guards !== undefined) takeTable($, m.guards)
    helloSeen()
    void pump($)
    void writeSock($).catch(err => log($, `the mod.sock path was not written: ${String(err)}`))
    return
  }
  if (hello === undefined) return
  if (op === 'guards') {
    takeTable($, m.table)
    return
  }
  const delivery = str(m.delivery)
  const nonce = str(m.nonce)
  if (op === 'submit' && delivery !== undefined) {
    if (nonce === undefined || nonce === '') {
      // Without a nonce the gate would drop the prompt: submit nothing, say so.
      void report($, 'submit.close', { delivery, end: 'fail', reason: 'the submit line carries no window nonce' })
    } else {
      await openWindow($, delivery, str(m.text) ?? '', nonce)
    }
  } else if (op === 'append' && delivery !== undefined) {
    await append($, delivery, str(m.text) ?? '')
  } else if (op === 'live') {
    await live($, m.ask)
  } else if (op === 'agents') {
    await agentList($, m.ask)
  } else if (op === 'command' && str(m.request) !== undefined && (m.name === 'clear' || m.name === 'exit' || m.name === 'compact')) {
    await command($, m.request as string, m.name, m.stop_shells === true && m.name !== 'exit')
  } else {
    log($, `the endpoint wrote an op the runner does not take: ${String(op)}`)
  }
}

function endpointEnded($: Dollar, why: string): void {
  if (ended) return
  ended = true
  log($, `the endpoint ended: ${why}`)
  dropQueue()
  if (win !== null) {
    const w = win
    w.closed = true
    stopTimer(w)
    win = null
  }
  void $.state.set(ENDPOINT, null).catch(() => {})
  void $.state.set(COMMAND, null).catch(() => {})
  helloSeen()
}

async function readArgv($: Dollar): Promise<string[] | undefined> {
  // The launch's own name for the endpoint wins. Set but not absolute is no endpoint: the
  // file beside the plugin may name another environment's command. A `$.env.get` that
  // throws says nothing about whether the variable is set; it counts as unset, and the
  // file is read.
  let named: string | undefined
  try {
    named = await $.env.get('MOXI_TURNS_ENDPOINT')
  } catch {
    named = undefined
  }
  if (named !== undefined) {
    if (!named.startsWith('/')) {
      log($, `no endpoint: MOXI_TURNS_ENDPOINT is ${JSON.stringify(named)}, not an absolute command`)
      return undefined
    }
    return [named]
  }
  let doc: unknown
  try {
    doc = JSON.parse(await $.fs.read(`${$.plugin.root}/endpoint.json`))
  } catch {
    log($, `no endpoint: ${$.plugin.root}/endpoint.json is missing or not JSON (run turns' setup)`)
    return undefined
  }
  const d = (doc ?? {}) as Json
  const argv = Array.isArray(d.argv) ? d.argv : typeof d.command === 'string' ? [d.command] : undefined
  if (argv === undefined || argv.length === 0 || !argv.every(a => typeof a === 'string') || !(argv[0] as string).startsWith('/')) {
    log($, 'no endpoint: endpoint.json names no absolute command')
    return undefined
  }
  return argv as string[]
}

async function spawn($: Dollar): Promise<void> {
  if (spawned) return
  spawned = true
  const argv = await readArgv($)
  if (argv === undefined) {
    endpointEnded($, 'no endpoint command')
    return
  }
  void (async () => {
    let buf = ''
    try {
      const child = $.process.spawn({ argv })
      for (;;) {
        const x = await child.next()
        if (x.done) {
          endpointEnded($, `exited ${JSON.stringify(x.value)}`)
          break
        }
        const { stream, text } = x.value
        if (stream !== 'stdout') continue
        buf += text
        let i: number
        while ((i = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, i)
          buf = buf.slice(i + 1)
          if (line.trim() !== '') void onLine($, line)
        }
      }
    } catch (err) {
      endpointEnded($, `could not run: ${String(err)}`)
    }
  })()
}

/** The first surfaces read decides: true when the session draws nowhere. */
async function isHeadless($: Dollar): Promise<boolean> {
  if (headless === undefined) {
    let surfaces: readonly string[] = []
    try {
      surfaces = await $.session.surfaces()
    } catch {
      surfaces = []
    }
    if (headless === undefined) headless = surfaces.length === 0
  }
  return headless
}

const withContext = <R extends { additionalContext?: string[] }>(r: R, context: unknown): R =>
  typeof context === 'string' && context !== '' ? { ...r, additionalContext: [...(r.additionalContext ?? []), context] } : r

// -- the guards ----------------------------------------------------------------------------

const escapeRe = (s: string): string => s.replace(/[.*+?^${}()|[\]\\/-]/g, '\\$&')

/** A tool-name pattern as a regex over the whole name: `*` any run, the rest literal. */
export function globRegex(pattern: string): RegExp {
  return new RegExp(`^${pattern.split('*').map(escapeRe).join('.*')}$`)
}

/** The break line of a guard that let its call through unchecked. */
export const breakLine = (id: string, why: string): string => `[turns] guard ${id} could not check this call (${why}); it ran unchecked.`
/** The refusal of a call whose broken guard declared `on_error` deny. */
export const breakDeny = (id: string, why: string): string => `turns could not check this call with guard ${id} (${why})`

/** One table entry as the leg gives it, ready to match; null when it is not one. */
export function parseGuard(v: unknown): Guard | null {
  if (v === null || typeof v !== 'object' || Array.isArray(v)) return null
  const d = v as Json
  const id = str(d.id)
  if (id === undefined || id === '' || !Array.isArray(d.calls) || !d.calls.every(p => typeof p === 'string')) return null
  if (d.who !== 'any' && d.who !== 'main' && d.who !== 'agents') return null
  const match: [string, RegExp][] = []
  let bad: string | null = null
  if (d.match !== null && d.match !== undefined) {
    if (typeof d.match !== 'object' || Array.isArray(d.match)) return null
    const flags = d.match_flags === 'i' ? 'i' : ''
    for (const [field, source] of Object.entries(d.match as Json)) {
      if (typeof source !== 'string') return null
      try {
        match.push([field, new RegExp(source, flags)])
      } catch (err) {
        bad ??= `its match on ${field} does not compile here: ${String(err)}`
      }
    }
  }
  return {
    id,
    calls: (d.calls as string[]).map(globRegex),
    who: d.who,
    match,
    judge: d.judge === true,
    deny: str(d.deny) ?? '',
    wantsPrompts: Array.isArray(d.wants) && d.wants.includes('prompts'),
    onError: d.on_error === 'deny' ? 'deny' : 'allow',
    deadlineMs: num(d.deadline_ms, GUARD_DEADLINE_MS),
    bad,
  }
}

/** The table a `guards` field or line holds; entries that are not entries are left out. */
export function parseTable(v: unknown): Guard[] | null {
  if (!Array.isArray(v)) return null
  const out: Guard[] = []
  for (const item of v) {
    const g = parseGuard(item)
    if (g !== null) out.push(g)
  }
  return out
}

function takeTable($: Dollar, v: unknown): void {
  const table = parseTable(v)
  if (table === null) {
    log($, 'the endpoint sent a guard table that is not a list; the last one stays')
    return
  }
  if (Array.isArray(v) && table.length !== v.length) log($, `${v.length - table.length} guard entries were not entries and are left out`)
  for (const g of table) if (g.bad !== null) log($, `guard ${g.id}: ${g.bad}`)
  guards = table
}

/** What the table says of one call. */
export type Decision = {
  /** The entries that cover it, in table order. */
  covering: Guard[]
  /** The first covering data-only entry: it refuses with no trip. */
  deny: Guard | null
  /** The covering judge entries, asked on /ask/guard when no data-only entry refuses. */
  ask: Guard[]
  /** Entries whose patterns and `who` take the call but whose `match` can't run here. */
  broken: Guard[]
}

const isMain = (agentId: unknown): boolean => agentId === undefined || agentId === null || agentId === ''

export function decide(table: readonly Guard[], tool: string, agentId: unknown, input: Json): Decision {
  const d: Decision = { covering: [], deny: null, ask: [], broken: [] }
  const main = isMain(agentId)
  for (const g of table) {
    if (!g.calls.some(r => r.test(tool))) continue
    if ((g.who === 'main' && !main) || (g.who === 'agents' && main)) continue
    if (g.bad !== null) {
      d.broken.push(g)
      continue
    }
    if (!g.match.every(([field, re]) => typeof input[field] === 'string' && re.test(input[field] as string))) continue
    d.covering.push(g)
    if (!g.judge) d.deny ??= g
    else d.ask.push(g)
  }
  return d
}

/** A call's input: its envelope less the keys that are no argument of the tool. */
function inputOf(e: Json): Json {
  const out: Json = {}
  for (const [k, v] of Object.entries(e)) if (!NOT_INPUT.has(k)) out[k] = v
  return out
}

/** Would the catch refuse this call? Only when a covering (or broken) entry declared
 *  `on_error` deny; a table it can't read refuses when any entry did. */
export function catchRefuses(table: readonly Guard[], e: Json): boolean {
  try {
    const d = decide(table, String(e.tool), e.agentId, inputOf(e))
    return [...d.covering, ...d.broken].some(g => g.onError === 'deny')
  } catch {
    return table.some(g => g.onError === 'deny')
  }
}

type Trip = { answer: Json } | { why: string }

/** One /ask/guard trip, bounded by `boundMs` on the plugin's own timer. */
async function guardTrip($: Dollar, body: Json, boundMs: number): Promise<Trip> {
  if (hello === undefined || ended) return { why: "turns' endpoint is not running" }
  const sent = post($, hello.sock, '/ask/guard', body).then(
    (answer): Trip => ({ answer }),
    (err): Trip => ({ why: `the trip failed: ${String(err)}` }),
  )
  const late = after<Trip>($, boundMs).then((): Trip => ({ why: `no answer within ${boundMs} ms` }))
  return Promise.race([sent, late])
}

const texts = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && x !== '') : [])

/** What the guards say before the tool runs: a refusal, or the lines for after it. */
async function judge($: Dollar, w: Watch, e: Json, d: Decision): Promise<{ deny: string } | { lines: string[] }> {
  if (d.deny !== null) return { deny: d.deny.deny !== '' ? d.deny.deny : `guard ${d.deny.id} refused this call` }
  const late: string[] = []
  for (const g of d.broken) {
    const why = g.bad ?? 'its match does not compile here'
    if (g.onError === 'deny') return { deny: breakDeny(g.id, why) }
    late.push(breakLine(g.id, why))
  }
  if (d.ask.length === 0) return { lines: late }
  const boundMs = Math.max(...d.ask.map(g => g.deadlineMs)) + MARGIN_MS
  const call: Json = { tool: e.tool, tool_use_id: e.tool_use_id, input: inputOf(e), agentId: isMain(e.agentId) ? null : e.agentId }
  if (d.ask.some(g => g.wantsPrompts)) call.prompts = turnPrompts.map(p => ({ origin: p.origin, text: p.text }))
  const t0 = performance.now()
  const trip = await guardTrip($, { call, guards: d.ask.map(g => g.id) }, boundMs)
  const ms = performance.now() - t0
  w.away += ms
  sample($, 'guard', ms)
  if ('why' in trip) {
    const denies = d.ask.find(g => g.onError === 'deny')
    if (denies !== undefined) return { deny: breakDeny(denies.id, trip.why) }
    return { lines: [...d.ask.map(g => breakLine(g.id, trip.why)), ...late] }
  }
  const deny = trip.answer.deny
  if (typeof deny === 'string' && deny !== '') return { deny }
  return { lines: [...texts(trip.answer.lines), ...texts(trip.answer.broken), ...late] }
}

/** The tool's result with `lines` added to what the model reads after it; a refusal as is. */
function withLines<R extends { deny?: unknown; context?: readonly string[] }>(r: R, lines: string[]): R {
  if (lines.length === 0 || r === null || typeof r !== 'object' || r.deny !== undefined) return r
  return { ...r, context: [...(r.context ?? []), ...lines] }
}

// -- the prompt list -----------------------------------------------------------------------

/** A prompt leaves the waiting set (once its turn has started, or it entered none). */
function unwait(p: Seen): void {
  const i = waiting.indexOf(p)
  if (i >= 0) waiting.splice(i, 1)
}

/** A prompt that entered no turn leaves the waiting set and the list. */
function forget(p: Seen): void {
  unwait(p)
  const i = turnPrompts.indexOf(p)
  if (i >= 0) turnPrompts.splice(i, 1)
}

/** A main turn starts: its list is the one waiting prompt the runner can tie to it. */
function openList(text: string): void {
  const same = waiting.filter(p => p.text === text)
  // Two with the same text: the one still in flight (the turn may be starting inside its
  // `next(e)`), then the latest.
  let chosen = [...same].reverse().find(p => p.inFlight) ?? same.at(-1)
  if (chosen === undefined && ownSubmits === 0) chosen = waiting.filter(p => p.turnId === null).at(-1)
  turnPrompts = chosen === undefined ? [] : [chosen]
  waiting = []
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const hook = watch()
    try {
      // Spawned before `next`, so the endpoint's start overlaps the session's.
      if (!(await isHeadless($))) await spawn($)
      return behind(hook, performance.now(), await next(e))
    } finally {
      timeHook($, 'session.start', hook)
    }
  })

  on('classic.SessionStart', async ($, e, next) => {
    const hook = watch()
    try {
      if (e.agent_id !== undefined || (await isHeadless($))) return behind(hook, performance.now(), await next(e))
      if (!spawned) await spawn($)
      if (hello === undefined && !ended) await Promise.race([helloP, after($, HELLO_WAIT_MS)])
      if (hello === undefined || ended) return behind(hook, performance.now(), await next(e))
      // A /clear empties $.state (and a resume or compaction may): the mod.sock path is
      // written again at every SessionStart, for the gate.
      await writeSock($).catch(err => log($, `the mod.sock path was not written: ${String(err)}`))
      const answer = await ask($, '/ask/context', { event: 'ready', source: e.source ?? null, session_id: e.session_id }, hello.listenWaitMs + MARGIN_MS)
      return withContext(behind(hook, performance.now(), await next(e)), answer?.context)
    } finally {
      timeHook($, 'classic.SessionStart', hook)
    }
  })

  on('classic.UserPromptSubmit', async ($, e, next) => {
    const hook = watch()
    try {
      if (e.agent_id !== undefined || headless === true || hello === undefined || ended) return behind(hook, performance.now(), await next(e))
      const w = win
      // turns' own entry hook: the bound is raised by this ask's bound while it runs.
      if (w !== null && turnOpen === null) arm($, w, IDLE_FAIL_MS + hello.listenWaitMs + MARGIN_MS)
      const answer = await ask($, '/ask/context', { event: 'prompt', source: null, session_id: e.session_id }, hello.listenWaitMs + MARGIN_MS)
      if (w !== null && win === w && turnOpen === null) arm($, w, IDLE_FAIL_MS)
      return withContext(behind(hook, performance.now(), await next(e)), answer?.context)
    } finally {
      timeHook($, 'classic.UserPromptSubmit', hook)
    }
  })

  on('turn.start', async ($, e, next) => {
    const hook = watch()
    try {
      // Only the main loop opens a turn: a subagent's or teammate's start (`agentId`)
      // leaves the open turn and its prompts as they are.
      if (e.agentId === undefined && headless !== true) openList(e.text)
      if (e.agentId !== undefined || headless === true || ended) return behind(hook, performance.now(), await next(e))
      turnOpen = e.turnId
      stopTimer(win)
      await reportAndWait($, 'turn.start', { turn_id: e.turnId })
      return behind(hook, performance.now(), await next(e))
    } finally {
      timeHook($, 'turn.start', hook)
    }
  })

  on('turn.complete', async ($, e, next) => {
    const hook = watch()
    try {
      if (e.agentId === undefined && e.isAborted) dropMainLoopCalls()
      if (e.agentId !== undefined || headless === true || ended) return behind(hook, performance.now(), await next(e))
      turnOpen = null
      await reportAndWait($, 'turn.complete', { turn_id: e.turnId, reason: e.reason, is_aborted: e.isAborted })
      if (win !== null) arm($, win, IDLE_FAIL_MS)
      return behind(hook, performance.now(), await next(e))
    } finally {
      timeHook($, 'turn.complete', hook)
    }
  })

  on('classic.Stop', async ($, e, next) => {
    const hook = watch()
    try {
      noteTasks(e.background_tasks)
      if (e.agent_id !== undefined || headless === true || hello === undefined || ended) return behind(hook, performance.now(), await next(e))
      const facts = {
        turn_id: turnOpen,
        session_id: e.session_id,
        stop_hook_active: e.stop_hook_active,
        task_types: e.background_tasks === undefined ? null : e.background_tasks.map(t => String((t as { type?: unknown }).type ?? 'unknown')),
        crons: e.session_crons === undefined ? null : e.session_crons.length,
      }
      const answer = await ask($, '/ask/stop', facts, hello.stopWaitMs + MARGIN_MS)
      const r = behind(hook, performance.now(), await next(e))
      const reason = answer?.reason
      return typeof reason === 'string' && reason !== '' ? { ...r, block: reason } : r
    } finally {
      timeHook($, 'classic.Stop', hook)
    }
  })

  on('classic.SubagentStop', async ($, e, next) => {
    const hook = watch()
    try {
      noteTasks(e.background_tasks)
      return behind(hook, performance.now(), await next(e))
    } finally {
      timeHook($, 'classic.SubagentStop', hook)
    }
  })

  // Every prompt but the runner's own passes here (a plugin's hooks never see its own
  // submits): the prompt list's steps 1 and 2.
  on('prompt.submit', async ($, e, next) => {
    const hook = watch()
    try {
      if (headless === true || e.isMeta === true) return behind(hook, performance.now(), await next(e))
      const kind = (e.origin as { kind?: unknown } | undefined)?.kind
      const p: Seen = { origin: typeof kind === 'string' ? kind : null, text: e.text, turnId: e.turnId ?? null, inFlight: true }
      waiting.push(p)
      if (p.turnId !== null && p.turnId === turnOpen) turnPrompts.push(p)
      let entered = false
      try {
        const since = performance.now()
        const r = behind(hook, since, await next(e))
        if (r.drop === undefined && typeof r.text === 'string') {
          entered = true
          if (p.turnId !== null) p.text = r.text
          else unwait(p)
        }
        return r
      } finally {
        p.inFlight = false
        if (!entered) forget(p)
      }
    } finally {
      timeHook($, 'prompt.submit', hook)
    }
  })

  // Every loop's tool calls pass here: each one in flight marks the list, and the guards
  // judge it. The hook's time leaves out the tool's own run (`next`) and the guard trip.
  // The runner's own calls (its TaskStop) are never judged and mark nothing.
  on('tool.call', async ($, e, next) => {
    const hook = watch()
    const id = e.tool_use_id
    const own = next.origin.plugin === PLUGIN || (e.tool === 'TaskStop' && stopping.has(String((e as Json).task_id)))
    if (!own) {
      inflight.set(id, e.agentId)
      tasksMarked = true
    }
    try {
      const d = own || headless === true || guards.length === 0 ? null : decide(guards, e.tool, e.agentId, inputOf(e as Json))
      if (d === null || (d.covering.length === 0 && d.broken.length === 0)) return behind(hook, performance.now(), await next(e))
      const verdict = await judge($, hook, e as Json, d)
      if ('deny' in verdict) return { deny: verdict.deny }
      const since = performance.now()
      return withLines(behind(hook, since, await next(e)), verdict.lines)
    } finally {
      if (!own) {
        inflight.delete(id)
        tasksMarked = true
      }
      timeHook($, 'tool.call', hook)
    }
  }).catch(($, e, next) => {
    // A grace of 1 s: decide at once, with no trip.
    if (next.called) return next(e)
    const own = next.origin.plugin === PLUGIN || (e.tool === 'TaskStop' && stopping.has(String((e as Json).task_id)))
    if (!own && catchRefuses(guards, e as Json)) return { deny: CANNOT_CHECK }
    if (!own) tasksMarked = true
    return next(e)
  })

  // A command the person runs with `!` raises no tool call, even moved to the background:
  // only its rows (door `command`) show it. Each such row marks the list, as a call's
  // start does.
  on('session.append', async ($, e, next) => {
    if (e.door === 'command' && commandRowMarks(e.message)) tasksMarked = true
    return next(e)
  })

  // A turn's abort stops its running tools: the main loop's calls leave the count, so a
  // frame that never returns cannot hold it for good.
  on('turn.abort', async ($, e, next) => {
    dropMainLoopCalls()
    return next(e)
  })
}

/** The main loop's calls leave the count. Each set the mark as it started, so the mark
 *  stays until a Stop; a turn that made no call leaves the list as it was. */
function dropMainLoopCalls(): void {
  for (const [id, agentId] of inflight) if (agentId === undefined) inflight.delete(id)
}
