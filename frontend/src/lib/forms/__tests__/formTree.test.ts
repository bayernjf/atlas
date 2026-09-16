import { describe, expect, it } from 'vitest'
import {
  MAX_FORM_DEPTH,
  appendAtPath,
  buildFormTree,
  defaultValueFor,
  diagnosticsAt,
  getAtPath,
  nextKeyName,
  pathToPointer,
  removeAtPath,
  renameKeyAtPath,
  setAtPath,
  splitTokenSegments,
  type FormArrayNode,
  type FormGroupNode,
  type FormKeyValueNode,
  type FormNode,
  type FormWidgetNode,
} from '../formTree'
import type { MetaSchema } from '../../schemas/metaSchema'
import type { Diagnostic, DiagnosticToken } from '../../validation/diagnostics'

// 九工具 input_schema 实测形态（database/query、http/request、message/send）
const querySchema: MetaSchema = {
  type: 'object',
  properties: {
    sql: { type: 'string', description: 'SELECT 查询语句' },
    params: { description: '命名绑定参数对象' },
    limit: { type: 'integer', minimum: 1, maximum: 1000, default: 500 },
  },
  required: ['sql'],
}

const requestSchema: MetaSchema = {
  type: 'object',
  properties: {
    method: { type: 'string', enum: ['GET', 'POST'] },
    url: { type: 'string' },
    headers: { type: 'object', additionalProperties: { type: 'string' } },
    body: { description: '对象/数组按 JSON 发送' },
    timeout: { type: 'number', minimum: 0 },
  },
  required: ['url'],
}

const sendSchema: MetaSchema = {
  type: 'object',
  properties: {
    channel: { type: 'string' },
    to: { oneOf: [{ type: 'string' }, { type: 'array', items: { type: 'string' }, maxItems: 20 }] },
  },
  required: ['channel', 'to'],
}

function widgetOf(node: ReturnType<typeof buildFormTree>): string {
  return node.kind === 'widget' ? node.widget : node.kind
}

function asWidget(node: FormNode): FormWidgetNode {
  if (node.kind !== 'widget') throw new Error(`期望 widget 节点，实际 ${node.kind}`)
  return node
}

describe('buildFormTree：结构树（U39③）', () => {
  it('object 按 properties 顺序分组，required 标记与 pointer 就位', () => {
    const tree = buildFormTree(querySchema, { sql: 'SELECT 1' }) as FormGroupNode
    expect(tree.kind).toBe('group')
    expect(tree.path).toEqual([])
    expect(tree.pointer).toBe('')
    expect(tree.children.map((child) => child.pointer)).toEqual(['/sql', '/params', '/limit'])
    expect(tree.children.map((child) => child.label)).toEqual(['sql', 'params', 'limit'])
    expect(tree.children.map((child) => child.required)).toEqual([true, false, false])
    expect(tree.children.map(widgetOf)).toEqual(['variable-input', 'json', 'number'])
    expect(asWidget(tree.children[0]).value).toBe('SELECT 1')
    expect(asWidget(tree.children[1]).value).toBeUndefined()
  })

  it('http/request 实测形态：enum→select、additionalProperties-only→键值行、无类型→json', () => {
    const tree = buildFormTree(requestSchema, {
      method: 'GET',
      url: '/orders',
      headers: { 'X-Demo-Token': 'demo-token' },
      timeout: 30,
    }) as FormGroupNode
    expect(tree.children.map(widgetOf)).toEqual(['select', 'variable-input', 'keyvalue', 'json', 'number'])
    const headers = tree.children[2] as FormKeyValueNode
    expect(headers.entries).toEqual([{ key: 'X-Demo-Token', value: 'demo-token' }])
    expect(headers.valueSchema).toEqual({ type: 'string' })
  })

  it('additionalProperties:true 的键值行无值 schema（值侧降级 json）', () => {
    const tree = buildFormTree({ type: 'object', additionalProperties: true }, { a: 1 }) as FormKeyValueNode
    expect(tree.kind).toBe('keyvalue')
    expect(tree.valueSchema).toEqual({})
    expect(widgetOf(buildFormTree(tree.valueSchema, 1))).toBe('json')
  })

  it('message/send 的 to（oneOf）与未声明类型字段一律降级 json', () => {
    const tree = buildFormTree(sendSchema, { channel: 'email', to: 'ops@example.com' }) as FormGroupNode
    expect(tree.children.map((child) => child.pointer)).toEqual(['/channel', '/to'])
    expect(tree.children.map(widgetOf)).toEqual(['variable-input', 'json'])
  })

  it('array 按当前值展开增删行，行 pointer 为数字段', () => {
    const tree = buildFormTree({ type: 'array', items: { type: 'string' } }, ['a', 'b']) as FormArrayNode
    expect(tree.kind).toBe('array')
    expect(tree.items.map((item) => item.pointer)).toEqual(['/0', '/1'])
    expect(tree.items.map((item) => item.label)).toEqual(['#1', '#2'])
    expect(tree.items.map((item) => asWidget(item).value)).toEqual(['a', 'b'])
  })

  it('嵌套 group 递归（array item 为 object 时继续分组）', () => {
    const schema: MetaSchema = {
      type: 'array',
      items: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] },
    }
    const tree = buildFormTree(schema, [{ id: 'o-1' }]) as FormArrayNode
    const row = tree.items[0] as FormGroupNode
    expect(row.kind).toBe('group')
    expect(row.pointer).toBe('/0')
    expect(row.children[0].pointer).toBe('/0/id')
    expect(row.children[0].required).toBe(true)
  })

  it('值形状与 schema 不符时不抛错，按空容器起步（交由 L1 报类型错）', () => {
    const asGroup = buildFormTree(querySchema, 'not-an-object') as FormGroupNode
    expect(asGroup.children.map((child) => asWidget(child).value)).toEqual([
      undefined,
      undefined,
      undefined,
    ])
    const asArray = buildFormTree({ type: 'array', items: { type: 'string' } }, 'nope') as FormArrayNode
    expect(asArray.items).toEqual([])
    const asKeyValue = buildFormTree({ type: 'object', additionalProperties: true }, 42) as FormKeyValueNode
    expect(asKeyValue.entries).toEqual([])
  })

  it('超出递归上限降级 json（防病态 schema 展开）', () => {
    const tree = buildFormTree(querySchema, { sql: 'x' }, { depth: MAX_FORM_DEPTH })
    expect(tree.kind).toBe('widget')
    expect(widgetOf(tree)).toBe('json')
  })

  it('节点 schema 来源下 x-widget 生效', () => {
    const schema: MetaSchema = {
      type: 'object',
      properties: { summary: { type: 'string', 'x-widget': 'textarea' } },
    }
    const asNode = buildFormTree(schema, {}, { source: 'node' }) as FormGroupNode
    expect(widgetOf(asNode.children[0])).toBe('textarea')
  })

  it('工具 params 的字符串字段一律走模板控件（保留既有 {{}} 用 variable-input 承载）', () => {
    const schema: MetaSchema = {
      type: 'object',
      properties: { subject: { type: 'string' }, body: { type: 'string', 'x-widget': 'textarea' } },
    }
    const asTool = buildFormTree(schema, { subject: '订单 {{trigger-1.context.payload.order_id}}' }, {
      source: 'tool',
    }) as FormGroupNode
    expect(asTool.children.map(widgetOf)).toEqual(['variable-input', 'variable-input'])
    // 节点来源仍按 x-widget 显式标注（M3 不消费节点来源）
    const asNode = buildFormTree(schema, {}, { source: 'node' }) as FormGroupNode
    expect(asNode.children.map(widgetOf)).toEqual(['text', 'textarea'])
  })
})

describe('pathToPointer / getAtPath', () => {
  it('pointer 转义 ~ 与 /，数字段不加引号', () => {
    expect(pathToPointer([])).toBe('')
    expect(pathToPointer(['a/b', 'c~d', 0])).toBe('/a~1b/c~0d/0')
  })

  it('getAtPath 读对象与数组，路径不存在返回 undefined', () => {
    const root = { a: { b: [1, 2] } }
    expect(getAtPath(root, ['a', 'b', 1])).toBe(2)
    expect(getAtPath(root, ['a', 'c'])).toBeUndefined()
    expect(getAtPath(root, ['a', 'b', 5])).toBeUndefined()
    expect(getAtPath(root, ['x', 'y'])).toBeUndefined()
  })
})

describe('不可变更新（U39④）', () => {
  it('setAtPath 只复制路径上的容器，原值与未触碰分支引用不变', () => {
    const root = { a: { b: { c: 1 } }, list: [{ x: 1 }], note: 'keep' }
    const next = setAtPath(root, ['a', 'b', 'c'], 2) as typeof root
    expect(next).toEqual({ a: { b: { c: 2 } }, list: [{ x: 1 }], note: 'keep' })
    expect(root.a.b.c).toBe(1)
    expect(next).not.toBe(root)
    expect(next.a).not.toBe(root.a)
    expect(next.a.b).not.toBe(root.a.b)
    expect(next.list).toBe(root.list)
  })

  it('setAtPath 支持数组下标，且不改动入参数组', () => {
    const root = { list: [{ x: 1 }, { x: 2 }] }
    const next = setAtPath(root, ['list', 1, 'x'], 9) as typeof root
    expect(next.list).toEqual([{ x: 1 }, { x: 9 }])
    expect(root.list).toEqual([{ x: 1 }, { x: 2 }])
    expect(next.list[0]).toBe(root.list[0])
  })

  it('setAtPath 空路径直接返回新根（整对象替换）', () => {
    expect(setAtPath({ a: 1 }, [], { b: 2 })).toEqual({ b: 2 })
  })

  it('setAtPath 缺失中间层按路径段类型建容器', () => {
    expect(setAtPath({}, ['a', 'b'], 1)).toEqual({ a: { b: 1 } })
    expect(setAtPath({}, ['a', 0], 'x')).toEqual({ a: ['x'] })
  })

  it('removeAtPath 删数组下标走 splice、删对象键走 delete，入参不变', () => {
    const root = { list: [1, 2, 3], keep: true }
    const next = removeAtPath(root, ['list', 1]) as typeof root
    expect(next).toEqual({ list: [1, 3], keep: true })
    expect(root.list).toEqual([1, 2, 3])
    expect(removeAtPath({ a: 1, b: 2 }, ['a'])).toEqual({ b: 2 })
    expect(removeAtPath({ a: 1 }, [])).toEqual({ a: 1 })
  })

  it('appendAtPath 追加行；目标非数组时按空数组起步', () => {
    expect(appendAtPath({ to: ['a'] }, ['to'], 'b')).toEqual({ to: ['a', 'b'] })
    expect(appendAtPath({ to: undefined }, ['to'], 'a')).toEqual({ to: ['a'] })
    expect(appendAtPath(['a'], [], 'b')).toEqual(['a', 'b'])
  })

  it('renameKeyAtPath 保持键顺序，冲突/空名/来源缺失时原样返回同一引用', () => {
    const root = { headers: { A: '1', B: '2' }, note: 'keep' }
    const next = renameKeyAtPath(root, ['headers'], 'A', 'X') as {
      headers: Record<string, string>
      note: string
    }
    expect(Object.keys(next.headers)).toEqual(['X', 'B'])
    expect(next.headers.X).toBe('1')
    expect(root.headers).toEqual({ A: '1', B: '2' })
    expect(renameKeyAtPath(root, ['headers'], 'A', 'B')).toBe(root)
    expect(renameKeyAtPath(root, ['headers'], 'A', '')).toBe(root)
    expect(renameKeyAtPath(root, ['headers'], 'Z', 'W')).toBe(root)
    expect(renameKeyAtPath(root, ['note'], 'note', 'x')).toBe(root)
  })

  it('未在 schema 声明的键在编辑后原样保留（不做参数裁剪）', () => {
    const root = { sql: 'SELECT 1', unbounded: { keep: true } }
    const next = setAtPath(root, ['sql'], 'SELECT 2') as typeof root
    expect(next.unbounded).toBe(root.unbounded)
    expect(next).toEqual({ sql: 'SELECT 2', unbounded: { keep: true } })
  })

  it('变更结果可 JSON.stringify 回写成合法 JSON 字符串（params 字符串存储不变）', () => {
    const root = JSON.parse('{"sql":"SELECT 1","params":{"min":1000},"limit":500}')
    const next = setAtPath(root, ['params', 'min'], 2000)
    const text = JSON.stringify(next)
    expect(typeof text).toBe('string')
    expect(JSON.parse(text)).toEqual({ sql: 'SELECT 1', params: { min: 2000 }, limit: 500 })
  })
})

describe('defaultValueFor / nextKeyName（增行初值）', () => {
  it('default → const → enum 首项 → 类型零值', () => {
    expect(defaultValueFor({ type: 'integer', default: 500 })).toBe(500)
    expect(defaultValueFor({ type: 'string', const: 'fixed' })).toBe('fixed')
    expect(defaultValueFor({ type: 'string', enum: ['email', 'sms'] })).toBe('email')
    expect(defaultValueFor({ type: 'string' })).toBe('')
    expect(defaultValueFor({ type: 'integer' })).toBe(0)
    expect(defaultValueFor({ type: 'boolean' })).toBe(false)
    expect(defaultValueFor({ type: 'array', items: { type: 'string' } })).toEqual([])
    expect(defaultValueFor({ type: 'object', properties: {} })).toEqual({})
    expect(defaultValueFor({ description: '无类型' })).toBe('')
  })

  it('nextKeyName 依次避让既有键', () => {
    expect(nextKeyName([])).toBe('新字段')
    expect(nextKeyName(['新字段'])).toBe('新字段2')
    expect(nextKeyName(['新字段', '新字段2'])).toBe('新字段3')
    expect(nextKeyName(['X-Token'], 'X-Token')).toBe('X-Token2')
  })
})

describe('diagnosticsAt（字段级诊断命中）', () => {
  const diagnostics: Diagnostic[] = [
    { severity: 'error', layer: 'field', code: 'FIELD_REQUIRED', message: '该字段必填', loc: { pointer: '/sql' } },
    { severity: 'warning', layer: 'template', code: 'REF_NODE_NOT_FOUND', message: '引用不存在', loc: {} },
    { severity: 'warning', layer: 'field', code: 'FIELD_RANGE', message: '数值超出允许范围', loc: { pointer: '/limit' } },
  ]

  it('精确匹配 pointer；无 pointer 的诊断按根命中', () => {
    expect(diagnosticsAt(diagnostics, '/sql').map((item) => item.code)).toEqual(['FIELD_REQUIRED'])
    expect(diagnosticsAt(diagnostics, '/limit').map((item) => item.code)).toEqual(['FIELD_RANGE'])
    expect(diagnosticsAt(diagnostics, '').map((item) => item.code)).toEqual(['REF_NODE_NOT_FOUND'])
    expect(diagnosticsAt(diagnostics, '/params')).toEqual([])
    expect(diagnosticsAt(undefined, '/sql')).toEqual([])
  })
})

describe('splitTokenSegments（U39⑥ 字段内 {{}} 高亮）', () => {
  const text = '订单 {{trigger-1.context.payload.order_id}} 待审批 {{ghost-1.result}}'
  const good = '{{trigger-1.context.payload.order_id}}'
  const bad = '{{ghost-1.result}}'
  const goodToken: DiagnosticToken = {
    start: text.indexOf(good),
    end: text.indexOf(good) + good.length,
    raw: good,
  }
  const badToken: DiagnosticToken = {
    start: text.indexOf(bad),
    end: text.indexOf(bad) + bad.length,
    raw: bad,
  }

  it('按 token 区间切段、乱序 markers 先排序、严重度来自命中诊断', () => {
    const diagnostics: Diagnostic[] = [
      { severity: 'warning', layer: 'template', code: 'REF_NOT_IN_SCOPE', message: '上游不可见', loc: { pointer: '/subject', token: goodToken } },
      { severity: 'error', layer: 'template', code: 'REF_NODE_NOT_FOUND', message: '引用不存在', loc: { pointer: '/subject', token: badToken } },
    ]
    const segments = splitTokenSegments(text, [badToken, goodToken], diagnostics)
    expect(segments.map((segment) => segment.text).join('')).toBe(text)
    expect(segments.map((segment) => [segment.text, segment.severity ?? null])).toEqual([
      ['订单 ', null],
      [good, 'warning'],
      [' 待审批 ', null],
      [bad, 'error'],
    ])
  })

  it('无命中诊断的 token 按 error 呈现（markers 只能来自诊断）', () => {
    const token: DiagnosticToken = { start: 0, end: bad.length, raw: bad }
    expect(splitTokenSegments(bad, [token])).toEqual([{ text: bad, token, severity: 'error' }])
  })

  it('越界与重叠区间跳过且不丢字符', () => {
    expect(splitTokenSegments('ab', [{ start: 5, end: 9, raw: 'x' }])).toEqual([{ text: 'ab' }])
    const overlapping: DiagnosticToken[] = [
      { start: 1, end: 6, raw: '{{b}}' },
      { start: 2, end: 5, raw: '{b}' },
    ]
    expect(splitTokenSegments('a{{b}}c', overlapping).map((segment) => segment.text)).toEqual([
      'a',
      '{{b}}',
      'c',
    ])
  })

  it('无 markers 或空文本时原样返回（不高亮）', () => {
    expect(splitTokenSegments('abc', undefined)).toEqual([{ text: 'abc' }])
    expect(splitTokenSegments('', undefined)).toEqual([])
  })
})
