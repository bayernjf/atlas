import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  deleteMemory,
  listMemories,
  searchMemories,
  type MemoryItem,
  type MemorySearchResult,
} from '../apiClient'

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

function mockFetch(body: unknown, status = 200) {
  return vi.fn(async (_url: string, _init?: RequestInit) => jsonResponse(body, status))
}

const item: MemoryItem = {
  id: 'mem-1',
  kind: 'fact',
  content: '订单 o-1 已退款',
  scope: { order_id: 'o-1' },
  confidence: 1,
  source: 'tool',
  metadata: {},
  created_at: '2026-09-19T00:00:00+00:00',
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('M11 memory apiClient（U97）', () => {
  it('listMemories 默认 limit=50 并解包 items', async () => {
    const fetchMock = mockFetch({ items: [item] })
    vi.stubGlobal('fetch', fetchMock)
    const items = await listMemories()
    expect(items).toHaveLength(1)
    expect(items[0].id).toBe('mem-1')
    const url = String(fetchMock.mock.calls[0][0])
    expect(url).toContain('/api/memories?')
    expect(url).toContain('limit=50')
    expect(fetchMock.mock.calls[0][1]?.method).toBeUndefined() // GET
  })

  it('listMemories 带 kind 时拼 kind 参数', async () => {
    const fetchMock = mockFetch({ items: [] })
    vi.stubGlobal('fetch', fetchMock)
    await listMemories('preference', 200)
    const url = String(fetchMock.mock.calls[0][0])
    expect(url).toContain('kind=preference')
    expect(url).toContain('limit=200')
  })

  it('searchMemories 拼 q/kind/top_k 并解包 results', async () => {
    const hit: MemorySearchResult = { ...item, score: 0.434524 }
    const fetchMock = mockFetch({ results: [hit] })
    vi.stubGlobal('fetch', fetchMock)
    const results = await searchMemories('配送偏好', { kind: 'fact', topK: 3 })
    expect(results[0].score).toBeCloseTo(0.434524)
    const url = String(fetchMock.mock.calls[0][0])
    expect(url).toContain('/api/memories/search?')
    expect(url).toContain('q=%E9%85%8D%E9%80%81%E5%81%8F%E5%A5%BD') // encodeURIComponent 中文
    expect(url).toContain('kind=fact')
    expect(url).toContain('top_k=3')
  })

  it('searchMemories 无 opts 时只带 q', async () => {
    const fetchMock = mockFetch({ results: [] })
    vi.stubGlobal('fetch', fetchMock)
    await searchMemories('x')
    const url = String(fetchMock.mock.calls[0][0])
    expect(url).toContain('q=x')
    expect(url).not.toContain('kind=')
    expect(url).not.toContain('top_k=')
  })

  it('deleteMemory 发 DELETE 到 /api/memories/{id}', async () => {
    const fetchMock = mockFetch({ deleted: true })
    vi.stubGlobal('fetch', fetchMock)
    await deleteMemory('mem-9')
    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/memories/mem-9')
    expect(fetchMock.mock.calls[0][1]?.method).toBe('DELETE')
  })

  it('后端 4xx 时抛错', async () => {
    vi.stubGlobal('fetch', mockFetch({ detail: '记忆不存在' }, 404))
    await expect(deleteMemory('mem-x')).rejects.toThrow('记忆不存在')
  })
})
