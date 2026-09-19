import { describe, expect, it } from 'vitest'
import {
  buildReverseIndex,
  removeDanglingRef,
  pointerSegments,
  rewriteTemplateHeads,
  renameNodeRefs,
  TARGET_FIELD_PATHS,
} from '../reverseDeps'
import type { ScopeNodeLike } from '../../scope'
import { schemaRegistry } from '../../schemas'
import type { JsonSchema } from '../../scope'

function node(id: string, kind: string, config: Record<string, unknown>): ScopeNodeLike {
  return { id, kind, config }
}

describe('buildReverseIndex：target 类引用', () => {
  it('condition branches/defaultTarget', () => {
    const nodes = [
      node('condition-1', 'condition', {
        branches: [
          { label: 'A', expression: 'x', target: 'tool-a' },
          { label: 'B', expression: 'y', target: 'tool-b' },
        ],
        defaultTarget: 'tool-c',
      }),
      node('tool-a', 'tool_call', {}),
      node('tool-b', 'tool_call', {}),
      node('tool-c', 'tool_call', {}),
    ]
    const index = buildReverseIndex(nodes)
    expect(index.referrersOf('tool-a').map((dep) => dep.pointer)).toEqual(['/branches/0/target'])
    expect(index.referrersOf('tool-b').map((dep) => dep.pointer)).toEqual(['/branches/1/target'])
    expect(index.referrersOf('tool-c').map((dep) => dep.pointer)).toEqual(['/defaultTarget'])
    expect(index.referrersOf('tool-a')[0].kind).toBe('target')
    expect(index.referrersOf('tool-a')[0].referrerId).toBe('condition-1')
  })

  it('loop body/exit、parallel branches/join、human_approval 双 target', () => {
    const nodes = [
      node('loop-1', 'loop', { bodyTarget: 'n-body', exitTarget: 'n-exit' }),
      node('parallel-1', 'parallel', {
        branches: [
          { label: 'A', target: 'n-a' },
          { label: 'B', target: 'n-b' },
        ],
        joinTarget: 'n-join',
      }),
      node('human-1', 'human_approval', { approvedTarget: 'n-ok', rejectedTarget: 'n-no' }),
      node('n-body', 'wait', {}),
      node('n-exit', 'wait', {}),
      node('n-a', 'wait', {}),
      node('n-b', 'wait', {}),
      node('n-join', 'wait', {}),
      node('n-ok', 'wait', {}),
      node('n-no', 'wait', {}),
    ]
    const index = buildReverseIndex(nodes)
    const pointersOf = (id: string) => index.referrersOf(id).map((dep) => dep.pointer)
    expect(pointersOf('n-body')).toEqual(['/bodyTarget'])
    expect(pointersOf('n-exit')).toEqual(['/exitTarget'])
    expect(pointersOf('n-a')).toEqual(['/branches/0/target'])
    expect(pointersOf('n-b')).toEqual(['/branches/1/target'])
    expect(pointersOf('n-join')).toEqual(['/joinTarget'])
    expect(pointersOf('n-ok')).toEqual(['/approvedTarget'])
    expect(pointersOf('n-no')).toEqual(['/rejectedTarget'])
  })

  it('空 target 值不登记', () => {
    const nodes = [node('condition-1', 'condition', { branches: [{ label: 'A', expression: 'x', target: '' }], defaultTarget: '' })]
    const index = buildReverseIndex(nodes)
    expect(index.referrersOf('')).toEqual([])
  })
})

describe('buildReverseIndex：template 类引用', () => {
  it('模板字段 {{节点id.路径}} 登记 token 区间', () => {
    const nodes = [
      node('ai-1', 'ai_decision', { promptTemplate: '前置 {{tool-1.result.order_id}} 后置' }),
      node('tool-1', 'tool_call', {}),
    ]
    const index = buildReverseIndex(nodes)
    const deps = index.referrersOf('tool-1')
    expect(deps).toHaveLength(1)
    expect(deps[0].kind).toBe('template')
    expect(deps[0]).toMatchObject({
      referrerId: 'ai-1',
      pointer: '/promptTemplate',
      refNodeId: 'tool-1',
    })
    if (deps[0].kind !== 'template') throw new Error('expected template dep')
    expect(deps[0].token.raw).toBe('{{tool-1.result.order_id}}')
    expect('前置 {{tool-1.result.order_id}} 后置'.slice(deps[0].token.start, deps[0].token.end)).toBe(
      '{{tool-1.result.order_id}}',
    )
  })

  it('global.* 引用不登记到节点反向索引', () => {
    const nodes = [
      node('ai-1', 'ai_decision', { promptTemplate: '{{global.amount}}' }),
      node('global', 'trigger', {}),
    ]
    expect(buildReverseIndex(nodes).referrersOf('global')).toEqual([])
  })
})

describe('removeDanglingRef', () => {
  it('模板 token：从字段文本删除引用', () => {
    const text = '前置 {{ghost-1.x}} 后置'
    const start = text.indexOf('{{ghost-1.x}}')
    const config = { promptTemplate: text }
    const patch = removeDanglingRef('ai_decision', config, '/promptTemplate', {
      start,
      end: start + '{{ghost-1.x}}'.length,
      raw: '{{ghost-1.x}}',
    })
    expect(patch).toEqual({ promptTemplate: '前置  后置' })
  })

  it('模板 token：数组分支 expression', () => {
    const config = {
      branches: [
        { label: 'A', expression: '{{ghost-1.x}} > 1', target: 'tool-a' },
        { label: 'B', expression: 'ok', target: 'tool-b' },
      ],
      defaultTarget: 'tool-a',
    }
    const text = config.branches[0].expression
    const start = text.indexOf('{{ghost-1.x}}')
    const patch = removeDanglingRef('condition', config, '/branches/0/expression', {
      start,
      end: start + '{{ghost-1.x}}'.length,
      raw: '{{ghost-1.x}}',
    })
    expect(patch).toEqual({
      branches: [
        { label: 'A', expression: ' > 1', target: 'tool-a' },
        { label: 'B', expression: 'ok', target: 'tool-b' },
      ],
    })
  })

  it('target：普通字段清空', () => {
    expect(
      removeDanglingRef('loop', { bodyTarget: 'ghost', exitTarget: 'n-exit' }, '/bodyTarget'),
    ).toEqual({ bodyTarget: '' })
    expect(
      removeDanglingRef('human_approval', { approvedTarget: 'ghost' }, '/approvedTarget'),
    ).toEqual({ approvedTarget: '' })
    expect(
      removeDanglingRef('condition', { defaultTarget: 'ghost' }, '/defaultTarget'),
    ).toEqual({ defaultTarget: '' })
    expect(
      removeDanglingRef('parallel', { joinTarget: 'ghost' }, '/joinTarget'),
    ).toEqual({ joinTarget: '' })
  })

  it('target：数组分支清空对应下标', () => {
    const config = {
      branches: [
        { label: 'A', target: 'tool-a' },
        { label: 'B', target: 'ghost' },
      ],
    }
    expect(removeDanglingRef('condition', config, '/branches/1/target')).toEqual({
      branches: [
        { label: 'A', target: 'tool-a' },
        { label: 'B', target: '' },
      ],
    })
  })

  it('未声明的 target 字段拒绝清空（防误清）', () => {
    expect(removeDanglingRef('ai_decision', { promptTemplate: 'x' }, '/promptTemplate')).toBeNull()
    expect(removeDanglingRef('loop', { bodyTarget: 'x' }, '/unknownField')).toBeNull()
  })

  it('token 越界/字段缺失返回 null', () => {
    expect(
      removeDanglingRef('ai_decision', { promptTemplate: 'abc' }, '/promptTemplate', {
        start: 10,
        end: 20,
        raw: '{{x}}',
      }),
    ).toBeNull()
    expect(
      removeDanglingRef('ai_decision', {}, '/promptTemplate', {
        start: 0,
        end: 5,
        raw: '{{x}}',
      }),
    ).toBeNull()
  })
})

describe('TARGET_FIELD_PATHS 与 schema x-ref 标注对拍（防漂移）', () => {
  function collectXRefPaths(node: JsonSchema, prefix = ''): string[] {
    const paths: string[] = []
    if (node['x-ref']) paths.push(prefix || '/')
    const props = node.properties
    if (props) {
      for (const [key, sub] of Object.entries(props)) {
        const path = `${prefix}/${key}`
        const subSchema = sub as JsonSchema
        if (subSchema['x-ref']) paths.push(path)
        if (subSchema.type === 'array' && subSchema.items) {
          paths.push(...collectXRefPaths(subSchema.items as JsonSchema, `${path}/{i}`))
        } else if (subSchema.properties) {
          paths.push(...collectXRefPaths(subSchema, path))
        }
      }
    }
    return paths
  }

  it('四类节点的 x-ref 路径与路径表逐条一致，其余节点无 x-ref', () => {
    for (const kind of schemaRegistry.registeredKinds()) {
      const schemaPaths = collectXRefPaths(schemaRegistry.get(kind)).sort()
      const tablePaths = (TARGET_FIELD_PATHS[kind] ?? []).map((field) => field.pointer).sort()
      expect(schemaPaths).toEqual(tablePaths)
    }
  })
})

describe('pointerSegments', () => {
  it('RFC6901 解码', () => {
    expect(pointerSegments('/branches/0/target')).toEqual(['branches', '0', 'target'])
    expect(pointerSegments('/inputs/a~1b')).toEqual(['inputs', 'a/b'])
  })
})

describe('D30/B3 rewriteTemplateHeads 模板头段改写', () => {
  it('替换头段并保留空白与后续路径；根引用与多 token 正确', () => {
    expect(rewriteTemplateHeads('单号 {{tool-a.result.x}} 完成', 'tool-a', 'tool-z')).toBe(
      '单号 {{tool-z.result.x}} 完成',
    )
    expect(rewriteTemplateHeads('{{ tool-a.result.x }}', 'tool-a', 'tool-z')).toBe(
      '{{ tool-z.result.x }}',
    )
    expect(rewriteTemplateHeads('{{tool-a}}', 'tool-a', 'tool-z')).toBe('{{tool-z}}')
    expect(rewriteTemplateHeads('{{tool-a.x}}-{{tool-b.y}}-{{tool-a.z}}', 'tool-a', 'tool-z')).toBe(
      '{{tool-z.x}}-{{tool-b.y}}-{{tool-z.z}}',
    )
  })

  it('无匹配原样返回；前缀相似不误伤', () => {
    expect(rewriteTemplateHeads('{{tool-ab.x}}', 'tool-a', 'tool-z')).toBe('{{tool-ab.x}}')
    expect(rewriteTemplateHeads('无引用文本', 'tool-a', 'tool-z')).toBe('无引用文本')
  })
})

describe('D30/B3 renameNodeRefs 重命名联动', () => {
  it('联动 target（含 branches 数组）与模板，按引用方折叠；被改名节点自身不入结果', () => {
    const nodes = [
      node('condition-1', 'condition', {
        branches: [
          { label: 'A', expression: 'x', target: 'tool-a' },
          { label: 'B', expression: 'y', target: 'tool-b' },
        ],
        defaultTarget: 'tool-a',
      }),
      node('ai-1', 'ai_decision', {
        promptTemplate: '看 {{tool-a.result.x}} 和 {{tool-a.result.y}}',
        model: 'demo',
      }),
      node('tool-a', 'tool_call', {}),
      node('tool-b', 'tool_call', {}),
    ]
    const edits = renameNodeRefs(nodes, 'tool-a', 'tool-z')
    const cond = edits.get('condition-1') as Record<string, any>
    expect(cond.branches[0].target).toBe('tool-z')
    expect(cond.branches[1].target).toBe('tool-b')
    expect(cond.defaultTarget).toBe('tool-z')
    const ai = edits.get('ai-1') as Record<string, any>
    expect(ai.promptTemplate).toBe('看 {{tool-z.result.x}} 和 {{tool-z.result.y}}')
    expect(edits.has('tool-a')).toBe(false)
  })

  it('无引用时返回空 Map', () => {
    const edits = renameNodeRefs(
      [node('a', 'tool_call', {}), node('b', 'tool_call', {})],
      'a',
      'z',
    )
    expect(edits.size).toBe(0)
  })
})
