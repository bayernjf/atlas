/**
 * 部署配置（RolloutConfig.rules）的 MetaSchema 与段唯一规约（docs/120，2026-10-10）。
 *
 * D29 解锁路径第 3 步第二片：RolloutModal 规则区三行手写表单迁入 forms 内核判别异构
 * 数组（消费 docs/119 第 2 步能力）——items.oneOf 三分支（internal/lowValueBucket/canary）、
 * 判别键 `to` const；编辑面不含 full（后端 canary 期求值剔除 full，store.py:152-157，
 * promote 后才全量，属运行态语义非编辑面）。
 * 数据形状与后端契约（apiClient.RolloutRule）不变——纯前端渲染层替换。
 *
 * normalizeRolloutRules：段唯一＋固定序规约（同 to 只保留最后一条、按 ROLLOUT_RULE_ORDER
 * 重排、未知段剔除、空数组保留空）——与 release.withRule 同族、对合法输入结果一致，
 * 由 RolloutModal onChange 收口，forms 内核保持通用不加业务约束。
 */
import type { MetaSchema } from '../schemas/metaSchema'
import { ROLLOUT_RULE_ORDER } from '../release'
import type { RolloutRule } from '../apiClient'

export function buildRolloutRulesSchema(): MetaSchema {
  return {
    type: 'array',
    items: {
      oneOf: [
        {
          type: 'object',
          properties: {
            to: { const: 'internal' },
            tenants: { type: 'array', items: { type: 'string' } },
          },
        },
        {
          type: 'object',
          properties: {
            to: { const: 'lowValueBucket' },
            value: { type: 'number', default: 200 },
            percent: { type: 'number', minimum: 1, maximum: 100, default: 100 },
          },
        },
        {
          type: 'object',
          properties: {
            to: { const: 'canary' },
            percent: { type: 'number', minimum: 1, maximum: 100, default: 5 },
          },
        },
      ],
    },
  }
}

/**
 * 段唯一＋固定序规约（docs/120 §2.2）。bucket 行补后端固定形状 field='payload.amount'、
 * op='<='（docs/120 §2.1「field/op 保留形状不编辑」：编辑面不含这两个字段、由规约注入，
 * 与 withRule 原逻辑一致；已含则保留）。
 */
export function normalizeRolloutRules(rules: RolloutRule[]): RolloutRule[] {
  const byTo = new Map<RolloutRule['to'], RolloutRule>()
  for (const rule of rules) {
    const to = rule?.to
    if (typeof to !== 'string' || !(ROLLOUT_RULE_ORDER as readonly string[]).includes(to)) continue
    const key = to as RolloutRule['to']
    const normalized =
      key === 'lowValueBucket'
        ? ({
            ...rule,
            field: (rule as { field?: string }).field ?? 'payload.amount',
            op: (rule as { op?: string }).op ?? '<=',
          } as RolloutRule)
        : rule
    byTo.set(key, normalized)
  }
  return (ROLLOUT_RULE_ORDER as readonly string[])
    .filter((to) => byTo.has(to as RolloutRule['to']))
    .map((to) => byTo.get(to as RolloutRule['to']) as RolloutRule)
}
