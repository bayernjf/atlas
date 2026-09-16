import type { NodeConfigSchema } from '../metaSchema'

/**
 * wait 节点 config schema（04 §5.5 / §4.9；投影自 nodeCatalog.ts 手写规则）。
 * v1 仅定时等待：waitType const=duration，durationSeconds 为 1-600 整数。
 */
export const waitSchema: NodeConfigSchema = {
  type: 'object',
  properties: {
    waitType: { type: 'string', const: 'duration', default: 'duration' },
    durationSeconds: { type: 'integer', minimum: 1, maximum: 600, default: 5 },
  },
  required: ['waitType', 'durationSeconds'],
  'x-outputSchema': {
    type: 'object',
    properties: {
      mode: {},
      waitType: { type: 'string' },
      durationSeconds: { type: 'integer' },
    },
  },
}
