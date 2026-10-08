import type { NodeConfigSchema } from '../metaSchema'

/**
 * info_extraction 节点 config schema（docs/109 打包 AB）。
 * fields 必填 ≥1（name 1-40、type 限 string/number/boolean/object、description 可选）；
 * textSource 为 {{路径}} 模板；model 节点级覆盖。
 */
export const infoExtractionSchema: NodeConfigSchema = {
  type: 'object',
  properties: {
    fields: {
      type: 'array',
      minItems: 1,
      maxItems: 30,
      items: {
        type: 'object',
        properties: {
          name: { type: 'string', pattern: '\\S', maxLength: 40 },
          type: { type: 'string', enum: ['string', 'number', 'boolean', 'object'], default: 'string' },
          description: { type: 'string', maxLength: 200 },
        },
        required: ['name', 'type'],
      },
    },
    textSource: { type: 'string', 'x-variable': true },
    model: { type: 'string' },
  },
  required: ['fields'],
  'x-outputSchema': {
    type: 'object',
    properties: {
      result: {
        type: 'object',
        properties: { fields: {}, missing: { type: 'array' } },
      },
      prompt_rendered: {},
    },
  },
}
