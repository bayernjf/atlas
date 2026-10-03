import { describe, expect, it, vi } from 'vitest'

// U1108 机检读源码用 Vite `?raw` 导入（不依赖 node:fs/node:path/__dirname，
// 前端 tsconfig types 只有 vite/client；`?raw` 由 vite/client 声明）
import reflectionLibSource from '../reflection.ts?raw'
import pageSource from '../../pages/Reflection.tsx?raw'

// 数据面测试 mock apiClient（lib/reflection.ts 的数据函数不触真实网络）
vi.mock('../apiClient')
import {
  READ_ONLY_WHITELIST,
  filterReportsByGraph,
  gotoTargetFor,
  isEmptyReports,
  loadCandidate,
  loadReports,
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

describe('ZS U1105：采纳入口四 scope 跳转分流（docs/92 E-3）', () => {
  it('graph_variable / node_config / gate_config → 编辑器（带 graphId）', () => {
    expect(gotoTargetFor('graph_variable', 'g-1')).toEqual({ kind: 'editor', graphId: 'g-1' })
    expect(gotoTargetFor('node_config', 'g-1')).toEqual({ kind: 'editor', graphId: 'g-1' })
    expect(gotoTargetFor('gate_config', 'g-1')).toEqual({ kind: 'editor', graphId: 'g-1' })
  })

  it('monitor_rule → 监控页', () => {
    expect(gotoTargetFor('monitor_rule', 'g-1')).toEqual({ kind: 'monitoring' })
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

describe('ZS U1108：页内零写调用守护（机检，非口头承诺）', () => {
  it('lib/reflection.ts 从 apiClient 的 import 面 ⊆ 只读白名单', () => {
    const importBlock = reflectionLibSource.match(/import \{([^}]*)\} from '\.\/apiClient'/)
    expect(importBlock, 'lib/reflection.ts 必须显式 import apiClient 成员').not.toBeNull()
    const imported = importBlock![1]
      .split(',')
      .map((part: string) => part.trim())
      .filter((part: string) => part && !part.startsWith('type ')) // type import 无运行时调用，不计入
      .map((part: string) => part.split(' as ')[0].trim())
      .filter(Boolean)
    for (const name of imported) {
      expect(READ_ONLY_WHITELIST, `import 了非白名单成员: ${name}`).toContain(name)
    }
    // 白名单内每个名字都被真正使用（防白名单注水）
    for (const name of READ_ONLY_WHITELIST) {
      expect(reflectionLibSource, `白名单成员 ${name} 未被引用`).toMatch(new RegExp(`\\b${name}\\b`))
    }
  })

  it('Reflection.tsx 不直接 import apiClient，页面只经 lib/reflection 取数', () => {
    expect(pageSource).not.toContain("from '../lib/apiClient'")
  })

  it('Reflection.tsx 无写请求形状（无 POST/PUT/DELETE method）', () => {
    expect(pageSource).not.toContain("method: 'POST'")
    expect(pageSource).not.toContain("method: 'PUT'")
    expect(pageSource).not.toContain("method: 'DELETE'")
  })
})
