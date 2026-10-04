import { describe, expect, it, vi } from 'vitest'

// U1108 机检读源码用 Vite `?raw` 导入（不依赖 node:fs/node:path/__dirname，
// 前端 tsconfig types 只有 vite/client；`?raw` 由 vite/client 声明）
import reflectionLibSource from '../reflection.ts?raw'
import pageSource from '../../pages/Reflection.tsx?raw'

// 数据面测试 mock apiClient（lib/reflection.ts 的数据函数不触真实网络）
vi.mock('../apiClient')
import {
  API_WHITELIST,
  decisionLabelKey,
  decisionTone,
  filterReportsByGraph,
  gotoTargetFor,
  isEmptyReports,
  loadCandidate,
  loadReports,
  markDecision,
  scopeForParamKey,
  statusTone,
  toReportView,
  type ReflectionReportItem,
} from '../reflection'
import zhReflection from '../../locales/zh-CN/reflection.json'
import enReflection from '../../locales/en-US/reflection.json'

// ── 打包 ZS（docs/92 §4 U1103–U1108）──
// 前端无组件渲染测试库（package.json devDeps 无 @testing-library/react），
// 页面行为断言按契约 §5 落 lib 层纯函数：数据映射/分流/空态判定的逻辑全覆盖，
// 渲染由 tsc + vite build + 浏览器冒烟兜底（与既有 757 用例同模式）。

const flatten = (obj: unknown, prefix = ''): Record<string, string> => {
  const out: Record<string, string> = {}
  if (obj !== null && typeof obj === 'object') {
    for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
      const path = prefix ? `${prefix}.${key}` : key
      if (value !== null && typeof value === 'object') Object.assign(out, flatten(value, path))
      else out[path] = String(value)
    }
  }
  return out
}

const mkReport = (over: Partial<ReflectionReportItem>): ReflectionReportItem => ({
  candidate_id: null,
  graph_id: 'g-1',
  base_version: 3,
  status: 'ok',
  reasons: [],
  generated_at: '2026-10-03T00:00:00Z',
  ...over,
})

describe('ZS U1103：列表渲染映射（状态徽标/候选有无/图过滤）', () => {
  it('status 四值映射到正确徽标色（ok=绿，两档 rejected=红，no_evidence=灰）', () => {
    expect(statusTone('ok')).toBe('success')
    expect(statusTone('rejected_whitelist')).toBe('error')
    expect(statusTone('rejected_bounds')).toBe('error')
    expect(statusTone('no_evidence')).toBe('default')
  })

  it('candidate_id 空/缺省 → 无候选建议标记，非空 → 有候选', () => {
    expect(toReportView(mkReport({})).hasCandidate).toBe(false)
    expect(toReportView(mkReport({ candidate_id: '' })).hasCandidate).toBe(false)
    expect(toReportView(mkReport({ candidate_id: 'refl-1' })).hasCandidate).toBe(true)
    expect(toReportView(mkReport({ candidate_id: 'refl-1' })).candidateId).toBe('refl-1')
  })

  it('graph 过滤：空值返全量，非空只留匹配图', () => {
    const items = [
      mkReport({ graph_id: 'g-1' }),
      mkReport({ graph_id: 'g-2', candidate_id: 'refl-2' }),
    ]
    expect(filterReportsByGraph(items, null)).toHaveLength(2)
    expect(filterReportsByGraph(items, '')).toHaveLength(2)
    const filtered = filterReportsByGraph(items, 'g-2')
    expect(filtered).toHaveLength(1)
    expect(filtered[0].graph_id).toBe('g-2')
  })
})

describe('ZS U1105：采纳入口四 scope 跳转分流（docs/92 E-3；ZU E-6 增 nodeId）', () => {
  it('graph_variable / gate_config → 编辑器（带 graphId，nodeId 恒 null）', () => {
    expect(gotoTargetFor('graph_variable', 'g-1')).toEqual({
      kind: 'editor',
      graphId: 'g-1',
      nodeId: null,
    })
    expect(gotoTargetFor('gate_config', 'g-1', 'ai-1')).toEqual({
      kind: 'editor',
      graphId: 'g-1',
      nodeId: null,
    })
  })

  it('node_config 无 nodeId → 编辑器 nodeId=null；带 nodeId → 透传', () => {
    expect(gotoTargetFor('node_config', 'g-1')).toEqual({
      kind: 'editor',
      graphId: 'g-1',
      nodeId: null,
    })
    expect(gotoTargetFor('node_config', 'g-1', 'ai-decide')).toEqual({
      kind: 'editor',
      graphId: 'g-1',
      nodeId: 'ai-decide',
    })
  })

  it('monitor_rule → 监控页（nodeId 不影响分流）', () => {
    expect(gotoTargetFor('monitor_rule', 'g-1')).toEqual({ kind: 'monitoring' })
    expect(gotoTargetFor('monitor_rule', 'g-1', 'ai-1')).toEqual({ kind: 'monitoring' })
  })

  it('graph_id 缺省（异常数据）→ disabled，四 scope 一致', () => {
    for (const scope of ['graph_variable', 'node_config', 'monitor_rule', 'gate_config'] as const) {
      expect(gotoTargetFor(scope, null)).toEqual({ kind: 'disabled' })
      expect(gotoTargetFor(scope, undefined)).toEqual({ kind: 'disabled' })
      expect(gotoTargetFor(scope, '')).toEqual({ kind: 'disabled' })
    }
  })

  it('白名单四 key 按前缀归 scope', () => {
    expect(scopeForParamKey('approval_limit')).toBe('graph_variable')
    expect(scopeForParamKey('node.confidenceThreshold')).toBe('node_config')
    expect(scopeForParamKey('monitor.failure_rate.rate')).toBe('monitor_rule')
    expect(scopeForParamKey('gate.run_error_rate')).toBe('gate_config')
  })
})

describe('ZS U1106：空态判定', () => {
  it('空列表 → 空态；非空 → 非空态', () => {
    expect(isEmptyReports([])).toBe(true)
    expect(isEmptyReports([mkReport({})])).toBe(false)
  })
})

describe('ZS U1104：候选详情数据面（changes 形状薄封装）', () => {
  it('loadCandidate 转发 candidate_id（changes 的 from/to 为别名输出）', async () => {
    const { getReflectionCandidate } = await import('../apiClient')
    vi.mocked(getReflectionCandidate).mockResolvedValue({
      candidate_id: 'refl-1',
      graph_id: 'g-1',
      base_version: 3,
      changes: [{ param_key: 'approval_limit', from: 100, to: 200, reason: 'r' }],
      prompt_suggestions: ['建议'],
      evidence_digest: 'g-1@3；运行 2 条',
      generated_at: '2026-10-03T00:00:00Z',
    })
    const detail = await loadCandidate('refl-1')
    expect(getReflectionCandidate).toHaveBeenCalledWith('refl-1')
    expect(detail.changes[0]).toMatchObject({ param_key: 'approval_limit', from: 100, to: 200 })
    expect(detail.prompt_suggestions).toHaveLength(1)
    expect(detail.evidence_digest).toContain('g-1@3')
  })

  it('loadReports 转发 graphId 与 limit', async () => {
    const { listReflectionReports } = await import('../apiClient')
    vi.mocked(listReflectionReports).mockResolvedValue([])
    await loadReports('g-1', 10)
    expect(listReflectionReports).toHaveBeenCalledWith('g-1', 10)
  })
})

describe('ZS U1107：reflection.json 双语目录奇偶（键全等＋en 零汉字）', () => {
  it('zh/en 叶子键集相等', () => {
    const zhKeys = Object.keys(flatten(zhReflection))
    const enKeys = Object.keys(flatten(enReflection))
    expect(zhKeys.sort()).toEqual(enKeys.sort())
  })

  it('en 值不含汉字', () => {
    for (const [key, value] of Object.entries(flatten(enReflection))) {
      expect(/[一-鿿]/.test(value), `${key}: ${value}`).toBe(false)
    }
  })
})

describe('ZU U1124：写调用面白名单与 actions 列守护（docs/94 E-7，机检非口头承诺）', () => {
  it('lib/reflection.ts 从 apiClient 的 import 面 ⊆ API_WHITELIST（四只读 + putReflectionDecision）', () => {
    const importBlock = reflectionLibSource.match(/import \{([^}]*)\} from '\.\/apiClient'/)
    expect(importBlock, 'lib/reflection.ts 必须显式 import apiClient 成员').not.toBeNull()
    const imported = importBlock![1]
      .split(',')
      .map((part: string) => part.trim())
      .filter((part: string) => part && !part.startsWith('type ')) // type import 无运行时调用，不计入
      .map((part: string) => part.split(' as ')[0].trim())
      .filter(Boolean)
    for (const name of imported) {
      expect(API_WHITELIST as readonly string[], `import 了非白名单成员: ${name}`).toContain(name)
    }
    // 白名单内每个名字都被真正使用（防白名单注水）
    for (const name of API_WHITELIST) {
      expect(reflectionLibSource, `白名单成员 ${name} 未被引用`).toMatch(new RegExp(`\\b${name}\\b`))
    }
    // 白名单里的写函数恰有一个：putReflectionDecision。
    const writeFns = imported.filter((name: string) => /^(put|post|create|update|delete|save)/i.test(name))
    expect(writeFns).toEqual(['putReflectionDecision'])
  })

  it('Reflection.tsx 不直接 import apiClient，页面只经 lib/reflection 取数/写标记', () => {
    expect(pageSource).not.toContain("from '../lib/apiClient'")
  })

  it('Reflection.tsx 无写请求 method 字面（写只经 lib 的 markDecision）', () => {
    expect(pageSource).not.toContain("method: 'POST'")
    expect(pageSource).not.toContain("method: 'PUT'")
    expect(pageSource).not.toContain("method: 'DELETE'")
    // 页面唯一写动作经 markDecision。
    expect(pageSource).toMatch(/\bmarkDecision\b/)
  })

  it("changeColumns 真实含 key: 'actions' 列（钉死 ZS「去修改」按钮从未渲染缺陷不复发）", () => {
    expect(pageSource).toMatch(/key:\s*'actions'/)
  })
})

describe('ZU U1121：候选决策纯函数与写封装（docs/94 E-1/E-4）', () => {
  it('decisionTone：adopted=success，dismissed/pending(null|undefined)=default', () => {
    expect(decisionTone('adopted')).toBe('success')
    expect(decisionTone('dismissed')).toBe('default')
    expect(decisionTone(null)).toBe('default')
    expect(decisionTone(undefined)).toBe('default')
  })

  it('decisionLabelKey：三态映射到 i18n 键，缺省 pending', () => {
    expect(decisionLabelKey('adopted')).toBe('decision.adopted')
    expect(decisionLabelKey('dismissed')).toBe('decision.dismissed')
    expect(decisionLabelKey(null)).toBe('decision.pending')
    expect(decisionLabelKey(undefined)).toBe('decision.pending')
  })

  it('markDecision 以 PUT …/decision 调 putReflectionDecision，透传 status 并返投影', async () => {
    const { putReflectionDecision } = await import('../apiClient')
    const updated = {
      candidate_id: 'refl-1',
      graph_id: 'g-1',
      base_version: 3,
      changes: [{ param_key: 'node.confidenceThreshold', from: 0.6, to: 0.7, reason: 'r', node_id: 'ai-1' }],
      prompt_suggestions: [],
      evidence_digest: 'd',
      generated_at: '2026-10-04T00:00:00Z',
      decision_status: 'adopted' as const,
      decided_at: '2026-10-05T00:00:00Z',
    }
    vi.mocked(putReflectionDecision).mockResolvedValue(updated)
    const result = await markDecision('refl-1', 'adopted')
    expect(putReflectionDecision).toHaveBeenCalledWith('refl-1', 'adopted')
    expect(result.decision_status).toBe('adopted')
    expect(result.changes[0].node_id).toBe('ai-1')
  })

  it('toReportView 透传报告行决策态，缺省为 null', () => {
    expect(toReportView(mkReport({})).decisionStatus).toBeNull()
    expect(
      toReportView(mkReport({ candidate_id: 'refl-1', decision_status: 'dismissed' })).decisionStatus,
    ).toBe('dismissed')
  })
})

describe('ZU U1122：gotoTargetFor 节点级定位（docs/94 E-6）', () => {
  it('node_config + nodeId ⇒ editor 目标携带 nodeId', () => {
    expect(gotoTargetFor('node_config', 'g-1', 'ai-decide')).toEqual({
      kind: 'editor',
      graphId: 'g-1',
      nodeId: 'ai-decide',
    })
  })

  it('node_config 无 nodeId（0/≥2 个 ai_decision 或快照缺失）⇒ nodeId=null，形状与旧版兼容', () => {
    expect(gotoTargetFor('node_config', 'g-1')).toEqual({
      kind: 'editor',
      graphId: 'g-1',
      nodeId: null,
    })
    expect(gotoTargetFor('node_config', 'g-1', null)).toEqual({
      kind: 'editor',
      graphId: 'g-1',
      nodeId: null,
    })
    expect(gotoTargetFor('node_config', 'g-1', '')).toEqual({
      kind: 'editor',
      graphId: 'g-1',
      nodeId: null,
    })
  })

  it('其余 scope 即使误传 nodeId 也不透传（仅 node_config 有节点语义）', () => {
    expect(gotoTargetFor('graph_variable', 'g-1', 'ai-1')).toEqual({
      kind: 'editor',
      graphId: 'g-1',
      nodeId: null,
    })
    expect(gotoTargetFor('gate_config', 'g-1', 'ai-1')).toEqual({
      kind: 'editor',
      graphId: 'g-1',
      nodeId: null,
    })
    expect(gotoTargetFor('monitor_rule', 'g-1', 'ai-1')).toEqual({ kind: 'monitoring' })
  })
})
