// The runner's after-tool ask and its floors, the usage reports, and what the context asks
// carry for the listener events: the transcript path at a ready, a prompt's text and origin.

import { describe, expect, test } from 'claude-code/testing'
import { world } from './world'
import type { World } from './world'
import { afterRows, originOf, parseFloors, usageOf } from '../hooks/register'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type E = any
type Json = Record<string, unknown>

const start = ($: E) => $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
const turnStart = ($: E, turnId: string, text = '') => $.turn.start({ text, turnId })
const turnEnd = ($: E, turnId: string, agentId?: string) =>
  $.turn.complete({ answer: '', durationMs: 1, isAborted: false, turnId, reason: 'answer', ...(agentId === undefined ? {} : { agentId }) })
const afterAsks = (w: World) => w.got.filter(g => g.path === '/ask/after-tool').map(g => g.body)
const usages = (w: World) => w.got.filter(g => g.path === '/report/usage').map(g => g.body.usage as Json)
const contextAsks = (w: World) => w.got.filter(g => g.path === '/ask/context').map(g => g.body)
const measure = ($: E, tokens: number | undefined, changed: string[], extra: Json = {}) =>
  $.session.measure({ context: { window: 200_000, ...(tokens === undefined ? {} : { tokens, percent: Math.round(tokens / 2000) }) }, rateLimits: [], changed, ...extra })

/** One guard entry, a judge on Bash. */
function entry(id: string, fields: Json = {}): Json {
  return { id, calls: ['Bash'], who: 'any', match: null, match_flags: '', judge: true, deny: '', wants: [], on_error: 'allow', deadline_ms: 500, ...fields }
}

async function attached($: E, w: World, extra: Json = {}): Promise<void> {
  await start($)
  await w.hello({ after_tool_wait_ms: 300, floors: { 'usage.rows:after': 0 }, ...extra })
}

describe('the after-tool ask', () => {
  test('with no row installed the runner never asks', async ($: E, on) => {
    const w = world(on)
    await start($)
    await w.hello()
    await $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: 'ls' })
    await w.hello({ floors: {} })
    await $.tool.call({ tool: 'Bash', tool_use_id: 'u2', command: 'ls' })
    expect(afterAsks(w)).toEqual([])
  })

  test('after the tool ran: the call, the rows, and the text into context', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    w.answers['/ask/after-tool'] = { context: 'you are at 81% of the window' }
    const r = await $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: 'ls' })
    expect(w.toolCalls.map(c => c.tool)).toEqual(['Bash'])
    expect(afterAsks(w)).toEqual([{ call: { tool: 'Bash', tool_use_id: 'u1', agentId: null, is_error: false }, rows: ['usage.rows:after'] }])
    expect(r.context).toEqual(['you are at 81% of the window'])
    expect(r.text).toBe('ok')
  })

  test('the ask is made only once the tool has run', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    const r = $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: 'hold:a' })
    await w.clock.settle()
    expect(afterAsks(w)).toEqual([])
    w.holds['hold:a']!()
    await r
    expect(afterAsks(w)).toHaveLength(1)
  })

  test('a call that errored is asked about, with is_error', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    const r = await $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: 'error:x' })
    expect(r.isError).toBe(true)
    expect((afterAsks(w)[0]!.call as Json).is_error).toBe(true)
  })

  test('a subagent\'s call carries its agentId', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    await $.tool.call({ tool: 'Read', tool_use_id: 'u3', file_path: '/x', agentId: 'a7' })
    expect(afterAsks(w)[0]!.call).toEqual({ tool: 'Read', tool_use_id: 'u3', agentId: 'a7', is_error: false })
  })

  test('not after a deny: neither a guard\'s nor one from beneath', async ($: E, on) => {
    const w = world(on)
    await attached($, w, { guards: [entry('t.stop', { judge: false, deny: 'not here', calls: ['Write'] })] })
    expect(await $.tool.call({ tool: 'Write', tool_use_id: 'u1', file_path: '/x' })).toEqual({ deny: 'not here' })
    const r = await $.tool.call({ tool: 'Bash', tool_use_id: 'u2', command: 'deny:x' })
    expect(r.deny).toBe('refused beneath')
    expect(afterAsks(w)).toEqual([])
  })

  test('the guards\' lines come first, then the after-tool text, in one context', async ($: E, on) => {
    const w = world(on)
    await attached($, w, { guards: [entry('t.one')] })
    w.answers['/ask/guard'] = { lines: ['guard says hi'], broken: ['[turns] guard t.two could not check this call (x); it ran unchecked.'] }
    w.answers['/ask/after-tool'] = { context: 'after text' }
    const r = await $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: 'ls' })
    expect(r.context).toEqual(['guard says hi', '[turns] guard t.two could not check this call (x); it ran unchecked.', 'after text'])
  })

  test('the runner\'s own TaskStop calls are never asked about', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    await $.classic.Stop({ stop_hook_active: false, background_tasks: [{ id: 's1', type: 'shell', status: 'running', description: '' }] })
    await w.line({ op: 'command', request: 'r1', name: 'compact', stop_shells: true })
    expect(w.stopCalls.map(c => c.taskId)).toEqual(['s1'])
    expect(afterAsks(w)).toEqual([])
  })

  test('no answer within after_tool_wait_ms + 1 s adds nothing, and the call returns', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    w.answers['/ask/after-tool'] = 'hang'
    const r = $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: 'ls' })
    let done = false
    void r.then(() => { done = true })
    await w.clock.advance(1299)
    expect(done).toBe(false)
    await w.clock.advance(1)
    const out = await r
    expect(out.text).toBe('ok')
    expect(out.context ?? []).toEqual([])
  })

  test('a failed trip adds nothing and the call returns', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    w.answers['/ask/after-tool'] = 'fail'
    const out = await $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: 'ls' })
    expect(out.text).toBe('ok')
    expect(out.context ?? []).toEqual([])
  })

  test('the trip is timed as the event after-tool, and the tool.call time leaves it out', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    w.answers['/ask/after-tool'] = async () => {
      const t0 = performance.now()
      while (performance.now() - t0 < 80) { /* the endpoint asking its rows */ }
      return { context: null }
    }
    await $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: 'ls' })
    await w.clock.advance(60_000)
    const samples = w.got.filter(g => g.path === '/hook-times').flatMap(g => g.body.samples as { event: string; ms: number }[])
    expect(samples.find(x => x.event === 'after-tool')!.ms).toBeGreaterThanOrEqual(80)
    expect(samples.find(x => x.event === 'tool.call')!.ms).toBeLessThan(80)
  })

  test('parallel calls each reach the rows', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    w.answers['/ask/after-tool'] = async body => ({ context: `for ${String((body.call as Json).tool_use_id)}` })
    const calls = ['a1', 'a2', 'a3', 'a4'].map((agent, i) => $.tool.call({ tool: 'Bash', tool_use_id: `u${i}`, command: 'ls', agentId: agent }))
    const out = await Promise.all(calls)
    expect(out.map(r => r.context)).toEqual([['for u0'], ['for u1'], ['for u2'], ['for u3']])
    expect(afterAsks(w).map(b => (b.call as Json).agentId).sort()).toEqual(['a1', 'a2', 'a3', 'a4'])
  })
})

describe('the floors', () => {
  const floored = { 'usage.rows:after': 50_000, 'other.rows:after': 0 }

  test('an unknown context never skips', async ($: E, on) => {
    const w = world(on)
    await attached($, w, { floors: floored })
    await $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: 'ls' })
    expect(afterAsks(w)[0]!.rows).toEqual(['usage.rows:after', 'other.rows:after'])
  })

  test('on the main loop a row below its floor is skipped; the rest are asked', async ($: E, on) => {
    const w = world(on)
    await attached($, w, { floors: floored })
    await measure($, 1200, ['context'])
    await $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: 'ls' })
    expect(afterAsks(w).map(b => b.rows)).toEqual([['other.rows:after']])
    await measure($, 50_000, ['context'])
    await $.tool.call({ tool: 'Bash', tool_use_id: 'u2', command: 'ls' })
    expect(afterAsks(w)[1]!.rows).toEqual(['usage.rows:after', 'other.rows:after'])
  })

  test('with every row skipped no trip is made', async ($: E, on) => {
    const w = world(on)
    await attached($, w, { floors: { 'usage.rows:after': 50_000 } })
    await measure($, 1200, ['context'])
    await $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: 'ls' })
    expect(afterAsks(w)).toEqual([])
  })

  test('an agent\'s call is never gated', async ($: E, on) => {
    const w = world(on)
    await attached($, w, { floors: { 'usage.rows:after': 50_000 } })
    await measure($, 1200, ['context'])
    await $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: 'ls', agentId: 'a1' })
    expect(afterAsks(w).map(b => b.rows)).toEqual([['usage.rows:after']])
  })

  test('a floors line replaces the map; a floor dropping to 0 lets the next main call through', async ($: E, on) => {
    const w = world(on)
    await attached($, w, { floors: { 'usage.rows:after': 50_000 } })
    await measure($, 1200, ['context'])
    await $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: 'ls' })
    await w.line({ op: 'floors', rows: { 'usage.rows:after': 0 } })
    await $.tool.call({ tool: 'Bash', tool_use_id: 'u2', command: 'ls' })
    expect(afterAsks(w).map(b => (b.call as Json).tool_use_id)).toEqual(['u2'])
  })

  test('a floors line that is not a map keeps the last one', async ($: E, on) => {
    const w = world(on)
    await attached($, w, { floors: { 'usage.rows:after': 50_000 } })
    await measure($, 1200, ['context'])
    await w.line({ op: 'floors', rows: ['nope'] })
    await $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: 'ls' })
    expect(afterAsks(w)).toEqual([])
  })

  test('the turn-end reading sets the context too', async ($: E, on) => {
    const w = world(on)
    await attached($, w, { floors: { 'usage.rows:after': 1000 } })
    w.usage = { startedAt: 500, context: { tokens: 900, window: 200_000 }, rateLimits: [] }
    await turnStart($, 't1')
    await turnEnd($, 't1')
    await $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: 'ls' })
    expect(afterAsks(w)).toEqual([])
  })

  test('a SessionStart makes the context unknown again', async ($: E, on) => {
    const w = world(on)
    await attached($, w, { floors: { 'usage.rows:after': 50_000 } })
    await measure($, 1200, ['context'])
    await $.classic.SessionStart({ source: 'compact' })
    await $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: 'ls' })
    expect(afterAsks(w).map(b => b.rows)).toEqual([['usage.rows:after']])
  })

  test('a measure with no tokens yet leaves the context unknown', async ($: E, on) => {
    const w = world(on)
    await attached($, w, { floors: { 'usage.rows:after': 50_000 } })
    await measure($, undefined, ['rateLimits'])
    await $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: 'ls' })
    expect(afterAsks(w)).toHaveLength(1)
  })

  test('afterRows and parseFloors', () => {
    expect(afterRows({ a: 10, b: 0 }, undefined, 5)).toEqual(['b'])
    expect(afterRows({ a: 10, b: 0 }, '', 10)).toEqual(['a', 'b'])
    expect(afterRows({ a: 10 }, 'x', 5)).toEqual(['a'])
    expect(afterRows({ a: 10 }, null, null)).toEqual(['a'])
    expect(parseFloors({ a: 3, b: 'x', c: -1 })).toEqual({ a: 3, c: 0 })
    expect(parseFloors([1])).toBeNull()
  })
})

describe('the usage reports', () => {
  test('a measure that moved the context or a rate limit posts the reading, less changed and breakdown', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    const limits = [{ window: 'five_hour', percentUsed: 61 }]
    await $.session.measure({ context: { tokens: 1200, window: 200_000, breakdown: { gridRows: [] } }, rateLimits: limits, cost: { usd: 0.5 }, changed: ['rateLimits'] })
    await w.clock.settle()
    expect(usages(w)).toEqual([{ context: { tokens: 1200, window: 200_000 }, rateLimits: limits, cost: { usd: 0.5 } }])
    await measure($, 1300, ['context'])
    await w.clock.settle()
    expect(usages(w)).toHaveLength(2)
  })

  test('a measure that moved only the cost posts nothing', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    await measure($, 1200, ['cost'])
    await w.clock.settle()
    expect(usages(w)).toEqual([])
  })

  test('each main turn end posts $.session.usage(); a subagent\'s does not', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    w.usage = { startedAt: 500, context: { tokens: 900, window: 200_000, percent: 0 }, rateLimits: [{ window: 'seven_day', percentUsed: 46 }] }
    await turnStart($, 't1')
    await turnEnd($, 'a-t1', 'a1')
    await w.clock.settle()
    expect(usages(w)).toEqual([])
    await turnEnd($, 't1')
    await w.clock.settle()
    expect(usages(w)).toEqual([w.usage])
    // The turn's report reaches the endpoint first.
    const paths = w.got.map(g => g.path === '/report' ? String(g.body.kind) : g.path)
    expect(paths.indexOf('turn.complete')).toBeLessThan(paths.indexOf('/report/usage'))
  })

  test('a usage read that fails posts nothing and the turn still ends', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    w.usage = null
    await turnStart($, 't1')
    const r = await turnEnd($, 't1')
    expect(r.text).toBe('')
    expect(usages(w)).toEqual([])
  })

  test('a report the endpoint never answers holds no hook', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    w.answers['/report/usage'] = 'hang'
    await measure($, 1200, ['context'])
    await turnStart($, 't1')
    await turnEnd($, 't1')
    expect(usages(w)).toHaveLength(2)
  })

  test('usageOf leaves the reading alone but for changed and breakdown', () => {
    const reading = { startedAt: 1, context: { tokens: 2, window: 3, breakdown: {} }, rateLimits: [], changed: ['context'] }
    expect(usageOf(reading)).toEqual({ startedAt: 1, context: { tokens: 2, window: 3 }, rateLimits: [] })
    expect(reading.context.breakdown).toEqual({})
  })
})

describe('what the context asks carry', () => {
  test('a ready carries the transcript path', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    await $.classic.SessionStart({ source: 'clear', session_id: 'S-2', transcript_path: '/p/S-2.jsonl' })
    expect(contextAsks(w)[0]).toEqual({ event: 'ready', source: 'clear', session_id: 'S-2', transcript_path: '/p/S-2.jsonl' })
  })

  test('a typed prompt\'s ask carries its text and the composer origin', async ($: E, on) => {
    const w = world(on)
    w.classicInSubmit = text => $.classic.UserPromptSubmit({ prompt: text })
    await attached($, w)
    await $.prompt.submit({ text: 'what next?', origin: { kind: 'composer' } })
    expect(contextAsks(w)).toEqual([{ event: 'prompt', source: null, session_id: expect.any(String), text: 'what next?', origin: 'composer' }])
  })

  test('another plugin\'s prompt carries origin plugin', async ($: E, on) => {
    const w = world(on)
    w.classicInSubmit = text => $.classic.UserPromptSubmit({ prompt: text })
    await attached($, w)
    await $.prompt.submit({ text: 'from a plugin', origin: { kind: 'plugin', name: 'other' } })
    expect(contextAsks(w)[0]!.origin).toBe('plugin')
  })

  test('a prompt rewritten beneath still ties when it is the only one in flight', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    w.classicInSubmit = text => $.classic.UserPromptSubmit({ prompt: text })
    w.rewrite = text => `${text}, rewritten`
    await $.prompt.submit({ text: 'raw', origin: { kind: 'composer' } })
    expect(contextAsks(w)[0]).toMatchObject({ text: 'raw, rewritten', origin: 'composer' })
    expect(originOf('raw, rewritten')).toBeNull() // nothing in flight any more
  })

  test('a UserPromptSubmit with no prompt in flight carries origin null', async ($: E, on) => {
    const w = world(on)
    await attached($, w)
    await $.classic.UserPromptSubmit({ prompt: 'hi' })
    expect(contextAsks(w)).toEqual([{ event: 'prompt', source: null, session_id: expect.any(String), text: 'hi', origin: null }])
  })

  test('the runner\'s own delivery carries origin null', async ($: E, on) => {
    const w = world(on)
    w.classicInSubmit = text => $.classic.UserPromptSubmit({ prompt: text })
    await attached($, w)
    await w.line({ op: 'submit', delivery: 'd1', kind: 'deliver', text: 'from comms', nonce: 'n1' })
    expect(contextAsks(w)).toHaveLength(1)
    expect(contextAsks(w)[0]!.origin).toBeNull()
  })
})
