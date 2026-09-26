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
  version: 3,
  cron: '*/5 * * * *',
  enabled: true,
  createdAt: '2026-09-26T08:00:00+00:00',
  lastFiredAt: null,
  lastSkippedAt: '2026-09-26T09:00:00+00:00',
  skipCount: 2,
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

  it('setScheduleEnabled POST 到按图编码的路径，body 只有 enabled', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ ...item, enabled: false }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const next = await setScheduleEnabled('graph 1', false)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/schedules/graph%201/enabled')
    const init = fetchMock.mock.calls[0][1]
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({ enabled: false })
    expect(next.enabled).toBe(false)
  })

  it('runScheduleNow 是带路径的 POST 且不带 body', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ runId: 'r-1', graphId: 'g1', version: 3 }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const result = await runScheduleNow('g1')
    const init = fetchMock.mock.calls[0][1]
    expect(fetchMock.mock.calls[0][0]).toBe('/api/schedules/g1/run-now')
    expect(init?.method).toBe('POST')
    expect(init?.body).toBeUndefined()
    expect(result.runId).toBe('r-1')
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
    expect(JSON.parse(init?.body as string)).toEqual({ cron: '5/2 * * * *' })
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
    await expect(runScheduleNow('g1')).rejects.toThrow('仍有运行在执行中')
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
