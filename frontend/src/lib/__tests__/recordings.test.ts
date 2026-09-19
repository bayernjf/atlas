import { describe, expect, it } from 'vitest'
import type { RunEvent } from '../apiClient'
import { toSteps } from '../recordings'

describe('toSteps', () => {
  it('maps node_end events to steps and ignores other events', () => {
    const events: RunEvent[] = [
      { type: 'node_start', node_id: 'trigger-1', node_type: 'trigger' },
      { type: 'node_end', node_id: 'trigger-1', node_type: 'trigger', output: { v: 1 } },
      { type: 'node_start', node_id: 'tool-1', node_type: 'tool_call' },
      { type: 'node_end', node_id: 'tool-1', node_type: 'tool_call', output: { ok: true } },
    ]
    expect(toSteps(events)).toEqual([
      { node_id: 'trigger-1', node_type: 'trigger', output: { v: 1 } },
      { node_id: 'tool-1', node_type: 'tool_call', output: { ok: true } },
    ])
  })

  it('keeps the last occurrence for duplicate node ids (parallel re-emit, loop revisit)', () => {
    const events: RunEvent[] = [
      { type: 'node_end', node_id: 'parallel-1', node_type: 'parallel', output: { status: 'running' } },
      { type: 'node_end', node_id: 'branch-a', node_type: 'tool_call', output: { v: 'a' } },
      { type: 'node_end', node_id: 'parallel-1', node_type: 'parallel', output: { status: 'success' } },
    ]
    const steps = toSteps(events)
    expect(steps.map((s) => s.node_id)).toEqual(['branch-a', 'parallel-1'])
    expect(steps[1].output).toEqual({ status: 'success' })
  })

  it('returns an empty list when no node_end events arrived', () => {
    expect(toSteps([])).toEqual([])
    expect(
      toSteps([{ type: 'node_start', node_id: 'trigger-1', node_type: 'trigger' }]),
    ).toEqual([])
  })

  it('A pack: excludes subgraph-internal node_end but keeps the subgraph node itself', () => {
    const events: RunEvent[] = [
      { type: 'node_end', node_id: 'p-trigger', node_type: 'trigger', output: {} },
      // 子图内部节点（带 subgraphPath）不得进入录制步骤
      {
        type: 'node_end',
        node_id: 'c-trigger',
        node_type: 'trigger',
        output: {},
        subgraphPath: ['subgraph-1'],
      },
      {
        type: 'node_end',
        node_id: 'c-tool',
        node_type: 'tool_call',
        output: { ok: true },
        subgraphPath: ['subgraph-1'],
      },
      // 嵌套子图内部节点同样排除
      {
        type: 'node_end',
        node_id: 'g-tool',
        node_type: 'tool_call',
        output: {},
        subgraphPath: ['subgraph-1', 'subgraph-inner'],
      },
      // 父图 subgraph 节点自身的 node_end（无 subgraphPath）保留，承载子图结果
      {
        type: 'node_end',
        node_id: 'subgraph-1',
        node_type: 'subgraph',
        output: { mode: 'subgraph', status: 'success' },
      },
      { type: 'node_end', node_id: 'tool-after', node_type: 'tool_call', output: {} },
    ]
    expect(toSteps(events).map((s) => s.node_id)).toEqual([
      'p-trigger',
      'subgraph-1',
      'tool-after',
    ])
  })
})
