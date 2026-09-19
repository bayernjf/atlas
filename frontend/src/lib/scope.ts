/**
 * 拓扑变量作用域索引 + L2 模板引用校验（04 §6.5，2026-09-16 Phase 2 延伸项）。
 *
 * 可见性按图拓扑推导：global 恒可见；trigger 恒可见；其余节点仅在引用方
 * 沿入边反向可达时可见；loop 的 index/iterations 仅循环体内可见；
 * parallel.result 仅汇聚点之后可见、入口 id 对照 branches 校验（其下深层动态放行，D30/B1）；
 * subgraph.outputs：注入子图内部节点索引时校验 outputs.<内部节点id> 存在性（D30/B2），
 * 未注入（编辑器尚未加载子图结构）时降级仅放行 outputs 根。
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

export type RefCode =
  | 'REF_NODE_NOT_FOUND'
  | 'REF_NOT_IN_SCOPE'
  | 'REF_PATH_NOT_FOUND'
  | 'REF_TYPE_MISMATCH'

/** M4 批 3 ⑪ quickFix v1 唯一动作：删除悬空引用（20 §2.5 法定，08 M4 立项条⑥）。 */
export const DELETE_DANGLING_REF_FIX = Object.freeze({
  id: 'delete-dangling-ref',
  title: '删除悬空引用',
})

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
  human_approval: ['decision', 'target', 'summary', 'approver', 'resolvedBy', 'comment', 'card'],
}

/** ai_decision.decision 固定结构允许的一层子键（M8 审批卡引用 decision.reason）。 */
const AI_DECISION_KEYS = ['action', 'reason', 'confidence', 'source']

/**
 * M8 卡片只读字段 bindings 索引：卡片 id → 该卡 FieldsSection 各 binding 的 ``{{路径}}`` 串。
 * 来自后端 /api/cards 目录（前端不硬编码，避免漂移）；action.output 的 ``{{form.*}}``
 * 不经节点作用域，故不纳入。
 */
export type CardBindings = Map<string, string[]>

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

/**
 * M8：human_approval 命中交互卡片时，把卡片 FieldsSection 的只读 bindings 追加为
 * 本节点的模板字段（pointer 落在 /cardTemplateId），与 summary 同走一个可见集；
 * 未配置卡片或目录未就绪时原样返回（后端编译期另有强校验兜底）。
 */
/**
 * 沿 JSON Schema 的 properties/items 走到 segments 末端，返回末端 type；
 * 不可静态判定（空 schema/oneOf/开放 additionalProperties/路径缺失）返回 null。
 */
export function jsonSchemaTypeAt(
  schema: JsonSchema | undefined,
  segments: string[],
): string | null {
  if (!schema || Object.keys(schema).length === 0 || schema.oneOf) return null
  let current: JsonSchema = schema
  for (const segment of segments) {
    if (current.oneOf || current.additionalProperties !== undefined) return null
    if (current.type === 'array' || current.items) {
      if (/^\d+$/.test(segment)) {
        if (!current.items) return null
        current = current.items
        continue
      }
      return null
    }
    if (current.properties && segment in current.properties) {
      const next = current.properties[segment]
      if (!next || next.oneOf) return null
      current = next
      continue
    }
    return null
  }
  return typeof current.type === 'string' ? current.type : null
}

const SCALAR_JSON_TYPES = new Set(['string', 'number', 'integer', 'boolean'])

/** 仅标量返回类型词；object/array/null/缺 type 返回 null（不可比对、放行）。 */
function scalarTypeOf(type: string | null): string | null {
  return type && SCALAR_JSON_TYPES.has(type) ? type : null
}

/**
 * 标量赋值兼容：相同兼容；期望 number 接受 integer 源（integer 是 number 子类型）；
 * 期望 integer 不接受 number 源（可能带小数，D30 约定对此警告）；其余不兼容。
 */
function scalarAssignable(expected: string, actual: string): boolean {
  if (expected === actual) return true
  if (expected === 'number' && actual === 'integer') return true
  return false
}

export type ParamTemplateLeaf = { pointer: string; text: string }

/**
 * 解析 tool_call params JSON，收集「值整体为单个 {{模板}}、无拼接」的叶子
 * （pointer 为相对 params 根的 RFC6901）。params 非合法 JSON 返回 []（结构问题由
 * L1/保存校验承接，类型校验降级放行）；拼接串、对象/数组叶子不收（无法静态定型）。
 */
export function extractSingleTemplateLeaves(paramsText: string): ParamTemplateLeaf[] {
  let data: unknown
  try {
    data = JSON.parse(paramsText)
  } catch {
    return []
  }
  const leaves: ParamTemplateLeaf[] = []
  const walk = (value: unknown, pointer: string): void => {
    if (typeof value === 'string') {
      const refs = extractTemplateRefs(value)
      if (refs.length === 1 && refs[0].raw === value.trim()) {
        leaves.push({ pointer, text: value })
      }
      return
    }
    if (Array.isArray(value)) {
      value.forEach((item, index) => walk(item, `${pointer}/${index}`))
      return
    }
    if (value && typeof value === 'object') {
      for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
        walk(child, `${pointer}/${escapePointerToken(key)}`)
      }
    }
  }
  walk(data, '')
  return leaves
}

function appendCardFields(
  fields: TemplateFieldLocation[],
  config: Record<string, unknown>,
  cardBindings?: CardBindings,
): TemplateFieldLocation[] {
  const cardId = config.cardTemplateId
  if (typeof cardId !== 'string' || !cardId) return fields
  const bindings = cardBindings?.get(cardId)
  if (!bindings || bindings.length === 0) return fields
  return [...fields, ...bindings.map((text) => ({ pointer: '/cardTemplateId', text }))]
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
    toolInputSchemas?: Record<string, JsonSchema>,
    cardBindings?: CardBindings,
  ) => Diagnostic[]
  /**
   * D30：收集「通过可见性判定」的模板数据依赖边 viewer→provider（同构后端
   * _validate_template_refs 顺带产出的 data_edges），供 L3 检测 GRAPH_DATA_CYCLE。
   */
  dataDependencyEdges: () => Array<{ viewer: string; provider: string; pointer: string }>
}

export function buildScopeIndex(
  nodes: ScopeNodeLike[],
  edges: ScopeEdgeLike[],
  variables: Pick<GraphVariable, 'name'>[] = [],
  // D30/B2：subgraph 节点 id -> 被引子图内部节点 id 集（由调用方注入已加载子图结构）；
  // 缺省/未加载的子图不在表中，outputs 深层降级放行（后端编译期为权威门，前端随 D21 接入拉取）。
  subgraphOutputs: Map<string, Set<string>> = new Map(),
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

  // D30/B1：parallel 汇聚区域（同构后端 _parallel_meta）——result.<入口> 仅汇聚点之后可见。
  // entries=branches 目标；region=各入口沿出边 BFS、止于 parallel 自身与 joinTarget（不含二者）。
  const parallelMeta = new Map<string, { entries: Set<string>; region: Set<string> }>()
  for (const pNode of nodes) {
    if (pNode.kind !== 'parallel') continue
    const joinTarget = pNode.config?.joinTarget
    const entries = new Set<string>()
    const branches = Array.isArray(pNode.config?.branches) ? pNode.config.branches : []
    for (const branch of branches) {
      const target = branch && typeof branch === 'object' ? (branch as Record<string, unknown>).target : undefined
      if (typeof target === 'string' && nodeById.has(target)) entries.add(target)
    }
    const stop = new Set([pNode.id])
    if (typeof joinTarget === 'string') stop.add(joinTarget)
    let region = new Set<string>()
    for (const entry of entries) region = new Set([...region, ...bfs(new Set([entry]), outgoing, stop)])
    parallelMeta.set(pNode.id, { entries, region })
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

  const validateRefsAt: ScopeIndex['validateRefsAt'] = (
    nodeId,
    kind,
    config,
    toolOutputSchemas,
    toolInputSchemas,
    cardBindings,
  ) => {
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
        // M4 批 3 ⑪：悬空引用（REF_NODE_NOT_FOUND）挂「删除悬空引用」quickFix（20 §2.5 法定）。
        quickFix: code === 'REF_NODE_NOT_FOUND' ? [DELETE_DANGLING_REF_FIX] : undefined,
      })
    }
    const fields = appendCardFields(templateFields(kind, config), config, cardBindings)
    for (const field of fields) {
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
          if (root === 'result') {
            const meta = parallelMeta.get(node.id)
            // B1：result 是汇聚产出，分支区域内（汇聚点之前）尚未产出，不可见。
            if (meta && meta.region.has(nodeId)) {
              push(
                field,
                ref,
                'REF_NOT_IN_SCOPE',
                `并行结果在汇聚点之后才可用（分支区域内尚未汇聚）：{{${ref.path}}}`,
              )
              continue
            }
            // result.<入口id>：入口须为 branches 目标；入口下深层为分支产出，动态放行。
            // branches 未配置（entries 空）时降级，配置缺失归 L1/拓扑校验，不双重报错。
            const entryId = rest[0]
            if (meta && meta.entries.size > 0 && entryId !== undefined && !meta.entries.has(entryId)) {
              push(
                field,
                ref,
                'REF_PATH_NOT_FOUND',
                `并行节点输出入口不存在（result 下须为分支入口节点 id，合法入口：${[...meta.entries].join('/')}）：{{${ref.path}}}`,
              )
            }
            continue
          }
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
          if (root === 'outputs') {
            // D30/B2：注入子图结构则校验 outputs.<内部节点id>；其后深层为内部节点产出，放行。
            // 未注入（子图未加载/跨租户/钉版缺失）降级仅放行 outputs 根。
            const inner = subgraphOutputs.get(node.id)
            if (inner && rest.length > 0 && !inner.has(rest[0])) {
              push(
                field,
                ref,
                'REF_PATH_NOT_FOUND',
                `子图输出中不存在该内部节点（outputs 下须为子图内节点 id，合法：${[...inner].join('/')}）：{{${ref.path}}}`,
              )
            }
            continue
          }
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

        if (node.kind === 'ai_decision') {
          const [root, ...rest] = tail
          let pathOk = false
          if (root === 'decision') {
            // decision 根对象本身合法；其下仅放行一层固定子键（M8 审批卡引用 decision.reason）。
            pathOk =
              rest.length === 0 ||
              (rest.length === 1 && AI_DECISION_KEYS.includes(rest[0]))
          } else if (root === 'prompt_rendered') {
            pathOk = rest.length === 0
          }
          if (!pathOk) {
            push(field, ref, 'REF_PATH_NOT_FOUND', `节点 ${node.id} 的输出中不存在该路径：{{${ref.path}}}`)
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

    // D30 REF_TYPE_MISMATCH（warning，不进后端编译 422）：仅 tool_call params 的单模板
    // 叶子，且引用源同为 tool_call、两端标量类型都可静态判定时比对。object/array/oneOf/
    // 拼接串/缺 schema 一律放行（outputSchema 缺省降级为仅存在性，19 §1.4.3 下限）。
    if (kind === 'tool_call') {
      const toolName = typeof config.tool === 'string' ? config.tool : ''
      const inputSchema = toolName ? toolInputSchemas?.[toolName] : undefined
      const paramsText = typeof config.params === 'string' ? config.params : ''
      if (inputSchema && paramsText) {
        for (const leaf of extractSingleTemplateLeaves(paramsText)) {
          const ref = extractTemplateRefs(leaf.text)[0]
          if (!ref) continue
          const refSegments = ref.path.split('.').filter(Boolean)
          if (refSegments.length === 0 || refSegments[0] === 'global') continue
          const providerNode = nodeById.get(refSegments[0])
          if (!providerNode || providerNode.kind !== 'tool_call') continue
          if (!visibleNodeIdsAt(nodeId).has(providerNode.id)) continue
          const [rootKey, ...restPath] = refSegments.slice(1)
          if (rootKey !== 'result') continue
          const providerTool =
            typeof providerNode.config?.tool === 'string' ? providerNode.config.tool : ''
          const outputSchema = providerTool ? toolOutputSchemas?.[providerTool] : undefined
          const actual = scalarTypeOf(jsonSchemaTypeAt(outputSchema, restPath))
          const expected = scalarTypeOf(
            jsonSchemaTypeAt(inputSchema, leaf.pointer.split('/').slice(1)),
          )
          if (!actual || !expected || scalarAssignable(expected, actual)) continue
          const offset = paramsText.indexOf(ref.raw)
          diagnostics.push({
            severity: 'warning',
            layer: 'template',
            code: 'REF_TYPE_MISMATCH',
            message:
              `参数 ${leaf.pointer || '（根）'} 期望 ${expected}，但引用 ${ref.raw} 的输出末端为 ` +
              `${actual}（REF_TYPE_MISMATCH：类型不匹配，运行期仍按原样插值）`,
            loc: {
              nodeId,
              pointer: '/params',
              token:
                offset >= 0
                  ? { start: offset, end: offset + ref.raw.length, raw: ref.raw }
                  : undefined,
            },
          })
        }
      }
    }
    return diagnostics
  }

  // 同构后端 data_edges：global/不存在/不可见引用不纳边（L2 各诊断承接）；loop 自身
  // index/iterations 是运行时循环计数、不依赖节点配置产出，不纳边（否则每个 loop 成自环）。
  // 仅取节点自身模板字段，不含卡片只读 bindings（后端数据边同样不含卡片字段）。
  const dataDependencyEdges: ScopeIndex['dataDependencyEdges'] = () => {
    const result: Array<{ viewer: string; provider: string; pointer: string }> = []
    for (const viewer of nodes) {
      const config = viewer.config ?? {}
      for (const field of templateFields(viewer.kind, config)) {
        for (const ref of extractTemplateRefs(field.text)) {
          const segments = ref.path.split('.').filter(Boolean)
          if (segments.length === 0) continue
          const head = segments[0]
          if (head === 'global') continue
          const provider = nodeById.get(head)
          if (!provider) continue
          const visible = visibleNodeIdsAt(viewer.id)
          const outputKey = segments[1]
          const loopSelfIndex =
            provider.kind === 'loop' &&
            provider.id === viewer.id &&
            (outputKey === 'index' || outputKey === 'iterations')
          const loopBlocked =
            provider.kind === 'loop' &&
            provider.id !== viewer.id &&
            !inLoopBody(viewer.id, provider.id)
          if (
            (provider.id === viewer.id && !loopSelfIndex) ||
            loopBlocked ||
            (!visible.has(provider.id) && !loopSelfIndex)
          ) {
            continue
          }
          if (loopSelfIndex) continue
          result.push({ viewer: viewer.id, provider: provider.id, pointer: field.pointer })
        }
      }
    }
    return result
  }

  return { visibleNodeIdsAt, listPathsAt, validateRefsAt, dataDependencyEdges }
}
