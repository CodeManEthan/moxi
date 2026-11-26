// The runner's guards: the shared matching cases, the data-only refusal, the ask and its
// answer, a trip that fails or overruns, the catch, the prompt list each ask carries, and
// the ask step at a permission request.

import { describe, expect, test } from 'claude-code/testing'
import { ROOT_ARGV as ROOT, world } from './world'
import type { World } from './world'
import { NO_TABLE_ASK_DENY, NO_TABLE_DENY, NO_TABLE_NESTED_DENY, NO_TABLE_REMOVAL_DENY, catchRefuses, catchRefusesAsk, decide, globRegex, noTableAskDeny, noTableDeny, noTableRefuses, parseLastTable, parseTable } from '../hooks/register'
import TABLE from './guard-cases'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type E = any
type Json = Record<string, unknown>

type CaseGuard = { id: string; calls: Record<string, string[]>; deadline_ms: number } & Json
type Case = { name: string; harness: string; tool: string; agent: string | null; input: Json; on: string[]; covering: string[]; decision: string; deny?: string; ask?: string[] }
/** The shared case table, which the Python matcher runs too. */
const CASES = TABLE as unknown as { max_wait_ms: number; guards: CaseGuard[]; cases: Case[] }

/** A harness's table as the endpoint sends it: each guard on, with calls for it. */
function tableFor(harness: string, on: readonly string[]): Json[] {
  return CASES.guards
    .filter(g => on.includes(g.id) && g.calls[harness] !== undefined)
    .map(g => ({ ...g, calls: g.calls[harness], deadline_ms: Math.min(g.deadline_ms, CASES.max_wait_ms) }))
}

/** One entry for a test's own table. */
function entry(id: string, fields: Json = {}): Json {
  return { id, calls: ['Bash'], who: 'any', match: null, match_flags: '', judge: true, deny: '', wants: [], on_error: 'allow', deadline_ms: 500, ...fields }
}

const start = ($: E) => $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
const turnStart = ($: E, turnId: string, text = '') => $.turn.start({ text, turnId })
const turnEnd = ($: E, turnId: string) => $.turn.complete({ answer: '', durationMs: 1, isAborted: false, turnId, reason: 'answer' })
const asks = (w: World) => w.got.filter(g => g.path === '/ask/guard').map(g => g.body)
const ran = (w: World) => w.toolCalls.map(c => c.tool)

async function attached($: E, w: World, table: Json[]): Promise<void> {
  await start($)
  await w.hello({ guards: table })
}

describe('the shared matching cases', () => {
  for (const c of CASES.cases) {
    test(`${c.harness}: ${c.name}`, () => {
      const table = parseTable(tableFor(c.harness, c.on))!
      expect(table.length).toBe(tableFor(c.harness, c.on).length)
      const d = decide(table, c.tool, c.agent, c.input as Json)
      expect(d.covering.map(g => g.id)).toEqual(c.covering)
      const decision = d.deny !== null ? 'deny' : d.ask.length > 0 ? 'ask' : 'next'
      expect(decision).toBe(c.decision)
      if (c.decision === 'deny') expect(d.deny!.deny).toBe(c.deny!)
      if (c.decision === 'ask') expect(d.ask.map(g => g.id)).toEqual(c.ask!)
    })
  }

  // The same cases through the runner's hook, on Claude's table, as the engine calls it.
  for (const c of CASES.cases.filter(c => c.harness === 'claude')) {
    test(`through the hook: ${c.name}`, async ($: E, on) => {
      const w = world(on)
      await attached($, w, tableFor('claude', c.on))
      const r = await $.tool.call({ tool: c.tool, tool_use_id: 'u1', ...(c.input as Json), ...(c.agent === null ? {} : { agentId: c.agent }) })
      if (c.decision === 'deny') {
        expect(r.deny).toBe(c.deny)
        expect(ran(w)).toEqual([])
      } else {
        expect(r.deny).toBeUndefined()
        expect(ran(w)).toEqual([c.tool])
      }
      expect(asks(w).map(b => b.guards)).toEqual(c.decision === 'ask' ? [c.ask] : [])
    })
  }

  test('a pattern is the whole name, and only * is special', () => {
    expect(globRegex('mcp__a.b__*').test('mcp__a.b__x')).toBe(true)
    expect(globRegex('mcp__a.b__*').test('mcp__aXb__x')).toBe(false)
    expect(globRegex('a(b)+[c]?').test('a(b)+[c]?')).toBe(true)
    expect(globRegex('Bash').test('BashOutput')).toBe(false)
    expect(globRegex('*').test('')).toBe(true)
  })
})

describe('the table', () => {
  test('the hello carries it, and a guards line replaces it', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.a', { judge: false, deny: 'no a' })])
    expect((await $.tool.call({ tool: 'Bash', command: 'ls' })).deny).toBe('no a')
    await w.line({ op: 'guards', table: [entry('t.b', { calls: ['Read'], judge: false, deny: 'no b' })] })
    expect((await $.tool.call({ tool: 'Bash', command: 'ls' })).deny).toBeUndefined()
    expect((await $.tool.call({ tool: 'Read', file_path: '/x' })).deny).toBe('no b')
  })

  test('a guards line that is not a list keeps the table; entries that are not entries are left out', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.a', { judge: false, deny: 'no a' })])
    await w.line({ op: 'guards', table: 'nonsense' })
    expect((await $.tool.call({ tool: 'Bash', command: 'ls' })).deny).toBe('no a')
    await w.line({ op: 'guards', table: [{ id: 't.x' }, entry('t.b', { who: 'sometimes' }), entry('t.c', { judge: false, deny: 'no c' })] })
    expect((await $.tool.call({ tool: 'Bash', command: 'ls' })).deny).toBe('no c')
  })

  test('an endpoint at leg 3, which has no ask step, is one the runner does not speak', async ($: E, on) => {
    const w = world(on)
    await start($)
    await w.line({ op: 'hello', leg: 3, mod_sock: '/x/mod.sock', stop_wait_ms: 2000, listen_wait_ms: 2000 })
    await turnStart($, 't1')
    expect(w.got).toEqual([])
  })
})

describe('the data-only refusal', () => {
  test('refuses with no trip and never runs the tool; the call leaves the in-flight count', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.nested', { calls: ['Agent'], who: 'agents', judge: false, deny: 'subagents may not start subagents' })])
    const r = await $.tool.call({ tool: 'Agent', tool_use_id: 'u1', prompt: 'go', agentId: 'a1' })
    expect(r).toEqual({ deny: 'subagents may not start subagents' })
    expect(ran(w)).toEqual([])
    expect(asks(w)).toEqual([])
    // Nothing is left in flight: a Stop vouches for its list, and a command reads complete.
    await $.classic.Stop({ stop_hook_active: false, background_tasks: [{ id: 's1', type: 'shell', status: 'running', description: '' }] })
    await w.line({ op: 'command', request: 'r1', name: 'compact', stop_shells: true })
    expect(w.reports().find(x => x.kind === 'command.done')?.shells).toEqual({ stopped: ['s1'], left: [], complete: true })
  })

  test('the runner\'s own TaskStop calls are never judged', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.all', { calls: ['*'], judge: false, deny: 'nothing runs' })])
    await $.classic.Stop({ stop_hook_active: false, background_tasks: [{ id: 's1', type: 'shell', status: 'running', description: '' }] })
    await w.line({ op: 'command', request: 'r1', name: 'compact', stop_shells: true })
    expect(w.stopCalls.map(c => c.taskId)).toEqual(['s1'])
    expect(w.reports().find(x => x.kind === 'command.done')?.shells).toMatchObject({ stopped: ['s1'] })
  })
})

describe('the ask', () => {
  test('the request: the call less its reserved keys, the ids in table order, no prompts unless wanted', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.one'), entry('t.two', { deadline_ms: 900 })])
    await $.tool.call({ tool: 'Bash', tool_use_id: 'u7', command: 'ls', description: 'list', agentId: 'a2' })
    expect(asks(w)).toEqual([{ call: { tool: 'Bash', tool_use_id: 'u7', input: { command: 'ls', description: 'list' }, agentId: 'a2', cwd: '/work' }, guards: ['t.one', 't.two'] }])
    await $.tool.call({ tool: 'Bash', tool_use_id: 'u8', command: 'ls' })
    expect(asks(w)[1]!.call).toEqual({ tool: 'Bash', tool_use_id: 'u8', input: { command: 'ls' }, agentId: null, cwd: '/work' })
  })

  test('a deny in the answer refuses, and the tool never runs', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.one')])
    w.answers['/ask/guard'] = { ok: true, deny: 'not that', lines: ['ignored'], broken: [] }
    expect(await $.tool.call({ tool: 'Bash', command: 'rm x' })).toEqual({ deny: 'not that' })
    expect(ran(w)).toEqual([])
  })

  test('an allow runs the tool, and its lines then the break lines follow the result', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.one'), entry('t.two')])
    w.answers['/ask/guard'] = { ok: true, lines: ['you are near the ceiling'], broken: ['[turns] guard t.two could not check this call (it raised); it ran unchecked.'] }
    const r = await $.tool.call({ tool: 'Bash', command: 'ls' })
    expect(r.text).toBe('ok')
    expect(r.context).toEqual(['you are near the ceiling', '[turns] guard t.two could not check this call (it raised); it ran unchecked.'])
    expect(ran(w)).toEqual(['Bash'])
  })

  test('a clean answer adds nothing', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.one')])
    w.answers['/ask/guard'] = { ok: true, lines: [], broken: [] }
    const r = await $.tool.call({ tool: 'Bash', command: 'ls' })
    expect(r.context ?? []).toEqual([])
  })

  test('the trip is timed as the event guard, and the tool.call time leaves it out', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.one')])
    w.answers['/ask/guard'] = async () => {
      const t0 = performance.now()
      while (performance.now() - t0 < 80) { /* the endpoint judging */ }
      return { ok: true, lines: [], broken: [] }
    }
    await $.tool.call({ tool: 'Bash', command: 'ls' })
    await w.clock.advance(60_000)
    const samples = w.got.filter(g => g.path === '/hook-times').flatMap(g => g.body.samples as { event: string; ms: number }[])
    expect(samples.find(x => x.event === 'guard')!.ms).toBeGreaterThanOrEqual(80)
    expect(samples.find(x => x.event === 'tool.call')!.ms).toBeLessThan(80)
  })
})

describe('the call\'s directory', () => {
  test('each ask carries the directory read in its own hook', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.one')])
    w.cwd = '/work/moved'
    await $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: 'rm -rf build' })
    w.cwd = '/work/.worktrees/agent-a1'
    await $.tool.call({ tool: 'Bash', tool_use_id: 'u2', command: 'rm -rf build', agentId: 'a1' })
    expect(asks(w).map(b => [(b.call as Json).agentId, (b.call as Json).cwd])).toEqual([
      [null, '/work/moved'],
      ['a1', '/work/.worktrees/agent-a1'],
    ])
    expect(w.cwdReads).toBe(2)
  })

  test('no ask, no read: a call nothing covers, a data-only refusal, the runner\'s own', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.one', { match: { command: '^rm' } }), entry('t.nested', { calls: ['Agent'], who: 'agents', judge: false, deny: 'no' })])
    await $.tool.call({ tool: 'Bash', command: 'ls' })
    await $.tool.call({ tool: 'Read', file_path: '/x' })
    expect((await $.tool.call({ tool: 'Agent', prompt: 'go', agentId: 'a1' })).deny).toBe('no')
    await $.classic.Stop({ stop_hook_active: false, background_tasks: [{ id: 's1', type: 'shell', status: 'running', description: '' }] })
    await w.line({ op: 'command', request: 'r1', name: 'compact', stop_shells: true })
    expect(w.stopCalls.map(c => c.taskId)).toEqual(['s1'])
    expect(asks(w)).toEqual([])
    expect(w.cwdReads).toBe(0)
  })

  for (const [name, given] of [['a read that rejects', 'fail'], ['a read that gives no string', 42], ['an empty read', '']] as const) {
    test(`${name} sends the ask with no cwd, and the guard still judges`, async ($: E, on) => {
      const w = world(on)
      await attached($, w, [entry('t.one', { on_error: 'deny' })])
      w.cwd = given
      w.answers['/ask/guard'] = { ok: true, deny: 'judged', lines: [], broken: [] }
      expect(await $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: 'rm -rf build' })).toEqual({ deny: 'judged' })
      expect(asks(w)).toHaveLength(1)
      expect('cwd' in (asks(w)[0]!.call as Json)).toBe(false)
      expect(ran(w)).toEqual([])
    })
  }

  test('a read that never answers counts against the trip\'s bound', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.one', { deadline_ms: 300 })])
    w.cwd = 'hang'
    let r: E
    const call = $.tool.call({ tool: 'Bash', command: 'rm x' }).then((x: E) => { r = x })
    await w.clock.advance(1299)
    expect(r).toBeUndefined()
    await w.clock.advance(1)
    await call
    expect(asks(w)).toEqual([])
    expect(r.context).toEqual(['[turns] guard t.one could not check this call (no answer within 1300 ms); it ran unchecked.'])
  })
})

describe('a trip that fails or overruns', () => {
  test('a failed trip lets the call through with one line per asked guard', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.one'), entry('t.two')])
    w.answers['/ask/guard'] = 'fail'
    const r = await $.tool.call({ tool: 'Bash', command: 'ls' })
    expect(ran(w)).toEqual(['Bash'])
    expect(r.context).toHaveLength(2)
    expect(r.context[0]).toMatch(/^\[turns\] guard t\.one could not check this call \(the trip failed: .+\); it ran unchecked\.$/)
    expect(r.context[1]).toMatch(/^\[turns\] guard t\.two could not check this call \(the trip failed: .+\); it ran unchecked\.$/)
  })

  test('a failed trip refuses when an asked guard declared on_error deny', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.one'), entry('t.push', { on_error: 'deny' })])
    w.answers['/ask/guard'] = 'fail'
    const r = await $.tool.call({ tool: 'Bash', command: 'git push' })
    expect(r.deny).toMatch(/^turns could not check this call with guard t\.push \(the trip failed: .+\)$/)
    expect(ran(w)).toEqual([])
  })

  test('the bound is the largest deadline plus 1 s; past it the trip is broken', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.one', { deadline_ms: 300 }), entry('t.two', { deadline_ms: 800 })])
    w.answers['/ask/guard'] = 'hang'
    let r: E
    const call = $.tool.call({ tool: 'Bash', command: 'ls' }).then((x: E) => { r = x })
    await w.clock.advance(1799)
    expect(r).toBeUndefined()
    await w.clock.advance(1)
    await call
    expect(r.context).toEqual([
      '[turns] guard t.one could not check this call (no answer within 1800 ms); it ran unchecked.',
      '[turns] guard t.two could not check this call (no answer within 1800 ms); it ran unchecked.',
    ])
  })

  test('with the endpoint gone each judge breaks; a data-only guard still refuses', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.one'), entry('t.cal', { calls: ['mcp__*Calendar__*'], judge: false, deny: 'no calendar' })])
    await w.end()
    const r = await $.tool.call({ tool: 'Bash', command: 'ls' })
    expect(r.context).toEqual(["[turns] guard t.one could not check this call (turns' endpoint is not running); it ran unchecked."])
    expect((await $.tool.call({ tool: 'mcp__x_Calendar__list' })).deny).toBe('no calendar')
  })

  test('a match that JavaScript can\'t compile breaks each call the guard\'s patterns take', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.py', { match: { command: '(?P<w>rm)' } }), entry('t.pyd', { calls: ['Read'], match: { file_path: '(?P<w>x)' }, on_error: 'deny' })])
    const r = await $.tool.call({ tool: 'Bash', command: 'ls' })
    expect(ran(w)).toEqual(['Bash'])
    expect(r.context).toHaveLength(1)
    expect(r.context[0]).toMatch(/^\[turns\] guard t\.py could not check this call \(its match on command does not compile here: .*\); it ran unchecked\.$/)
    expect((await $.tool.call({ tool: 'Read', file_path: '/a' })).deny).toMatch(/^turns could not check this call with guard t\.pyd \(its match on file_path/)
    expect(asks(w)).toEqual([])
  })
})

describe('the catch', () => {
  const table = parseTable([
    entry('t.soft', { match: { command: 'rm' } }),
    entry('t.hard', { calls: ['Bash'], match: { command: '^git push' }, on_error: 'deny' }),
    entry('t.agent', { calls: ['Agent'], who: 'agents', on_error: 'deny' }),
  ])!

  test('refuses only when a covering guard declared on_error deny', () => {
    expect(catchRefuses(table, { tool: 'Bash', command: 'git push origin' })).toBe(true)
    expect(catchRefuses(table, { tool: 'Bash', command: 'rm x' })).toBe(false)
    expect(catchRefuses(table, { tool: 'Bash', command: 'ls' })).toBe(false)
    expect(catchRefuses(table, { tool: 'Agent', prompt: 'go' })).toBe(false)
    expect(catchRefuses(table, { tool: 'Agent', prompt: 'go', agentId: 'a1' })).toBe(true)
    expect(catchRefuses([], { tool: 'Bash', command: 'git push' })).toBe(false)
  })
})

describe('the prompt list', () => {
  const ASK = entry('t.ask', { calls: ['AskUserQuestion'], who: 'main', wants: ['prompts'] })
  /** What the next AskUserQuestion's trip carries as the turn's prompts. */
  async function promptsNow($: E, w: World): Promise<unknown> {
    const n = asks(w).length
    await $.tool.call({ tool: 'AskUserQuestion', questions: [] })
    return (asks(w)[n]!.call as Json).prompts
  }
  const composer = { kind: 'composer' }
  /** A typed prompt: the engine's `prompt.submit`; `turn` opens the turn inside its `next`. */
  function typed($: E, w: World, text: string, options: { turnId?: string; origin?: Json; turn?: { id: string; text?: string }; isMeta?: true } = {}) {
    w.settle = async t => {
      if (options.turn !== undefined) await turnStart($, options.turn.id, options.turn.text ?? t)
      return { text: t }
    }
    return $.prompt.submit({ text, origin: options.origin ?? composer, wait: false, ...(options.turnId === undefined ? {} : { turnId: options.turnId }), ...(options.isMeta ? { isMeta: true } : {}) })
  }

  test('typed at idle: in flight when its turn starts, it ties by text', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [ASK])
    await typed($, w, 'a question for you', { turn: { id: 't1' } })
    expect(await promptsNow($, w)).toEqual([{ origin: 'composer', text: 'a question for you' }])
  })

  test('the positional launch prompt, seen before the endpoint\'s first line, ties the same way', async ($: E, on) => {
    const w = world(on)
    await start($)
    await typed($, w, 'launch question', { turn: { id: 't1' } })
    await w.hello({ guards: [ASK] })
    expect(await promptsNow($, w)).toEqual([{ origin: 'composer', text: 'launch question' }])
  })

  test('only a guard that wants prompts gets them', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.plain', { calls: ['AskUserQuestion'] })])
    await typed($, w, 'question', { turn: { id: 't1' } })
    await $.tool.call({ tool: 'AskUserQuestion', questions: [] })
    expect('prompts' in (asks(w)[0]!.call as Json)).toBe(false)
  })

  test('typed during a turn\'s last model step: it joins that turn, then opens the next by text', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [ASK])
    await typed($, w, 'first', { turn: { id: 't1' } })
    await typed($, w, 'a question, typed late', { turnId: 't1' }) // queued: resolves, opens nothing yet
    expect(await promptsNow($, w)).toEqual([{ origin: 'composer', text: 'first' }, { origin: 'composer', text: 'a question, typed late' }])
    await turnEnd($, 't1')
    await turnStart($, 't2', 'a question, typed late')
    expect(await promptsNow($, w)).toEqual([{ origin: 'composer', text: 'a question, typed late' }])
  })

  test('typed during a tool call: it joins its turn, and the next main turn (a delivered one) drops it', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [ASK])
    await turnStart($, 't1', 'go')
    await typed($, w, 'question, mid-call', { turnId: 't1' })
    expect(await promptsNow($, w)).toEqual([{ origin: 'composer', text: 'question, mid-call' }])
    await turnEnd($, 't1')
    w.settle = async t => {
      await turnStart($, 't2', t) // turns' own deliver: its turn starts inside the runner's submit
      return { text: t }
    }
    await w.line({ op: 'submit', delivery: 'd1', kind: 'deliver', text: 'a message', nonce: 'n'.repeat(32) })
    expect(await promptsNow($, w)).toEqual([])
  })

  test('typed during a tool call: a plugin\'s turn next drops it too, and counts only itself', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [ASK])
    await turnStart($, 't1', 'go')
    await typed($, w, 'question, mid-call', { turnId: 't1' })
    await turnEnd($, 't1')
    await typed($, w, 'from a plugin', { origin: { kind: 'plugin', name: 'other' }, turn: { id: 't2', text: 'The other plugin sent a message: from a plugin' } })
    expect(await promptsNow($, w)).toEqual([{ origin: 'plugin', text: 'from a plugin' }])
  })

  test('another plugin\'s prompt: no text match, so the one in flight without a turnId is its turn\'s', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [ASK])
    await typed($, w, 'ping', { origin: { kind: 'plugin', name: 'other' }, turn: { id: 't1', text: 'The other plugin sent a message: ping' } })
    expect(await promptsNow($, w)).toEqual([{ origin: 'plugin', text: 'ping' }])
  })

  test('an idle prompt a hook beneath rewrote still opens its turn as the one in flight', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [ASK])
    w.settle = async () => {
      await turnStart($, 't1', 'rewritten question')
      return { text: 'rewritten question' }
    }
    await $.prompt.submit({ text: 'question', origin: composer, wait: false })
    expect(await promptsNow($, w)).toEqual([{ origin: 'composer', text: 'question' }])
  })

  test('turns\' own deliver: the runner\'s hook never sees it, so its turn\'s list is empty', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [ASK])
    w.settle = async t => {
      await turnStart($, 't1', t)
      return { text: t }
    }
    await w.line({ op: 'submit', delivery: 'd1', kind: 'deliver', text: 'a question from a peer', nonce: 'n'.repeat(32) })
    expect(w.submits).toHaveLength(1)
    expect(await promptsNow($, w)).toEqual([])
  })

  test('a prompt that enters no turn: a drop, a rejection, or one that resolves with none, leaves the set', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [ASK])
    w.settle = async () => ({ drop: 'not now' })
    await $.prompt.submit({ text: 'dropped question', origin: composer, wait: false })
    w.settle = async () => { throw new Error('no') }
    await $.prompt.submit({ text: 'rejected question', origin: composer, wait: false }).catch(() => {})
    w.settle = async t => ({ text: t }) // a command that runs with no turn
    await $.prompt.submit({ text: '/context question', origin: composer, wait: false })
    // A turn nobody submitted through this hook (a task notification) takes none of them.
    await turnStart($, 't1', 'a task finished')
    expect(await promptsNow($, w)).toEqual([])
  })

  test('a prompt joined to the running turn and then dropped leaves its list', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [ASK])
    await turnStart($, 't1', 'go')
    w.settle = async () => ({ drop: 'a hook refused it' })
    await $.prompt.submit({ text: 'question', origin: composer, wait: false, turnId: 't1' })
    expect(await promptsNow($, w)).toEqual([])
  })

  test('a prompt with isMeta is not recorded', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [ASK])
    await typed($, w, 'meta question', { isMeta: true, turn: { id: 't1' } })
    expect(await promptsNow($, w)).toEqual([])
  })

  test('a subagent\'s turn leaves the list and the waiting set alone', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [ASK])
    await turnStart($, 't1', 'go')
    await typed($, w, 'question', { turnId: 't1' })
    await $.turn.start({ text: 'look around', turnId: 'a-t1', agentId: 'a1' })
    await $.turn.complete({ answer: '', durationMs: 1, isAborted: false, turnId: 'a-t1', reason: 'answer', agentId: 'a1' })
    expect(await promptsNow($, w)).toEqual([{ origin: 'composer', text: 'question' }])
    await turnEnd($, 't1')
    await turnStart($, 't2', 'question') // it was still waiting: typed in the last step
    expect(await promptsNow($, w)).toEqual([{ origin: 'composer', text: 'question' }])
  })

  test('accepted, fails closed: one turn opened with two queued prompts has an empty list', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [ASK])
    await turnStart($, 't1', 'go')
    await typed($, w, 'one question', { turnId: 't1' })
    await typed($, w, 'two', { turnId: 't1' })
    await turnEnd($, 't1')
    await turnStart($, 't2', 'one question\ntwo')
    expect(await promptsNow($, w)).toEqual([])
  })

  test('accepted, fails closed: a late prompt queued behind a peer\'s message loses its own turn', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [ASK])
    await turnStart($, 't1', 'go')
    await typed($, w, 'a question, typed late', { turnId: 't1' })
    await turnEnd($, 't1')
    await typed($, w, 'a peer says hi', { origin: { kind: 'peer' }, turn: { id: 't2' } })
    expect(await promptsNow($, w)).toEqual([{ origin: 'peer', text: 'a peer says hi' }])
    await turnEnd($, 't2')
    await turnStart($, 't3', 'a question, typed late')
    expect(await promptsNow($, w)).toEqual([])
  })

  describe('while one of the runner\'s own submits is in flight', () => {
    const NONCE = 'n'.repeat(32)

    test('a deliver whose turn starts while a typed prompt is in flight: no fallback, an empty list', async ($: E, on) => {
      const w = world(on)
      await attached($, w, [ASK])
      // The typed prompt's next(e) is held open; the delivery's turn starts inside its own submit.
      let enterTyped: (v: Json) => void = () => {}
      w.settle = t => (t.startsWith('moxi-turns-window: ')
        ? turnStart($, 't1', t).then(() => ({ text: t }))
        : new Promise(r => { enterTyped = r }))
      const pending = $.prompt.submit({ text: 'a typed question', origin: composer, wait: false })
      await w.clock.settle()
      await w.line({ op: 'submit', delivery: 'd1', kind: 'deliver', text: 'delivered', nonce: NONCE })
      expect(await promptsNow($, w)).toEqual([])
      // The typed prompt resolves queued; it left the set at the delivered turn's start, so
      // its own turn fails closed too.
      enterTyped({ text: 'a typed question' })
      await pending
      await turnEnd($, 't1')
      await turnStart($, 't2', 'a typed question')
      expect(await promptsNow($, w)).toEqual([])
    })

    test('a typed prompt\'s own turn starting first still ties by text', async ($: E, on) => {
      const w = world(on)
      await attached($, w, [ASK])
      // The delivery is held unsettled (it waits for idle); the typed prompt opens its turn.
      w.settle = t => (t.startsWith('moxi-turns-window: ') ? new Promise(() => {}) : turnStart($, 't1', t).then(() => ({ text: t })))
      await w.line({ op: 'submit', delivery: 'd1', kind: 'deliver', text: 'delivered', nonce: NONCE })
      await $.prompt.submit({ text: 'a typed question', origin: composer, wait: false })
      expect(await promptsNow($, w)).toEqual([{ origin: 'composer', text: 'a typed question' }])
    })

    test('an idle prompt a hook rewrote, opening its turn in that window, fails closed', async ($: E, on) => {
      const w = world(on)
      await attached($, w, [ASK])
      w.settle = t => (t.startsWith('moxi-turns-window: ') ? new Promise(() => {}) : turnStart($, 't1', 'rewritten').then(() => ({ text: 'rewritten' })))
      await w.line({ op: 'submit', delivery: 'd1', kind: 'deliver', text: 'delivered', nonce: NONCE })
      await $.prompt.submit({ text: 'a typed question', origin: composer, wait: false })
      expect(await promptsNow($, w)).toEqual([])
    })

    test('the count falls when the submit settles, after its bound too', async ($: E, on) => {
      const w = world(on)
      await attached($, w, [ASK])
      let enter: (v: Json) => void = () => {}
      w.settle = t => (t.startsWith('moxi-turns-window: ') ? new Promise(r => { enter = r }) : turnStart($, 't1', 'framed').then(() => ({ text: t })))
      await w.line({ op: 'submit', delivery: 'd1', kind: 'deliver', text: 'delivered', nonce: NONCE })
      await w.clock.advance(5_000) // the bound closes nothing; the call is still in flight
      await $.prompt.submit({ text: 'ping', origin: { kind: 'plugin', name: 'other' }, wait: false })
      expect(await promptsNow($, w)).toEqual([])
      await turnEnd($, 't1')
      enter({ drop: 'moxi-turns-gate: a late prompt was dropped (late)' })
      await w.clock.settle()
      await $.prompt.submit({ text: 'ping', origin: { kind: 'plugin', name: 'other' }, wait: false })
      expect(await promptsNow($, w)).toEqual([{ origin: 'plugin', text: 'ping' }])
    })

    test('the count falls when the submit rejects', async ($: E, on) => {
      const w = world(on)
      await attached($, w, [ASK])
      w.settle = async t => {
        if (t.startsWith('moxi-turns-window: ')) throw new Error('no')
        await turnStart($, 't1', 'framed')
        return { text: t }
      }
      await w.line({ op: 'submit', delivery: 'd1', kind: 'deliver', text: 'delivered', nonce: NONCE })
      await $.prompt.submit({ text: 'ping', origin: { kind: 'plugin', name: 'other' }, wait: false })
      expect(await promptsNow($, w)).toEqual([{ origin: 'plugin', text: 'ping' }])
    })
  })

  describe('two waiting prompts with the same text', () => {
    test('the one still in flight wins over an older one kept from a tool call', async ($: E, on) => {
      const w = world(on)
      await attached($, w, [ASK])
      await turnStart($, 't1', 'go')
      await typed($, w, 'yes', { turnId: 't1', origin: { kind: 'bridge' } }) // typed over a tool call
      await turnEnd($, 't1')
      await typed($, w, 'yes', { turn: { id: 't2' } }) // typed at idle, opens its turn
      expect(await promptsNow($, w)).toEqual([{ origin: 'composer', text: 'yes' }])
    })

    test('with neither in flight, the latest wins', async ($: E, on) => {
      const w = world(on)
      await attached($, w, [ASK])
      await turnStart($, 't1', 'go')
      await typed($, w, 'yes', { turnId: 't1', origin: { kind: 'bridge' } })
      await typed($, w, 'yes', { turnId: 't1' })
      await turnEnd($, 't1')
      await turnStart($, 't2', 'yes')
      expect(await promptsNow($, w)).toEqual([{ origin: 'composer', text: 'yes' }])
    })
  })

  test('a turn nobody submitted through this hook, starting while a typed prompt is in flight, takes it', async ($: E, on) => {
    // The rule's own tie: the turn that starts while the prompt's next(e) is in flight. A
    // task notification's turn in that window is taken for the prompt's.
    const w = world(on)
    await attached($, w, [ASK])
    let enter: (v: Json) => void = () => {}
    w.settle = () => new Promise(r => { enter = r })
    const pending = $.prompt.submit({ text: 'a typed question', origin: composer, wait: false })
    await w.clock.settle()
    await turnStart($, 't1', 'a task finished')
    expect(await promptsNow($, w)).toEqual([{ origin: 'composer', text: 'a typed question' }])
    enter({ text: 'a typed question' })
    await pending
  })
})

describe('the ask step', () => {
  const permission = ($: E, fields: Json = {}) => $.classic.PermissionRequest({
    session_id: 's1', transcript_path: '/t/s1.jsonl', cwd: '/work/t', permission_mode: 'bypassPermissions',
    tool_name: 'Bash', tool_input: { command: 'cd t && rm -rf *' }, ...fields,
  })

  test('nothing covering the ask: no trip, and the ask goes on to Claude Code', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.call', { judge: false, deny: 'not at a call' })])
    const r = await permission($)
    expect(r.decision).toBeUndefined()
    expect(asks(w)).toEqual([])
  })

  test('a data-only ask entry denies the ask with no trip, and never touches a tool call', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.asks', { step: 'ask', judge: false, deny: 'nobody is there to ask' })])
    expect((await permission($)).decision).toEqual({ behavior: 'deny', message: 'nobody is there to ask' })
    expect(asks(w)).toEqual([])
    expect((await $.tool.call({ tool: 'Bash', command: 'ls' })).deny).toBeUndefined()
    expect(ran(w)).toEqual(['Bash'])
  })

  test('a judge entry is asked with the step, the mode and the record path; its deny denies', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.ask', { step: 'ask' })])
    w.answers['/ask/guard'] = { ok: true, deny: 'refused in bypass', lines: [], broken: [] }
    expect((await permission($, { agent_id: 'a1' })).decision).toEqual({ behavior: 'deny', message: 'refused in bypass' })
    expect(asks(w)).toEqual([{
      call: { tool: 'Bash', input: { command: 'cd t && rm -rf *' }, agentId: 'a1', permission_mode: 'bypassPermissions', transcript_path: '/t/s1.jsonl', cwd: '/work/t' },
      guards: ['t.ask'], step: 'ask',
    }])
  })

  test('an answer with no deny leaves the ask to Claude Code: nothing approves it', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.ask', { step: 'ask' })])
    w.answers['/ask/guard'] = { ok: true, lines: ['an allow line'], broken: [] }
    expect((await permission($)).decision).toBeUndefined()
    expect(asks(w)).toHaveLength(1)
  })

  test('a failed trip leaves the ask, or denies it when an asked entry declared on_error deny', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [entry('t.soft', { step: 'ask' }), entry('t.hard', { step: 'ask', calls: ['Write'], on_error: 'deny' })])
    w.answers['/ask/guard'] = 'fail'
    expect((await permission($)).decision).toBeUndefined()
    const r = await permission($, { tool_name: 'Write', tool_input: { file_path: '/a' } })
    expect(r.decision.behavior).toBe('deny')
    expect(r.decision.message).toMatch(/^turns could not check this call with guard t\.hard \(the trip failed: .+\)$/)
  })

  test('the catch refuses an ask only when a covering ask entry declared on_error deny', () => {
    const table = parseTable([
      entry('t.call', { on_error: 'deny' }),
      entry('t.ask', { step: 'ask', calls: ['Write'], on_error: 'deny' }),
    ])!
    expect(catchRefusesAsk(table, { tool_name: 'Bash', tool_input: { command: 'ls' } })).toBe(false)
    expect(catchRefusesAsk(table, { tool_name: 'Write', tool_input: { file_path: '/a' } })).toBe(true)
    expect(catchRefuses(table, { tool: 'Write', file_path: '/a' })).toBe(false)
    expect(decide(table, 'Bash', null, {}, 'ask').covering).toEqual([])
  })
})

describe('no table yet', () => {
  /** Question mode's entry as the endpoint sends it. */
  const QUESTION = entry('turns.question-mode', { calls: ['AskUserQuestion'], wants: ['prompts'], on_error: 'deny', deadline_ms: 3000 })
  const ask = ($: E, fields: Json = {}) => $.tool.call({ tool: 'AskUserQuestion', tool_use_id: 'q1', questions: [], ...fields })

  test('AskUserQuestion is refused with the named reason, a subagent\'s too, with no trip', async ($: E, on) => {
    const w = world(on)
    await start($)
    expect(await ask($)).toEqual({ deny: NO_TABLE_DENY })
    expect(await ask($, { agentId: 'a1' })).toEqual({ deny: NO_TABLE_DENY })
    expect(NO_TABLE_DENY).toBe("turns' guards aren't loaded, so question mode can't check this call; ask in prose")
    expect(ran(w)).toEqual([])
    expect(asks(w)).toEqual([])
  })

  test('another tool runs as before', async ($: E, on) => {
    const w = world(on)
    await start($)
    expect((await $.tool.call({ tool: 'Bash', command: 'ls' })).deny).toBeUndefined()
    expect(ran(w)).toEqual(['Bash'])
    expect(asks(w)).toEqual([])
  })

  test('a hello with no table, or a guards line that is not a list, is no table', async ($: E, on) => {
    const w = world(on)
    await start($)
    await w.hello()
    expect(await ask($)).toEqual({ deny: NO_TABLE_DENY })
    await w.line({ op: 'guards', table: 'nonsense' })
    expect(await ask($)).toEqual({ deny: NO_TABLE_DENY })
    expect(ran(w)).toEqual([])
  })

  test('an endpoint that ends before its first line leaves it refused', async ($: E, on) => {
    const w = world(on)
    await start($)
    await w.end()
    expect(await ask($)).toEqual({ deny: NO_TABLE_DENY })
  })

  test('an empty table says question mode is off: the call runs', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [])
    expect((await ask($)).deny).toBeUndefined()
    expect(ran(w)).toEqual(['AskUserQuestion'])
  })

  test('once the table arrives, question mode judges as before', async ($: E, on) => {
    const w = world(on)
    await start($)
    expect(await ask($)).toEqual({ deny: NO_TABLE_DENY })
    await w.hello({ guards: [QUESTION] })
    w.answers['/ask/guard'] = { ok: true, deny: 'nothing you typed mentions a question', lines: [], broken: [] }
    expect(await ask($)).toEqual({ deny: 'nothing you typed mentions a question' })
    w.answers['/ask/guard'] = { ok: true, lines: [], broken: [] }
    expect((await ask($)).deny).toBeUndefined()
    expect(asks(w).map(b => b.guards)).toEqual([['turns.question-mode'], ['turns.question-mode']])
    expect(ran(w)).toEqual(['AskUserQuestion'])
  })

  test('a guards line brings the table too', async ($: E, on) => {
    const w = world(on)
    await start($)
    await w.hello()
    await w.line({ op: 'guards', table: [] })
    expect((await ask($)).deny).toBeUndefined()
  })

  test('the table stays when the endpoint ends: its break refuses as before', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [QUESTION])
    await w.end()
    expect((await ask($)).deny).toBe("turns could not check this call with guard turns.question-mode (turns' endpoint is not running)")
  })

  test('a headless session is not refused', async ($: E, on) => {
    const w = world(on)
    await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: false })
    expect((await ask($)).deny).toBeUndefined()
    expect(ran(w)).toEqual(['AskUserQuestion'])
  })

  test('the rule the hook and its catch share', () => {
    expect(noTableRefuses('AskUserQuestion', false, undefined)).toBe(true)
    expect(noTableRefuses('AskUserQuestion', false, false)).toBe(true)
    expect(noTableRefuses('AskUserQuestion', true, false)).toBe(false)
    expect(noTableRefuses('AskUserQuestion', false, true)).toBe(false)
    expect(noTableRefuses('Bash', false, false)).toBe(false)
    expect(noTableRefuses(undefined, false, false)).toBe(false)
  })
})

describe('no table yet: the record of the last table', () => {
  const RECORD = '/state/turns/guards-seen.json'
  const record = (facts: Json) => JSON.stringify({ at: 1.5, removal: false, no_nested_agents: false, ask_step: false, ...facts })
  /** A world whose launch names the record, holding `text` (none: the read fails). */
  const named = (on: E, text?: string) => world(on, { env: { MOXI_TURNS_GUARDS_SEEN: RECORD }, files: text === undefined ? {} : { [RECORD]: text } })
  const nested = ($: E) => $.tool.call({ tool: 'Agent', tool_use_id: 'g1', agentId: 'a1', prompt: 'go' })
  const bash = ($: E, command: string) => $.tool.call({ tool: 'Bash', tool_use_id: 'b1', command })
  const permission = ($: E, fields: Json = {}) => $.classic.PermissionRequest({
    session_id: 's1', transcript_path: '/t/s1.jsonl', cwd: '/work', permission_mode: 'bypassPermissions',
    tool_name: 'Bash', tool_input: { command: 'cd /work && ls' }, ...fields,
  })
  const refusedAll = async ($: E, w: World) => {
    expect(await nested($)).toEqual({ deny: NO_TABLE_NESTED_DENY })
    for (const command of ['rm -rf build', 'R\\m x', "'r'm x", 'cd t && rmdir d'])
      expect(await bash($, command)).toEqual({ deny: NO_TABLE_REMOVAL_DENY })
    expect((await permission($)).decision).toEqual({ behavior: 'deny', message: NO_TABLE_ASK_DENY })
    expect(ran(w)).toEqual([])
    expect(asks(w)).toEqual([])
  }

  test('each reason names the table and says what to do', () => {
    for (const reason of [NO_TABLE_NESTED_DENY, NO_TABLE_REMOVAL_DENY, NO_TABLE_ASK_DENY]) {
      expect(reason).toMatch(/^turns' guard table didn't load, so /)
    }
    expect(NO_TABLE_REMOVAL_DENY).toBe("turns' guard table didn't load, so the removal guard can't check this command; nothing ran. Remove files with literal paths once guards load, or ask the person.")
  })

  test('with no record named, all three are refused, with no trip, and logged', async ($: E, on) => {
    const w = world(on)
    await start($)
    await refusedAll($, w)
    expect(w.logs).toContain('moxi-turns: no record of the last guard table is named; before a table, every guard counts as on')
    expect(w.logs).toContain('moxi-turns: refused Bash: no guard table has come from the endpoint')
    expect(w.logs).toContain('moxi-turns: refused the Bash ask: no guard table has come from the endpoint')
  })

  for (const [what, text] of [
    ['missing', undefined],
    ['not JSON', 'not json'],
    ['lacking a fact', JSON.stringify({ removal: false, no_nested_agents: false })],
    ['holding a fact that is no boolean', JSON.stringify({ removal: 'no', no_nested_agents: false, ask_step: false })],
  ] as const) {
    test(`a record ${what} is no record: all three are refused`, async ($: E, on) => {
      const w = named(on, text)
      await start($)
      await refusedAll($, w)
    })
  }

  test('a record of removal alone refuses the rm, and lets the subagent call and the bypass ask pass', async ($: E, on) => {
    const w = named(on, record({ removal: true }))
    await start($)
    expect(await bash($, 'rm -rf build')).toEqual({ deny: NO_TABLE_REMOVAL_DENY })
    expect((await nested($)).deny).toBeUndefined()
    expect((await permission($)).decision).toBeUndefined()
    expect(ran(w)).toEqual(['Agent'])
  })

  test('a record of all three false refuses none of them', async ($: E, on) => {
    const w = named(on, record({}))
    await start($)
    expect((await nested($)).deny).toBeUndefined()
    expect((await bash($, 'rm -rf build')).deny).toBeUndefined()
    expect((await permission($)).decision).toBeUndefined()
    expect(ran(w)).toEqual(['Agent', 'Bash'])
  })

  test('the record endpoint.json names is read when the variable is unset', async ($: E, on) => {
    const w = world(on, { endpointJson: JSON.stringify({ argv: ROOT, guards_seen: '/elsewhere/guards-seen.json' }), files: { '/elsewhere/guards-seen.json': record({}) } })
    await start($)
    expect((await bash($, 'rm -rf build')).deny).toBeUndefined()
    expect(w.reads.filter(p => p.endsWith('/endpoint.json')).length).toBe(1)
  })

  test('MOXI_TURNS_GUARDS_SEEN wins over the path endpoint.json names', async ($: E, on) => {
    const w = world(on, {
      endpointJson: JSON.stringify({ argv: ROOT, guards_seen: '/elsewhere/guards-seen.json' }),
      env: { MOXI_TURNS_GUARDS_SEEN: RECORD },
      files: { '/elsewhere/guards-seen.json': record({}), [RECORD]: record({ removal: true }) },
    })
    await start($)
    expect(await bash($, 'rm -rf build')).toEqual({ deny: NO_TABLE_REMOVAL_DENY })
    expect(w.reads).not.toContain('/elsewhere/guards-seen.json')
  })

  test('a call before the record is read counts as no record', async ($: E, on) => {
    const w = named(on, record({}))
    expect(await bash($, 'rm -rf build')).toEqual({ deny: NO_TABLE_REMOVAL_DENY })
    expect(ran(w)).toEqual([])
  })

  test('a main-loop Agent call, a Bash call with no rm word, a non-bypass ask and a plan ask always pass', async ($: E, on) => {
    const w = world(on)
    await start($)
    expect((await $.tool.call({ tool: 'Agent', tool_use_id: 'g0', prompt: 'go' })).deny).toBeUndefined()
    expect((await bash($, 'git status && form x && perm y')).deny).toBeUndefined()
    expect((await permission($, { permission_mode: 'default' })).decision).toBeUndefined()
    expect((await permission($, { tool_name: 'ExitPlanMode', tool_input: {} })).decision).toBeUndefined()
    expect((await permission($, { tool_name: 'EnterPlanMode', tool_input: {} })).decision).toBeUndefined()
    expect(ran(w)).toEqual(['Agent', 'Bash'])
  })

  test('after a table, even an empty one, every call goes to the table', async ($: E, on) => {
    const w = world(on)
    await attached($, w, [])
    expect((await nested($)).deny).toBeUndefined()
    expect((await bash($, 'rm -rf build')).deny).toBeUndefined()
    expect((await permission($)).decision).toBeUndefined()
    expect(ran(w)).toEqual(['Agent', 'Bash'])
  })

  test('a headless session refuses none of them', async ($: E, on) => {
    const w = world(on)
    await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: false })
    expect((await nested($)).deny).toBeUndefined()
    expect((await bash($, 'rm -rf build')).deny).toBeUndefined()
    expect((await permission($)).decision).toBeUndefined()
    expect(ran(w)).toEqual(['Agent', 'Bash'])
  })

  test('the rules the hooks and their catches share', () => {
    const all = parseLastTable({ removal: true, no_nested_agents: true, ask_step: true })
    expect(all).toEqual({ removal: true, noNestedAgents: true, askStep: true })
    expect(parseLastTable({ removal: true, no_nested_agents: true })).toBeNull()
    expect(parseLastTable([])).toBeNull()
    expect(noTableDeny('Agent', 'a1', {}, false, false, null)).toBe(NO_TABLE_NESTED_DENY)
    expect(noTableDeny('Task', 'a1', {}, false, undefined, all)).toBe(NO_TABLE_NESTED_DENY)
    expect(noTableDeny('Agent', null, {}, false, false, null)).toBeNull()
    expect(noTableDeny('Bash', null, { command: 'RM x' }, false, false, null)).toBe(NO_TABLE_REMOVAL_DENY)
    expect(noTableDeny('Bash', null, { command: 'rm x' }, true, false, null)).toBeNull()
    expect(noTableDeny('Bash', null, { command: 'rm x' }, false, true, null)).toBeNull()
    expect(noTableDeny('Bash', null, { command: 'rmx' }, false, false, null)).toBeNull()
    expect(noTableDeny('AskUserQuestion', null, {}, false, false, parseLastTable({ removal: false, no_nested_agents: false, ask_step: false }))).toBe(NO_TABLE_DENY)
    expect(noTableAskDeny({ permission_mode: 'bypassPermissions', tool_name: 'Write' }, false, false, null)).toBe(NO_TABLE_ASK_DENY)
    expect(noTableAskDeny({ permission_mode: 'bypassPermissions', tool_name: 'ExitPlanMode' }, false, false, null)).toBeNull()
    expect(noTableAskDeny({ permission_mode: 'acceptEdits', tool_name: 'Write' }, false, false, null)).toBeNull()
    expect(noTableAskDeny({ permission_mode: 'bypassPermissions', tool_name: 'Write' }, false, false, { removal: true, noNestedAgents: true, askStep: false })).toBeNull()
  })
})
