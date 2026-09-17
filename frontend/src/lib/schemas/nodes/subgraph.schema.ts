import type { NodeConfigSchema } from '../metaSchema'

/**
 * subgraph 节点 config schema（04 §5.7 / §4.9；投影自 nodeCatalog.ts 手写规则）。
 * graphId 必填非空，M4 批 2 迁移走 saved-graph-select 业务控件（拉 /api/graphs）；
 * inputs 为对象，值是非空模板字符串（x-variable → variable-input）。
 * 入参空键名/键名重复非白名单可表达规则，手写保留。
 * x-outputSchema 仅列 status/outputs 根；outputs 深层展开缓做 D30。
 */
export const subgraphSchema: NodeConfigSchema = {
  type: 'object',
  properties: {
    graphId: { type: 'string', pattern: '\\S', 'x-widget': 'saved-graph-select' },
    inputs: {
      type: 'object',
      additionalProperties: { type: 'string', pattern: '\\S', 'x-variable': true },
    },
  },
  required: ['graphId'],
  'x-outputSchema': {
    type: 'object',
    properties: {
      status: {},
      outputs: {},
    },
  },
}
