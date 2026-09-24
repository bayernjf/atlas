import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  deleteOpenApiImport,
  getOpenApiImport,
  importOpenApi,
  listOpenApiImports,
  previewOpenApi,
  purgeOpenApiImport,
  putOpenApiCredentials,
  type ImportedSpec,
  type OpenApiPreview,
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

afterEach(() => {
  vi.unstubAllGlobals()
})

const preview: OpenApiPreview = {
  title: 'Petstore',
  base_url: 'https://petstore.example.com/v1',
  imported_count: 1,
  skipped_count: 1,
  operations: [
    {
      name: 'list_pets',
      method: 'get',
      path: '/pets',
      summary: 'List pets',
      description: null,
      permission: 'read',
      idempotent: true,
      locations: { limit: 'query' },
      input_schema: {
        type: 'object',
        properties: { limit: { type: 'integer' } },
      },
      security: [],
      skipped: false,
      skip_reason: null,
    },
    {
      name: 'get_pet_by_id',
      method: 'get',
      path: '/pets/{petId}',
      summary: null,
      description: null,
      permission: 'read',
      idempotent: true,
      locations: {},
      input_schema: { type: 'object', properties: {} },
      security: [],
      skipped: true,
      skip_reason: '参数 q 使用了暂不支持的 oneOf',
    },
  ],
  security_schemes: [],
}

const imported: ImportedSpec = {
  spec_id: 'openapi-1',
  title: 'Petstore',
  base_url: 'https://petstore.example.com/v1',
  created_at: '2026-09-23T00:00:00+00:00',
  operations: [preview.operations[0]],
  security_schemes: {},
  credential_envelopes: {},
}

describe('OpenAPI apiClient（docs/42 §5）', () => {
  it('previewOpenApi POST 粘贴内容到 /api/openapi/preview', async () => {
    const fetchMock = mockFetch(preview)
    vi.stubGlobal('fetch', fetchMock)

    const result = await previewOpenApi({ content: '{"openapi":"3.0.0"}' })

    expect(result.imported_count).toBe(1)
    expect(result.skipped_count).toBe(1)
    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/openapi/preview')
    expect(fetchMock.mock.calls[0][1]?.method).toBe('POST')
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      content: '{"openapi":"3.0.0"}',
    })
  })

  it('previewOpenApi 支持 url 来源', async () => {
    const fetchMock = mockFetch(preview)
    vi.stubGlobal('fetch', fetchMock)

    await previewOpenApi({ url: 'https://petstore.example.com/openapi.json' })

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      url: 'https://petstore.example.com/openapi.json',
    })
  })

  it('importOpenApi POST 并回传 201 创建的规格', async () => {
    const fetchMock = mockFetch(imported, 201)
    vi.stubGlobal('fetch', fetchMock)

    const result = await importOpenApi({ content: '{}' })

    expect(result.spec_id).toBe('openapi-1')
    expect(result.operations).toHaveLength(1)
    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/openapi/imports')
    expect(fetchMock.mock.calls[0][1]?.method).toBe('POST')
  })

  it('listOpenApiImports 解包 items', async () => {
    const fetchMock = mockFetch({ items: [imported] })
    vi.stubGlobal('fetch', fetchMock)

    const items = await listOpenApiImports()

    expect(items).toHaveLength(1)
    expect(items[0].title).toBe('Petstore')
    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/openapi/imports')
    expect(fetchMock.mock.calls[0][1]?.method).toBeUndefined()
  })

  it('getOpenApiImport GET 单个规格（id 经编码）', async () => {
    const fetchMock = mockFetch(imported)
    vi.stubGlobal('fetch', fetchMock)

    const result = await getOpenApiImport('openapi-1')

    expect(result.spec_id).toBe('openapi-1')
    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/openapi/imports/openapi-1')
  })

  it('deleteOpenApiImport 发 DELETE', async () => {
    const fetchMock = mockFetch({ deleted: true })
    vi.stubGlobal('fetch', fetchMock)

    const result = await deleteOpenApiImport('openapi-1')

    expect(result.deleted).toBe(true)
    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/openapi/imports/openapi-1')
    expect(fetchMock.mock.calls[0][1]?.method).toBe('DELETE')
  })

  it('importOpenApi 携带 credentials 加密入参', async () => {
    const fetchMock = mockFetch(imported, 201)
    vi.stubGlobal('fetch', fetchMock)

    await importOpenApi({ content: '{}' }, { KeyHeader: 'secret-key' })

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      content: '{}',
      credentials: { KeyHeader: 'secret-key' },
    })
  })

  it('putOpenApiCredentials PUT 到 credentials 子路径', async () => {
    const fetchMock = mockFetch({ configured: ['KeyHeader'] })
    vi.stubGlobal('fetch', fetchMock)

    const result = await putOpenApiCredentials('openapi-1', { KeyHeader: 'k' })

    expect(result.configured).toEqual(['KeyHeader'])
    expect(String(fetchMock.mock.calls[0][0])).toBe(
      '/api/openapi/imports/openapi-1/credentials',
    )
    expect(fetchMock.mock.calls[0][1]?.method).toBe('PUT')
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      credentials: { KeyHeader: 'k' },
    })
  })

  it('后端结构化错误码透传 message', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(
        { detail: { code: 'OPENAPI_LIMIT_EXCEEDED', message: '每租户最多导入 5 份 API 规格' } },
        422,
      ),
    )

    await expect(importOpenApi({ content: '{}' })).rejects.toThrow('每租户最多导入 5 份 API 规格')
  })

  it('listOpenApiImports(true) 带 include_deleted 查询参数', async () => {
    const deleted: ImportedSpec = { ...imported, deleted_at: '2026-09-24T00:00:00+00:00' }
    const fetchMock = mockFetch({ items: [imported, deleted] })
    vi.stubGlobal('fetch', fetchMock)

    const items = await listOpenApiImports(true)
    expect(items).toHaveLength(2)
    expect(items[1].deleted_at).toBeTruthy()
    expect(String(fetchMock.mock.calls[0][0])).toBe(
      '/api/openapi/imports?include_deleted=true',
    )
  })

  it('purgeOpenApiImport 发 hard=true DELETE 并容忍 204 空响应', async () => {
    const fetchMock = mockFetch(null, 204)
    vi.stubGlobal('fetch', fetchMock)

    await expect(purgeOpenApiImport('openapi-1')).resolves.toBeUndefined()
    expect(String(fetchMock.mock.calls[0][0])).toBe(
      '/api/openapi/imports/openapi-1?hard=true',
    )
    expect(fetchMock.mock.calls[0][1]?.method).toBe('DELETE')
  })

  it('purge 未软删时后端 409 OPENAPI_NOT_SOFT_DELETED 透传 message', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(
        { detail: { code: 'OPENAPI_NOT_SOFT_DELETED', message: '尚未软删除，请先删除再彻底删除' } },
        409,
      ),
    )
    await expect(purgeOpenApiImport('openapi-1')).rejects.toThrow(
      '尚未软删除，请先删除再彻底删除',
    )
  })
})
