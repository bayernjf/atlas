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
  /** M8：命中内置交互卡片时携带卡片 id；缺省走 summary 旧路径。 */
  cardTemplateId?: string
}

export type DebugAction = 'step' | 'continue' | 'stop'

export type DebugBreakpoint = {
  node_id: string
  expression?: string
  /** B 包（docs/27 §4.2）：每 N 次命中暂停一次（正整数）；缺省每次命中暂停。 */
  hitCount?: number
  /** B 包：非空即日志断点（logpoint），命中只发 debug_log 不暂停。 */
  logMessage?: string
  /** docs/28 §3.2：异常断点——节点逻辑抛异常时先暂停，resume 后原样重抛。 */
  onException?: boolean
}

export type DebugRequest = {
  breakpoints: DebugBreakpoint[]
}

/** docs/28 §3.1：单个 global 顶层键在相邻暂停间的变化（新增 old=null）。 */
export type VariableChangeEntry = {
  key: string
  old: unknown
  new: unknown
}

/** docs/28 §3.1：一次暂停对应的变量变化历史条目。 */
export type VariableHistoryItem = {
  seq: number
  node_id: string
  reason: string
  since_nodes: string[]
  changes: VariableChangeEntry[]
}

/** docs/28 §3.2：异常断点暂停时携带的异常类型与消息。 */
export type PausedError = { type: string; message: string }

export type PausedFrame = {
  type: 'paused'
  token: string
  node_id: string
  node_type: string
  reason: 'step' | 'breakpoint' | 'condition' | 'exception'
  globals: Record<string, unknown>
  outputs: Record<string, unknown>
  /** docs/28 §3.1：截至本次暂停的变量变化历史（易失、随调试会话）。 */
  history?: VariableHistoryItem[]
  /** docs/28 §3.2：仅 reason=exception 时存在。 */
  error?: PausedError
  /** docs/28 §3.3：子图内部暂停时父图 subgraph 节点 id 路径；顶层节点无此键。 */
  subgraphPath?: string[]
}

export type StoppedFrame = {
  type: 'stopped'
  node_id: string
  reason: 'user_stop'
}

/** B 包（docs/27 §4.2）：日志断点命中帧，不暂停运行。 */
export type DebugLogFrame = {
  type: 'debug_log'
  node_id: string
  hits: number
  message: string
  subgraphPath?: string[]
}

/** B 包（docs/27 §4.1）：普通（非调试）运行被协作式急停的终帧。 */
export type CancelledFrame = {
  type: 'cancelled'
  node_id: string
  reason: 'user_cancel'
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

/** 普通运行被协作式急停结束（cancelled 帧）；用户主动，非请求失败。 */
export class RunCancelledError extends Error {
  nodeId: string
  constructor(nodeId: string) {
    super(`运行已取消：${nodeId}`)
    this.name = 'RunCancelledError'
    this.nodeId = nodeId
  }
}

export type RunEvent =
  | {
      type: 'node_start'
      node_id: string
      node_type: string
      approval?: ApprovalRequest
      /** A 包（docs/27 §3.1）：子图内部节点携带每层父图 subgraph 节点 id 路径；顶层节点缺省。 */
      subgraphPath?: string[]
    }
  | {
      type: 'node_end'
      node_id: string
      node_type: string
      output: unknown
      /** A 包（docs/27 §3.1）：子图内部节点携带每层父图 subgraph 节点 id 路径；顶层节点缺省。 */
      subgraphPath?: string[]
    }
  | {
      /** docs/28 §4.1 ⑧：工具适配器调用埋点（监控采集用，编辑器无分支即忽略）。 */
      type: 'tool_metric'
      node_id: string
      tool: string
      duration_ms: number
      action_status: 'SUCCESS' | 'FAILED' | 'SIMULATED'
      error_code: string | null
      subgraphPath?: string[]
    }
  | ({ type: 'run_end' } & Partial<RunResult>)
  | PausedFrame
  | StoppedFrame
  | DebugLogFrame
  | CancelledFrame

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
    const fallback = `请求失败：${response.status}`
    throw new Error(
      Array.isArray(detail)
        ? detail.join('；')
        : typeof detail === 'object'
          ? (detail?.message ?? fallback)
          : detail || fallback,
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

// --- 用户管理与改密（docs/31 §3，步骤 7） -----------------------------------

export type UserRole = 'viewer' | 'operator' | 'admin'

export type UserAccountView = {
  username: string
  displayName: string
  role: UserRole
  status: 'active' | 'disabled'
  createdAt: string
  updatedAt: string
}

export type CreateUserInput = {
  username: string
  password: string
  displayName: string
  role: UserRole
}

export type UpdateUserPatch = {
  displayName?: string
  role?: UserRole
  status?: 'active' | 'disabled'
}

export async function changePassword(
  oldPassword: string,
  newPassword: string,
): Promise<{ changed: boolean }> {
  return request('/api/auth/change-password', {
    method: 'POST',
    body: JSON.stringify({ oldPassword: oldPassword, newPassword: newPassword }),
  })
}

export async function listUsers(): Promise<UserAccountView[]> {
  return request<UserAccountView[]>('/api/users')
}

export async function createUser(input: CreateUserInput): Promise<UserAccountView> {
  return request('/api/users', { method: 'POST', body: JSON.stringify(input) })
}

export async function updateUser(
  username: string,
  patch: UpdateUserPatch,
): Promise<UserAccountView> {
  return request(`/api/users/${encodeURIComponent(username)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

export async function resetUserPassword(
  username: string,
  newPassword: string,
): Promise<{ reset: boolean }> {
  return request(`/api/users/${encodeURIComponent(username)}/reset-password`, {
    method: 'POST',
    body: JSON.stringify({ newPassword: newPassword }),
  })
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

/** 覆盖已存图的 latest 草稿（M9：同一 graph 迭代多版本，不新建 id；已发布版本不可变） */
export async function saveGraphDraft(
  graphId: string,
  graph: SerializedGraph,
): Promise<{ id: string; version: number }> {
  return request(`/api/graphs/${graphId}`, { method: 'PUT', body: JSON.stringify(graph) })
}

export async function compileGraph(id: string): Promise<CompileResult> {
  return request(`/api/graphs/${id}/compile`, { method: 'POST' })
}

export type TriggerEventPayload = {
  channel?: 'api' | 'webhook' | 'im' | 'embed'
  payload?: Record<string, unknown>
}

export type RunOptions = {
  /** 手动钉住的发布版本（草稿运行不传）；与 event 互斥 */
  releaseVersion?: number
  /** 入站事件（经 Router 三段分桶解析版本）；与 releaseVersion 互斥、不支持 debug */
  event?: TriggerEventPayload
}

function runBody(inputs: RunInputs | undefined, debug?: DebugRequest, opts?: RunOptions) {
  const body: Record<string, unknown> = { inputs }
  if (debug) body.debug = debug
  if (opts?.releaseVersion !== undefined) body.releaseVersion = opts.releaseVersion
  if (opts?.event) body.event = opts.event
  return JSON.stringify(body)
}

export async function runGraph(
  id: string,
  inputs?: RunInputs,
  opts?: RunOptions,
): Promise<RunResult> {
  return request(`/api/graphs/${id}/run`, { method: 'POST', body: runBody(inputs, undefined, opts) })
}

export async function nlGenerate(prompt: string): Promise<{ graph: SerializedGraph; paramWarnings?: string[] }> {
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

// --- 邮件深链审批（docs/36 §3；无登录，签名 token 即凭证） -------------------

export type EmailApprovalView = {
  status: 'pending' | 'resolved'
  summary: string
  nodeId: string
  graphId: string
  approver: string
  timeoutSeconds: number
  createdAt: number
  remainingSeconds: number
  decision?: string
  resolvedBy?: string | null
  card?: EmailCardView
}

export type EmailDecisionPayload = {
  token: string
  decision?: 'approved' | 'rejected'
  comment?: string
  actionId?: string
  form?: Record<string, string>
}

async function publicPost<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const json = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(json?.detail || `请求失败：${response.status}`)
  }
  return json as T
}

export async function getEmailApprovalView(token: string): Promise<EmailApprovalView> {
  const query = new URLSearchParams({ token })
  // 公开端点：不带 Bearer，失败不触发登录跳转
  const response = await fetch(`/api/approvals/email-view?${query.toString()}`)
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(body?.detail || `请求失败：${response.status}`)
  }
  return body as EmailApprovalView
}

export function decideByEmail(
  payload: EmailDecisionPayload,
): Promise<{ token: string; decision: string; resolvedBy: string; actionId?: string }> {
  return publicPost('/api/approvals/email-decision', payload)
}

export type QueueApprovalItem = {
  token: string
  node_id: string
  graph_id: string
  summary: string
  approver: string
  timeoutSeconds: number
  createdAt: number
  cardTemplateId?: string
}

export async function listApprovalsQueue(): Promise<QueueApprovalItem[]> {
  const body = await request<{ items: QueueApprovalItem[] }>('/api/approvals')
  return body.items
}

export type DecidedApprovalItem = {
  token: string
  node_id: string
  graph_id: string
  summary: string
  approver: string
  decision: 'approved' | 'rejected'
  resolvedBy: 'human' | 'email-link' | 'timeout' | 'input'
  comment: string
  createdAt: number
  cardTemplateId?: string
}

export async function listDecidedApprovals(
  limit = 50,
): Promise<{ items: DecidedApprovalItem[]; limit: number }> {
  return request(`/api/approvals/decided?limit=${limit}`)
}

export type AuditEventItem = {
  id: string
  tenantId: string
  actor: string
  action: string
  statusCode: number
  path: string
  ip: string
  at: string
}

export async function listAuditEvents(
  limit: number,
  action?: string,
): Promise<{ items: AuditEventItem[]; limit: number }> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (action) params.set('action', action)
  return request(`/api/audit/events?${params.toString()}`)
}

export async function exportAuditJsonl(action?: string): Promise<Blob> {
  const params = new URLSearchParams({ format: 'jsonl' })
  if (action) params.set('action', action)
  const headers = new Headers()
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`/api/audit/export?${params.toString()}`, { headers })
  if (!response.ok) throw new Error(`导出失败：${response.status}`)
  return response.blob()
}

// --- M8 交互卡片（04 §5.6 追加段 / 12 §3.11） ------------------------------

/** 内置卡片目录项（GET /api/cards，只读代码常量）。 */
export type CardSummary = {
  id: string
  name: string
  channels: Array<'web' | 'im' | 'email'>
  sections: Array<Record<string, unknown>>
  actions: Array<{
    id: string
    label: string
    style?: 'primary' | 'danger' | 'default'
    output: Record<string, unknown>
  }>
  fallback?: Record<string, unknown> | null
}

export type CardFieldRow = { label: string; value: unknown }
export type CardFormSpec = {
  type: 'textarea' | 'input'
  name: string
  label: string | null
  required: boolean
  default: string
}
export type CardActionView = {
  id: string
  label: string
  style?: 'primary' | 'danger' | 'default'
}
export type WebCardView = {
  channel: 'web'
  cardId: string
  name: string
  fields: CardFieldRow[]
  form: CardFormSpec[]
  actions: CardActionView[]
  token: string
  approver: string
  timeoutSeconds: number | null
}
export type ImCardView = {
  channel: 'im'
  name: string
  text: string
  buttons: Array<{ id: string; label: string; url: string }>
  detailUrl: string
}
export type EmailCardView = {
  channel: 'email'
  subject: string
  html: string
  links: Array<{ id: string; label: string; url: string }>
}
export type RenderedCard = WebCardView | ImCardView | EmailCardView

export async function listCards(): Promise<CardSummary[]> {
  const body = await request<{ items: CardSummary[] }>('/api/cards')
  return body.items
}

export async function getApprovalCard(
  token: string,
  channel: 'web' | 'im' | 'email' = 'web',
): Promise<RenderedCard> {
  const query = new URLSearchParams({ channel })
  return request(`/api/approvals/${encodeURIComponent(token)}/card?${query.toString()}`)
}

/** 卡片动作提交：服务端按 action.output 映射 decision/comment（map_action_output 唯一权威）。 */
export async function decideCardAction(
  token: string,
  actionId: string,
  form?: Record<string, string>,
): Promise<{ token: string; decision: string; resolvedBy: string; actionId?: string }> {
  return request(`/api/approvals/${token}/decision`, {
    method: 'POST',
    body: JSON.stringify({ actionId, form }),
  })
}

export async function resumeDebug(
  token: string,
  action: DebugAction,
  globals?: Record<string, unknown>,
): Promise<{ token: string; action: DebugAction }> {
  // B 包（docs/27 §4.3）：step/continue 可带 globals 顶层键浅合并覆盖；stop 忽略。
  const body = globals !== undefined ? JSON.stringify({ action, globals }) : JSON.stringify({ action })
  return request(`/api/debug/${token}/resume`, {
    method: 'POST',
    body,
  })
}

/**
 * B 包（docs/27 §4.1）协作式急停：取本图最新在途 run（running；wait 阻塞为 suspended，
 * broker 句柄仍在亦可取消），置取消事件，下一节点边界生效。无在途 run 返回 null。
 * 不在 wait/approval/tool 阻塞中点强杀。
 */
export async function cancelActiveRun(
  graphId: string,
): Promise<{ runId: string } | null> {
  const runs = await request<{
    items: Array<{ runId: string; graphId: string; status: string }>
  }>('/api/runs?limit=50')
  const target = runs.items.find(
    (r) => r.graphId === graphId && (r.status === 'running' || r.status === 'suspended'),
  )
  if (!target) return null
  await request<{ run_id: string; cancelled: boolean }>(
    `/api/runs/${target.runId}/cancel`,
    { method: 'POST' },
  )
  return { runId: target.runId }
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
  /** 所属图 id（M9 纯超集；旧用例为空串） */
  graph_id: string
  graph: SerializedGraph
  inputs: RunInputs | null
  steps: RecordStep[]
  status: string
  created_at: string
  /** 录制时钟锚点（C 包；回放冻结到该时刻） */
  recorded_at?: string | null
  /** 录制时递归冻结的 subgraph 引用快照（key＝引用原文含 @N） */
  subgraphs?: Record<string, SerializedGraph>
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
  /** 时钟锚点缺失/不可解析时的提示（C 包，可选） */
  clock_note?: string
  /** docs/28 §2.2：本次被桩替代的工具节点 id（未启用 mock 为 []） */
  mocked_tools?: string[]
}

/** docs/28 §2.2/§2.3：单用例回放可选请求体 */
export type ReplayRequestOptions = {
  mock_tools?: boolean
  /** 顶层键浅合并进用例 inputs（一次性，不落库） */
  inputs_override?: RunInputs
}

/** docs/28 §2.3：用例元信息编辑（仅 name/inputs 可改） */
export type RecordingUpdatePatch = {
  name?: string
  inputs?: RunInputs
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

export async function replayRecording(
  id: string,
  body?: ReplayRequestOptions,
): Promise<ReplayReport> {
  return request(`/api/recordings/${id}/replay`, {
    method: 'POST',
    body: body ? JSON.stringify(body) : undefined,
  })
}

/** 单用例完整详情（编辑预填 inputs；列表投影 RecordingSummary 不含 inputs） */
export async function getRecording(id: string): Promise<RecordingCase> {
  return request(`/api/recordings/${id}`)
}

/** docs/28 §2.3：编辑用例 name/inputs（仅这两项可改；不存在后端 404） */
export async function updateRecording(
  id: string,
  patch: RecordingUpdatePatch,
): Promise<RecordingCase> {
  return request(`/api/recordings/${id}`, { method: 'PUT', body: JSON.stringify(patch) })
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
  opts?: RunOptions,
): Promise<RunResult> {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`/api/graphs/${id}/run/stream`, {
    method: 'POST',
    headers,
    body: runBody(inputs, debug, opts),
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
  let cancelledNodeId: string | null = null

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
      } else if (payload.type === 'cancelled') {
        cancelledNodeId = payload.node_id
        onEvent(payload as CancelledFrame)
      } else {
        onEvent(payload as RunEvent)
      }
    }
  }
  if (stoppedNodeId !== null) throw new DebugRunStoppedError(stoppedNodeId)
  if (cancelledNodeId !== null) throw new RunCancelledError(cancelledNodeId)
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

export type BusinessOutcome = {
  auto_refunded: boolean
  manual_escalated: boolean
  refunded_amount: number | null
  expected_amount: number | null
  amount_diff: boolean
}

export type BusinessRateRows = {
  graph_id: string
  resolved_version?: number | null
  samples: number
  auto_refund_rate: number | null
  manual_escalation_rate: number | null
  refund_amount_diff_rate: number | null
}

export type BusinessMetricsSummary = {
  auto_refund_rate: number | null
  manual_escalation_rate: number | null
  refund_amount_diff_rate: number | null
  per_graph: BusinessRateRows[]
  per_version: BusinessRateRows[]
}

/** docs/28 §4.1 ⑧ 单次工具适配器调用埋点。 */
export type ToolCallMetric = {
  node_id: string
  tool: string
  duration_ms: number
  action_status: 'SUCCESS' | 'FAILED' | 'SIMULATED'
  error_code: string | null
}

/** docs/28 §4.1 ⑧ 按工具聚合的调用指标行（SIMULATED 不纳延迟分位，样本 0 为 null）。 */
export type ToolMetricsRow = {
  tool: string
  calls: number
  failed: number
  simulated: number
  error_codes: Record<string, number>
  p50: number | null
  p95: number | null
}

export type RunRecord = {
  id: string
  graph_id: string
  mode: 'sync' | 'stream'
  status: 'completed' | 'error' | 'cancelled'
  started_at: string
  finished_at: string
  duration_ms: number
  nodes: NodeResult[]
  error: string | null
  resolved_version?: number | null
  business?: BusinessOutcome | null
  tool_calls?: ToolCallMetric[]
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
  business: BusinessMetricsSummary
  /** docs/28 §4.1 ⑧：适配器调用聚合（旧后端/空数据为 []）。 */
  tools?: ToolMetricsRow[]
}

export type RuleId =
  | 'run_error'
  | 'node_failed'
  | 'consecutive_failures'
  | 'failure_rate'
  | 'rollout_gate'

export type AlertStatus = 'open' | 'acknowledged' | 'resolved'

export type AlertItem = {
  /** 内置四条/rollout_gate 为字面量，自定义规则为 custom:{cid}，故放宽为 string。 */
  rule_id: string
  id: string
  graph_id: string
  severity: 'critical' | 'warning'
  message: string
  first_seen: string
  last_seen: string
  count: number
  status: AlertStatus
  last_run_id: string
  action?: RolloutAlertAction | null
  /** docs/28 §4.2 ⑨：自定义规则名（内置规则缺省；PG 档 v1 不持久化，可能为空）。 */
  rule_name?: string | null
  /** docs/33 §5.2：惰性升级时间（进程内，可能为空）。 */
  escalated_at?: string | null
  /** docs/33 §5.3：新建时值班人（进程内，可能为空）。 */
  assignee?: string | null
}

/** docs/28 §4.2 ⑨ 自定义告警规则（表达式复用安全条件引擎，禁 eval）。 */
export type CustomRuleConfig = {
  cid: string
  name: string
  enabled: boolean
  expression: string
  severity: 'critical' | 'warning'
}

export type RuleConfig = {
  run_error: { enabled: boolean }
  node_failed: { enabled: boolean }
  consecutive_failures: { enabled: boolean; threshold: number }
  failure_rate: { enabled: boolean; window: number; min_samples: number; rate: number }
  /** 纯超集：旧后端/旧配置缺省为空数组，不报错。 */
  custom?: CustomRuleConfig[]
  /** docs/33 §5.2：warning 未确认 N 分钟升 critical；null/缺省关闭。 */
  escalation_ack_minutes?: number | null
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

// docs/33 §4：Trace span 树（递归 children）；列表 RunRecord 不含 spans，trace 端点懒加载。
export type TraceSpanNode = {
  traceId: string
  spanId: string
  name: string
  kind: string
  startedAt: string
  durationMs: number
  status: string
  parentSpanId?: string | null
  graphVersion?: number | null
  internal?: boolean
  attrs?: Record<string, unknown>
  children?: TraceSpanNode[]
}

export type RunTrace = {
  id: string
  trace_id: string
  spans: TraceSpanNode | null // 历史/debug/回放记录为 null
}

export async function getRunTrace(runId: string): Promise<RunTrace> {
  return request(`/api/monitoring/runs/${encodeURIComponent(runId)}/trace`)
}

export async function getRules(): Promise<RuleConfig> {
  return request('/api/monitoring/rules')
}

export async function updateRules(rules: RuleConfig): Promise<RuleConfig> {
  return request('/api/monitoring/rules', { method: 'PUT', body: JSON.stringify(rules) })
}

export type AlertChannelKind = 'dingtalk' | 'wecom' | 'feishu' | 'webhook' | 'email'
export type AlertMinSeverity = 'critical' | 'warning'

export type AlertChannelConfig = {
  enabled: boolean
  channel: AlertChannelKind
  to: string
  secret: string
  minSeverity: AlertMinSeverity
  updatedAt: string
  lastDelivery: AlertChannelDelivery | null
}

export type AlertChannelDelivery = {
  lastNotifiedAt: string
  errorCode: string | null
  errorMessage: string | null
}

export async function getAlertChannel(): Promise<AlertChannelConfig> {
  return request('/api/monitoring/alert-channel')
}

export async function updateAlertChannel(
  config: Omit<AlertChannelConfig, 'updatedAt' | 'lastDelivery'>,
): Promise<AlertChannelConfig> {
  return request('/api/monitoring/alert-channel', {
    method: 'PUT',
    body: JSON.stringify(config),
  })
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

// docs/33 §5：静默 / 值班（进程内 v1，重启清空）
export type Silence = {
  id: string
  rule_id: string | null
  graph_id: string | null
  reason: string
  created_by: string
  created_at: string
  expires_at: string
  suppressed_count: number
  active?: boolean
}

export type OnCallSchedule = {
  members: string[]
  index: number
  current: string | null
  updated_at: string | null
  updated_by: string | null
}

export async function createSilence(body: {
  rule_id?: string | null
  graph_id?: string | null
  duration_minutes: number
  reason: string
}): Promise<Silence> {
  return request('/api/monitoring/silences', { method: 'POST', body: JSON.stringify(body) })
}

export async function listSilences(active?: boolean): Promise<Silence[]> {
  const query = active === undefined ? '' : `?active=${active ? 'true' : 'false'}`
  const body = await request<{ items: Silence[] }>(`/api/monitoring/silences${query}`)
  return body.items
}

export async function deleteSilence(id: string): Promise<void> {
  await request(`/api/monitoring/silences/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export async function getOnCall(): Promise<OnCallSchedule> {
  return request('/api/monitoring/on-call')
}

export async function updateOnCall(members: string[]): Promise<OnCallSchedule> {
  return request('/api/monitoring/on-call', { method: 'PUT', body: JSON.stringify({ members }) })
}

export async function rotateOnCall(): Promise<OnCallSchedule> {
  return request('/api/monitoring/on-call/rotate', { method: 'POST' })
}

// --- M9 版本发布 / 发布门禁 / 灰度发布（03 release_gate/rollout_config，04 §5.11/§5.16） ---

export type GateCaseRow = {
  case_id: string
  name: string
  matches: boolean
  replay_status: string
  note: string
}

export type GateReport = {
  /** D26 报告 v1：沉淀后的报告 id（rr-N），纯超集；旧消费方可忽略 */
  id?: string
  graph_id: string
  target: string
  total: number
  passed: number
  failed: number
  skipped: boolean
  blocked: boolean
  cases: GateCaseRow[]
}

/** D26 报告 v1：批量回放沉淀报告摘要（列表项，不含 cases；03 release_report） */
export type ReleaseReportSummary = {
  id: string
  graph_id: string
  target: string
  trigger: 'manual' | 'publish-gate'
  total: number
  passed: number
  failed: number
  skipped: boolean
  blocked: boolean
  /** passed/total；total=0（skipped 未覆盖）为 null */
  pass_rate: number | null
  created_at: string
}

/** D26 报告 v1：报告详情（含逐例 ✓/✗/note） */
export type ReleaseReport = ReleaseReportSummary & { cases: GateCaseRow[] }

export type InternalRule = { to: 'internal'; tenants: string[] }
export type BucketRule = {
  to: 'lowValueBucket'
  field?: string
  op?: '<='
  value: number
  percent?: number
}
export type CanaryRule = { to: 'canary'; percent: number }
export type FullRule = { to: 'full' }
export type RolloutRule = InternalRule | BucketRule | CanaryRule | FullRule

export type GateMetricId =
  | 'run_error_rate'
  | 'manual_escalation_rate'
  | 'refund_amount_diff_rate'

export type GateMetric = {
  id: GateMetricId
  threshold: number
  compareWith?: number | null
  minSamples?: number | null
}

export type GateConfig = {
  observeMinutes: number
  autoRollback: boolean
  minSamples: number
  metrics: GateMetric[]
}

export type RolloutConfig = {
  strategy?: 'progressive'
  rules: RolloutRule[]
  gate: GateConfig
  inFlightPolicy?: 'pin-to-version'
}

export type RolloutStatus = 'idle' | 'canary' | 'full' | 'rolled_back'

export type RolloutAlertAction = {
  type: 'rollback'
  from_version: number | null
  to_version: number | null
  reason: string
  actor: string
}

export type RolloutTraffic = {
  stable: number
  candidate: number
  segments: Partial<Record<'internal' | 'lowValueBucket' | 'canary' | 'full' | 'fallback', number>>
}

export type RolloutSnapshot = {
  graphId: string
  status: RolloutStatus
  config: RolloutConfig | null
  stable: number | null
  candidate: number | null
  startedAt: string | null
  rolledBackAt: string | null
  rollbackReason: string | null
  rollbackActor: string | null
  traffic: RolloutTraffic
}

/** 发布门禁 blocked（409）：携带完整 GateReport 供 Modal 展示，不产新版本 */
export class GateBlockedError extends Error {
  report: GateReport
  constructor(report: GateReport) {
    super('发布门禁未通过，已拦截发布（存在不匹配用例）')
    this.name = 'GateBlockedError'
    this.report = report
  }
}

export async function listVersions(graphId: string): Promise<number[]> {
  const body = await request<{ items: number[] }>(`/api/graphs/${graphId}/versions`)
  return body.items
}

/** B3（D26 子集）：两版 graph 配置结构差异。 */
export type GraphNodeChange =
  | { field: 'type' | 'name'; from: unknown; to: unknown }
  | { field: 'config'; configKeys: string[] }

export type GraphDiff = {
  nodes: { added: string[]; removed: string[]; changed: { id: string; changes: GraphNodeChange[] }[] }
  edges: { added: string[]; removed: string[] }
  variables: { added: string[]; removed: string[]; changed: string[] }
}

export type GraphDiffResponse = {
  graphId: string
  fromVersion: number | null
  toVersion: number | null
  summary: Record<string, number>
  diff: GraphDiff
}

/** 两版 graph 配置结构 diff（只读）。版本号省略时：to＝latest 草稿、from＝最近发布版。 */
export async function getGraphDiff(
  graphId: string,
  fromVersion?: number,
  toVersion?: number,
): Promise<GraphDiffResponse> {
  const params = new URLSearchParams()
  if (fromVersion !== undefined) params.set('fromVersion', String(fromVersion))
  if (toVersion !== undefined) params.set('toVersion', String(toVersion))
  const qs = params.toString()
  return request(`/api/graphs/${graphId}/diff${qs ? `?${qs}` : ''}`)
}

export async function runReleaseGate(graphId: string): Promise<GateReport> {
  return request(`/api/graphs/${graphId}/release-gate`, { method: 'POST' })
}

/** docs/28 §5.2 ⑪：发布前子图版本升级体检（只读，不产版本、不阻断）。 */
export type SubgraphUpgrade = {
  node_id: string
  sub_id: string
  from_version: number | null
  to_version: number
  first_pin: boolean
}

export async function getSubgraphUpgrades(graphId: string): Promise<SubgraphUpgrade[]> {
  const body = await request<{ items: SubgraphUpgrade[] }>(
    `/api/graphs/${graphId}/subgraph-upgrades`,
  )
  return body.items
}

/** D26 报告 v1：本图批量回放报告历史（倒序摘要，不含逐例 cases） */
export async function listReleaseReports(graphId: string): Promise<ReleaseReportSummary[]> {
  const body = await request<{ items: ReleaseReportSummary[] }>(
    `/api/graphs/${graphId}/release-reports`,
  )
  return body.items
}

/** docs/28 §2.4：跨图用例集报告看板（倒序摘要，limit 默认 100、上限 200） */
export async function listAllReleaseReports(
  limit = 100,
): Promise<ReleaseReportSummary[]> {
  const body = await request<{ items: ReleaseReportSummary[] }>(
    `/api/release-reports?limit=${limit}`,
  )
  return body.items
}

/** D26 报告 v1：报告详情（含逐例 ✓/✗/note）；跨图/不存在由后端 404 */
export async function getReleaseReport(
  graphId: string,
  reportId: string,
): Promise<ReleaseReport> {
  return request(`/api/graphs/${graphId}/release-reports/${reportId}`)
}

/** D26 报告导出：浏览器下载 CSV/JSON（attachment，read 角色；404 口径同详情）。 */
export async function exportReleaseReport(
  graphId: string,
  reportId: string,
  format: 'csv' | 'json',
): Promise<void> {
  const headers = new Headers()
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(
    `/api/graphs/${graphId}/release-reports/${reportId}/export?format=${format}`,
    { headers },
  )
  if (!response.ok) {
    throw new Error(`报告导出失败：${response.status}`)
  }
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${reportId}.${format}`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

/**
 * 发布 latest 草稿为不可变版本。gate=true 先跑批量回放门禁：
 * blocked → 抛 GateBlockedError（携带报告、不产版本）；total=0 skipped 不阻塞。
 */
export async function publishGraph(
  graphId: string,
  gate = false,
): Promise<{ id: string; releaseVersion: number }> {
  if (!gate) {
    return request(`/api/graphs/${graphId}/publish`, { method: 'POST', body: JSON.stringify({}) })
  }
  const headers = new Headers({ 'Content-Type': 'application/json' })
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`/api/graphs/${graphId}/publish`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ gate: true }),
  })
  const body = await response.json().catch(() => null)
  if (response.status === 409 && body?.detail?.report) {
    throw new GateBlockedError(body.detail.report as GateReport)
  }
  if (!response.ok) {
    const detail = body?.detail
    throw new Error(
      typeof detail === 'object'
        ? detail?.message ?? `发布失败：${response.status}`
        : detail || `发布失败：${response.status}`,
    )
  }
  return body as { id: string; releaseVersion: number }
}

export async function getRollout(graphId: string): Promise<RolloutSnapshot> {
  return request(`/api/graphs/${graphId}/rollout`)
}

export async function updateRollout(
  graphId: string,
  config: RolloutConfig,
): Promise<RolloutSnapshot> {
  return request(`/api/graphs/${graphId}/rollout`, {
    method: 'PUT',
    body: JSON.stringify(config),
  })
}

export async function startRollout(graphId: string): Promise<RolloutSnapshot> {
  return request(`/api/graphs/${graphId}/rollout/start`, { method: 'POST' })
}

export async function promoteRollout(graphId: string): Promise<RolloutSnapshot> {
  return request(`/api/graphs/${graphId}/rollout/promote`, { method: 'POST' })
}

export async function rollbackRollout(graphId: string): Promise<RolloutSnapshot> {
  return request(`/api/graphs/${graphId}/rollout/rollback`, { method: 'POST' })
}

// --- M11 长期记忆（docs/26 §6；只读浏览 + admin 删除，写入只走图工具） --------

export type MemoryKind = 'fact' | 'preference'

export type MemoryItem = {
  id: string
  kind: MemoryKind
  content: string
  scope: Record<string, string>
  confidence: number
  source: string
  metadata: Record<string, string>
  created_at: string
}

export type MemorySearchResult = MemoryItem & { score: number }

export async function listMemories(
  kind?: MemoryKind,
  limit = 50,
): Promise<MemoryItem[]> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (kind) params.set('kind', kind)
  const body = await request<{ items: MemoryItem[] }>(`/api/memories?${params.toString()}`)
  return body.items
}

export async function searchMemories(
  q: string,
  opts: { kind?: MemoryKind; topK?: number; minScore?: number } = {},
): Promise<MemorySearchResult[]> {
  const params = new URLSearchParams({ q })
  if (opts.kind) params.set('kind', opts.kind)
  if (opts.topK !== undefined) params.set('top_k', String(opts.topK))
  if (opts.minScore !== undefined) params.set('min_score', String(opts.minScore))
  const body = await request<{ results: MemorySearchResult[] }>(
    `/api/memories/search?${params.toString()}`,
  )
  return body.results
}

export async function deleteMemory(id: string): Promise<void> {
  await request(`/api/memories/${id}`, { method: 'DELETE' })
}

/** docs/28 §5.1 ⑩：手动新建/编辑记忆入参（source 由后端固定 manual，不在此传）。 */
export type MemoryWritePayload = {
  kind: MemoryKind
  content: string
  scope?: Record<string, string>
  confidence?: number
  metadata?: Record<string, string>
}

export async function createMemory(payload: MemoryWritePayload): Promise<MemoryItem> {
  return request('/api/memories', { method: 'POST', body: JSON.stringify(payload) })
}

export async function updateMemory(
  id: string,
  payload: Partial<MemoryWritePayload>,
): Promise<MemoryItem> {
  return request(`/api/memories/${id}`, { method: 'PUT', body: JSON.stringify(payload) })
}

// --- D26 影子模式（docs/33 §3；线上旁路录制，sync、无 SSE） ---

/** 一次工具节点的旁路意图（action_status：SHADOW_DRY_RUN/SUCCESS/FAILED/SIMULATED）。 */
export type ToolIntent = {
  node_id: string
  tool: string
  permission: string | null
  dry_run: boolean
  parameters: Record<string, unknown> | null
  action_status: string
}

/** 路由决策节点（condition/human_approval/loop）的产出目标。 */
export type ShadowDecision = {
  node_id: string
  node_type: string
  target: string | null
}

/** 人工实际处理（创建时可带，或事后补录）。 */
export type HumanOutcome = {
  action: string
  note?: string | null
}

export type ShadowComparison = {
  /** true 一致 / false 不一致 / null 无法判定（系统无写意图或尚无人工结果）。 */
  match: boolean | null
  auto_action: string | null
  human_action: string | null
  diffs: string[]
}

export type ShadowRun = {
  id: string
  graph_id: string
  inputs: Record<string, unknown> | null
  status: string // completed | error
  error: string | null
  decisions: ShadowDecision[]
  tool_intents: ToolIntent[]
  trace_id: string
  auto_action: string | null
  human_outcome: HumanOutcome | null
  comparison: ShadowComparison
  created_at: string
}

/** 对已保存图发起一次影子运行（operate；latest 草稿、预置全 approved、零副作用）。 */
export async function createShadowRun(
  graphId: string,
  body: { inputs?: Record<string, unknown>; human_outcome?: HumanOutcome },
): Promise<ShadowRun> {
  return request(`/api/graphs/${graphId}/shadow-runs`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/** 列出影子运行（read；倒序，可按图过滤，limit 1–200 默认 50）。 */
export async function listShadowRuns(
  graphId?: string,
  limit = 50,
): Promise<ShadowRun[]> {
  const qs = new URLSearchParams({ limit: String(limit) })
  if (graphId) qs.set('graph_id', graphId)
  const body = await request<{ items: ShadowRun[] }>(`/api/shadow-runs?${qs.toString()}`)
  return body.items
}

export async function getShadowRun(id: string): Promise<ShadowRun> {
  return request(`/api/shadow-runs/${id}`)
}

/** 补录人工实际处理并重算对比（operate）。 */
export async function compareShadowRun(
  id: string,
  humanOutcome: HumanOutcome,
): Promise<ShadowRun> {
  return request(`/api/shadow-runs/${id}/compare`, {
    method: 'POST',
    body: JSON.stringify({ human_outcome: humanOutcome }),
  })
}

// --- OAuth2 连接管理（docs/35 §4，T4；generic，平台无关） --------------------

export type ConnectionStatus = 'draft' | 'connected' | 'error'

/** 连接对外投影：后端 public_view 已删除全部信封/明文秘密。 */
export type ConnectionView = {
  id: string
  provider: string
  displayName: string
  authUrl: string
  tokenUrl: string
  clientId: string
  hasClientSecret: boolean
  scopes: string[]
  redirectUri: string
  status: ConnectionStatus
  tokenType: string | null
  expiresAt: string | null
  lastError: string | null
  createdBy: string | null
  createdAt: string | null
  updatedAt: string | null
}

export type ConnectionInput = {
  provider: string
  displayName: string
  authUrl: string
  tokenUrl: string
  clientId: string
  clientSecret?: string
  scopes?: string[]
  redirectUri?: string
}

export type AuthorizeInfo = { authorizeUrl: string; state: string; expiresIn: number }
export type ConnectionTestResult = {
  ok: boolean
  status: ConnectionStatus
  expiresAt: string | null
  reason?: string
}

export async function listConnections(): Promise<ConnectionView[]> {
  const body = await request<{ items: ConnectionView[] }>('/api/connections')
  return body.items
}

export async function createConnection(input: ConnectionInput): Promise<ConnectionView> {
  return request('/api/connections', { method: 'POST', body: JSON.stringify(input) })
}

export async function updateConnection(
  id: string,
  input: Partial<ConnectionInput>,
): Promise<ConnectionView> {
  return request(`/api/connections/${id}`, { method: 'PUT', body: JSON.stringify(input) })
}

export async function deleteConnection(id: string): Promise<{ deleted: boolean }> {
  return request(`/api/connections/${id}`, { method: 'DELETE' })
}

export async function authorizeConnection(id: string): Promise<AuthorizeInfo> {
  return request(`/api/connections/${id}/authorize`, { method: 'POST' })
}

export async function exchangeConnection(
  id: string,
  code: string,
  state: string,
): Promise<ConnectionView> {
  return request(`/api/connections/${id}/exchange`, {
    method: 'POST',
    body: JSON.stringify({ code, state }),
  })
}

export async function refreshConnection(id: string): Promise<ConnectionView> {
  return request(`/api/connections/${id}/refresh`, { method: 'POST' })
}

export async function testConnection(id: string): Promise<ConnectionTestResult> {
  return request(`/api/connections/${id}/test`, { method: 'POST' })
}

export type ChannelBindingView = {
  id: string
  provider: string
  connectionId: string
  config: { shop: string; apiVersion: string }
  status: 'connected' | 'error'
  lastError: string | null
  createdBy: string | null
  createdAt: string | null
}

export type ChannelBindingInput = {
  provider: string
  connectionId: string
  config: { shop: string; apiVersion?: string }
}

export type ChannelTestResult = {
  ok: boolean
  status: string
  reason?: string
}

export async function listChannelBindings(): Promise<ChannelBindingView[]> {
  const body = await request<{ items: ChannelBindingView[] }>('/api/channels')
  return body.items
}

export async function createChannelBinding(
  input: ChannelBindingInput,
): Promise<ChannelBindingView> {
  return request('/api/channels', { method: 'POST', body: JSON.stringify(input) })
}

export async function testChannelBinding(id: string): Promise<ChannelTestResult> {
  return request(`/api/channels/${id}/test`, { method: 'POST' })
}

export async function deleteChannelBinding(id: string): Promise<{ deleted: boolean }> {
  return request(`/api/channels/${id}`, { method: 'DELETE' })
}

export type WebhookSubscription = {
  topic: string
  graphId: string
  enabled: boolean
}

export async function getWebhookSubscriptions(id: string): Promise<WebhookSubscription[]> {
  const body = await request<{ items: WebhookSubscription[] }>(
    `/api/channels/${id}/webhooks`,
  )
  return body.items
}

export async function putWebhookSubscriptions(
  id: string,
  subscriptions: WebhookSubscription[],
): Promise<WebhookSubscription[]> {
  const body = await request<{ items: WebhookSubscription[] }>(
    `/api/channels/${id}/webhooks`,
    { method: 'PUT', body: JSON.stringify({ subscriptions }) },
  )
  return body.items
}

export type WebhookDeadLetter = {
  webhookId: string
  bindingId: string
  topic: string
  shop: string
  reasons: { graphId: string; code: string }[]
  createdAt: string
  replayedAt: string | null
}

export type WebhookDeliveryMetrics = {
  byTopic: Record<string, { received: number; dead: number; duplicates: number }>
  totals: { received: number; dead: number; duplicates: number }
}

export async function listWebhookDeadLetters(params?: {
  topic?: string
  bindingId?: string
  limit?: number
}): Promise<WebhookDeadLetter[]> {
  const query = new URLSearchParams()
  if (params?.topic) query.set('topic', params.topic)
  if (params?.bindingId) query.set('bindingId', params.bindingId)
  if (params?.limit) query.set('limit', String(params.limit))
  const suffix = query.size > 0 ? `?${query.toString()}` : ''
  const body = await request<{ items: WebhookDeadLetter[] }>(
    `/api/channels/webhooks/dead-letters${suffix}`,
  )
  return body.items
}

export async function replayWebhookDeadLetter(
  webhookId: string,
): Promise<{ webhookId: string; status: string; reasons: { graphId: string; code: string }[] }> {
  return request(
    `/api/channels/webhooks/dead-letters/${encodeURIComponent(webhookId)}/replay`,
    { method: 'POST' },
  )
}

export async function deleteWebhookDeadLetter(webhookId: string): Promise<{ deleted: boolean }> {
  return request(`/api/channels/webhooks/dead-letters/${encodeURIComponent(webhookId)}`, {
    method: 'DELETE',
  })
}

export async function getWebhookMetrics(): Promise<WebhookDeliveryMetrics> {
  return request('/api/channels/webhooks/metrics')
}

export type RemoteWebhook = {
  remoteId: string
  topic: string
  address: string
}

export async function listRemoteWebhooks(
  id: string,
): Promise<{ items: RemoteWebhook[]; error?: string }> {
  return request(`/api/channels/${id}/remote-webhooks`)
}

export async function registerRemoteWebhook(
  id: string,
  topic: string,
): Promise<RemoteWebhook> {
  return request(`/api/channels/${id}/remote-webhooks`, {
    method: 'POST',
    body: JSON.stringify({ topic }),
  })
}

export async function unregisterRemoteWebhook(
  id: string,
  topic: string,
): Promise<{ deleted: boolean }> {
  return request(
    `/api/channels/${id}/remote-webhooks/${topic
      .split('/')
      .map((part) => encodeURIComponent(part))
      .join('/')}`,
    { method: 'DELETE' },
  )
}

// --- OpenAPI 导入（docs/42 §5；零新依赖，进程内 store） --------------------

export type OpenApiSource = {
  content?: string
  url?: string
}

export type SecurityScheme = {
  name: string
  kind: 'api_key' | 'bearer' | 'basic'
  location: 'header' | 'query' | null
  param: string
}

export type BasicCredential = {
  username: string
  password: string
}

export type CredentialValue = string | BasicCredential

export type CredentialInput = CredentialValue | null

export type OperationDescriptor = {
  name: string
  method: string
  path: string
  summary: string | null
  description: string | null
  permission: string
  idempotent: boolean
  locations: Record<string, string>
  input_schema: JsonSchema
  security: string[][]
  skipped: boolean
  skip_reason?: string | null
}

export type OpenApiPreview = {
  title: string
  base_url: string
  operations: OperationDescriptor[]
  security_schemes: SecurityScheme[]
  imported_count: number
  skipped_count: number
}

export type ImportedSpec = {
  spec_id: string
  title: string
  base_url: string
  created_at: string
  operations: OperationDescriptor[]
  security_schemes: Record<string, SecurityScheme>
  credential_envelopes: Record<string, string>
}

export async function previewOpenApi(source: OpenApiSource): Promise<OpenApiPreview> {
  return request('/api/openapi/preview', { method: 'POST', body: JSON.stringify(source) })
}

export async function importOpenApi(
  source: OpenApiSource,
  credentials?: Record<string, CredentialValue>,
): Promise<ImportedSpec> {
  return request('/api/openapi/imports', {
    method: 'POST',
    body: JSON.stringify(credentials ? { ...source, credentials } : source),
  })
}

export async function putOpenApiCredentials(
  specId: string,
  credentials: Record<string, CredentialInput>,
): Promise<{ configured: string[] }> {
  return request(`/api/openapi/imports/${encodeURIComponent(specId)}/credentials`, {
    method: 'PUT',
    body: JSON.stringify({ credentials }),
  })
}

export async function listOpenApiImports(): Promise<ImportedSpec[]> {
  const body = await request<{ items: ImportedSpec[] }>('/api/openapi/imports')
  return body.items
}

export async function getOpenApiImport(specId: string): Promise<ImportedSpec> {
  return request(`/api/openapi/imports/${encodeURIComponent(specId)}`)
}

export async function deleteOpenApiImport(specId: string): Promise<{ deleted: boolean }> {
  return request(`/api/openapi/imports/${encodeURIComponent(specId)}`, { method: 'DELETE' })
}
