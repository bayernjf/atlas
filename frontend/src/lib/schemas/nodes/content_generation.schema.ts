import type { NodeConfigSchema } from '../metaSchema'

/**
 * content_generation 节点 config schema（docs/109 打包 AB）。
 * template 必填（1-2000 字，可含 {{路径}}）；style 可选；maxLength 1-4000 整数；
 * model 节点级覆盖。
 */
export const contentGenerationSchema: NodeConfigSchema = {
  type: 'object',
  properties: {
    template: { type: 'string', pattern: '\\S', maxLength: 2000, 'x-variable': true },
    style: { type: 'string' },
    maxLength: { type: 'integer', minimum: 1, maximum: 4000, default: 800 },
    model: { type: 'string' },
  },
  required: ['template'],
  'x-outputSchema': {
    type: 'object',
    properties: {
      result: {
        type: 'object',
        properties: { text: {}, prompt_rendered: {} },
      },
      prompt_rendered: {},
    },
  },
}
