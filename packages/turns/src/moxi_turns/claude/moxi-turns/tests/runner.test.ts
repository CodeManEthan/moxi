// The runner: the non-interactive rule, the internal leg's queue and bounds,
// the tie's window with its three ends and the idle-only bound, the context asks, the Stop
// ask, and the session commands.

import { describe, expect, test } from 'claude-code/testing'
import { ROOT_ARGV, SOCK, commandRow, world } from './world.ts'
import type { World } from './world.ts'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type E = any

const start = ($: E) => $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
const turnStart = ($: E, turnId: string) => $.turn.start({ text: '', turnId })
const turnEnd = ($: E, turnId: string, reason = 'answer') =>
  $.turn.complete({ answer: '', durationMs: 1, isAborted: reason === 'aborted', turnId, reason })

async function attached($: E, w: World): Promise<void> {
  await start($)
  await w.hello()
}

describe('the non-interactive rule', () => {
  test('a headless run spawns nothing, and SessionStart waits for no first line', async ($: E, on) => {
    const w = world(on, { surfaces: [] })
    // Under -p classic.SessionStart comes first; its read decides.
    const r = await $.classic.SessionStart({ source: 'startup' })
    await start($)
    await turnStart($, 't1')
    await turnEnd($, 't1')
    await $.classic.Stop({ stop_hook_active: false })
    expect(w.spawned).toEqual([])
    expect(w.got).toEqual([])
    expect(r.additionalContext ?? []).toEqual([])
    expect(w.clock.now()).toBe(1_000)
  })

  test('an interactive start spawns the endpoint named in endpoint.json, at session.start', async ($: E, on) => {
    const w = world(on)
    await start($)
    expect(w.spawned).toEqual([ROOT_ARGV])
  })

  test('with endpoint.json missing nothing is spawned and SessionStart does not wait', async ($: E, on) => {
    const w = world(on, { endpointJson: null })
    await start($)
    await $.classic.SessionStart({ source: 'startup' })
    expect(w.spawned).toEqual([])
    expect(w.clock.now()).toBe(1_000)
  })

  test('endpoint.json naming a relative command spawns nothing', async ($: E, on) => {
    const w = world(on, { endpointJson: JSON.stringify({ argv: ['moxi-turns-endpoint'] }) })
    await start($)
    expect(w.spawned).toEqual([])
  })

  test('MOXI_TURNS_ENDPOINT set and absolute is the command, and endpoint.json is never read', async ($: E, on) => {
    const w = world(on, { env: { MOXI_TURNS_ENDPOINT: '/opt/launch/bin/moxi-turns-endpoint' } })
    await start($)
    expect(w.spawned).toEqual([['/opt/launch/bin/moxi-turns-endpoint']])
    expect(w.reads.filter(p => p.endsWith('/endpoint.json'))).toEqual([])
  })

  test('MOXI_TURNS_ENDPOINT alone, with no endpoint.json, is enough', async ($: E, on) => {
    const w = world(on, { endpointJson: null, env: { MOXI_TURNS_ENDPOINT: '/opt/launch/bin/moxi-turns-endpoint' } })
    await start($)
    expect(w.spawned).toEqual([['/opt/launch/bin/moxi-turns-endpoint']])
  })

  test('MOXI_TURNS_ENDPOINT set but not absolute spawns nothing, even with a good endpoint.json', async ($: E, on) => {
    const w = world(on, { env: { MOXI_TURNS_ENDPOINT: 'moxi-turns-endpoint' } })
    await start($)
    await $.classic.SessionStart({ source: 'startup' })
    expect(w.spawned).toEqual([])
    expect(w.reads.filter(p => p.endsWith('/endpoint.json'))).toEqual([])
    expect(w.clock.now()).toBe(1_000)
  })

  test('MOXI_TURNS_ENDPOINT set but empty spawns nothing', async ($: E, on) => {
    const w = world(on, { env: { MOXI_TURNS_ENDPOINT: '' } })
    await start($)
    expect(w.spawned).toEqual([])
  })

  test('MOXI_TURNS_ENDPOINT unset falls back to endpoint.json', async ($: E, on) => {
    const w = world(on)
    await start($)
    expect(w.spawned).toEqual([ROOT_ARGV])
    expect(w.reads.filter(p => p.endsWith('/endpoint.json')).length).toBe(1)
  })

  test('an env read that throws counts as unset, and endpoint.json is read', async ($: E, on) => {
    const w = world(on, { envFails: true })
    await start($)
    expect(w.spawned).toEqual([ROOT_ARGV])
    expect(w.reads.filter(p => p.endsWith('/endpoint.json')).length).toBe(1)
  })
})

describe('the report queue', () => {
  test('nothing is sent before the first line; attach goes first, then what waited, in order', async ($: E, on) => {
    const w = world(on)
    await start($)
    await turnStart($, 't1')
    await turnEnd($, 't1')
    expect(w.got).toEqual([])
    await w.hello()
    expect(w.kinds()).toEqual(['session', 'turn.start', 'turn.complete'])
    expect(w.reports()[0]).toEqual({ seq: 1, kind: 'session', session_id: 'S-1', mod_version: '0.1.0' })
    expect(w.reports().map(r => r.seq)).toEqual([1, 2, 3])
    expect(w.reports()[2]).toEqual({ seq: 3, kind: 'turn.complete', turn_id: 't1', reason: 'answer', is_aborted: false })
  })

  test('a turn-start hook waits at most 1 s for its report, and the report still lands after', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    w.answers['/report'] = 'hang'
    const pending = turnStart($, 't1')
    await w.clock.advance(999)
    let done = false
    void pending.then(() => { done = true })
    await w.clock.settle()
    expect(done).toBe(false)
    await w.clock.advance(1)
    await pending
    expect(done).toBe(true)
  })

  test('a send that keeps failing is dropped after 5 tries, and the next report goes', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    w.answers['/report'] = 'fail'
    void turnStart($, 't1')
    await w.clock.advance(1000)
    const tries = w.got.filter(g => g.path === '/report' && g.body.kind === 'turn.start').length
    expect(tries).toBe(5)
    w.answers['/report'] = {}
    await turnEnd($, 't1')
    expect(w.kinds().at(-1)).toBe('turn.complete')
  })
})

describe('main-loop turns only', () => {
  test('a subagent starting during an open main turn leaves it open and opens none; the main turn ends normally', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    await turnStart($, 't1')
    await $.turn.start({ text: 'look around', turnId: 'a-t1', agentId: 'a1' })
    // The main turn is still the open one when its Stop asks.
    await $.classic.Stop({ stop_hook_active: false, background_tasks: [], session_crons: [] })
    expect(w.got.find(g => g.path === '/ask/stop')?.body.turn_id).toBe('t1')
    await turnEnd($, 't1')
    await $.turn.complete({ answer: '', durationMs: 1, isAborted: false, turnId: 'a-t1', reason: 'answer', agentId: 'a1' })
    expect(w.kinds()).toEqual(['session', 'turn.start', 'turn.complete'])
    expect(w.reports().slice(1)).toEqual([
      { seq: 2, kind: 'turn.start', turn_id: 't1' },
      { seq: 3, kind: 'turn.complete', turn_id: 't1', reason: 'answer', is_aborted: false },
    ])
  })

  test('a subagent starting while idle opens no turn and does not stop a pending submit\'s bound', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    w.settle = () => new Promise(() => {})
    await w.line(submitLine('d1', 'hello'))
    await $.turn.start({ text: '', turnId: 'a-t1', agentId: 'a1' })
    await w.clock.advance(5_000)
    expect(w.kinds()).toEqual(['session', 'submit.bound'])
  })
})

describe('the context asks', () => {
  test('SessionStart waits for the first line, then hands back the listeners\' context', async ($: E, on) => {
    const w = world(on)
    await start($)
    w.answers['/ask/context'] = { context: 'ROSTER' }
    const r = $.classic.SessionStart({ source: 'startup' })
    await w.clock.advance(100)
    await w.hello()
    const out = await r
    expect(out.additionalContext).toEqual(['ROSTER'])
    expect(w.got.find(g => g.path === '/ask/context')?.body).toEqual({ event: 'ready', source: 'startup', session_id: expect.any(String) })
  })

  test('SessionStart gives up on the first line after 2 s', async ($: E, on) => {
    const w = world(on)
    await start($)
    const r = $.classic.SessionStart({ source: 'startup' })
    await w.clock.advance(2000)
    const out = await r
    expect(out.additionalContext ?? []).toEqual([])
    expect(w.got).toEqual([])
  })

  test('a context ask that misses listen_wait_ms + 1 s adds nothing', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    w.answers['/ask/context'] = 'hang'
    const r = $.classic.UserPromptSubmit({ prompt: 'hi' })
    await w.clock.advance(2999)
    let done = false
    void r.then(() => { done = true })
    await w.clock.settle()
    expect(done).toBe(false)
    await w.clock.advance(1)
    expect((await r).additionalContext ?? []).toEqual([])
  })
})

describe('the Stop ask', () => {
  test('a reason from the endpoint holds the turn open', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    await turnStart($, 't1')
    w.answers['/ask/stop'] = { reason: 'reply first' }
    const r = await $.classic.Stop({ stop_hook_active: false, background_tasks: [], session_crons: [] })
    expect(r.block).toBe('reply first')
    expect(w.got.find(g => g.path === '/ask/stop')?.body).toEqual({ turn_id: 't1', session_id: expect.any(String), stop_hook_active: false, task_types: [], crons: 0 })
  })

  test('an endpoint that does not answer within stop_wait_ms + 1 s lets the turn end', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    w.answers['/ask/stop'] = 'hang'
    const r = $.classic.Stop({ stop_hook_active: false })
    await w.clock.advance(3000)
    expect((await r).block).toBeUndefined()
  })
})

const N1 = 'a'.repeat(32)
const N2 = 'b'.repeat(32)
const submitLine = (delivery: string, text: string, nonce = N1, kind = 'deliver') => ({ op: 'submit', delivery, kind, text, nonce })
const closes = (w: World) => w.reports().filter(r => r.kind === 'submit.close')
const bounds = (w: World) => w.reports().filter(r => r.kind === 'submit.bound')

describe('the tie: one window per submit', () => {
  test('an idle submit: the nonce line, the path, its turn-start, then the close by resolve', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    expect(w.stateWrites).toContainEqual({ key: 'endpoint', value: { sock: SOCK } }) // at the first line
    w.settle = async () => {
      await turnStart($, 't1') // the prompt's own turn.start fires before the resolve
      return { text: 'x' }
    }
    const n = w.stateWrites.length
    await w.line(submitLine('d1', 'hello\nsecond line', N1, 'start'))
    expect(w.submits).toEqual([`moxi-turns-window: ${N1}\nhello\nsecond line`])
    expect(w.stateWrites.slice(n)).toEqual([{ key: 'endpoint', value: { sock: SOCK } }]) // again, before the submit
    expect(w.kinds()).toEqual(['session', 'turn.start', 'submit.close'])
    expect(w.reports()[2]).toMatchObject({ delivery: 'd1', end: 'resolve', reason: null })
  })

  test('a drop by the gate closes with the gate\'s reason; another plugin\'s drop reads dropped', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    w.settle = async () => ({ drop: 'moxi-turns-gate: the start was refused (stale-turn)' })
    await w.line(submitLine('d1', 'a', N1, 'start'))
    w.settle = async () => ({ drop: 'moxi-turns-gate: a moxi-turns prompt was dropped (gone)' })
    await w.line(submitLine('d2', 'b', N2))
    w.settle = async () => ({ drop: 'someone: no' })
    await w.line(submitLine('d3', 'c', 'c'.repeat(32)))
    expect(closes(w)).toMatchObject([
      { delivery: 'd1', end: 'drop', reason: 'stale-turn' },
      { delivery: 'd2', end: 'drop', reason: 'gone' },
      { delivery: 'd3', end: 'drop', reason: 'dropped' },
    ])
  })

  test('a rejected submit fails its window', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    w.settle = async () => { throw new Error('nope') }
    await w.line(submitLine('d1', 'a'))
    expect(closes(w)).toMatchObject([{ delivery: 'd1', end: 'fail' }])
  })

  test('with the gate not loaded nothing is submitted, a deliver or a start, and each closes as unsupported', async ($: E, on) => {
    const w = world(on, { gate: false })
    await attached($, w)
    await w.line(submitLine('d1', 'a', N1, 'start'))
    await w.line(submitLine('d2', 'b', N2))
    expect(w.submits).toEqual([])
    expect(closes(w)).toMatchObject([
      { delivery: 'd1', end: 'drop', reason: 'unsupported' },
      { delivery: 'd2', end: 'drop', reason: 'unsupported' },
    ])
  })

  test('a submit line with no nonce submits nothing and fails', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    await w.line({ op: 'submit', delivery: 'd1', kind: 'deliver', text: 'a' })
    expect(w.submits).toEqual([])
    expect(closes(w)).toMatchObject([{ delivery: 'd1', end: 'fail' }])
  })

  test('the bound is a report: once at 5 s idle, not before; the settle is still reported after it', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    let resolve: (v: Record<string, unknown>) => void = () => {}
    w.settle = () => new Promise(r => { resolve = r })
    await w.line(submitLine('d1', 'a'))
    await w.clock.advance(4999)
    expect(bounds(w)).toEqual([])
    await w.clock.advance(1)
    expect(bounds(w)).toMatchObject([{ delivery: 'd1', reason: 'unsettled after 5000 ms with no turn open' }])
    expect(closes(w)).toEqual([])
    await w.clock.advance(20_000)
    expect(bounds(w).length).toBe(1)
    resolve({ drop: 'moxi-turns-gate: a late prompt was dropped (late)' }) // the dialog closed
    await w.clock.settle()
    expect(closes(w)).toMatchObject([{ delivery: 'd1', end: 'drop', reason: 'late' }])
  })

  test('the bound counts only idle time: stopped by a turn.start, from zero again at its turn.complete', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    await turnStart($, 't0') // a turn runs: the submit queues behind it
    w.settle = () => new Promise(() => {})
    await w.line(submitLine('d1', 'a'))
    await w.clock.advance(20_000)
    expect(bounds(w)).toEqual([])
    await turnEnd($, 't0')
    await w.clock.advance(4999)
    expect(bounds(w)).toEqual([])
    await w.clock.advance(1)
    expect(bounds(w).length).toBe(1)
  })

  test('the bound is raised while turns\' UserPromptSubmit ask runs', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    w.answers['/ask/context'] = 'hang'
    w.settle = () => new Promise(() => {})
    await w.line(submitLine('d1', 'a'))
    await w.clock.advance(4000)
    void $.classic.UserPromptSubmit({ prompt: 'a' }) // the prompt is entering; its ask takes 3 s
    await w.clock.advance(2000) // 6 s from the call
    expect(bounds(w)).toEqual([])
    await w.clock.advance(1000) // the ask gave up; the bound starts again from zero
    await w.clock.advance(4999)
    expect(bounds(w)).toEqual([])
    await w.clock.advance(1)
    expect(bounds(w).length).toBe(1)
  })

  test('a new submit line while the last one is pending: the last reports its settle later, its bound no more', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    const pending: ((v: Record<string, unknown>) => void)[] = []
    w.settle = () => new Promise(r => { pending.push(r) })
    await w.line(submitLine('d1', 'same'))
    await w.clock.advance(5000) // d1's bound; the endpoint fails its window and hands d2
    await w.line(submitLine('d2', 'same', N2))
    expect(w.submits).toEqual([`moxi-turns-window: ${N1}\nsame`, `moxi-turns-window: ${N2}\nsame`])
    pending[0]?.({ drop: 'moxi-turns-gate: a late prompt was dropped (late)' })
    await w.clock.settle()
    await turnStart($, 't2')
    pending[1]?.({ text: 'same' })
    await w.clock.settle()
    expect(closes(w)).toMatchObject([
      { delivery: 'd1', end: 'drop', reason: 'late' },
      { delivery: 'd2', end: 'resolve' },
    ])
    expect(bounds(w)).toMatchObject([{ delivery: 'd1' }])
  })

  test('the endpoint\'s end closes the window, and nothing more is sent', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    w.settle = () => new Promise(() => {})
    await w.line(submitLine('d1', 'a'))
    const before = w.got.length
    await w.end()
    await w.clock.advance(10_000)
    await turnStart($, 't9')
    expect(w.got.length).toBe(before)
    expect(w.stateWrites).toContainEqual({ key: 'endpoint', value: null })
  })
})

describe('the other lines', () => {
  test('live: the busy subagents and the box, answered on /live', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    w.agents = [{ id: 'a', status: 'running' }, { id: 'b', status: 'completed' }]
    w.box = 'half typed'
    await w.line({ op: 'live', ask: 7 })
    expect(w.got.find(g => g.path === '/live')?.body).toEqual({ ask: 7, agents: 1, draft: true })
  })

  test('live: an unreadable subagent list is sent as null, never 0', async ($: E, on) => {
    const w = world(on)
    w.agentsFail = true
    await attached($, w)
    await w.line({ op: 'live', ask: 8 })
    expect(w.got.find(g => g.path === '/live')?.body).toEqual({ ask: 8, agents: null, draft: false })
  })

  test('agents: the list as the engine gives it, answered on /agents', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    w.agents = [
      { id: 'a', type: 'Explore', description: 'look', status: 'running', spawnedBy: 'p', name: 'n' },
      { id: 'b', type: 'teammate', description: 'mate', status: 'idle', teammateId: 'ann@crew', parentId: 'a' },
      { id: 'c', type: 'x', description: 'done', status: 'completed' },
    ]
    await w.line({ op: 'agents', ask: 3 })
    expect(w.got.find(g => g.path === '/agents')?.body).toEqual({
      ask: 3,
      entries: [
        { id: 'a', type: 'Explore', description: 'look', status: 'running' },
        { id: 'b', type: 'teammate', description: 'mate', status: 'idle', parentId: 'a', teammateId: 'ann@crew' },
        { id: 'c', type: 'x', description: 'done', status: 'completed' },
      ],
    })
  })

  test('agents: an unreadable list is sent as null, never empty', async ($: E, on) => {
    const w = world(on)
    w.agentsFail = true
    await attached($, w)
    await w.line({ op: 'agents', ask: 4 })
    expect(w.got.find(g => g.path === '/agents')?.body).toEqual({ ask: 4, entries: null })
  })

  test('append: a steer is a user row, reported', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    await w.line({ op: 'append', delivery: 'd3', text: 'also this' })
    expect(w.kinds()).not.toContain('append.failed')
    expect(w.reports().find(r => r.kind === 'append.done')).toEqual({ seq: 2, kind: 'append.done', delivery: 'd3' })
  })

  test('command: marked for the gate, run, and reported with the session id', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    await w.line({ op: 'command', request: 'r1', name: 'clear' })
    expect(w.commands).toEqual(['clear'])
    expect(w.stateWrites).toContainEqual({ key: 'command', value: { request: 'r1', name: 'clear', sock: SOCK } })
    expect(w.reports().find(r => r.kind === 'command.done')).toEqual({ seq: 2, kind: 'command.done', request: 'r1', state: 'ran', reason: null, new_session_id: 'S-1' })
  })

  test('command: with the gate not loaded nothing runs, and it reads unsupported', async ($: E, on) => {
    const w = world(on, { gate: false })
    await attached($, w)
    await w.line({ op: 'command', request: 'r1', name: 'exit' })
    expect(w.commands).toEqual([])
    expect(w.reports().find(r => r.kind === 'command.done')).toMatchObject({ request: 'r1', state: 'refused', reason: 'unsupported' })
  })

  for (const source of ['clear', 'resume', 'compact', 'startup']) {
    test(`a SessionStart (${source}) writes the mod.sock path again`, async ($: E, on) => {
      const w = world(on)
      await attached($, w)
      const n = w.stateWrites.length
      await $.classic.SessionStart({ source })
      expect(w.stateWrites.slice(n)).toContainEqual({ key: 'endpoint', value: { sock: SOCK } })
    })
  }
})

describe('hook times', () => {
  test('a hook times its own work, not what runs behind next, and the samples go out a minute on', async ($: E, on) => {
    const w = world(on, { spinMs: 80 })
    await attached($, w)
    await turnStart($, 't1')
    await turnEnd($, 't1')
    const sent = () => w.got.filter(g => g.path === '/hook-times')
    expect(sent()).toEqual([])
    await w.clock.advance(60_000)
    expect(sent().length).toBe(1)
    const samples = sent()[0]!.body.samples as { event: string; ms: number }[]
    const events = samples.map(x => x.event)
    expect(events).toContain('session.start')
    expect(events).toContain('turn.start')
    expect(events).toContain('turn.complete')
    expect(samples.find(x => x.event === 'turn.start')!.ms).toBeLessThan(80)
    expect(w.kinds()).toEqual(['session', 'turn.start', 'turn.complete'])
  })

  test('samples are dropped, not sent, once the endpoint has ended', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    await turnStart($, 't1')
    await w.end()
    await w.clock.advance(60_000)
    expect(w.got.filter(g => g.path === '/hook-times')).toEqual([])
  })
})

describe('compact and the shell stop', () => {
  const shell = (id: string) => ({ id, type: 'shell', status: 'running', description: id, command: 'sleep 600' })
  const monitor = (id: string) => ({ id, type: 'monitor', status: 'running', description: id })
  const subagent = (id: string) => ({ id, type: 'subagent', status: 'running', description: id })
  const stop = ($: E, tasks: unknown[], agent?: string) =>
    $.classic.Stop({ stop_hook_active: false, background_tasks: tasks, ...(agent === undefined ? {} : { agent_id: agent }) })
  const subagentStop = ($: E, tasks: unknown[], agent = 'a1') =>
    $.classic.SubagentStop({ stop_hook_active: false, agent_id: agent, agent_type: 'general', agent_transcript_path: '', last_assistant_message: '', background_tasks: tasks })
  const done = (w: World) => w.reports().filter(r => r.kind === 'command.done')
  const shellsOf = (w: World) => done(w).at(-1)?.shells
  const UNLISTED = { id: null, why: 'unlisted' }
  async function command(w: World, name: string, stopShells = true, request = 'r1') {
    await w.line({ op: 'command', request, name, stop_shells: stopShells })
  }
  /** A tool call the test holds open; `end()` lets it finish. */
  function hold($: E, w: World, key: string, agentId?: string) {
    const call = $.tool.call({ tool: 'Bash', command: `hold:${key}`, ...(agentId === undefined ? {} : { agentId }) })
    return {
      call,
      async end() {
        w.holds[`hold:${key}`]!()
        await call
        await w.clock.settle()
      },
    }
  }

  test('compact runs as clear does, and without stop_shells reports no shells', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    await command(w, 'compact', false)
    expect(w.commands).toEqual(['compact'])
    expect(w.stateWrites).toContainEqual({ key: 'command', value: { request: 'r1', name: 'compact', sock: SOCK } })
    expect(done(w)).toEqual([{ seq: 2, kind: 'command.done', request: 'r1', state: 'ran', reason: null, new_session_id: 'S-1' }])
    expect(w.stopCalls).toEqual([])
  })

  test('a vouched list: each shell and Monitor is checked, then stopped after the command, by the runner itself', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    await stop($, [shell('s1'), monitor('m1'), subagent('a1'), { id: 'w1', type: 'workflow', status: 'running', description: '' }])
    await command(w, 'clear')
    expect(w.order).toEqual(['run clear', 'stop s1', 'stop m1'])
    expect(w.stopCalls.map(c => c.origin)).toEqual(['moxi-turns', 'moxi-turns'])
    expect(done(w)).toEqual([{ seq: 2, kind: 'command.done', request: 'r1', state: 'ran', reason: null, new_session_id: 'S-1', shells: { stopped: ['s1', 'm1'], left: [], complete: true } }])
    // The runner's own TaskStop calls set no mark: a second command still vouches.
    await command(w, 'compact', true, 'r2')
    expect(shellsOf(w)).toEqual({ stopped: ['s1', 'm1'], left: [], complete: true })
  })

  test('a vouched empty list stops nothing and reads complete', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    await stop($, [subagent('a1')])
    await command(w, 'compact')
    expect(w.commands).toEqual(['compact'])
    expect(shellsOf(w)).toEqual({ stopped: [], left: [], complete: true })
  })

  for (const verdict of ['ask', 'deny']) {
    test(`a TaskStop check answering ${verdict} refuses unsupported and runs nothing`, async ($: E, on) => {
      const w = world(on)
      await attached($, w)
      await stop($, [shell('s1'), shell('s2')])
      w.checks.s2 = verdict
      await command(w, 'clear')
      expect(w.commands).toEqual([])
      expect(w.stopCalls).toEqual([])
      expect(done(w)).toEqual([{ seq: 2, kind: 'command.done', request: 'r1', state: 'refused', reason: 'unsupported', new_session_id: null }])
    })
  }

  test('nothing is stopped when the gate withdraws the command or it fails', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    await stop($, [shell('s1')])
    w.commandAnswer = async name => ({ text: `moxi-turns-gate: /${name} was refused (busy-turn)` })
    await command(w, 'clear')
    w.commandAnswer = async () => { throw new Error('no compaction') }
    await command(w, 'compact', true, 'r2')
    expect(w.stopCalls).toEqual([])
    expect(done(w).map(d => [d.state, d.reason, 'shells' in d])).toEqual([['refused', 'busy-turn', false], ['failed', expect.any(String), false]])
  })

  test('a stop not back within 10 s is left timeout; a deny, an error and an ended task read as such', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    await stop($, [shell('s1'), shell('s2'), shell('s3'), shell('s4'), monitor('m1')])
    w.stops.s1 = 'hang'                                   // a hook's dialog holds it open
    w.stops.s2 = { deny: 'not now' }
    w.stops.s3 = { isError: true, text: 'Task s3 is not running (status: completed)', result: 'x' }
    w.stops.s4 = { isError: true, text: 'No task found with ID: s4', result: 'x' }
    const line = command(w, 'compact')
    await w.clock.advance(9_999)
    expect(done(w)).toEqual([])
    await w.clock.advance(1)
    await line
    expect(shellsOf(w)).toEqual({
      stopped: ['m1'],
      left: [{ id: 's1', why: 'timeout' }, { id: 's2', why: 'denied' }, { id: 's4', why: 'No task found with ID: s4' }],
      complete: true,
    })
  })

  test('with stop_after_clear off a clear stops nothing and names each listed shell', async ($: E, on) => {
    const w = world(on)
    await start($)
    await w.hello({ stop_after_clear: false })
    await stop($, [shell('s1'), monitor('m1')])
    await command(w, 'clear')
    expect(w.stopCalls).toEqual([])
    expect(shellsOf(w)).toEqual({ stopped: [], left: [{ id: 's1', why: 'not-stopped-after-clear' }, { id: 'm1', why: 'not-stopped-after-clear' }], complete: true })
    await command(w, 'compact', true, 'r2')                // a compaction still stops them
    expect(w.stopCalls.map(c => c.taskId)).toEqual(['s1', 'm1'])
  })

  describe('a list turns cannot vouch for is reported, never read as empty', () => {
    test('a fresh runner with no Stop seen', async ($: E, on) => {
      const w = world(on)
      await attached($, w)
      await command(w, 'clear')
      expect(shellsOf(w)).toEqual({ stopped: [], left: [UNLISTED], complete: false })
    })

    test('a turn with a tool call that ends with no Stop (an interrupt)', async ($: E, on) => {
      const w = world(on)
      await attached($, w)
      await stop($, [shell('s1')])
      await turnStart($, 't1')
      await $.tool.call({ tool: 'Bash', command: 'ls' })
      await turnEnd($, 't1', 'aborted')
      await command(w, 'clear')
      expect(shellsOf(w)).toEqual({ stopped: ['s1'], left: [UNLISTED], complete: false })
    })

    test('a Stop that blocks, then more tool calls with no second Stop', async ($: E, on) => {
      const w = world(on)
      await attached($, w)
      await turnStart($, 't1')
      w.answers['/ask/stop'] = { reason: 'reply first' }
      expect((await stop($, [shell('s1')])).block).toBe('reply first')
      await $.tool.call({ tool: 'Bash', command: 'run_in_background' })
      await command(w, 'compact')
      expect(shellsOf(w)).toEqual({ stopped: ['s1'], left: [UNLISTED], complete: false })
    })

    test('a subagent\'s call after the main turn\'s Stop, with no SubagentStop', async ($: E, on) => {
      const w = world(on)
      await attached($, w)
      await stop($, [shell('s1')])
      await $.tool.call({ tool: 'Bash', command: 'ls', agentId: 'a1' })
      await command(w, 'clear')
      expect(shellsOf(w)?.complete).toBe(false)
      // Its SubagentStop, with nothing in flight, vouches again.
      await subagentStop($, [shell('s1'), shell('s2')])
      await command(w, 'compact', true, 'r2')
      expect(shellsOf(w)).toEqual({ stopped: ['s1', 's2'], left: [], complete: true })
    })

    test('a Stop seen while another loop\'s call is in flight clears nothing; a later one does', async ($: E, on) => {
      const w = world(on)
      await attached($, w)
      await stop($, [shell('s1')])
      const h = hold($, w, 'sub', 'a1')
      await w.clock.settle()
      await stop($, [shell('s1'), shell('s2')])            // a1's call can still start a shell
      await command(w, 'clear')
      expect(shellsOf(w)).toEqual({ stopped: ['s1'], left: [UNLISTED], complete: false })
      await h.end()
      await stop($, [shell('s3')])
      await command(w, 'compact', true, 'r2')
      expect(shellsOf(w)).toEqual({ stopped: ['s3'], left: [], complete: true })
    })

    test('a ! command moved to the background marks the list; a later Stop with no call in flight clears it', async ($: E, on) => {
      const w = world(on)
      await attached($, w)
      await stop($, [shell('s1')])
      // Ctrl+B on `! sleep 1600`: no tool call, only its three rows.
      await commandRow($, '<local-command-caveat>The command below was run directly in Claude Code</local-command-caveat>')
      await commandRow($, '<bash-input>sleep 1600</bash-input>')
      await commandRow($, '<bash-stdout>Command was manually backgrounded by user with ID: b1</bash-stdout>')
      await command(w, 'clear')
      expect(shellsOf(w)).toEqual({ stopped: ['s1'], left: [UNLISTED], complete: false })
      await stop($, [shell('b1')])
      await command(w, 'compact', true, 'r2')
      expect(shellsOf(w)).toEqual({ stopped: ['b1'], left: [], complete: true })
    })

    test('any command row marks the list, one of a slash command too', async ($: E, on) => {
      const w = world(on)
      await attached($, w)
      await stop($, [shell('s1')])
      await commandRow($, '<command-name>/context</command-name>')
      await command(w, 'clear')
      expect(shellsOf(w)?.complete).toBe(false)
    })

    test('the rows of the runner\'s own command mark nothing, but a ! command\'s row among them does', async ($: E, on) => {
      const w = world(on)
      await attached($, w)
      await stop($, [shell('s1')])
      // The engine appends a slash command's rows as it runs.
      let rows: string[] = ['<command-name>/compact</command-name>', '<local-command-stdout>Compacted</local-command-stdout>']
      w.commandAnswer = async name => {
        for (const text of rows) await commandRow($, text)
        return { text: `ran ${name}` }
      }
      await command(w, 'compact')
      expect(shellsOf(w)).toEqual({ stopped: ['s1'], left: [], complete: true })
      // Once the command is done, a row marks again.
      rows = []
      await commandRow($, '<command-name>/context</command-name>')
      await command(w, 'compact', true, 'r2')
      expect(shellsOf(w)?.complete).toBe(false)
      await stop($, [shell('s1')])
      rows = ['<bash-input>sleep 1600</bash-input>']
      await command(w, 'compact', true, 'r3')
      expect(shellsOf(w)).toEqual({ stopped: ['s1'], left: [UNLISTED], complete: false })
    })

    test('a turn with no tool call keeps the list vouched for', async ($: E, on) => {
      const w = world(on)
      await attached($, w)
      await stop($, [shell('s1')])
      await turnStart($, 't2')
      await turnEnd($, 't2', 'aborted')
      await command(w, 'clear')
      expect(shellsOf(w)).toEqual({ stopped: ['s1'], left: [], complete: true })
    })

    test('a later Stop with nothing in flight clears the mark, an agent\'s Stop too', async ($: E, on) => {
      const w = world(on)
      await attached($, w)
      await $.tool.call({ tool: 'Bash', command: 'ls' })
      await stop($, [monitor('m1')], 'a1')
      await command(w, 'compact')
      expect(shellsOf(w)).toEqual({ stopped: ['m1'], left: [], complete: true })
    })

    test('an aborted turn drops the main loop\'s stuck call, never another loop\'s', async ($: E, on) => {
      const w = world(on)
      await attached($, w)
      await turnStart($, 't1')
      hold($, w, 'main')                                      // its frame never returns
      const sub = hold($, w, 'sub', 'a1')
      await w.clock.settle()
      await turnEnd($, 't1', 'aborted')                      // the person's interrupt
      await stop($, [shell('s1')])                           // a1's call is still in flight
      await command(w, 'clear')
      expect(shellsOf(w)?.complete).toBe(false)
      await sub.end()
      await stop($, [shell('s1')])
      await command(w, 'compact', true, 'r2')
      expect(shellsOf(w)).toEqual({ stopped: ['s1'], left: [], complete: true })
    })
  })

  test('the tool.call hook passes the call on and times only its own work', async ($: E, on) => {
    const w = world(on, { spinMs: 80 })
    await attached($, w)
    const r = await $.tool.call({ tool: 'Bash', command: 'ls' })
    expect(r.text).toBe('ok')
    expect(w.toolCalls).toEqual([{ tool: 'Bash', origin: 'engine' }])
    await w.clock.advance(60_000)
    const samples = w.got.filter(g => g.path === '/hook-times').flatMap(g => g.body.samples as { event: string; ms: number }[])
    const call = samples.find(x => x.event === 'tool.call')
    expect(call).toBeDefined()
    expect(call!.ms).toBeLessThan(80)
  })
})
