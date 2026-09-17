/**
 * validationStore 纯状态测试（M4 批 2 ⑧）：不挂 React，直接驱动 getState 动作，
 * 再用 rank 复算全图 Problems 序列（useProblems hook 的同一逻辑）。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { useValidationStore } from '../validationStore'
import { rank, type Diagnostic } from '../../lib/validation/diagnostics'

function diag(overrides: Partial<Diagnostic>): Diagnostic {
  return {
    severity: 'error',
    layer: 'field',
    code: 'X',
    message: 'x',
    loc: {},
    ...overrides,
  }
}

afterEach(() => {
  useValidationStore.getState().reset()
})

describe('validationStore', () => {
  it('patchNodes 合并节点诊断并推进 computedRevision', () => {
    const store = useValidationStore.getState()
    store.patchNodes({ 'a-1': [diag({ message: 'a' })] }, 1)
    store.patchNodes({ 'b-1': [diag({ message: 'b' })] }, 3)
    const state = useValidationStore.getState()
    expect(state.nodeDiagnostics['a-1']).toHaveLength(1)
    expect(state.computedRevision).toBe(3)
  })

  it('setGraph 存图级诊断与拓扑序；全图 Problems 经 rank：error 先于 warning、按拓扑序', () => {
    const store = useValidationStore.getState()
    store.patchNodes(
      {
        'b-1': [diag({ severity: 'warning', message: 'wb', loc: { nodeId: 'b-1' } })],
        'a-1': [diag({ severity: 'error', message: 'ea', loc: { nodeId: 'a-1' } })],
      },
      1,
    )
    store.setGraph(
      [diag({ severity: 'error', layer: 'graph', code: 'GRAPH_UNREACHABLE', message: 'g', loc: { nodeId: 'c-1' } })],
      ['a-1', 'b-1', 'c-1'],
      1,
    )
    const state = useValidationStore.getState()
    const problems = rank(
      [...Object.values(state.nodeDiagnostics).flat(), ...state.graphDiagnostics],
      state.nodeOrder,
    )
    expect(problems.map((p) => p.message)).toEqual(['ea', 'g', 'wb'])
  })

  it('pruneNodes 清除已删节点残留', () => {
    const store = useValidationStore.getState()
    store.patchNodes({ 'a-1': [diag({})], 'dead-1': [diag({})] }, 1)
    useValidationStore.getState().pruneNodes(['a-1'])
    expect(useValidationStore.getState().nodeDiagnostics['dead-1']).toBeUndefined()
    expect(useValidationStore.getState().nodeDiagnostics['a-1']).toHaveLength(1)
  })
})
