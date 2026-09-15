/**
 * Graph DSL 后端 API 客户端（docs/12 §5；dev 经 Vite /api 代理到 8000）。
 */
import type { SerializedGraph } from './graphSerializer'

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

export type RunEvent =
  | { type: 'node_start'; node_id: string; node_type: string; approval?: ApprovalRequest }
  | { type: 'node_end'; node_id: string; node_type: string; output: unknown }
  | ({ type: 'run_end' } & Partial<RunResult>)

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = body?.detail
    throw new Error(
      Array.isArray(detail) ? detail.join('；') : detail || `请求失败：${response.status}`,
    )
  }
  return body as T
}

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
 */
export async function streamRun(
  id: string,
  inputs: RunInputs | undefined,
  onEvent: (event: RunEvent) => void,
): Promise<RunResult> {
  const response = await fetch(`/api/graphs/${id}/run/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ inputs }),
  })
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => null)
    throw new Error(body?.detail || `流式运行失败：${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let result: RunResult | null = null

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
      } else {
        onEvent(payload as RunEvent)
      }
    }
  }
  if (!result) throw new Error('SSE 流缺少最终运行结果')
  return result
}
