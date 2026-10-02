import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  listSchedules,
  previewScheduleCron,
  runScheduleNow,
  setScheduleEnabled,
} from '../apiClient'

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const item = {
  graphId: 'graph 1',
  scheduleId: 't1n',
  action: 'run' as const,
  version: 3,
  cron: '*/5 * * * *',
  enabled: true,
  createdAt: '2026-09-26T08:00:00+00:00',
  lastFiredAt: null,
  lastSkippedAt: '2026-09-26T09:00:00+00:00',
  skipCount: 2,
  timeZone: 'UTC',
  catchUpMinutes: 0,
  overlapPolicy: 'skip' as const,
  nextFireAt: '2026-09-26T10:05:00+00:00',
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('定时调度 REST 面（docs/68 §2.4，打包 N ⑤）', () => {
  it('listSchedules 取 GET /api/schedules 的 items 信封', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ items: [item] }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const rows = await listSchedules()
    expect(fetchMock.mock.calls[0][0]).toBe('/api/schedules')
    expect(rows).toEqual([item])
  })

  it('setScheduleEnabled POST 到按图编码的路径，body 带 schedule_id', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ ...item, enabled: false }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const next = await setScheduleEnabled('graph 1', false, 't1n')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/schedules/graph%201/enabled')
    const init = fetchMock.mock.calls[0][1]
    expect(init?.method).toBe('POST')
    // 打包 ZN：开关按 schedule_id 点名（run 的 schedule_id＝定时节点 id）。
    expect(JSON.parse(init?.body as string)).toEqual({
      enabled: false,
      schedule_id: 't1n',
    })
    expect(next.enabled).toBe(false)
  })

  it('setScheduleEnabled 点名 reflect 调度时 body 带 __reflect__', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ ...item, scheduleId: '__reflect__', action: 'reflect', enabled: false }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const next = await setScheduleEnabled('graph 1', false, '__reflect__')
    const init = fetchMock.mock.calls[0][1]
    expect(JSON.parse(init?.body as string)).toEqual({
      enabled: false,
      schedule_id: '__reflect__',
    })
    expect(next.scheduleId).toBe('__reflect__')
    expect(next.action).toBe('reflect')
  })

  it('runScheduleNow 是带路径的 POST 且 body 点名 schedule_id', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ runId: 'r-1', graphId: 'g1', version: 3 }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const result = await runScheduleNow('g1', 't1n')
    const init = fetchMock.mock.calls[0][1]
    expect(fetchMock.mock.calls[0][0]).toBe('/api/schedules/g1/run-now')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({ schedule_id: 't1n' })
    expect('runId' in result && result.runId).toBe('r-1')
  })

  it('runScheduleNow reflect 回的是报告摘要（无 runId，candidateId 可空）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, _init?: RequestInit) =>
        jsonResponse({ candidateId: null, graphId: 'g1', baseVersion: 2, status: 'no_evidence' }),
      ),
    )
    const result = await runScheduleNow('g1', '__reflect__')
    expect('runId' in result).toBe(false)
    expect('candidateId' in result && result.candidateId).toBeNull()
    expect('status' in result && result.status).toBe('no_evidence')
  })

  it('不合法的 cron 是答案不是异常（valid:false 正常 resolve）', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({
        valid: false,
        message: "Cron 表达式 '5/2 * * * *' 无效：分钟字段的 '5/2' 不在支持的语法内",
        nextFireAt: [],
        timeZone: 'UTC',
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const preview = await previewScheduleCron('5/2 * * * *')
    expect(preview.valid).toBe(false)
    expect(preview.nextFireAt).toEqual([])
    const init = fetchMock.mock.calls[0][1]
    // 打包 ZL：预演请求带 timeZone（缺省 UTC）
    expect(JSON.parse(init?.body as string)).toEqual({ cron: '5/2 * * * *', timeZone: 'UTC' })
  })

  it('409（同图还在跑）把后端中文 detail 带进 Error，页面才说得出原因', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse(
          { detail: '图 g1 仍有运行在执行中（running/suspended），本次立即运行已跳过' },
          409,
        ),
      ),
    )
    await expect(runScheduleNow('g1', 't1n')).rejects.toThrow('仍有运行在执行中')
  })

  it('cron 预演的 UTC 口径与三条槽位原样透传（页面自己格式化，不在此换算）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({
          valid: true,
          message: '',
          nextFireAt: [
            '2026-09-26T10:05:00+00:00',
            '2026-09-26T10:10:00+00:00',
            '2026-09-26T10:15:00+00:00',
          ],
          timeZone: 'UTC',
        }),
      ),
    )
    const preview = await previewScheduleCron('*/5 * * * *')
    expect(preview.timeZone).toBe('UTC')
    expect(preview.nextFireAt).toHaveLength(3)
    expect(preview.nextFireAt.every((iso) => iso.endsWith('+00:00'))).toBe(true)
  })
})
