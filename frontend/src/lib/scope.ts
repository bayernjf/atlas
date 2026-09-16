/**
 * 拓扑变量作用域索引 + L2 模板引用校验（04 §6.5，2026-09-16 Phase 2 延伸项）。
 *
 * 可见性按图拓扑推导：global 恒可见；trigger 恒可见；其余节点仅在引用方
 * 沿入边反向可达时可见；loop 的 index/iterations 仅循环体内可见；
 * parallel.result / subgraph.outputs 的动态深层路径只列根、不做存在性判定。
 * v1 不做类型级校验（D30），运行期插值缺失保留原样的语义不变。
 */

import type { GraphVariable } from './variables'
import type { Diagnostic } from './validation/diagnostics'
import { escapePointerToken } from './validation/diagnostics'

export type JsonSchema = {
  type?: string
  properties?: Record<string, JsonSchema>
  required?: string[]
  items?: JsonSchema
  additionalProperties?: boolean | JsonSchema
  oneOf?: JsonSchema[]
  [keyword: string]: unknown
}

export type ScopeNodeLike = {
  id: string
  kind: string
  config?: Record<string, unknown>
}

export type ScopeEdgeLike = {
  source: string
  target: string
}

export type RefCode = 'REF_NODE_NOT_FOUND' | 'REF_NOT_IN_SCOPE' | 'REF_PATH_NOT_FOUND'

export type TemplateRef = {
  path: string
  start: number
  end: number
  /** 源串切片（含 {{}}）。 */
  raw: string
}

/** 模板字段定位：RFC 6901 pointer（相对节点 config 根）+ 字段文本。 */
export type TemplateFieldLocation = {
  pointer: string
  text: string
}

const TEMPLATE_RE = /\{\{\s*([^{}]+?)\s*\}\}/g

/** 提取模板中的全部引用及 token 区间（含 {{}}，保持出现顺序，不去重）。 */
export function extractTemplateRefs(text: string): TemplateRef[] {
  const refs: TemplateRef[] = []
  for (const match of text.matchAll(TEMPLATE_RE)) {
    if (match.index === undefined) continue
    refs.push({
      path: match[1],
      start: match.index,
      end: match.index + match[0].length,
      raw: match[0],
    })
  }
  return refs
}

const STATIC_OUTPUT_KEYS: Record<string, string[]> = {
  ai_decision: ['decision', 'prompt_rendered'],
  condition: ['branch', 'target'],
  loop: ['index', 'iterations'],
  parallel: ['status', 'branches', 'joinStrategy', 'joinTarget'],
  wait: ['mode', 'waitType', 'durationSeconds'],
  subgraph: ['status', 'outputs'],
  human_approval: ['decision', 'target', 'summary', 'approver', 'resolvedBy'],
}

const TRIGGER_CONTEXT_KEYS = ['triggerType', 'cron', 'webhookUrl', 'payload']

/** 节点 config 中可能含 {{路径}} 的字符串字段（04 §6.5）；pointer 相对 config 根（RFC 6901）。 */
export function templateFields(kind: string, config: Record<string, unknown>): TemplateFieldLocation[] {
  const fields: TemplateFieldLocation[] = []
  const push = (pointer: string, value: unknown) => {
    if (typeof value === 'string') fields.push({ pointer, text: value })
  }
  switch (kind) {
    case 'ai_decision':
      push('/promptTemplate', config.promptTemplate)
      break
    case 'tool_call':
      push('/params', config.params)
      break
    case 'condition':
      for (const [index, branch] of (Array.isArray(config.branches) ? config.branches : []).entries()) {
        if (branch && typeof branch === 'object') {
          push(`/branches/${index}/expression`, (branch as Record<string, unknown>).expression)
        }
      }
      break
    case 'loop':
      push('/continueExpression', config.continueExpression)
      break
    case 'human_approval':
      push('/summary', config.summary)
      break
    case 'subgraph': {
      const inputs = config.inputs
      if (inputs && typeof inputs === 'object') {
        for (const [key, value] of Object.entries(inputs as Record<string, unknown>)) {
          push(`/inputs/${escapePointerToken(key)}`, value)
        }
      }
      break
    }
    default:
      break
  }
  return fields
}

function bfs(start: Set<string>, adjacency: Map<string, Set<string>>, stop?: Set<string>): Set<string> {
  const seen = new Set<string>()
  const queue = [...start]
  while (queue.length > 0) {
    const current = queue.shift()!
    if (seen.has(current) || stop?.has(current)) continue
    seen.add(current)
    for (const next of adjacency.get(current) ?? []) {
      if (!seen.has(next) && !stop?.has(next)) queue.push(next)
    }
  }
  return seen
}

export type ScopeIndex = {
  /** 当前节点可引用的节点 id 集合（不含自身；trigger/global 另行恒可见）。 */
  visibleNodeIdsAt: (nodeId: string) => Set<string>
  /** 变量插入清单：global.* + 可见节点输出投影（loop 仅体内）。 */
  listPathsAt: (
    nodeId: string,
    toolOutputSchemas?: Record<string, JsonSchema>,
  ) => string[]
  /** 对单个节点的模板字段做 L2 引用校验，一次性聚合（layer:'template' 诊断）。 */
  validateRefsAt: (
    nodeId: string,
    kind: string,
    config: Record<string, unknown>,
    toolOutputSchemas?: Record<string, JsonSchema>,
  ) => Diagnostic[]
}

export function buildScopeIndex(
  nodes: ScopeNodeLike[],
  edges: ScopeEdgeLike[],
  variables: Pick<GraphVariable, 'name'>[] = [],
): ScopeIndex {
  const nodeById = new Map(nodes.map((node) => [node.id, node]))
  const incoming = new Map<string, Set<string>>()
  const outgoing = new Map<string, Set<string>>()
  for (const edge of edges) {
    if (!incoming.has(edge.target)) incoming.set(edge.target, new Set())
    incoming.get(edge.target)!.add(edge.source)
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, new Set())
    outgoing.get(edge.source)!.add(edge.target)
  }

  const triggerIds = new Set(nodes.filter((node) => node.kind === 'trigger').map((node) => node.id))
  const globalNames = new Set(variables.map((variable) => variable.name))

  // 复刻 dsl.py _loop_body_set：自 bodyTarget 沿出边 BFS，遇 loop 自身/exitTarget 停。
  const loopBodies = new Map<string, Set<string>>()
  for (const node of nodes) {
    if (node.kind !== 'loop') continue
    const bodyTarget = node.config?.bodyTarget
    const exitTarget = node.config?.exitTarget
    if (typeof bodyTarget !== 'string' || !bodyTarget) continue
    const stop = new Set([node.id])
    if (typeof exitTarget === 'string' && exitTarget) stop.add(exitTarget)
    loopBodies.set(node.id, bfs(new Set([bodyTarget]), outgoing, stop))
  }

  const reverseCache = new Map<string, Set<string>>()
  const visibleNodeIdsAt = (nodeId: string): Set<string> => {
    const cached = reverseCache.get(nodeId)
    if (cached) return cached
    const visible = bfs(new Set([nodeId]), incoming)
    visible.delete(nodeId)
    for (const triggerId of triggerIds) visible.add(triggerId)
    reverseCache.set(nodeId, visible)
    return visible
  }

  const inLoopBody = (nodeId: string, loopId: string): boolean =>
    loopBodies.get(loopId)?.has(nodeId) ?? false

  function outputKeyPrefixes(node: ScopeNodeLike, viewerId: string): string[] | null {
    if (node.kind === 'loop') {
      return inLoopBody(viewerId, node.id) ? STATIC_OUTPUT_KEYS.loop : []
    }
    if (node.kind === 'trigger') return ['context']
    if (node.kind === 'tool_call') return ['result']
    return STATIC_OUTPUT_KEYS[node.kind] ?? null
  }

  function schemaPaths(schema: JsonSchema | undefined, prefix: string): string[] {
    if (!schema || schema.oneOf || schema.additionalProperties !== undefined) return [prefix]
    if (schema.type === 'object' && schema.properties) {
      const paths = [prefix]
      for (const [key, sub] of Object.entries(schema.properties)) {
        if (sub.type === 'object' && sub.properties) {
          paths.push(...schemaPaths(sub, `${prefix}.${key}`))
        } else {
          paths.push(`${prefix}.${key}`)
        }
      }
      return paths
    }
    return [prefix]
  }

  function toolResultPaths(node: ScopeNodeLike, toolOutputSchemas?: Record<string, JsonSchema>): string[] {
    const tool = typeof node.config?.tool === 'string' ? node.config.tool : ''
    const schema = tool ? toolOutputSchemas?.[tool] : undefined
    return schemaPaths(schema, `${node.id}.result`)
  }

  const listPathsAt = (nodeId: string, toolOutputSchemas?: Record<string, JsonSchema>): string[] => {
    const globals = [...globalNames].map((name) => `global.${name}`)
    const paths: string[] = [...globals]
    const visible = visibleNodeIdsAt(nodeId)
    for (const node of nodes) {
      if (!visible.has(node.id)) continue
      const prefixes = outputKeyPrefixes(node, nodeId)
      if (prefixes === null) continue
      if (node.kind === 'trigger') {
        for (const key of TRIGGER_CONTEXT_KEYS) paths.push(`${node.id}.context.${key}`)
        continue
      }
      if (node.kind === 'tool_call') {
        paths.push(...toolResultPaths(node, toolOutputSchemas))
        continue
      }
      for (const key of prefixes) paths.push(`${node.id}.${key}`)
    }
    return paths
  }

  function schemaHasPath(schema: JsonSchema | undefined, segments: string[]): boolean {
    // 无 schema 约束（未注册工具/空 schema/开放对象）→ 无法静态判定，放行。
    if (!schema || Object.keys(schema).length === 0 || schema.oneOf) return true
    if (segments.length === 0) return true
    const [head, ...rest] = segments
    if (schema.type === 'array' || schema.items) {
      if (/^\d+$/.test(head)) return schemaHasPath(schema.items, rest)
      // 数组上的非下标属性：无 items 信息时放行
      return !schema.items
    }
    if (schema.additionalProperties !== undefined) {
      if (schema.properties && head in schema.properties) {
        return schemaHasPath(schema.properties[head], rest)
      }
      if (schema.additionalProperties === true) return true
      if (typeof schema.additionalProperties === 'object') {
        return schemaHasPath(schema.additionalProperties, rest)
      }
    }
    if (schema.properties && head in schema.properties) {
      return schemaHasPath(schema.properties[head], rest)
    }
    if (!schema.type && !schema.properties) return true
    return false
  }

  const validateRefsAt: ScopeIndex['validateRefsAt'] = (nodeId, kind, config, toolOutputSchemas) => {
    const diagnostics: Diagnostic[] = []
    const push = (field: TemplateFieldLocation, ref: TemplateRef, code: RefCode, message: string) => {
      diagnostics.push({
        severity: 'error',
        layer: 'template',
        code,
        message,
        loc: {
          nodeId,
          pointer: field.pointer,
          token: { start: ref.start, end: ref.end, raw: ref.raw },
        },
      })
    }
    for (const field of templateFields(kind, config)) {
      for (const ref of extractTemplateRefs(field.text)) {
        const segments = ref.path.split('.').filter(Boolean)
        if (segments.length === 0) continue
        const head = segments[0]

        if (head === 'global') {
          const name = segments[1]
          if (!name || !globalNames.has(name)) {
            push(field, ref, 'REF_NODE_NOT_FOUND', `未声明的全局变量：{{${ref.path}}}`)
          }
          continue
        }

        const node = nodeById.get(head)
        if (!node) {
          push(field, ref, 'REF_NODE_NOT_FOUND', `引用的节点不存在：{{${ref.path}}}`)
          continue
        }

        const visible = visibleNodeIdsAt(nodeId)
        const outputKey = segments[1]
        const loopSelfIndex =
          node.kind === 'loop' && node.id === nodeId && (outputKey === 'index' || outputKey === 'iterations')
        const loopBlocked =
          node.kind === 'loop' && node.id !== nodeId && !inLoopBody(nodeId, node.id)
        if ((node.id === nodeId && !loopSelfIndex) || loopBlocked || (!visible.has(node.id) && !loopSelfIndex)) {
          push(
            field,
            ref,
            'REF_NOT_IN_SCOPE',
            `节点 ${node.id} 在当前位置不可见（不是上游，或循环变量越出循环体）：{{${ref.path}}}`,
          )
          continue
        }

        const tail = segments.slice(1)
        if (tail.length === 0) {
          push(
            field,
            ref,
            'REF_PATH_NOT_FOUND',
            `引用缺少输出字段（应为 {{${ref.path}.<字段>}}）：{{${ref.path}}}`,
          )
          continue
        }

        if (node.kind === 'trigger') {
          const [contextKey, ...rest] = tail
          if (contextKey !== 'context' || !TRIGGER_CONTEXT_KEYS.includes(rest[0] ?? '')) {
            push(
              field,
              ref,
              'REF_PATH_NOT_FOUND',
              `触发器输出路径不存在（context 下仅 ${TRIGGER_CONTEXT_KEYS.join('/')}）：{{${ref.path}}}`,
            )
            continue
          }
          // payload 内部来自运行 inputs，无静态 schema，深层任意放行
          continue
        }

        if (node.kind === 'tool_call') {
          const [root, ...rest] = tail
          if (root !== 'result') {
            push(field, ref, 'REF_PATH_NOT_FOUND', `工具节点仅暴露 result 输出：{{${ref.path}}}`)
            continue
          }
          const tool = typeof node.config?.tool === 'string' ? node.config.tool : ''
          const outputSchema = tool ? toolOutputSchemas?.[tool] : undefined
          if (!schemaHasPath(outputSchema, rest)) {
            push(
              field,
              ref,
              'REF_PATH_NOT_FOUND',
              `工具 ${tool || '未选择'} 的输出中不存在该路径：{{${ref.path}}}`,
            )
          }
          continue
        }

        if (node.kind === 'parallel') {
          const [root, ...rest] = tail
          if (root === 'result') continue // 动态入口键（D30），深层放行
          if (!STATIC_OUTPUT_KEYS.parallel.includes(root) || rest.length > 0) {
            push(
              field,
              ref,
              'REF_PATH_NOT_FOUND',
              `并行节点输出路径不存在（status/branches/joinStrategy/joinTarget，result 为动态入口）：{{${ref.path}}}`,
            )
          }
          continue
        }

        if (node.kind === 'subgraph') {
          const [root, ...rest] = tail
          if (root === 'outputs') continue // 子图深层展开缓做 D30，放行
          if (!STATIC_OUTPUT_KEYS.subgraph.includes(root) || rest.length > 0) {
            push(
              field,
              ref,
              'REF_PATH_NOT_FOUND',
              `子图节点输出路径不存在（status / outputs 根）：{{${ref.path}}}`,
            )
          }
          continue
        }

        const staticKeys = STATIC_OUTPUT_KEYS[node.kind]
        if (staticKeys) {
          const [root, ...rest] = tail
          if (!staticKeys.includes(root) || rest.length > 0) {
            push(field, ref, 'REF_PATH_NOT_FOUND', `节点 ${node.id} 的输出中不存在该路径：{{${ref.path}}}`)
          }
        }
      }
    }
    return diagnostics
  }

  return { visibleNodeIdsAt, listPathsAt, validateRefsAt }
}
