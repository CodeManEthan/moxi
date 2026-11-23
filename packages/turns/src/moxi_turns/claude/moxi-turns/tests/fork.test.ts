// The runner's fork: the spawn with its warm-up, the task as the first resumed round and
// its answer, every way a fork is lost, how long the runner holds a fork's id, and how it
// tells a fork's calls from its own.
//
// The test kit answers no spawn with an agent id (only the engine's core sets one), so the
// sequence runs here through `runFork` on a stand-in engine; what needs no id (a refused
// spawn, one with no agent, the calls of agents the runner does not hold) runs through the
// runner's own hooks.

import { describe, expect, test } from 'claude-code/testing'
import { world } from './world'
import type { World } from './world'
import { WARM_UP, heldFork, noteForkEnd, ownCall, roundWaitMs, runFork, runRound } from '../hooks/register'
import type { Fork, ForkEngine } from '../hooks/register'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type E = any
type Json = Record<string, unknown>

/** A stand-in engine: spawns and sends as the test sets them, a clock the test moves, and
 *  the reports in order. */
function stand(options: { spawn?: Json | 'throw'; send?: Json | 'throw' } = {}) {
  let now = 0
  const timers: { at: number; then: () => void; off: boolean }[] = []
  const s = {
    forks: new Map<string, Fork>(),
    spawns: [] as Json[],
    sends: [] as { to: unknown; text: string }[],
    reports: [] as Json[],
    spawnAnswer: options.spawn ?? { model: 'm', agentId: 'f1' } as Json | 'throw',
    sendAnswer: options.send ?? { isDelivered: true } as Json | 'throw',
    /** Lets every pending step run. */
    async settle() {
      for (let i = 0; i < 50; i++) await Promise.resolve()
    },
    async advance(ms: number) {
      now += ms
      for (const t of timers) {
        if (!t.off && t.at <= now) {
          t.off = true
          t.then()
        }
      }
      await this.settle()
    },
    /** A turn.complete in the agent's loop. */
    async end(agentId: string, answer: string, reason = 'answer') {
      noteForkEnd(s.forks, agentId, { reason, isAborted: reason === 'aborted', answer })
      await this.settle()
    },
    kinds(): string[] {
      return s.reports.map(r => String(r.kind))
    },
  }
  const engine: ForkEngine = {
    async spawn(args) {
      s.spawns.push({ ...args })
      if (s.spawnAnswer === 'throw') throw new Error('no spawn here')
      return s.spawnAnswer as { agentId?: string; deny?: string }
    },
    async send(args) {
      s.sends.push({ to: args.to, text: args.text })
      if (s.sendAnswer === 'throw') throw new Error('no send here')
      return s.sendAnswer as { isDelivered: boolean; reason?: string }
    },
    after(ms, then) {
      const t = { at: now + ms, then, off: false }
      timers.push(t)
      return { cancel: () => { t.off = true } }
    },
    report(kind, fields) {
      s.reports.push({ kind, ...fields })
    },
  }
  return Object.assign(s, {
    /** The endpoint's `fork` line; `roundMs` its bound, when the line carries one. */
    fork(delivery = 'd1', text = 'recall what we said', roundMs?: number): Promise<void> {
      return runFork(engine, s.forks, delivery, text, roundMs)
    },
    /** The endpoint's `fork.continue` line. */
    round(delivery: string, agentId: string, text: string, roundMs?: number): Promise<void> {
      return runRound(engine, s.forks, delivery, agentId, text, roundMs)
    },
  })
}

describe('the fork', () => {
  test('spawned with the warm-up; the task goes as its first resumed round, and only that round is reported', async () => {
    const s = stand()
    const done = s.fork()
    await s.settle()
    expect(s.spawns).toEqual([{ subagentType: 'fork', prompt: WARM_UP, description: 'subconscious' }])
    expect(s.reports).toEqual([{ kind: 'fork.started', delivery: 'd1', agent_id: 'f1' }])
    expect(s.sends).toEqual([])
    expect(heldFork(s.forks, 'f1')).toBe(true)
    await s.end('f1', 'READY')
    expect(s.sends).toEqual([{ to: { agentId: 'f1' }, text: 'recall what we said' }])
    expect(s.reports.length).toBe(1)
    expect(heldFork(s.forks, 'f1')).toBe(true)
    await s.end('f1', 'two points')
    await done
    expect(s.reports).toEqual([
      { kind: 'fork.started', delivery: 'd1', agent_id: 'f1' },
      { kind: 'fork.done', delivery: 'd1', answer: 'two points', reason: 'answer', is_aborted: false },
    ])
    // Held until the delivery settled, and no longer.
    expect(heldFork(s.forks, 'f1')).toBe(false)
  })

  test('a round that ends in an error is still the delivery\'s end, with its reason', async () => {
    const s = stand()
    const done = s.fork()
    await s.settle()
    await s.end('f1', 'READY')
    await s.end('f1', '', 'error')
    await done
    expect(s.reports.at(-1)).toEqual({ kind: 'fork.done', delivery: 'd1', answer: '', reason: 'error', is_aborted: false })
  })

  test('a refused spawn, one that throws and one that names no agent are lost, denied, and hold nothing', async () => {
    for (const [answer, reason] of [
      [{ deny: 'too many agents' }, 'denied: too many agents'],
      ['throw', 'denied: the spawn failed: Error: no spawn here'],
      [{ model: 'm' }, 'denied: the spawn named no agent'],
    ] as const) {
      const s = stand({ spawn: answer as Json | 'throw' })
      await s.fork()
      expect(s.reports).toEqual([{ kind: 'fork.lost', delivery: 'd1', reason }])
      expect(s.sends).toEqual([])
      expect(s.forks.size).toBe(0)
    }
  })

  test('a warm-up that never completes is lost, gone, at 30 s, never a hang, and its id is let go', async () => {
    const s = stand()
    const done = s.fork()
    await s.settle()
    await s.advance(29_999)
    expect(s.kinds()).toEqual(['fork.started'])
    await s.advance(1)
    await done
    expect(s.reports.at(-1)).toEqual({ kind: 'fork.lost', delivery: 'd1', reason: 'gone: the warm-up did not complete within 30 s' })
    expect(s.sends).toEqual([])
    expect(heldFork(s.forks, 'f1')).toBe(false)
    // A late end changes nothing.
    await s.end('f1', 'READY')
    expect(s.sends).toEqual([])
    expect(s.reports.length).toBe(2)
  })

  test('a warm-up that ends in an error or aborted is lost, gone, and the task is never sent', async () => {
    for (const reason of ['error', 'aborted', 'refusal']) {
      const s = stand()
      const done = s.fork()
      await s.settle()
      await s.end('f1', '', reason)
      await done
      expect(s.reports.at(-1)).toEqual({ kind: 'fork.lost', delivery: 'd1', reason: `gone: the warm-up ended ${reason}` })
      expect(s.sends).toEqual([])
    }
  })

  test('a task the engine does not deliver, or a send that throws, is lost, gone', async () => {
    for (const [answer, reason] of [
      [{ isDelivered: false, reason: 'the agent is gone' }, 'gone: the task was not delivered: the agent is gone'],
      ['throw', 'gone: the task was not sent: Error: no send here'],
    ] as const) {
      const s = stand({ send: answer as Json | 'throw' })
      const done = s.fork()
      await s.settle()
      await s.end('f1', 'READY')
      await done
      expect(s.reports.at(-1)).toEqual({ kind: 'fork.lost', delivery: 'd1', reason })
      expect(s.forks.size).toBe(0)
    }
  })

  test('a task round with no end in 120 s is lost, gone', async () => {
    const s = stand()
    const done = s.fork()
    await s.settle()
    await s.end('f1', 'READY')
    await s.advance(119_999)
    expect(s.kinds()).toEqual(['fork.started'])
    await s.advance(1)
    await done
    expect(s.reports.at(-1)).toEqual({ kind: 'fork.lost', delivery: 'd1', reason: 'gone: the fork did not answer within 120 s' })
  })

  test('a task round with the asker\'s bound is lost, gone, at that bound', async () => {
    for (const ms of [5_000, 1_800_000]) {
      const s = stand()
      const done = s.fork('d1', 'recall', ms)
      await s.settle()
      await s.end('f1', 'READY')
      await s.advance(ms - 1)
      expect(s.kinds()).toEqual(['fork.started'])
      await s.advance(1)
      await done
      expect(s.reports.at(-1)).toEqual({ kind: 'fork.lost', delivery: 'd1', reason: `gone: the fork did not answer within ${ms / 1000} s` })
    }
  })

  test('a round past 120 s with a longer bound still answers', async () => {
    const s = stand()
    const done = s.fork('d1', 'recall', 3_600_000)
    await s.settle()
    await s.end('f1', 'READY')
    await s.advance(900_000)
    await s.end('f1', 'slow but answered')
    await done
    expect(s.reports.at(-1)).toMatchObject({ kind: 'fork.done', answer: 'slow but answered' })
  })

  test('a line\'s bound is taken when it is a number above 0 within the runtime\'s timers, else 120 s', () => {
    expect(roundWaitMs(5_000)).toBe(5_000)
    expect(roundWaitMs(2 ** 31 - 1)).toBe(2 ** 31 - 1)
    for (const bad of [undefined, null, 0, -1, 2 ** 31, Number.NaN, Number.POSITIVE_INFINITY, '5000', true]) {
      expect(roundWaitMs(bad)).toBe(120_000)
    }
  })

  test('the warm-up bound stops at the warm-up\'s end: the round gets its own', async () => {
    const s = stand()
    const done = s.fork()
    await s.settle()
    await s.advance(29_000)
    await s.end('f1', 'READY')
    await s.advance(5_000)
    expect(s.kinds()).toEqual(['fork.started'])
    await s.end('f1', 'late but answered')
    await done
    expect(s.reports.at(-1)).toMatchObject({ kind: 'fork.done', answer: 'late but answered' })
  })

  test('two forks at once each take their own ends', async () => {
    const s = stand()
    const one = s.fork('d1', 'one')
    await s.settle()
    s.spawnAnswer = { model: 'm', agentId: 'f2' }
    const two = s.fork('d2', 'two')
    await s.settle()
    await s.end('f2', 'READY')
    expect(s.sends).toEqual([{ to: { agentId: 'f2' }, text: 'two' }])
    await s.end('f1', 'READY')
    await s.end('f2', 'answer two')
    await s.end('f1', 'answer one')
    await Promise.all([one, two])
    expect(s.reports.filter(r => r.kind === 'fork.done').map(r => [r.delivery, r.answer])).toEqual([['d2', 'answer two'], ['d1', 'answer one']])
  })

  test('an end of an agent the runner does not hold is nobody\'s', () => {
    const forks = new Map<string, Fork>()
    noteForkEnd(forks, 'a9', { reason: 'answer', isAborted: false, answer: 'x' })
    noteForkEnd(forks, undefined, { reason: 'answer', isAborted: false, answer: 'x' })
    expect(forks.size).toBe(0)
  })
})

describe('a further round', () => {
  /** A fork `f1` whose task round answered. */
  async function answered() {
    const s = stand()
    const done = s.fork()
    await s.settle()
    await s.end('f1', 'READY')
    await s.end('f1', 'first answer')
    await done
    s.reports.length = 0
    s.sends.length = 0
    return s
  }

  test('a fork that answered is resumed with the text; started once delivered, done at its end', async () => {
    const s = await answered()
    const done = s.round('d2', 'f1', 'go on')
    await s.settle()
    expect(s.spawns.length).toBe(1)
    expect(s.sends).toEqual([{ to: { agentId: 'f1' }, text: 'go on' }])
    expect(s.reports).toEqual([{ kind: 'fork.started', delivery: 'd2', agent_id: 'f1' }])
    expect(heldFork(s.forks, 'f1')).toBe(true)
    await s.end('f1', 'second answer')
    await done
    expect(s.reports.at(-1)).toEqual({ kind: 'fork.done', delivery: 'd2', answer: 'second answer', reason: 'answer', is_aborted: false })
    expect(heldFork(s.forks, 'f1')).toBe(false)
  })

  test('a round asked while one is open is lost, denied, and sends nothing; the open one goes on', async () => {
    const s = await answered()
    const open = s.round('d2', 'f1', 'go on')
    await s.settle()
    await s.round('d3', 'f1', 'and on')
    expect(s.reports.at(-1)).toEqual({ kind: 'fork.lost', delivery: 'd3', reason: 'denied: a round of the fork is still open' })
    expect(s.sends.length).toBe(1)
    await s.end('f1', 'second answer')
    await open
    expect(s.reports.at(-1)).toMatchObject({ kind: 'fork.done', delivery: 'd2' })
  })

  test('a round the engine does not deliver is lost, gone, and never started', async () => {
    const s = await answered()
    s.sendAnswer = { isDelivered: false, reason: 'no such agent' }
    await s.round('d2', 'f1', 'go on')
    expect(s.reports).toEqual([{ kind: 'fork.lost', delivery: 'd2', reason: 'gone: the task was not delivered: no such agent' }])
    expect(heldFork(s.forks, 'f1')).toBe(false)
  })

  test('a round takes its own line\'s bound, not the task\'s', async () => {
    const s = await answered()
    const done = s.round('d2', 'f1', 'go on', 2_000)
    await s.settle()
    await s.advance(2_000)
    await done
    expect(s.reports.at(-1)).toEqual({ kind: 'fork.lost', delivery: 'd2', reason: 'gone: the fork did not answer within 2 s' })
    expect(heldFork(s.forks, 'f1')).toBe(false)
  })

  test('a round with no end in 120 s is lost, gone', async () => {
    const s = await answered()
    const done = s.round('d2', 'f1', 'go on')
    await s.settle()
    await s.advance(120_000)
    await done
    expect(s.reports.at(-1)).toEqual({ kind: 'fork.lost', delivery: 'd2', reason: 'gone: the fork did not answer within 120 s' })
    expect(heldFork(s.forks, 'f1')).toBe(false)
  })
})

describe('the fork line through the runner', () => {
  const start = ($: E) => $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  const forkReports = (w: World) => w.reports().filter(r => String(r.kind).startsWith('fork.')).map(r => {
    const { seq, ...rest } = r
    void seq
    return rest
  })

  test('the spawn is a background fork with the warm-up; one refused is reported lost, denied', async ($: E, on) => {
    const w = world(on)
    await start($)
    await w.hello()
    w.spawnAnswer = { deny: 'too many agents' }
    await w.line({ op: 'fork', delivery: 'd1', text: 'recall' })
    expect(w.spawns).toEqual([{ tool: 'Agent', subagent_type: 'fork', prompt: WARM_UP, description: 'subconscious', run_in_background: true }])
    expect(forkReports(w)).toEqual([{ kind: 'fork.lost', delivery: 'd1', reason: 'denied: too many agents' }])
  })

  test('a spawn that starts no agent is reported lost, denied, and nothing is sent', async ($: E, on) => {
    const w = world(on)
    await start($)
    await w.hello()
    await w.line({ op: 'fork', delivery: 'd1', text: 'recall' })
    expect(forkReports(w)).toEqual([{ kind: 'fork.lost', delivery: 'd1', reason: 'denied: the spawn named no agent' }])
    expect(w.sends).toEqual([])
  })

  test('an agent\'s turn.complete is never reported as the main loop\'s', async ($: E, on) => {
    const w = world(on)
    await start($)
    await w.hello()
    await $.turn.complete({ answer: 'READY', durationMs: 1, isAborted: false, turnId: 'f-t', reason: 'answer', agentId: 'f1' })
    expect(w.kinds()).not.toContain('turn.complete')
  })

  test('an agent\'s call the runner does not hold is judged and asked after as before', async ($: E, on) => {
    const w = world(on)
    await start($)
    await w.hello({
      guards: [{ id: 't.judge', calls: ['Bash'], who: 'agents', match: null, match_flags: '', judge: true, deny: '', wants: [], on_error: 'allow', deadline_ms: 500, step: 'call' }],
      floors: { 'usage.rows:after': 0 },
    })
    await $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: 'ls', agentId: 'a9' })
    expect(w.got.filter(g => g.path === '/ask/guard').map(g => (g.body.call as Json).agentId)).toEqual(['a9'])
    expect(w.got.filter(g => g.path === '/ask/after-tool').map(g => (g.body.call as Json).agentId)).toEqual(['a9'])
  })

  test('the engine\'s notice of a finished round passes through as the engine sent it', async ($: E, on) => {
    const w = world(on)
    await start($)
    await w.hello()
    const text = '<task-notification><result>NOTHING</result></task-notification>'
    const r = await $.prompt.submit({ text, origin: { kind: 'task-notification' }, wait: false })
    expect(r.text).toBe(text)
    expect(w.submits).toEqual([text])
  })
})

describe('the runner\'s own calls (fail closed)', () => {
  const none = new Set<string>()
  test('only a call from this plugin with no agentId at all is the runner\'s own', () => {
    expect(ownCall('moxi-turns', undefined, 'TaskStop', 's1', none)).toBe(true)
    expect(ownCall('moxi-turns', null, 'Bash', undefined, none)).toBe(true)
    expect(ownCall('moxi-turns', 'f1', 'Bash', undefined, none)).toBe(false)
    expect(ownCall('moxi-turns', 'someone-else', 'Bash', undefined, none)).toBe(false)
    expect(ownCall('engine', undefined, 'Bash', undefined, none)).toBe(false)
    expect(ownCall('other', undefined, 'Bash', undefined, none)).toBe(false)
  })

  test('a TaskStop of a task the runner is stopping is its own only on the main loop', () => {
    const now = new Set(['s1'])
    expect(ownCall('engine', undefined, 'TaskStop', 's1', now)).toBe(true)
    expect(ownCall('engine', 'f1', 'TaskStop', 's1', now)).toBe(false)
    expect(ownCall('engine', undefined, 'TaskStop', 's2', now)).toBe(false)
  })

  test('a fork is held only by its own id', () => {
    const forks = new Map([['f1', {}]])
    expect(heldFork(forks, 'f1')).toBe(true)
    expect(heldFork(forks, 'f2')).toBe(false)
    expect(heldFork(forks, undefined)).toBe(false)
    expect(heldFork(forks, '')).toBe(false)
  })
})
