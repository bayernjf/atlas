/**
 * 画布 → Graph 定义 JSON 序列化（node_schema 形状，03 契约索引）。
 * W5-W6 只做编辑器侧序列化，W7-W8 的 DSL→LangGraph 编译以此为输入。
 */

import type { Edge } from '@xyflow/react'
import type { EditorNodeData } from './nodeCatalog'
import type { GraphVariable } from './variables'

export type EditorNode = {
  id: string
  position: { x: number; y: number }
  data: EditorNodeData
}

/** docs/60 §5.1：随图持久化的断点（形状与后端 _validate_debug 入参一一对齐）。 */
export type PersistedBreakpoint = {
  nodeId: string
  expression?: string | null
  hitCount?: number | null
  logMessage?: string | null
  onException?: boolean
}

type BreakpointLike = {
  expression?: string
  hitCount?: number
  logMessage?: string
  onException?: boolean
}

export type SerializedGraph = {
  version: number
  variables: GraphVariable[]
  nodes: Array<{
    id: string
    type: EditorNodeData['kind']
    name: string
    description: string
    position: { x: number; y: number }
    config: EditorNodeData['config']
    retry: SerializedRetry
  }>
  edges: Array<{ id: string; source: string; target: string }>
  /** docs/60 §5.1：可选顶层；仅当存在至少一个启用断点时输出。 */
  debugSettings?: { breakpoints: PersistedBreakpoint[] }
}

/**
 * 打包 AJ（docs/121）：编辑器 retry 用 camelCase（maxRetries/onError/errorTarget），
 * 后端 RetryConfig 是 snake_case（max_retries/on_error/error_target）——pydantic 对未知键
 * 静默丢弃，序列化/反序列化必须显式互转，否则面板配置永远到不了运行期。
 */
type SerializedRetry = {
  max_retries: number
  backoff: string
  timeout: number
  on_error: EditorNodeData['retry']['onError']
  error_target?: string
}

function serializeRetry(retry: EditorNodeData['retry']): SerializedRetry {
  const out: SerializedRetry = {
    max_retries: retry.maxRetries,
    backoff: retry.backoff,
    timeout: retry.timeout,
    on_error: retry.onError,
  }
  if (retry.onError === 'jump_to' && retry.errorTarget) {
    out.error_target = retry.errorTarget
  }
  return out
}

function deserializeRetry(raw: unknown): EditorNodeData['retry'] {
  const record = (raw ?? {}) as Record<string, unknown>
  return {
    maxRetries: typeof record.max_retries === 'number' ? record.max_retries : 0,
    backoff: typeof record.backoff === 'string' ? record.backoff : '1s',
    timeout: typeof record.timeout === 'number' ? record.timeout : 30,
    onError:
      record.on_error === 'continue' || record.on_error === 'jump_to'
        ? record.on_error
        : 'stop',
    errorTarget: typeof record.error_target === 'string' ? record.error_target : undefined,
  }
}

/** 裁剪 undefined/默认值；空断点（普通行断点）输出为 { nodeId }。 */
function toPersistedBreakpoint(nodeId: string, breakpoint: BreakpointLike): PersistedBreakpoint {
  const persisted: PersistedBreakpoint = { nodeId }
  const expression = breakpoint.expression?.trim()
  if (expression) persisted.expression = expression
  if (
    typeof breakpoint.hitCount === 'number' &&
    Number.isInteger(breakpoint.hitCount) &&
    breakpoint.hitCount >= 1
  ) {
    persisted.hitCount = breakpoint.hitCount
  }
  const logMessage = breakpoint.logMessage?.trim()
  if (logMessage) persisted.logMessage = logMessage
  if (breakpoint.onException === true) persisted.onException = true
  return persisted
}

export function serializeGraph(
  nodes: EditorNode[],
  edges: Edge[],
  variables: GraphVariable[],
  breakpoints?: Record<string, BreakpointLike>,
): SerializedGraph {
  const graph: SerializedGraph = {
    version: 1,
    variables,
    nodes: nodes.map((node) => ({
      id: node.id,
      type: node.data.kind,
      name: node.data.label,
      description: node.data.description ?? '',
      position: { x: Math.round(node.position.x), y: Math.round(node.position.y) },
      config: node.data.config,
      retry: serializeRetry(node.data.retry),
    })),
    edges: edges.map((edge) => ({ id: edge.id, source: edge.source, target: edge.target })),
  }
  const persisted = Object.entries(breakpoints ?? {}).map(([nodeId, breakpoint]) =>
    toPersistedBreakpoint(nodeId, breakpoint),
  )
  // 仅当存在至少一个启用断点时输出 debugSettings（旧形状零回归）
  if (persisted.length > 0) {
    graph.debugSettings = { breakpoints: persisted }
  }
  return graph
}

/**
 * 后端 Graph JSON（NL 草稿/已保存图）→ 编辑器元素。
 * 草稿节点统一补 idle 状态，缺省的 retry 由序列化侧保证。
 */
export function deserializeGraph(graph: SerializedGraph): {
  nodes: EditorNode[]
  edges: Edge[]
  variables: GraphVariable[]
  debugSettings?: { breakpoints: PersistedBreakpoint[] }
} {
  // 模板 instantiate/NL 草稿等来源的图可能缺 position（GraphDSL 默认值不落库）。
  // 兜底按序生成瀑布位置，避免画布节点 position=undefined 导致序列化崩溃（ZX 冒烟暴露）。
  const DEFAULT_NODE_GAP = 80
  return {
    nodes: graph.nodes.map((node, index) => ({
      id: node.id,
      position: node.position ?? { x: 40, y: 40 + index * DEFAULT_NODE_GAP },
      data: {
        label: node.name,
        kind: node.type,
        status: 'idle' as const,
        description: node.description ?? '',
        config: node.config,
        retry: deserializeRetry(node.retry),
      },
    })),
    edges: graph.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
    })),
    variables: graph.variables,
    debugSettings: graph.debugSettings,
  }
}
