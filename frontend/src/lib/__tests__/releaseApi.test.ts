import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  GateBlockedError,
  exportReleaseReport,
  getReleaseReport,
  getRollout,
  listReleaseReports,
  listVersions,
  promoteRollout,
  publishGraph,
  rollbackRollout,
  runGraph,
  runReleaseGate,
  saveGraphDraft,
  startRollout,
  updateRollout,
  type GateReport,
  type ReleaseReportSummary,
  type RolloutConfig,
} from '../apiClient'

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

function stubFetch(body: unknown, status = 200) {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(body, status)))
}

const blockedReport: GateReport = {
  graph_id: 'graph-1',
  target: 'draft',
  total: 1,
  passed: 0,
  failed: 1,
  skipped: false,
  blocked: true,
  cases: [
    { case_id: 'c1', name: '退款黄金用例', matches: false, replay_status: 'completed', note: 'tool 输出不一致' },
  ],
}

const rolloutConfig: RolloutConfig = {
  strategy: 'progressive',
  rules: [{ to: 'canary', percent: 5 }],
  gate: {
    observeMinutes: 30,
    autoRollback: true,
    minSamples: 3,
    metrics: [{ id: 'run_error_rate', threshold: 0.02 }],
  },
  inFlightPolicy: 'pin-to-version',
}

const rolloutSnapshot = {
  graphId: 'graph-1',
  status: 'idle',
  config: rolloutConfig,
  stable: null,
  candidate: null,
  startedAt: null,
  rolledBackAt: null,
  rollbackReason: null,
  rollbackActor: null,
  traffic: { stable: 0, candidate: 0, segments: { internal: 0, lowValueBucket: 0, canary: 0, full: 0, fallback: 0 } },
}

afterEach(() => vi.unstubAllGlobals())

describe('发布门禁与草稿端点（M9）', () => {
  it('runReleaseGate POST 到 release-gate 并返回报告', async () => {
    stubFetch(blockedReport)
    const report = await runReleaseGate('graph-1')
    expect(report.blocked).toBe(true)
    const fetchMock = vi.mocked(fetch)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/graphs/graph-1/release-gate')
    expect(fetchMock.mock.calls[0][1]?.method).toBe('POST')
  })

  it('publishGraph(gate=true) 成功返回新版本，请求体带 gate', async () => {
    stubFetch({ id: 'graph-1', releaseVersion: 2 })
    const result = await publishGraph('graph-1', true)
    expect(result.releaseVersion).toBe(2)
    expect(JSON.parse(vi.mocked(fetch).mock.calls[0][1]?.body as string)).toEqual({ gate: true })
  })

  it('publishGraph(gate=true) blocked 抛 GateBlockedError 且携带报告', async () => {
    stubFetch({ detail: { message: '门禁未通过', report: blockedReport } }, 409)
    await expect(publishGraph('graph-1', true)).rejects.toBeInstanceOf(GateBlockedError)
  })

  it('saveGraphDraft 用 PUT 覆盖同图草稿', async () => {
    stubFetch({ id: 'graph-1', version: 3 })
    const graph = { version: 3, variables: [], nodes: [], edges: [] }
    await saveGraphDraft('graph-1', graph)
    const fetchMock = vi.mocked(fetch)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/graphs/graph-1')
    expect(fetchMock.mock.calls[0][1]?.method).toBe('PUT')
  })

  it('listVersions 返回版本号数组', async () => {
    stubFetch({ items: [1, 2, 3] })
    await expect(listVersions('graph-1')).resolves.toEqual([1, 2, 3])
  })
})

describe('D26 报告 v1：历史报告端点（U60 ⑧）', () => {
  const summary: ReleaseReportSummary = {
    id: 'rr-2',
    graph_id: 'graph-1',
    target: 'draft',
    trigger: 'manual',
    total: 2,
    passed: 1,
    failed: 1,
    skipped: false,
    blocked: true,
    pass_rate: 0.5,
    created_at: '2026-09-18T10:00:00+00:00',
  }

  it('listReleaseReports GET 摘要列表（不含 cases）并倒序返回', async () => {
    stubFetch({ items: [summary] })
    const items = await listReleaseReports('graph-1')
    expect(items).toHaveLength(1)
    expect(items[0].id).toBe('rr-2')
    expect('cases' in items[0]).toBe(false)
    const url = vi.mocked(fetch).mock.calls[0][0]
    expect(url).toBe('/api/graphs/graph-1/release-reports')
  })

  it('getReleaseReport GET 详情（含逐例 cases）', async () => {
    stubFetch({
      ...summary,
      trigger: 'publish-gate',
      cases: [
        { case_id: 'rec-1', name: '黄金用例', matches: true, replay_status: 'completed', note: '全部节点一致' },
      ],
    })
    const detail = await getReleaseReport('graph-1', 'rr-2')
    expect(detail.cases).toHaveLength(1)
    expect(detail.trigger).toBe('publish-gate')
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe(
      '/api/graphs/graph-1/release-reports/rr-2',
    )
  })

  it('exportReleaseReport 拉取 blob 并触发 a[download] 点击（CSV）', async () => {
    const blob = new Blob(['csv'], { type: 'text/csv' })
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, blob: async () => blob })))
    const createObjectURL = vi.fn(() => 'blob:mock')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
    const click = vi.fn()
    const anchor = { href: '', download: '', click, remove: vi.fn() }
    vi.stubGlobal('document', {
      body: { appendChild: vi.fn() },
      createElement: () => anchor,
    })

    await exportReleaseReport('graph-1', 'rr-2', 'csv')

    expect(vi.mocked(fetch).mock.calls[0][0]).toBe(
      '/api/graphs/graph-1/release-reports/rr-2/export?format=csv',
    )
    expect(createObjectURL).toHaveBeenCalledTimes(1)
    expect(click).toHaveBeenCalledTimes(1)
    expect(anchor.download).toBe('rr-2.csv')
  })

  it('exportReleaseReport 非 2xx 拒绝（不触发下载）', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 404, blob: async () => new Blob([]) })))
    await expect(exportReleaseReport('graph-1', 'rr-x', 'json')).rejects.toThrow('404')
  })
})

describe('灰度状态机端点（M9）', () => {
  it('getRollout GET、start/promote/rollback 分别 POST 对应子路径', async () => {
    stubFetch(rolloutSnapshot)
    await getRollout('graph-1')
    await startRollout('graph-1')
    await promoteRollout('graph-1')
    await rollbackRollout('graph-1')
    const calls = vi.mocked(fetch).mock.calls.map((call) => [
      call[0],
      call[1]?.method ?? 'GET',
    ])
    expect(calls).toEqual([
      ['/api/graphs/graph-1/rollout', 'GET'],
      ['/api/graphs/graph-1/rollout/start', 'POST'],
      ['/api/graphs/graph-1/rollout/promote', 'POST'],
      ['/api/graphs/graph-1/rollout/rollback', 'POST'],
    ])
  })

  it('updateRollout PUT 配置体', async () => {
    stubFetch(rolloutSnapshot)
    await updateRollout('graph-1', rolloutConfig)
    const init = vi.mocked(fetch).mock.calls[0][1]
    expect(init?.method).toBe('PUT')
    expect(JSON.parse(init?.body as string).gate.autoRollback).toBe(true)
  })
})

describe('runGraph 入站事件与钉版（M9）', () => {
  it('event 选项写入 body.event，供 Router 分桶', async () => {
    stubFetch({ id: 'run-1', status: 'completed', outputs: {}, trace: [] })
    await runGraph('graph-1', { order_id: '12347' }, {
      event: { channel: 'webhook', payload: { amount: 128 } },
    })
    const body = JSON.parse(vi.mocked(fetch).mock.calls[0][1]?.body as string)
    expect(body.event).toEqual({ channel: 'webhook', payload: { amount: 128 } })
    expect(body.inputs).toEqual({ order_id: '12347' })
    expect(body.debug).toBeUndefined()
  })

  it('releaseVersion 选项写入 body.releaseVersion', async () => {
    stubFetch({ id: 'run-2', status: 'completed', outputs: {}, trace: [] })
    await runGraph('graph-1', {}, { releaseVersion: 2 })
    const body = JSON.parse(vi.mocked(fetch).mock.calls[0][1]?.body as string)
    expect(body.releaseVersion).toBe(2)
    expect(body.event).toBeUndefined()
  })
})
