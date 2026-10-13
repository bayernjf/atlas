/**
 * 打包 AT 前端纯逻辑（docs/128 §3，lib/promote.ts）。
 *
 * 核心要钉的是"默认不勾选"与"越表选择不可发出"——这两条一旦在前端被偷懒实现
 * （预勾选、或把 selectionIsValid 当装饰），后端的两阶段设计就名存实亡。
 */
import { describe, expect, it } from 'vitest'

import type { PromotePlan, PromoteSubstitution } from '../apiClient'
import {
  countPlan,
  groupByNode,
  guideFilename,
  proposedIds,
  pruneWarningCounts,
  selectionIsValid,
  toggleSelection,
} from '../promote'

function sub(over: Partial<PromoteSubstitution>): PromoteSubstitution {
  return {
    id: 'nodes[1].config.params#1',
    path: 'nodes[1].config.params',
    nodeId: 'tool-1',
    occurrence: 1,
    from: '12345',
    to: '{{trigger-1.context.payload.order_id}}',
    inputKey: 'order_id',
    excerpt: '{"order_id": "12345"}',
    ...over,
  }
}

function plan(over: Partial<PromotePlan> = {}): PromotePlan {
  return {
    caseId: 'rec-1',
    suggestedName: '退款试教（来自用例 rec-1）',
    graph: { version: 1, variables: [], nodes: [], edges: [] } as unknown as PromotePlan['graph'],
    triggerId: 'trigger-1',
    inputsDeclaration: [{ key: 'order_id', type: 'string' }],
    substitutions: [sub({}), sub({ id: 'nodes[5].config.params#1', nodeId: 'tool_call-approve' })],
    ambiguous: [],
    skipped: [],
    prunePreview: { keptNodeIds: ['trigger-1'], droppedNodeIds: ['tool-2'] },
    compile: { ok: true, issues: [], codes: [], params: [], locations: [] },
    ...over,
  }
}

describe('grouping keeps backend order so checkboxes cannot shift', () => {
  it('groups by node and preserves first-seen order', () => {
    const groups = groupByNode(
      [
        sub({ id: 'a#1', nodeId: 'tool-b' }),
        sub({ id: 'b#1', nodeId: 'tool-a' }),
        sub({ id: 'a#2', nodeId: 'tool-b' }),
      ],
      (nodeId) => `节点:${nodeId}`,
    )

    expect(groups.map((group) => group.nodeId)).toEqual(['tool-b', 'tool-a'])
    expect(groups[0].items.map((item) => item.id)).toEqual(['a#1', 'a#2'])
    expect(groups[0].nodeTitle).toBe('节点:tool-b')
  })

  it('handles an empty candidate table without inventing groups', () => {
    expect(groupByNode([], () => 'x')).toEqual([])
  })
})

describe('selection is human-made, never pre-filled', () => {
  it('starts from an empty selection', () => {
    // 反向门：把 toggleSelection 写成"默认全选"时这条立刻红
    expect(toggleSelection([], 'a#1', false)).toEqual([])
    expect(toggleSelection([], 'a#1', true)).toEqual(['a#1'])
    expect(toggleSelection(['a#1'], 'a#1', true)).toEqual(['a#1'])
    expect(toggleSelection(['a#1', 'b#1'], 'a#1', false)).toEqual(['b#1'])
  })

  it('proposedIds only ever contains machine-proposed ids', () => {
    expect(proposedIds(plan())).toEqual([
      'nodes[1].config.params#1',
      'nodes[5].config.params#1',
    ])
    expect(proposedIds(null)).toEqual([])
  })

  it('rejects a selection containing anything the plan never proposed', () => {
    const good = plan()
    expect(selectionIsValid(good, proposedIds(good))).toBe(true)
    expect(selectionIsValid(good, ['nodes[99].config.params#1'])).toBe(false)
    // 空选择是合法的（只剪枝、不替换）
    expect(selectionIsValid(good, [])).toBe(true)
    expect(selectionIsValid(null, ['anything'])).toBe(false)
  })
})

describe('plan counts feed the UI copy', () => {
  it('counts every table including the prune preview', () => {
    const counts = countPlan(
      plan({
        ambiguous: [
          {
            id: 'x#2',
            path: 'nodes[1].config.params',
            nodeId: 'tool-1',
            occurrence: 2,
            excerpt: 'e',
            value: '299',
            candidates: ['amount', 'threshold'],
            reason: '同值多键，机器不选',
          },
        ],
        skipped: [
          {
            id: 'y#1',
            path: 'nodes[2].config.summary',
            nodeId: 'appr-1',
            occurrence: 1,
            excerpt: 'e',
            value: '12345',
            inputKey: 'order_id',
            candidates: ['order_id'],
            reason: '非替换位',
          },
        ],
      }),
    )

    expect(counts).toEqual({ proposals: 2, ambiguous: 1, skipped: 1, kept: 1, dropped: 1 })
    expect(pruneWarningCounts(plan())).toBe(1)
    expect(countPlan(null)).toEqual({ proposals: 0, ambiguous: 0, skipped: 0, kept: 0, dropped: 0 })
  })

  it('names the downloaded guide after the case and the language actually used', () => {
    expect(guideFilename('rec-1', 'zh')).toBe('atlas-guide-rec-1-zh.md')
    expect(guideFilename('rec-1', 'en')).toBe('atlas-guide-rec-1-en.md')
  })
})
