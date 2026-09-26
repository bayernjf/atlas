import type { NodeConfigSchema } from '../metaSchema'

/**
 * trigger 节点 config 的声明式 Data Schema（04 §4.9；手写规则投影自 nodeCatalog.ts）。
 * 条件必填：schedule 必须有非空 cron、webhook 必须有非空 webhookUrl；manual 无约束。
 */
export const triggerSchema: NodeConfigSchema = {
  type: 'object',
  properties: {
    triggerType: { type: 'string', enum: ['manual', 'schedule', 'webhook'], default: 'manual' },
    cron: { type: 'string', description: 'triggerType=schedule 时必填的 Cron 表达式', 'x-widget': 'cron-input' },
    webhookUrl: { type: 'string', description: 'triggerType=webhook 时必填' },
  },
  oneOf: [
    { properties: { triggerType: { const: 'manual' } } },
    {
      properties: {
        triggerType: { const: 'schedule' },
        cron: { type: 'string', pattern: '\\S' },
      },
      required: ['cron'],
    },
    {
      properties: {
        triggerType: { const: 'webhook' },
        webhookUrl: { type: 'string', pattern: '\\S' },
      },
      required: ['webhookUrl'],
    },
  ],
  'x-outputSchema': {
    type: 'object',
    properties: {
      context: {
        type: 'object',
        properties: {
          triggerType: { type: 'string' },
          cron: { type: 'string' },
          webhookUrl: { type: 'string' },
          payload: {},
        },
      },
    },
  },
}
