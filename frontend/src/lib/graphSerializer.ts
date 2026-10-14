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
  /** 打包 AL（docs/123 §2.1/§2.2）：图级全局异常降级与自愈（snake_case，后端 pydantic）。 */
  global_error_handler?: Record<string, unknown>
  self_healing?: Record<string, unknown>
  /**
   * 打包 BN（docs/130 §3.1）：图级自走循环配置。
   * **注意：字段名本身是 camelCase 的 `loop`，其子键也是 camelCase**（与打包 AL 的
   * snake_case 不同）——仍逐字段显式写出，理由同上（pydantic 静默丢未知键）。
   */
  loop?: Record<string, unknown>
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
  graphSettings?: {
    /** 打包 AL（docs/123 §2.1，P-1=C）：图级全局异常降级（camelCase 编辑器形态）。 */
    globalErrorHandler?: {
      onUncaughtError?: 'mark_only' | 'route_to_subgraph' | 'escalate_to_human'
      errorSubgraphId?: string
      escalation?: { channel?: string; recipients?: string[] }
    }
    /** docs/123 §2.2：selfHealing 自愈（camelCase 编辑器形态）。 */
    selfHealing?: {
      triggerOn?: string[]
      diagnoseSubgraphId?: string
      maxAttempts?: number
      continueWhen?: string
    }
    /** 打包 BN（docs/130 §3.1）：图级自主循环（camelCase 编辑器形态＝后端契约同名）。 */
    loop?: {
      enabled?: boolean
      maxIterations?: number
      maxWallClockSeconds?: number
      maxTokens?: number
      completeWhen?: string
      judgeMode?: 'rule' | 'llm'
      judgePrompt?: string
      carry?: Record<string, string>
      intervalSeconds?: number
    }
  },
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
  // 打包 AL：图级配置显式 snake_case 互转（camelCase→snake_case，pydantic 静默丢未知键坑）
  if (graphSettings?.globalErrorHandler) {
    const h = graphSettings.globalErrorHandler
    graph.global_error_handler = {
      on_uncaught_error: h.onUncaughtError ?? 'mark_only',
    }
    if (h.errorSubgraphId) graph.global_error_handler.error_subgraph_id = h.errorSubgraphId
    if (h.escalation) graph.global_error_handler.escalation = h.escalation
  }
  if (graphSettings?.selfHealing) {
    const s = graphSettings.selfHealing
    graph.self_healing = {}
    if (s.triggerOn) graph.self_healing.trigger_on = s.triggerOn
    if (s.diagnoseSubgraphId) graph.self_healing.diagnose_subgraph_id = s.diagnoseSubgraphId
    if (s.maxAttempts !== undefined) graph.self_healing.max_attempts = s.maxAttempts
    if (s.continueWhen !== undefined) graph.self_healing.continue_when = s.continueWhen
  }
  // 打包 BN：图级自主循环（`loop` 的键名本身即后端契约的 camelCase，仍逐字段显式写）
  if (graphSettings?.loop) {
    const l = graphSettings.loop
    graph.loop = {}
    if (l.enabled !== undefined) graph.loop.enabled = l.enabled
    if (l.maxIterations !== undefined) graph.loop.maxIterations = l.maxIterations
    if (l.maxWallClockSeconds !== undefined) graph.loop.maxWallClockSeconds = l.maxWallClockSeconds
    if (l.maxTokens !== undefined) graph.loop.maxTokens = l.maxTokens
    if (l.completeWhen !== undefined) graph.loop.completeWhen = l.completeWhen
    if (l.judgeMode !== undefined) graph.loop.judgeMode = l.judgeMode
    if (l.judgePrompt !== undefined) graph.loop.judgePrompt = l.judgePrompt
    if (l.carry !== undefined) graph.loop.carry = l.carry
    if (l.intervalSeconds !== undefined) graph.loop.intervalSeconds = l.intervalSeconds
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
  /** 打包 AL：图级 globalErrorHandler/selfHealing（snake_case→camelCase 编辑器形态）。 */
  graphSettings?: {
    globalErrorHandler?: {
      onUncaughtError?: 'mark_only' | 'route_to_subgraph' | 'escalate_to_human'
      errorSubgraphId?: string
      escalation?: { channel?: string; recipients?: string[] }
    }
    selfHealing?: {
      triggerOn?: string[]
      diagnoseSubgraphId?: string
      maxAttempts?: number
      continueWhen?: string
    }
    /** 打包 BN（docs/130 §3.1）：图级自主循环（键名即后端契约 camelCase）。 */
    loop?: {
      enabled?: boolean
      maxIterations?: number
      maxWallClockSeconds?: number
      maxTokens?: number
      completeWhen?: string
      judgeMode?: 'rule' | 'llm'
      judgePrompt?: string
      carry?: Record<string, string>
      intervalSeconds?: number
    }
  }
} {
  // 模板 instantiate/NL 草稿等来源的图可能缺 position（GraphDSL 默认值不落库）。
  // 兜底按序生成瀑布位置，避免画布节点 position=undefined 导致序列化崩溃（ZX 冒烟暴露）。
  const DEFAULT_NODE_GAP = 80
  const gh = graph.global_error_handler
  const sh = graph.self_healing
  const graphSettings: NonNullable<
    ReturnType<typeof deserializeGraph>
  >['graphSettings'] = {}
  if (gh && typeof gh === 'object') {
    const handler: NonNullable<typeof graphSettings.globalErrorHandler> = {}
    const anyGh = gh as Record<string, unknown>
    if (typeof anyGh.on_uncaught_error === 'string') {
      handler.onUncaughtError = anyGh.on_uncaught_error as
        | 'mark_only'
        | 'route_to_subgraph'
        | 'escalate_to_human'
    }
    if (typeof anyGh.error_subgraph_id === 'string') handler.errorSubgraphId = anyGh.error_subgraph_id
    if (anyGh.escalation && typeof anyGh.escalation === 'object') {
      const esc = anyGh.escalation as Record<string, unknown>
      handler.escalation = {}
      if (typeof esc.channel === 'string') handler.escalation.channel = esc.channel
      if (Array.isArray(esc.recipients)) handler.escalation.recipients = esc.recipients as string[]
    }
    graphSettings.globalErrorHandler = handler
  }
  if (sh && typeof sh === 'object') {
    const healing: NonNullable<typeof graphSettings.selfHealing> = {}
    const anySh = sh as Record<string, unknown>
    if (Array.isArray(anySh.trigger_on)) healing.triggerOn = anySh.trigger_on as string[]
    if (typeof anySh.diagnose_subgraph_id === 'string') healing.diagnoseSubgraphId = anySh.diagnose_subgraph_id
    if (typeof anySh.max_attempts === 'number') healing.maxAttempts = anySh.max_attempts
    if (typeof anySh.continue_when === 'string') healing.continueWhen = anySh.continue_when
    graphSettings.selfHealing = healing
  }
  // 打包 BN：图级自主循环（键名与后端契约同名，逐字段校验后读回）
  const lp = graph.loop
  if (lp && typeof lp === 'object') {
    const loop: NonNullable<typeof graphSettings.loop> = {}
    const anyLp = lp as Record<string, unknown>
    if (typeof anyLp.enabled === 'boolean') loop.enabled = anyLp.enabled
    if (typeof anyLp.maxIterations === 'number') loop.maxIterations = anyLp.maxIterations
    if (typeof anyLp.maxWallClockSeconds === 'number') loop.maxWallClockSeconds = anyLp.maxWallClockSeconds
    if (typeof anyLp.maxTokens === 'number') loop.maxTokens = anyLp.maxTokens
    if (typeof anyLp.completeWhen === 'string') loop.completeWhen = anyLp.completeWhen
    if (anyLp.judgeMode === 'rule' || anyLp.judgeMode === 'llm') loop.judgeMode = anyLp.judgeMode
    if (typeof anyLp.judgePrompt === 'string') loop.judgePrompt = anyLp.judgePrompt
    if (anyLp.carry && typeof anyLp.carry === 'object' && !Array.isArray(anyLp.carry)) {
      const carry: Record<string, string> = {}
      for (const [key, value] of Object.entries(anyLp.carry as Record<string, unknown>)) {
        if (typeof value === 'string') carry[key] = value
      }
      loop.carry = carry
    }
    if (typeof anyLp.intervalSeconds === 'number') loop.intervalSeconds = anyLp.intervalSeconds
    graphSettings.loop = loop
  }
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
    graphSettings,
  }
}
