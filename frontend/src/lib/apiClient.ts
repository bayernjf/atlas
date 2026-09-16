/**
 * Graph DSL 后端 API 客户端（docs/12 §5；dev 经 Vite /api 代理到 8000）。
 */
import {
  clearSession,
  getToken,
  handleUnauthorized,
  saveSession,
  type LoginResponse,
  type Principal,
} from './auth'
import type { SerializedGraph } from './graphSerializer'
import type { JsonSchema } from './scope'

export type CompileResult = {
  id: string
  nodes: Array<{ id: string; type: string; name: string }>
  edges: Array<{ id: string; source: string; target: string }>
  entrypoints: string[]
  terminals: string[]
}

export type RunResult = {
  id: string
  status: string
  outputs: Record<string, unknown>
  trace: string[]
}

export type RunInputs = Record<string, string | number>

export type ApprovalRequest = {
  token: string
  summary: string
  approver: string
  timeoutSeconds: number
}

export type DebugAction = 'step' | 'continue' | 'stop'

export type DebugBreakpoint = {
  node_id: string
  expression?: string
}

export type DebugRequest = {
  breakpoints: DebugBreakpoint[]
}

export type PausedFrame = {
  type: 'paused'
  token: string
  node_id: string
  node_type: string
  reason: 'step' | 'breakpoint' | 'condition'
  globals: Record<string, unknown>
  outputs: Record<string, unknown>
}

export type StoppedFrame = {
  type: 'stopped'
  node_id: string
  reason: 'user_stop'
}

/** 调试运行被「停止」结束（stopped 帧）；无 result，属正常终止而非请求失败。 */
export class DebugRunStoppedError extends Error {
  nodeId: string
  constructor(nodeId: string) {
    super(`调试已停止：${nodeId}`)
    this.name = 'DebugRunStoppedError'
    this.nodeId = nodeId
  }
}

export type RunEvent =
  | { type: 'node_start'; node_id: string; node_type: string; approval?: ApprovalRequest }
  | { type: 'node_end'; node_id: string; node_type: string; output: unknown }
  | ({ type: 'run_end' } & Partial<RunResult>)
  | PausedFrame
  | StoppedFrame

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  headers.set('Content-Type', 'application/json')
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(path, { ...init, headers })
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    if (response.status === 401) {
      // 登录端点的 401 是「用户名或密码错误」，不触发会话失效跳转
      if (path !== '/api/auth/login') handleUnauthorized()
    }
    const detail = body?.detail
    throw new Error(
      Array.isArray(detail) ? detail.join('；') : detail || `请求失败：${response.status}`,
    )
  }
  return body as T
}

// --- 认证会话（04 §5.14） -------------------------------------------------

export async function login(username: string, password: string): Promise<LoginResponse> {
  const response = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(body?.detail || `登录失败：${response.status}`)
  }
  const session = body as LoginResponse
  saveSession(session.token, session.principal)
  return session
}

export async function me(): Promise<LoginResponse> {
  return request<LoginResponse>('/api/auth/me')
}

export async function logout(): Promise<void> {
  try {
    if (getToken()) await request('/api/auth/logout', { method: 'POST' })
  } finally {
    clearSession()
  }
}

export type { Principal }

export type SavedGraphSummary = {
  id: string
  node_count: number
  updated_at: string
}

export type AdapterToolInfo = {
  name: string
  description: string
  permission: string
  idempotent: boolean
  input_schema: JsonSchema
  output_schema: JsonSchema
}

export type AdapterInfo = {
  id: string
  type: string
  healthy: boolean
  tools: AdapterToolInfo[]
}

export async function listAdapters(): Promise<AdapterInfo[]> {
  return request<AdapterInfo[]>('/api/adapters')
}

export async function saveGraph(graph: SerializedGraph): Promise<{ id: string; version: number }> {
  return request('/api/graphs', { method: 'POST', body: JSON.stringify(graph) })
}

export async function listGraphs(): Promise<SavedGraphSummary[]> {
  const body = await request<{ items: SavedGraphSummary[] }>('/api/graphs')
  return body.items
}

export async function compileGraph(id: string): Promise<CompileResult> {
  return request(`/api/graphs/${id}/compile`, { method: 'POST' })
}

export async function runGraph(id: string, inputs?: RunInputs): Promise<RunResult> {
  return request(`/api/graphs/${id}/run`, { method: 'POST', body: JSON.stringify({ inputs }) })
}

export async function nlGenerate(prompt: string): Promise<{ graph: SerializedGraph }> {
  return request('/api/nl/generate', { method: 'POST', body: JSON.stringify({ prompt }) })
}

export type TemplateSummary = {
  id: string
  name: string
  description: string
  tags: string[]
  node_count: number
}

export type TemplateDetail = {
  id: string
  name: string
  description: string
  tags: string[]
  graph: SerializedGraph
}

export async function listTemplates(): Promise<TemplateSummary[]> {
  const body = await request<{ items: TemplateSummary[] }>('/api/templates')
  return body.items
}

export async function getTemplate(id: string): Promise<TemplateDetail> {
  return request(`/api/templates/${id}`)
}

export async function decideApproval(
  token: string,
  decision: 'approved' | 'rejected',
  comment = '',
): Promise<{ token: string; decision: string; resolvedBy: string }> {
  return request(`/api/approvals/${token}/decision`, {
    method: 'POST',
    body: JSON.stringify({ decision, comment }),
  })
}

export async function resumeDebug(
  token: string,
  action: DebugAction,
): Promise<{ token: string; action: DebugAction }> {
  return request(`/api/debug/${token}/resume`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
}

export type FeedbackType = 'bug' | 'suggestion'
export async function submitFeedback(input: {
  type: FeedbackType
  content: string
  contact?: string
}): Promise<{ id: string; created_at: string }> {
  return request('/api/feedback', { method: 'POST', body: JSON.stringify(input) })
}

export type RecordStep = {
  node_id: string
  node_type: string
  output: Record<string, unknown>
}

export type RecordingSummary = {
  id: string
  name: string
  node_count: number
  step_count: number
  status: string
  created_at: string
}

export type RecordingCase = {
  id: string
  name: string
  graph: SerializedGraph
  inputs: RunInputs | null
  steps: RecordStep[]
  status: string
  created_at: string
}

export type ReplayStepRow = {
  node_id: string
  match: boolean
  note: string
  diff_keys?: string[]
}

export type ReplayReport = {
  matches: boolean
  baseline_status: string
  replay_status: string
  steps: ReplayStepRow[]
}

export async function listRecordings(): Promise<RecordingSummary[]> {
  const body = await request<{ items: RecordingSummary[] }>('/api/recordings')
  return body.items
}

export async function saveRecording(input: {
  name: string
  graph_id: string
  inputs: RunInputs | null
  steps: RecordStep[]
  status: string
}): Promise<RecordingCase> {
  return request('/api/recordings', { method: 'POST', body: JSON.stringify(input) })
}

export async function deleteRecording(id: string): Promise<void> {
  await request(`/api/recordings/${id}`, { method: 'DELETE' })
}

export async function replayRecording(id: string): Promise<ReplayReport> {
  return request(`/api/recordings/${id}/replay`, { method: 'POST' })
}

/**
 * SSE 流式运行（08 §7.3 验收 5）：节点开始/结束事件实时回调，
 * 最终 result 事件以 RunResult 结束。
 *
 * 传 debug 时为调试运行（04 §5.12）：收到 paused 帧回调后由调用方经
 * resumeDebug 放行；action=stop 收尾为 stopped 帧并抛 DebugRunStoppedError。
 */
export async function streamRun(
  id: string,
  inputs: RunInputs | undefined,
  onEvent: (event: RunEvent) => void,
  debug?: DebugRequest,
): Promise<RunResult> {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`/api/graphs/${id}/run/stream`, {
    method: 'POST',
    headers,
    body: JSON.stringify(debug ? { inputs, debug } : { inputs }),
  })
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => null)
    if (response.status === 401) handleUnauthorized()
    throw new Error(body?.detail || `流式运行失败：${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let result: RunResult | null = null
  let stoppedNodeId: string | null = null

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() ?? ''
    for (const chunk of chunks) {
      const dataLine = chunk.split('\n').find((line) => line.startsWith('data: '))
      if (!dataLine) continue
      const payload = JSON.parse(dataLine.slice(6))
      if (payload.id && payload.outputs) {
        result = payload as RunResult
      } else if (payload.type === 'stopped') {
        stoppedNodeId = payload.node_id
        onEvent(payload as StoppedFrame)
      } else {
        onEvent(payload as RunEvent)
      }
    }
  }
  if (stoppedNodeId !== null) throw new DebugRunStoppedError(stoppedNodeId)
  if (!result) throw new Error('SSE 流缺少最终运行结果')
  return result
}

// --- 基础监控告警（04 §5.13） ---------------------------------------------

export type NodeResult = {
  node_id: string
  node_type: string
  status: 'success' | 'failed'
  error: string | null
}

export type RunRecord = {
  id: string
  graph_id: string
  mode: 'sync' | 'stream'
  status: 'completed' | 'error'
  started_at: string
  finished_at: string
  duration_ms: number
  nodes: NodeResult[]
  error: string | null
}

export type MetricsStats = {
  total: number
  healthy: number
  unhealthy: number
  success_rate: number | null
  p50: number | null
  p95: number | null
}

export type FailedNodeRow = {
  node_id: string
  node_type: string
  count: number
  last_error: string | null
  last_seen: string
}

export type MetricsSummary = MetricsStats & {
  per_graph: Array<{ graph_id: string } & MetricsStats>
  failed_nodes: FailedNodeRow[]
}

export type RuleId = 'run_error' | 'node_failed' | 'consecutive_failures' | 'failure_rate'

export type AlertStatus = 'open' | 'acknowledged' | 'resolved'

export type AlertItem = {
  id: string
  rule_id: RuleId
  graph_id: string
  severity: 'critical' | 'warning'
  message: string
  first_seen: string
  last_seen: string
  count: number
  status: AlertStatus
  last_run_id: string
}

export type RuleConfig = {
  run_error: { enabled: boolean }
  node_failed: { enabled: boolean }
  consecutive_failures: { enabled: boolean; threshold: number }
  failure_rate: { enabled: boolean; window: number; min_samples: number; rate: number }
}

export async function getMetrics(): Promise<MetricsSummary> {
  return request('/api/monitoring/metrics')
}

export async function getRuns(graphId?: string, limit = 50): Promise<RunRecord[]> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (graphId) params.set('graph_id', graphId)
  const body = await request<{ items: RunRecord[] }>(`/api/monitoring/runs?${params}`)
  return body.items
}

export async function getRules(): Promise<RuleConfig> {
  return request('/api/monitoring/rules')
}

export async function updateRules(rules: RuleConfig): Promise<RuleConfig> {
  return request('/api/monitoring/rules', { method: 'PUT', body: JSON.stringify(rules) })
}

export async function listAlerts(status?: AlertStatus): Promise<AlertItem[]> {
  const body = await request<{ items: AlertItem[] }>(
    `/api/alerts${status ? `?status=${status}` : ''}`,
  )
  return body.items
}

export async function acknowledgeAlert(id: string): Promise<AlertItem> {
  return request(`/api/alerts/${id}/acknowledge`, { method: 'POST' })
}

export async function resolveAlert(id: string): Promise<AlertItem> {
  return request(`/api/alerts/${id}/resolve`, { method: 'POST' })
}
