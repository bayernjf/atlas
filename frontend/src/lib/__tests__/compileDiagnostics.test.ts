import { afterEach, describe, expect, it, vi } from 'vitest'

import { changeLanguage, getLanguage } from '../../locales'
import { useValidationStore } from '../../store/validationStore'
import { dedupeServerDiagnostics } from '../validation/diagnostics'
import type { Diagnostic } from '../validation/diagnostics'
import {
  buildCompileIssues,
  compileIssueMessage,
  CompileValidationError,
  saveGraph,
} from '../apiClient'

/** docs/61 §2 / 打包 H H1：编译 422 稀疏 locations 侧车的逐条归位与前端接线。 */

function stubBody(body: unknown, status = 422) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: false,
      status,
      json: async () => body,
    })),
  )
}

function diag(overrides: Partial<Diagnostic> & { code: string }): Diagnostic {
  return {
    severity: 'error',
    layer: 'field',
    message: 'm',
    loc: {},
    ...overrides,
  }
}

const graph = {} as never

afterEach(() => {
  vi.unstubAllGlobals()
  changeLanguage('zh-CN')
  useValidationStore.getState().reset()
})

describe('buildCompileIssues：locations 稀疏侧车按 index 归位（U743–U746）', () => {
  it('图级错误无 locations 条目 → 不挂 nodeId/pointer', () => {
    const issues = buildCompileIssues(['图中存在数据环'], ['GRAPH_DATA_CYCLE'], [{}], [])
    expect(issues).toHaveLength(1)
    expect(issues[0]).toMatchObject({ index: 0, code: 'GRAPH_DATA_CYCLE' })
    expect(issues[0].nodeId).toBeUndefined()
    expect(issues[0].pointer).toBeUndefined()
  })

  it('节点级条目按 index 挂回 nodeId 与 RFC6901 pointer', () => {
    const issues = buildCompileIssues(
      ['条件分支缺少表达式'],
      ['COND_BRANCHES_REQUIRED'],
      [{ owner: 'cond-1' }],
      [{ index: 0, nodeId: 'cond-1', pointer: '/branches/0/expression' }],
    )
    expect(issues[0].nodeId).toBe('cond-1')
    expect(issues[0].pointer).toBe('/branches/0/expression')
  })

  it('稀疏混合：只有部分条目带定位，其余保持缺省', () => {
    const issues = buildCompileIssues(
      ['a', 'b', 'c'],
      ['C1', 'C2', 'C3'],
      [{}, {}, {}],
      [{ index: 2, nodeId: 'n-3', pointer: '/x' }],
    )
    expect(issues.map((issue) => issue.nodeId)).toEqual([undefined, undefined, 'n-3'])
    expect(issues[1].pointer).toBeUndefined()
  })

  it('codes/params 与 detail 不等长时整列忽略；越界与非对象 locations 条目丢弃', () => {
    const issues = buildCompileIssues(
      ['a', 'b'],
      ['ONLY_ONE'],
      undefined,
      [{ index: 9, nodeId: 'ghost' }, null, { index: 1, nodeId: 'n-2' }],
    )
    expect(issues.every((issue) => issue.code === undefined)).toBe(true)
    expect(issues.every((issue) => issue.params === undefined)).toBe(true)
    expect(issues.map((issue) => issue.nodeId)).toEqual([undefined, 'n-2'])
  })

  it('detail 非数组返回空快照', () => {
    expect(buildCompileIssues('boom', undefined, undefined, undefined)).toEqual([])
  })
})

describe('compileIssueMessage：渲染期解析与中文兜底（U747–U749）', () => {
  it('有码命中中文模板并插值 params（数组先 join）', () => {
    expect(
      compileIssueMessage(
        { index: 0, code: 'NODE_ID_DUPLICATE', message: '中文兜底', params: { nodeId: 'a' } },
        'zh-CN',
      ),
    ).toBe('节点 id 重复：a')
  })

  it('缺码/未知码回退后端中文原文，绝不泄漏 i18n key', () => {
    const fallback = '后端才知道的措辞'
    expect(compileIssueMessage({ index: 0, message: fallback }, 'zh-CN')).toBe(fallback)
    expect(
      compileIssueMessage({ index: 0, code: 'NO_SUCH_CODE_AT_ALL', message: fallback }, 'zh-CN'),
    ).toBe(fallback)
  })

  it('语言由入参决定：全局仍是中文态，显式 en-US 也渲染英文且不含汉字', () => {
    const text = compileIssueMessage(
      {
        index: 0,
        code: 'PAR_STRATEGY_INVALID',
        message: '中文兜底',
        params: { strategies: ['wait_all', 'wait_any'], owner: 'par-1' },
      },
      'en-US',
    )
    expect(getLanguage()).toBe('zh-CN')
    expect(text).not.toMatch(/[一-鿿]/)
    expect(text).toContain('wait_all, wait_any')
  })
})

describe('request 422 抛错形状（U750–U751）', () => {
  it('detail 为数组时抛 CompileValidationError，message 仍与逐条拼接串逐字一致', async () => {
    stubBody({
      detail: ['条件分支缺少表达式', '图中存在数据环'],
      codes: ['COND_BRANCHES_REQUIRED', 'GRAPH_DATA_CYCLE'],
      params: [{}, {}],
      locations: [{ index: 0, nodeId: 'cond-1', pointer: '/branches/0/expression' }],
    })
    const error = await saveGraph(graph).catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(CompileValidationError)
    const compileError = error as CompileValidationError
    expect(compileError.issues).toHaveLength(2)
    expect(compileError.issues[0].nodeId).toBe('cond-1')
    expect(compileError.message).toBe(
      compileError.issues.map((issue) => compileIssueMessage(issue, 'zh-CN')).join('；'),
    )
  })

  it('detail 为对象时仍是普通 Error，不携带 issues', async () => {
    // 用一个既非认证码也非运行码的 code，走 rec.message 兜底分支。
    stubBody({ detail: { code: 'OPENAPI_NOT_SOFT_DELETED', message: '请先软删除' } }, 409)
    const error = await saveGraph(graph).catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(Error)
    expect(error).not.toBeInstanceOf(CompileValidationError)
    expect((error as Error).message).toBe('请先软删除')
  })
})

describe('dedupeServerDiagnostics：三元组去重、本地优先（U752–U754）', () => {
  it('同 (nodeId, pointer, code) 被本地吞掉，本地没有的条目保留', () => {
    const local = [diag({ code: 'COND_BRANCHES_REQUIRED', loc: { nodeId: 'n-1', pointer: '/x' } })]
    const server = [
      diag({ code: 'COND_BRANCHES_REQUIRED', layer: 'server', loc: { nodeId: 'n-1', pointer: '/x' } }),
      diag({ code: 'GRAPH_DATA_CYCLE', layer: 'server', loc: {} }),
    ]
    const merged = dedupeServerDiagnostics(local, server)
    expect(merged).toHaveLength(2)
    expect(merged[1].code).toBe('GRAPH_DATA_CYCLE')
  })

  it('pointer 不同即视为不同诊断（同一字段改过才报新的一条）', () => {
    const local = [diag({ code: 'C', loc: { nodeId: 'n-1', pointer: '/a' } })]
    const server = [diag({ code: 'C', layer: 'server', loc: { nodeId: 'n-1', pointer: '/b' } })]
    expect(dedupeServerDiagnostics(local, server)).toHaveLength(2)
  })

  it('server 全被吞时返回本地原数组引用；不修改入参', () => {
    const local = [diag({ code: 'C', loc: { nodeId: 'n-1' } })]
    const server = [diag({ code: 'C', layer: 'server', loc: { nodeId: 'n-1' } })]
    expect(dedupeServerDiagnostics(local, server)).toBe(local)
    expect(dedupeServerDiagnostics(local, [])).toBe(local)
    expect(local).toHaveLength(1)
    expect(server).toHaveLength(1)
  })
})

describe('validationStore 陈旧基准（U754 续）', () => {
  it('setServerIssues 记下当时的 computedRevision，引擎重算后基准即失配', () => {
    const store = useValidationStore.getState()
    store.setServerIssues([{ index: 0, code: 'C', message: 'm' }])
    expect(useValidationStore.getState().serverIssuesRevision).toBe(-1)
    store.patchNodes({ 'n-1': [diag({ code: 'L1', loc: { nodeId: 'n-1' } })] }, 7)
    expect(useValidationStore.getState().computedRevision).toBe(7)
    expect(useValidationStore.getState().serverIssuesRevision).not.toBe(7)
  })

  it('clearServerIssues 与 reset 都清空快照与基准', () => {
    useValidationStore.getState().setServerIssues([{ index: 0, message: 'm' }])
    useValidationStore.getState().clearServerIssues()
    expect(useValidationStore.getState().serverIssues).toEqual([])
    expect(useValidationStore.getState().serverIssuesRevision).toBe(-1)
    useValidationStore.getState().setServerIssues([{ index: 0, message: 'm' }])
    useValidationStore.getState().reset()
    expect(useValidationStore.getState().serverIssues).toEqual([])
  })
})
