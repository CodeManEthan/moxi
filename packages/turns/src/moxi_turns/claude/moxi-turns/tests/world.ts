// The world beneath the runner in its tests: the engine's nouns from memory, a child
// process whose stdout the test writes, and an endpoint on mod.sock that records every
// request and answers by path.

import { mock } from 'claude-code/testing'
import type { On } from 'claude-code'

export const ROOT_ARGV = ['/opt/env/bin/moxi-turns-endpoint']
export const SOCK = '/run/user/1000/moxi/turns/claude-n1/mod.sock'

type Answer = Record<string, unknown> | 'hang' | 'fail'

/** A row the engine appends at the door `command`: a slash command's or a `!` command's. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function commandRow($: any, text: string) {
  return $.session.append({
    message: { type: 'user', role: 'user', content: [{ type: 'text', text }] },
    door: 'command',
    origin: { kind: 'unclassified' },
    uuid: `row-${text.length}`,
  })
}

export function world(on: On, options: { surfaces?: string[]; endpointJson?: string | null; env?: Record<string, string>; envFails?: boolean; gate?: boolean; spinMs?: number } = {}) {
  const clock = mock.clock(on, { now: 1_000 })
  // The session's environment: only what the test names, never the one the tests run in.
  // With `envFails`, every `$.env.get` throws instead.
  if (options.envFails) {
    on('env.get', async () => {
      throw new Error('the environment cannot be read')
    })
  } else {
    mock.env(on, options.env ?? {})
  }
  const w = {
    clock,
    surfaces: options.surfaces ?? ['terminal'],
    sessionId: 'S-1',
    agents: [] as Record<string, unknown>[],
    /** `$.agent.list()` throws: the list cannot be read. */
    agentsFail: false,
    box: '',
    gate: options.gate ?? true,
    spawned: [] as string[][],
    /** Every path the runner read with `$.fs.read`, in order. */
    reads: [] as string[],
    /** Every request the endpoint got, in order: path and body. */
    got: [] as { path: string; body: Record<string, unknown> }[],
    /** The endpoint's answer per path; a default `{}`. */
    answers: {} as Record<string, Answer>,
    submits: [] as string[],
    /** How the engine settles each prompt.submit: set before the line arrives. */
    settle: undefined as undefined | ((text: string) => Promise<Record<string, unknown>>),
    commands: [] as string[],
    stateWrites: [] as { key: string; value: unknown }[],
    lines: [] as string[],
    /** `$.tool.check`'s verdict per TaskStop task id; a default `allow`. A function may
     *  throw. */
    checks: {} as Record<string, string>,
    /** What a `TaskStop` call does per task id: a result (`{ isError, text }` or
     *  `{ deny }`), `hang`, or a promise the test settles; a default a clean stop. */
    stops: {} as Record<string, Record<string, unknown> | 'hang' | Promise<Record<string, unknown>>>,
    /** Every TaskStop the runner called, in order, and each call's origin. */
    stopCalls: [] as { taskId: string; origin: string }[],
    /** The other tools' calls the test holds open: resolve one to end it. */
    holds: {} as Record<string, () => void>,
    /** Every `tool.call` that reached the engine, by tool, with its origin. */
    toolCalls: [] as { tool: string; origin: string }[],
    /** How `command.run` answers, when set: the gate's withdrawal line, or a throw. */
    commandAnswer: undefined as undefined | ((name: string) => Promise<Record<string, unknown>>),
    /** The commands run and the tasks stopped, in the order they happened. */
    order: [] as string[],
    wake: () => {},
    childDone: false,
  }
  const endpointJson = options.endpointJson === undefined ? JSON.stringify({ argv: ROOT_ARGV }) : options.endpointJson

  on('session.start', async ($, e) => ({ cwd: e.cwd }))
  on('session.surfaces', async () => ({ value: w.surfaces as never }))
  on('session.id', async () => ({ value: w.sessionId }))
  on('turn.start', async ($, e) => {
    // The work behind a hook (another plugin's, the engine's): real time, which a hook's
    // own timing leaves out.
    const t0 = performance.now()
    while (performance.now() - t0 < (options.spinMs ?? 0)) { /* spin */ }
    return { turnId: e.turnId }
  })
  on('turn.complete', async ($, e) => ({ text: e.answer }))
  on('classic.SessionStart', async () => ({}))
  on('classic.UserPromptSubmit', async () => ({}))
  on('classic.Stop', async () => ({}))
  on('fs.read', async ($, e) => {
    w.reads.push(e.path)
    if (e.path.endsWith('/endpoint.json') && endpointJson !== null) return { value: endpointJson }
    throw new Error(`ENOENT: ${e.path}`)
  })
  on('command.list', async () => ({ value: (w.gate ? [{ name: 'moxi-turns-gate' }] : []) as never }))
  on('agent.list', async () => {
    if (w.agentsFail) throw new Error('no list')
    return { value: w.agents as never }
  })
  on('prompt.read', async () => ({ value: { text: w.box, cursor: w.box.length } }))
  on('state.set', async ($, e, next) => {
    w.stateWrites.push({ key: (e as { key: string }).key, value: (e as { value: unknown }).value })
    return next(e)
  })
  on('prompt.submit', async ($, e) => {
    w.submits.push(e.text)
    if (w.settle) return (await w.settle(e.text)) as never
    return { text: e.text, origin: e.origin }
  })
  on('command.run', async ($, e) => {
    w.commands.push(e.command)
    w.order.push(`run ${e.command}`)
    if (w.commandAnswer) return (await w.commandAnswer(e.command)) as never
    return { text: `ran ${e.command}` }
  })
  on('session.append', async ($, e, next) => next(e))
  on('classic.SubagentStop', async () => ({}))
  on('tool.check', async ($, e) => {
    const id = String((e.input as Record<string, unknown>)?.task_id)
    return { decision: (w.checks[id] ?? 'allow') as never }
  })
  on('tool.call', async ($, e, next) => {
    const origin = next.origin.plugin
    w.toolCalls.push({ tool: e.tool, origin })
    if (e.tool === 'TaskStop') {
      const id = String((e as Record<string, unknown>).task_id)
      w.stopCalls.push({ taskId: id, origin })
      w.order.push(`stop ${id}`)
      const how = w.stops[id]
      if (how === 'hang') await new Promise(() => {})
      const r = how === undefined ? { result: { message: 'stopped', task_id: id, task_type: 'local_bash' }, text: `stopped ${id}` } : await how
      return r as never
    }
    // A call the test holds open runs until it lets go: its `command` names it.
    const t0 = performance.now()
    while (performance.now() - t0 < (options.spinMs ?? 0)) { /* spin */ }
    const key = String((e as Record<string, unknown>).command ?? e.tool)
    if (key in w.holds || key.startsWith('hold:')) await new Promise<void>(resolve => { w.holds[key] = resolve })
    return { result: {}, text: 'ok' } as never
  })
  on('http.fetch', async ($, e) => {
    const path = new URL(e.url).pathname
    const body = JSON.parse(String(e.init?.body ?? '{}')) as Record<string, unknown>
    w.got.push({ path, body })
    const a = w.answers[path] ?? {}
    if (a === 'fail') throw new Error('ECONNREFUSED')
    if (a === 'hang') await new Promise(() => {})
    return { value: { status: 200, ok: true, headers: {}, text: JSON.stringify(a) } }
  })
  on('process.spawn', async function* ($, e) {
    w.spawned.push([...e.argv])
    for (;;) {
      while (w.lines.length > 0) yield { stream: 'stdout' as const, text: w.lines.shift()! + '\n' }
      if (w.childDone) return { value: { code: 0, signal: null } } as never
      await new Promise<void>(resolve => { w.wake = resolve })
    }
  })

  return Object.assign(w, {
    /** The endpoint writes a line on its stdout. */
    async line(obj: Record<string, unknown>) {
      w.lines.push(JSON.stringify(obj))
      w.wake()
      await clock.settle()
    },
    async hello(extra: Record<string, unknown> = {}) {
      await this.line({ op: 'hello', leg: 2, mod_sock: SOCK, stop_wait_ms: 2000, listen_wait_ms: 2000, endpoint_version: '0.1.0', pid: 42, ...extra })
    },
    async end() {
      w.childDone = true
      w.wake()
      await clock.settle()
    },
    reports(): Record<string, unknown>[] {
      return w.got.filter(g => g.path === '/report').map(g => g.body)
    },
    kinds(): string[] {
      return this.reports().map(r => String(r.kind))
    },
  })
}

export type World = ReturnType<typeof world>
