import type { NodeConfigSchema } from '../metaSchema'

/**
 * intent_recognition 节点 config schema（docs/109 打包 AB）。
 * intents 必填 ≥1（name 1-40、description ≤200、examples 可选）；textSource 为
 * {{路径}} 模板（x-variable）；model 节点级覆盖；跨字段规则（意图名唯一）由 L1。
 */
export const intentRecognitionSchema: NodeConfigSchema = {
  type: 'object',
  properties: {
    intents: {
      type: 'array',
      minItems: 1,
      maxItems: 20,
      items: {
        type: 'object',
        properties: {
          name: { type: 'string', pattern: '\\S', maxLength: 40 },
          description: { type: 'string', maxLength: 200 },
          examples: { type: 'array', items: { type: 'string' } },
        },
        required: ['name'],
      },
    },
    textSource: { type: 'string', 'x-variable': true },
    model: { type: 'string' },
  },
  required: ['intents'],
  'x-outputSchema': {
    type: 'object',
    properties: {
      result: {
        type: 'object',
        properties: { intent: {}, confidence: {}, slots: {} },
      },
      prompt_rendered: {},
    },
  },
}
