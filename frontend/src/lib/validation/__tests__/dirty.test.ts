import { describe, expect, it } from 'vitest'
import {
  INITIAL_DIRTY,
  markClean,
  markConfigEdit,
  markConsumed,
  markEdgeChanged,
  markGraphLoaded,
  markNodeAdded,
  markNodeDeleted,
  markNodeMetaEdit,
  markVariablesChanged,
  patchTouchesReferences,
} from '../dirty'

describe('patchTouchesReferences（config 变更是否触及 L2 引用）', () => {
  it('target / 表达式 / 分支 / 模板字段判为承载引用', () => {
    expect(patchTouchesReferences({ approvedTarget: 'x-1' })).toBe(true)
    expect(patchTouchesReferences({ bodyTarget: 'x-1' })).toBe(true)
    expect(patchTouchesReferences({ defaultTarget: 'x-1' })).toBe(true)
    expect(patchTouchesReferences({ continueExpression: 'x' })).toBe(true)
    expect(patchTouchesReferences({ promptTemplate: 'x' })).toBe(true)
    expect(patchTouchesReferences({ branches: [] })).toBe(true)
    expect(patchTouchesReferences({ params: '{}' })).toBe(true)
  })

  it('字符串值里出现 {{ 兜底判为承载引用', () => {
    expect(patchTouchesReferences({ note: '引用 {{trigger-1.x}}' })).toBe(true)
  })

  it('纯数值/枚举/文案字段不承载引用', () => {
    expect(patchTouchesReferences({ timeoutSeconds: 300 })).toBe(false)
    expect(patchTouchesReferences({ onTimeout: 'reject' })).toBe(false)
    expect(patchTouchesReferences({ approver: 'risk-team' })).toBe(false)
    expect(patchTouchesReferences({ maxIterations: 10 })).toBe(false)
    expect(patchTouchesReferences({})).toBe(false)
  })
})

describe('失效范围标记（M4 批 1 ⑤）', () => {
  it('初始仅 L3 待首次校验、revision=0', () => {
    expect(INITIAL_DIRTY).toEqual({ l1NodeIds: [], l2NodeIds: [], l3: true, revision: 0 })
  })

  it('改纯字段只脏该节点 L1，不脏 L2/L3，revision+1', () => {
    const d = markConfigEdit(INITIAL_DIRTY, 'human-1', { timeoutSeconds: 100 })
    expect(d.l1NodeIds).toEqual(['human-1'])
    expect(d.l2NodeIds).toEqual([])
    expect(d.l3).toBe(true) // 初始 l3 未被消费，保持
    expect(d.revision).toBe(1)
  })

  it('改引用字段同时脏该节点 L1+L2，重复编辑同节点去重', () => {
    let d = markConfigEdit(INITIAL_DIRTY, 'loop-1', { bodyTarget: 'a-1' })
    d = markConfigEdit(d, 'loop-1', { exitTarget: 'b-1' })
    expect(d.l1NodeIds).toEqual(['loop-1'])
    expect(d.l2NodeIds).toEqual(['loop-1'])
    expect(d.revision).toBe(2)
  })

  it('新增节点：L3 置位 + 新节点进 L1/L2', () => {
    const clean = markClean(INITIAL_DIRTY)
    const d = markNodeAdded(clean, 'wait-1')
    expect(d.l3).toBe(true)
    expect(d.l1NodeIds).toEqual(['wait-1'])
    expect(d.l2NodeIds).toEqual(['wait-1'])
  })

  it('删除节点：L3 置位、L2 全量剩余、被删节点移出 L1', () => {
    let d = markNodeAdded(markNodeAdded(INITIAL_DIRTY, 'a-1'), 'b-1')
    d = markNodeDeleted(d, ['a-1'])
    expect(d.l3).toBe(true)
    expect(d.l2NodeIds).toEqual(['a-1'])
    expect(d.l1NodeIds).not.toContain('b-1')
  })

  it('边变更：L3 置位 + 端点进 L2（去重）', () => {
    const d = markEdgeChanged(markClean(INITIAL_DIRTY), ['a-1', 'b-1', 'a-1'])
    expect(d.l3).toBe(true)
    expect(d.l2NodeIds).toEqual(['a-1', 'b-1'])
  })

  it('换图：L3 + 全部节点进 L1/L2', () => {
    const d = markGraphLoaded(INITIAL_DIRTY, ['t-1', 'd-1'])
    expect(d.l3).toBe(true)
    expect(d.l1NodeIds).toEqual(['t-1', 'd-1'])
    expect(d.l2NodeIds).toEqual(['t-1', 'd-1'])
  })

  it('全局变量增删：L2 全量、L3 不动', () => {
    const d = markVariablesChanged(markClean(INITIAL_DIRTY), ['t-1', 'd-1'])
    expect(d.l2NodeIds).toEqual(['t-1', 'd-1'])
    expect(d.l3).toBe(false)
  })

  it('markClean 清空三层待算但保留 revision', () => {
    const d = markClean(markNodeAdded(INITIAL_DIRTY, 'a-1'))
    expect(d.l1NodeIds).toEqual([])
    expect(d.l2NodeIds).toEqual([])
    expect(d.l3).toBe(false)
    expect(d.revision).toBe(1)
  })
})

describe('markConfigEdit 的 loop 结构感知（批 2 ⑦）', () => {
  it('loop 改 bodyTarget/exitTarget：L3 置位 + L2 全量', () => {
    const d0 = markClean(INITIAL_DIRTY)
    const d = markConfigEdit(d0, 'loop-1', { bodyTarget: 'tool-a' }, {
      kind: 'loop',
      allNodeIds: ['loop-1', 'tool-a', 'tool-b'],
    })
    expect(d.l3).toBe(true)
    expect(d.l2NodeIds).toEqual(['loop-1', 'tool-a', 'tool-b'])
    expect(d.l1NodeIds).toContain('loop-1')
  })

  it('loop 改 continueExpression：只脏本节点 L1/L2，不置 L3', () => {
    const d0 = markClean(INITIAL_DIRTY)
    const d = markConfigEdit(d0, 'loop-1', { continueExpression: 'x > 0' }, {
      kind: 'loop',
      allNodeIds: ['loop-1', 'tool-a'],
    })
    expect(d.l3).toBe(false)
    expect(d.l2NodeIds).toEqual(['loop-1'])
  })

  it('非 loop 节点改 target：不置 L3', () => {
    const d = markConfigEdit(markClean(INITIAL_DIRTY), 'h-1', { approvedTarget: 'tool-a' }, {
      kind: 'human_approval',
      allNodeIds: ['h-1', 'tool-a'],
    })
    expect(d.l3).toBe(false)
  })
})

describe('markConsumed（按 revision 消费）', () => {
  it('revision 一致：只清实际消费的范围', () => {
    const d0 = markNodeAdded(markClean(INITIAL_DIRTY), 'a-1') // revision 1, l3 true
    const d = markConsumed(d0, { revision: 1, l1NodeIds: ['a-1'] })
    expect(d.l1NodeIds).toEqual([])
    expect(d.l2NodeIds).toContain('a-1')
    expect(d.l3).toBe(true)
    expect(d.revision).toBe(1)
  })

  it('消费期间又有新变更（revision 已增）：原样保留，交下轮重算', () => {
    const d0 = markNodeAdded(markClean(INITIAL_DIRTY), 'a-1')
    const d1 = markConfigEdit(d0, 'a-1', { promptTemplate: 'x' }) // revision 2
    const d = markConsumed(d1, { revision: 1, l1NodeIds: ['a-1'], l3: true })
    expect(d).toEqual(d1)
  })
})

describe('markNodeMetaEdit', () => {
  it('改名只脏该节点 L1，不碰 L2/L3', () => {
    const d = markNodeMetaEdit(markClean(INITIAL_DIRTY), 'a-1')
    expect(d.l1NodeIds).toEqual(['a-1'])
    expect(d.l2NodeIds).toEqual([])
    expect(d.l3).toBe(false)
  })
})
