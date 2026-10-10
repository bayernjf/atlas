import type { NodeConfigSchema } from '../metaSchema'

/**
 * ai_decision 节点 config schema（04 §4.9；投影自 nodeCatalog.ts 手写规则）。
 * promptTemplate 接受 {{路径}} 模板（x-variable，L2 见 04 §6.5）；
 * confidenceThreshold 可选，取值 0-1。
 * memoryInjection（打包 AK，docs/122 D-3）可选、缺省关闭：开启后在决策 prompt 末尾追加
 * 一段【相关记忆】，关闭时节点行为与接入前逐字一致。
 */
export const aiDecisionSchema: NodeConfigSchema = {
  type: 'object',
  properties: {
    promptTemplate: { type: 'string', pattern: '\\S', 'x-variable': true },
    model: { type: 'string' },
    confidenceThreshold: { type: 'number', minimum: 0, maximum: 1, default: 0.6 },
    memoryInjection: {
      type: 'object',
      properties: {
        enabled: { type: 'boolean', default: false },
        query: { type: 'string', 'x-variable': true },
        topK: { type: 'integer', minimum: 1, maximum: 20, default: 5 },
        minScore: { type: 'number', minimum: 0, maximum: 1, default: 0 },
        scope: { type: 'object', additionalProperties: { type: 'string' } },
      },
      additionalProperties: false,
    },
  },
  required: ['promptTemplate'],
  'x-outputSchema': {
    type: 'object',
    properties: {
      decision: {},
      prompt_rendered: {},
      memory_injected: {},
    },
  },
}
