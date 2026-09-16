import { describe, expect, it } from 'vitest'
import { buildScopeIndex, extractTemplateRefs, type JsonSchema, type ScopeNodeLike, type ScopeEdgeLike } from '../scope'

const node = (id: string, kind: string, config: Record<string, unknown> = {}): ScopeNodeLike => ({
  id,
  kind,
  config,
})
const edge = (source: string, target: string): ScopeEdgeLike => ({ source, target })

const REFUND_RESULT_SCHEMA: JsonSchema = {
  type: 'object',
  properties: {
    order_id: { type: 'string' },
    status: { type: 'string', enum: ['refunded', 'human_review'] },
  },
  required: ['order_id', 'status'],
}

const QUERY_SCHEMA: JsonSchema = {
  type: 'object',
  properties: {
    columns: { type: 'array', items: { type: 'string' } },
    rows: { type: 'array' },
    row_count: { type: 'integer' },
    truncated: { type: 'boolean' },
  },
}

const HTTP_SCHEMA: JsonSchema = {
  type: 'object',
  properties: {
    status: { type: 'integer' },
    headers: { type: 'object' },
    body: {},
  },
}

const TOOL_SCHEMAS: Record<string, JsonSchema> = {
  'shop/execute_refund': REFUND_RESULT_SCHEMA,
  'database/query': QUERY_SCHEMA,
  'http/request': HTTP_SCHEMA,
}

describe('extractTemplateRefs', () => {
  it('提取路径与含 {{}} 的 token 区间', () => {
    const refs = extractTemplateRefs('a {{ trigger-1.context.payload.x }} b {{global.limit}}')
    expect(refs).toHaveLength(2)
    expect(refs[0].path).toBe('trigger-1.context.payload.x')
    expect(refs[0].start).toBe(2)
    expect(refs[0].end).toBe(2 + '{{ trigger-1.context.payload.x }}'.length)
    expect(refs[1]).toMatchObject({ path: 'global.limit', start: 38, end: 54 })
  })

  it('无引用时返回空', () => {
    expect(extractTemplateRefs('plain text')).toEqual([])
  })
})

describe('buildScopeIndex 拓扑可见性', () => {
  // trigger -> ai -> tool；diamond: ai -> c1 -> join, ai -> c2 -> join；join 之后 approval
  const nodes = [
    node('trigger-1', 'trigger'),
    node('ai-1', 'ai_decision', { promptTemplate: '' }),
    node('c1', 'tool_call', { tool: 'shop/execute_refund' }),
    node('c2', 'tool_call', { tool: 'database/query' }),
    node('join-1', 'wait'),
  ]
  const edges = [
    edge('trigger-1', 'ai-1'),
    edge('ai-1', 'c1'),
    edge('ai-1', 'c2'),
    edge('c1', 'join-1'),
    edge('c2', 'join-1'),
  ]
  const scope = buildScopeIndex(nodes, edges, [{ name: 'limit' }])

  it('沿入边反向可达 + trigger 恒可见', () => {
    const visibleAtJoin = scope.visibleNodeIdsAt('join-1')
    expect([...visibleAtJoin].sort()).toEqual(['ai-1', 'c1', 'c2', 'trigger-1'])
  })

  it('菱形互不可见的并列分支不可互相引用', () => {
    expect(scope.visibleNodeIdsAt('c1').has('c2')).toBe(false)
    expect(scope.visibleNodeIdsAt('c2').has('c1')).toBe(false)
  })

  it('下游对上游不可见', () => {
    expect(scope.visibleNodeIdsAt('ai-1').has('c1')).toBe(false)
  })

  it('插入清单只列当前节点可见路径并展开工具 output_schema', () => {
    const paths = scope.listPathsAt('join-1', TOOL_SCHEMAS)
    expect(paths).toContain('global.limit')
    expect(paths).toContain('trigger-1.context.payload')
    expect(paths).toContain('ai-1.decision')
    expect(paths).toContain('c1.result.order_id')
    expect(paths).toContain('c1.result.status')
    expect(paths).toContain('c2.result.row_count')
    expect(paths.some((p) => p.startsWith('join-1.'))).toBe(false)
  })

  it('c1 看不到并列分支工具与自身', () => {
    const paths = scope.listPathsAt('c1', TOOL_SCHEMAS)
    expect(paths.some((p) => p.startsWith('c2.'))).toBe(false)
    expect(paths.some((p) => p.startsWith('c1.'))).toBe(false)
    expect(paths).toContain('trigger-1.context.payload')
  })
})

describe('loop 循环体区域', () => {
  const nodes = [
    node('trigger-1', 'trigger'),
    node('loop-1', 'loop', { bodyTarget: 'body-1', exitTarget: 'exit-1' }),
    node('body-1', 'tool_call', { tool: 'database/query' }),
    node('exit-1', 'wait'),
  ]
  const edges = [
    edge('trigger-1', 'loop-1'),
    edge('loop-1', 'body-1'),
    edge('body-1', 'loop-1'),
    edge('body-1', 'exit-1'),
  ]
  const scope = buildScopeIndex(nodes, edges, [])

  it('循环体内可引用 loop.index/iterations', () => {
    expect(scope.listPathsAt('body-1')).toContain('loop-1.index')
    const diagnostics = scope.validateRefsAt(
      'body-1',
      'tool_call',
      { params: '{{loop-1.index}}' },
      TOOL_SCHEMAS,
    )
    expect(diagnostics).toEqual([])
  })

  it('循环节点自身的继续条件可引用自身 index/iterations', () => {
    expect(
      scope.validateRefsAt('loop-1', 'loop', { continueExpression: '{{loop-1.index}} < 3' }),
    ).toEqual([])
    expect(
      scope.validateRefsAt('loop-1', 'loop', { continueExpression: '{{loop-1.target}}' })[0]?.code,
    ).toBe('REF_NOT_IN_SCOPE')
  })

  it('退出目标引用 loop.index → REF_NOT_IN_SCOPE', () => {
    expect(scope.listPathsAt('exit-1').some((p) => p.startsWith('loop-1.'))).toBe(false)
    const diagnostics = scope.validateRefsAt(
      'exit-1',
      'wait',
      { durationSeconds: 1 },
    )
    // wait 无模板字段；用 body 外的 ai 节点模拟引用
    const probe = buildScopeIndex(
      [...nodes, node('probe-1', 'ai_decision')],
      [...edges, edge('exit-1', 'probe-1')],
      [],
    )
    const found = probe.validateRefsAt(
      'probe-1',
      'ai_decision',
      { promptTemplate: '{{loop-1.index}}' },
    )
    expect(diagnostics).toEqual([])
    expect(found.map((d) => d.code)).toEqual(['REF_NOT_IN_SCOPE'])
  })
})

describe('L2 引用诊断三码', () => {
  const nodes = [
    node('trigger-1', 'trigger'),
    node('tool-1', 'tool_call', { tool: 'shop/execute_refund' }),
    node('query-1', 'tool_call', { tool: 'database/query' }),
    node('http-1', 'tool_call', { tool: 'http/request' }),
    node('ai-1', 'ai_decision'),
    node('parallel-1', 'parallel'),
    node('sub-1', 'subgraph'),
    node('approval-1', 'human_approval'),
  ]
  const edges = [
    edge('trigger-1', 'tool-1'),
    edge('tool-1', 'query-1'),
    edge('query-1', 'http-1'),
    edge('http-1', 'parallel-1'),
    edge('parallel-1', 'sub-1'),
    edge('sub-1', 'approval-1'),
    edge('approval-1', 'ai-1'),
  ]
  const scope = buildScopeIndex(nodes, edges, [{ name: 'limit' }])
  const validate = (template: string) =>
    scope.validateRefsAt('ai-1', 'ai_decision', { promptTemplate: template }, TOOL_SCHEMAS)

  it('REF_NODE_NOT_FOUND：节点不存在', () => {
    expect(validate('{{ghost-1.result.x}}')[0]).toMatchObject({ code: 'REF_NODE_NOT_FOUND' })
  })

  it('REF_NODE_NOT_FOUND：未声明全局变量', () => {
    const d = validate('{{global.unknown}}')
    expect(d).toHaveLength(1)
    expect(d[0].code).toBe('REF_NODE_NOT_FOUND')
  })

  it('REF_NOT_IN_SCOPE：自身与下游不可引用', () => {
    expect(validate('{{ai-1.decision}}').map((d) => d.code)).toEqual(['REF_NOT_IN_SCOPE'])
  })

  it('REF_PATH_NOT_FOUND：触发器仅 context 四键', () => {
    expect(validate('{{trigger-1.wrong.payload}}')[0].code).toBe('REF_PATH_NOT_FOUND')
    expect(validate('{{trigger-1.context.secret}}')[0].code).toBe('REF_PATH_NOT_FOUND')
  })

  it('trigger context.payload 深层任意放行（无静态 schema）', () => {
    expect(validate('{{trigger-1.context.payload.order_id}}')).toEqual([])
  })

  it('REF_PATH_NOT_FOUND：工具输出深层路径按 output_schema 判定', () => {
    expect(validate('{{tool-1.result.refunded}}')[0].code).toBe('REF_PATH_NOT_FOUND')
    expect(validate('{{query-1.result.row_count}}')).toEqual([])
  })

  it('空 schema（http body）深层任意放行；status 存在', () => {
    expect(validate('{{http-1.result.body.data[0].x}}')).toEqual([])
    expect(validate('{{http-1.result.status}}')).toEqual([])
  })

  it('parallel.result 动态入口放行，静态键深层/错名拒绝', () => {
    expect(validate('{{parallel-1.result.branch-1.x}}')).toEqual([])
    expect(validate('{{parallel-1.status}}')).toEqual([])
    expect(validate('{{parallel-1.branches.x}}')[0].code).toBe('REF_PATH_NOT_FOUND')
  })

  it('subgraph.outputs 深层放行，status 为标量根', () => {
    expect(validate('{{sub-1.outputs.child.deep}}')).toEqual([])
    expect(validate('{{sub-1.status}}')).toEqual([])
    expect(validate('{{sub-1.graphId}}')[0].code).toBe('REF_PATH_NOT_FOUND')
  })

  it('内置模板风格引用全部合法（回归）', () => {
    expect(
      validate(
        '{{trigger-1.context.payload.order_id}} {{global.limit}} {{tool-1.result.status}} {{query-1.result.row_count}} {{approval-1.decision}} {{http-1.result.status}}',
      ),
    ).toEqual([])
  })

  it('无 schema 表时工具深层路径一律放行（发现失败不阻塞编辑）', () => {
    const d = scope.validateRefsAt('ai-1', 'ai_decision', {
      promptTemplate: '{{tool-1.result.anything}}',
    })
    expect(d).toEqual([])
  })

  it('多字段聚合：一次返回全部问题', () => {
    const d = scope.validateRefsAt(
      'ai-1',
      'condition',
      {
        branches: [
          { label: 'a', expression: '{{ghost-1.x}}', target: 'x' },
          { label: 'b', expression: '{{ai-1.decision}}', target: 'y' },
        ],
      },
      TOOL_SCHEMAS,
    )
    expect(d.map((x) => x.code)).toEqual(['REF_NODE_NOT_FOUND', 'REF_NOT_IN_SCOPE'])
  })

  it('无任何引用 → 零诊断', () => {
    expect(validate('普通文本 1 > 0')).toEqual([])
  })
})

describe('L2 结构化诊断映射 (U37④)', () => {
  it('诊断携带 layer/nodeId/pointer/token(raw 为含 {{}} 的源串切片)', () => {
    const scope = buildScopeIndex([node('ai-1', 'ai_decision', { promptTemplate: '' })], [], [])
    const text = '判断 {{ghost-1.x}} 是否成立'
    const diagnostics = scope.validateRefsAt('ai-1', 'ai_decision', { promptTemplate: text })
    expect(diagnostics).toHaveLength(1)
    const diagnostic = diagnostics[0]
    expect(diagnostic.severity).toBe('error')
    expect(diagnostic.layer).toBe('template')
    expect(diagnostic.code).toBe('REF_NODE_NOT_FOUND')
    expect(diagnostic.message).toBe('引用的节点不存在：{{ghost-1.x}}')
    expect(diagnostic.loc.nodeId).toBe('ai-1')
    expect(diagnostic.loc.pointer).toBe('/promptTemplate')
    const token = text.indexOf('{{ghost-1.x}}')
    expect(diagnostic.loc.token).toEqual({
      start: token,
      end: token + '{{ghost-1.x}}'.length,
      raw: '{{ghost-1.x}}',
    })
    // 带内部空白的引用：raw 保留源串切片，message 仍用规整化路径（文案不变）。
    const spaced = scope.validateRefsAt('ai-1', 'ai_decision', { promptTemplate: '{{ ghost-1.x }}' })
    expect(spaced[0].loc.token?.raw).toBe('{{ ghost-1.x }}')
    expect(spaced[0].message).toBe('引用的节点不存在：{{ghost-1.x}}')
  })

  it('condition 分支与 subgraph 入参映射到字段 pointer（键做 RFC6901 转义）', () => {
    const scope = buildScopeIndex([node('c-1', 'condition')], [], [])
    const condition = scope.validateRefsAt(
      'c-1',
      'condition',
      { branches: [{ label: 'a', expression: '{{ghost-1.x}}', target: 't' }] },
    )
    expect(condition[0].loc.pointer).toBe('/branches/0/expression')

    const subScope = buildScopeIndex([node('sub-1', 'subgraph')], [], [])
    const subgraph = subScope.validateRefsAt(
      'sub-1',
      'subgraph',
      { graphId: 'g-1', inputs: { 'a/b': '{{ghost-1.x}}' } },
    )
    expect(subgraph[0].loc.nodeId).toBe('sub-1')
    expect(subgraph[0].loc.pointer).toBe('/inputs/a~1b')
  })

  it('extractTemplateRefs 返回 raw 切片', () => {
    const refs = extractTemplateRefs('x {{ global.limit }} y')
    expect(refs[0].raw).toBe('{{ global.limit }}')
    expect(refs[0].path).toBe('global.limit')
  })
})
