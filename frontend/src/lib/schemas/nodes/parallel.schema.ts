import type { NodeConfigSchema } from '../metaSchema'

/**
 * parallel 节点 config schema（04 §5.4 / §4.9；投影自 nodeCatalog.ts 手写规则）。
 * v1 静态扇出：2-10 个分支，每分支 label/target 非空，joinTarget 必填。
 * 分支名/目标唯一性与汇聚互异非白名单规则，手写保留；joinStrategy 由 M4 批 2
 * 迁移补 enum + radio（x-widget），分支入口/汇聚目标走 target-select 业务控件。
 * x-outputSchema 仅含静态键；result 为动态入口（D30），不列键。
 */
export const parallelSchema: NodeConfigSchema = {
  type: 'object',
  properties: {
    joinStrategy: {
      type: 'string',
      enum: ['all_success', 'all_completed'],
      'x-widget': 'radio',
    },
    branches: {
      type: 'array',
      minItems: 2,
      maxItems: 10,
      items: {
        type: 'object',
        properties: {
          label: { type: 'string', pattern: '\\S' },
          target: { type: 'string', pattern: '\\S', 'x-ref': { kinds: ['*'] }, 'x-widget': 'target-select' },
        },
        required: ['label', 'target'],
      },
    },
    joinTarget: { type: 'string', pattern: '\\S', 'x-ref': { kinds: ['*'] }, 'x-widget': 'target-select' },
  },
  required: ['branches', 'joinTarget'],
  'x-outputSchema': {
    type: 'object',
    properties: {
      status: {},
      branches: {},
      joinStrategy: { type: 'string' },
      joinTarget: {},
    },
  },
}
