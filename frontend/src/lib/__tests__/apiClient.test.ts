import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  DebugRunStoppedError,
  RunCancelledError,
  cancelActiveRun,
  createChannelBinding,
  deleteChannelBinding,
  getWebhookSubscriptions,
  listChannelBindings,
  putWebhookSubscriptions,
  resumeDebug,
  streamRun,
  testChannelBinding,
  type ChannelBindingView,
  type RunEvent,
} from '../apiClient'

function sseResponse(frames: Array<Record<string, unknown>>) {
  const body = frames
    .map((frame) => `event: ${frame.type ?? 'frame'}\ndata: ${JSON.stringify(frame)}\n\n`)
    .join('')
  return {
    ok: true,
    body: new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(body))
        controller.close()
      },
    }),
  } as Response
}

const pausedFrame = {
  type: 'paused',
  token: 'dbg-1',
  node_id: 'trigger-1',
  node_type: 'trigger',
  reason: 'step',
  globals: { approval_limit: 500 },
  outputs: {},
}

const resultFrame = {
  id: 'graph-1',
  status: 'completed',
  outputs: { 'tool_call-1': { result: { status: 'SUCCESS' } } },
  trace: ['trigger-1'],
}

afterEach(() => vi.unstubAllGlobals())

describe('streamRun debug framing (04 §5.12)', () => {
  it('forwards paused frames without mistaking them for the result', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse([pausedFrame, resultFrame])))
    const events: RunEvent[] = []
    const result = await streamRun('graph-1', {}, (event) => events.push(event), {
      breakpoints: [],
    })
    expect(events).toHaveLength(1)
    expect(events[0].type).toBe('paused')
    expect(result.id).toBe('graph-1')

    const fetchMock = vi.mocked(fetch)
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(JSON.parse(init.body as string)).toEqual({ inputs: {}, debug: { breakpoints: [] } })
  })

  it('rejects with DebugRunStoppedError after a stopped frame and no result', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse([pausedFrame, {
      type: 'stopped',
      node_id: 'tool_call-1',
      reason: 'user_stop',
    }])))
    const events: RunEvent[] = []
    await expect(streamRun('graph-1', {}, (event) => events.push(event))).rejects.toBeInstanceOf(
      DebugRunStoppedError,
    )
    expect(events.map((event) => event.type)).toEqual(['paused', 'stopped'])
  })
})


function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data } as Response
}

describe('B 包 streamRun 急停/日志帧（docs/27 §4，U134/U136）', () => {
  it('cancelled 终帧转发并以 RunCancelledError 拒绝（无 result）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        sseResponse([
          { type: 'node_start', node_id: 'trigger-1', node_type: 'trigger' },
          { type: 'cancelled', node_id: 'tool-1', reason: 'user_cancel' },
        ]),
      ),
    )
    const events: RunEvent[] = []
    await expect(
      streamRun('graph-1', {}, (event) => events.push(event)),
    ).rejects.toBeInstanceOf(RunCancelledError)
    expect(events.map((event) => event.type)).toEqual(['node_start', 'cancelled'])
  })

  it('debug_log 帧转发给 onEvent 且不暂停、运行正常完成', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        sseResponse([
          { type: 'debug_log', node_id: 'tool-1', hits: 2, message: '到达退款节点' },
          resultFrame,
        ]),
      ),
    )
    const events: RunEvent[] = []
    const result = await streamRun('graph-1', {}, (event) => events.push(event))
    expect(events[0].type).toBe('debug_log')
    if (events[0].type === 'debug_log') {
      expect(events[0].hits).toBe(2)
      expect(events[0].message).toBe('到达退款节点')
    }
    expect(result.id).toBe('graph-1')
  })
})

describe('B 包 resumeDebug globals 覆盖（docs/27 §4.3，U137）', () => {
  it('带 globals 时请求体含 globals，不带时不含', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ token: 'dbg-1', action: 'continue' }),
    )
    vi.stubGlobal('fetch', fetchMock)
    await resumeDebug('dbg-1', 'continue', { amount: 9999 })
    let init = fetchMock.mock.calls[0][1]
    expect(JSON.parse(init?.body as string)).toEqual({
      action: 'continue',
      globals: { amount: 9999 },
    })

    fetchMock.mockClear()
    await resumeDebug('dbg-1', 'stop')
    init = fetchMock.mock.calls[0][1]
    expect(JSON.parse(init?.body as string)).toEqual({ action: 'stop' })
  })
})

describe('B 包 cancelActiveRun 协作式急停（docs/27 §4.1，U134）', () => {
  it('命中本图在途 run 时 POST cancel 并返回 runId', async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/api/runs?limit=50')) {
        return jsonResponse({
          items: [
            { runId: 'run-old', graphId: 'other', status: 'completed' },
            { runId: 'run-1', graphId: 'graph-1', status: 'running' },
          ],
        })
      }
      if (url.endsWith('/api/runs/run-1/cancel')) {
        expect(init?.method).toBe('POST')
        return jsonResponse({ run_id: 'run-1', cancelled: true })
      }
      throw new Error(`unexpected url ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const result = await cancelActiveRun('graph-1')
    expect(result).toEqual({ runId: 'run-1' })
  })

  it('suspended（wait 阻塞中）的在途 run 也可取消', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        url.endsWith('/api/runs?limit=50')
          ? jsonResponse({ items: [{ runId: 'run-2', graphId: 'graph-1', status: 'suspended' }] })
          : jsonResponse({ run_id: 'run-2', cancelled: true }),
      ),
    )
    expect(await cancelActiveRun('graph-1')).toEqual({ runId: 'run-2' })
  })

  it('无本图在途 run 返回 null（不调用 cancel）', async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ items: [{ runId: 'run-3', graphId: 'graph-1', status: 'completed' }] }),
    )
    vi.stubGlobal('fetch', fetchMock)
    expect(await cancelActiveRun('graph-1')).toBeNull()
    expect(fetchMock.mock.calls).toHaveLength(1)
  })
})

describe('真实渠道绑定 /api/channels（docs/38 §1C/§1E）', () => {
  const binding: ChannelBindingView = {
    id: 'ch-1',
    provider: 'shopify',
    connectionId: 'conn-1',
    config: { shop: 'acme', apiVersion: '2025-01' },
    status: 'connected',
    lastError: null,
    createdBy: 'admin-a',
    createdAt: '1780000000',
  }

  it('listChannelBindings GETs /api/channels and unwraps items', async () => {
    const fetchMock = vi.fn(async (_url: string) => jsonResponse({ items: [binding] }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await listChannelBindings()
    expect(result).toEqual([binding])
    const [url] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/channels')
  })

  it('createChannelBinding POSTs provider/connectionId/config and returns the view', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({
      ok: true,
      status: 201,
      json: async () => binding,
    }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await createChannelBinding({
      provider: 'shopify',
      connectionId: 'conn-1',
      config: { shop: 'acme' },
    })
    expect(result.id).toBe('ch-1')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/channels')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({
      provider: 'shopify',
      connectionId: 'conn-1',
      config: { shop: 'acme' },
    })
  })

  it('testChannelBinding POSTs to the test subpath', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => jsonResponse({ ok: false, status: 'error', reason: 'CHANNEL_UNAUTHORIZED' }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await testChannelBinding('ch-1')
    expect(result.ok).toBe(false)
    expect(result.reason).toBe('CHANNEL_UNAUTHORIZED')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/channels/ch-1/test')
    expect(init?.method).toBe('POST')
  })

  it('deleteChannelBinding DELETEs and returns {deleted}', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => jsonResponse({ deleted: true }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await deleteChannelBinding('ch-1')
    expect(result).toEqual({ deleted: true })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/channels/ch-1')
    expect(init?.method).toBe('DELETE')
  })

  it('getWebhookSubscriptions GETs webhooks and unwraps items', async () => {
    const items = [{ topic: 'orders/create', graphId: 'graph-1', enabled: true }]
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => jsonResponse({ items }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await getWebhookSubscriptions('ch-1')
    expect(result).toEqual(items)
    const [url] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/channels/ch-1/webhooks')
  })

  it('putWebhookSubscriptions PUTs payload and unwraps items', async () => {
    const items = [{ topic: 'refunds/create', graphId: 'graph-2', enabled: false }]
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => jsonResponse({ items }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await putWebhookSubscriptions('ch-1', items)
    expect(result).toEqual(items)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/channels/ch-1/webhooks')
    expect(init?.method).toBe('PUT')
    expect(JSON.parse(init?.body as string)).toEqual({ subscriptions: items })
  })
})
