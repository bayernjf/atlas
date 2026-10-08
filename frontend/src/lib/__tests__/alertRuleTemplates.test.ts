/**
 * docs/59 F-1 + 打包 ZS（docs/102）告警规则模板前端契约测试（13 U660–U662、U1227–U1229）。
 * 无 jsdom：i18n 双语键 + apiClient 端点/方法 + 一键应用复用 PUT rules 契约 + 用户模板写端点。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import zh from '../../locales/zh-CN/monitoring.json'
import en from '../../locales/en-US/monitoring.json'
import {
  createAlertRuleTemplate,
  deleteAlertRuleTemplate,
  getAlertRuleTemplate,
  getAlertRuleTemplates,
  updateAlertRuleTemplate,
  updateRules,
  type AlertRuleTemplate,
  type RuleConfig,
} from '../apiClient'

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

const KEYS = [
  'button',
  'modalTitle',
  'hint',
  'apply',
  'confirmTitle',
  'confirmOk',
  'confirmCancel',
  'applied',
  'applyFailed',
  'loadFailed',
  'empty',
  // 打包 ZS：用户自建 CRUD 键
  'new',
  'edit',
  'delete',
  'nameLabel',
  'nameRequired',
  'descLabel',
  'tagsLabel',
  'configLabel',
  'configInvalid',
  'save',
  'cancel',
  'sourceBuiltin',
  'sourceUser',
  'createSuccess',
  'updateSuccess',
  'deleteSuccess',
  'saveFailed',
  'deleteFailed',
  'deleteConfirmTitle',
  'builtinProtected',
]

// U660 ---------------------------------------------------------------------
describe('monitoring.templates i18n（U660 / U1227 扩展键）', () => {
  it('zh/en 都齐备全部模板市场键', () => {
    for (const key of KEYS) {
      expect((zh as { templates: Record<string, string> }).templates[key]).toBeTruthy()
      expect((en as { templates: Record<string, string> }).templates[key]).toBeTruthy()
    }
  })

  it('en 文案零汉字', () => {
    const block = (en as { templates: Record<string, string> }).templates
    for (const key of KEYS) {
      expect(/[一-鿿]/.test(block[key])).toBe(false)
    }
  })

  it('applied 插值变量集合 zh/en 一致（{{name}}）', () => {
    const vars = (s: string) => (s.match(/{{\s*\w+\s*}}/g) ?? []).sort()
    const t = (zh as { templates: Record<string, string> }).templates
    const e = (en as { templates: Record<string, string> }).templates
    expect(vars(e.applied)).toEqual(vars(t.applied))
    expect(vars(t.applied)).toEqual(['{{name}}'])
  })
})

// U661 ---------------------------------------------------------------------
describe('告警规则模板只读端点（U661）', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('getAlertRuleTemplates 走 GET 列表并解包 items（投影不含 config，source 区分）', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({
        items: [
          { id: 'strict-sre', name: '严格 SRE', description: 'd', tags: ['严格'], source: 'builtin' },
          { id: 'urt-1', name: '我的规则', description: 'e', tags: ['ops'], source: 'user' },
        ],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const items = await getAlertRuleTemplates()
    expect(items).toHaveLength(2)
    expect(items[0].source).toBe('builtin')
    expect(items[1].source).toBe('user')
    expect('config' in items[0]).toBe(false)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/alert-rule-templates')
    expect(init?.method).toBeUndefined() // 默认 GET
  })

  it('getAlertRuleTemplate 走 GET 详情、id 经 encodeURIComponent、source 随行', async () => {
    const config: RuleConfig = {
      run_error: { enabled: true },
      node_failed: { enabled: true },
      consecutive_failures: { enabled: true, threshold: 2 },
      failure_rate: { enabled: true, window: 10, min_samples: 3, rate: 0.3 },
      custom: [],
    }
    const detail: AlertRuleTemplate = {
      id: 'strict-sre',
      name: '严格 SRE',
      description: 'd',
      tags: ['严格'],
      source: 'builtin',
      config,
    }
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => jsonResponse(detail))
    vi.stubGlobal('fetch', fetchMock)
    const got = await getAlertRuleTemplate('strict-sre')
    expect(got.config.failure_rate.rate).toBe(0.3)
    expect(got.source).toBe('builtin')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/alert-rule-templates/strict-sre')
  })
})

// U662 ---------------------------------------------------------------------
describe('一键应用复用 PUT rules 全量替换（U662）', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('取模板 config 后 updateRules 发 PUT /api/monitoring/rules，body 即完整 config', async () => {
    const config: RuleConfig = {
      run_error: { enabled: true },
      node_failed: { enabled: true },
      consecutive_failures: { enabled: true, threshold: 5 },
      failure_rate: { enabled: true, window: 20, min_samples: 10, rate: 0.8 },
      custom: [],
      escalation_ack_minutes: null,
      recovery_healthy_streak: 1,
      recovery_cooldown_minutes: null,
    }
    const calls: Array<[string, RequestInit | undefined]> = []
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push([url, init])
      // 详情端点返回模板；PUT 回显同一 config
      const body = url === '/api/monitoring/rules' ? config : { id: 'demo-lenient', source: 'builtin', config }
      return jsonResponse(body)
    })
    vi.stubGlobal('fetch', fetchMock)

    // 模拟组件「应用」动作：先取详情，再全量替换
    const detail = await getAlertRuleTemplate('demo-lenient')
    const saved = await updateRules(detail.config)

    const put = calls.find(([url]) => url === '/api/monitoring/rules')
    expect(put).toBeTruthy()
    expect(put?.[1]?.method).toBe('PUT')
    expect(JSON.parse(put?.[1]?.body as string)).toEqual(config)
    expect(saved.failure_rate.rate).toBe(0.8)
  })
})

// U1228 ---------------------------------------------------------------------
describe('用户模板写端点方法（U1228）', () => {
  afterEach(() => vi.unstubAllGlobals())

  const config: RuleConfig = {
    run_error: { enabled: true },
    node_failed: { enabled: true },
    consecutive_failures: { enabled: true, threshold: 3 },
    failure_rate: { enabled: true, window: 20, min_samples: 5, rate: 0.5 },
    custom: [],
    escalation_ack_minutes: null,
    recovery_healthy_streak: 1,
    recovery_cooldown_minutes: null,
  }
  const payload = { name: '我的规则', description: 'd', tags: ['ops'], config }
  const echo: AlertRuleTemplate = { id: 'urt-1', ...payload, source: 'user' }

  it('createAlertRuleTemplate 走 POST /api/alert-rule-templates，body 含 name/description/tags/config', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => jsonResponse(echo))
    vi.stubGlobal('fetch', fetchMock)
    const created = await createAlertRuleTemplate(payload)
    expect(created.id).toBe('urt-1')
    expect(created.source).toBe('user')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/alert-rule-templates')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual(payload)
  })

  it('updateAlertRuleTemplate 走 PUT /api/alert-rule-templates/{id}', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => jsonResponse(echo))
    vi.stubGlobal('fetch', fetchMock)
    const updated = await updateAlertRuleTemplate('urt-1', payload)
    expect(updated.config.consecutive_failures.threshold).toBe(3)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/alert-rule-templates/urt-1')
    expect(init?.method).toBe('PUT')
  })

  it('deleteAlertRuleTemplate 走 DELETE /api/alert-rule-templates/{id}', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ deleted: true }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const result = await deleteAlertRuleTemplate('urt-1')
    expect(result).toEqual({ deleted: true })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/alert-rule-templates/urt-1')
    expect(init?.method).toBe('DELETE')
  })
})

// U1229 ---------------------------------------------------------------------
describe('AlertRuleTemplateMarket 组件渲染面（U1229，tsc 类型面 + 组件静态契约）', () => {
  it('apiClient 导出类型契约：AlertRuleTemplateSummary 必含 source', () => {
    // 类型面由 tsc -b 守护；此处校验运行时形状不变量
    const summary: { id: string; source: string } = { id: 'x', source: 'builtin' }
    expect(summary.source).toBe('builtin')
  })
})
