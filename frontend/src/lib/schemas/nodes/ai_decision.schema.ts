import type { NodeConfigSchema } from '../metaSchema'

/**
 * ai_decision 节点 config schema（04 §4.9；投影自 nodeCatalog.ts 手写规则）。
 * promptTemplate 接受 {{路径}} 模板（x-variable，L2 见 04 §6.5）；
 * confidenceThreshold 可选，取值 0-1。
 */
export const aiDecisionSchema: NodeConfigSchema = {
  type: 'object',
  properties: {
    promptTemplate: { type: 'string', pattern: '\\S', 'x-variable': true },
    model: { type: 'string' },
    confidenceThreshold: { type: 'number', minimum: 0, maximum: 1, default: 0.6 },
  },
  required: ['promptTemplate'],
  'x-outputSchema': {
    type: 'object',
    properties: {
      decision: {},
      prompt_rendered: {},
    },
  },
}
