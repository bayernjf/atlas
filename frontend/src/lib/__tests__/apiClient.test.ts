import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  broadcastWaitEvent,
  listTasks,
  resolveInterruption,
  listWaits,
  signalWait,
  DebugRunStoppedError,
  RunCancelledError,
  RunSupersededError,
  cancelActiveRun,
  createChannelBinding,
  createUserTemplate,
  deleteChannelBinding,
  exportTemplate,
  instantiateTemplate,
  getWebhookSubscriptions,
  importTemplate,
  listTemplates,
  listChannelBindings,
  listRemoteWebhooks,
  listWebhookDeadLetters,
  putWebhookSubscriptions,
  registerRemoteWebhook,
  replayWebhookDeadLetter,
  resumeDebug,
  streamRun,
  testChannelBinding,
  touchTemplateUsage,
  unregisterRemoteWebhook,
  updateUserTemplate,
  createMessageTemplate,
  deleteMessageTemplate,
  getMessageTemplate,
  listMessageTemplates,
  updateMessageTemplate,
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

  it('superseded 终帧转发并以 RunSupersededError 拒绝（无 result）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        sseResponse([
          { type: 'node_start', node_id: 'trigger-1', node_type: 'trigger' },
          { type: 'superseded', node_id: 'tool-1', reason: 'resumed_by_other_process' },
        ]),
      ),
    )
    const events: RunEvent[] = []
    let caught: unknown
    try {
      await streamRun('graph-1', {}, (event) => events.push(event))
    } catch (e) {
      caught = e
    }
    expect(caught).toBeInstanceOf(RunSupersededError)
    expect(events.map((event) => event.type)).toEqual(['node_start', 'superseded'])
    expect((caught as Error).message).not.toContain('SSE 流缺少最终运行结果')
    expect((caught as RunSupersededError).nodeId).toBe('tool-1')
  })

  it('superseded 拒绝文案不再误报「SSE 流缺少最终运行结果」', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        sseResponse([
          { type: 'node_start', node_id: 'trigger-1', node_type: 'trigger' },
          { type: 'superseded', node_id: 'tool-1', reason: 'resumed_by_other_process' },
        ]),
      ),
    )
    let caught: unknown
    try {
      await streamRun('graph-1', {}, () => undefined)
    } catch (e) {
      caught = e
    }
    expect(caught).toBeInstanceOf(RunSupersededError)
    expect((caught as Error).message).not.toContain('SSE 流缺少最终运行结果')
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
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => jsonResponse({ ok: false, status: 'error', reason: 'CHANNEL_TOKEN_UNAVAILABLE' }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await testChannelBinding('ch-1')
    expect(result.ok).toBe(false)
    expect(result.reason).toBe('CHANNEL_TOKEN_UNAVAILABLE')
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

  it('listRemoteWebhooks GETs remote-webhooks keeping items and error', async () => {
    const payload = { items: [], error: 'CHANNEL_UPSTREAM_UNAUTHORIZED' }
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => jsonResponse(payload))
    vi.stubGlobal('fetch', fetchMock)
    const result = await listRemoteWebhooks('ch-1')
    expect(result).toEqual(payload)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/channels/ch-1/remote-webhooks')
  })

  it('registerRemoteWebhook POSTs topic and returns the remote webhook', async () => {
    const payload = { remoteId: '9', topic: 'orders/create', address: 'https://x/h' }
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => jsonResponse(payload))
    vi.stubGlobal('fetch', fetchMock)
    const result = await registerRemoteWebhook('ch-1', 'orders/create')
    expect(result).toEqual(payload)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/channels/ch-1/remote-webhooks')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({ topic: 'orders/create' })
  })

  it('unregisterRemoteWebhook DELETEs topic path and returns deleted flag', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ deleted: true }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const result = await unregisterRemoteWebhook('ch-1', 'refunds/create')
    expect(result).toEqual({ deleted: true })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/channels/ch-1/remote-webhooks/refunds/create')
    expect(init?.method).toBe('DELETE')
  })

  it('listWebhookDeadLetters GETs with filters and unwraps items', async () => {
    const items = [{
      webhookId: 'wh-1', bindingId: 'ch-1', topic: 'orders/create', shop: 'acme',
      reasons: [{ graphId: 'graph-1', code: 'NO_PUBLISHED_VERSION' }],
      createdAt: '2026-09-23T00:00:00+00:00', replayedAt: null,
    }]
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => jsonResponse({ items }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await listWebhookDeadLetters({ topic: 'orders/create', limit: 50 })
    expect(result).toEqual(items)
    const [url] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/channels/webhooks/dead-letters?topic=orders%2Fcreate&limit=50')
  })

  it('replayWebhookDeadLetter POSTs replay and returns status', async () => {
    const payload = {
      webhookId: 'wh/1', status: 'received',
      reasons: [],
    }
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => jsonResponse(payload))
    vi.stubGlobal('fetch', fetchMock)
    const result = await replayWebhookDeadLetter('wh/1')
    expect(result).toEqual(payload)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/channels/webhooks/dead-letters/wh%2F1/replay')
    expect(init?.method).toBe('POST')
  })

  it('listWaits GETs /api/waits and unwraps items', async () => {
    const items = [{
      token: 'w-1', eventKey: 'order.created', nodeId: 'wait-1', graphId: 'graph-1',
      timeoutSeconds: 300, deadlineAt: '2026-09-25T00:00:00Z',
    }]
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => jsonResponse({ items }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await listWaits()
    expect(result).toEqual(items)
    const [url] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/waits')
  })

  it('listTasks GETs /api/tasks with limit and unwraps items', async () => {
    const items = [{
      taskId: 't-1', runId: 'run-1', idempotencyKey: 'ik-1', traceId: 'tr-1',
      graphVersion: '1', type: 'shop.login', assignee: 'ops', payload: {},
      deadlineMs: 1000, parentSpanId: '', state: 'pending', result: {}, attempt: 1,
    }]
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => jsonResponse({ items }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await listTasks(200)
    expect(result).toEqual(items)
    const [url] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/tasks?limit=200')
  })

  it('signalWait POSTs payload to /api/waits/{token}/signal', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ token: 'w/1', released: true }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await signalWait('w/1', { order_id: '12345' })
    expect(result).toEqual({ token: 'w/1', released: true })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/waits/w%2F1/signal')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({ payload: { order_id: '12345' } })
  })

  it('broadcastWaitEvent POSTs eventKey and payload to /api/waits/events', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ released: 2, queued: false }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await broadcastWaitEvent('order.created', { order_id: '12345' })
    expect(result).toEqual({ released: 2, queued: false })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/waits/events')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({
      eventKey: 'order.created',
      payload: { order_id: '12345' },
    })
  })

  it('resolveInterruption POSTs abandon action to /api/interruptions/{token}/resolve (pack BL, docs/96)', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({
        resumeToken: 'tok/1',
        runId: 'r1',
        resolved: 'interrupted',
        frameDeleted: true,
      }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await resolveInterruption('tok/1', { action: 'abandon', reason: '未退款' })
    expect(result).toEqual({
      resumeToken: 'tok/1',
      runId: 'r1',
      resolved: 'interrupted',
      frameDeleted: true,
    })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/interruptions/tok%2F1/resolve')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({ action: 'abandon', reason: '未退款' })
  })
})

describe('template category + URL import/export (pack ZM, docs/08)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('listTemplates keeps category projection', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({
          items: [
            { id: 'refund-auto', name: 'n', description: '', tags: [], category: '退款流程', node_count: 2, source: 'catalog', deletable: false },
            { id: 'utpl-1', name: 'u', description: '', tags: [], category: '', node_count: 2, source: 'user', deletable: true, created_at: 'x' },
          ],
        }),
      ),
    )
    const items = await listTemplates()
    expect(items[0].category).toBe('退款流程')
    expect(items[1].category).toBe('')
  })

  it('exportTemplate parses attachment filename and body text', async () => {
    const pkg = { format: 'atlas-template-v1', meta: { name: 'n' }, graph: { nodes: [] } }
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({
      ok: true,
      status: 200,
      headers: new Headers({
        'content-disposition': 'attachment; filename="refund-auto.atlas-template.json"',
      }),
      json: async () => pkg,
      text: async () => JSON.stringify(pkg),
    }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await exportTemplate('refund-auto')
    expect(result.filename).toBe('refund-auto.atlas-template.json')
    expect(JSON.parse(result.packageText).format).toBe('atlas-template-v1')
    const [url] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/templates/refund-auto/export')
  })

  it('importTemplate POSTs a package payload', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ id: 'utpl-9', name: 'n', description: '', tags: [], category: '数据查询', graph: { nodes: [] }, source: 'user', deletable: true, created_at: 'x' }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await importTemplate({ package: { format: 'atlas-template-v1', meta: { name: 'n' }, graph: { nodes: [] } } })
    expect(result.category).toBe('数据查询')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/templates/import')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({ package: { format: 'atlas-template-v1', meta: { name: 'n' }, graph: { nodes: [] } } })
  })

  it('importTemplate POSTs a URL payload', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ id: 'utpl-10', name: 'r', description: '', tags: [], category: '', graph: { nodes: [] }, source: 'user', deletable: true, created_at: 'x' }))
    vi.stubGlobal('fetch', fetchMock)
    await importTemplate({ url: 'https://example.com/share/t.json' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/templates/import')
    expect(JSON.parse(String(init?.body))).toEqual({ url: 'https://example.com/share/t.json' })
  })
})

describe('A1 template productization client (U1187)', () => {
  it('listTemplates passes q through when provided, plain URL otherwise', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ items: [{ id: 'utpl-1', name: 'u', description: '', tags: [], category: '', node_count: 1, source: 'user', deletable: true, version: 1, updated_at: 'x', usage_count: 2 }] }),
    )
    vi.stubGlobal('fetch', fetchMock)
    await listTemplates()
    expect(fetchMock.mock.calls[0][0]).toBe('/api/templates')
    await listTemplates('  退款  ')
    expect(fetchMock.mock.calls[1][0]).toBe('/api/templates?q=%E9%80%80%E6%AC%BE')
  })

  it('touchTemplateUsage POSTs usage and returns count', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => jsonResponse({ usage_count: 3 }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await touchTemplateUsage('utpl-1')
    expect(result.usage_count).toBe(3)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/templates/utpl-1/usage')
    expect(init?.method).toBe('POST')
  })

  it('instantiateTemplate POSTs values and returns graph + template meta', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ graph: { nodes: [], edges: [] }, template: { id: 'utpl-1', name: 'n', version: 1 } }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const result = await instantiateTemplate('utpl-1', { min_amount: 250, channel: 'im' })
    expect(result.template.version).toBe(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/templates/utpl-1/instantiate')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({ values: { min_amount: 250, channel: 'im' } })
  })

  it('createUserTemplate forwards params declaration', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ id: 'utpl-2', name: 'n', description: '', tags: [], category: '', graph: { version: 1, variables: [], nodes: [], edges: [] }, source: 'user', deletable: true, params: { p: { type: 'number', required: true } } }),
    )
    vi.stubGlobal('fetch', fetchMock)
    await createUserTemplate({ name: 'n', graph: { version: 1, variables: [], nodes: [], edges: [] }, params: { p: { type: 'number', required: true } } })
    const [, init] = fetchMock.mock.calls[0]
    expect(JSON.parse(String(init?.body))).toEqual({
      name: 'n', description: '', category: '', graph: { version: 1, variables: [], nodes: [], edges: [] },
      params: { p: { type: 'number', required: true } },
    })
  })

  it('updateUserTemplate omits params when not provided', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ id: 'utpl-2', name: 'n', description: '', tags: [], category: '', graph: { version: 1, variables: [], nodes: [], edges: [] }, source: 'user', deletable: true }),
    )
    vi.stubGlobal('fetch', fetchMock)
    await updateUserTemplate('utpl-2', { name: 'n', graph: { version: 1, variables: [], nodes: [], edges: [] } })
    const [, init] = fetchMock.mock.calls[0]
    const body = JSON.parse(String(init?.body))
    expect(body).not.toHaveProperty('params')
    expect(body).not.toHaveProperty('if_match_version')
  })
})


describe('message templates API (pack A2, docs/98; U1194)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('listMessageTemplates GETs without kind and with kind filter', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({
        items: [
          { id: 'mtpl-1', name: '审批提醒', kind: 'approval', subject: '[Atlas] {{title}}', variables: ['title'], updated_at: 'x' },
        ],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const items = await listMessageTemplates()
    expect(items).toHaveLength(1)
    expect(items[0].kind).toBe('approval')
    let [url] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/message-templates')

    await listMessageTemplates('alert')
    url = fetchMock.mock.calls[1][0]
    expect(url).toBe('/api/message-templates?kind=alert')
  })

  it('getMessageTemplate GETs detail with body', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ id: 'mtpl-1', name: 'n', kind: 'approval', subject: 's', body: 'b {{title}}', variables: ['title'], updated_at: 'x' }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const detail = await getMessageTemplate('mtpl-1')
    expect(detail.body).toContain('{{title}}')
    const [url] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/message-templates/mtpl-1')
  })

  it('createMessageTemplate POSTs full payload', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ id: 'mtpl-1', name: '审批提醒', kind: 'approval', subject: 's', body: 'b', variables: ['title'], updated_at: 'x' }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const input = { name: '审批提醒', kind: 'approval' as const, subject: 's', body: 'b', variables: ['title'] }
    await createMessageTemplate(input)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/message-templates')
    expect((init as RequestInit).method).toBe('POST')
    expect(JSON.parse((init as RequestInit).body as string)).toEqual(input)
  })

  it('updateMessageTemplate PUTs to the item path', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ id: 'mtpl-2', name: '告警提醒', kind: 'alert', subject: 's2', body: 'b2', variables: ['title', 'graph_id'], updated_at: 'x' }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const input = { name: '告警提醒', kind: 'alert' as const, subject: 's2', body: 'b2', variables: ['title', 'graph_id'] }
    await updateMessageTemplate('mtpl-2', input)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/message-templates/mtpl-2')
    expect((init as RequestInit).method).toBe('PUT')
    expect(JSON.parse((init as RequestInit).body as string)).toEqual(input)
  })

  it('deleteMessageTemplate DELETEs the item path', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => jsonResponse({ deleted: true }))
    vi.stubGlobal('fetch', fetchMock)
    await deleteMessageTemplate('mtpl-3')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/message-templates/mtpl-3')
    expect((init as RequestInit).method).toBe('DELETE')
  })
})
