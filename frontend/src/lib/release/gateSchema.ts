/**
 * 部署配置（RolloutConfig.gate）的 MetaSchema（docs/115 §5 第 3 步第一片，2026-10-10）。
 *
 * gate 配置面迁入 forms 内核的第一片：标量（observeMinutes/minSamples/autoRollback）
 * 走内置 number/switch 控件；metrics「固定集合成员的可空阈值」走部署业务控件
 * deploy-gate-metrics（x-widget 声明，deployRegistry 注册）。
 * 数据形状与后端契约（apiClient.GateConfig）不变——纯前端渲染层替换。
 */
import type { MetaSchema } from '../schemas/metaSchema'
import { DEPLOY_GATE_METRICS_WIDGET } from '../forms/types'

export function buildGateSchema(): MetaSchema {
  return {
    type: 'object',
    properties: {
      observeMinutes: { type: 'number', minimum: 1 },
      minSamples: { type: 'number', minimum: 1 },
      autoRollback: { type: 'boolean' },
      metrics: { type: 'array', 'x-widget': DEPLOY_GATE_METRICS_WIDGET },
    },
    required: ['observeMinutes', 'autoRollback', 'minSamples', 'metrics'],
  }
}
